"""P1.5 - evaluation and optimization.  These tests pin the MEASURED results of harness/evaluation/p15.py (claim corpus, isolation, temporal integrity, cache, compression, seven
end-to-end scenarios) and the three narrow fixes that the measurements justified.  Deterministic: scripted models, real CSV excerpts, in-memory SQLite, no network."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory import support  # noqa: E402
from harness import compression  # noqa: E402
from harness.evaluation import p15, p15_corpus  # noqa: E402
from harness.evaluation.p15_corpus import CATEGORIES, CORPUS, N, V  # noqa: E402


@pytest.fixture(scope="module")
def claims():
    return p15.evaluate_claims()


@pytest.fixture(scope="module")
def isolation():
    return p15.evaluate_isolation()


@pytest.fixture(scope="module")
def temporal():
    return p15.evaluate_temporal()


@pytest.fixture(scope="module")
def cache():
    return p15.evaluate_cache()


@pytest.fixture(scope="module")
def comp():
    return p15.evaluate_compression()


@pytest.fixture(scope="module")
def scenarios():
    return p15.run_scenarios()


# ===================================================================================================== corpus
def test_the_corpus_is_balanced_labelled_by_hand_and_independent_of_the_systems_under_test():
    assert len({c.id for c in CORPUS}) == len(CORPUS) >= 70
    assert {c.category for c in CORPUS} == set(CATEGORIES) and all(sum(c.category == k for c in CORPUS) >= 8 for k in CATEGORIES)
    assert sum(c.expected == V for c in CORPUS) >= 20 and sum(c.expected == N for c in CORPUS) >= 40            # positive AND negative AND adversarial
    assert len({c.client for c in CORPUS}) == 6 and max(sum(c.client == k for c in CORPUS) for k in {c.client for c in CORPUS}) <= 0.6 * len(CORPUS)      # not one client
    import ast
    tree = ast.parse(Path(p15_corpus.__file__).read_text(encoding="utf-8"))
    imported = {n.module or a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert imported <= {"__future__", "dataclasses"}, imported                                                         # the labels import no verifier, matcher or store: they are literals
    for c in CORPUS:
        assert c.why and c.expected_security and c.expected_report and c.expected in (V, N)


def test_the_shipped_matcher_flags_restore_after_the_before_measurement():
    before = dict(support.FLAGS)
    with p15.shipped_p142():
        assert support.FLAGS == {"modal_parity": False, "magnitude_units": False, "structure": False}
    assert support.FLAGS == before == {"modal_parity": True, "magnitude_units": True, "structure": True}


# ===================================================================================================== claim verification metrics
def test_the_memory_matcher_has_no_false_positive_and_is_never_worse_than_shipped_in_any_category(claims):
    new, old, base = claims["memory_current"], claims["memory_matcher_rules_off"], claims["memory_p141_baseline"]
    assert new["overall"]["fp"] == 0 and new["overall"]["false_positive_rate"] == 0.0 and new["overall"]["precision"] == 1.0
    assert old["overall"]["fp"] == 6 and base["overall"]["fp"] == 30 and claims["none (no verification)"]["overall"]["fp"] == 43      # the documented before / before-before / no-verification numbers
    assert new["overall"]["fn"] <= old["overall"]["fn"] == 8                                                          # 7 when P1.5 shipped; P1.4.3 adds f05 (a REJECTED value is no longer a current value) to BOTH runs because the lexicon is shared; recorded, not hidden
    for cat in CATEGORIES:                                                                                            # a high aggregate can hide a category: check each one
        assert new["by_category"][cat]["fp"] <= old["by_category"][cat]["fp"] and new["by_category"][cat]["fn"] <= old["by_category"][cat]["fn"], cat
    assert new["overall"]["contradiction_detection_rate"] == 1.0 and new["overall"]["ambiguity_detection_rate"] == 1.0


def test_the_six_false_positives_of_shipped_p142_are_each_fixed_and_stay_fixed(claims):
    now = {r["id"]: r["actual"] for r in claims["memory_current"]["rows"]}
    was = {r["id"]: r["actual"] for r in claims["memory_matcher_rules_off"]["rows"]}
    for cid in ("c06", "e02", "e09", "f01", "f02", "f10"):
        assert was[cid] == V and now[cid] == N, cid
    assert all(now[c.id] == N for c in CORPUS if c.category == "wrong_client" and c.expected == N)                    # foreign / ownerless evidence is never accepted


def test_the_misses_are_the_paraphrases_and_restatements_a_deterministic_matcher_cannot_establish(claims):
    missed = {r["id"] for r in claims["memory_current"]["rows"] if not r["correct"]}
    assert missed == {"d04", "d09", "d11", "d13", "e05", "e08", "f05", "f12"}                                              # reported, not hidden; none of them is loosened to look better
    assert all(c.expected == V for c in CORPUS if c.id in missed)


def test_the_report_layer_verifier_is_measured_on_the_same_labels(claims):
    o, before = claims["report_current"]["overall"], claims["report_before_p151 (clause check off)"]["overall"]
    assert before["fp"] == 6                                                                                          # the P1.5 audit finding H1 on the corpus: reproduced with the clause check switched off
    assert o["fp"] == 0 and o["fn"] <= 10                                                                              # guard against regression; the 6 FPs are recorded as P1.1 findings, not hidden
    assert claims["report_current"]["by_category"]["wrong_client"]["fp"] == 0 and claims["report_current"]["by_category"]["negation"]["fp"] == 0


@pytest.mark.parametrize("claim,evidence,value,verdict", [
    ("Rebecca's mortgage balance is 63 lakh rupees.", "Rebecca's mortgage balance is 63 crore rupees.", "63 lakh", False),            # a lakh is not a crore
    ("Peg's fund is worth $220 thousand.", "Peg's fund is worth $220 million.", "$220 thousand", False),
    ("Peg's fund is worth $220 million.", "Peg's fund is worth $220 million.", "$220 million", True),
    ("Kenji's portfolio is worth 2 million dollars.", "Kenji's portfolio was worth 2 million dollars in 2019.", "2 million dollars", False),     # 'was' is not 'is'
    ("Kenji's portfolio was worth 2 million dollars in 2019.", "Kenji's portfolio was worth 2 million dollars in 2019.", "2 million dollars", True),
    ("Kenji has retired at 63.", "Kenji plans to retire at 63.", "63", False),                                                      # an intention is not a completed fact
    ("Kenji will retire at 63.", "Kenji wants to retire at 63.", "63", False),
    ("Kenji will retire at 63.", "Kenji proposes to retire at 63, subject to approval.", "63", False),
    ("Kenji hopes to retire at 63.", "Kenji will retire at 63.", "63", False),                                                      # nor may a decision be softened
    ("Kenji plans to retire at 63.", "Kenji plans to retire at 63.", "63", True),
])
def test_unit_magnitude_tense_and_intent_rules(claim, evidence, value, verdict):
    assert support.claim_supported(claim, value, [evidence], verbatim=False, context="retirement age")[0] is verdict


def test_the_new_rules_do_not_disturb_a_sentence_where_the_marker_belongs_to_another_number():
    k2 = "You've decided to retire at 63, replacing the earlier plan of 65, because the role changed and Mariko wants to teach until 62."
    assert support.claim_supported("You've decided to retire at 63", "63", [k2], verbatim=False, context="retirement age")[0]       # 'wants' belongs to 62
    assert support.claim_supported(k2, "63", [k2], verbatim=True, context="retirement age")[0]
    assert not support.claim_supported("Mariko will teach until 62", "62", [k2], verbatim=False, context="retirement age")[0]       # 'wants' -> 'will': firmed up


# ===================================================================================================== client isolation
def test_zero_successful_unauthorized_cross_client_access(isolation):
    ev = isolation["cross_client_evidence_acceptance"]
    assert ev["attempts"] == 30 and ev["clients_covered"] == 6 and ev["accepted"] == 0
    assert isolation["cross_client_retrieval_leakage"]["items_checked"] > 5 and isolation["cross_client_retrieval_leakage"]["leaked"] == 0
    assert isolation["unauthorized_memory_access"]["attempts"] > 5 and isolation["unauthorized_memory_access"]["succeeded"] == 0
    assert isolation["unauthorized_conflict_access"]["attempts"] > 3 and isolation["unauthorized_conflict_access"]["succeeded"] == 0
    cc = isolation["cross_client_cache_leakage"]
    assert cc["served_from_cache_or_leaked"] == 0 and cc["unauthorized_requests_denied"] == cc["unauthorized_requests"] == 2
    ap = isolation["unauthorized_approval_release"]
    assert ap["succeeded"] == 0 and ap["attempts"] == 2 and ap["requester_release_ok"] is True


# ===================================================================================================== temporal integrity
def test_every_temporal_integrity_check_passes_and_each_group_is_reported(temporal):
    assert set(temporal["summary"]) == {"current_value_selection", "historical_value_misclassification", "supersession_correctness", "invalid_chronology_rejection",
                                        "human_correction_preservation", "conflict_visibility"}
    failed = [(g, c["name"], c["detail"]) for g, cs in temporal["checks"].items() for c in cs if not c["passed"]]
    assert not failed, failed


# ===================================================================================================== cache
def test_every_cache_case_passes(cache):
    failed = {k: v for k, v in cache.items() if isinstance(v, dict) and "passed" in v and not v["passed"]}
    assert not failed, failed
    assert {str(k) for k in cache if k != "workload"} >= {str(i) for i in range(1, 13)} | {"approval_vs_conflict"}
    assert cache["workload"]["hit_rate"] == 0.4 and cache["workload"]["hits"] == 4


def test_regression_a_recorded_or_resolved_conflict_changes_the_data_version_and_voids_an_earlier_approval():
    from harness.runtime import data_version
    mem, cor = p15.corrected_world(with_conflict=False)
    st = mem.store
    v0 = data_version(st)
    eps = [r["memory_id"] for r in st.records("CLIENT-001", "episodic")][:2]
    from agents.memory.integrity import Principal
    assert st.boundary.record_conflict(eps[0], eps[1], "contradiction_same_period", "high", "x", principal=Principal.service("consolidation", ["CLIENT-001"]))
    v1 = data_version(st)
    st.boundary.resolve_conflict(st.conflicts("CLIENT-001", "open")[0]["conflict_id"], "dismiss", "not material", principal=p15.person(["CLIENT-001"]))
    v2 = data_version(st)
    assert len({v0, v1, v2}) == 3                                                                                     # before P1.5: v0 == v1 == v2
    assert p15._approval_invalidated_by_conflict() is True


# ===================================================================================================== compression
def test_compression_never_alters_a_line_and_keeps_every_protected_line(comp):
    for frac, rows in comp["budgets"].items():
        for label, r in rows.items():
            assert r["altered_lines"] == 0 and r["protected_lines_all_retained"] is True and r["fits"] is True, (frac, label)
            assert r["provenance_retention"] == 1.0                                                                   # a retained line keeps its source reference
    assert comp["droppable_critical_markers"] == []                                                                   # conflict / approval / procedure indicators are protected by classification


def test_critical_last_retains_more_critical_facts_than_position_order_at_equal_budget(comp):
    better = 0
    for frac, rows in comp["budgets"].items():
        old, new = rows["p14_position_order"], rows["p15_critical_last"]
        assert new["critical_fact_retention"] >= old["critical_fact_retention"] and new["negation_retention"] >= old["negation_retention"], frac
        assert new["uncertain_retention"] >= old["uncertain_retention"] and new["correction_retention"] >= old["correction_retention"], frac
        assert new["ratio"] <= old["ratio"] + 0.02 and new["fits"], frac                                              # no compression-ratio sacrifice beyond rounding
        better += new["critical_fact_retention"] > old["critical_fact_retention"]
    assert better >= 3
    low = comp["budgets"]["0.4"]
    assert low["p14_position_order"]["critical_fact_retention"] < 0.3 and low["p15_critical_last"]["critical_fact_retention"] > 0.9
    assert low["p15_critical_last"]["probe_claims_still_verifiable"] > low["p14_position_order"]["probe_claims_still_verifiable"]


def test_the_production_default_keeps_caveats_longest():
    """Through compress() with its DEFAULT settings (the path ContextBudgetController uses) - not through the evaluation's toggling."""
    from harness.token_counter import TokenCounter
    assert compression.CRITICAL_LAST is True
    corpus = p15.compression_corpus()
    text = "\n".join(line for line, _ in corpus)
    counter = TokenCounter("qwen2.5:3b")
    r = compression.compress(text, int(counter.count(text).tokens * 0.55), counter)
    kept = set(r.text.split("\n"))
    assert r.fits and all(line in kept for line, tags in corpus if "correction" in tags or "negation" in tags)       # corrections and negated statements survive 45% compression


def test_classification_rules_for_the_new_tier():
    c = compression.classify
    assert c("[E5 conversation_evidence] Episode; retirement_age; value 65; active/correction; valid ? to open: x") == "episode_critical"
    assert c("[E6 conversation_evidence] Episode; retirement_age; value 60; active/exploratory; valid ? to open: x") == "episode_critical"
    assert c("[E7 conversation_evidence] Episode; retirement_age; value 63; disputed/confirmed; valid ? to open: x") == "episode_critical"
    assert c("[E8 conversation_evidence] Episode; retirement_age; value 63; active/confirmed: Kenji will not retire at 63") == "episode_critical"       # a statement of what is NOT so
    assert c("[E9 conversation_evidence] Episode; income; value 40; active/confirmed: income is 40 thousand") == "episode"
    assert c("[E1 memory] Current knowledge; retirement_age; value 63; active/confirmed: x") == "protected"
    compression.CRITICAL_LAST = False
    try:
        assert c("[E5 conversation_evidence] Episode; retirement_age; value 65; active/correction: x") == "episode"           # the P1.4 order is still reachable for comparison
    finally:
        compression.CRITICAL_LAST = True


# ===================================================================================================== verifier: a statement naming another client
def test_a_finding_that_names_a_client_outside_the_plan_is_withheld_and_not_echoed():
    from harness.evidence_verifier import EvidenceVerifier
    from schemas.report import EvidenceItem, Finding
    from services.conversations import shared
    conv = shared()
    row = conv.meeting("INT-090")[1]
    ev = {"E1": EvidenceItem(ref="E1", source_class="conversation_evidence", description="Episode; x", interaction_id="INT-090", turn_index=row["turn_index"], excerpt=row["utterance"][:40],
                             client_id="CLIENT-001", memory_type="episodic")}
    v = EvidenceVerifier(conv, None, structured=True)
    bad = v.verify_finding("key_findings", Finding(text="Rebecca decided to decline employee ownership", refs=["E1"]), ev, {"CLIENT-001"}, None)
    assert bad.verdict == "unsupported" and "CLIENT_MISMATCH" in bad.reason_codes
    fine = v.verify_finding("key_findings", Finding(text="A grace period applies to the review", refs=["E1"]), ev, {"CLIENT-001"}, None)
    assert "CLIENT_MISMATCH" not in fine.reason_codes                                                                 # a lower-case word that is also a first name is not a client


# ===================================================================================================== end to end
def test_all_seven_end_to_end_scenarios_pass_and_capture_what_was_asked(scenarios):
    assert [s["scenario"] for s in scenarios] == [1, 2, 3, 4, 5, 6, 7]
    for s in scenarios:
        assert s["passed"], (s["scenario"], s["name"], s["actual"])
        for key in ("input", "agent_sequence", "memory_operations", "tool_operations", "evidence_refs", "telemetry", "final_output", "expected", "actual"):
            assert key in s, (s["scenario"], key)
    by = {s["scenario"]: s for s in scenarios}
    assert by[1]["agent_sequence"] == ["planner", "report"] and by[1]["actual"]["provenance_ok"] is True
    assert by[2]["actual"]["rebecca_text_in_output"] is False and by[2]["actual"]["request_for_other_client_denied"] is True
    assert "text not shown" in " ".join(by[2]["final_output"]["uncertainties"])                                       # the withheld statement is not echoed back
    assert by[4]["actual"]["review_required_in_report"] is True and by[7]["actual"]["open_before"] == 1 and by[7]["actual"]["open_after"] == 0
    assert by[5]["final_output"]["findings"] == [] and by[5]["actual"]["content_in_withheld_output"] is False and by[5]["actual"]["repeat_cache_hits"] > 0
    assert by[6]["actual"]["compressed"] is True


def test_report_quality_and_runtime_sections_do_not_invent_provider_usage(scenarios):
    rq = p15.report_quality(scenarios)
    assert rq["unsupported_rate"] == 0.0 and rq["fabricated_numeric_values"] == 0 and rq["evidence_traceability"] == 1.0 and rq["contradicted_claims_released"] == 0
    rt = p15.runtime_metrics(scenarios)
    assert rt["usage_is_exact"] is False and rt["source"].startswith("mocked") and rt["model_calls"] > 0


# ===================================================================================================== reproducibility and real-model honesty
def test_two_independent_runs_give_identical_results_apart_from_wall_clock(claims, temporal, comp, scenarios):
    again = (p15.evaluate_claims(), p15.evaluate_temporal(), p15.evaluate_compression(), p15.run_scenarios())
    one = json.dumps(p15.strip_timings([{k: v["overall"] for k, v in claims.items()}, temporal["summary"], comp, scenarios]), sort_keys=True, default=str)
    two = json.dumps(p15.strip_timings([{k: v["overall"] for k, v in again[0].items()}, again[1]["summary"], again[2], again[3]]), sort_keys=True, default=str)
    assert one == two


def test_the_real_model_section_never_falls_back_to_a_mock(monkeypatch):
    monkeypatch.setenv("CMF_LLM_BACKEND", "mock")
    r = p15.real_model_run()
    assert r["ran"] is False and "mock" in r["reason"] and r["requested"] is True
