"""P1.1 regression tests: structured, deterministic claim verification.

Written BEFORE the implementation.  The 12 cases in the task are reproduced against the existing public interface
(EvidenceVerifier.verify_finding) so each one demonstrates a real false verification of the audited code.  The evidence is plain source text held by a
fake conversation store, with the owning client attached: the verifier must decide from structure, not from shared words.
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from schemas.report import EvidenceItem, Finding  # noqa: E402
from schemas.tools import Envelope, Provenance, ToolExecution, ToolSelectorState  # noqa: E402

KENJI, REBECCA = "CLIENT-001", "CLIENT-002"


class FakeConv:
    """A conversation store holding exactly the evidence sentences of a test: the 'original source text'."""

    def __init__(self):
        self.turns = {}

    def add(self, client, text):
        idx = len(self.turns) + 1
        self.turns[("INT-T", idx)] = dict(client_id=client, speaker="Client", utterance=text)
        return idx

    def turn(self, iid, idx):
        return self.turns.get((iid, idx))

    def verify_excerpt(self, client_id, iid, idx, excerpt):
        t = self.turns.get((iid, idx))
        return bool(t) and t["client_id"] == client_id and " ".join(excerpt.lower().split()) in " ".join(t["utterance"].lower().split())


def evidence(conv, text, client=KENJI, ref="E1", source_text=None, **kw):
    """An evidence item quoting `text`; `source_text` (default: the same) is what the original source really says."""
    idx = conv.add(client, source_text if source_text is not None else text)
    base = dict(ref=ref, source_class="conversation_evidence", description="Episode; " + text, interaction_id="INT-T", turn_index=idx, excerpt=text,
                client_id=client, memory_type="episodic")
    base.update(kw)
    return EvidenceItem(**base)


def verdict(claim, evidence_text, *, client=KENJI, plan=(KENJI,), refs=("E1",), section="key_findings", **kw):
    conv = FakeConv()
    item = evidence(conv, evidence_text, client=client, **kw)
    v = EvidenceVerifier(conv, None)
    return v.verify_finding(section, Finding(text=claim, refs=list(refs)), {"E1": item}, set(plan), ToolSelectorState(request_id="R"))


# ===================================================================================================== the 12 audited cases
NOT_VERIFIED = [
    pytest.param("Kenji is retiring at 63.", "Kenji is not retiring at 63.", id="01-negated"),
    pytest.param("Rebecca's retirement age is 63.", "Kenji's retirement age is 63.", id="02-wrong-person"),
    pytest.param("The retirement age is 6.", "The retirement age is 63.", id="03-substring-number"),
    pytest.param("Available cash covers 12 months.", "Available cash covers 15 months.", id="04-wrong-runway"),
    pytest.param("The emergency fund target is 9 months.", "The emergency fund target is 6 months.", id="05-wrong-target"),
    pytest.param("The allocation is 80%.", "The allocation is 8%.", id="06-wrong-percentage"),
    pytest.param("The current retirement age is 60.", "In 2024, retirement age was planned at 60.", id="07-historical-vs-current"),
    pytest.param("The client has decided to retire at 60.", "We could consider retiring at 60.", id="08-exploratory-vs-confirmed"),
    pytest.param("The target is 12 years.", "The target is 12 months.", id="09-wrong-unit"),
]


@pytest.mark.parametrize("claim,source", NOT_VERIFIED)
def test_audited_false_positives_are_not_verified(claim, source):
    v = verdict(claim, source)
    assert v.verdict != "verified", f"FALSE VERIFICATION: {claim!r} verified by {source!r}"


def test_case_10_missing_evidence_handle_is_not_verified():
    conv = FakeConv()
    v = EvidenceVerifier(conv, None).verify_finding("key_findings", Finding(text="Kenji's retirement age is 63.", refs=["E9"]), {}, {KENJI}, ToolSelectorState(request_id="R"))
    assert v.verdict == "unsupported"


def test_case_10b_inaccessible_evidence_is_not_verified():
    """The handle exists, but the evidence belongs to a client outside the actor's scope."""
    conv = FakeConv()
    item = evidence(conv, "Kenji's retirement age is 63.")
    v = EvidenceVerifier(conv, None, owns=lambda cid: False).verify_finding("key_findings", Finding(text="Kenji's retirement age is 63.", refs=["E1"]), {"E1": item}, {KENJI},
                                                                          ToolSelectorState(request_id="R"))
    assert v.verdict == "unsupported"


def test_case_11_cross_client_evidence_is_a_verification_failure():
    v = verdict("Rebecca's retirement age is 63.", "Rebecca's retirement age is 63.", client=REBECCA, plan=(KENJI,))
    assert v.verdict == "unsupported"
    v2 = verdict("Kenji's retirement age is 63.", "Rebecca's retirement age is 63.", client=REBECCA, plan=(KENJI,))
    assert v2.verdict == "unsupported"


def test_case_12_exact_positive_case_is_verified():
    v = verdict("Kenji's retirement age is 63.", "Kenji's retirement age is 63.")
    assert v.verdict == "verified", v.reasons


# ===================================================================================================== held-out paraphrases
VERIFIED_PARAPHRASES = [
    pytest.param("Kenji will retire at 63.", "Kenji's retirement age is 63.", id="p1-retire-at-vs-retirement-age"),
    pytest.param("Cash covers fifteen months of expenses.", "Available cash covers 15 months of expenses.", id="p2-number-words"),
    pytest.param("The target for the emergency fund is six months.", "The emergency fund target is 6 months.", id="p3-reordered-words"),
    pytest.param("Equities make up 35 percent of the allocation.", "The allocation to equities is 35%.", id="p4-percent-word"),
    pytest.param("Kenji decided on retiring at 63.", "Kenji has decided to retire at 63.", id="p5-same-modality"),
    pytest.param("Kenji's retirement age is 63 years.", "The retirement age is 63.", id="p6-implied-unit-and-default-subject"),
    pytest.param("Available cash covers 1 year of expenses.", "Available cash covers 12 months of expenses.", id="p7-equivalent-durations"),
]


@pytest.mark.parametrize("claim,source", VERIFIED_PARAPHRASES)
def test_held_out_paraphrases_of_true_statements_are_verified(claim, source):
    v = verdict(claim, source)
    assert v.verdict == "verified", (claim, source, v.reasons)


STILL_NOT_VERIFIED = [
    pytest.param("Kenji is retiring at 63.", "Kenji isn't retiring at 63.", id="n1-contraction"),
    pytest.param("Kenji will retire at 63.", "Kenji will never retire at 63.", id="n2-never"),
    pytest.param("Kenji plans to retire at 63.", "Kenji no longer plans to retire at 63.", id="n3-no-longer"),
    pytest.param("Available cash covers 1.5 months.", "Available cash covers 15 months.", id="n4-decimal-shift"),
    pytest.param("The allocation is 8 months.", "The allocation is 8%.", id="n5-percent-vs-duration"),
    pytest.param("Kenji will retire at 60.", "If markets fall, Kenji would retire at 60.", id="n6-hypothetical"),
    pytest.param("Kenji retires at 63.", "Kenji may retire at 63, but nothing is settled.", id="n7-uncertain"),
    pytest.param("Kenji's retirement age is 63.", "Rebecca's retirement age is 63.", id="n8-evidence-names-someone-else"),
    pytest.param("Kenji will retire at 63 in 2030.", "Kenji will retire at 63 in 2028.", id="n9-different-year"),
    pytest.param("Kenji's retirement age is 63 except for 2025.", "Kenji's retirement age is 63.", id="n10-claim-with-exception"),
    pytest.param("Kenji's retirement age is 63.", "Kenji's retirement age is 63 unless the sale closes.", id="n11-evidence-with-condition"),
    pytest.param("Kenji's retirement age is 63 and his monthly expenses are 5 thousand.", "Kenji's retirement age is 63.", id="n12-extra-unsupported-figure"),
    pytest.param("Kenji's retirement age is 62.", "Kenji's retirement age is 63.", id="n13-off-by-one"),
]


@pytest.mark.parametrize("claim,source", STILL_NOT_VERIFIED)
def test_held_out_variants_stay_unverified(claim, source):
    assert verdict(claim, source).verdict != "verified", (claim, source)


def test_evidence_without_a_client_never_verifies():
    conv = FakeConv()
    item = evidence(conv, "Kenji's retirement age is 63.", client=None)
    v = EvidenceVerifier(conv, None).verify_finding("key_findings", Finding(text="Kenji's retirement age is 63.", refs=["E1"]), {"E1": item}, {KENJI}, ToolSelectorState(request_id="R"))
    assert v.verdict != "verified"


def test_claim_without_a_resolvable_subject_in_a_multi_client_report_is_not_verified():
    v = verdict("The retirement age is 63.", "Kenji's retirement age is 63.", plan=(KENJI, REBECCA))
    assert v.verdict != "verified"


def test_a_quote_that_is_not_in_the_source_is_not_proof():
    """The evidence item quotes text the original turn never contained (an LLM paraphrase presented as an excerpt)."""
    v = verdict("Kenji's retirement age is 63.", "Kenji's retirement age is 63.", source_text="We talked about the weather and lunch plans today.")
    assert v.verdict == "unsupported"


def test_paraphrased_description_cannot_substitute_for_the_quote():
    """The description (agent-written) says 63 but the verified quote says 60: only the quote is evidence."""
    conv = FakeConv()
    item = evidence(conv, "Kenji's retirement age is 60.", description="Current knowledge; retirement_age; value 63")
    v = EvidenceVerifier(conv, None).verify_finding("key_findings", Finding(text="Kenji's retirement age is 63.", refs=["E1"]), {"E1": item}, {KENJI}, ToolSelectorState(request_id="R"))
    assert v.verdict != "verified"


def test_a_stronger_value_in_a_second_cited_handle_does_not_rescue_a_wrong_claim():
    conv = FakeConv()
    a = evidence(conv, "Kenji's retirement age is 63.", ref="E1")
    b = evidence(conv, "Kenji's favourite number is 6 and he likes lunch.", ref="E2")
    v = EvidenceVerifier(conv, None).verify_finding("key_findings", Finding(text="Kenji's retirement age is 6.", refs=["E1", "E2"]), {"E1": a, "E2": b}, {KENJI},
                                                  ToolSelectorState(request_id="R"))
    assert v.verdict != "verified"


# ===================================================================================================== tool evidence (typed values, exact)
def tool_item(data, tool="calculate_liquidity_ratio", client=KENJI):
    env = Envelope(tool=tool, client_id=client, as_of="2026-06-30", data=data, provenance=Provenance(source="t"))
    ex = ToolExecution(id="EX-1", iteration=1, tool=tool, arguments={"client_id": client}, status="success", result=env)
    item = EvidenceItem(ref="E1", source_class="tool_calculation", description=f"{tool} as of 2026-06-30: ...", tool_execution_id="EX-1", synthetic=True, client_id=client)
    return item, ToolSelectorState(request_id="R", executions=[ex])


@pytest.mark.parametrize("claim,expected", [
    ("Liquidity as of 2026-06-30: cash covers 15 months of expenses.", "verified"),
    ("Liquidity as of 2026-06-30: cash covers 12 months of expenses.", "not"),
    ("Liquidity as of 2026-06-30: the liquidity target is 6 months.", "verified"),
    ("Liquidity as of 2026-06-30: the liquidity target is 9 months.", "not"),
    ("Liquidity as of 2026-06-30: the liquidity target is 15 months.", "not"),          # 15 exists in the data, but as the cash figure, not the target
    ("Liquidity as of 2026-06-30: cash covers 15 years of expenses.", "not"),
])
def test_tool_figures_are_matched_per_value_unit_and_topic(claim, expected):
    item, tools = tool_item({"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0})
    v = EvidenceVerifier(FakeConv(), None).verify_finding("financial_analysis", Finding(text=claim, refs=["E1"]), {"E1": item}, {KENJI}, tools)
    assert (v.verdict == "verified") is (expected == "verified"), (claim, v.reasons)


# ===================================================================================================== new interface: outcomes, reasons, claim structure
EXPECTED_REASONS = [
    ("Kenji is retiring at 63.", "Kenji is not retiring at 63.", "CONTRADICTED", "POLARITY_MISMATCH"),
    ("Rebecca's retirement age is 63.", "Kenji's retirement age is 63.", "UNVERIFIED", "CLIENT_MISMATCH"),
    ("The retirement age is 6.", "The retirement age is 63.", "CONTRADICTED", "VALUE_MISMATCH"),
    ("Available cash covers 12 months.", "Available cash covers 15 months.", "CONTRADICTED", "VALUE_MISMATCH"),
    ("The emergency fund target is 9 months.", "The emergency fund target is 6 months.", "CONTRADICTED", "VALUE_MISMATCH"),
    ("The allocation is 80%.", "The allocation is 8%.", "CONTRADICTED", "VALUE_MISMATCH"),
    ("The current retirement age is 60.", "In 2024, retirement age was planned at 60.", "UNVERIFIED", "TEMPORAL_MISMATCH"),
    ("The client has decided to retire at 60.", "We could consider retiring at 60.", "UNVERIFIED", "MODALITY_MISMATCH"),
    ("The target is 12 years.", "The target is 12 months.", "UNVERIFIED", "UNIT_MISMATCH"),
]


@pytest.mark.parametrize("claim,source,outcome,reason", EXPECTED_REASONS)
def test_every_rejection_carries_an_outcome_and_a_structured_reason(claim, source, outcome, reason):
    v = verdict(claim, source)
    assert v.outcome == outcome and reason in v.reason_codes, (v.outcome, v.reason_codes)
    assert v.claims and v.claims[0]["outcome"] == outcome and reason in v.claims[0]["reasons"]


def test_missing_handle_reason_code():
    v = EvidenceVerifier(FakeConv(), None).verify_finding("key_findings", Finding(text="Kenji's retirement age is 63.", refs=["E9"]), {}, {KENJI}, ToolSelectorState(request_id="R"))
    assert v.outcome == "UNVERIFIED" and "EVIDENCE_NOT_FOUND" in v.reason_codes


def test_exact_positive_case_reports_the_structured_claim():
    v = verdict("Kenji's retirement age is 63 years.", "Kenji's retirement age is 63.")
    c = v.claims[0]["claim"]
    assert v.outcome == "VERIFIED" and v.reason_codes == []
    assert c["subject_client"] == KENJI and c["topic"] == "retirement_age" and Decimal(str(c["value"])) == 63 and c["unit"] == "years"
    assert c["polarity"] == "POSITIVE" and c["evidence_handle"] == "E1" and c["source_id"]


def test_structured_claim_schema():
    from schemas.claims import ClaimVerification, Modality, Outcome, Period, PeriodKind, Polarity, Reason, StructuredClaim
    assert {o.value for o in Outcome} == {"VERIFIED", "UNVERIFIED", "CONTRADICTED", "AMBIGUOUS"}
    assert {"CLIENT_MISMATCH", "POLARITY_MISMATCH", "VALUE_MISMATCH", "UNIT_MISMATCH", "TEMPORAL_MISMATCH", "MODALITY_MISMATCH", "EVIDENCE_NOT_FOUND", "AMBIGUOUS_SUBJECT"} <= {r.value for r in Reason}
    assert {m.value for m in Modality} >= {"CONFIRMED", "REPORTED", "ESTIMATED", "HYPOTHETICAL", "EXPLORATORY", "UNCERTAIN"}
    c = StructuredClaim(claim_id="C1", text="x")                                           # unavailable information is represented explicitly, never guessed
    assert c.subject_client is None and c.topic is None and c.value is None and c.unit is None and c.polarity is None and c.modality is None and c.evidence_handle is None
    assert c.period is None or c.period.kind == PeriodKind.UNSPECIFIED
    assert Polarity.NEGATIVE.value == "NEGATIVE" and Period().dates == []
    ClaimVerification(claim=c, outcome=Outcome.AMBIGUOUS, reasons=[Reason.AMBIGUOUS_SUBJECT])


# ===================================================================================================== extraction unit tests (deterministic, no evidence)
def facts(text, names=None):
    from harness.claim_extraction import extract_claims
    return extract_claims(text, names or {"kenji": KENJI, "rebecca": REBECCA})


def test_extraction_reads_value_unit_polarity_modality_and_period():
    (c,) = facts("Kenji has decided to retire at 63.")
    assert (c.subject_client, c.topic, c.value, c.polarity.value, c.modality.value) == (KENJI, "retirement_age", Decimal(63), "POSITIVE", "CONFIRMED")
    (n,) = facts("Kenji is not retiring at 63.")
    assert n.polarity.value == "NEGATIVE"
    (h,) = facts("In 2024, retirement age was planned at 60.")
    assert h.period.kind.value == "HISTORICAL" and "2024" in h.period.dates
    (e,) = facts("We could consider retiring at 60.")
    assert e.modality.value == "EXPLORATORY"


def test_extraction_distinguishes_units_and_never_substring_matches():
    (a,) = facts("Available cash covers 15 months.")
    assert (a.value, a.unit) == (Decimal(15), "months")
    (b,) = facts("The allocation is 8%.")
    assert (b.value, b.unit) == (Decimal(8), "percent")
    (c,) = facts("The retirement age is 63.")
    assert c.value == Decimal(63) and c.value != Decimal(6)
    (d,) = facts("Costs are $1,200.50 per month.")
    assert d.value == Decimal("1200.50") and d.unit == "USD"
    (w,) = facts("Cash covers fifteen months.")
    assert w.value == Decimal(15)


def test_extraction_flags_what_it_cannot_judge():
    ms = facts("Kenji is not retiring at 63 or 60.")
    assert len(ms) == 2 and all("negation_scope" in c.flags for c in ms)                # a negation next to two figures: scope is NOT guessed
    (q,) = facts("Kenji retires at 63 unless the sale closes.")
    assert "qualifier" in q.flags
    both = facts("Kenji and Rebecca retire at 63.")
    assert both and all("multi_subject" in c.flags for c in both)
    assert facts("Kenji prefers conservative investments.")[0].value is None              # qualitative: no value is invented
