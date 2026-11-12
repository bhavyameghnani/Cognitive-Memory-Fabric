"""P1.5.1 - report-layer verification (audit finding H1), evaluation integrity (H2, M1-M3) and the narrow cache / compression changes.

The 73 probes come VERBATIM from the P1.4.2 review (harness/evaluation/p151_probes.py).  They are run through the REAL structured report verification path
(EvidenceVerifier.verify_finding via harness.evaluation.adversarial.present) and, for finding generation, through the whole pipeline.  All databases are in-memory / temporary."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from harness import clause_support  # noqa: E402
from harness.evaluation import adversarial, fixtures, p15, p151  # noqa: E402
from harness.evaluation.p151_probes import P  # noqa: E402
from harness.evaluation.p15_corpus import N, V, Case15  # noqa: E402

K = "CLIENT-001"
# reproduced against the P1.5 report verifier (clause check switched off): the 16 unsupported probes presented as VERIFIED
ORIGINAL_REPORT_FALSE_POSITIVES = {"A4", "A5", "A6", "B7", "B18", "C4", "C5", "C8", "E1", "E2", "E3", "E4", "M1", "M3", "M5", "M9"}
# supported by the source but declined by the conservative checker: recorded, not rescued
KNOWN_REPORT_FALSE_NEGATIVES = {"B16", "D10", "F8"}


def _presented(pid_row, clause=True):
    c = Case15(pid_row[0], "probe", K, pid_row[1], pid_row[2], pid_row[3], pid_row[4], "-", open_item=bool(pid_row[5]))
    clause_support.ENABLED = clause
    try:
        return adversarial.present(p15._report_case(c), "structured")
    finally:
        clause_support.ENABLED = True


@pytest.fixture(scope="module")
def after():
    return {r[0]: _presented(r) for r in P}


def test_the_baseline_reproduces_the_16_original_false_positives():
    before = {r[0] for r in P if r[4] == N and _presented(r, clause=False)["presented_as_verified"]}
    assert before == ORIGINAL_REPORT_FALSE_POSITIVES


def test_no_unsupported_probe_is_presented_as_verified(after):
    fp = sorted(r[0] for r in P if r[4] == N and after[r[0]]["presented_as_verified"])
    assert fp == []


def test_every_original_false_positive_is_now_withheld_or_qualified(after):
    for pid in sorted(ORIGINAL_REPORT_FALSE_POSITIVES):
        assert not after[pid]["presented_as_verified"], (pid, after[pid])


def test_false_negatives_are_recorded_not_hidden(after):
    fn = {r[0] for r in P if r[4] == V and not after[r[0]]["presented_as_verified"]}
    assert fn == KNOWN_REPORT_FALSE_NEGATIVES


def test_all_supported_probes_that_can_be_verified_still_are(after):
    ok = {r[0] for r in P if r[4] == V and after[r[0]]["presented_as_verified"]}
    assert ok == {"B1", "C7", "C12", "C15", "D4", "D5", "F4", "F6"}


@pytest.mark.parametrize("pid", [r[0] for r in P])
def test_probe_through_the_real_report_verifier(after, pid):
    row = next(r for r in P if r[0] == pid)
    if row[4] == N:
        assert not after[pid]["presented_as_verified"], after[pid]
    elif pid in KNOWN_REPORT_FALSE_NEGATIVES:
        assert not after[pid]["presented_as_verified"]
    else:
        assert after[pid]["presented_as_verified"], after[pid]


# ---------------------------------------------------------------------------------------------------------------- the required regression categories
CATEGORIES = [  # (finding, evidence quote) - every one must NOT be presented as verified
    ("Kenji will retire at 63.", "My wife will retire at 63."), ("Kenji will retire at 63.", "My father will retire at 63."), ("Kenji will retire at 63.", "My daughter will retire at 63."),
    ("Kenji will retire at 63.", "My colleague will retire at 63."), ("Kenji will retire at 63.", "My advisor will retire at 63."), ("Kenji retires before his wife at 63.", "My wife retires before me at 63."),
    ("Kenji will retire at 63 and has a serious illness.", "Kenji will retire at 63."), ("Kenji will retire at 63 and invest $1 million.", "Kenji will retire at 63."),
    ("Kenji will retire at 63 and owns a vacation home.", "Kenji will retire at 63."), ("Kenji will retire at 63 and has retired.", "Kenji will retire at 63."),
    ("Kenji will retire at 63 and works part time.", "Kenji will retire at 63."), ("Kenji will retire at 63 and earns 9000 a month.", "Kenji will retire at 63."),
    ("Kenji will retire at 63.", "Kenji will not retire at 63."), ("Kenji will retire at 63.", "Kenji will never retire at 63."), ("Kenji will retire at 63.", "Kenji rejected retiring at 63."),
    ("Kenji will retire at 63.", "Kenji declined to retire at 63."), ("Kenji will retire at 63.", "Kenji refused to retire at 63."), ("Kenji will retire at 63.", "Kenji might retire at 63."),
    ("Kenji will retire at 63.", "Kenji may retire at 63."), ("Kenji will retire at 63.", "Kenji is considering retiring at 63."), ("Kenji will retire at 63.", "It is unknown whether Kenji will retire at 63."),
    ("Kenji has decided to retire at 63.", "Kenji has not decided to retire at 63."), ("Kenji will retire at 63.", "Kenji will retire at 63 unless things change."),
    ("Kenji will retire at 63.", "If the sale closes Kenji will retire at 63."), ("Kenji will retire at 63.", "Kenji will retire at 63 except in a downturn."),
    ("Kenji will retire at 63 and sell the house.", "Kenji will retire at 63 or sell the house."), ("Kenji will retire at 63.", "Kenji will retire at 63 only after the sale."),
    ("Kenji will retire at 63 before the sale.", "Kenji will retire at 63 after the sale."), ("Kenji will retire at 63.", "I was told Kenji will retire at 63."),
    ("Kenji will retire at 63.", "My advisor recommends that Kenji retire at 63."), ("Kenji will retire at 63.", "My spouse believes Kenji will retire at 63."),
    ("Kenji will retire at 63.", "Someone suggested that Kenji retire at 63."), ("Kenji will retire at 63.", "Kenji will retire at 63. Just kidding."),
    ("Kenji will retire at 63.", "Kenji will retire at 63. I take that back."), ("Kenji will retire at 63.", "Kenji will retire at 63. I no longer believe this."),
    ("Kenji will retire at 63.", "Kenji will retire at 63. That plan was cancelled."), ("Kenji earns $10,000 a month.", "Kenji earns $10,000 a year."),
    ("Kenji's loan is Rs 63 lakh.", "Kenji's loan is Rs 63 crore."), ("Kenji's retirement age was 60.", "Kenji's retirement age is 60."), ("Kenji has retired at 63.", "Kenji plans to retire at 63."),
    ("Kenji will retire at 60.", "Kenji will retire at 63 and his wife at 60."), ("Kenji will retire at 63.", "Kenji's old plan was to retire at 63."),
]


@pytest.mark.parametrize("finding,quote", CATEGORIES)
def test_required_categories_are_never_verified(finding, quote):
    c = Case15("X", "probe", K, finding, quote, "63", N, "-")
    assert not adversarial.present(p15._report_case(c), "structured")["presented_as_verified"], (finding, quote)


def test_supported_statements_are_still_verified():
    for finding, quote in (("Kenji will retire at 63.", "Kenji will retire at 63."), ("Kenji will retire at 63.", "Kenji will retire at 63 and his wife at 60."),
                           ("Kenji's retirement age is 63 years.", "The retirement age is 63."), ("Kenji earns $10,000 a month.", "Kenji earns $10,000 a month.")):
        c = Case15("X", "probe", K, finding, quote, "63", V, "-")
        assert adversarial.present(p15._report_case(c), "structured")["presented_as_verified"], (finding, quote)


def test_the_clause_check_only_downgrades_it_never_promotes():
    assert clause_support.check("Kenji will retire at 63.", [], "", ["Kenji"]) == []
    clause_support.ENABLED = False
    try:
        assert clause_support.check("Kenji will retire at 63 and is ill.", ["Kenji will retire at 63."], "", ["Kenji"]) == []
    finally:
        clause_support.ENABLED = True
    problems = clause_support.check("Kenji will retire at 63 and has a terminal illness.", [("Kenji will retire at 63.", {"Kenji"})], "retirement_age 63", ["Kenji"])
    assert problems and all(p.outcome.value in ("UNVERIFIED", "AMBIGUOUS") for p in problems)


# ---------------------------------------------------------------------------------------------------------------- whole pipeline
FINDING = lambda text: p151._finding(text)      # noqa: E731


@pytest.mark.parametrize("text,needle", [("Kenji's current retirement age is 63 and he has a terminal illness", "terminal illness"),
                                         ("Kenji's current retirement age is 63, which his wife also chose", "wife"),
                                         ("Kenji's wife will retire at 63", "wife"),
                                         ("Kenji's current retirement age is 63 unless things change", "unless things change")])
def test_pipeline_an_unsupported_extension_of_a_verified_finding_is_not_presented_as_verified(text, needle):
    w = p15.build_world()
    res = p15.ask(w, p151.Q, p15.make_harness(), report=FINDING(text))
    shown = [f for f in res.report.key_findings if needle in f.text]
    assert all(f.text.startswith("[Qualified") for f in shown)                       # at most shown WITH a visible qualification, never as a plain finding
    assert p151._outcomes(res).get("VERIFIED", 0) == 0


def test_pipeline_the_plain_supported_finding_is_still_verified():
    w = p15.build_world()
    res = p15.ask(w, p151.Q, p15.make_harness(), report=FINDING("Kenji's current retirement age is 63"))
    assert p151._outcomes(res).get("VERIFIED") == 1 and any("63" in f.text for f in res.report.key_findings)


def test_report_verifier_refuses_any_finding_resting_on_non_verified_memory_even_without_the_authority_split():
    from harness.evidence_verifier import EvidenceVerifier
    from schemas.report import EvidenceItem, Finding
    conv, tools, evidence, refs, section = adversarial._build(adversarial.AdvCase("x", "g", "Kenji's retirement age is 63.", "Kenji's retirement age is 63.", V, "-"))
    for cls in ("unverified", "contradicted", "open_conflict"):
        evidence["E1"].evidence_class = cls
        v = EvidenceVerifier(conv, None, structured=True).verify_finding(section, Finding(text="Kenji's retirement age is 63.", refs=refs), evidence, {adversarial.KENJI}, tools)
        assert v.verdict == "unsupported" and "EVIDENCE_NOT_VERIFIED" in v.reason_codes, cls


# ---------------------------------------------------------------------------------------------------------------- evaluation integrity (H2, M1, M2, M3)
@pytest.fixture(scope="module")
def probes():
    return p151.evaluate_probes()


def test_memory_and_report_layer_metrics_are_reported_separately(probes):
    assert set(probes) == {"memory_episodic_llm", "memory_derived_semantic", "report_before_p151 (clause check switched off)", "report_current"}
    assert probes["report_before_p151 (clause check switched off)"]["overall"]["fp"] == 16 and probes["report_current"]["overall"]["fp"] == 0
    assert probes["memory_episodic_llm"]["overall"]["fp"] == 0 and probes["memory_derived_semantic"]["overall"]["fp"] == 0
    assert probes["report_current"]["false_negative_ids"] == sorted(KNOWN_REPORT_FALSE_NEGATIVES)


def test_the_derived_semantic_path_is_measured_and_never_outranks_its_episode(probes):
    assert probes["memory_derived_semantic"]["false_positive_ids"] == []


def test_all_12_p151_scenarios_pass_and_record_every_layer():
    sc = p151.run_scenarios()
    assert [s["scenario"] for s in sc] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 10, 12]
    for s in sc:
        assert s["passed"], s
        assert set(s["layers"]) == {"storage", "retrieval", "context", "report_evidence", "finding", "approval", "release"}


def test_the_baseline_is_not_called_p142_any_more():
    claims = p15.evaluate_claims()
    assert "memory_p142_shipped" not in claims and "memory_matcher_rules_off" in claims
    assert "NOT the matcher as shipped in P1.4.2" in p15.shipped_p142.__doc__


def test_documented_headline_numbers_equal_the_computed_ones():
    claims = p15.evaluate_claims()
    o = claims["memory_current"]["overall"]
    doc = (ROOT / "docs" / "harness.md").read_text(encoding="utf-8")
    assert f"| **memory, current** | {o['tp']} | **{o['fp']}** | {o['fn']} | {o['tn']} |" in doc
    r = claims["report_current"]["overall"]
    assert f"| **report layer, current (P1.5.1)** | {r['tp']} | **{r['fp']}** | {r['fn']} | {r['tn']} |" in doc
    assert "regression measurement" in doc and "not independent validation" in doc


# ---------------------------------------------------------------------------------------------------------------- cache and compression (narrow changes)
def test_cache_key_client_serialisation_is_unambiguous():
    from harness.cache import make_key
    base = dict(model="m", agent="a", system="s", schema=None, user="u", actor="x", data_version="v")
    assert make_key(**base, clients=("a,b",)) != make_key(**base, clients=("a", "b"))
    assert make_key(**base, clients=("b", "a")) == make_key(**base, clients=("a", "b")) != make_key(**base, clients=("a",))


def test_omission_markers_name_the_omitted_caveat_kinds():
    from agents.report.evidence import prompt_lines
    from harness import compression
    from harness.token_counter import TokenCounter
    w, _ = p15.corrected_world()
    res = p15.ask(w, p151.Q, p15.make_harness())
    text = "\n".join(prompt_lines(res.report.evidence))
    tc = TokenCounter()
    out = compression.compress(text, tc.count(text).tokens // 3, tc)
    markers = [ln for ln in out.text.splitlines() if ln.startswith("[context compressed")]
    assert markers and any("kinds:" in m for m in markers)
    assert compression.omitted_kinds(["[E3 memory, unverified] Episode; x [UNVERIFIED - needs confirmation]", "[E4] Episode; active/correction"]) == ["unverified", "correction"]


def test_an_omitted_unverified_or_contradicted_value_cannot_come_back_as_a_verified_fact():
    """A model that saw only the marker (the line was compressed away) and states the omitted value anyway: the value is checked against the full evidence, where it is non-verified."""
    w = p15.build_world()
    mid = p151._fabricated(w)
    res = p15.ask(w, p151.Q, p15.make_harness(), report=p151._finding("Kenji has been diagnosed with a serious illness", r"\[(E\d+)[^\]]*\][^\n]*serious illness"))
    assert not p151._presented(res, "serious illness")
    ev = next(e for e in res.report.evidence if e.memory_id == mid)
    assert ev.evidence_class == "unverified" and ev.verification == "unverified"


def test_an_unauthenticated_or_anonymous_caller_cannot_release_an_approved_answer():
    from harness import AuthContext, PermissionDenied
    from harness.runtime import data_version
    w = p15.build_world()
    h = p15.make_harness()
    r1 = p15.ask(w, "Should Kenji retire at 60?", h, planner=fixtures.planner_json(task="forecast_feasibility"))
    apr = r1.harness["approval"]
    h.decide_approval(apr["approval_id"], AuthContext.for_test("SUP-1", "supervisor"), True, "reviewed")
    dv = data_version(w.store)
    for who in (AuthContext(actor_id="ADVISOR-001", role="advisor", authenticated=False), AuthContext(actor_id="", role="advisor", authenticated=True)):
        with pytest.raises(PermissionDenied) as e:
            h.release(apr["approval_id"], dv, actor=who)
        assert e.value.code == "unauthenticated"
    assert h.release(apr["approval_id"], dv, actor=AuthContext.for_test("ADVISOR-001", "advisor")).summary        # the requester still can, once


def test_default_compression_keeps_a_caveat_bearing_line_longer_than_a_plain_episode():
    """With the DEFAULT policy (not a switch the evaluation sets itself): at a budget that forces one episode out, the plain episode goes and the correction / negated one stays."""
    from harness import compression
    from harness.token_counter import TokenCounter
    assert compression.CRITICAL_LAST is True
    plains = [f"[E{i} conversation_evidence] Episode; retirement_age; value 6{i}; active/confirmed: Kenji will retire at 6{i}." for i in range(2, 6)]
    caveat = "[E9 conversation_evidence] Episode; retirement_age; value 60; active/correction: Kenji did not decide to retire at 60."
    text = chr(10).join(["REQUEST: x", *plains, caveat])
    tc = TokenCounter()
    total = tc.count(text).tokens
    seen = 0
    for budget in range(total, total // 3, -3):                                            # every budget at which only PLAIN episodes had to go
        out = compression.compress(text, budget, tc)
        if out.removed.get("episode") and not out.removed.get("episode_critical"):
            seen += 1
            assert caveat in out.text, budget
    assert seen >= 3                                                                          # the property was exercised, not vacuous


# ---------------------------------------------------------------------------------------------------------------- each clause rule on its own (the other layers cannot mask it)
def _reasons(finding, quote, vocab=""):
    return {p.reason.value for p in clause_support.check(finding, [(quote, {"Kenji"})], vocab, ["Kenji"])}


def test_clause_unit_a_person_the_source_never_mentions_is_refused_even_when_every_word_is_vocabulary():
    assert "ACTOR_MISMATCH" in _reasons("His wife loves sailing.", "Kenji will retire at 63.", vocab="wife loves sailing")


def test_clause_unit_hearsay_in_the_source_is_refused_when_the_finding_states_it_directly():
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63.", "I was told to retire at 63.")
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63.", "Someone suggested that I retire at 63.")
    assert _reasons("Kenji will retire at 63.", "I will retire at 63.") == set()


def test_clause_unit_the_other_rules_each_fire_alone():
    assert "POLARITY_MISMATCH" in _reasons("Kenji will retire at 63.", "I will not retire at 63.")
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63.", "I may retire at 63.")                      # hedge
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63.", "I will retire at 63 only after the sale.")    # condition left out
    assert "TEMPORAL_MISMATCH" in _reasons("Kenji has retired at 63.", "I plan to retire at 63.")
    assert "ACTOR_MISMATCH" in _reasons("Kenji will retire at 63.", "My wife will retire at 63.")


# ---------------------------------------------------------------------------------------------------------------- P1.5.2: "after tax" is an amount qualifier, other "after" stays a condition
def test_p152_after_tax_is_an_amount_qualifier_not_a_condition():
    q = "I plan to retire at 65 on about fifteen thousand a month after tax."
    assert _reasons("Kenji plans to retire at 65.", q) == set()
    assert _reasons("Kenji plans to retire at 65.", q.replace("after tax", "after-tax")) == set()
    assert _reasons("Kenji plans to retire at 65.", q.replace("after tax", "after taxes")) == set()


def test_p152_a_genuine_temporal_after_is_still_a_condition():
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63.", "I will retire at 63 after the house sells.")
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63.", "I will retire at 63 after my daughter graduates.")


def test_p152_a_genuine_conditional_after_is_still_a_condition_even_beside_after_tax():
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 65.", "I will retire at 65 only after the sale, on fifteen thousand a month after tax.")
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 65.", "I will retire at 65 after the sale, with fifteen thousand a month after tax.")


def test_p152_source_and_claim_that_disagree_about_a_condition_are_still_flagged():
    # the claim states a condition the source does not (reverse direction is not this rule's job), and a claim's "after tax" cannot mask a source condition
    assert "MODALITY_MISMATCH" in _reasons("Kenji will retire at 63 after tax.", "I will retire at 63 after the sale.")
    assert _reasons("Kenji will retire at 63 after the sale.", "I will retire at 63 after the sale.") == set()


# ---------------------------------------------------------------------------------------------------------------- P1.5.3: the tax-qualifier exemption is narrow, and each side of the comparison is tested alone
def _marks(tokens):
    return {k.split(":")[0] for k in clause_support.conditions(tokens)}


_TAXV = "retire tax month"          # vocabulary that covers the claim's own words, so UNSUPPORTED_CLAUSE cannot be the reason a case fails


def test_p153_tokenizer_drops_hyphens_so_the_rule_cannot_rely_on_them():
    from agents.memory import support
    assert support.tokens("after-tax income") == ["after", "tax", "income"] and support.tokens("after tax") == ["after", "tax"]


def test_p153_amount_qualifier_after_tax_is_not_a_condition():
    assert _marks(["fifteen", "thousand", "a", "month", "after", "tax"]) == set()
    assert _reasons("Kenji will retire at 65.", "I will retire at 65 on fifteen thousand a month after tax.", _TAXV) == set()


def test_p153_hyphenated_amount_qualifier_is_not_a_condition():
    from agents.memory import support
    for text in ("after-tax income", "after-tax income is 15,000", "$5,000 after taxes"):
        assert _marks(support.tokens(text)) == set(), text


def test_p153_after_tax_season_remains_a_condition():
    from agents.memory import support
    for text in ("I retire after tax season", "I retire after tax filing", "I retire after tax", "I retire at 63 after tax", "retire after tax income arrives"):
        assert _marks(support.tokens(text)) == {"after"}, text
    assert _reasons("Kenji will retire at 63.", "I will retire at 63 after tax season.", _TAXV) == {"MODALITY_MISMATCH"}


def test_p153_after_retirement_and_after_the_sale_remain_conditions():
    from agents.memory import support
    for text in ("I will move abroad after retirement", "I will retire at 63 after the sale", "I retire after my daughter graduates"):
        assert _marks(support.tokens(text)) == {"after"}, text
    assert _reasons("Kenji will retire at 63.", "I will retire at 63 after retirement.", _TAXV) == {"MODALITY_MISMATCH"}
    assert _reasons("Kenji will retire at 63.", "I will retire at 63 after the sale.", _TAXV) == {"MODALITY_MISMATCH"}


def test_p153_a_source_with_both_a_tax_qualifier_and_a_real_condition_stays_conditional():
    from agents.memory import support
    assert _marks(support.tokens("after the sale on a month after tax")) == {"after"}          # one genuine 'after' is enough
    assert _marks(support.tokens("a month after tax and after the sale")) == {"after"}          # in either order
    assert _reasons("Kenji will retire at 63.", "I will retire at 63 after the sale and a month after tax.", _TAXV) == {"MODALITY_MISMATCH"}


def test_p153_a_claim_side_tax_qualifier_cannot_mask_a_source_condition():
    # claim: only a tax qualifier; source: a real 'after'.  Vocabulary covers every claim word, so the ONLY possible problem is the condition comparison.
    assert _reasons("Kenji will retire at 63 a month after tax.", "I will retire at 63 after the sale.", _TAXV) == {"MODALITY_MISMATCH"}
    # control: the same claim against a source that has the same qualifier and no condition is clean (the case above is not failing for some other reason)
    assert _reasons("Kenji will retire at 63 a month after tax.", "I will retire at 63 a month after tax.", _TAXV) == set()


def test_p153_a_claim_that_states_the_same_condition_as_the_source_is_not_flagged():
    assert _reasons("Kenji will retire at 63 after the sale.", "I will retire at 63 after the sale.", _TAXV) == set()


def test_p153_a_different_after_in_the_claim_does_not_cover_the_sources_after():
    # keys compare WHAT follows 'after': 'after tax season' (claim) is not 'after the sale' (source), 'after the sale' equals 'after sale' (determiners skipped)
    assert clause_support.conditions(["after", "tax", "season"]) == {"after:tax"} and clause_support.conditions(["after", "the", "sale"]) == {"after:sale"}
    assert _reasons("Kenji will retire at 63 after tax season.", "I will retire at 63 after the sale.", _TAXV + " season") == {"MODALITY_MISMATCH"}
    assert _reasons("Kenji will retire at 63 after sale.", "I will retire at 63 after the sale.", _TAXV + " sale") == set()
