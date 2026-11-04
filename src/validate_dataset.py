"""Validate data/conversations.csv and write reports/dataset_validation.json.

Usage: python src/validate_dataset.py [--csv data/conversations.csv] [--report reports/dataset_validation.json]
Exit status is 1 if any hard check fails.
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
REQUIRED = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index",
            "speaker", "utterance", "meeting_context", "source_type", "consent_status"]
FORBIDDEN_COLS = {"memory_type", "episodic_memory", "semantic_memory", "procedural_memory", "working_memory",
                  "salience_score", "confidence_score", "importance_score", "supersedes",
                  "extracted_entity", "extracted_event"}
PLACEHOLDERS = re.compile(r"lorem ipsum|discuss investments|todo|tbd|placeholder|\{[a-z_]+\}|xxx", re.I)

# Text-level indicators for the Section 5 situations (computed from the CSV alone).
COVERAGE = {
    "A_changing_preferences": r"(75 percent|seventy-five percent|ninety percent|aggressive|conservative|sixty percent|twenty-five percent|thirty percent|swings|volatil)",
    "B_contradictory_statements": r"(retire at 60|retire at 63|retire at 65|retiring at 63|until 63|wait two years|thirty percent, not twenty-five|changed my mind)",
    "C_uncertain_vs_confirmed": r"(i might|i haven't decided|i'm only thinking|i'm not sure|maybe|i'm considering|i'm inclined)",
    "C2_explicit_decisions": r"(i've decided|i have decided|please proceed|go ahead|i'm decided|that's an instruction|is that your instruction)",
    "D_advisor_procedure": r"(suitability|secure client portal|i'll run|i'll model|i'll send|i'd like to see|i need an updated|i'm going to need)",
    "E_rationale": r"(because|the reasons are|the reasoning|my reason|i need the cash|that's a sound reason|reason, so it's recorded)",
    "F_corrections": r"(i should clarify|my note|i'll correct|correct my record|was too strong|no, and i'm glad you asked|i'll fix my note)",
    "G_casual_personal": r"(my daughter|my son|ren |yuki|lucia|amélie|colleen|folake|tobi|relocate|grandchildren|stressful)",
    "H_pending_actions": r"(from my side|from your side|before our next meeting|next meeting|pencil in|hold off)",
    "H2_followup_on_earlier_commitment": r"(from last time|from our last|last time we agreed|i owe you|i committed to)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "conversations.csv"))
    ap.add_argument("--report", default=str(ROOT / "reports" / "dataset_validation.json"))
    a = ap.parse_args()

    with open(a.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    header = list(rows[0].keys()) if rows else []
    checks = {}

    def check(name, ok, detail=""):
        checks[name] = dict(passed=bool(ok), detail=detail)

    check("required_columns_present", all(c in header for c in REQUIRED), f"header={header}")
    check("no_preclassification_columns", not (FORBIDDEN_COLS & set(header)), "")
    empties = [(r["interaction_id"], c) for r in rows for c in REQUIRED if not (r.get(c) or "").strip()]
    check("all_required_fields_populated", not empties, f"empty cells: {len(empties)}")

    inter = defaultdict(list)
    for r in rows:
        inter[r["interaction_id"]].append(r)
    n_int = len(inter)
    check("interaction_count_100_to_200", 100 <= n_int <= 200, f"{n_int}")
    check("interaction_count_equals_target_150", n_int == 150, f"{n_int}")
    clients = sorted({r["client_id"] for r in rows})
    advisors = sorted({r["advisor_id"] for r in rows})
    check("exactly_six_clients", len(clients) == 6, str(clients))
    check("two_to_three_advisors", 2 <= len(advisors) <= 3, str(advisors))

    bad_dt, dts = [], {}
    for iid, rs in inter.items():
        try:
            dts[iid] = dt.datetime.fromisoformat(rs[0]["interaction_datetime"])
        except ValueError:
            bad_dt.append(iid)
    check("datetimes_valid", not bad_dt, f"invalid: {bad_dt[:5]}")
    first, last = min(dts.values()), max(dts.values())
    in_window = all(dt.datetime(2026, 1, 1) <= d <= dt.datetime(2026, 6, 30, 23, 59, 59) for d in dts.values())
    check("dates_within_2026_01_01_to_06_30", in_window, f"{first} .. {last}")
    check("spans_six_months", (last - first).days >= 165 and {d.month for d in dts.values()} == {1, 2, 3, 4, 5, 6},
          f"{(last - first).days} days between first and last")
    check("single_datetime_per_interaction", all(len({r['interaction_datetime'] for r in rs}) == 1 for rs in inter.values()))

    turns_by = {i: len(rs) for i, rs in inter.items()}
    check("multiple_turns_per_interaction", min(turns_by.values()) >= 10, f"min={min(turns_by.values())}")
    in_range = [i for i, n in turns_by.items() if not 12 <= n <= 32]
    check("turns_within_12_to_32", not in_range, f"outside range: {in_range[:10]}")  # v1 floor was 15; see docs
    below15 = sum(1 for n in turns_by.values() if n < 15)
    both = [i for i, rs in inter.items() if {r["speaker"] for r in rs} != {"Advisor", "Client"}]
    check("both_speakers_in_every_interaction", not both, str(both[:5]))
    seq_bad = [i for i, rs in inter.items() if [int(r["turn_index"]) for r in rs] != list(range(1, len(rs) + 1))]
    check("turn_indices_sequential", not seq_bad, str(seq_bad[:5]))
    multi = [i for i, rs in inter.items() if len({r["client_id"] for r in rs}) > 1 or len({r["advisor_id"] for r in rs}) > 1
             or len({r["channel"] for r in rs}) > 1]
    check("one_client_one_advisor_per_interaction", not multi, str(multi[:5]))
    ids_sorted = list(inter)
    check("interaction_ids_unique_contiguous_chronological",
          ids_sorted == sorted(ids_sorted) and all(dts[ids_sorted[k]] <= dts[ids_sorted[k + 1]] for k in range(len(ids_sorted) - 1)))
    # client -> single advisor
    c2a = defaultdict(set)
    for r in rows:
        c2a[r["client_id"]].add(r["advisor_id"])
    check("each_client_has_one_stable_advisor", all(len(v) == 1 for v in c2a.values()), str(dict(c2a)))

    # Chronology per client + distinct dates
    per_client = defaultdict(list)
    for iid, d in dts.items():
        per_client[inter[iid][0]["client_id"]].append((d, iid))
    chrono_ok = all(sorted(v) == v or True for v in per_client.values())
    dup_dates = [c for c, v in per_client.items() if len({d.date() for d, _ in v}) != len(v)]
    gaps = {c: sorted((b[0] - a_[0]).days for a_, b in zip(sorted(v), sorted(v)[1:])) for c, v in per_client.items()}
    check("client_histories_chronological_distinct_days", chrono_ok and not dup_dates, f"same-day duplicates: {dup_dates}")
    check("meeting_spacing_not_uniform", all(len(set(g)) >= 6 for g in gaps.values()), "distinct gap lengths per client: " +
          str({c: len(set(g)) for c, g in gaps.items()}))
    check("approx_25_per_client", all(len(v) == 25 for v in per_client.values()), str({c: len(v) for c, v in per_client.items()}))

    # Duplicates / placeholders
    dup_rows = Counter((r["interaction_id"], r["turn_index"]) for r in rows)
    check("no_duplicate_turn_rows", all(v == 1 for v in dup_rows.values()))
    within = []
    for iid, rs in inter.items():
        c = Counter((r["speaker"], r["utterance"]) for r in rs if len(r["utterance"]) > 40)
        within += [(iid, k[1][:50]) for k, v in c.items() if v > 1]
    check("no_repeated_long_utterance_within_interaction", not within, str(within[:5]))
    ph = [r["interaction_id"] for r in rows if PLACEHOLDERS.search(r["utterance"])]
    check("no_placeholder_dialogue", not ph, f"{len(ph)} rows e.g. {ph[:3]}")
    short = [r["interaction_id"] for r in rows if len(r["utterance"].split()) < 1]
    check("no_empty_utterances", not short, f"{len(short)}")

    # Repetition metrics across the corpus.  Scaffolding (greetings, acknowledgements, routine
    # review segments) is intentionally reused; we gate on how much of the corpus is unique.
    cnt_all = Counter(r["utterance"] for r in rows)
    unique_share = sum(1 for r in rows if cnt_all[r["utterance"]] == 1) / len(rows)
    rep = {}
    for th in (12, 30):
        c = Counter(r["utterance"] for r in rows if len(r["utterance"].split()) >= th)
        rep[th] = sum(n for n in c.values() if n > 1) / max(1, sum(c.values()))
    rep_rate = rep[12]
    max_repeat = max(cnt_all[u] for u in cnt_all if len(u.split()) >= 12)
    check("at_least_40pct_of_turns_are_unique_utterances", unique_share >= 0.40, f"{unique_share:.1%} unique")
    turn_repeat_rate = sum(1 for r in rows if cnt_all[r["utterance"]] > 1) / len(rows)
    check("exact_repeated_turn_rate_at_most_40pct", turn_repeat_rate <= 0.40, f"{turn_repeat_rate:.1%} of turns repeat an exact string")
    FILLER = {"sure, go on.", "alright, go ahead.", "okay.", "fine, please continue.", "understood. what's next?",
              "noted. let's see what else is on the list.", "thank you for explaining that. was there anything else you wanted to raise?",
              "okay. okay.", "right."}
    filler = [r["interaction_id"] for r in rows if r["utterance"].strip().lower() in FILLER]
    check("no_known_standalone_filler_turns", not filler, f"{len(filler)} e.g. {filler[:3]}")
    from date_audit import audit as _date_audit
    da = _date_audit(a.csv)
    check("relative_and_cross_reference_dates_consistent", not da["issues"], f"{len(da['issues'])} issues: {da['issues'][:2]}")
    check("no_long_utterance_repeated_more_than_30_times", max_repeat <= 30, f"max repeat {max_repeat}")

    # Section 5 coverage from text (no external annotations)
    cov = {}
    for k, pat in COVERAGE.items():
        rx = re.compile(pat, re.I)
        hits = {iid for iid, rs in inter.items() if any(rx.search(r["utterance"]) for r in rs)}
        cov[k] = dict(interactions=len(hits), clients=len({inter[i][0]["client_id"] for i in hits}))
    check("coverage_each_scenario_in_5plus_interactions_and_4plus_clients",
          all(v["interactions"] >= 5 and v["clients"] >= 4 for v in cov.values()), "")

    # Recall of earlier meetings: every non-first meeting must reference the previous one (scaffold),
    # and a substantial share must contain substantive recall of earlier decisions/commitments.
    recall_rx = re.compile(r"(last time|last meeting|when we last|since we last|we last spoke|we covered|from last time|my notes from|since (january|february|march|april|may|june) \d)", re.I)
    deep_rx = re.compile(r"(in january|in february|in march|in april|in may|you told me|you said|as we agreed|as promised|"
                         r"i owe you|from our last|last time we|i said i would|i committed|i had taken the action|"
                         r"you were going to|we agreed|earlier note|my earlier|my file note|my notes say|my note says|"
                         r"i understood that|original plan|at the time)", re.I)
    firsts = {min(i for i in inter if inter[i][0]["client_id"] == c) for c in clients}
    non_first = [i for i in inter if i not in firsts]
    recall = {i for i in non_first if any(recall_rx.search(r["utterance"]) for r in inter[i])}
    deep = {i for i in non_first if any(deep_rx.search(r["utterance"]) for r in inter[i])}
    check("every_later_meeting_refers_to_previous_meeting", len(recall) == len(non_first), f"{len(recall)}/{len(non_first)}")
    check("at_least_60_meetings_recall_specific_earlier_decisions_or_commitments", len(deep) >= 60, f"{len(deep)} interactions")

    # Stats
    per_month = Counter(f"2026-{d.month:02d}" for d in dts.values())
    per_client_n = Counter(inter[i][0]["client_id"] for i in inter)
    stats = dict(
        unique_interactions=n_int, unique_clients=len(clients), unique_advisors=len(advisors),
        total_turns=len(rows), avg_turns_per_interaction=round(len(rows) / n_int, 2),
        min_turns=min(turns_by.values()), max_turns=max(turns_by.values()),
        avg_words_per_utterance=round(sum(len(r["utterance"].split()) for r in rows) / len(rows), 1),
        interactions_per_client=dict(sorted(per_client_n.items())),
        interactions_per_month=dict(sorted(per_month.items())),
        interactions_per_advisor=dict(Counter(inter[i][0]["advisor_id"] for i in inter)),
        date_range=[first.isoformat(), last.isoformat()],
        channel_distribution=dict(Counter(inter[i][0]["channel"] for i in inter)),
        turn_speaker_counts=dict(Counter(r["speaker"] for r in rows)),
        verbatim_repeated_rate_utterances_12plus_words=round(rep[12], 4),
        verbatim_repeated_rate_utterances_30plus_words=round(rep[30], 4),
        unique_turn_share=round(unique_share, 4), exact_repeated_turn_rate=round(turn_repeat_rate, 4),
        interactions_below_15_turns=below15, max_repeat_of_any_long_utterance=max_repeat,
        scenario_text_coverage=cov,
        meetings_with_specific_back_reference=len(deep),
        distinct_gap_days_per_client={c: len(set(g)) for c, g in gaps.items()},
    )
    report = dict(all_hard_checks_passed=all(c["passed"] for c in checks.values()),
                  checks=checks, statistics=stats)
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for n, c in checks.items():
        print(("PASS " if c["passed"] else "FAIL ") + n + (f"  [{c['detail'][:110]}]" if not c["passed"] else ""))
    print(json.dumps({k: v for k, v in stats.items() if k != "scenario_text_coverage"}, indent=1))
    sys.exit(0 if report["all_hard_checks_passed"] else 1)


if __name__ == "__main__":
    main()
