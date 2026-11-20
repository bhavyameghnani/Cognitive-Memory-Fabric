"""Read-only audit of scenario quality (support in the source records) and dataset realism (routine vs memory-bearing).

Usage: python -I datasets/audit_dataset.py       (prints tables; writes nothing; exit 0)
Heuristic by design: regexes flag candidate evidence per scenario so a reviewer can confirm it. It does not label
ground truth and is not used by any ingestion path.
"""
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "datasets"


def read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


_U = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_T = {w: 10 * i for i, w in enumerate("x x twenty thirty forty fifty sixty seventy eighty ninety".split())}


def numbers(text):
    """All integers in text, from digits ('$14k', '2,800') and spelled-out words ('thirty-one thousand'); 'thirteen point five million' -> 13500000."""
    t = text.lower().replace("-", " ").replace(",", "")
    out = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(k|m|million|thousand)?", t):
        out.add(round(float(m.group(1)) * {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get(m.group(2), 1)))
    for m in re.finditer(r"([a-z ]+?) point ([a-z]+) (million|thousand)", t):
        a_, b_ = _U.get(m.group(1).split()[-1]), _U.get(m.group(2))
        if a_ is not None and b_ is not None: out.add(round((a_ + b_ / 10) * {"million": 1e6, "thousand": 1e3}[m.group(3)]))
    cur = total = 0; seen = False
    for tok in re.findall(r"[a-z]+", t) + ["#"]:
        if tok in _U: cur += _U[tok]; seen = True
        elif tok in _T: cur += _T[tok]; seen = True
        elif tok == "hundred" and seen: cur = (cur or 1) * 100
        elif tok == "thousand" and seen: total += (cur or 1) * 1000; cur = 0
        elif tok == "million" and seen: total += (cur or 1) * 1000000; cur = 0
        elif tok == "and" and seen: continue
        else:
            if seen:
                out.add(total + cur)
                if total: out.add(total)
            cur = total = 0; seen = False
    return out


CATS = {   # the seven scenario-quality categories from the audit brief
    "exploratory": r"\b(might|maybe|inclined|leaning|thinking about|exploring|an idea|possibility|only a thought|just a thought|daydream|wondering|considering|not sure)\b",
    "confirmed_decision": r"\b(decided|i.ve decided|you have decided|decision is|we.ve decided|i want to (do|go|sell|keep)|go ahead|let.s do)\b",
    "correction": r"\b(correct(ion|ed|ing)?|clarif\w+|that was wrong|i was wrong|not (additional|extra) tuition|inclined, not accepted|i had read)\b",
    "advisor_vs_client": r"\b(my (earlier|march|february|may|june)\b.*\b(wrong|too loosely|incorrect)|i wrote that|following your acceptance|you told me (you are|it was|that)|not something you decided|is not a purpose)\b",
    "changed_fact": r"\b(instead of|changed|now (comes|five|sixty|63)|used to|actually|stops at closing|keeps you in|five (aide )?days|sixty percent|a different)\b",
    "unresolved": r"\b(not yet|still (waiting|open|undecided|unconfirmed)|hasn.t arrived|haven.t decided|undecided|unconfirmed|open question|pending|no decision|left open|stays open|hasn.t been decided|not decided|isn.t decided)\b",
    "pending_commitment": r"\b(i.ll (send|mail|forward|chase|model|show|call|ask|check|record|submit|upload|hold|leave|prepare|look)|will do|i will send|please send|send me|by (friday|monday|june \d+)|before (our next|the half-year)|to follow)\b",
    "incomplete_xref": r"\b(yesterday.s|this morning.s|our (april|march|may|june|february) \d+ (call|meeting)|my (march|february|may|june|april) \d+ email|last (week|time)|as (we|you) (discussed|said|mentioned)|that note|that email|the other day|following (today.s|our|this morning.s|yesterday.s) (call|meeting)|what we talked about)\b",
}
CATS = {k: re.compile(v, re.I) for k, v in CATS.items()}
MEMORY_SIGNALS = ("confirmed_decision", "correction", "advisor_vs_client", "changed_fact")


def main():
    calls = read(ROOT / "data/conversations.csv")
    man = {r["interaction_id"]: r for r in read(DS / "source_manifest.csv")}
    tu = read(DS / "meetings/meeting_turns.csv"); em = read(DS / "emails/emails.csv"); ch = read(DS / "chats/chats.csv")
    sc = read(DS / "evaluation/scenarios.csv"); sl = read(DS / "evaluation/scenario_links.csv")

    text, speaker = defaultdict(list), defaultdict(list)    # source id -> [(role, text)]
    for r in calls: text[r["interaction_id"]].append((r["speaker"], r["utterance"]))
    for r in tu: text[r["meeting_id"]].append((r["speaker_role"], r["utterance"]))
    for r in em: text[r["thread_id"]].append((("Advisor" if r["sender_id"].startswith("ADVISOR") else "Client" if r["sender_id"].startswith("CLIENT") else "Other"), r["body"]))
    for r in ch: text[r["chat_conversation_id"]].append((r["sender_role"], r["message_text"]))
    chan = {i: m["assigned_channel"] for i, m in man.items()}
    chan.update({k: "Meetings" for k in text if k.startswith("MTG-")}); chan.update({k: "Email" for k in text if k.startswith("THR-")}); chan.update({k: "Chat" for k in text if k.startswith("CHT-")})

    print("SCENARIO SUPPORT (records in the scenario whose own text matches each category; 0 = no textual support found)")
    cols = list(CATS)
    print(f"{'scenario':10}" + "".join(f"{c[:10]:>11}" for c in cols))
    by_s = defaultdict(list)
    for r in sl: by_s[r["scenario_id"]].append(r["source_id"])
    gaps = []
    for s in sc:
        row = []
        for c in cols:
            n = sum(1 for sid in by_s[s["scenario_id"]] if any(CATS[c].search(t) for _, t in text[sid]))
            row.append(n)
            if n == 0: gaps.append((s["scenario_id"], c))
        print(f"{s['scenario_id']:10}" + "".join(f"{n:>11}" for n in row))
    tot = {c: sum(1 for s in sc if any(any(CATS[c].search(t) for _, t in text[sid]) for sid in by_s[s["scenario_id"]])) for c in cols}
    print("scenarios with at least one supporting record:", tot, f"of {len(sc)}")

    # every scenario that CLAIMS a correction must have a record whose text carries a correction cue
    unsupported = []
    for s in sc:
        claims = any("correct" in l["storyline_note"].lower() for l in sl if l["scenario_id"] == s["scenario_id"])
        if claims and not any(any(CATS["correction"].search(t) or CATS["changed_fact"].search(t) for _, t in text[sid]) for sid in by_s[s["scenario_id"]]):
            unsupported.append(s["scenario_id"])
    print("scenarios claiming a correction with no corrective text in any linked record:", unsupported or "none")

    # dollar figures in generated text that never appear (as digits OR spelled-out words) in the same client's originals
    orig = defaultdict(str)
    for r in calls: orig[r["client_id"]] += " " + r["utterance"]
    have = {c: numbers(t) for c, t in orig.items()}
    newfig = defaultdict(set)
    for rows, col in ((tu, "utterance"), (em, "body"), (ch, "message_text")):
        for r in rows:
            for m in re.finditer(r"\$([\d,]+(?:\.\d+)?)\s*(k|M|million|thousand)?", r[col]):
                v = round(float(m.group(1).replace(",", "")) * {"k": 1e3, "thousand": 1e3, "M": 1e6, "million": 1e6}.get(m.group(2), 1))
                if v >= 1000 and v not in have[r["client_id"]]: newfig[r["client_id"]].add(m.group(0).strip())
    print("\nDOLLAR FIGURES IN GENERATED TEXT NOT FOUND (digits or words) IN THE SAME CLIENT'S ORIGINAL CALLS:", dict(newfig) or "none")

    print("\nREALISM: share of records whose own text carries no memory-bearing signal (decision/correction/advisor-vs-client/changed fact)")
    for label, ids in (("original Calls", [i for i in text if i.startswith("INT-") and chan[i] == "Calls"]),
                       ("original Meetings", [i for i in text if i.startswith("INT-") and chan[i] == "Meetings"]),
                       ("generated Meetings", [i for i in text if i.startswith("MTG-")]),
                       ("generated Email threads", [i for i in text if i.startswith("THR-")]),
                       ("generated Chats", [i for i in text if i.startswith("CHT-")])):
        routine = sum(1 for i in ids if not any(any(CATS[c].search(t) for _, t in text[i]) for c in MEMORY_SIGNALS))
        print(f"  {label:24} {len(ids):4} records, {routine:3} without a memory signal ({100 * routine / max(1, len(ids)):.0f}%)")
    # message level for the short-form channels
    for label, rows, col in (("generated email messages", em, "body"), ("generated chat messages", ch, "message_text")):
        routine = sum(1 for r in rows if not any(CATS[c].search(r[col]) for c in MEMORY_SIGNALS))
        print(f"  {label:24} {len(rows):4} messages, {routine:3} without a memory signal ({100 * routine / len(rows):.0f}%)")
    linked = {r["source_id"] for r in sl}
    print(f"  original interactions in no scenario: {sum(1 for i in man if i not in linked)} of {len(man)}")
    print(f"  generated chat messages <= 12 words: {sum(1 for r in ch if len(r['message_text'].split()) <= 12)} of {len(ch)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
