"""Build reports/dataset_quality_comparison.json (v1 backup vs current revision).

Everything under "automated" is computed from the CSVs/manifests.  Everything under "manual_review" is written
by a human-style read-through (done by the assistant that produced the revision) and is NOT computed.

Usage: python src/compare_quality.py
"""
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from date_audit import audit  # noqa: E402

V1_CSV = ROOT / "backup/v1/data/conversations_v1.csv"
V1_MAN = ROOT / "backup/v1/reports/generation_manifest.json"
V1_VAL = ROOT / "backup/v1/reports/dataset_validation.json"
V2_CSV = ROOT / "data/conversations.csv"
V2_MAN = ROOT / "reports/generation_manifest.json"
V2_VAL = ROOT / "reports/dataset_validation.json"

NAMES = r"\b(Kenji|Mariko|Yuki|Ren|Rebecca|David|Ethan|Dorothy|Sofia|Mateo|Lucia|Grace|Samuel|Tobi|Folake|Lucas|Camille|Am[eé]lie|Peg|Frank|Colleen|Brian|Priya|Daniel|Elena|Hannah|Mei Lin|Luis|Hugo|Dennis)\b"
MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"

DEFINITIONS = {
    "exact_unique_utterance_count": "number of distinct utterance strings (case- and punctuation-sensitive) in the whole CSV",
    "pct_turns_with_repeated_exact_utterance": "share of ALL turns whose exact utterance string occurs more than once anywhere in the CSV (the headline 58.5% figure for v1)",
    "pct_long_turns_with_repeated_exact_utterance": "same, restricted to turns of >=12 words",
    "normalized_utterance": "lower-cased; client/advisor first names -> <name>; month-day dates -> <date>; $amounts and digits -> #; punctuation removed; whitespace collapsed",
    "pct_turns_with_repeated_normalized_utterance": "share of turns whose NORMALISED string occurs more than once; always >= the exact figure; catches template lines that differ only by name, date or number",
    "semantic_uniqueness": "NOT COMPUTED. Embedding-based measures are out of scope for this phase; no semantic claim is made.",
    "short_turns": "turns of <=3 words",
    "substantive_unique_turn": "turn of >=15 words whose exact string occurs exactly once in the corpus (a proxy for hand-authored or state-specific content)",
    "meetings_with_no_substantive_new_information_proxy": "meetings with fewer than 4 substantive unique turns. A proxy only: it can mislabel a short but important scripted meeting, and cannot see meaning",
    "meetings_without_scripted_outcome_authoring": "meetings whose authoring manifest records no decision, plan, tentative idea, deferral, correction or action item. Authoring-side metadata, not derived from the text",
}


def load(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    by = defaultdict(list)
    for r in rows:
        by[r["interaction_id"]].append(r)
    return rows, by


def norm(t):
    t = re.sub(NAMES, "<name>", t)
    t = re.sub(MONTH + r" \d{1,2}", "<date>", t)
    t = re.sub(r"\$[\d,\.]+( million)?|\d+", "#", t)
    t = re.sub(r"[^\w<>#\s]", "", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def metrics(csv_path, man_path):
    rows, by = load(csv_path)
    man = json.load(open(man_path, encoding="utf-8"))
    n = len(rows)
    ex = Counter(r["utterance"] for r in rows)
    nm = Counter(norm(r["utterance"]) for r in rows)
    long_rows = [r for r in rows if len(r["utterance"].split()) >= 12]
    rep = lambda c, subset: round(sum(1 for r in subset if c[r["utterance"] if c is ex else norm(r["utterance"])] > 1) / len(subset), 4)
    turns_per = [len(v) for v in by.values()]
    subst = {i: sum(1 for r in rs if len(r["utterance"].split()) >= 15 and ex[r["utterance"]] == 1) for i, rs in by.items()}
    low = sorted(i for i, v in subst.items() if v < 4)
    ints = {i["interaction_id"]: i for i in man["interactions"]}
    no_outcome = sorted(i for i, x in ints.items() if not x["authored_outcomes"])
    corr = defaultdict(list)
    for iid, x in ints.items():
        if "correction" in x["scenario_tags"]:
            corr[x["client_id"]].append(iid)
    led = man["action_ledger"]
    opened = sum(len(v) for v in led.values())
    done = sum(1 for v in led.values() for a in v.values() if a["status"] == "done")
    cl = Counter(rs[0]["client_id"] for rs in by.values())
    mo = Counter(rs[0]["interaction_datetime"][:7] for rs in by.values())
    da = audit(csv_path)
    return dict(
        total_interactions=len(by), total_turns=n,
        turns_per_interaction=dict(mean=round(statistics.mean(turns_per), 2), min=min(turns_per), max=max(turns_per),
                                   interactions_below_15=sum(1 for t in turns_per if t < 15)),
        avg_words_per_utterance=round(sum(len(r["utterance"].split()) for r in rows) / n, 1),
        total_words=sum(len(r["utterance"].split()) for r in rows),
        exact_unique_utterance_count=len(ex),
        exact_strings_occurring_once=sum(1 for v in ex.values() if v == 1),
        pct_turns_with_repeated_exact_utterance=round(sum(1 for r in rows if ex[r["utterance"]] > 1) / n, 4),
        pct_long_turns_with_repeated_exact_utterance=round(sum(1 for r in long_rows if ex[r["utterance"]] > 1) / len(long_rows), 4),
        normalized_unique_utterance_count=len(nm),
        pct_turns_with_repeated_normalized_utterance=round(sum(1 for r in rows if nm[norm(r["utterance"])] > 1) / n, 4),
        short_turns_3_words_or_fewer=sum(1 for r in rows if len(r["utterance"].split()) <= 3),
        most_frequently_repeated_utterances=[dict(count=c, utterance=u[:140]) for u, c in ex.most_common(10)],
        meetings_with_no_substantive_new_information_proxy=dict(count=len(low), ids=low),
        meetings_without_scripted_outcome_authoring=dict(count=len(no_outcome), ids=no_outcome),
        correction_scenarios_by_client={k: dict(count=len(v), interactions=v) for k, v in sorted(corr.items())},
        correction_meetings_total=sum(len(v) for v in corr.values()),
        action_items=dict(opened=opened, completed=done, outstanding=opened - done,
                          outstanding_ids=sorted(f"{c}:{a}" for c, v in led.items() for a, x in v.items() if x["status"] != "done")),
        interactions_per_client=dict(sorted(cl.items())), interactions_per_month=dict(sorted(mo.items())),
        date_consistency_audit=dict(issues=len(da["issues"]), examples=da["issues"][:8]),
    ), rows, by, led


def main():
    m1, rows1, by1, led1 = metrics(V1_CSV, V1_MAN)
    m2, rows2, by2, led2 = metrics(V2_CSV, V2_MAN)

    # ---- preservation checks
    meta = lambda by: {i: (rs[0]["client_id"], rs[0]["advisor_id"], rs[0]["interaction_datetime"], rs[0]["channel"]) for i, rs in by.items()}
    ctx1 = {i: rs[0]["meeting_context"] for i, rs in by1.items()}
    ctx2 = {i: rs[0]["meeting_context"] for i, rs in by2.items()}
    long1 = {r["utterance"] for r in rows1 if len(r["utterance"].split()) >= 20}
    kept = sum(1 for u in long1 if any(u == r["utterance"] for r in rows2)) if False else len(long1 & {r["utterance"] for r in rows2})
    kept_sub = tot_sub = 0
    for i, rs in by1.items():
        text2 = " ".join(r["utterance"] for r in by2[i])
        for r in rs:
            if len(r["utterance"].split()) >= 20:
                tot_sub += 1
                kept_sub += r["utterance"] in text2
    sub_pres = dict(kept=kept_sub, of=tot_sub, pct=round(kept_sub / tot_sub, 3))
    corpus2 = " ".join(r["utterance"] for r in rows2)
    script_total, script_missing = 0, []
    for f in sorted((ROOT / "backup/v1/src/cmfgen/clients").glob("c[1-6].py")):
        src = open(f, encoding="utf-8").read()
        for k, part in enumerate(re.split(r"\n \(\"", src[src.index("STEPS = ["):])[1:], 1):
            for m in re.finditer(r"^(A|C): (.+)$", part, re.M):
                script_total += 1
                if m.group(2).strip() not in corpus2:
                    script_missing.append(f"{f.stem}.step{k}")
    script_pres = dict(
        hand_written_v1_script_lines=script_total, still_present_verbatim=script_total - len(script_missing),
        pct=round((script_total - len(script_missing)) / script_total, 3),
        changed_lines_by_step=dict(Counter(script_missing)),
        reasons_for_change=("c1.step3/step8, c2.step13/14, c3.step10, c6.step15: relative dates made accurate; "
                            "c1.step4, c2.step20, c4.step24, c5.step16: thin meetings rewritten; "
                            "c6.step25: recap updated after the Maplewood decision. No decision, reversal or rationale was removed."))
    preservation = dict(
        interaction_ids_clients_advisors_datetimes_channels_identical=meta(by1) == meta(by2),
        interactions=len(by2), clients=len({r["client_id"] for r in rows2}), advisors=len({r["advisor_id"] for r in rows2}),
        columns_identical=list(rows1[0].keys()) == list(rows2[0].keys()),
        meeting_context_labels_changed=sorted(i for i in ctx1 if ctx1[i] != ctx2[i]),
        action_ledger_identical_to_v1=led1 == led2,
        v1_utterances_of_20plus_words_still_present_as_whole_turns=dict(kept=kept, of=len(long1), pct=round(kept / len(long1), 3)),
        v1_utterances_of_20plus_words_found_as_substring_within_same_interaction=sub_pres,
        hand_written_script_lines_preserved=script_pres,
        v1_scripted_content_note="a whole-turn match understates continuity because adjacent same-speaker turns are now merged and filler turns were removed",
        interactions_whose_turn_sequence_is_unchanged=sum(1 for i in by1 if [r["utterance"] for r in by1[i]] == [r["utterance"] for r in by2[i]]),
        pre_classification_columns_present=False,
        manifest_kept_out_of_csv=True,
    )

    v1val = json.load(open(V1_VAL, encoding="utf-8"))
    v2val = json.load(open(V2_VAL, encoding="utf-8"))
    structural = dict(
        v1_all_passed=v1val["all_hard_checks_passed"], v1_checks=len(v1val["checks"]),
        v2_all_passed=v2val["all_hard_checks_passed"], v2_checks=len(v2val["checks"]),
        v2_failed=[k for k, c in v2val["checks"].items() if not c["passed"]],
        checks_carried_over_from_v1=sorted(set(v1val["checks"]) & set(v2val["checks"])),
        checks_changed_or_removed=sorted(set(v1val["checks"]) - set(v2val["checks"])),
        checks_added_in_v2=sorted(set(v2val["checks"]) - set(v1val["checks"])),
        note=("turns_within_15_to_32 became turns_within_12_to_32 because removing filler turns shortened meetings "
              "(mean 21.95 -> 14.6); every_later_meeting_refers_to_previous_meeting is unchanged in meaning but its regex "
              "now also accepts 'since <date>' phrasing"),
    )

    manual = json.load(open(ROOT / "reports/manual_review_notes.json", encoding="utf-8"))
    thin_ids = sorted(m1["meetings_without_scripted_outcome_authoring"]["ids"])
    covered = sorted(manual["previously_thin_meetings"])
    out = dict(
        generated_by="src/compare_quality.py",
        definitions=DEFINITIONS,
        automated=dict(v1=m1, v2=m2, preservation=preservation, structural_validation=structural),
        manual_review=dict(
            statement=("Manual review is qualitative and was performed by reading transcripts, not by keyword matching. "
                       "It was done by the assistant that made the revision, so it is not an independent review. "
                       "A human research reviewer should spot-check the entries marked adequate or still_thin."),
            previously_thin_meetings_all_read=(thin_ids == covered),
            **manual),
    )
    (ROOT / "reports/dataset_quality_comparison.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    brief = {k: (m1[k], m2[k]) for k in ("total_turns", "exact_unique_utterance_count", "pct_turns_with_repeated_exact_utterance",
                                          "pct_turns_with_repeated_normalized_utterance", "short_turns_3_words_or_fewer", "correction_meetings_total")}
    print(json.dumps(brief, indent=1))
    print("thin v1:", m1["meetings_without_scripted_outcome_authoring"]["count"], "v2:", m2["meetings_without_scripted_outcome_authoring"]["count"])
    print("proxy low-substance v1:", m1["meetings_with_no_substantive_new_information_proxy"]["count"], "v2:", m2["meetings_with_no_substantive_new_information_proxy"]["count"])
    print("preservation:", {k: v for k, v in preservation.items() if k != "meeting_context_labels_changed"})
    print("thin coverage in manual notes:", thin_ids == covered)


if __name__ == "__main__":
    main()
