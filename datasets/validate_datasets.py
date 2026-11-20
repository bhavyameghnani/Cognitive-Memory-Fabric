"""Validate the four-channel synthetic dataset. Standard library only, read-only.

Usage:  python -I datasets/validate_datasets.py        (exit code 0 = all checks passed)
Covers the 13 mandatory checks from the dataset brief plus extra integrity, governance and attribution checks.
"""
import csv
import hashlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "datasets"
CALLS_SRC = ROOT / "data" / "conversations.csv"

EXPECT = {  # exact channel label -> exact source platform (brief, slide 5)
    "Meetings": "OneDrive and Teams",
    "Email": "Exchange mailbox",
    "Calls": "Telephony platform",
    "Chat": "Messaging platform",
}
WINDOW = (datetime(2026, 1, 2), datetime(2026, 6, 30, 23, 59, 59))
# Entity names that the calls attach to one client only. Verified against the CSV below, then used
# to detect cross-client leakage in new records.
OWN_NAMES = {
    1: ["Kenji", "Tanaka", "Mariko", "Yuki", "Northgate", "Osaka", "Hannah", "Park"],
    2: ["Rebecca", "Holloway", "David", "Ethan", "Dorothy", "Lakeshore", "Luis", "Mei", "Chu"],
    3: ["Sofia", "Alvarez", "Mateo", "Lucia"],
    4: ["Grace", "Adeyemi", "Samuel", "Tobi", "Folake"],
    5: ["Lucas", "Moreau", "Camille", "Vantora"],
    6: ["Peg", "Sullivan", "Frank", "Colleen", "Brian", "Maplewood", "Dennis", "Hall"],
}
GENERIC_OK = set("""Dear Hi Hello Thanks Thank Best Kind Regards Warm Yes No Okay Ok Fine Good Please Understood Agreed
Right Correct Sure Perfect Great Lovely Confirmed Confirming Quick Not Nothing Next Then Today Tomorrow Monday Tuesday
Wednesday Thursday Friday Saturday Sunday January February March April May June July August September October November
December Medicare Social Security Roth Seoul Lisbon Portugal Portuguese Treasury Treasuries Treasury Teams OneDrive
Exchange U S USD PDF IRA CPA HR RE Fine Amazing Estimates Version Sell Selling Between Both Second Two First Three
Only Some Those That This What Who When Where Why How And But So If It I We You He She They Our Your My His Her Their
There Here Let Is Are Was Were Do Does Did Have Has Had Can Could Would Should Will Shall May Might Must The A An
For From Of On In At By To With Without About After Before During Following Since Until While Also Maybe Idk Lol Ugh
Hmm Oh Yeah Nope Nothing Everything Anything Someone Something Anyone Everyone Because Whether Either Neither One
Summary Short Model Models Attached Please Estimates Withholding Elder Law Church Frank Hannah Dennis""".split())

results = []   # (name, ok, detail)
warnings = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ts(s):
    return datetime.fromisoformat(s)


def words(s):
    return re.findall(r"[a-z0-9']+", s.lower())


def _load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def main():
    # ------------------------------------------------------------------ load
    calls = read(CALLS_SRC)
    mt = read(DS / "meetings/meetings.csv"); tu = read(DS / "meetings/meeting_turns.csv"); md = read(DS / "meetings/meeting_metadata.csv")
    em = read(DS / "emails/emails.csv"); th = read(DS / "emails/email_threads.csv")
    ch = read(DS / "chats/chats.csv"); cv = read(DS / "chats/chat_conversations.csv")
    cm = read(DS / "calls/calls_metadata.csv"); omm = read(DS / "meetings/original_meetings_metadata.csv")
    man = read(DS / "source_manifest.csv")
    crm = {k: read(DS / "crm" / f"crm_{k}.csv") for k in ("meetings", "email", "calls", "chat")}
    rr = read(DS / "evaluation/routine_records.csv"); sc = read(DS / "evaluation/scenarios.csv"); sl = read(DS / "evaluation/scenario_links.csv"); reg = read(DS / "character_registry.csv")

    call_ids = {r["interaction_id"] for r in calls}
    man_ch = {r["interaction_id"]: r["assigned_channel"] for r in man}
    call_client = {r["interaction_id"]: r["client_id"] for r in calls}
    call_time = {r["interaction_id"]: ts(r["interaction_datetime"]) for r in calls}
    call_turns = Counter(r["interaction_id"] for r in calls)
    csv_clients = {r["client_id"] for r in calls}
    csv_advisors = {r["advisor_id"] for r in calls}
    adv_of = {}
    for r in calls: adv_of.setdefault(r["client_id"], set()).add(r["advisor_id"])
    adv_of = {k: next(iter(v)) for k, v in adv_of.items() if len(v) == 1}
    corpus = " ".join(r["utterance"] for r in calls)

    # ------------------------------------------------------------------ 1 characters
    names = {r["display_name"]: r for r in reg}
    bad = []
    for r in reg:
        first = r["first_name"]
        hits = [c for c in calls if re.search(rf"\b{re.escape(first)}\b", c["utterance"])]
        if not hits: bad.append(f"{r['display_name']}: first name not found in conversations.csv")
        if r["character_id"].startswith("CLIENT-"):
            owners = {c["client_id"] for c in hits}
            if owners != {r["character_id"]}: bad.append(f"{first} appears under {sorted(owners)}, expected only {r['character_id']}")
        if r["character_id"].startswith("ADVISOR-"):
            owners = {c["advisor_id"] for c in hits}
            if owners != {r["character_id"]}: bad.append(f"{first} appears under advisors {sorted(owners)}")
        if r["role"].startswith("third_party"):
            if not re.search(rf"\b{re.escape(r['display_name'])}\b", corpus): bad.append(f"{r['display_name']}: full name not in conversations.csv")
    for r in reg:
        if r["role"].startswith(("family", "other", "third_party")) and r["character_id"].startswith("MENT-"):
            hits = {c["client_id"] for c in calls if re.search(r"\b" + re.escape(r["first_name"]) + r"\b", c["utterance"])}
            if r["client_scope"] not in hits: bad.append(f"{r['display_name']} not found in {r['client_scope']}'s interactions")
    used_names = set()
    for r in tu: used_names.add(r["speaker_name"])
    for r in em: used_names.add(r["sender_name"])
    for r in ch: used_names.add(r["sender_name"])
    for r in md: used_names.update(r["participant_names"].split("|"))
    unknown = used_names - set(names)
    if unknown: bad.append(f"names not in registry: {sorted(unknown)}")
    reg_emails = {r["email"] for r in reg}
    for r in em:
        for col in ("sender_email", "to_emails", "cc_emails"):
            for e in filter(None, r[col].split("|")):
                if e not in reg_emails: bad.append(f"{r['email_id']}: unknown address {e}")
    check("1. every generated character exists in conversations.csv", not bad, "; ".join(bad[:5]) or f"{len(used_names)} names used, all in registry and attested in the CSV")

    # unseen-word heuristic (warning only): capitalised words in new text never seen in the calls
    vocab = set(words(corpus)); regtok = {t.lower() for r in reg for t in r["display_name"].split()}
    texts = [r["utterance"] for r in tu] + [r["body"] for r in em] + [r["message_text"] for r in ch] + [r["subject"] for r in em]
    unseen = Counter()
    for t in texts:
        for w in re.findall(r"\b[A-Z][a-z]{2,}\b", t):
            if w.lower() not in vocab and w.lower() not in regtok and w not in GENERIC_OK: unseen[w] += 1
    if unseen: warnings.append(f"capitalised words not seen in conversations.csv (review for stray names): {dict(unseen)}")

    # ------------------------------------------------------------------ 2 client ids
    new_client_ids = {r["client_id"] for rows in (mt, tu, md, em, th, ch, cv, sc, sl) for r in rows}
    new_adv_ids = {r["advisor_id"] for rows in (mt, em, th, ch, cv, sc) for r in rows}
    bad = []
    if not new_client_ids <= csv_clients: bad.append(f"unknown clients {sorted(new_client_ids - csv_clients)}")
    if not new_adv_ids <= csv_advisors: bad.append(f"unknown advisors {sorted(new_adv_ids - csv_advisors)}")
    for rows in (mt, em, th, ch, cv, sc):
        for r in rows:
            if adv_of.get(r["client_id"]) != r["advisor_id"]: bad.append(f"{r.get('meeting_id') or r.get('email_id') or r.get('thread_id') or r.get('message_id') or r.get('chat_conversation_id') or r['scenario_id']}: advisor mismatch")
    for r in reg:
        if r["character_id"].startswith("CLIENT-") and r["advisor_scope"] != adv_of[r["character_id"]]: bad.append(f"registry advisor for {r['character_id']}")
    check("2. every generated client ID exists in conversations.csv (advisor pairing preserved)", not bad, "; ".join(bad[:5]) or f"{len(new_client_ids)} clients, {len(new_adv_ids)} advisors")

    # ------------------------------------------------------------------ 3 calls untouched
    lock = (DS / "calls/conversations.sha256").read_text(encoding="utf-8").splitlines()
    locked = lock[0].split()[0]
    actual = hashlib.sha256(CALLS_SRC.read_bytes()).hexdigest()
    bad = []
    if locked != actual: bad.append("sha256 differs from the value recorded at build time")
    if len(calls) != 2199 or len(call_ids) != 150: bad.append(f"row/interaction count changed ({len(calls)}/{len(call_ids)})")
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True)
        in_repo = top.returncode == 0 and Path(top.stdout.strip()).resolve() == ROOT.resolve()
        g = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "data/conversations.csv"], cwd=ROOT,
                           capture_output=True) if in_repo else None
        if g is None: gstatus = "git comparison skipped (not a git checkout)"
        elif g.returncode == 0: gstatus = "identical to git HEAD"
        elif g.returncode == 1:
            gstatus = "DIFFERS from git HEAD"; bad.append("git diff reports changes to data/conversations.csv")
        else: gstatus = "git comparison skipped (not a git checkout)"
    except Exception as e:  # git unavailable
        gstatus = f"git check skipped ({e.__class__.__name__})"
    # metadata agrees with source: calls_metadata + original_meetings_metadata partition the 150 interactions
    if len(cm) + len(omm) != len(call_ids) or {r["call_source_id"] for r in cm} & {r["call_source_id"] for r in omm}:
        bad.append("calls_metadata + original_meetings_metadata do not partition the interactions")
    for r in cm + omm:
        i = r["call_source_id"]
        if i not in call_ids or call_turns[i] != int(r["turn_count"]) or call_client[i] != r["client_id"] or r["interaction_datetime"] != call_time[i].strftime("%Y-%m-%dT%H:%M:%S"):
            bad.append(f"original-interaction metadata mismatch for {i}")
    stray = [p for p in DS.rglob("*") if p.is_file() and p.name.lower().startswith("conversations") and p.suffix == ".csv"]
    if stray: bad.append(f"copy of conversations.csv inside datasets/: {stray}")
    check("3. original Calls data not modified", not bad, "; ".join(bad[:5]) or f"sha256 matches lock, {gstatus}, 2199 rows / 150 interactions")

    # ------------------------------------------------------------------ 4 & 5 channels / platform names
    seen = {}
    for label, rows in (("Meetings", mt), ("Meetings", tu), ("Meetings", md), ("Meetings", omm), ("Email", em), ("Email", th), ("Chat", ch), ("Chat", cv), ("Calls", cm)):
        for r in rows: seen.setdefault((r["channel"], r["source_platform"]), 0); seen[(r["channel"], r["source_platform"])] += 1
    chan_present = {c for c, _ in seen}
    check("4. all four channels represented", chan_present == set(EXPECT), f"present: {sorted(chan_present)}")
    wrong = [k for k in seen if EXPECT.get(k[0]) != k[1]]
    for r in sl:
        if r["channel"] not in EXPECT: wrong.append((r["channel"], "scenario_links"))
    check("5. channel labels and platform names match slide 5 exactly", not wrong and len(seen) == 4, f"pairs used: {sorted(seen)}")

    # ------------------------------------------------------------------ 6 stable identifiers
    bad = []
    def uniq(rows, col, pat, label):
        vals = [r[col] for r in rows]
        if len(vals) != len(set(vals)): bad.append(f"{label}: duplicate {col}")
        for v in vals:
            if not re.fullmatch(pat, v): bad.append(f"{label}: bad {col} {v!r}")
    uniq(mt, "meeting_id", r"MTG-\d{3}", "meetings"); uniq(tu, "turn_id", r"MTG-\d{3}-T\d{2}", "turns")
    uniq(em, "email_id", r"EML-\d{3}", "emails"); uniq(em, "message_id", r"<EML-\d{3}\.THR-\d{3}@exchange\.synthetic\.example>", "emails")
    uniq(th, "thread_id", r"THR-\d{3}", "threads"); uniq(ch, "message_id", r"CHM-\d{4}", "chats")
    uniq(cv, "chat_conversation_id", r"CHT-\d{3}", "chat conversations"); uniq(cm + omm, "call_source_id", r"INT-\d{3}", "original interactions")
    uniq(sc, "scenario_id", r"SCN-C\d-\d{2}", "scenarios")
    for rows, c in ((mt, "meeting_id"), (md, "meeting_id"), (th, "thread_id"), (cv, "chat_conversation_id")):
        pass
    if {r["meeting_id"] for r in md} != {r["meeting_id"] for r in mt}: bad.append("meeting_metadata ids != meetings ids")
    if {r["thread_id"] for r in em} != {r["thread_id"] for r in th}: bad.append("emails thread ids != email_threads ids")
    if {r["chat_conversation_id"] for r in ch} != {r["chat_conversation_id"] for r in cv}: bad.append("chats conversation ids != chat_conversations ids")
    for r in tu + em + ch:
        if not r.get("consent_status") == "synthetic": bad.append("non-synthetic consent label")
    check("6. every record has a stable source identifier", not bad, "; ".join(bad[:5]) or f"{len(mt)} meetings, {len(tu)} turns, {len(th)} threads, {len(em)} emails, {len(cv)} chats, {len(ch)} messages: ids unique and well formed")

    # ------------------------------------------------------------------ 7 timestamps
    bad = []
    meet = {r["meeting_id"]: r for r in mt}
    by_meeting = defaultdict(list)
    for r in tu: by_meeting[r["meeting_id"]].append(r)
    all_ts = []
    for m in mt:
        s, e = ts(m["meeting_start_datetime"]), ts(m["meeting_end_datetime"])
        all_ts += [s, e]
        if not s < e: bad.append(f"{m['meeting_id']} end <= start")
        last = None
        for t in sorted(by_meeting[m["meeting_id"]], key=lambda r: int(r["turn_index"])):
            x = ts(t["turn_timestamp"]); all_ts.append(x)
            if x < s or x > e or (last and x < last): bad.append(f"{t['turn_id']} out of order")
            last = x
    by_thread = defaultdict(list)
    for r in em: by_thread[r["thread_id"]].append(r)
    for tid, msgs in by_thread.items():
        msgs.sort(key=lambda r: int(r["message_seq"]))
        for a, b in zip(msgs, msgs[1:]):
            if not ts(a["timestamp"]) < ts(b["timestamp"]): bad.append(f"{tid} message {b['message_seq']} not after previous")
        all_ts += [ts(r["timestamp"]) for r in msgs]
    by_chat = defaultdict(list)
    for r in ch: by_chat[r["chat_conversation_id"]].append(r)
    for cid_, msgs in by_chat.items():
        msgs.sort(key=lambda r: int(r["message_seq"]))
        for a, b in zip(msgs, msgs[1:]):
            if ts(b["timestamp"]) < ts(a["timestamp"]): bad.append(f"{cid_} message {b['message_seq']} earlier than previous")
        all_ts += [ts(r["timestamp"]) for r in msgs]
    if any(x < WINDOW[0] or x > WINDOW[1] for x in all_ts): bad.append(f"timestamp outside the calls window {WINDOW[0].date()}..{WINDOW[1].date()}")
    for r in th:
        t = sorted(by_thread[r["thread_id"]], key=lambda x: int(x["message_seq"]))
        if r["first_message_timestamp"] != t[0]["timestamp"] or r["last_message_timestamp"] != t[-1]["timestamp"] or int(r["message_count"]) != len(t): bad.append(f"{r['thread_id']} header mismatch")
    # time span helper for cross-reference ordering
    span = {r["meeting_id"]: (ts(r["meeting_start_datetime"]), ts(r["meeting_end_datetime"])) for r in mt}
    span.update({r["thread_id"]: (ts(r["first_message_timestamp"]), ts(r["last_message_timestamp"])) for r in th})
    span.update({r["chat_conversation_id"]: (ts(r["first_message_timestamp"]), ts(r["last_message_timestamp"])) for r in cv})
    span.update({i: (t, t) for i, t in call_time.items()})
    check("7. timestamps valid and chronologically coherent", not bad, "; ".join(bad[:5]) or f"{len(all_ts)} timestamps parsed; ordered within meetings, threads and chats; all inside {WINDOW[0].date()}..{WINDOW[1].date()}")

    # ------------------------------------------------------------------ 8 email threads
    bad = []
    mids = {r["message_id"]: r for r in em}
    ext_scope = {r["character_id"]: r["client_scope"] for r in reg if r["character_id"].startswith("EXT-")}
    thr = {r["thread_id"]: r for r in th}
    for tid, msgs in by_thread.items():
        tpart = set(thr[tid]["participant_ids"].split("|"))
        prev = None
        for r in msgs:
            parts = {r["sender_id"]} | set(filter(None, r["to_ids"].split("|"))) | set(filter(None, r["cc_ids"].split("|")))
            if r["sender_id"] in set(r["to_ids"].split("|")) | set(filter(None, r["cc_ids"].split("|"))): bad.append(f"{r['email_id']} sender among recipients")
            if not parts <= tpart: bad.append(f"{r['email_id']} participant outside thread set")
            if r["client_id"] not in parts: bad.append(f"{r['email_id']} client not a participant")
            for p in parts:
                if p.startswith("EXT-") and ext_scope[p] != r["client_id"]: bad.append(f"{r['email_id']} third party {p} outside its client scope")
                if p.startswith("CLIENT-") and p != r["client_id"]: bad.append(f"{r['email_id']} other client {p}")
                if p.startswith("ADVISOR-") and p != r["advisor_id"]: bad.append(f"{r['email_id']} other advisor {p}")
            if prev is None:
                if r["in_reply_to"] or r["references"]: bad.append(f"{r['email_id']} first message has reply headers")
            else:
                if r["in_reply_to"] != prev["message_id"]: bad.append(f"{r['email_id']} in_reply_to wrong")
                if r["references"].split() != prev["references"].split() + [prev["message_id"]]: bad.append(f"{r['email_id']} references chain wrong")
                pprev = {prev["sender_id"]} | set(filter(None, prev["to_ids"].split("|"))) | set(filter(None, prev["cc_ids"].split("|")))
                if r["sender_id"] not in pprev: bad.append(f"{r['email_id']} sender was not on the message it replies to")
                if not r["subject"].startswith("RE: "): bad.append(f"{r['email_id']} subject not RE:")
            prev = r
        if thr[tid]["includes_third_party"] != ("true" if any(p.startswith("EXT-") for p in tpart) else "false"): bad.append(f"{tid} third-party flag")
    check("8. email threads have consistent sender/recipient relationships", not bad, "; ".join(bad[:5]) or f"{len(th)} threads / {len(em)} messages: reply headers, participants and third-party scope consistent")

    # ------------------------------------------------------------------ 9 meeting turns
    bad = []
    for m in mt:
        rows = sorted(by_meeting[m["meeting_id"]], key=lambda r: int(r["turn_index"]))
        if [int(r["turn_index"]) for r in rows] != list(range(1, len(rows) + 1)): bad.append(f"{m['meeting_id']} turn_index not 1..n")
        if int(m["turn_count"]) != len(rows): bad.append(f"{m['meeting_id']} turn_count")
        allowed = {m["advisor_id"]: "Advisor", m["client_id"]: "Client"}
        for a, b in zip(rows, rows[1:]):
            if a["speaker_id"] == b["speaker_id"]: bad.append(f"{b['turn_id']} consecutive turns by same speaker (calls convention merges these)")
        for r in rows:
            if allowed.get(r["speaker_id"]) != r["speaker_role"]: bad.append(f"{r['turn_id']} speaker/role invalid")
            if r["client_id"] != m["client_id"] or not r["utterance"].strip(): bad.append(f"{r['turn_id']} client/utterance invalid")
        if rows[0]["speaker_role"] != "Advisor": bad.append(f"{m['meeting_id']} should open with the advisor")
    mdm = {r["meeting_id"]: r for r in md}
    for m in mt:
        if set(mdm[m["meeting_id"]]["participant_ids"].split("|")) != {m["advisor_id"], m["client_id"]}: bad.append(f"{m['meeting_id']} participants")
        if mdm[m["meeting_id"]]["organizer_id"] != m["advisor_id"]: bad.append(f"{m['meeting_id']} organizer")
        if not mdm[m["meeting_id"]]["recording_ref"].startswith("onedrive://") : bad.append(f"{m['meeting_id']} recording_ref")
    check("9. meeting turns have valid speaker attribution and turn indices", not bad, "; ".join(bad[:5]) or f"{len(tu)} turns across {len(mt)} meetings")

    # ------------------------------------------------------------------ 10 chats
    bad = []
    cvm = {r["chat_conversation_id"]: r for r in cv}
    cmid = {r["message_id"]: r for r in ch}
    for r in ch:
        c = cvm.get(r["chat_conversation_id"])
        if not c: bad.append(f"{r['message_id']} unknown conversation"); continue
        if {r["sender_id"], r["recipient_id"]} != {c["client_id"], c["advisor_id"]} or r["sender_id"] == r["recipient_id"]: bad.append(f"{r['message_id']} sender/recipient")
        if {"Advisor": r["advisor_id"], "Client": r["client_id"]}.get(r["sender_role"]) != r["sender_id"]: bad.append(f"{r['message_id']} role/id")
        if r["client_id"] != c["client_id"]: bad.append(f"{r['message_id']} client mismatch")
        if r["reply_to_message_id"]:
            p = cmid.get(r["reply_to_message_id"])
            if not p or p["chat_conversation_id"] != r["chat_conversation_id"] or int(p["message_seq"]) >= int(r["message_seq"]) or ts(p["timestamp"]) > ts(r["timestamp"]): bad.append(f"{r['message_id']} bad reply_to")
    for cid_, msgs in by_chat.items():
        if [int(r["message_seq"]) for r in msgs] != list(range(1, len(msgs) + 1)): bad.append(f"{cid_} message_seq")
        if int(cvm[cid_]["message_count"]) != len(msgs): bad.append(f"{cid_} message_count")
    check("10. chat messages have valid sender and conversation references", not bad, "; ".join(bad[:5]) or f"{len(ch)} messages in {len(cv)} conversations")

    # ------------------------------------------------------------------ 11 cross-channel references
    bad = []
    all_ids = call_ids | set(meet) | set(thr) | set(cvm)
    rec_client = {**call_client, **{m: meet[m]["client_id"] for m in meet}, **{t: thr[t]["client_id"] for t in thr}, **{c: cvm[c]["client_id"] for c in cvm}}
    refs_total = 0
    for rows, key in ((md, "meeting_id"), (th, "thread_id"), (cv, "chat_conversation_id")):
        for r in rows:
            for x in filter(None, r["references_source_ids"].split("|")):
                refs_total += 1
                if x not in all_ids: bad.append(f"{r[key]} -> missing {x}"); continue
                if rec_client[x] != r["client_id"]: bad.append(f"{r[key]} -> {x} belongs to another client")
                if span[x][0] > span[r[key]][1]: bad.append(f"{r[key]} references later record {x}")
                if x == r[key]: bad.append(f"{r[key]} self reference")
    for r in sl:
        if r["source_id"] not in all_ids: bad.append(f"scenario {r['scenario_id']} -> missing {r['source_id']}"); continue
        if rec_client[r["source_id"]] != r["client_id"]: bad.append(f"scenario {r['scenario_id']} -> {r['source_id']} wrong client")
        expect_ch = man_ch.get(r["source_id"]) if r["source_id"].startswith("INT-") else "Meetings" if r["source_id"].startswith("MTG-") else "Email" if r["source_id"].startswith("THR-") else "Chat"
        if r["channel"] != expect_ch: bad.append(f"scenario link channel {r['source_id']}")
        if ts(r["source_timestamp"]) != span[r["source_id"]][0]: bad.append(f"scenario link timestamp {r['source_id']}")
    by_s = defaultdict(list)
    for r in sl: by_s[r["scenario_id"]].append(r)
    for sid, rows in by_s.items():
        rows.sort(key=lambda r: int(r["sequence_no"]))
        if [ts(r["source_timestamp"]) for r in rows] != sorted(ts(r["source_timestamp"]) for r in rows): bad.append(f"{sid} links not chronological")
        if len({r["source_id"] for r in rows}) != len(rows): bad.append(f"{sid} duplicate source in links")
    for s in sc:
        if int(s["record_count"]) != len(by_s[s["scenario_id"]]): bad.append(f"{s['scenario_id']} record_count")
    for r in cm + omm:
        want = sorted({x["scenario_id"] for x in sl if x["source_id"] == r["call_source_id"]})
        if r["linked_scenario_ids"].split("|") != (want or [""]): bad.append(f"calls_metadata link list {r['call_source_id']}")
    # every new record is in at least one scenario
    linked = {r["source_id"] for r in sl}
    routine_ids = {r["record_id"] for r in rr}
    orphans = [i for i in list(meet) + list(thr) + list(cvm) if i not in linked]
    if set(orphans) != routine_ids: bad.append(f"records in no scenario {sorted(orphans)} != declared routine records {sorted(routine_ids)}")
    check("11. cross-channel references point to existing, earlier, same-client records", not bad, "; ".join(bad[:5]) or f"{refs_total} explicit references + {len(sl)} scenario links resolve")

    # ------------------------------------------------------------------ 12 client separation
    bad = []
    # verify the entity-name ownership claim against the CSV itself
    own_ok = {}
    for n, lst in OWN_NAMES.items():
        for nm in lst:
            owners = {c["client_id"] for c in calls if re.search(rf"\b{nm}\b", c["utterance"])}
            if owners and owners != {f"CLIENT-{n:03d}"}: warnings.append(f"name {nm} appears under {sorted(owners)} in the calls; excluded from leakage check")
            else: own_ok[nm] = n
    def scan(client_id, text, label):
        n = int(client_id[-3:])
        for nm, owner in own_ok.items():
            if owner != n and re.search(rf"\b{nm}\b", text): bad.append(f"{label} (client {n}) mentions {nm} (client {owner})")
    for r in tu: scan(r["client_id"], r["utterance"], r["turn_id"])
    for r in em: scan(r["client_id"], r["body"] + " " + r["subject"], r["email_id"])
    for r in ch: scan(r["client_id"], r["message_text"], r["message_id"])
    for r in mt: scan(r["client_id"], r["subject"], r["meeting_id"])
    for src, key in ((tu, "turn_id"), (ch, "message_id"), (em, "email_id")):
        for r in src:
            if r["client_id"] != {**{m: meet[m]["client_id"] for m in meet}, **{c: cvm[c]["client_id"] for c in cvm}, **{t: thr[t]["client_id"] for t in thr}}.get(r.get("meeting_id") or r.get("chat_conversation_id") or r.get("thread_id")): bad.append(f"{r[key]} client differs from its container")
    # metadata carries client scope
    for r in md:
        if r["client_scope"] != r["client_id"]: bad.append(f"{r['meeting_id']} client_scope")
    check("12. no client's records assigned to or mentioning another client", not bad, "; ".join(bad[:5]) or "container/client ids consistent; no other client's names or family appear in any record")

    # ------------------------------------------------------------------ 13 variety / test coverage
    bad, info = [], {}
    alltext = {"meet": [r["utterance"] for r in tu], "mail": [r["body"] for r in em], "chat": [r["message_text"] for r in ch]}
    pat = {
        "correction": r"\bcorrect(ion|ed|ing)?\b|\bclarif|\bI should clarify\b|\bnot extra tuition\b|\bwas wrong\b",
        "tentative": r"\b(might|maybe|inclined|leaning|thinking about|not deciding|not decided|an idea|possibility|only a thought|hasn.t decided|haven.t decided|undecided|idk)\b",
        "decision": r"\b(decided|you have decided|confirming|i want the|i.ve decided|decision)\b",
        "advisor_suggestion": r"\b(my suggestion|i.d suggest|my recommendation|i recommend|my view is|my instinct|i lean|recommendation, not)\b",
        "question": r"\?",
        "commitment": r"\b(i.ll send|i will send|i.ll (do|mail|call|model|show|record|ask|chase|message)|by friday|will do|i.ll keep|before our next)\b",
        "pending": r"\b(pending|not yet|still waiting|hasn.t arrived|still hasn.t|open|overdue|until I|can.t change)\b",
    }
    for k, p in pat.items():
        for ch_name, texts_ in alltext.items():
            info[(k, ch_name)] = sum(1 for t in texts_ if re.search(p, t, re.I))
    for k in pat:
        for ch_name in alltext:
            if k in ("decision", "advisor_suggestion", "correction") and ch_name == "chat" and k == "advisor_suggestion": pass
    need = [("correction", "meet"), ("correction", "mail"), ("correction", "chat"), ("tentative", "meet"), ("tentative", "mail"), ("tentative", "chat"),
            ("decision", "mail"), ("decision", "meet"), ("advisor_suggestion", "mail"), ("advisor_suggestion", "meet"), ("advisor_suggestion", "chat"),
            ("commitment", "meet"), ("commitment", "mail"), ("commitment", "chat"), ("pending", "meet"), ("pending", "mail"), ("question", "meet"), ("question", "mail"), ("question", "chat")]
    for k in need:
        if info[k] < 2: bad.append(f"too few {k[0]} signals in {k[1]} ({info[k]})")
    dims = Counter(d for s in sc for d in s["test_dimensions"].split("|"))
    for d in ("context", "certainty", "currency", "provenance", "governance"):
        if dims[d] < 3: bad.append(f"dimension {d} covered by only {dims[d]} scenarios")
    corr = [s for s in sc if any("correct" in r["storyline_note"].lower() for r in by_s[s["scenario_id"]])]
    if not (0 < len(corr) < len(sc)): bad.append("corrections should appear in some but not all scenarios")
    if len(corr) > 0.75 * len(sc): bad.append("too many scenarios contain corrections")
    stable = [s for s in sc if s not in corr]
    per_client = defaultdict(set)
    for s in sc: per_client[s["client_id"]].add(s["scenario_id"])
    for c in csv_clients:
        chans = defaultdict(int)
        for r in sl:
            if r["client_id"] == c: chans[r["channel"]] += 1
        if set(chans) != set(EXPECT): bad.append(f"{c} not present in all four channels via scenarios: {dict(chans)}")
    multi = [s for s in sc if len(s["channels_present"].split("|")) >= 3]
    if len(multi) < 12: bad.append(f"only {len(multi)} scenarios span 3+ channels")
    if sum(1 for r in em if r["includes_third_party"] == "true") < 3: bad.append("fewer than 3 third-party emails for governance tests")
    check("13. dataset varied enough to test context, certainty, currency, provenance, governance", not bad,
          "; ".join(bad[:6]) or f"{len(sc)} scenarios ({len(corr)} with corrections, {len(stable)} without), {len(multi)} span 3+ channels; dimension counts {dict(dims)}")

    # ------------------------------------------------------------------ extra integrity
    bad = []
    call_utts = {re.sub(r"\W+", " ", r["utterance"].lower()).strip() for r in calls}
    shingles = set()
    for r in calls:
        w = words(r["utterance"])
        shingles.update(tuple(w[i:i + 12]) for i in range(len(w) - 11))
    for label, rows, col, idc in (("turn", tu, "utterance", "turn_id"), ("email", em, "body", "email_id"), ("chat", ch, "message_text", "message_id")):
        for r in rows:
            w = words(r[col])
            if re.sub(r"\W+", " ", r[col].lower()).strip() in call_utts: bad.append(f"{r[idc]} copies a call utterance")
            if any(tuple(w[i:i + 12]) in shingles for i in range(len(w) - 11)): bad.append(f"{r[idc]} shares a 12-word run with a call utterance")
    bodies = [r["body"] for r in em] + [r["utterance"] for r in tu] + [r["message_text"] for r in ch if len(r["message_text"]) > 40]
    dup = [b for b, n in Counter(bodies).items() if n > 1]
    if dup: bad.append(f"{len(dup)} duplicated texts inside new channels")
    check("extra. new text is not copied from the calls or duplicated across channels", not bad, "; ".join(bad[:5]) or "no verbatim copies or 12-word overlaps")

    # ------------------------------------------------------------------ 14 source manifest (Calls vs Meetings)
    bad = []
    chan_of_mod = {"telephone": "Calls", "video": "Meetings", "in-person": "Meetings"}
    per_int = defaultdict(set)
    for r in calls: per_int[r["interaction_id"]].add((r["channel"], r["client_id"], r["advisor_id"], r["interaction_datetime"]))
    if any(len(v) != 1 for v in per_int.values()): bad.append("an interaction has inconsistent metadata across its turns")
    if [r["interaction_id"] for r in man] != sorted(call_ids) or len({r["interaction_id"] for r in man}) != len(man):
        bad.append("manifest does not list every interaction exactly once")
    first_row = {}
    for r in calls: first_row.setdefault(r["interaction_id"], r)
    for r in man:
        i = r["interaction_id"]; src = first_row.get(i)
        if not src: continue
        want = chan_of_mod.get(src["channel"], "Unclassified")
        if r["assigned_channel"] != want: bad.append(f"{i}: assigned {r['assigned_channel']}, modality {src['channel']!r} implies {want}")
        if r["assigned_channel"] not in ("Meetings", "Calls", "Unclassified"): bad.append(f"{i}: bad channel label")
        if r["original_channel_value"] != src["channel"] or r["original_source_type"] != src["source_type"]: bad.append(f"{i}: original labels not preserved")
        if (r["client_id"], r["advisor_id"], r["interaction_datetime"], int(r["turn_count"])) != (src["client_id"], src["advisor_id"], src["interaction_datetime"], call_turns[i]):
            bad.append(f"{i}: id/timestamp/turn count differs from source")
        if r["assigned_channel"] in EXPECT and r["assigned_source_platform"] != EXPECT[r["assigned_channel"]]: bad.append(f"{i}: platform label")
        if r["assigned_channel"] == "Calls" and r["original_channel_value"] != "telephone": bad.append(f"{i}: Calls without a telephone modality")
    mc = Counter(r["assigned_channel"] for r in man)
    if {r["call_source_id"] for r in cm} != {r["interaction_id"] for r in man if r["assigned_channel"] == "Calls"}: bad.append("calls_metadata != manifest Calls")
    if {r["call_source_id"] for r in omm} != {r["interaction_id"] for r in man if r["assigned_channel"] == "Meetings"}: bad.append("original_meetings_metadata != manifest Meetings")
    check("14. manifest maps every original interaction to Meetings / Calls / Unclassified from its modality", not bad,
          "; ".join(bad[:5]) or f"150 interactions: {dict(mc)}; by modality {dict(Counter(r['original_channel_value'] for r in man))}; labels, ids, timestamps, turn counts match the CSV")

    # ------------------------------------------------------------------ 15 generated meetings vs original meeting-like interactions
    bad, worst = [], 0.0
    orig_meet = [r for r in man if r["assigned_channel"] == "Meetings"]
    def sh(text, n=5):
        w = words(text); return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}
    orig_text = defaultdict(str)
    for r in calls: orig_text[r["interaction_id"]] += " " + r["utterance"]
    for m in mt:
        refs = [x for x in mdm[m["meeting_id"]]["references_source_ids"].split("|") if x.startswith("INT-")]
        if not refs: bad.append(f"{m['meeting_id']}: no documented relationship to an original interaction")
        mtext = " ".join(r["utterance"] for r in by_meeting[m["meeting_id"]]); ms = sh(mtext)
        s0 = ts(m["meeting_start_datetime"])
        for o in orig_meet:
            if o["client_id"] != m["client_id"]: continue
            if abs((ts(o["interaction_datetime"]) - s0).total_seconds()) < 2 * 3600: bad.append(f"{m['meeting_id']} starts within 2h of original {o['interaction_id']}")
            if o["original_meeting_context"].strip().lower() == m["subject"].strip().lower(): bad.append(f"{m['meeting_id']} reuses {o['interaction_id']}'s title")
            os_ = sh(orig_text[o["interaction_id"]])
            j = len(ms & os_) / max(1, len(ms | os_)); worst = max(worst, j)
            if j > 0.10: bad.append(f"{m['meeting_id']} / {o['interaction_id']} 5-word-shingle Jaccard {j:.2f}")
    check("15. generated meetings are distinct events, not duplicates of original meeting-modality interactions", not bad,
          "; ".join(bad[:5]) or f"{len(mt)} generated vs {len(orig_meet)} original meetings: none within 2h, no shared title, max 5-word-shingle Jaccard {worst:.3f}; each cites its related originals in references_source_ids")

    # ------------------------------------------------------------------ 16 CRM layer provenance
    bad = []
    crm_all = [r for rows in crm.values() for r in rows]
    cat = {"meetings": "Meetings", "email": "Email", "calls": "Calls", "chat": "Chat"}
    ids_seen = [r["crm_record_id"] for r in crm_all]
    if len(ids_seen) != len(set(ids_seen)): bad.append("duplicate CRM ids")
    chan_for = {i: man_ch[i] for i in call_ids}
    chan_for.update({m: "Meetings" for m in meet}); chan_for.update({t: "Email" for t in thr}); chan_for.update({c: "Chat" for c in cvm})
    for k, rows in crm.items():
        for r in rows:
            who = r["crm_record_id"]
            if not re.fullmatch(r"CRM-(MTG|EML|CAL|CHT)-\d{4}", who): bad.append(f"{who}: bad id")
            if r["crm_artifact"] != f"crm_{k}.csv" or r["channel"] != cat[k]: bad.append(f"{who}: artifact/channel mismatch")
            if r["is_original_source_evidence"] != "false" or r["record_layer"] != "crm_derived": bad.append(f"{who}: not marked as derived")
            if r["record_type"] not in ("Note", "Summary", "Activity"): bad.append(f"{who}: record_type")
            if adv_of.get(r["client_id"]) != r["advisor_id"] or r["author_id"] != r["advisor_id"]: bad.append(f"{who}: author/client pairing")
            refs = [x for x in r["source_refs"].split("|") if x]
            if not refs: bad.append(f"{who}: no source reference")
            for x in refs:
                if x not in chan_for: bad.append(f"{who} -> missing {x}"); continue
                if chan_for[x] != r["channel"]: bad.append(f"{who} -> {x} is in channel {chan_for[x]}, not {r['channel']}")
                if rec_client[x] != r["client_id"]: bad.append(f"{who} -> {x} other client")
                if ts(r["created_at"]) < span[x][0]: bad.append(f"{who} created before its source {x}")
            if "source_platform" in r: bad.append(f"{who}: CRM row carries a source_platform column")
    for k, rows in crm.items():
        cr_ = [r["created_at"] for r in rows]
        cut = next((i for i in range(1, len(cr_)) if cr_[i] < cr_[i - 1]), len(cr_))
        routine_ids_ = {x["record_id"] for x in rr}
        # chronological; the only permitted exception is a final run appended later (append-only IDs) that cites only routine records
        if cr_[cut:] != sorted(cr_[cut:]) or not all(set(r["source_refs"].split("|")) <= routine_ids_ for r in rows[cut:]):
            bad.append(f"crm_{k}.csv not chronological")
    leak = [r["crm_record_id"] for r in crm_all if any(r["crm_record_id"] in (t_ or "") for t_ in
            [x["body"] for x in em] + [x["utterance"] for x in tu] + [x["message_text"] for x in ch])]
    if leak: bad.append(f"CRM ids appear inside source text: {leak[:3]}")
    if any(r["source_id"].startswith("CRM-") for r in sl): bad.append("a scenario link points at a CRM record")
    if any(x.startswith("CRM-") for rows in (md, th, cv) for r in rows for x in r["references_source_ids"].split("|")): bad.append("a source record cites a CRM record")
    if any(r.get("source_type", "").lower().startswith("crm") for rows in (tu, em, ch, mt, md, th, cv, cm, omm) for r in rows): bad.append("a source row is typed as CRM")
    tcount = Counter(r["record_type"] for r in crm_all)
    if not all(tcount[t_] for t_ in ("Note", "Summary", "Activity")): bad.append("CRM lacks one of Notes / Summaries / Activity records")
    check("16. CRM artifacts are separate, derived, and reference real same-client source records", not bad,
          "; ".join(bad[:5]) or f"{len(crm_all)} CRM records in 4 flat artifacts {dict((cat[k], len(v)) for k, v in crm.items())}; types {dict(tcount)}; all marked derived; every source_refs resolves")

    # ------------------------------------------------------------------ 17 evaluation ground truth separated from source paths
    import importlib.util
    spec = importlib.util.spec_from_file_location("source_catalog", DS / "source_catalog.py")
    catalog = importlib.util.module_from_spec(spec); spec.loader.exec_module(catalog)
    bad = []
    found = catalog.discover_source_files(include_crm=True, include_governance=True)
    if any(catalog.is_evaluation_path(p) for p in found): bad.append("discover_source_files() returned an evaluation file")
    for p in found:
        if not Path(p).exists(): bad.append(f"catalog lists missing file {p}"); continue
        header = set(next(csv.reader(open(p, encoding="utf-8", newline=""))))
        if header & {"scenario_id", "storyline_note", "arc_summary", "test_dimensions"}: bad.append(f"{Path(p).name} carries evaluation columns")
    try:
        catalog.assert_no_evaluation_data([DS / "evaluation/scenarios.csv"]); bad.append("guard failed to reject scenarios.csv")
    except ValueError: pass
    try:
        catalog.assert_no_evaluation_data([DS / "elsewhere/scenario_links.csv"]); bad.append("guard failed to reject scenario_links.csv")
    except ValueError: pass
    # every dataset file is exactly one of: source / crm / manifest / evaluation / registry / docs / tooling
    known = {str(Path(p).resolve()) for p in found}
    for p in DS.rglob("*.csv"):
        rel = p.relative_to(DS).as_posix()
        if rel.startswith("evaluation/"): continue
        if str(p.resolve()) not in known and rel != "character_registry.csv": bad.append(f"{rel} is in neither the source catalog nor evaluation/")
    for p in (DS / "evaluation").glob("*.csv"):
        if p.name not in catalog.FORBIDDEN_NAMES: bad.append(f"unexpected file in evaluation/: {p.name}")
    if any((DS / n).exists() for n in ("scenarios.csv", "scenario_links.csv")): bad.append("stale evaluation file outside evaluation/")
    # no application code reads the evaluation files
    hits = []
    for base in ("src", "utils", "services"):
        for p in (ROOT / base).rglob("*.py") if (ROOT / base).exists() else []:
            try: t_ = p.read_text(encoding="utf-8", errors="ignore")
            except OSError: continue
            if re.search(r"scenario_links|datasets[/\\]evaluation|evaluation[/\\]scenarios|access_scenarios|routine_records", t_): hits.append(p.relative_to(ROOT).as_posix())
    if hits: bad.append(f"application code references evaluation data: {hits}")
    check("17. evaluation ground truth is excluded from every source-discovery path", not bad,
          "; ".join(bad[:5]) or f"catalog returns {len(found)} files (source + CRM + governance), none from evaluation/; no src/utils/services code references the evaluation files; guard rejects the evaluation files")

    # ------------------------------------------------------------------ 18 attribution
    bad, flags = [], []
    if {r["speaker"] for r in calls} != {"Advisor", "Client"}: bad.append("original speaker labels are not exactly Advisor/Client")
    regd = {r["character_id"]: r for r in reg}
    name_to_id = {r["display_name"]: r["character_id"] for r in reg}
    mention_only = {r["character_id"] for r in reg if r["character_id"].startswith("MENT-")}
    for r in tu:
        if name_to_id.get(r["speaker_name"]) != r["speaker_id"]: bad.append(f"{r['turn_id']} name/id mismatch")
        if r["speaker_id"] in mention_only: bad.append(f"{r['turn_id']} mentioned-only person speaks")
    for r in em:
        for who, nm in ((r["sender_id"], r["sender_name"]),):
            if name_to_id.get(nm) != who: bad.append(f"{r['email_id']} sender name/id mismatch")
        if ({r["sender_id"]} | set(filter(None, r["to_ids"].split("|"))) | set(filter(None, r["cc_ids"].split("|")))) & mention_only: bad.append(f"{r['email_id']} mentioned-only person on header")
        if r["sender_email"] != regd[r["sender_id"]]["email"]: bad.append(f"{r['email_id']} sender email != registry")
    for r in ch:
        if name_to_id.get(r["sender_name"]) != r["sender_id"] or r["sender_id"] in mention_only or r["recipient_id"] in mention_only: bad.append(f"{r['message_id']} chat attribution")
    for rows in (md, th, cv):
        for r in rows:
            if set(r["participant_ids"].split("|")) & mention_only: bad.append(f"participant list names a mentioned-only person: {r.get('meeting_id') or r.get('thread_id') or r.get('chat_conversation_id')}")
    ext_speakers = Counter(r["sender_id"] for r in em if r["sender_id"].startswith("EXT-"))
    if any(r["speaker_id"].startswith("EXT-") for r in tu) or any(r["sender_id"].startswith("EXT-") for r in ch): bad.append("third party speaks in a meeting/chat")
    flags.append(f"{len(mention_only)} mentioned-only people are never speakers; their roles are inferred from utterances, not stated")
    flags.append("Mei Lin Chu (accountant of CLIENT-002) is mentioned-only; her relation to the lawyer in INT-126 is not stated and is not assumed")
    flags.append("original CSV has no speaker names: Priya/Daniel/Elena and the six client names are mapped by first-name co-occurrence")
    flags.append(f"external senders: {dict(ext_speakers)} (EXT-001/EXT-002 appear only as email correspondents)")
    for f_ in flags: warnings.append("attribution: " + f_)
    check("18. speaker/sender/recipient attribution matches the registry and the original CSV", not bad,
          "; ".join(bad[:5]) or f"{len(tu)} turns, {len(em)} emails, {len(ch)} chat messages: speaker is always the container's client/advisor (or a registered email correspondent); no mentioned-only person is a participant")

    # ------------------------------------------------------------------ 19 routine (non-memory-bearing) communication
    bad = []
    rt = {r["record_id"] for r in rr}
    rem = [r for r in em if r["thread_id"] in rt]; rch = [r for r in ch if r["chat_conversation_id"] in rt]
    if sum(1 for r in rr if r["channel"] == "Email") < 6 or sum(1 for r in rr if r["channel"] == "Chat") < 6: bad.append("fewer than 6 routine email threads or chats")
    if len({r["client_id"] for r in rr}) < 6: bad.append("routine records do not cover all six clients")
    if len(rem) + len(rch) < 20: bad.append("fewer than 20 routine messages")
    sig = re.compile(r"\$|\d{3,}|decid|correct|clarif|recommend|suggest|might|inclined|retire|sell|loan|pension|tuition|will (send|call)|i.ll (send|call)", re.I)
    selflabel = re.compile(r"routine|ordinary|non-memory|administrative message", re.I)
    for r in rem:
        if selflabel.search(r["body"] + r["subject"]): bad.append(f"{r['email_id']} labels itself routine")
        if sig.search(r["body"]): bad.append(f"{r['email_id']} routine text carries a memory-like signal")
    for r in rch:
        if selflabel.search(r["message_text"]): bad.append(f"{r['message_id']} labels itself routine")
        if sig.search(r["message_text"]): bad.append(f"{r['message_id']} routine text carries a memory-like signal")
    cited = [r.get("thread_id") or r.get("chat_conversation_id") for rows_ in (th, cv) for r in rows_
             if (r.get("thread_id") or r.get("chat_conversation_id")) in rt and r["references_source_ids"]]
    # a routine record may point at an earlier same-client record (check 11 resolves it) but must not be a scenario member
    if any(r["source_id"] in rt for r in sl): bad.append("a routine record is in a scenario")
    in_scn_calls = {r["source_id"] for r in sl if r["source_id"].startswith("INT-")}
    unlinked = [i for i in call_ids if i not in in_scn_calls]
    if len(unlinked) < 50: bad.append(f"only {len(unlinked)} original interactions are outside every scenario")
    check("19. dataset contains routine, non-memory-bearing communication as well as storylines", not bad,
          "; ".join(bad[:5]) or f"{len(rt)} routine records ({len(rem)} emails, {len(rch)} chat messages; {len(cited)} cite an earlier record, the rest are context-free) with no memory-like text and no scenario; {len(unlinked)} of {len(call_ids)} original interactions are in no scenario")

    # ------------------------------------------------------------------ 20-22 synthetic source-control metadata
    gc = _load_module("governance_check", DS / "governance_check.py")
    ctx = gc.load(DS)
    ctrl, accp, retp, grants = ctx["control"], ctx["access"], ctx["retention"], ctx["grants"]
    PAIR = {"CLIENT-001": "ADVISOR-001", "CLIENT-002": "ADVISOR-002", "CLIENT-003": "ADVISOR-003",
            "CLIENT-004": "ADVISOR-001", "CLIENT-005": "ADVISOR-002", "CLIENT-006": "ADVISOR-003"}
    bad = []
    crm_ids = {r["crm_record_id"] for rows_ in crm.values() for r in rows_}
    expected_ids = ({r["interaction_id"] for r in man} | {r["meeting_id"] for r in mt} | {r["thread_id"] for r in th}
                    | {r["chat_conversation_id"] for r in cv} | crm_ids)
    if set(ctrl) != expected_ids:
        bad.append(f"control rows != records: missing {sorted(expected_ids - set(ctrl))[:3]} extra {sorted(set(ctrl) - expected_ids)[:3]}")
    chan_of_id = {r["interaction_id"]: r["assigned_channel"] for r in man}
    chan_of_id.update({r["meeting_id"]: "Meetings" for r in mt}); chan_of_id.update({r["thread_id"]: "Email" for r in th})
    chan_of_id.update({r["chat_conversation_id"]: "Chat" for r in cv})
    client_of_id = {r["interaction_id"]: r["client_id"] for r in man}
    client_of_id.update({r["meeting_id"]: r["client_id"] for r in mt}); client_of_id.update({r["thread_id"]: r["client_id"] for r in th})
    client_of_id.update({r["chat_conversation_id"]: r["client_id"] for r in cv})
    src_of_crm = {}
    for rows_ in crm.values():
        for r in rows_:
            chan_of_id[r["crm_record_id"]] = r["channel"]; client_of_id[r["crm_record_id"]] = r["client_id"]
            src_of_crm[r["crm_record_id"]] = r["source_refs"].split("|")
    for rid, r in ctrl.items():
        derived = rid in crm_ids
        if (r["record_layer"] == "crm_derived") != derived or r["is_original_source_evidence"] != ("false" if derived else "true"):
            bad.append(f"{rid} layer / original-evidence flag wrong")
        if r["channel"] != chan_of_id.get(rid) or r["client_scope"] != client_of_id.get(rid): bad.append(f"{rid} channel or client scope differs from the record")
        if r["access_policy_id"] not in accp or r["retention_policy_id"] not in retp: bad.append(f"{rid} unknown policy id")
        if r["restriction_policy_id"] and (r["restriction_policy_id"] not in accp or not r["restriction_basis"]): bad.append(f"{rid} restriction policy unknown or has no basis")
        if r["restriction_policy_id"]:   # a restriction may only narrow
            if not gc.effective(ctx, r)["roles"] <= set(accp[r["access_policy_id"]]["permitted_roles"].split("|")): bad.append(f"{rid} restriction widens access")
        if r["review_date"] <= r["retention_start_date"]: bad.append(f"{rid} review date not after retention start")
        if r["responsible_advisor_id"] != PAIR[r["client_scope"]]: bad.append(f"{rid} responsible advisor does not match the pairing")
        if derived and not (r["access_policy_id"].startswith("SYN-ACC-CRM") and r["retention_policy_id"].startswith("SYN-RET-CRM")):
            bad.append(f"{rid} CRM record lacks its own CRM policies")
        if not derived and (r["access_policy_id"].startswith("SYN-ACC-CRM") or r["retention_policy_id"].startswith("SYN-RET-CRM")):
            bad.append(f"{rid} source record carries a CRM policy")
        if "synthetic" not in r["control_basis"]: bad.append(f"{rid} control_basis does not say synthetic")
    for p_ in list(accp.values()) + list(retp.values()):
        if "synthetic" not in p_["assumption"]: bad.append("a policy row is not labelled synthetic")
    src_rows = [r for r in ctrl.values() if r["record_layer"] == "original_evidence"]
    if len({r["retention_policy_id"] for r in src_rows}) < 4: bad.append("source channels do not have distinct retention policies")
    if len({r["access_policy_id"] for r in src_rows}) < 4: bad.append("source channels do not have distinct access policies")
    restricted_n = sum(1 for r in ctrl.values() if r["restriction_policy_id"])
    pc = Counter(r["access_policy_id"] for r in ctrl.values()); rc = Counter(r["retention_policy_id"] for r in ctrl.values())
    check("20. every source and CRM record has its own synthetic access and retention control row", not bad,
          "; ".join(bad[:5]) or f"{len(ctrl)} control rows for {len(ctrl) - len(crm_ids)} source + {len(crm_ids)} CRM records; access policies {dict(pc)}; retention policies {dict(rc)}; {restricted_n} records under a record-level restriction; all labelled synthetic")

    bad = []
    ev_scn = read(DS / "evaluation/access_scenarios.csv")
    cats = {r["gap_category"] for r in ev_scn}
    need = {"authorized_external_sharing", "unauthorized_cross_client_disclosure", "advisor_outside_assignment",
            "agent_restricted_retrieval", "derived_crm_stricter_than_source", "revoked_or_expired_authorization"}
    if not need <= cats: bad.append(f"categories not covered: {sorted(need - cats)}")
    got = Counter()
    grant_ids = {x["grant_id"] for x in grants}
    for r in ev_scn:
        d_, code = gc.decide(ctx, r["requester_id"], r["requester_type"], r["acting_for_id"], r["target_record_id"], r["request_date"])
        got[d_] += 1
        if (d_, code) != (r["expected_decision"], r["expected_reason_code"]):
            bad.append(f"{r['scenario_id']} expected {r['expected_decision']}/{r['expected_reason_code']} got {d_}/{code}")
        if not r["rationale"] or not r["supporting_source_ids"]: bad.append(f"{r['scenario_id']} has no rationale or supporting records")
        for x in r["supporting_source_ids"].split("|") + [r["target_record_id"]]:
            if x not in ctrl: bad.append(f"{r['scenario_id']} cites unknown record {x}")
        if r["target_record_id"] in ctrl and r["request_date"] < ctrl[r["target_record_id"]]["retention_start_date"]:
            bad.append(f"{r['scenario_id']} requests a record before it exists")
        for g in filter(None, r["supporting_grant_ids"].split("|")):
            if g not in grant_ids: bad.append(f"{r['scenario_id']} cites unknown grant {g}")
    if got["allow"] < 4 or got["deny"] < 12: bad.append(f"outcome mix too one-sided: {dict(got)}")
    for g in grants:
        if g["record_id"] not in ctrl or ctrl[g["record_id"]]["client_scope"] != g["client_scope"]: bad.append(f"{g['grant_id']} record/client mismatch")
        if g["principal_id"] not in ctx["principals"] or ctx["principals"][g["principal_id"]]["client_scope"] != g["client_scope"]:
            bad.append(f"{g['grant_id']} grantee is not scoped to the record's client")
        if g["record_id"] in crm_ids: bad.append(f"{g['grant_id']} is a grant on a derived CRM record")
        if "synthetic" not in g["assumption"]: bad.append(f"{g['grant_id']} not labelled synthetic")
        for b_ in g["basis_source_ids"].split("|"):
            if b_ not in ctrl: bad.append(f"{g['grant_id']} basis {b_} does not resolve")
    check("21. access evaluator reproduces every expected outcome in the governance scenarios", not bad,
          "; ".join(bad[:5]) or f"{len(ev_scn)} scenarios across {len(cats)} categories reproduced independently ({dict(got)}); {len(grants)} grants resolve and are scoped to one client and one record")

    bad, narrower, equal = [], 0, 0
    on = "2026-06-30"
    for rid in sorted(crm_ids):
        crm_r = gc.readers(ctx, rid, on)
        for sid_ in src_of_crm[rid]:
            src_r = gc.readers(ctx, sid_, on)
            if not crm_r <= src_r: bad.append(f"{rid} is readable by {sorted(crm_r - src_r)} who cannot read its source {sid_}")
            if crm_r < src_r: narrower += 1
            elif crm_r == src_r: equal += 1
    for g in grants:   # a grant on a source record must not open any CRM record derived from it
        for rid in crm_ids:
            if g["record_id"] in src_of_crm[rid] and g["principal_id"] in gc.readers(ctx, rid, on): bad.append(f"grant {g['grant_id']} leaks to {rid}")
    if narrower == 0: bad.append("no CRM record is stricter than its source")
    check("22. derived CRM records never inherit or widen access from their sources", not bad,
          "; ".join(bad[:5]) or f"{len(crm_ids)} CRM records: none readable by anyone who cannot read its source; {narrower} source links strictly narrower, {equal} equal; no source grant reaches a CRM record")

    # ------------------------------------------------------------------ 23 attribution status
    bad = []
    need_cols = ["person_category", "id_status", "name_status", "role_status", "attribution_confidence", "attribution_evidence"]
    for r in reg:
        for c_ in need_cols:
            if not r.get(c_): bad.append(f"{r['character_id']} missing {c_}")
        cid_ = r["character_id"]
        if r.get("id_status") == "established_in_csv" and not cid_.startswith(("CLIENT-", "ADVISOR-")): bad.append(f"{cid_} claims an ID established in the CSV")
        if cid_.startswith(("MENT-", "EXT-")) and r.get("id_status") != "registry_assigned": bad.append(f"{cid_} ID not marked registry_assigned")
        if cid_.startswith("MENT-") and r.get("person_category") != "mentioned_only": bad.append(f"{cid_} not mentioned_only")
        if cid_.startswith("EXT-") and r.get("person_category") != "external_correspondent": bad.append(f"{cid_} not external_correspondent")
        if r.get("attribution_confidence") == "high" and r.get("role_status") == "role_inferred_from_source": bad.append(f"{cid_} inferred role upgraded to high confidence")
        if cid_.startswith(("CLIENT-", "ADVISOR-")) and "inferred" not in r.get("name_status", ""): bad.append(f"{cid_} name mapping not marked as inferred")
    conf = Counter(r["attribution_confidence"] for r in reg)
    cat_conf = Counter(f"{r['person_category']}:{r['attribution_confidence']}" for r in reg)
    check("23. every registered person carries an explicit identity, role and confidence status", not bad,
          "; ".join(bad[:5]) or f"{len(reg)} people: confidence {dict(conf)}; by category {dict(sorted(cat_conf.items()))}; inferred roles never rated high")
    for r in reg:
        if r["person_category"] == "mentioned_only" and r["attribution_confidence"] != "high":
            warnings.append(f"attribution: {r['display_name']} ({r['character_id']}) {r['attribution_confidence']} confidence: {r['role_status']}")

    # ------------------------------------------------------------------ report
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for n, ok, d in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {n}\n         {d}")
        failed += not ok
    for w in warnings: print("[WARN]", w)
    ref_calls = {r["source_id"] for r in sl if r["source_id"].startswith("INT-")}
    ref_calls |= {x for rows in (md, th, cv) for r in rows for x in r["references_source_ids"].split("|") if x.startswith("INT-")}
    print("\nSUMMARY")
    print(f"  clients: {len(new_client_ids)}  | advisors: {len(new_adv_ids)} | characters in registry: {len(reg)} (all attested in conversations.csv)")
    print(f"  meetings: {len(mt)}  meeting turns: {len(tu)}")
    print(f"  email threads: {len(th)}  email messages: {len(em)}")
    print(f"  original interactions in conversations.csv: {len(call_ids)} ({len(calls)} turns) -> manifest: {dict(Counter(r['assigned_channel'] for r in man))}; referenced by new records: {len(ref_calls)} ({sum(call_turns[i] for i in ref_calls)} turns)")
    print(f"  channel totals (original + generated): Meetings {len(omm) + len(mt)} | Calls {len(cm)} | Email {len(th)} threads | Chat {len(cv)} conversations")
    print(f"  CRM records: {sum(len(v) for v in crm.values())} {dict((k, len(v)) for k, v in crm.items())}")
    print(f"  chat conversations: {len(cv)}  chat messages: {len(ch)}")
    print(f"  cross-channel linked scenarios: {len(sc)} ({len(multi)} spanning 3+ channels; all {len(sc)} span 2+: {all(len(s['channels_present'].split('|')) >= 2 for s in sc)})")
    print(f"  routine records: {len(rt)} ({len(rem)} emails, {len(rch)} chat messages); governance: {len(ctrl)} control rows, {len(grants)} grants, {len(ev_scn)} access scenarios")
    print(f"  checks passed: {len(results) - failed}/{len(results)}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
