"""Read-only inspection of the structured evidence database (build/cmf_evidence.db).

Opens the database with SQLite mode=ro; never ingests, rebuilds or writes.  Source CSVs are read (read-only) only to show the original beside the parsed row.
Nothing here changes the parser or annotation rules, and nothing creates memories.

    python scripts/inspect_evidence.py containers [--channel Email|Meetings|Calls|Chat]
    python scripts/inspect_evidence.py show THR-010            # email thread / chat / meeting / call, hierarchical, with source comparison
    python scripts/inspect_evidence.py segments MTG-001
    python scripts/inspect_evidence.py segment SEG-MTG-001-01  # exact evidence spans of one segment
    python scripts/inspect_evidence.py annotations EML-046
    python scripts/inspect_evidence.py relations THR-010
    python scripts/inspect_evidence.py crm [--client CLIENT-001 | --id CRM-EML-0011]
    python scripts/inspect_evidence.py timeline CLIENT-001
    python scripts/inspect_evidence.py report
    python scripts/inspect_evidence.py sample [--per-channel 5]
    python scripts/inspect_evidence.py quality
    python scripts/inspect_evidence.py retrieval
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import textwrap
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DB = ROOT / "build" / "cmf_evidence.db"
CHANNELS = ["Meetings", "Calls", "Email", "Chat"]


def connect(path=DB) -> sqlite3.Connection:
    if not Path(path).exists():
        raise SystemExit(f"{path} not found (this tool never creates or builds it)")
    c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only = ON")
    return c


def Q(c, sql, *p):
    return [dict(r) for r in c.execute(sql, p)]


def ind(text, n):
    pad = " " * n
    return "\n".join(pad + ln for ln in str(text).splitlines()) if text else pad + "(empty)"


# ------------------------------------------------------------------------------------------------------------ source side
class Source:
    """The ORIGINAL text of a turn, read from the source CSVs (not from the database), to compare against the parsed row."""

    def __init__(self):
        self.text = {}
        d = ROOT / "datasets"
        for r in csv.DictReader(open(d / "emails/emails.csv", encoding="utf-8", newline="")):
            self.text[r["email_id"]] = r["body"]
        for r in csv.DictReader(open(d / "chats/chats.csv", encoding="utf-8", newline="")):
            self.text[r["message_id"]] = r["message_text"]
        for r in csv.DictReader(open(d / "meetings/meeting_turns.csv", encoding="utf-8", newline="")):
            self.text[r["turn_id"]] = r["utterance"]
        for r in csv.DictReader(open(ROOT / "data/conversations.csv", encoding="utf-8", newline="")):
            self.text[f"{r['interaction_id']}#T{r['turn_index']}"] = r["utterance"]


# ------------------------------------------------------------------------------------------------------------ listings
def cmd_containers(c, a):
    sql, p = "SELECT container_id, channel, container_kind, client_id, started_at, turn_count, subject FROM containers", []
    if a.channel:
        sql += " WHERE channel=?"
        p.append(a.channel)
    for ch, rows in _group(Q(c, sql + " ORDER BY channel, started_at, container_id", *p), "channel").items():
        print(f"\n== {ch}: {len(rows)} containers")
        for r in rows:
            print(f"  {r['container_id']:<9} {r['client_id']}  {str(r['started_at'])[:16]:<16}  {r['turn_count']:>3} {'messages' if ch in ('Email', 'Chat') else 'turns'}  {r['subject'] or ''}")


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return dict(sorted(out.items(), key=lambda kv: CHANNELS.index(kv[0]) if kv[0] in CHANNELS else 9))


def _anns(c, turn_id):
    return Q(c, "SELECT kind, value, provenance, method, confidence FROM annotations WHERE target_type='turn' AND target_id=? ORDER BY kind, value", turn_id)


def _fmt_ann(rows):
    by = defaultdict(list)
    for r in rows:
        conf = "" if r["confidence"] is None else f" {r['confidence']:.2f}"
        by[r["kind"]].append(f"{r['value']}[{'obs' if r['provenance'] == 'observed' else 'rule'}{conf}]")
    return "; ".join(f"{k}: {', '.join(v)}" for k, v in by.items()) or "(none)"


def _seg_of(c, turn_id):
    r = Q(c, "SELECT s.segment_id, s.topic_label, s.confidence FROM segments s JOIN segment_turns st ON st.segment_id=s.segment_id WHERE st.turn_id=?", turn_id)
    return r[0] if r else None


def _compare(src, t):
    s = src.text.get(t["turn_id"])
    return "MATCHES source" if s == t["original_text"] else ("SOURCE MISSING" if s is None else "DIFFERS FROM SOURCE")


def cmd_show(c, a, src=None):
    src = src or Source()
    k = Q(c, "SELECT * FROM containers WHERE container_id=?", a.id)
    if not k:
        raise SystemExit(f"{a.id}: no such container")
    k = k[0]
    meta = json.loads(k["metadata"])
    kind = k["container_kind"]
    print(f"{kind.upper().replace('_', ' ')} {k['container_id']}  client {k['client_id']}  channel {k['channel']}")
    print(f"  subject/context : {k['subject']}")
    print(f"  date            : {k['started_at']}" + (f"  to {k['ended_at']}" if k['ended_at'] else "") + f"   participants: {k['participant_ids']}   platform: {k['source_platform']}")
    if k["classification_basis"]:
        print(f"  classified as   : {k['channel']} because {k['classification_basis']}  (original channel value {meta.get('original_channel_value')})")
    segs = Q(c, "SELECT * FROM segments WHERE container_id=? ORDER BY seg_index", a.id)
    turns = Q(c, "SELECT * FROM turns WHERE container_id=? ORDER BY seq", a.id)
    by_seg = {}
    for s in segs:
        for r in Q(c, "SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", s["segment_id"]):
            by_seg[r["turn_id"]] = s
    cur = None
    for t in turns:
        s = by_seg.get(t["turn_id"])
        if kind != "email_thread" and (s is None or s is not cur):
            cur = s
            print(f"\n  [segment {s['segment_id']}] topic={s['topic_label']} turns {s['start_seq']}-{s['end_seq']} method={s['method']} conf={s['confidence']} review={s['review_status']}" if s else "\n  [NO SEGMENT]")
        _print_turn(c, t, kind, s, src, 4 if kind != "email_thread" else 2)


def _print_turn(c, t, kind, s, src, n):
    sp = f"{t['speaker_name'] or t['speaker_id']} ({t['speaker_id']}{', ' + t['speaker_role'] if t['speaker_role'] else ''}) attribution={t['attribution_status']}/{t['attribution_confidence']}"
    if kind == "email_thread":
        e = Q(c, "SELECT * FROM email_message_details WHERE turn_id=?", t["turn_id"])[0]
        print(f"\n{' ' * n}MESSAGE {t['turn_id']}  #{t['seq']}  {t['ts']}   message-id {e['message_id']}")
        print(f"{' ' * n}  From: {sp}")
        print(f"{' ' * n}  To: {e['to_ids']}   Cc: {e['cc_ids'] or '-'}   Attachments: {e['attachment_refs'] or '-'}")
        print(f"{' ' * n}  Subject: {e['subject']}")
        rel = e["in_reply_to_turn_id"] or ("(In-Reply-To names a message outside the thread)" if e["in_reply_to"] else "(not a reply)")
        print(f"{' ' * n}  Reply to: {rel}")
        print(f"{' ' * n}  Authored text:\n{ind(e['authored_text'], n + 4)}")
        print(f"{' ' * n}  Quoted text ({e['quote_basis']}): {e['quoted_text'] or '(none identified)'}")
        seg = _seg_of(c, t["turn_id"])
        print(f"{' ' * n}  Segment/topic: {seg['segment_id'] + ' / ' + seg['topic_label'] if seg else 'NONE'}")
    else:
        ts = f"  {t['ts']}" if t["ts"] else "  (no per-turn time in source)"
        extra = ""
        if kind == "chat_conversation":
            rt = Q(c, "SELECT reply_to_turn_id FROM chat_message_details WHERE turn_id=?", t["turn_id"])
            extra = f"   reply-to: {rt[0]['reply_to_turn_id'] or '-'}" if rt else ""
        print(f"{' ' * n}{'MSG' if kind == 'chat_conversation' else 'TURN'} {t['turn_id']} #{t['seq']}{ts}  {sp}{extra}")
        print(ind(t["original_text"], n + 4))
    print(f"{' ' * (n + 2)}annotations: {_fmt_ann(_anns(c, t['turn_id']))}")
    ev = Q(c, "SELECT evidence_id, span_kind, start_offset, end_offset FROM evidence_spans WHERE turn_id=?", t["turn_id"])
    print(f"{' ' * (n + 2)}evidence: {', '.join('%s[%s %s:%s]' % (e['evidence_id'], e['span_kind'], e['start_offset'], e['end_offset']) for e in ev)}   source check: {_compare(src, t)}")


def cmd_segments(c, a):
    k = Q(c, "SELECT * FROM containers WHERE container_id=?", a.id)
    if not k:
        raise SystemExit(f"{a.id}: no such container")
    print(f"{a.id} ({k[0]['channel']}) - {k[0]['subject']}")
    for s in Q(c, "SELECT * FROM segments WHERE container_id=? ORDER BY seg_index", a.id):
        ids = [r["turn_id"] for r in Q(c, "SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", s["segment_id"])]
        print(f"  {s['segment_id']}  topic={s['topic_label']}  seq {s['start_seq']}-{s['end_seq']}  method={s['method']}  conf={s['confidence']}  review={s['review_status']}  summary={s['summary']}")
        print(f"      turns: {', '.join(ids)}")


def cmd_segment(c, a, src=None):
    src = src or Source()
    s = Q(c, "SELECT * FROM segments WHERE segment_id=?", a.id)
    if not s:
        raise SystemExit(f"{a.id}: no such segment")
    s = s[0]
    print(f"SEGMENT {s['segment_id']} topic={s['topic_label']} confidence={s['confidence']} review={s['review_status']} method={s['method']}")
    for r in Q(c, "SELECT e.*, t.speaker_name, t.seq FROM segment_turns st JOIN evidence_spans e ON e.turn_id=st.turn_id JOIN turns t ON t.turn_id=st.turn_id WHERE st.segment_id=? ORDER BY st.position, e.span_kind", a.id):
        ok = src.text.get(r["turn_id"]) is not None and r["exact_text"] == src.text[r["turn_id"]][r["start_offset"]:r["end_offset"]]
        print(f"\n  {r['evidence_id']}  ({r['span_kind']}, chars {r['start_offset']}:{r['end_offset']})  turn {r['turn_id']} #{r['seq']}  {r['speaker_name']}   exact text equals source slice: {ok}")
        print(ind(r["exact_text"], 6))


def cmd_annotations(c, a):
    t = Q(c, "SELECT * FROM turns WHERE turn_id=?", a.id)
    if not t:
        raise SystemExit(f"{a.id}: no such turn")
    print(f"{a.id}  {t[0]['speaker_name']}:\n{ind(t[0]['original_text'], 4)}")
    for r in _anns(c, a.id):
        print(f"  {r['kind']:<16} {r['value']:<34} {r['provenance']:<14} {r['method']:<24} conf={r['confidence']}")


def cmd_relations(c, a):
    rows = Q(c, "SELECT r.*, f.speaker_name fn, t.speaker_name tn, f.original_text ft, t.original_text tt FROM turn_relations r JOIN turns f ON f.turn_id=r.from_turn_id JOIN turns t ON t.turn_id=r.to_turn_id WHERE r.container_id=? ORDER BY f.seq", a.id)
    print(f"{a.id}: {len(rows)} relations")
    for r in rows:
        print(f"  {r['from_turn_id']} ({r['fn']}) --{r['rel_type']}--> {r['to_turn_id']} ({r['tn']})   basis={r['basis']} [{r['provenance']}]")
        print(f"      from: {_one(r['ft'])}\n      to  : {_one(r['tt'])}")


def _one(t, n=160):
    t = " ".join(t.split())
    return t if len(t) <= n else t[:n] + " ..."


def cmd_crm(c, a):
    sql, p = "SELECT * FROM derived_artifacts", []
    if a.id:
        sql += " WHERE crm_record_id=?"; p.append(a.id)
    elif a.client:
        sql += " WHERE client_id=?"; p.append(a.client)
    rows = Q(c, sql + " ORDER BY created_at, crm_record_id", *p)
    print(f"{len(rows)} derived CRM artifacts (derived_status=derived; never original evidence)")
    for d in rows:
        print(f"\n  {d['crm_record_id']}  {d['artifact_type']}  {d['channel']}  {d['client_id']}  {d['created_at']}  by {d['author_id']}  transfer: {d['transfer_method']}")
        print(f"    subject: {d['subject']}\n    text:\n{ind(d['original_text'], 8)}")
        for s in Q(c, "SELECT * FROM derived_sources WHERE crm_record_id=?", d["crm_record_id"]):
            k = Q(c, "SELECT channel, started_at, subject, turn_count FROM containers WHERE container_id=?", s["source_ref"])
            print(f"    cites {s['source_ref']}  [{s['resolution']}]" + (f" -> {k[0]['channel']} {k[0]['started_at']} \"{k[0]['subject']}\" ({k[0]['turn_count']} turns)" if k else "  -> NOT FOUND"))


def cmd_timeline(c, a):
    rows = Q(c, "SELECT container_id id, channel, container_kind kind, started_at ts, subject, turn_count n, 'original' layer FROM containers WHERE client_id=? "
                "UNION ALL SELECT crm_record_id, channel, artifact_type, created_at, subject, 0, 'derived' FROM derived_artifacts WHERE client_id=? ORDER BY ts, id", a.client, a.client)
    print(f"Timeline for {a.client}: {len(rows)} items ({sum(r['layer'] == 'original' for r in rows)} original containers, {sum(r['layer'] == 'derived' for r in rows)} derived CRM)")
    for r in rows:
        mark = "  CRM(derived)" if r["layer"] == "derived" else f"  {r['n']:>3} turns"
        print(f"  {str(r['ts'])[:16]:<16}  {r['channel']:<8} {r['id']:<13}{mark}  {r['kind']:<17} {r['subject'] or ''}")


# ------------------------------------------------------------------------------------------------------------ report
def cmd_report(c, a=None, out=print):
    out("CHANNEL-LEVEL INSPECTION REPORT  (database opened read-only)\n")
    out(f"{'channel':<9}{'containers':>11}{'msgs/turns':>12}{'segments':>10}")
    for ch in CHANNELS:
        n = Q(c, "SELECT count(*) n, coalesce(sum(turn_count),0) t FROM containers WHERE channel=?", ch)[0]
        s = Q(c, "SELECT count(*) n FROM segments s JOIN containers k ON k.container_id=s.container_id WHERE k.channel=?", ch)[0]["n"]
        out(f"{ch:<9}{n['n']:>11}{n['t']:>12}{s:>10}")
    tot = Q(c, "SELECT (SELECT count(*) FROM containers) n, (SELECT count(*) FROM turns) t, (SELECT count(*) FROM segments) s")[0]
    out(f"{'TOTAL':<9}{tot['n']:>11}{tot['t']:>12}{tot['s']:>10}")
    out("\nAnnotations by kind and provenance:")
    for r in Q(c, "SELECT kind, provenance, count(*) n FROM annotations GROUP BY kind, provenance ORDER BY kind, provenance"):
        out(f"  {r['kind']:<18}{r['provenance']:<16}{r['n']:>6}")
    pv = {r["provenance"]: r["n"] for r in Q(c, "SELECT provenance, count(*) n FROM annotations GROUP BY provenance")}
    out(f"  observed={pv.get('observed', 0)}  rule_generated={pv.get('rule_generated', 0)}  model_generated={pv.get('model_generated', 0)}")
    out("\nSegments by review status: " + ", ".join(f"{r['review_status']}={r['n']}" for r in Q(c, "SELECT review_status, count(*) n FROM segments GROUP BY 1")))
    out("Segments by method: " + ", ".join(f"{r['method']}={r['n']}" for r in Q(c, "SELECT method, count(*) n FROM segments GROUP BY 1")))
    out("\nIntegrity:")
    chk = [
        ("source artifacts with status=failed", "SELECT count(*) FROM source_artifacts WHERE status='failed'"),
        ("turns without a segment", "SELECT count(*) FROM turns WHERE turn_id NOT IN (SELECT turn_id FROM segment_turns)"),
        ("turns without a full_turn evidence span", "SELECT count(*) FROM turns WHERE turn_id NOT IN (SELECT turn_id FROM evidence_spans WHERE span_kind='full_turn')"),
        ("segments with no turns", "SELECT count(*) FROM segments WHERE segment_id NOT IN (SELECT segment_id FROM segment_turns)"),
        ("segment_turns pointing at a missing turn", "SELECT count(*) FROM segment_turns WHERE turn_id NOT IN (SELECT turn_id FROM turns)"),
        ("segment turns from a different container than the segment", "SELECT count(*) FROM segment_turns st JOIN segments s ON s.segment_id=st.segment_id JOIN turns t ON t.turn_id=st.turn_id WHERE t.container_id<>s.container_id"),
        ("segments whose turn seqs differ from start/end", "SELECT count(*) FROM segments s WHERE (SELECT min(t.seq) FROM segment_turns st JOIN turns t ON t.turn_id=st.turn_id WHERE st.segment_id=s.segment_id)<>s.start_seq OR (SELECT max(t.seq) FROM segment_turns st JOIN turns t ON t.turn_id=st.turn_id WHERE st.segment_id=s.segment_id)<>s.end_seq"),
        ("spans whose exact_text != original_text slice", "SELECT count(*) FROM evidence_spans e JOIN turns t ON t.turn_id=e.turn_id WHERE e.exact_text <> substr(t.original_text, e.start_offset+1, e.end_offset-e.start_offset)"),
        ("turns whose container turn_count disagrees", "SELECT count(*) FROM containers k WHERE turn_count <> (SELECT count(*) FROM turns t WHERE t.container_id=k.container_id)"),
        ("relations to a turn in another container", "SELECT count(*) FROM turn_relations r JOIN turns a ON a.turn_id=r.from_turn_id JOIN turns b ON b.turn_id=r.to_turn_id WHERE a.container_id<>b.container_id"),
        ("annotations with confidence < 0.5 (rule-generated)", "SELECT count(*) FROM annotations WHERE provenance='rule_generated' AND confidence < 0.5"),
        ("turns carrying 3 or more dialogue acts (ambiguous)", "SELECT count(*) FROM (SELECT target_id FROM annotations WHERE kind='dialogue_act' GROUP BY target_id HAVING count(*)>=3)"),
        ("turns labelled only 'information' (default, no cue fired)", "SELECT count(*) FROM (SELECT target_id FROM annotations WHERE kind='dialogue_act' GROUP BY target_id HAVING count(*)=1 AND max(value)='information')"),
        ("segments labelled general_discussion", "SELECT count(*) FROM segments WHERE topic_label='general_discussion'"),
        ("segments with confidence <= 0.4", "SELECT count(*) FROM segments WHERE confidence <= 0.4"),
        ("CRM artifacts, valid source link (all refs resolve)", "SELECT count(*) FROM derived_artifacts d WHERE NOT EXISTS (SELECT 1 FROM derived_sources s WHERE s.crm_record_id=d.crm_record_id AND (s.resolution<>'container' OR s.resolved_container_id IS NULL OR s.resolved_container_id NOT IN (SELECT container_id FROM containers)))"),
        ("CRM artifacts, invalid/unresolved source link", "SELECT count(*) FROM derived_artifacts d WHERE EXISTS (SELECT 1 FROM derived_sources s WHERE s.crm_record_id=d.crm_record_id AND (s.resolution<>'container' OR s.resolved_container_id IS NULL OR s.resolved_container_id NOT IN (SELECT container_id FROM containers)))"),
        ("CRM artifacts whose cited container belongs to another client", "SELECT count(*) FROM derived_sources s JOIN derived_artifacts d ON d.crm_record_id=s.crm_record_id JOIN containers k ON k.container_id=s.resolved_container_id WHERE k.client_id<>d.client_id"),
    ]
    for label, sql in chk:
        out(f"  {label:<62}{c.execute(sql).fetchone()[0]:>6}")
    out("\nAttribution status of turns: " + ", ".join(f"{r['attribution_status']}={r['n']}" for r in Q(c, "SELECT attribution_status, count(*) n FROM turns GROUP BY 1")))
    out("Turns with a per-turn timestamp: " + ", ".join(f"{r['channel']}={r['n']}/{r['t']}" for r in Q(c, "SELECT channel, sum(ts IS NOT NULL) n, count(*) t FROM turns GROUP BY channel")))
    out("Email quote markers found: " + ", ".join(f"{r['quote_basis']}={r['n']}" for r in Q(c, "SELECT quote_basis, count(*) n FROM email_message_details GROUP BY 1")))
    out("Chat reply links stated by source: " + str(Q(c, "SELECT count(*) n FROM chat_message_details WHERE reply_to_turn_id IS NOT NULL")[0]["n"]) + " of " + str(Q(c, "SELECT count(*) n FROM chat_message_details")[0]["n"]))
    out("Relations: " + ", ".join(f"{r['rel_type']}/{r['provenance']}={r['n']}" for r in Q(c, "SELECT rel_type, provenance, count(*) n FROM turn_relations GROUP BY 1,2")))
    out("Dialogue acts: " + ", ".join(f"{r['value']}={r['n']}" for r in Q(c, "SELECT value, count(*) n FROM annotations WHERE kind='dialogue_act' GROUP BY value ORDER BY 2 DESC")))
    out("Segment topics: " + ", ".join(f"{r['topic_label']}={r['n']}" for r in Q(c, "SELECT topic_label, count(*) n FROM segments GROUP BY 1 ORDER BY 2 DESC")))


# ------------------------------------------------------------------------------------------------------------ sample
def _classify(c, cid):
    """Categories this container genuinely exhibits, judged from stored annotations/relations (not invented)."""
    acts = Counter(r["value"] for r in Q(c, "SELECT value FROM annotations WHERE container_id=? AND kind='dialogue_act'", cid))
    rel = Counter(r["rel_type"] for r in Q(c, "SELECT rel_type FROM turn_relations WHERE container_id=?", cid))
    n = Q(c, "SELECT turn_count FROM containers WHERE container_id=?", cid)[0]["turn_count"]
    segs = Q(c, "SELECT count(*) n FROM segments WHERE container_id=?", cid)[0]["n"]
    low = Q(c, "SELECT count(*) n FROM segments s WHERE container_id=? AND (topic_label='general_discussion' OR (SELECT count(*) FROM segment_turns st WHERE st.segment_id=s.segment_id)=1)", cid)[0]["n"]
    cats = []
    if n >= 4 and set(acts) <= {"information", "question", "agreement", "social"}:
        cats.append("ordinary")
    if n >= 6 or segs >= 3:
        cats.append("multi-message discussion")
    if acts["correction"] or rel["corrects"] or acts["rejection"]:
        cats.append("correction/disagreement")
    if rel["answers"] and acts["question"]:
        cats.append("question-and-answer")
    chan = Q(c, "SELECT channel FROM containers WHERE container_id=?", cid)[0]["channel"]
    if low or (chan == "Chat" and n <= 3):                  # a general_discussion or 1-turn segment, or a very short chat
        cats.append("fragmented/ambiguous context")
    return cats


def cmd_sample(c, a, src=None):
    src = src or Source()
    per = a.per_channel
    print(f"DETERMINISTIC SAMPLE: up to {per} containers per channel; the first container (by id) found for each category, then fill by id order.\n")
    for ch in CHANNELS:
        ids = [r["container_id"] for r in Q(c, "SELECT container_id FROM containers WHERE channel=? ORDER BY container_id", ch)]
        cats = {i: _classify(c, i) for i in ids}
        chosen, why = [], {}
        for want in ["ordinary", "multi-message discussion", "correction/disagreement", "question-and-answer", "fragmented/ambiguous context"]:
            hit = next((i for i in ids if want in cats[i] and i not in chosen), None)
            if hit and len(chosen) < per:
                chosen.append(hit); why[hit] = want
            elif not hit:
                print(f"[{ch}] category '{want}': UNAVAILABLE in the data (no container qualifies under the stated rules)")
        for i in ids:
            if len(chosen) >= per:
                break
            if i not in chosen:
                chosen.append(i); why[i] = "fill (id order)"
        print(f"\n######## {ch}: sample {chosen}")
        for i in chosen:
            print(f"\n--- {i}: selected as '{why[i]}'; categories exhibited: {cats[i]}")
            if a.full:
                cmd_show(c, argparse.Namespace(id=i), src)
            else:
                _sample_side_by_side(c, i, src)


def _sample_side_by_side(c, cid, src):
    k = Q(c, "SELECT * FROM containers WHERE container_id=?", cid)[0]
    print(f"    {k['container_kind']} {cid} client {k['client_id']} '{k['subject']}' {k['started_at']}")
    seg_of = {}
    for s in Q(c, "SELECT * FROM segments WHERE container_id=?", cid):
        for r in Q(c, "SELECT turn_id FROM segment_turns WHERE segment_id=?", s["segment_id"]):
            seg_of[r["turn_id"]] = s
    for t in Q(c, "SELECT * FROM turns WHERE container_id=? ORDER BY seq", cid):
        s = seg_of[t["turn_id"]]
        print(f"    SOURCE  {t['turn_id']}: {_one(src.text.get(t['turn_id'], '<missing>'), 400)}")
        print(f"    PARSED  seq={t['seq']} speaker={t['speaker_id']}[{t['attribution_status']}] seg={s['segment_id'][-6:]}:{s['topic_label']}({s['confidence']}) | {_fmt_ann(_anns(c, t['turn_id']))} | {_compare(src, t)}")
    rel = Q(c, "SELECT from_turn_id, rel_type, to_turn_id, basis FROM turn_relations WHERE container_id=?", cid)
    if rel:
        print("    RELATIONS " + "; ".join(f"{r['from_turn_id']} {r['rel_type']} {r['to_turn_id']} [{r['basis']}]" for r in rel))


# ------------------------------------------------------------------------------------------------------------ quality
def cmd_quality(c, a):
    """Candidate defects in the annotation rules, each with the original text and the reason it may be wrong.  Reports; never changes a rule."""
    from evidence import semantics as S
    N = a.examples
    turns = Q(c, "SELECT t.turn_id, t.container_id, t.seq, t.speaker_id, t.speaker_role, t.original_text, t.channel, (SELECT authored_text FROM email_message_details e WHERE e.turn_id=t.turn_id) at FROM turns t ORDER BY t.turn_id")
    acts = defaultdict(list)
    for r in Q(c, "SELECT target_id, value FROM annotations WHERE kind='dialogue_act'"):
        acts[r["target_id"]].append(r["value"])
    text = {t["turn_id"]: (t["at"] if t["at"] is not None else t["original_text"]) for t in turns}
    by_id = {t["turn_id"]: t for t in turns}

    def show(title, items, why):
        print(f"\n### {title}: {len(items)} candidates; showing {min(N, len(items))}\n    why suspect: {why}")
        for tid, note in items[:N]:
            t = by_id[tid]
            print(f"    - {tid} [{t['speaker_role'] or t['speaker_id']}] acts={acts[tid]}  {note}\n        \"{_one(text[tid], 260)}\"")

    # false positives
    show("QUESTION label without a real question", [(t, "'?' only inside a quote/rhetorical?") for t in acts if "question" in acts[t] and not re.search(r"\?\s*$|\?[\"')\s]*$", text[t].strip()) and text[t].count("?") == 1 and len(text[t]) > 200], "a '?' in the middle of a long statement may be a quoted or embedded question, not the turn's act")
    show("AGREEMENT from a leading 'Good/Right/Fine/OK/Yes' that continues with a contrary or new point", [(t, "") for t in acts if "agreement" in acts[t] and re.match(r"^\W*(good|right|okay|ok|yes|sure|fine)\b[^.?!]{0,3}[,.]?\s*(but|however|though|except)\b", text[t], re.I) or ("agreement" in acts[t] and re.match(r"^\W*(good|right)\b", text[t], re.I) and "?" in text[t])], "the opening word is a discourse marker, not agreement with the previous turn")
    show("REJECTION from a leading 'No' that is only an answer to a question or a social 'no problem'", [(t, "") for t in acts if "rejection" in acts[t] and re.match(r"^\W*no\b[,.]?\s*(problem|worries|rush|hurry|trouble|need)", text[t], re.I)], "'No problem' / 'no rush' is courtesy, not rejection of a proposal")
    show("REJECTION anywhere", [(t, "") for t in acts if "rejection" in acts[t]], "inspect whether the turn rejects an advisor proposal or just negates a fact")
    show("DECISION labels (client first-person or advisor-recorded)", [(t, "") for t in acts if "decision" in acts[t]], "cue-matched only; check each is a confirmed decision, not a restatement of a pending one")
    show("DECISION that also carries deferral/uncertainty cues", [(t, "") for t in acts if "decision" in acts[t] and (("deferral_unresolved" in acts[t]) or S._UNCERT.search(text[t]))], "a turn cannot be both settled and unresolved; the label is probably over-confident")
    show("COMMITMENT from a bare 'I'll / let me' that is not a task", [(t, "") for t in acts if "commitment" in acts[t] and len(text[t]) < 60], "short turns with 'I'll' may be acknowledgements")
    show("SOCIAL label on turns that are long", [(t, "") for t in acts if "social" in acts[t] and len(text[t].split()) > 10], "greeting prefix followed by substantive content")
    # false negatives
    show("QUESTION missed: interrogative opening without '?'", [(t, "") for t in acts if "question" not in acts[t] and re.match(r"^\W*(do|does|did|can|could|would|should|will|is|are|what|when|where|why|how|who|which|have|has)\b[^.!]{8,}$", text[t].strip(), re.I) and "?" not in text[t]], "interrogative form with no question mark")
    show("QUESTION missed: statement asking for something ('please confirm', 'tell me', 'can you' inside text) with no '?'", [(t, "") for t in acts if "question" not in acts[t] and re.search(r"\b(please (?:confirm|tell|let me know|send|check)|let me know|could you|can you|would you)\b", text[t], re.I)], "a request is not labelled as a question or any request act (the act set has no 'request')")
    show("CORRECTION missed: contradiction cues the regex lacks", [(t, "") for t in acts if "correction" not in acts[t] and re.search(r"\b(that's not right|not quite|not what i (?:said|meant)|wrong|incorrect|mislabel|overstat|understat|typo|should say|should have said|isn't (?:right|correct)|is not (?:right|correct)|i never said|didn't say)\b", text[t], re.I)], "wording of corrections varies; the cue list is narrow")
    show("DECISION missed: first-person 'we'll go with / let's do X' variants the cue list lacks", [(t, "") for t in acts if "decision" not in acts[t] and re.search(r"\b(i(?:'ll| will) (?:take|choose|pick|elect|keep|sell|buy|file)|let's proceed|we(?:'re| are) going (?:with|to)|i want to (?:go|proceed|elect|keep|sell))\b", text[t], re.I) and not S._EXPLORATORY.search(text[t])], "decisions phrased outside the cue list")
    # relations
    ans = Q(c, "SELECT r.from_turn_id f, r.to_turn_id t FROM turn_relations r WHERE r.rel_type='answers' AND r.basis LIKE 'rule_v1:%'")
    weak = []
    for r in ans:
        a_, q_ = by_id[r["f"]], by_id[r["t"]]
        ov = S._toks(text[r["f"]]) & S._toks(text[r["t"]])
        if not ov and a_["speaker_id"] != q_["speaker_id"]:
            weak.append((r["f"], f"'answers' {r['t']} but shares no distinctive word with it:  Q: \"{_one(text[r['t']], 140)}\""))
    show("ANSWERS relation with no lexical connection", weak, "adjacency is the only evidence; the next speaker may be answering something else, or the question may be rhetorical/one of several")
    multi = [(r["f"], f"answers {r['t']}") for r in ans if text[r["t"]].count("?") >= 2]
    show("ANSWERS to a turn containing two or more questions", multi, "which question is answered is not identified; the reply may answer only one")
    sameseg = Q(c, "SELECT count(*) n FROM turn_relations r WHERE r.rel_type='answers' AND r.basis LIKE 'rule_v1:%'")[0]["n"]
    cor = Q(c, "SELECT from_turn_id f, to_turn_id t, basis FROM turn_relations WHERE rel_type='corrects'")
    print(f"\n### CORRECTS relations: {len(cor)} in total (all shown; each should be read against 'does the later turn really correct the earlier one?')")
    for r in cor:
        print(f"    - {r['f']} corrects {r['t']}\n        later  : \"{_one(text[r['f']], 220)}\"\n        earlier: \"{_one(text[r['t']], 220)}\"")
    # topics
    hits = Counter()
    for t in turns:
        for k, v in S.topic_scores(text[t["turn_id"]]).items():
            hits[k] += 1
    print("\n### TOPIC cues: number of turns that fire each topic (breadth), and the cue words doing most of the work")
    for k, ws in S.TOPICS.items():
        cw = Counter()
        for t in turns:
            lo = " " + text[t["turn_id"]].lower() + " "
            for w in ws:
                if w in lo:
                    cw[w] += 1
        print(f"    {k:<28} turns={hits[k]:>4}  top cues: {', '.join(f'{w.strip()}({n})' for w, n in cw.most_common(4))}")
    sub = []
    for t in turns:
        lo = " " + text[t["turn_id"]].lower() + " "
        for w in ("home", "house", "plan ", "loan", "index", "fund", "stock", "business", "tax", "son ", "claim", "cash", "rent", "will "):
            if w in lo and not re.search(r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])", lo):
                sub.append((t["turn_id"], f"cue '{w.strip()}' matches inside a longer word"))
    show("TOPIC cue matching as a substring of another word", sub, "substring matching ('home' in 'homework', 'tax' in 'taxi', 'will ' as a verb)")
    # segments
    seg = Q(c, "SELECT segment_id, container_id, topic_label, confidence, start_seq, end_seq FROM segments")
    mixed, tiny = [], []
    for s in seg:
        tids = [r["turn_id"] for r in Q(c, "SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", s["segment_id"])]
        per = Counter()
        for tid in tids:
            for k in S.topic_scores(text[tid]):
                per[k] += 1
        if len(tids) >= 5 and len([k for k, v in per.items() if v >= max(2, len(tids) // 3)]) >= 2:
            mixed.append((s["segment_id"], f"{len(tids)} turns labelled {s['topic_label']}, but turns also cue {dict(per)}"))
        if len(tids) == 1:
            tiny.append((s["segment_id"], f"1-turn segment ({s['topic_label']}) in {s['container_id']}"))
    print(f"\n### SEGMENTS that may combine unrelated topics: {len(mixed)} candidates; showing {min(N, len(mixed))}\n    why suspect: hysteresis keeps one label while several topics keep firing")
    for sid, note in mixed[:N]:
        print(f"    - {sid}: {note}")
    print(f"\n### SEGMENTS that may split related turns: 1-turn segments={len(tiny)} ; answers-relations or chat replies that CROSS a segment boundary:")
    cross = Q(c, "SELECT r.from_turn_id f, r.to_turn_id t, r.rel_type, r.basis FROM turn_relations r JOIN segment_turns a ON a.turn_id=r.from_turn_id JOIN segment_turns b ON b.turn_id=r.to_turn_id WHERE a.segment_id<>b.segment_id")
    print(f"    {len(cross)} of {Q(c, 'SELECT count(*) n FROM turn_relations')[0]['n']} relations cross a segment boundary ({Counter(r['rel_type'] + '/' + r['basis'].split(':')[0] for r in cross).most_common()})")
    for r in cross[:N]:
        print(f"    - {r['f']} {r['rel_type']} {r['t']}  [{r['basis']}]   reply: \"{_one(text[r['f']], 110)}\"   to: \"{_one(text[r['t']], 110)}\"")
    for sid, note in tiny[:N]:
        print(f"    - {sid}: {note}")
    for ch in ("Email", "Chat"):
        n = Q(c, "SELECT count(*) n, sum(m) m FROM (SELECT container_id, count(*) m FROM segments WHERE container_id IN (SELECT container_id FROM containers WHERE channel=?) GROUP BY 1)", ch)[0]
        print(f"    {ch}: {n['n']} containers produced {n['m']} segments (an email thread / short chat is usually one exchange; more than one segment may split a single discussion)")


# ------------------------------------------------------------------------------------------------------------ retrieval
class ReadOnlyStore:
    """Duck-typed stand-in for EvidenceStore exposing only what EvidenceRetriever needs, so the write-capable store (which could create tables) is never constructed."""

    def __init__(self, path=DB):
        self.path = Path(path)
        c = connect(path)
        self.has_fts = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='turns_fts'").fetchone())
        c.close()

    def reader(self):
        return connect(self.path)


def cmd_retrieval(c, a):
    from evidence.retrieval import EvidenceRetriever, Requester
    store = ReadOnlyStore()
    R = EvidenceRetriever(store, Requester.advisor("ADVISOR-001"), on=date(2026, 6, 30))
    print("RETRIEVAL INSPECTION - requester: ADVISOR-001 (assigned to CLIENT-001 and CLIENT-004), as of 2026-06-30")
    print("MODE: every text query below is LEXICAL - SQLite FTS5 bm25 over the exact words of the turn, tokens joined with OR. There is NO semantic/embedding retrieval;")
    print("      a query that shares no words with the evidence will not find it.  Topic filters use the rule-based keyword labels, also lexical.\n")

    def spans(title, rows, n=4):
        print(f"\n## {title}  ({len(rows)} spans)")
        for r in rows[:n]:
            print(f"  {r['evidence_id']:<18} {r['channel']:<8} {str(r['when_ts'])[:16]:<16} {r['speaker_name'] or r['speaker_id']:<14} \"{_one(r['exact_text'], 170)}\"")

    q1 = "what did Kenji decide about the deferred compensation payout"
    spans(f"1. natural-language question, keyword matching: '{q1}'", R.find_spans("CLIENT-001", text=q1, limit=6))
    print("   note: bm25 ranks turns sharing the most/rarest words; 'decide' does not match 'decided' (no stemming), so decision turns are found only through other shared words.")
    q2 = "tell me about my pension payout choices"
    spans(f"1b. vocabulary mismatch: '{q2}'", R.find_spans("CLIENT-001", text=q2, limit=4))
    spans("1c. same intent via the rule-based topic filter (topic=retirement_planning), newest-first not ranked", R.find_spans("CLIENT-001", topic="retirement_planning", limit=4))
    spans("2. speaker filter: only CLIENT-001's own words about 'installments'", R.find_spans("CLIENT-001", text="installments", speaker_id="CLIENT-001", limit=6))
    spans("2b. the advisor's words on the same word", R.find_spans("CLIENT-001", text="installments", speaker_id="ADVISOR-001", limit=4))
    rows = R.find_spans("CLIENT-001", text="installments election form", limit=30)
    rows.sort(key=lambda r: (r["when_ts"], r["evidence_id"]))
    print("\n## 3. cross-channel chronology: lexical hits on 'installments election form' re-sorted by time (ranking discarded)")
    for r in rows[:12]:
        print(f"  {str(r['when_ts'])[:16]:<16} {r['channel']:<8} {r['container_id']:<9} {r['speaker_name'] or r['speaker_id']:<14} \"{_one(r['exact_text'], 120)}\"")
    print("   note: calls and original meetings have no turn time, so they sort at their container start; order within one container is by turn index only.")
    print("\n## 4. correction of an earlier statement: dialogue_act=correction, then the 'corrects' relation")
    corr = R.find_spans("CLIENT-001", dialogue_act="correction", limit=6) + R.find_spans("CLIENT-004", dialogue_act="correction", limit=3)
    if not corr:
        print("  UNAVAILABLE: no turn in these clients' evidence carries a correction label")
    for r in corr[:4]:
        ctx = R.span_context(r["turn_id"], window=1)
        rel = [x for x in ctx["relations"] if x["rel_type"] == "corrects" and x["from_turn_id"] == r["turn_id"]]
        print(f"  {r['evidence_id']} ({r['speaker_name']}): \"{_one(r['exact_text'], 200)}\"")
        if rel:
            tgt = next(t for t in ctx["container_turns"] if t["turn_id"] == rel[0]["to_turn_id"])
            print(f"      corrects -> {tgt['turn_id']} ({tgt['speaker_name']}): \"{_one(tgt['original_text'], 200)}\"  [{rel[0]['basis']}]")
        else:
            print("      no 'corrects' relation was derived (the label alone is a cue match)")
    print("\n## 5. client's explicit decision vs advisor suggestion/proposal (cue labels, not verified decisions)")
    spans("5a. dialogue_act=decision, speaker = the client", R.find_spans("CLIENT-001", dialogue_act="decision", speaker_id="CLIENT-001", limit=4))
    spans("5b. dialogue_act=decision, speaker = the advisor (an advisor RECORDING a decision)", R.find_spans("CLIENT-001", dialogue_act="decision", speaker_id="ADVISOR-001", limit=3))
    spans("5c. dialogue_act=suggestion, speaker = the advisor", R.find_spans("CLIENT-001", dialogue_act="suggestion", speaker_id="ADVISOR-001", limit=3))
    spans("5d. dialogue_act=proposal, speaker = the advisor", R.find_spans("CLIENT-001", dialogue_act="proposal", speaker_id="ADVISOR-001", limit=3))
    print("\n## 6. expanding a hit: span -> segment -> full communication")
    hit = R.find_spans("CLIENT-001", text="election form submitted Thursday", limit=1)
    if hit:
        ctx = R.span_context(hit[0]["evidence_id"], window=1)
        print(f"  span {hit[0]['evidence_id']}: \"{_one(hit[0]['exact_text'], 160)}\"")
        for s in ctx["segments"]:
            print(f"  segment {s['segment_id']} topic={s['topic_label']} conf={s['confidence']} turns={s['turn_ids']}")
        print(f"  container {ctx['container']['container_id']} ({ctx['container']['channel']}, {ctx['container']['started_at']}) '{ctx['container']['subject']}' - {len(ctx['container_turns'])} turns retrievable")
        print(f"  neighbours before: {[t['turn_id'] for t in ctx['before']]} after: {[t['turn_id'] for t in ctx['after']]}")
        print(f"  provenance: {ctx['source_references']['source_file']} / {ctx['source_references']['turn_source_ref']}  revision {ctx['source_references']['revision']}  parser {ctx['source_references']['parser_version']}")
        d = R.derived_for_container(ctx["container"]["container_id"])
        print(f"  derived CRM records that cite this container (separate layer): {[x['crm_record_id'] for x in d]}")
    R.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only inspection of build/cmf_evidence.db")
    ap.add_argument("--db", default=str(DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("containers"); s.add_argument("--channel", choices=CHANNELS)
    for n in ("show", "segments", "segment", "annotations", "relations"):
        s = sub.add_parser(n); s.add_argument("id")
    s = sub.add_parser("crm"); s.add_argument("--client"); s.add_argument("--id")
    s = sub.add_parser("timeline"); s.add_argument("client")
    sub.add_parser("report")
    s = sub.add_parser("sample"); s.add_argument("--per-channel", type=int, default=5); s.add_argument("--full", action="store_true", help="hierarchical view instead of side-by-side")
    s = sub.add_parser("quality"); s.add_argument("--examples", type=int, default=6)
    sub.add_parser("retrieval")
    a = ap.parse_args(argv)
    c = connect(a.db)
    {"containers": cmd_containers, "show": cmd_show, "segments": cmd_segments, "segment": cmd_segment, "annotations": cmd_annotations, "relations": cmd_relations,
     "crm": cmd_crm, "timeline": cmd_timeline, "report": cmd_report, "sample": cmd_sample, "quality": cmd_quality, "retrieval": cmd_retrieval}[a.cmd](c, a)
    c.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
