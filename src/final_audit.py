"""Final acceptance audit for the Phase 1 source dataset.

Automated parts are computed here.  Judgments (consistency results, required actions, accepted limitations) are
typed below next to the evidence they refer to, and are labelled as manual.  Evidence quotes are extracted from the
CSV so they cannot drift from the data.

Usage: python src/final_audit.py [--csv ...] [--manifest ...] [--out-json ...] [--out-md ...] [--dry-run]
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from date_audit import audit as date_audit  # noqa: E402

V1_CSV = ROOT / "backup/v1/data/conversations_v1.csv"
V1_MD5_RECORDED = "ee199516894c8111e6903ce40ea68d9e"  # recorded when the v1 backup was created
PRE_CSV = ROOT / "backup/v2_pre_final/conversations_v2.csv"
EXPECTED_COLUMNS = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index",
                    "speaker", "utterance", "meeting_context", "source_type", "consent_status"]
CORRECTED_INTERACTIONS = ["INT-009", "INT-115", "INT-119", "INT-148"]


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def load(p):
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    by = defaultdict(list)
    for r in rows:
        by[r["interaction_id"]].append(r)
    return rows, by


def meta(by):
    return {i: (rs[0]["client_id"], rs[0]["advisor_id"], rs[0]["interaction_datetime"], rs[0]["channel"],
                rs[0]["meeting_context"]) for i, rs in by.items()}


def run(cmd):
    p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout[-400:] + p.stderr[-400:]


# --------------------------------------------------------------------------------------------- fact table
# (client number, fact, first-appearance regex, later-mention regex or None, consistency result, required action)
FACTS = [
 (2, "Ethan has an existing college account of about $85k", r"about eighty-five in it", None,
  "consistent", "none", "Single mention; Ethan is 17 (profile) and applying this autumn; nothing contradicts it."),
 (2, "Walk-away price of $22M against the $24M letter of intent", r"Below twenty-two I walk", r"twenty-two instead of twenty-four",
  "consistent", "none", "Arithmetic checked: 2M price drop x ~0.6 after tax = 1.2M; 13.5M cash at closing (INT-058) -> 12.3M as stated in INT-077. Recorded as a floor 'where I am today', not a decision, and not restated after the INT-083 decision to sell; no contradiction."),
 (2, "Retention pool of up to ~$1.2M, ~$700k after tax", r"seven hundred thousand less", None,
  "consistent, tax figure unverified", "human tax plausibility check (optional)", "Tentative idea, never decided; stays tentative in the manifest. The 700k after-tax cost implies roughly a 40% tax effect, stated by the advisor as 'roughly'."),
 (2, "$55k pre-signing costs ($40k retainer + $15k advance) paid from cash", r"Fifty-five thousand out of your own pocket", None,
  "consistent", "none", "40k + 15k = 55k. Cash 220k -> 165k afterwards; no later meeting quotes a Rebecca cash balance, so nothing contradicts the new level."),
 (2, "12-month paid consulting role after closing (corrects 'salary stops at closing')", r"paid consultant for twelve months", r"salary would stop",
  "consistent", "none", "The earlier assumption is preserved in INT-130 (advisor says income stops at closing) and explicitly superseded in INT-142 (later). Correction wording attributes the error to the advisor's assumption."),
 (3, "Lucia born Tuesday 3 March; the 4 March meeting is a call from hospital", r"Lucia was born on", r"Lucia smiled|any day now",
  "consistent", "none", "INT-045 (2 March) has her at 39 weeks, 'any day now'; January said baby due in March; 5 weeks later (10 April) Lucia smiles for the first time. Token date resolves to Tuesday, March 3 (a Tuesday)."),
 (3, "Manager hinted at a four-day week (about -20% salary)", r"might let you move to four days", r"four-day week",
  "consistent", "none", "Tentative in INT-045; closed as 'not now' in INT-147; no intermediate contradiction."),
 (3, "Sofia's student loans are federal", r"Federal\. And what", None,
  "consistent", "none", "INT-019 gives $48k at 5.2% without a type; nothing contradicts federal."),
 (4, "Survivor pension is 60% of Samuel's pension (corrects 'about half')", r"sixty percent for life", r"about half",
  "consistent", "none", "Original 'about half' statement is in INT-029 (9 Feb); INT-050 (9 Mar) explicitly corrects it and keeps the original. No later mention uses 'half' for Grace."),
 (4, "First investing step on 1 June of ~$24k (one of six)", r"about twenty-four thousand dollars", r"second step on July first|On June first we bought",
  "consistent", "none", "Arithmetic: 6 x 24k = 144k = 10 points of $1.45M, i.e. 15% -> 25% as agreed in INT-097; a 20% fall of 24k = 4.8k as stated. INT-097 says only 'starting next month'; the date 1 June (a Monday) first appears in INT-110 and is confirmed by the advisor. v1 had the investing-step meetings dated before this; fixed in revision 2."),
 (4, "Annual house cost of 'fifteen thousand or more'", r"fifteen thousand or more", None,
  "partly supported", "none; accepted", "Client states property tax ~9k and insurance 2k (=11k) plus unquantified upkeep; the 15k is the advisor's estimate. Not contradicted anywhere."),
 (5, "April tax shortfall of $20-25k, covered ~22k from invoices and ~3k from cash", r"short by twenty to twenty-five", r"Twenty-two of the twenty-five",
  "consistent", "none", "Feb 25 -> Apr 15 is seven weeks as stated. INT-076 closes the loop (22k of 25k from invoices, rest from cash). Estimates paid for 2025 (35k) were his own guess (INT-016), consistent with the shortfall being on the 2025 return."),
 (5, "Estimated taxes use the safe-harbor method (110% of last year's tax)", r"hundred and ten percent of last year", r"safe.harbor",
  "consistent", "none", "Decided INT-016, applied INT-118; 110% applies because net income (~190k) exceeds the 150k threshold."),
 (5, "Remaining Vantora position about a sixth of liquid assets after three sales", r"roughly a sixth of the liquid portfolio", r"third of the liquid portfolio",
  "inconsistency found and fixed", "fixed in this pass", "v2 text said 'a fifth' and cited the hold-until-Q3 decision on 29 May, eleven days before it was made (9 June). Arithmetic: ~33% start, -30% fall, 40% trimmed => ~14-17%. Text is now step-aware: starting-size wording until the third sale, 'about a sixth' only after it."),
 (6, "Required distribution about $31k; $6k of it via the church gift", r"about thirty-one thousand", r"twenty-five thousand",
  "consistent, IRA balance never stated", "human check (optional)", "31k - 6k = 25k as stated in INT-129. No IRA balance appears in any dialogue, so 31k cannot be tied to a balance; plausible for age 74 with a ~0.8M IRA."),
 (6, "Aide five days a week at ~$6,400 a month; spending $10,100; portfolio gap $3,800", r"invoice for May was sixty-four hundred", r"thirty-eight hundred",
  "inconsistency found and fixed", "fixed in this pass", "5,200 -> 6,400 is +1,200 and 8,900 -> 10,100 as stated. But the gap was given as 4,200 (and 3,000 in INT-009) while January's own figures give 8,900 - 6,300 = 2,600, so the new gap is 10,100 - 6,300 = 3,800. INT-009 and INT-119 corrected; the January statements are untouched."),
 (6, "Remaining required amount taken monthly Jul-Dec with 12% withheld", r"Dennis said twelve percent", None,
  "weakly supported", "human check (optional)", "The 12% is attributed to the accountant Dennis, who is mentioned earlier (INT-028, INT-085), but 12% appears first in the advisor's own turn in INT-129. Not contradicted. Left as is: it is the advisor reporting an accountant's figure."),
 (6, "Maturing bond rung of about $50k", r"about fifty thousand dollars", None,
  "consistent", "none", "At the corrected 3,800 a month, one year of withdrawals is ~46k, so a ~50k rung fits. No earlier statement gives a rung size."),
 (6, "Frank's pension survivor amount unconfirmed (carried forward)", r"survivor amount is unconfirmed", r"booklet",
  "loose end found and fixed", "fixed in this pass", "INT-009 promised to return to it 'when we look at the care costs'; INT-041 and INT-119 did not. INT-119 now carries it forward as still unconfirmed with Peg's own estimate of about half used for the stress case. No answer is invented."),
 (1, "Working assumption of six months' base salary as severance", r"six months of base salary", None,
  "consistent, salary never stated", "human check (optional)", "'Well over a year' of cover depends on a base salary that is not stated in any dialogue; with 150k cash alone ~13 months, the claim holds for any plausible salary. Not contradicted."),
 (1, "Ren's 14k is the program fee, flights and housing, not extra tuition", r"fourteen thousand", r"program fee, flights and housing",
  "consistent", "none", "INT-091 (client: 'beyond normal tuition') -> INT-114 (advisor note had it as extra tuition; client corrects). Original statements preserved."),
]

# Half-year review recap claims: (review id, client number, claim, evidence regex)
REVIEWS = {
 "INT-145": (6, [("35% equities in January", r"thirty-five percent"), ("asked to cut to 25% after Frank's fall", r"Twenty-five percent\. Please do it|Maybe down to twenty-five"),
                 ("moved to 30% after the inflation worry", r"thirty percent, not twenty-five"), ("declined Brian's $80k loan", r"not lending him eighty thousand"),
                 ("decided against Maplewood for now", r"decided against it"), ("power of attorney document still open", r"power of attorney")],
             "p_poa"),
 "INT-146": (4, [("annuity declined for access reasons", r"decided not to buy the annuity"), ("rolled Samuel's account into her own", r"roll it over into my own account"),
                 ("kept the mortgage after first wanting it paid off", r"rather keep the mortgage"), ("gift of up to 15k for Tobi conditional on a plan", r"up to fifteen thousand, as a gift"),
                 ("working until 63", r"work until 63"), ("survivor benefit postponed", r"note it as postponed"), ("six equal investing steps to ~25% stocks", r"six equal steps")],
             None),
 "INT-147": (3, [("90% equities, 'aggressive' in January", r"Aggressive\. I have thirty years"), ("March swings were hard", r"swings in March were hard"),
                 ("75% for retirement decided in May", r"seventy-five percent in equities rather than ninety"), ("house purchase had been only a possibility", r"I was only saying I might"),
                 ("search postponed until the autumn", r"no house search until the autumn"), ("will/guardianship open", r"guardianship"), ("529 deferred", r"college savings account for Lucia")],
             "s_will"),
 "INT-148": (5, [("wanted to concentrate on Vantora", r"I believe in Vantora"), ("nine-month reserve", r"build the reserve to nine months"),
                 ("declined the restaurant", r"decided not to invest in the restaurant"), ("sold half of the secondary shares", r"I.ll sell half"),
                 ("Vantora trimmed 40% over three steps", r"Sell forty percent over three steps"), ("third sale completed", r"final Vantora sale went through"),
                 ("sold the crypto", r"I want to sell it"), ("disability quotes pending", r"The broker told me three weeks|broker, but he says it will take three weeks")],
             "l_ins"),
 "INT-149": (1, [("planned 65 in January", r"plan has always been 65"), ("exploring 60 in February", r"leaning toward 60"), ("March correction: only exploring", r"I was only exploring the possibility"),
                 ("63 decided in May with Mariko", r"We.ve decided: 63"), ("'sit tight' at 75% in January", r"I.d sit tight"), ("60% decided in April", r"go to sixty percent equities"),
                 ("Mariko's SS statement open", r"Social Security statement"), ("estate/beneficiary forms open", r"beneficiary forms are in a safe-deposit box")],
             "k_estate,k_spouse_ss"),
 "INT-150": (2, [("curious but undecided in January", r"I.m curious, nothing more"), ("Lakeshore letter in March", r"Twenty-four million"),
                 ("corrected the 'accepted' note", r"I haven.t signed the letter of intent"), ("declined employee ownership for certain cash", r"Ten years is a deal-breaker"),
                 ("wavered about waiting in April", r"not sure about selling now"), ("decided to sell with a 10% rollover", r"I.m going to sell to Lakeshore"),
                 ("investment policy drafted for signature", r"I.ve drafted it")],
             "r_spa,r_estate"),
}
# Threads that are unresolved at 30 June but NOT carried into the client's half-year review (typed judgment).
NOT_CARRIED = {
 "INT-145": ["Frank's pension survivor amount (unconfirmed since INT-009; carried forward in INT-119 only)"],
 "INT-146": ["whether Folake's share should be held in trust (INT-124)", "downsizing the house, deferred to at least November (INT-135)",
             "second investing step on 1 July with a conditional advisor call (INT-128; beyond the window)"],
 "INT-147": ["childcare gap covered by trimming brokerage contributions (decided, not open)", "Mateo's job-offer details (ledger item s_offer, moot)"],
 "INT-148": ["annualized-installment tax method, reconsidered for Q3 (INT-118)", "any further Vantora reduction deferred to after Q3 earnings (carried in the snapshot line, not in the recap)"],
 "INT-149": ["Roth conversion deferred to the second-half review (INT-141 neighbourhood)"],
 "INT-150": ["Ethan's college funding gap (INT-038)", "retention pool for key staff (INT-121)", "22M walk-away floor (INT-077)"],
}


def first_match(rows, by, client, pat, before=None):
    rx = re.compile(pat, re.I)
    for iid in sorted(by):
        if by[iid][0]["client_id"][-1] != str(client):
            continue
        if before and iid >= before:
            break
        for r in by[iid]:
            m = rx.search(r["utterance"])
            if m:
                s = max(0, m.start() - 110)
                e = min(len(r["utterance"]), m.end() + 110)
                return iid, by[iid][0]["interaction_datetime"][:10], r["speaker"], r["utterance"][s:e].strip()
    return None


def all_matches(by, client, pat):
    rx = re.compile(pat, re.I)
    return sorted({iid for iid in by if by[iid][0]["client_id"][-1] == str(client)
                   and any(rx.search(r["utterance"]) for r in by[iid])})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data/conversations.csv"))
    ap.add_argument("--manifest", default=str(ROOT / "reports/generation_manifest.json"))
    ap.add_argument("--out-json", default=str(ROOT / "reports/final_acceptance_audit.json"))
    ap.add_argument("--out-md", default=str(ROOT / "docs/final_dataset_acceptance.md"))
    ap.add_argument("--dry-run", action="store_true", help="audit a staged CSV; skip the report/compare files that use fixed paths")
    a = ap.parse_args()

    rows, by = load(a.csv)
    pre_rows, pre_by = load(PRE_CSV)
    v1_rows, v1_by = load(V1_CSV)
    man = json.load(open(a.manifest, encoding="utf-8"))
    checks = []

    def chk(name, ok, detail=""):
        checks.append(dict(name=name, passed=bool(ok), detail=detail))

    # ---- structure
    per_client = Counter(rs[0]["client_id"] for rs in by.values())
    chk("150_interactions", len(by) == 150, str(len(by)))
    chk("six_clients_25_each", len(per_client) == 6 and set(per_client.values()) == {25}, str(dict(per_client)))
    chk("three_advisors", len({r["advisor_id"] for r in rows}) == 3)
    chk("eleven_columns_unchanged", list(rows[0].keys()) == EXPECTED_COLUMNS, str(list(rows[0].keys())))
    chk("ids_clients_advisors_timestamps_channels_identical_to_v1",
        {k: v[:4] for k, v in meta(by).items()} == {k: v[:4] for k, v in meta(v1_by).items()})
    chk("meeting_context_labels_identical_to_pre_final_v2", {k: v[4] for k, v in meta(by).items()} == {k: v[4] for k, v in meta(pre_by).items()})
    chk("turn_indices_sequential", all([int(r["turn_index"]) for r in rs] == list(range(1, len(rs) + 1)) for rs in by.values()))
    # ---- no annotation / manifest leakage
    leak_terms = set()
    for cid, led in man["action_ledger"].items():
        leak_terms.update(led.keys())
    leak_terms.update(c["id"] for c in man.get("commitments_beyond_observation_window", []))
    leak_terms.update({"scenario_tags", "authored_outcomes", "action_ledger", "memory_type", "salience", "supersedes", "episodic", "semantic_memory"})
    hits = [(r["interaction_id"], t) for r in rows for t in leak_terms if re.search(r"\b" + re.escape(t) + r"\b", r["utterance"]) or t == r["meeting_context"]]
    chk("no_manifest_ids_or_annotation_terms_in_csv", not hits, str(hits[:5]))
    chk("source_type_and_consent_constant", {(r["source_type"], r["consent_status"]) for r in rows} == {("synthetic_meeting", "synthetic")})
    # ---- diff against the pre-final v2 snapshot
    changed = sorted(i for i in pre_by if [x["utterance"] for x in pre_by[i]] != [x["utterance"] for x in by[i]])
    chk("only_the_four_audited_interactions_changed_text", changed == CORRECTED_INTERACTIONS, str(changed))
    chk("unchanged_interactions_byte_identical_rows",
        all(pre_by[i] == by[i] for i in pre_by if i not in CORRECTED_INTERACTIONS))
    # ---- ledger
    led = man["action_ledger"]
    opened = sum(len(v) for v in led.values())
    done = sum(1 for v in led.values() for x in v.values() if x["status"] == "done")
    v1led = json.load(open(ROOT / "backup/v1/reports/generation_manifest.json", encoding="utf-8"))["action_ledger"]
    chk("ledger_48_opened_40_completed_8_outstanding", (opened, done, opened - done) == (48, 40, 8), f"{opened}/{done}/{opened - done}")
    chk("ledger_identical_to_v1", led == v1led)
    bw = man.get("commitments_beyond_observation_window", [])
    chk("july_1_commitment_recorded_outside_ledger_counts",
        len(bw) == 1 and bw[0]["status"] == "outstanding_beyond_observation_window" and bw[0]["made_in"] == "INT-128"
        and bw[0]["counted_in_action_ledger"] is False and "g_jul1_call" not in sum((list(v) for v in led.values()), []),
        json.dumps(bw)[:300])
    # ---- date audit
    da = date_audit(a.csv)
    chk("relative_date_and_cross_reference_audit_clean", not da["issues"], str(da["issues"][:2]))
    # ---- targeted text assertions for this pass
    txt = defaultdict(list)
    for r in rows:
        txt[r["interaction_id"]].append(r["utterance"])
    t148 = " ".join(txt["INT-148"])
    chk("INT-148_has_explicit_pending_status", "record it as pending" in t148 and "quotes aren't back yet" in t148)
    chk("INT-148_closing_no_longer_says_nothing_requires_action", "requires action" not in t148 and "check that nothing is drifting" not in t148)
    chk("INT-148_logs_follow_up_and_unconfirmed_intention",
        [o[0] for i in man["interactions"] if i["interaction_id"] == "INT-148" for o in i["authored_outcomes"]] == ["c_action", "tentative"])
    chk("INT-148_broker_timing_consistent_with_18_June", "Thursday, June 18" in t148 and any("2026-06-18" in r["interaction_datetime"] and r["client_id"] == "CLIENT-005" for r in rows))
    chk("gap_arithmetic_consistent_CLIENT-006", "thirty-eight hundred" in " ".join(txt["INT-119"]) and "four thousand two hundred" not in " ".join(txt["INT-119"])
        and "twenty-six hundred" in " ".join(txt["INT-009"]) and "three thousand from the portfolio" not in " ".join(txt["INT-009"]))
    chk("vantora_text_not_ahead_of_decision",
        "third-quarter earnings" not in " ".join(txt["INT-115"]) and "a fifth" not in " ".join(r["utterance"] for r in rows if r["client_id"] == "CLIENT-005"))
    # ---- determinism
    with tempfile.TemporaryDirectory() as td:
        outs = []
        for k in (1, 2):
            c, m = Path(td) / f"a{k}.csv", Path(td) / f"a{k}.json"
            rc, o = run(["src/generate_dataset.py", "--out", str(c), "--manifest", str(m)])
            outs.append((rc, md5(c) if c.exists() else None, md5(m) if m.exists() else None))
    chk("generator_deterministic_two_runs_identical", outs[0] == outs[1] and outs[0][0] == 0, str(outs))
    chk("generated_output_equals_delivered_csv_and_manifest", outs[0][1] == md5(a.csv) and outs[0][2] == md5(a.manifest))
    # ---- validator
    with tempfile.TemporaryDirectory() as td:
        rc, o = run(["src/validate_dataset.py", "--csv", a.csv, "--report", str(Path(td) / "v.json")])
        vr = json.load(open(Path(td) / "v.json", encoding="utf-8"))
    chk("validator_all_33_checks_pass", rc == 0 and vr["all_hard_checks_passed"] and len(vr["checks"]) == 33,
        f"exit {rc}, {len(vr['checks'])} checks, failed={[k for k, c in vr['checks'].items() if not c['passed']]}")
    # ---- v1 backup
    import os
    mt = [os.path.getmtime(os.path.join(rt, f)) for rt, _, fs in os.walk(ROOT / "backup/v1") for f in fs]
    chk("v1_backup_csv_md5_matches_value_recorded_at_backup_time", md5(V1_CSV) == V1_MD5_RECORDED, md5(V1_CSV))
    chk("v1_backup_files_share_one_copy_timestamp_no_later_edits", max(mt) - min(mt) < 5, f"spread {max(mt) - min(mt):.2f}s over {len(mt)} files")
    chk("v1_backup_ledger_48_40_8", (sum(len(v) for v in v1led.values()), sum(1 for v in v1led.values() for x in v.values() if x["status"] == "done")) == (48, 40))

    # ---- comparison report (only on the real paths)
    comparison = None
    if not a.dry_run:
        rc, o = run(["src/compare_quality.py"])
        d = json.load(open(ROOT / "reports/dataset_quality_comparison.json", encoding="utf-8"))["automated"]
        comparison = dict(exit_code=rc, v1_repeat_pct=d["v1"]["pct_turns_with_repeated_exact_utterance"], v2_repeat_pct=d["v2"]["pct_turns_with_repeated_exact_utterance"],
                          v2_turns=d["v2"]["total_turns"], date_audit_issues=d["v2"]["date_consistency_audit"]["issues"],
                          ledger_identical_to_v1=d["preservation"]["action_ledger_identical_to_v1"],
                          ids_dates_channels_identical=d["preservation"]["interaction_ids_clients_advisors_datetimes_channels_identical"],
                          script_lines_preserved=d["preservation"]["hand_written_script_lines_preserved"]["pct"])
        chk("quality_comparison_runs_and_preservation_flags_true", rc == 0 and comparison["ledger_identical_to_v1"] and comparison["ids_dates_channels_identical"])

    # ---- new-fact trace
    facts_out = []
    for client, fact, pat, later, result, action, note in FACTS:
        fm = first_match(rows, by, client, pat)
        lm = [i for i in all_matches(by, client, later)] if later else []
        facts_out.append(dict(client=f"CLIENT-00{client}", fact=fact,
                              first_interaction=fm[0] if fm else None, date=fm[1] if fm else None,
                              speaker=fm[2] if fm else None, supporting_dialogue=fm[3] if fm else None,
                              related_interactions=lm, consistency_result=result, required_action=action, notes=note))
    chk("every_new_fact_located_in_the_csv", all(f["first_interaction"] for f in facts_out),
        str([f["fact"] for f in facts_out if not f["first_interaction"]]))

    # ---- half-year reviews
    rev_out = {}
    for rid, (client, claims, ledger_ids) in REVIEWS.items():
        rdate = by[rid][0]["interaction_datetime"][:10]
        cl = []
        for claim, pat in claims:
            fm = first_match(rows, by, client, pat)
            ok = bool(fm) and fm[0] < rid
            # a claim is evidenced if the phrase occurs in an interaction of that client dated before (or in) the review
            cl.append(dict(claim=claim, evidence_interaction=fm[0] if fm else None, evidence_date=fm[1] if fm else None, evidenced=ok))
        open_items = []
        for lid in (ledger_ids or "").split(","):
            if lid:
                x = led[by[rid][0]["client_id"]][lid]
                open_items.append(dict(ledger_id=lid, status=x["status"], description=x["description"]))
        text = " ".join(txt[rid])
        rev_out[rid] = dict(client=by[rid][0]["client_id"], date=rdate, recap_claims=cl,
                            all_recap_claims_evidenced=all(c["evidenced"] for c in cl),
                            ledger_items_referenced=open_items, ledger_items_still_open=all(o["status"] != "done" for o in open_items),
                            unresolved_threads_not_carried_into_review=NOT_CARRIED[rid],
                            states_explicit_open_items=bool(re.search(r"Open items|open item|still weighing|hold off|pending", text, re.I)))
    chk("all_half_year_recap_claims_traceable_to_earlier_dialogue", all(v["all_recap_claims_evidenced"] for v in rev_out.values()),
        str({k: [c["claim"] for c in v["recap_claims"] if not c["evidenced"]] for k, v in rev_out.items() if not v["all_recap_claims_evidenced"]}))
    chk("half_year_reviews_reference_ledger_items_that_are_open", all(v["ledger_items_still_open"] for v in rev_out.values()))

    # ---- report
    ok_all = all(c["passed"] for c in checks)
    report = dict(
        generated_by="src/final_audit.py", csv=str(Path(a.csv).name), csv_md5=md5(a.csv), manifest_md5=md5(a.manifest),
        v1_csv_md5=md5(V1_CSV), pre_final_v2_csv_md5=md5(PRE_CSV), dry_run=a.dry_run,
        verdict=dict(fit_to_freeze=ok_all,
                     statement=("Fit to freeze as the final Phase 1 source dataset. All automated checks pass, 4 genuine inconsistencies found in this "
                                "pass were corrected with a deterministic regeneration that changed only 4 interactions, and no confirmed "
                                "inconsistency remains open. The items listed under human_review_required are recommended confirmations, not blockers."
                                if ok_all else "NOT fit to freeze: see failed automated checks.")),
        automated_checks=checks,
        corrections_made_in_this_pass=[
            dict(interaction="INT-009", problem="portfolio withdrawal stated as 'about three thousand' while the 5 January figures (8,900 spending - 6,300 income) give 2,600", fix="'about twenty-six hundred'"),
            dict(interaction="INT-119", problem="portfolio-funded gap stated as 4,200; 10,100 - 6,300 = 3,800. The INT-009 promise to revisit Frank's pension survivor amount 'when we look at the care costs' had not been kept", fix="3,800 (and 2,600 as the prior gap); the unconfirmed survivor amount is carried forward without inventing an answer"),
            dict(interaction="INT-115", problem="Vantora holding text said 'you've chosen to hold the rest until after the third-quarter earnings' on 29 May, but that decision is made on 9 June (INT-127)", fix="step-aware wording: starting-size description until the third sale"),
            dict(interaction="INT-148", problem="disability-insurance follow-up had no status: the closing said 'nothing we saw requires action' and logged no outcome; the dialogue also implied the broker was overdue although 'three weeks' was said on 18 June", fix="explicit pending status ('quotes not back, nothing bought until seen'), logged as one client action and one unconfirmed intention; timing made consistent. No decision was manufactured"),
        ],
        new_fact_trace=facts_out,
        INT_148=dict(
            transcript_read_in_full=True,
            status="unresolved / follow-up pending",
            what_is_pending="broker quotes for disability cover; Lucas wants the cover but has bought nothing and decided nothing",
            decision_made=False,
            advisor_commitment_in_dialogue=False,
            advisor_commitment_note="The advisor recommends checking in with the broker and records the item as pending; no advisor task is promised.",
            client_action_logged="check in with your broker so he has what he needs for the disability insurance quotes",
            ledger_item=dict(id="CLIENT-005:l_ins", **led["CLIENT-005"]["l_ins"]),
            ledger_representation=("Represented as the open client item l_ins (2 deferrals). Its description is the original request (send policy summaries); "
                                   "the dialogue evolved to broker quotes. The drift is documented in the manifest's ledger_annotations, and counts are unchanged."),
        ),
        july_1_commitment=dict(
            commitment=bw[0] if bw else None,
            representation=("Held in the manifest's commitments_beyond_observation_window, outside the 48/40/8 in-window ledger, with status "
                            "'outstanding_beyond_observation_window'. No meeting was added or extended."),
            dialogue_evidence=first_match(rows, by, 4, r"I.ll call you before I place the second")[:2] if first_match(rows, by, 4, r"I.ll call you before I place the second") else None,
        ),
        half_year_reviews=rev_out,
        comparison_summary=comparison,
        manual_checks_performed=[
            "Read INT-148 and INT-145 in full before and after the correction; read INT-009, INT-115, INT-119, INT-148 in their revised form",
            "Read the earlier and later mentions of every new fact (greps over the CSV by client) and compared them with the client profile and storyline",
            "Recomputed the arithmetic behind each stated figure (see notes in the fact table)",
            "Compared each half-year recap sentence with the earlier dialogue it summarises (automated phrase search plus reading)",
            "Checked each outstanding ledger item against the last dialogue about it",
            "Confirmed that the corrected text changed no other interaction by diffing the CSV against the pre-final snapshot",
        ],
        human_review_required=[
            "Tax plausibility of the retention-pool after-tax cost (INT-121) and of the 'safe harbor' explanation (INT-016); advisor figures are stated as approximate",
            "Whether a 31k required distribution (INT-085) and a ~50k bond rung (INT-137) are acceptable without a stated IRA balance or ladder schedule",
            "The 12% withholding attributed to the accountant (INT-129) first appears in the advisor's own turn",
            "Whether 'a sixth' (INT-148) is acceptable as an approximation of the remaining Vantora position",
            "Spot-check that the rewritten meetings still read as natural speech (this audit does not assess realism)",
            "Independent confirmation of this audit: it was produced by the same assistant that wrote the revision",
        ],
        confirmed_inconsistencies=dict(
            fixed_in_this_pass=["INT-009/INT-119 gap arithmetic", "INT-115/INT-148 premature Vantora decision and share figure", "INT-148 missing pending status and closing contradiction", "INT-119 unkept advisor promise to revisit Frank's pension survivor amount"],
            still_open=[],
            ledger_note="CLIENT-003:s_offer is counted as outstanding but the dialogue made it moot; kept to preserve 48/40/8 and annotated in the manifest.",
        ),
        accepted_limitations=[
            "Template-derived portfolio snapshot lines state allocations from generator state; for CLIENT-004 (15% then 17%) and CLIENT-005 (about 90%, 'matches your current target') the target was never stated in dialogue",
            "The six half-year reviews do not list every unresolved thread (see unresolved_threads_not_carried_into_review per review); the manifest holds them",
            "Priya's conditional 1 July call falls after 30 June and is outside the ledger by design",
            "Advisor 'I'll call/arrange' statements logged as a_action in closings are not all in the ledger (the ledger tracks only structured items; v1 behaviour)",
            "Meetings are shorter than v1 (mean 14.7 turns); routine segments remain templated (28.6% exact-repeat turns); voices are least differentiated in the preserved v1 scripts",
            "'In January you said no large irreversible decisions for twelve months' (INT-135) slightly overstates the advisor's 'six to twelve months' that Grace agreed to in INT-005",
            "The automated 'no substantive new information' proxy is not discriminating (see comparison report)",
        ],
        reproduction=["python src/generate_dataset.py", "python src/validate_dataset.py", "python src/compare_quality.py", "python src/final_audit.py"],
    )
    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(a.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_md).write_text(render_md(report), encoding="utf-8")
    fails = [c for c in checks if not c["passed"]]
    print(f"checks: {len(checks)} passed: {len(checks) - len(fails)}  verdict fit_to_freeze={ok_all}")
    for c in fails:
        print("FAIL", c["name"], c["detail"][:200])
    sys.exit(0 if ok_all else 1)


def render_md(R):
    L = []
    w = L.append
    w("# Final dataset acceptance (Phase 1 source conversations)\n")
    w(f"**Verdict: {'FIT TO FREEZE' if R['verdict']['fit_to_freeze'] else 'NOT FIT TO FREEZE'}.** {R['verdict']['statement']}\n")
    w(f"CSV md5 `{R['csv_md5']}` (v1 backup `{R['v1_csv_md5']}`, pre-final v2 snapshot `{R['pre_final_v2_csv_md5']}`). Machine-readable version: `reports/final_acceptance_audit.json`.\n")
    w("## 1. What this pass did\n")
    w("A targeted QA pass, not a rewrite. Every new fact from revision 2 was traced, the six half-year reviews and INT-148 were read, and the audit found "
      "four genuine inconsistencies. They were corrected in the generator source and the CSV regenerated deterministically. **Only 4 of 150 interactions changed text** "
      "(INT-009, INT-115, INT-119, INT-148); every other row is byte-identical to the pre-final snapshot, and IDs, timestamps, channels, advisors and `meeting_context` labels are unchanged.\n")
    w("| Interaction | Problem found | Fix |\n|---|---|---|")
    for c in R["corrections_made_in_this_pass"]:
        w(f"| {c['interaction']} | {c['problem']} | {c['fix']} |")
    w("")
    w("## 2. Automated checks\n")
    w("| Check | Result | Detail |\n|---|---|---|")
    for c in R["automated_checks"]:
        w(f"| {c['name']} | {'PASS' if c['passed'] else '**FAIL**'} | {str(c['detail'])[:120].replace('|', '/')} |")
    w("")
    w("## 3. New facts introduced by revision 2\n")
    w("Evidence quotes are extracted from the CSV. The consistency result and action are manual judgments.\n")
    w("| Client | Fact | First interaction | Supporting dialogue | Consistency result | Required action |\n|---|---|---|---|---|---|")
    for f in R["new_fact_trace"]:
        q = (f["supporting_dialogue"] or "NOT FOUND").replace("|", "/").replace("\n", " ")
        w(f"| {f['client']} | {f['fact']} | {f['first_interaction']} ({f['date']}, {f['speaker']}) | \"...{q}...\" | {f['consistency_result']} | {f['required_action']} |")
    w("\nNotes per fact (arithmetic and cross-checks):\n")
    for f in R["new_fact_trace"]:
        w(f"- **{f['client']} - {f['fact']}:** {f['notes']}" + (f" Related interactions: {', '.join(f['related_interactions'])}." if f["related_interactions"] else ""))
    w("")
    w("## 4. INT-148\n")
    i = R["INT_148"]
    w(f"- Status: **{i['status']}**. Pending: {i['what_is_pending']}. Decision made: **{i['decision_made']}**. Advisor commitment in the dialogue: **{i['advisor_commitment_in_dialogue']}** ({i['advisor_commitment_note']})")
    w(f"- Client action logged: {i['client_action_logged']}.")
    w(f"- Ledger: {i['ledger_representation']}\n")
    w("## 5. The 1 July commitment\n")
    j = R["july_1_commitment"]
    w(f"- {j['commitment']['description']} (made in {j['commitment']['made_in']}, due {j['commitment']['due']}, {j['commitment']['condition']}).")
    w(f"- {j['representation']}\n")
    w("## 6. Half-year reviews (INT-145 to INT-150)\n")
    w("| Review | Client | Recap claims traced to earlier dialogue | Open ledger items referenced | Unresolved threads not carried into the review |\n|---|---|---|---|---|")
    for rid, v in R["half_year_reviews"].items():
        oi = ", ".join(x["ledger_id"] for x in v["ledger_items_referenced"]) or "none (client has no outstanding ledger item or none named)"
        nc = "; ".join(v["unresolved_threads_not_carried_into_review"])
        w(f"| {rid} | {v['client']} | {sum(c['evidenced'] for c in v['recap_claims'])}/{len(v['recap_claims'])} | {oi} | {nc} |")
    w("\nNo review was rewritten to force a decision. Each recap sentence is evidenced by earlier dialogue; no historical preference is stated as current without evidence, apart from the template-derived snapshot caveat under accepted limitations.\n")
    w("## 7. Manual checks performed\n")
    for m in R["manual_checks_performed"]:
        w(f"- {m}")
    w("\n## 8. Checks that still require a human reviewer\n")
    for m in R["human_review_required"]:
        w(f"- {m}")
    w("\n## 9. Confirmed inconsistencies\n")
    for m in R["confirmed_inconsistencies"]["fixed_in_this_pass"]:
        w(f"- Fixed: {m}")
    w(f"- Still open: {'none' if not R['confirmed_inconsistencies']['still_open'] else R['confirmed_inconsistencies']['still_open']}")
    w(f"- Ledger note: {R['confirmed_inconsistencies']['ledger_note']}")
    w("\n## 10. Accepted limitations\n")
    for m in R["accepted_limitations"]:
        w(f"- {m}")
    w("\n## 11. Reproduce\n")
    w("```bash\n" + "\n".join(R["reproduction"]) + "\n```")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
