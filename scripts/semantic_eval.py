"""Semantic-annotation evaluation framework for the CMF evidence layer (preparation phase).

Reads the evidence database READ-ONLY (SQLite mode=ro) and the source CSVs read-only; writes ONLY into the output directory given with --out.
It never ingests, never changes a rule, parser, database, source file, agent or harness, and creates no memories.  The existing rule-generated labels are
shown to analysts as "system labels" and are NEVER treated as ground truth: expected annotations live in a separate, blank, human-filled template.

    python scripts/semantic_eval.py build   [--out evaluation/semantic_annotation]   # sample, blind review sheet, templates, known defects, diagnostics
    python scripts/semantic_eval.py diagnose                                         # print the immediately computable diagnostics
    python scripts/semantic_eval.py score --items items.jsonl --gold filled_template.csv [--seg-items ... --seg-gold ...]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import inspect_evidence as IE  # noqa: E402  (read-only helpers: connect, Source, Q)
from evidence import semantics as S  # noqa: E402  (rules are only READ here, never changed)

DEFAULT_OUT = ROOT / "evaluation" / "semantic_annotation"
CHANNELS = ["Meetings", "Calls", "Email", "Chat"]
CONTEXT = 2
VERSION = "semantic-eval-1"

# ------------------------------------------------------------------------------------------------------------------------ label schema
LABELS = {
    "dialogue_acts": {
        "question": "The speaker asks for information. A question mark is neither necessary nor sufficient; an embedded or rhetorical '?' is not a question.",
        "request": "The speaker asks the other party to DO something (send, check, confirm, call) rather than to say something.",
        "answer": "The turn supplies the information a preceding question asked for. Say which question in answers_turn.",
        "suggestion": "The speaker recommends or suggests a course of action without committing anyone to it ('I'd suggest', 'you could').",
        "proposal": "The speaker puts a concrete plan or next step forward for acceptance ('let's schedule', 'shall we').",
        "agreement": "The speaker agrees with something said earlier. A discourse opener ('Good morning', 'Right now', 'Okay so') is NOT agreement.",
        "rejection": "The speaker declines or disagrees with a proposal, suggestion or claim. Courtesy phrases ('No problem', 'No apology needed') are not rejection.",
        "preference": "The speaker states what they want or would rather do, without deciding ('I'd rather keep the cash', 'I don't want to run out of money').",
        "correction": "The speaker states that an earlier statement, record or note (their own or another's) was wrong and gives the right version. Apology alone is not correction.",
        "decision": "The speaker, FIRST-HAND, states a settled choice made by themselves or jointly with the other party. A choice reported on behalf of someone else is a decision_report: tag decision_status=reported_by_other.",
        "commitment": "The speaker undertakes to do something specific. 'I'll have to think about that' is not a commitment.",
        "deferral": "The speaker explicitly leaves something open, undecided or for later ('nothing is decided', 'let's revisit').",
        "exploration": "The speaker is considering an option without settling it ('I'm thinking about', 'what if', 'leaning toward').",
        "fact": "The speaker states information, a figure, an event or a status with no other act above.",
        "social": "Greeting, thanks, courtesy or boilerplate carrying no planning content.",
        "other": "A real act not covered above (explain in notes).",
        "unresolved": "Reviewer cannot tell from the evidence. Use alone; the item is excluded from precision/recall and counted separately.",
    },
    "decision_maker": {
        "client": "The client made the choice.", "advisor": "The advisor made the choice (rare; advisors recommend).",
        "joint": "Both parties explicitly agreed.", "third_party": "Someone else (accountant, lawyer, family member).",
        "none": "No decision is stated in this turn.", "unresolved": "Cannot tell.",
    },
    "decision_status": {
        "confirmed": "Settled and stated first-hand by the decision-maker or explicitly confirmed by them.",
        "conditional": "Settled only if a stated condition holds.",
        "exploratory": "Being considered, leaning, or hypothetical.",
        "recommendation_only": "An advisor's recommendation the client has not accepted.",
        "reported_by_other": "The speaker REPORTS or recaps a decision attributed to someone else. An advisor's recap is evidence of what the advisor says the client decided, not a confirmed client decision.",
        "none": "No decision content.", "unresolved": "Cannot tell.",
    },
    "attributed_to": {
        "self": "The speaker is stating their own position or act.", "client": "The speaker attributes the content to the client.",
        "advisor": "The speaker attributes the content to the advisor.", "third_party": "The speaker attributes it to someone else.",
        "none": "No attribution.", "unresolved": "Cannot tell.",
    },
    "topics": {k: "Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes)." for k in S.TOPICS} | {
        "general_discussion": "No identifiable planning topic.", "other": "A real topic outside this list (name it in notes).",
        "none": "No topic content (pure social turn).", "unresolved": "Cannot tell."},
    "correction_evidence": {
        "explicit_reference": "The text names the earlier statement (by date, medium or quoted content): 'my February 18 email', 'I wrote that ...'.",
        "restated_value": "The turn restates the earlier item with a changed value and signals the change ('actually it is 60%').",
        "self_repair": "The speaker repairs their own immediately preceding words.",
        "none": "No supporting evidence in the text: do NOT give a correction target.",
    },
    "confidence": {"high": "Clear from the text.", "medium": "Reasonable but another reading exists.", "low": "Weak evidence; consider 'unresolved'."},
}
SPECIAL = {"none", "unresolved"}

# system label -> gold label (labels the system cannot produce are listed as unsupported in the metrics)
SYS_ACT_MAP = {"information": "fact", "question": "question", "agreement": "agreement", "proposal": "proposal", "commitment": "commitment",
               "deferral_unresolved": "deferral", "decision": "decision", "rejection": "rejection", "social": "social", "suggestion": "suggestion",
               "correction": "correction"}
UNSUPPORTED_ACTS = sorted(set(LABELS["dialogue_acts"]) - set(SYS_ACT_MAP.values()) - {"unresolved"})

TEMPLATE_COLS = ["item_id", "annotator_id", "dialogue_acts", "decision_maker", "decision_status", "attributed_to", "topics",
                 "correction_target_turn", "correction_evidence", "answers_turn", "temporal_refs", "confidence", "notes"]
SEG_TEMPLATE_COLS = ["container_id", "annotator_id", "boundary_turns", "segment_topics", "related_groups", "notes"]

# ------------------------------------------------------------------------------------------------------------------------ system state
MONTHS = "january february march april may june july august september october november december".split()


def load_state(c, src):
    turns = IE.Q(c, "SELECT t.*, k.container_kind, k.subject AS container_subject, (SELECT authored_text FROM email_message_details e WHERE e.turn_id=t.turn_id) AS authored "
                    "FROM turns t JOIN containers k ON k.container_id=t.container_id ORDER BY t.container_id, t.seq")
    st = dict(turns={t["turn_id"]: t for t in turns}, by_container=defaultdict(list), acts=defaultdict(list), cues=defaultdict(lambda: defaultdict(list)),
              out=defaultdict(list), inn=defaultdict(list), seg_of={}, segs={}, seg_by_container=defaultdict(list), src=src)
    for t in turns:
        t["text"] = t["authored"] if t["authored"] is not None else t["original_text"]
        st["by_container"][t["container_id"]].append(t)
    for r in IE.Q(c, "SELECT target_id, kind, value, confidence FROM annotations WHERE target_type='turn' ORDER BY annotation_id"):
        if r["kind"] == "dialogue_act":
            st["acts"][r["target_id"]].append((r["value"], r["confidence"]))
        else:
            st["cues"][r["target_id"]][r["kind"]].append(r["value"])
    for r in IE.Q(c, "SELECT from_turn_id f, to_turn_id t, rel_type, basis, provenance FROM turn_relations ORDER BY rel_type, from_turn_id"):
        st["out"][r["f"]].append(r)
        st["inn"][r["t"]].append(r)
    for s in IE.Q(c, "SELECT * FROM segments ORDER BY container_id, seg_index"):
        s["turn_ids"] = [x["turn_id"] for x in IE.Q(c, "SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", s["segment_id"])]
        st["segs"][s["segment_id"]] = s
        st["seg_by_container"][s["container_id"]].append(s)
        for tid in s["turn_ids"]:
            st["seg_of"][tid] = s
    for s in st["segs"].values():                      # mixed-topic flag, same criterion as the inspection tool
        per = Counter()
        for tid in s["turn_ids"]:
            for k in S.topic_scores(st["turns"][tid]["text"]):
                per[k] += 1
        n = len(s["turn_ids"])
        s["mixed"] = n >= 5 and len([k for k, v in per.items() if v >= max(2, n // 3)]) >= 2
        s["topic_hits"] = dict(per)
    return st


def party(t):
    sid = t["speaker_id"] or ""
    return "client" if sid.startswith("CLIENT") else "advisor" if sid.startswith("ADVISOR") else "third_party"


# ------------------------------------------------------------------------------------------------------------------------ strata
def _rx(p):
    return re.compile(p, re.I)


_MON = "(?:january|february|march|april|may|june|july|august|september|october|november|december)"
RX = dict(
    decision_cue=_rx(r"\b(i(?:'ll| will) (?:take|choose|pick|elect|keep|sell|buy|file|go)|let's proceed|we(?:'re| are) going (?:with|to)|i want to (?:go|proceed|elect|keep|sell)|please proceed|go ahead)\b"),
    correction_wide=_rx(r"\b(that's not right|not quite|not what i (?:said|meant)|wrong|incorrect|mislabel|overstat|understat|typo|should say|isn't (?:right|correct)|i never said|didn't say|correction|clarify)\b"),
    earlier_ref=_rx(r"\b(?:my|your|the) (?:" + _MON + r" \d{1,2}|last|earlier|previous|yesterday's|today's) (?:email|note|call|message|meeting|summary)|as you (?:explained|told me|said|described) on|\bi wrote that\b|\byou (?:said|wrote|told me) (?:that|on|last)"),
    request=_rx(r"\b(please (?:confirm|tell|let me know|send|check|call|forward)|let me know|could you|can you|would you|send me)\b"),
    preference=_rx(r"\b(i(?:'d| would) (?:rather|prefer|like)|i prefer|i don't want|i do not want|i want)\b"),
    confirmed=_rx(r"\b(decided|is recorded|we agreed|agreed to|you(?:'ve| have) decided)\b"),
    opener=_rx(r"^\W*(good (?:morning|afternoon|question)|right now|thanks|thank you)\b"),
    modal=re.compile(r"(?<![A-Za-z])(will|may)(?![A-Za-z])"),
)


def build_strata(st):
    """name -> (selector over turn dicts, quota, what the stratum probes).  Selectors use the turn's TEXT or system output only to choose WHAT TO REVIEW."""
    T = st["turns"]
    acts = lambda t: {a for a, _ in st["acts"][t["turn_id"]]}
    seg = lambda t: st["seg_of"][t["turn_id"]]
    has_out = lambda t, r: any(x["rel_type"] == r for x in st["out"][t["turn_id"]])
    cli = lambda t: party(t) == "client"
    adv = lambda t: party(t) == "advisor"
    txt = lambda t: t["text"]
    return {
        "decision_label_client": (lambda t: "decision" in acts(t) and cli(t), 8, "client first-person decisions the system labelled"),
        "decision_label_advisor_recap": (lambda t: "decision" in acts(t) and adv(t), 8, "advisor recaps / reports labelled decision"),
        "decision_cue_unlabelled": (lambda t: "decision" not in acts(t) and cli(t) and RX["decision_cue"].search(txt(t)) and not S._EXPLORATORY.search(txt(t)), 6, "possible missed client decisions"),
        "confirmed_vs_recorded": (lambda t: RX["confirmed"].search(txt(t)) and "decision" not in acts(t), 6, "decision wording the rule did not label"),
        "correction_label": (lambda t: "correction" in acts(t), 8, "turns the system labelled correction"),
        "correction_candidate_unlabelled": (lambda t: "correction" not in acts(t) and RX["correction_wide"].search(txt(t)), 6, "possible missed corrections"),
        "corrects_relation_source": (lambda t: has_out(t, "corrects"), 17, "every system corrects relation (target accuracy)"),
        "explicit_reference_to_earlier": (lambda t: RX["earlier_ref"].search(txt(t)), 8, "turns that explicitly refer to an earlier message"),
        "question_label": (lambda t: "question" in acts(t), 8, "question labels (embedded '?')"),
        "request_unlabelled": (lambda t: "question" not in acts(t) and RX["request"].search(txt(t)), 8, "requests: no act exists for them"),
        "answers_relation_source": (lambda t: has_out(t, "answers"), 10, "system answers relations"),
        "answers_crossing_segment": (lambda t: any(x["rel_type"] == "answers" and st["seg_of"][x["f"]]["segment_id"] != st["seg_of"][x["t"]]["segment_id"] for x in st["out"][t["turn_id"]]), 8, "answer links that cross a segment boundary"),
        "agreement_label": (lambda t: "agreement" in acts(t), 8, "agreement labels (discourse openers)"),
        "rejection_label": (lambda t: "rejection" in acts(t), 8, "rejection labels (courtesy, preference)"),
        "preference_statement": (lambda t: RX["preference"].search(txt(t)), 8, "preference statements: no act exists"),
        "exploratory": (lambda t: S._EXPLORATORY.search(txt(t)) and cli(t), 8, "exploratory client statements"),
        "deferral_label": (lambda t: "deferral_unresolved" in acts(t), 6, "deferral labels"),
        "temporal_label": (lambda t: bool(st["cues"][t["turn_id"]].get("temporal_ref")), 8, "temporal extraction (precision)"),
        "temporal_modal_ambiguity": (lambda t: any(v.lower() in ("may", "will") for v in st["cues"][t["turn_id"]].get("temporal_ref", [])) or (RX["modal"].search(txt(t)) and "estate_and_trusts" in S.topic_scores(txt(t))), 6, "modal 'may'/'will' as date or topic"),
        "social_label": (lambda t: "social" in acts(t), 4, "social labels on content turns"),
        "boilerplate_opener": (lambda t: RX["opener"].search(txt(t)), 6, "openers mistaken for acts"),
        "information_only_control": (lambda t: [a for a, _ in st["acts"][t["turn_id"]]] == ["information"], 8, "control: turns where no rule fired"),
        "multi_topic_segment": (lambda t: seg(t)["mixed"], 6, "segments combining topics"),
        "fragmented_context": (lambda t: len(seg(t)["turn_ids"]) == 1 or seg(t)["topic_label"] == "general_discussion" or (t["channel"] == "Chat" and len(st["by_container"][t["container_id"]]) <= 4), 8, "fragmented / single-turn contexts"),
        "portal_topic": (lambda t: "portal" in txt(t).lower(), 4, "'portal' alone as scheduling_and_admin"),
    }


def h(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()


def select(st, strata, forced):
    """Deterministic stratified selection: candidates ordered by sha1(stratum|turn_id); round-robin over channels; unchosen turns preferred."""
    chosen: dict[str, list] = defaultdict(list)
    order = sorted(st["turns"].values(), key=lambda t: t["turn_id"])
    for tid in forced:
        chosen[tid].append("known_defect")
    for name, (pred, quota, _) in strata.items():
        cands = [t for t in order if pred(t)]
        per = {ch: sorted([t for t in cands if t["channel"] == ch], key=lambda t: h(name, t["turn_id"])) for ch in CHANNELS}
        taken = []
        for allow_repeat in (False, True):
            idx = {ch: 0 for ch in CHANNELS}
            while len(taken) < quota:
                progressed = False
                for ch in CHANNELS:
                    while idx[ch] < len(per[ch]) and len(taken) < quota:
                        t = per[ch][idx[ch]]
                        idx[ch] += 1
                        if t["turn_id"] in [x["turn_id"] for x in taken] or (not allow_repeat and t["turn_id"] in chosen):
                            continue
                        taken.append(t)
                        progressed = True
                        break
                if not progressed:
                    break
            if len(taken) >= quota:
                break
        for t in taken:
            chosen[t["turn_id"]].append(name)
    return chosen


# ------------------------------------------------------------------------------------------------------------------------ items
def turn_view(st, t):
    return dict(turn_id=t["turn_id"], seq=t["seq"], speaker_id=t["speaker_id"], speaker_name=t["speaker_name"], speaker_role=t["speaker_role"], text=t["original_text"])


def make_item(st, tid, strata, kds, n):
    t = st["turns"][tid]
    src_text = st["src"].text.get(tid)
    seg = st["seg_of"][tid]
    lst = st["by_container"][t["container_id"]]
    i = next(k for k, x in enumerate(lst) if x["turn_id"] == tid)
    return dict(
        item_id=f"SEM-{n:03d}", strata=sorted(strata), known_defect_ids=sorted(kds), channel=t["channel"], container_id=t["container_id"],
        container_kind=t["container_kind"], container_subject=t["container_subject"], turn_id=tid, seq=t["seq"], ts=t["ts"],
        speaker_id=t["speaker_id"], speaker_name=t["speaker_name"], speaker_role=t["speaker_role"], attribution_status=t["attribution_status"],
        attribution_confidence=t["attribution_confidence"], source_text=src_text, parsed_text=t["original_text"], authored_text=t["authored"],
        source_matches_parsed=src_text == t["original_text"],
        system=dict(
            topic_segment=dict(segment_id=seg["segment_id"], topic=seg["topic_label"], confidence=seg["confidence"], turn_ids=seg["turn_ids"], mixed_topic_flag=seg["mixed"]),
            dialogue_acts=[dict(act=a, confidence=c) for a, c in st["acts"][tid]],
            cues={k: v for k, v in st["cues"][tid].items()},
            relations_out=[dict(rel_type=r["rel_type"], to=r["t"], basis=r["basis"], provenance=r["provenance"]) for r in st["out"][tid]],
            relations_in=[dict(rel_type=r["rel_type"], frm=r["f"], basis=r["basis"], provenance=r["provenance"]) for r in st["inn"][tid]]),
        context_before=[turn_view(st, x) for x in lst[max(0, i - CONTEXT):i]], context_after=[turn_view(st, x) for x in lst[i + 1:i + 1 + CONTEXT]])


def blind_sheet(items, seg_items):
    """What annotators see: source text and context only.  No system labels, no strata, no known-defect hints (prevents anchoring)."""
    out = ["# Blind annotation sheet", "", "Annotate each item in `annotation_template.csv` using `label_definitions.md`. System labels are intentionally NOT shown.",
           "Leave a field blank if you have not annotated it; write `none` for 'nothing applies' and `unresolved` for 'cannot tell'.", ""]
    for it in items:
        out += [f"## {it['item_id']}  -  {it['channel']} {it['container_id']} ({it['container_kind']}), turn {it['turn_id']}",
                f"Subject/context: {it['container_subject']}   Time: {it['ts'] or '(no per-turn time in source)'}", ""]
        for x in it["context_before"]:
            out.append(f"  (before) {x['turn_id']} [{x['speaker_name']}]: {x['text']}")
        out.append(f"  >>> {it['turn_id']} [{it['speaker_name']} / {it['speaker_id']}; attribution {it['attribution_status']}, {it['attribution_confidence']}]:")
        out += ["      " + ln for ln in it["parsed_text"].splitlines()]
        for x in it["context_after"]:
            out.append(f"  (after)  {x['turn_id']} [{x['speaker_name']}]: {x['text']}")
        out.append("")
    out += ["", "# Segmentation sheet", "", "For each container list the turns where a NEW topical segment starts, the topic of each segment, and any turn groups that belong together (Q+A, claim+correction).", ""]
    for s in seg_items:
        out += [f"## {s['container_id']}  -  {s['channel']} ({s['container_kind']}): {s['subject']}"]
        for t in s["turns"]:
            out.append(f"  {t['turn_id']} [{t['speaker_name']}]: {t['text']}")
        out.append("")
    return "\n".join(out)


def seg_item(st, cid, why):
    lst = st["by_container"][cid]
    segs = st["seg_by_container"][cid]
    return dict(container_id=cid, channel=lst[0]["channel"], container_kind=lst[0]["container_kind"], subject=lst[0]["container_subject"], why_selected=why,
                turns=[dict(turn_id=t["turn_id"], seq=t["seq"], speaker_name=t["speaker_name"], text=t["original_text"]) for t in lst],
                system_segments=[dict(segment_id=s["segment_id"], topic=s["topic_label"], confidence=s["confidence"], start_seq=s["start_seq"], end_seq=s["end_seq"], turn_ids=s["turn_ids"]) for s in segs],
                system_boundary_turns=[s["turn_ids"][0] for s in segs[1:]])


def select_containers(st, per=5):
    cross = defaultdict(set)
    for tid, rels in st["out"].items():
        for r in rels:
            if st["seg_of"][tid]["segment_id"] != st["seg_of"][r["t"]]["segment_id"]:
                cross[st["turns"][tid]["container_id"]].add(r["rel_type"])
    picks = []
    for ch in CHANNELS:
        ids = sorted({t["container_id"] for t in st["turns"].values() if t["channel"] == ch})
        flags = {c: [] for c in ids}
        for c in ids:
            segs = st["seg_by_container"][c]
            if cross.get(c):
                flags[c].append("relation crosses a segment boundary")
            if any(len(s["turn_ids"]) == 1 for s in segs):
                flags[c].append("single-turn segment")
            if any(s["mixed"] for s in segs):
                flags[c].append("mixed-topic segment")
            if len(segs) > 1 and ch in ("Email", "Chat"):
                flags[c].append("short thread/chat split into several segments")
            if any(s["topic_label"] == "general_discussion" for s in segs):
                flags[c].append("general_discussion segment")
        order = sorted(ids, key=lambda c: h("seg", c))
        chosen = []
        for want in ("relation crosses a segment boundary", "single-turn segment", "mixed-topic segment", "short thread/chat split into several segments", "general_discussion segment"):
            hit = next((c for c in order if want in flags[c] and c not in chosen), None)
            if hit:
                chosen.append(hit)
        chosen += [c for c in order if c not in chosen][: max(0, per - len(chosen))]
        picks += [(c, flags[c] or ["control: no flag"]) for c in chosen[:per]]
    return picks


# ------------------------------------------------------------------------------------------------------------------------ known defects
def _acts(st, tid):
    return [a for a, _ in st["acts"][tid]]


def _find_estate_will(st):
    for t in sorted(st["turns"].values(), key=lambda t: h("will", t["turn_id"])):
        sc = S.topic_scores(t["text"])
        if "estate_and_trusts" in sc and RX["modal"].search(t["text"]) and not [w for w in S.TOPICS["estate_and_trusts"] if w != "will " and w in " " + t["text"].lower() + " "]:
            return t["turn_id"]


def _find_cue(st, topic, word, whole):
    for t in sorted(st["turns"].values(), key=lambda t: h(topic, word, t["turn_id"])):
        low = " " + t["text"].lower() + " "
        others = [w for w in S.TOPICS[topic] if w != word and w in low]
        in_word = word in low and not re.search(r"(?<![a-z])" + re.escape(word.strip()) + r"(?![a-z])", low)
        if word in low and not others and (in_word if not whole else not in_word):
            return t["turn_id"]


def known_defects(st):
    """Each case is reproduced from the DATABASE (label/relation present?) and shown with the system interpretation.  The 'expected' column is a reviewer assertion
    from the inspection phase, pending independent annotation; it is not ground truth."""
    kd = []

    def case(kd_id, title, why, cases, expected):
        rows = []
        for c in cases:
            tid = c["turn"]
            t = st["turns"].get(tid)
            if not t:
                rows.append(dict(turn_id=tid, reproduced=False, system="turn not found", text="")); continue
            kind = c["kind"]
            if kind == "label":
                ok, sysv = c["label"] in _acts(st, tid), f"dialogue_act={_acts(st, tid)}"
            elif kind == "absent":
                ok, sysv = c["label"] not in _acts(st, tid), f"dialogue_act={_acts(st, tid)} (no '{c['label']}')"
            elif kind == "rel":
                rel = [r for r in st["out"][tid] if r["rel_type"] == c["rel"] and (not c.get("to") or r["t"] == c["to"])]
                ok, sysv = bool(rel), "; ".join(f"{r['rel_type']} -> {r['t']} [{r['basis']}]" for r in st["out"][tid]) or "no relation"
            elif kind == "rel_cross":
                rel = [r for r in st["out"][tid] if r["rel_type"] == "answers" and r["t"] == c["to"]]
                ok = bool(rel) and st["seg_of"][tid]["segment_id"] != st["seg_of"][c["to"]]["segment_id"]
                sysv = f"answers -> {c['to']}; segments {st['seg_of'][tid]['segment_id']} vs {st['seg_of'][c['to']]['segment_id']}"
            elif kind == "cue":
                ok = c["value"] in [v.lower() for v in st["cues"][tid].get(c["cue_kind"], [])] or c["value"] in st["cues"][tid].get(c["cue_kind"], [])
                sysv = f"{c['cue_kind']}={st['cues'][tid].get(c['cue_kind'])}"
            elif kind == "topic_cue":
                ok = c["topic"] in S.topic_scores(t["text"])
                sysv = f"topic cue scores={S.topic_scores(t['text'])}; segment topic={st['seg_of'][tid]['topic_label']}"
            else:
                ok, sysv = False, "?"
            if c.get("to") and kind == "rel":
                tgt = st["turns"].get(c["to"])
                sysv += f"   [target text: {tgt['text'][:120] if tgt else '?'}]"
            rows.append(dict(turn_id=tid, reproduced=bool(ok), system=sysv, text=t["original_text"], speaker=t["speaker_name"], channel=t["channel"], container_id=t["container_id"]))
        kd.append(dict(kd_id=kd_id, title=title, why_wrong=why, expected=expected, cases=rows, reproduced_all=all(r["reproduced"] for r in rows), reproduced_n=sum(r["reproduced"] for r in rows)))

    case("KD-01", "Boilerplate 'Sorry' triggers correction", "The cue 'sorry, i|that' matches apologies and scheduling courtesy; no earlier statement is corrected.",
         [dict(turn=f"INT-{n}#T2", kind="rel", rel="corrects", to=f"INT-{n}#T1") for n in ("023", "045", "065", "113", "139")]
         + [dict(turn="INT-012#T3", kind="label", label="correction"), dict(turn="INT-015#T3", kind="label", label="correction")],
         "No correction act and no corrects relation (social/apology).")
    case("KD-02", "Email corrections linked to the wrong earlier message", "The rule links to the nearest earlier turn sharing a long word; the text names a dated earlier email (see diagnostics: date-implied targets).",
         [dict(turn=a, kind="rel", rel="corrects", to=b) for a, b in (("EML-022", "EML-014"), ("EML-020", "EML-019"), ("EML-039", "EML-033"), ("EML-068", "EML-061"), ("EML-015", "EML-009"))],
         "Target = the earlier message the text explicitly names by date (human to confirm).")
    case("KD-03", "'No apology needed' labelled rejection", "'No' opening a courtesy phrase is not a rejection of anything.", [dict(turn="CHM-0011", kind="label", label="rejection")], "social")
    case("KD-04", "Discourse openers labelled agreement", "'Good morning', 'Good question', 'Right now' open a turn; none agrees with the previous turn.",
         [dict(turn="INT-001#T1", kind="label", label="agreement"), dict(turn="INT-003#T11", kind="label", label="agreement"), dict(turn="INT-003#T7", kind="label", label="agreement"),
          dict(turn="INT-008#T2", kind="label", label="agreement")], "No agreement act (INT-008#T2 'Sure, but I reserve the right to disagree' is at most hedged agreement).")
    case("KD-05", "'I'll have to think about that' labelled commitment", "It is a deferral, not an undertaking to do a specific thing.", [dict(turn="INT-033#T12", kind="label", label="commitment")], "deferral")
    case("KD-06", "'The decision is yours' labelled decision", "It hands the decision to the client; nobody has decided.", [dict(turn="EML-017", kind="label", label="decision")], "No decision (advisor declines to decide).")
    case("KD-07", "Advisor recaps read as client decisions", "The speaker is the advisor reporting what the client decided; the label has no maker or status. EML-001's recap was later corrected by EML-028 as overstating what the client said.",
         [dict(turn=t, kind="label", label="decision") for t in ("INT-010#T17", "INT-022#T23", "EML-001", "EML-024", "EML-030")],
         "decision_status = reported_by_other; the decision-maker and confirmation need independent evidence (the client's own turn).")
    will = _find_estate_will(st)
    may = "EML-051"
    rent = _find_cue(st, "housing_and_debt", "rent", False)
    son = _find_cue(st, "family_and_education", "son ", False)
    case("KD-08", "Modal 'will'/'may' and in-word cues trigger topic or temporal labels", "'will ' (modal) fires estate_and_trusts; 'May' matches the modal 'may'; 'rent' matches 'current/different'; 'son ' matches 'reason/comparison'.",
         [c for c in ([dict(turn=will, kind="topic_cue", topic="estate_and_trusts")] if will else [])
          + [dict(turn=may, kind="cue", cue_kind="temporal_ref", value="may")]
          + ([dict(turn=rent, kind="topic_cue", topic="housing_and_debt")] if rent else []) + ([dict(turn=son, kind="topic_cue", topic="family_and_education")] if son else [])],
         "No such topic / no date reference in these turns.")
    portal = next((t["turn_id"] for t in sorted(st["turns"].values(), key=lambda t: h("portal", t["turn_id"])) if "portal" in t["text"].lower() and "scheduling_and_admin" in S.topic_scores(t["text"])
                   and not [w for w in S.TOPICS["scheduling_and_admin"] if w != "portal" and w in t["text"].lower()]), None)
    case("KD-09", "'portal' alone triggers scheduling_and_admin", "A document upload portal is not scheduling or administration of the engagement.",
         [dict(turn=portal, kind="topic_cue", topic="scheduling_and_admin")] if portal else [], "Topic judged by the content of the turn (human).")
    case("KD-10", "Questions linked to answers across segment boundaries", "The answers relation ignores segmentation, and segmentation splits question from answer.",
         [dict(turn=a, kind="rel_cross", to=b) for a, b in (("INT-003#T11", "INT-003#T10"), ("INT-003#T13", "INT-003#T12"), ("INT-009#T15", "INT-009#T14"))],
         "A question and its answer belong to one exchange (human to confirm).")
    amt = next((t["turn_id"] for t in sorted(st["turns"].values(), key=lambda t: t["turn_id"]) if any(v.endswith(" m") and "million" in t["text"] for v in st["cues"][t["turn_id"]].get("amount_ref", []))), None)
    amt_val = next((v for v in st["cues"][amt]["amount_ref"] if v.endswith(" m")), "") if amt else ""
    case("KD-11", "Amount regex truncates '$1.46 million' to '$1.46 m'", "Regex alternation matches 'm' before 'million', so the extracted amount loses its unit.",
         [dict(turn=amt, kind="cue", cue_kind="amount_ref", value=amt_val)] if amt else [], "The full amount including 'million' (extraction accuracy).")
    case("KD-12", "Question label from an embedded greeting question", "'Shall we begin?' inside a long agenda opener yields question + proposal + agreement.", [dict(turn="INT-001#T1", kind="label", label="question")], "social/agenda opening")
    case("KD-13", "Acts the rules cannot see", "No request act; a question without '?' is missed; 'Nothing is decided' is not marked as deferral.",
         [dict(turn="CHM-0056", kind="absent", label="question"), dict(turn="CHM-0076", kind="absent", label="question"), dict(turn="CHM-0008", kind="absent", label="deferral_unresolved")],
         "question / request / deferral respectively.")
    return kd


def kd_markdown(kd):
    out = ["# Known defects report", "",
           "Each case is reproduced from the stored database (`reproduced = yes` means the system output shown is really in the database). The 'expected' text is a reviewer",
           "assertion from the inspection phase and is **not ground truth**: it is to be confirmed by independent annotation (see `annotation_template.csv`).", ""]
    for k in kd:
        out += [f"## {k['kd_id']} - {k['title']}", f"Why it is suspect: {k['why_wrong']}", f"Expected (pending annotation): {k['expected']}",
                f"Reproduced: {k['reproduced_n']}/{len(k['cases'])}", ""]
        for r in k["cases"]:
            out += [f"- `{r['turn_id']}` reproduced={'yes' if r['reproduced'] else 'NO'}  ({r.get('channel', '')}, {r.get('speaker', '')})", f"  - original: {' '.join(r['text'].split())[:400]}", f"  - existing interpretation: {r['system']}"]
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------------------------------ diagnostics
def date_implied_targets(st):
    """For each system corrects relation whose text names a dated earlier email ('my February 18 email'): the same-speaker earlier turn(s) on that date.
    Candidate evidence for the human annotator, NOT ground truth."""
    out = []
    rx = re.compile(r"\bmy (" + _MON + r") (\d{1,2})(?:,? \d{4})? (?:email|note|message)", re.I)
    for tid, rels in st["out"].items():
        for r in rels:
            if r["rel_type"] != "corrects":
                continue
            t = st["turns"][tid]
            m = rx.search(t["text"])
            if not m or not t["ts"]:
                out.append(dict(turn_id=tid, system_target=r["t"], explicit_date_reference=None, date_implied=None, verdict="no dated reference in text")); continue
            mm = MONTHS.index(m.group(1).lower()) + 1
            want = f"{t['ts'][:4]}-{mm:02d}-{int(m.group(2)):02d}"
            cand = [x["turn_id"] for x in st["by_container"][t["container_id"]] if x["seq"] < t["seq"] and (x["ts"] or "")[:10] == want and x["speaker_id"] == t["speaker_id"]]
            verdict = "no same-speaker message on that date in the thread" if not cand else ("system target agrees" if r["t"] in cand else "system target DISAGREES")
            if len(cand) > 1 and r["t"] in cand:
                verdict = "system target is one of several same-day candidates"
            out.append(dict(turn_id=tid, system_target=r["t"], explicit_date_reference=f"{m.group(1)} {m.group(2)}", date_implied=cand, verdict=verdict))
    return out


def diagnostics(st):
    T = st["turns"]
    D = {}
    acts = lambda tid: _acts(st, tid)

    def add(name, ids, total=None, note=""):
        D[name] = dict(count=len(ids), total=total, examples=sorted(ids)[:6], note=note)

    corr = [t for t in T if "correction" in acts(t)]
    add("correction_labels_only_via_sorry", [t for t in corr if re.search(r"sorry,? (i|that)", T[t]["text"], re.I) and not re.search(r"i misspoke|correction|let me correct|that was wrong|i was wrong|my mistake|i meant|should clarify", T[t]["text"], re.I)], len(corr), "correction labels whose only cue is an apology")
    ag = [t for t in T if "agreement" in acts(t)]
    add("agreement_from_discourse_opener", [t for t in ag if re.match(r"^\W*(good (morning|afternoon|evening|question|news|for you)|right now|right,? (so|then))", T[t]["text"], re.I)], len(ag))
    rj = [t for t in T if "rejection" in acts(t)]
    add("rejection_from_courtesy_no", [t for t in rj if re.match(r"^\W*no (apolog|problem|worries|rush|need|trouble)", T[t]["text"], re.I)], len(rj))
    add("rejection_from_preference_statement", [t for t in rj if re.search(r"\bi don't want\b", T[t]["text"], re.I) and not re.match(r"^\W*no\b", T[t]["text"], re.I)], len(rj))
    dec = [t for t in T if "decision" in acts(t)]
    add("decision_the_decision_is_yours", [t for t in dec if re.search(r"decision is yours", T[t]["text"], re.I) and not re.search(r"decided|decision (?:to|is) recorded", T[t]["text"], re.I)], len(dec))
    add("decision_labels_on_advisor_turns", [t for t in dec if party(T[t]) == "advisor"], len(dec), "advisor-spoken: recap or recommendation, not a first-hand decision")
    add("commitment_i_have_to_think", [t for t in T if "commitment" in acts(t) and re.search(r"i'll have to (think|check)|i(?:'ll| will) think", T[t]["text"], re.I)], len([t for t in T if "commitment" in acts(t)]))
    q = [t for t in T if "question" in acts(t)]
    add("question_label_with_one_embedded_question_mark", [t for t in q if len(T[t]["text"]) > 200 and T[t]["text"].count("?") == 1 and not T[t]["text"].strip().endswith("?")], len(q))
    add("turns_with_3plus_acts", [t for t in T if len(acts(t)) >= 3], len(T))
    add("information_only_turns", [t for t in T if acts(t) == ["information"]], len(T))
    # topic cue breadth and in-word matching: forms shown so a reader can tell a legitimate stem (retire -> retirement) from a wrong word (rent -> current)
    cue = {}
    for topic, words in S.TOPICS.items():
        for w in words:
            hits = [t for t in T if w in " " + T[t]["text"].lower() + " "]
            if not hits:
                continue
            forms = Counter()
            whole = 0
            for t in hits:
                for m in re.finditer(r"[a-z]*" + re.escape(w.strip()) + r"[a-z]*", T[t]["text"].lower()):
                    forms[m.group(0)] += 1
                if re.search(r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])", T[t]["text"].lower()):
                    whole += 1
            cue[f"{topic}:{w.strip()}"] = dict(turns=len(hits), whole_word_turns=whole, top_forms=dict(forms.most_common(4)))
    D["topic_cue_breadth"] = dict(note="turns firing each cue and the surface forms matched; stems such as 'retire' legitimately match 'retirement', but 'rent'->'current' or 'son '->'reason' do not", cues=cue)
    mod = [t for t in T for v in st["cues"][t].get("temporal_ref", []) if v.lower() == "may" and v == "may"]
    add("temporal_ref_modal_may", mod, len([1 for t in T for v in st["cues"][t].get("temporal_ref", []) if v.lower() == "may"]), "lower-case 'may' counted as the month")
    add("amount_ref_truncated_m", [t for t in T if any(v.endswith(" m") for v in st["cues"][t].get("amount_ref", []))], len([t for t in T if st["cues"][t].get("amount_ref")]))
    # relations
    corrects = [(f, r) for f, rs in st["out"].items() for r in rs if r["rel_type"] == "corrects"]
    D["corrects_date_check"] = dict(note="system corrects links vs the earlier message the text names by date (candidate evidence, not ground truth)", rows=date_implied_targets(st))
    D["corrects_summary"] = dict(total=len(corrects), by_verdict=dict(Counter(r["verdict"] for r in D["corrects_date_check"]["rows"])))
    ans = [(f, r) for f, rs in st["out"].items() for r in rs if r["rel_type"] == "answers"]
    cross = [(f, r) for f, r in ans if st["seg_of"][f]["segment_id"] != st["seg_of"][r["t"]]["segment_id"]]
    allrel = [(f, r) for f, rs in st["out"].items() for r in rs]
    D["answers_cross_segment"] = dict(count=len(cross), total=len(ans), examples=sorted(f"{f}->{r['t']}" for f, r in cross)[:6])
    D["relations_cross_segment"] = dict(count=len([1 for f, r in allrel if st["seg_of"][f]["segment_id"] != st["seg_of"][r["t"]]["segment_id"]]), total=len(allrel))
    D["answers_to_multi_question_turn"] = dict(count=len([1 for f, r in ans if T[r["t"]]["text"].count("?") >= 2]), total=len(ans))
    # segments
    segs = list(st["segs"].values())
    D["segments"] = dict(total=len(segs), single_turn=len([s for s in segs if len(s["turn_ids"]) == 1]), general_discussion=len([s for s in segs if s["topic_label"] == "general_discussion"]),
                         confidence_le_0_4=len([s for s in segs if s["confidence"] <= 0.4]), mixed_topic=len([s for s in segs if s["mixed"]]),
                         email_threads_split=len([c for c in st["seg_by_container"] if T[st["by_container"][c][0]["turn_id"]]["channel"] == "Email" and len(st["seg_by_container"][c]) > 1]),
                         chats_split=len([c for c in st["seg_by_container"] if T[st["by_container"][c][0]["turn_id"]]["channel"] == "Chat" and len(st["seg_by_container"][c]) > 1]),
                         unreviewed=len([s for s in segs if s["review_status"] == "unreviewed"]))
    D["label_distribution"] = dict(dialogue_acts=dict(Counter(a for t in T for a in acts(t))), segment_topics=dict(Counter(s["topic_label"] for s in segs)))
    return D


# ------------------------------------------------------------------------------------------------------------------------ writers
def write_all(out: Path, c, src):
    out.mkdir(parents=True, exist_ok=True)
    st = load_state(c, src)
    kd = known_defects(st)
    forced = sorted({r["turn_id"] for k in kd for r in k["cases"] if r["turn_id"] in st["turns"]})
    strata = build_strata(st)
    chosen = select(st, strata, forced)
    kd_of = defaultdict(set)
    for k in kd:
        for r in k["cases"]:
            kd_of[r["turn_id"]].add(k["kd_id"])
    ids = sorted(chosen)
    items = [make_item(st, tid, chosen[tid], kd_of.get(tid, ()), n) for n, tid in enumerate(ids, 1)]
    segpicks = select_containers(st)
    seg_items = [seg_item(st, cid, why) for cid, why in segpicks]
    diag = diagnostics(st)

    def w(name, text):
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    w("items.jsonl", "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items))
    w("segmentation_items.jsonl", "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in seg_items))
    for name, cols, rows in (("annotation_template.csv", TEMPLATE_COLS, [[i["item_id"]] + [""] * (len(TEMPLATE_COLS) - 1) for i in items]),
                             ("segmentation_template.csv", SEG_TEMPLATE_COLS, [[s["container_id"]] + [""] * (len(SEG_TEMPLATE_COLS) - 1) for s in seg_items])):
        with open(out / name, "w", encoding="utf-8", newline="") as f:
            cw = csv.writer(f, lineterminator="\n")
            cw.writerow(cols)
            cw.writerows(rows)
    w("review_sheet_blind.md", blind_sheet(items, seg_items))
    w("labels.json", json.dumps(LABELS, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    w("label_definitions.md", label_markdown())
    w("known_defects.json", json.dumps(kd, indent=2, ensure_ascii=False) + "\n")
    w("known_defects.md", kd_markdown(kd))
    w("diagnostics.json", json.dumps(diag, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    comp = dict(items=len(items), by_channel=dict(Counter(i["channel"] for i in items)), by_stratum={n: sum(1 for i in items if n in i["strata"]) for n in list(strata) + ["known_defect"]},
                stratum_quota={n: q for n, (_, q, _) in strata.items()}, stratum_purpose={n: p for n, (_, _, p) in strata.items()},
                segmentation_containers=len(seg_items), seg_by_channel=dict(Counter(s["channel"] for s in seg_items)), source_matches_parsed=all(i["source_matches_parsed"] for i in items),
                tool_version=VERSION)
    w("sample_manifest.json", json.dumps(comp, indent=2, sort_keys=True) + "\n")
    return items, seg_items, kd, diag, comp


def label_markdown():
    out = ["# Label definitions and permitted values", "", "Fill `annotation_template.csv`. Multi-valued fields use `|`. Blank = not annotated; `none` = nothing applies; `unresolved` = cannot tell from the evidence.",
           "Judge each turn from its own text and the context shown. Distinguish what the speaker SAYS from what the speaker ATTRIBUTES to someone else. Do not infer a correction target from shared vocabulary:",
           "a `correction_target_turn` requires `correction_evidence` other than `none`. An advisor's recap is not a confirmed client decision.", ""]
    for field, vals in LABELS.items():
        out += [f"## {field}", ""] + [f"- **{k}**: {v}" for k, v in vals.items()] + [""]
    out += ["## Other fields", "", "- **correction_target_turn**: a turn id from the same container, or `none` / `unresolved`.", "- **answers_turn**: the turn id of the question this turn answers, or `none` / `unresolved`.",
            "- **temporal_refs**: verbatim date/time expressions in this turn separated by `|`, or `none`. A modal verb ('may', 'will') is not a date.",
            "- **Segmentation sheet**: `boundary_turns` = turn ids that begin a new topical segment (first turn excluded) or `none`; `segment_topics` = topics in order (`|`); `related_groups` = turn groups that must stay together, `;` between groups and `,` between ids.", ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------------------------------ scoring
def _split(v, sep="|"):
    v = (v or "").strip()
    return None if v == "" else [x.strip() for x in v.split(sep) if x.strip()]


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if (p is not None and r is not None) else None)
    return dict(tp=tp, fp=fp, fn=fn, precision=p, recall=r, f1=f)


def load_items(path):
    return {j["item_id"]: j for j in (json.loads(l) for l in open(path, encoding="utf-8") if l.strip())}


def load_gold(path):
    return list(csv.DictReader(open(path, encoding="utf-8", newline="")))


def score(items, gold):
    """Metrics over the annotated rows.  Blank fields are not annotated; 'unresolved' is excluded from P/R and counted."""
    res, rows = {}, [g for g in gold if g["item_id"] in items]
    res["annotated_rows"] = len(rows)
    # --- dialogue acts per class
    cls = defaultdict(lambda: [0, 0, 0])
    skipped = n = 0
    for g in rows:
        G = _split(g.get("dialogue_acts"))
        if G is None:
            continue
        if "unresolved" in G:
            skipped += 1; continue
        n += 1
        sysset = {SYS_ACT_MAP.get(a["act"], a["act"]) for a in items[g["item_id"]]["system"]["dialogue_acts"]}
        for k in set(G) | sysset:
            cls[k][0] += k in G and k in sysset
            cls[k][1] += k not in G and k in sysset
            cls[k][2] += k in G and k not in sysset
    per = {k: prf(*v) | dict(support=v[0] + v[2]) for k, v in sorted(cls.items())}
    tp, fp, fn = (sum(v[i] for v in cls.values()) for i in range(3))
    res["dialogue_acts"] = dict(items=n, unresolved_excluded=skipped, per_class=per, micro=prf(tp, fp, fn),
                                macro_f1=(sum(x["f1"] or 0 for x in per.values()) / len(per)) if per else None,
                                unsupported_by_system=[k for k in UNSUPPORTED_ACTS if k in cls], note="classes the rule set cannot output have recall 0 by construction")
    # --- topics (system topic = the turn's segment label)
    tc = defaultdict(lambda: [0, 0, 0])
    nt = 0
    for g in rows:
        G = _split(g.get("topics"))
        if G is None or "unresolved" in G:
            continue
        nt += 1
        sysset = {items[g["item_id"]]["system"]["topic_segment"]["topic"]}
        for k in set(G) | sysset:
            tc[k][0] += k in G and k in sysset
            tc[k][1] += k not in G and k in sysset
            tc[k][2] += k in G and k not in sysset
    ptc = {k: prf(*v) | dict(support=v[0] + v[2]) for k, v in sorted(tc.items())}
    res["topics"] = dict(items=nt, per_label=ptc, micro=prf(*(sum(v[i] for v in tc.values()) for i in range(3))),
                         macro_f1=(sum(x["f1"] or 0 for x in ptc.values()) / len(ptc)) if ptc else None)
    # --- decision attribution
    d = Counter()
    for g in rows:
        mk, stt, G = (g.get("decision_maker") or "").strip(), (g.get("decision_status") or "").strip(), _split(g.get("dialogue_acts")) or []
        if not mk and not stt:
            continue
        it = items[g["item_id"]]
        sys_dec = any(a["act"] == "decision" for a in it["system"]["dialogue_acts"])
        sys_maker = "client" if (it["speaker_id"] or "").startswith("CLIENT") else "advisor" if (it["speaker_id"] or "").startswith("ADVISOR") else "third_party"
        first_hand = stt == "confirmed" and mk in ("client", "advisor", "joint", "third_party")
        if sys_dec:
            d["system_decision_labels_annotated"] += 1
            if first_hand and (mk == sys_maker or (mk == "joint" and sys_maker in ("client", "advisor"))):
                d["correct_attribution"] += 1
            elif stt == "reported_by_other":
                d["recap_read_as_decision"] += 1
            elif stt in ("exploratory", "recommendation_only", "none", "conditional"):
                d["not_a_confirmed_decision"] += 1
            elif mk in ("none",):
                d["no_decision_at_all"] += 1
            elif first_hand:
                d["wrong_decision_maker"] += 1
        if first_hand:
            d["gold_first_hand_decisions"] += 1
            d["gold_first_hand_found_with_right_maker"] += bool(sys_dec and (mk == sys_maker or (mk == "joint" and sys_maker in ("client", "advisor"))))
    n_sys = d["system_decision_labels_annotated"]
    res["decision_attribution"] = dict(counts=dict(d), attribution_precision=(d["correct_attribution"] / n_sys) if n_sys else None,
                                       recall=(d["gold_first_hand_found_with_right_maker"] / d["gold_first_hand_decisions"]) if d["gold_first_hand_decisions"] else None)
    # --- relations
    for rel, col, ev in (("corrects", "correction_target_turn", "correction_evidence"), ("answers", "answers_turn", None)):
        c = Counter()
        for g in rows:
            gv = (g.get(col) or "").strip()
            if not gv:
                continue
            sys_t = {r["to"] for r in items[g["item_id"]]["system"]["relations_out"] if r["rel_type"] == rel}
            if gv == "unresolved":
                c["unresolved_excluded"] += 1; continue
            concrete = gv != "none"
            if rel == "corrects" and concrete and (g.get(ev) or "").strip() in ("", "none"):
                c["invalid_gold_target_without_evidence"] += 1; continue
            c["rows"] += 1
            if concrete:
                c["gold_links"] += 1
                c["gold_links_found"] += gv in sys_t
            if sys_t:
                c["system_relations"] += len(sys_t)
                c["system_rows_where_gold_has_a_relation"] += concrete
                c["system_rows_with_correct_target"] += concrete and gv in sys_t
        sysr = c["system_relations"]
        res[f"{rel}_relations"] = dict(counts=dict(c), relation_precision=(c["system_rows_where_gold_has_a_relation"] / sysr) if sysr else None,
                                       target_accuracy=(c["system_rows_with_correct_target"] / c["system_rows_where_gold_has_a_relation"]) if c["system_rows_where_gold_has_a_relation"] else None,
                                       recall=(c["gold_links_found"] / c["gold_links"]) if c["gold_links"] else None)
    # --- temporal
    ex = [0, 0, 0]
    le = [0, 0, 0]
    nt = 0
    for g in rows:
        G = _split(g.get("temporal_refs"))
        if G is None or "unresolved" in G:
            continue
        nt += 1
        gs = set() if G == ["none"] else {x.lower() for x in G}
        ss = {x.lower() for x in items[g["item_id"]]["system"]["cues"].get("temporal_ref", [])}
        ex[0] += len(gs & ss); ex[1] += len(ss - gs); ex[2] += len(gs - ss)
        mt_s = {s for s in ss if any(s in x or x in s for x in gs)}
        mt_g = {x for x in gs if any(s in x or x in s for s in ss)}
        le[0] += len(mt_g); le[1] += len(ss - mt_s); le[2] += len(gs - mt_g)
    res["temporal"] = dict(items=nt, exact=prf(*ex), lenient_overlap=prf(*le))
    return res


def score_segmentation(seg_items, gold):
    byid = {s["container_id"]: s for s in seg_items}
    rows = [g for g in gold if g["container_id"] in byid and _split(g.get("boundary_turns")) is not None]
    ex = [0, 0, 0]
    tol = [0, 0, 0]
    grp_total = grp_kept = gold_segs = sys_segs = 0
    for g in rows:
        s = byid[g["container_id"]]
        seq = {t["turn_id"]: t["seq"] for t in s["turns"]}
        gb = set() if _split(g["boundary_turns"]) == ["none"] else set(_split(g["boundary_turns"]))
        sb = set(s["system_boundary_turns"])
        ex[0] += len(gb & sb); ex[1] += len(sb - gb); ex[2] += len(gb - sb)
        near = lambda a, B: any(abs(seq[a] - seq[b]) <= 1 for b in B if a in seq and b in seq)
        tol[0] += len([b for b in gb if near(b, sb)]); tol[1] += len([b for b in sb if not near(b, gb)]); tol[2] += len([b for b in gb if not near(b, sb)])
        gold_segs += len(gb) + 1; sys_segs += len(sb) + 1
        seg_of = {tid: sg["segment_id"] for sg in s["system_segments"] for tid in sg["turn_ids"]}
        for grp in (_split(g.get("related_groups"), ";") or []):
            ids = [x.strip() for x in grp.split(",") if x.strip()]
            if len(ids) > 1 and all(i in seg_of for i in ids):
                grp_total += 1
                grp_kept += len({seg_of[i] for i in ids}) == 1
    return dict(containers=len(rows), boundary_exact=prf(*ex), boundary_within_1_turn=prf(*tol),
                related_group_preservation=(grp_kept / grp_total) if grp_total else None, related_groups=grp_total,
                over_split_ratio=(sys_segs / gold_segs) if gold_segs else None)


# ------------------------------------------------------------------------------------------------------------------------ cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--out", default=str(DEFAULT_OUT)); b.add_argument("--db", default=str(IE.DB))
    d = sub.add_parser("diagnose"); d.add_argument("--db", default=str(IE.DB))
    s = sub.add_parser("score")
    s.add_argument("--items", required=True); s.add_argument("--gold", required=True); s.add_argument("--seg-items"); s.add_argument("--seg-gold")
    a = ap.parse_args(argv)
    if a.cmd == "score":
        out = score(load_items(a.items), load_gold(a.gold))
        if a.seg_items and a.seg_gold:
            out["segmentation"] = score_segmentation(list(load_items_seg(a.seg_items)), load_gold(a.seg_gold))
        print(json.dumps(out, indent=2))
        return
    c = IE.connect(a.db)
    src = IE.Source()
    if a.cmd == "diagnose":
        print(json.dumps(diagnostics(load_state(c, src)), indent=2, ensure_ascii=False))
    else:
        items, seg_items, kd, diag, comp = write_all(Path(a.out), c, src)
        print(json.dumps(comp, indent=2))
        print(f"known defects reproduced: {sum(k['reproduced_n'] for k in kd)}/{sum(len(k['cases']) for k in kd)} cases; written to {a.out}")
    c.close()


def load_items_seg(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
