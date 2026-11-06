"""Adversarial claim-verification fixtures with HAND-WRITTEN expected outcomes.

Independence: the expected label of every case is written here by a person ("a claim of 6 is not supported by evidence saying 63"), never computed by any verifier.  The verifiers are
the systems UNDER TEST; they are not used to produce, filter or repair a label.  Three systems are scored against the same labels:
    none         the existing CMF pipeline, which has no evidence verification: every claim is presented as it was written (so every wrong claim is a false verification)
    lexical      the audited harness verifier (word overlap + "does this figure occur anywhere"), kept as EvidenceVerifier(structured=False)
    structured   the P1.1 verifier
Metrics: a FALSE POSITIVE is a claim labelled NOT_VERIFIED that the system presented as verified; a FALSE NEGATIVE is a claim labelled VERIFIED that it did not present as verified
(qualified, withheld).  Scripted fixtures only: this measures a deterministic component, not the accuracy of any language model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from harness.evidence_verifier import EvidenceVerifier
from schemas.report import EvidenceItem, Finding
from schemas.tools import Envelope, Provenance, ToolExecution, ToolSelectorState

KENJI, REBECCA = "CLIENT-001", "CLIENT-002"
V, N = "VERIFIED", "NOT_VERIFIED"


@dataclass
class AdvCase:
    id: str
    group: str                      # audited | paraphrase | held_out | structural | tool
    claim: str
    evidence: str
    expected: str                   # V | N  - written by hand
    why: str
    evidence_client: Optional[str] = KENJI
    plan: tuple = (KENJI,)
    kind: str = "text"              # text | missing_handle | inaccessible | fabricated_quote | description_only | tool
    tool_data: dict = field(default_factory=dict)


CASES: list[AdvCase] = [
    # ---- the twelve audited cases ---------------------------------------------------------------------------------------------------------------
    AdvCase("a01-negated", "audited", "Kenji is retiring at 63.", "Kenji is not retiring at 63.", N, "the evidence denies what the claim asserts"),
    AdvCase("a02-wrong-person", "audited", "Rebecca's retirement age is 63.", "Kenji's retirement age is 63.", N, "evidence about Kenji cannot support a claim about Rebecca"),
    AdvCase("a03-substring-number", "audited", "The retirement age is 6.", "The retirement age is 63.", N, "6 is not 63"),
    AdvCase("a04-wrong-runway", "audited", "Available cash covers 12 months.", "Available cash covers 15 months.", N, "12 is not 15"),
    AdvCase("a05-wrong-target", "audited", "The emergency fund target is 9 months.", "The emergency fund target is 6 months.", N, "9 is not 6"),
    AdvCase("a06-wrong-percentage", "audited", "The allocation is 80%.", "The allocation is 8%.", N, "80% is not 8%"),
    AdvCase("a07-historical-vs-current", "audited", "The current retirement age is 60.", "In 2024, retirement age was planned at 60.", N, "an old plan is not the current value"),
    AdvCase("a08-exploratory-vs-confirmed", "audited", "The client has decided to retire at 60.", "We could consider retiring at 60.", N, "an idea is not a decision"),
    AdvCase("a09-wrong-unit", "audited", "The target is 12 years.", "The target is 12 months.", N, "years are not months"),
    AdvCase("a10-missing-handle", "audited", "Kenji's retirement age is 63.", "", N, "the cited handle does not exist", kind="missing_handle"),
    AdvCase("a10b-inaccessible-handle", "audited", "Kenji's retirement age is 63.", "Kenji's retirement age is 63.", N, "the actor may not use this evidence", kind="inaccessible"),
    AdvCase("a11-cross-client-evidence", "audited", "Kenji's retirement age is 63.", "Rebecca's retirement age is 63.", N, "evidence from another client", evidence_client=REBECCA),
    AdvCase("a12-exact-positive", "audited", "Kenji's retirement age is 63.", "Kenji's retirement age is 63.", V, "everything matches"),
    # ---- held-out paraphrases of TRUE statements (must still verify; a verifier that only accepts identical sentences scores false negatives here) ----
    AdvCase("p1-retire-at", "paraphrase", "Kenji will retire at 63.", "Kenji's retirement age is 63.", V, "same fact, different wording"),
    AdvCase("p2-number-words", "paraphrase", "Cash covers fifteen months of expenses.", "Available cash covers 15 months of expenses.", V, "fifteen = 15"),
    AdvCase("p3-reordered", "paraphrase", "The target for the emergency fund is six months.", "The emergency fund target is 6 months.", V, "same fact, reordered"),
    AdvCase("p4-percent-word", "paraphrase", "Equities make up 35 percent of the allocation.", "The allocation to equities is 35%.", V, "percent = %"),
    AdvCase("p5-decided", "paraphrase", "Kenji decided on retiring at 63.", "Kenji has decided to retire at 63.", V, "both are decisions"),
    AdvCase("p6-implied-unit", "paraphrase", "Kenji's retirement age is 63 years.", "The retirement age is 63.", V, "a retirement age is in years; the single client is the subject"),
    AdvCase("p7-equivalent-durations", "paraphrase", "Available cash covers 1 year of expenses.", "Available cash covers 12 months of expenses.", V, "1 year = 12 months"),
    # ---- held-out variants of FALSE statements (must stay unverified) -----------------------------------------------------------------------------
    AdvCase("n1-contraction", "held_out", "Kenji is retiring at 63.", "Kenji isn't retiring at 63.", N, "isn't = is not"),
    AdvCase("n2-never", "held_out", "Kenji will retire at 63.", "Kenji will never retire at 63.", N, "never"),
    AdvCase("n3-no-longer", "held_out", "Kenji plans to retire at 63.", "Kenji no longer plans to retire at 63.", N, "no longer"),
    AdvCase("n4-decimal-shift", "held_out", "Available cash covers 1.5 months.", "Available cash covers 15 months.", N, "1.5 is not 15"),
    AdvCase("n5-percent-vs-duration", "held_out", "The allocation is 8 months.", "The allocation is 8%.", N, "months are not percent"),
    AdvCase("n6-hypothetical", "held_out", "Kenji will retire at 60.", "If markets fall, Kenji would retire at 60.", N, "a hypothetical is not a plan"),
    AdvCase("n7-uncertain", "held_out", "Kenji retires at 63.", "Kenji may retire at 63, but nothing is settled.", N, "nothing is settled"),
    AdvCase("n8-evidence-names-other", "held_out", "Kenji's retirement age is 63.", "Rebecca's retirement age is 63.", N, "the quoted sentence is about Rebecca"),
    AdvCase("n9-different-year", "held_out", "Kenji will retire at 63 in 2030.", "Kenji will retire at 63 in 2028.", N, "2030 is not 2028"),
    AdvCase("n10-claim-exception", "held_out", "Kenji's retirement age is 63 except for 2025.", "Kenji's retirement age is 63.", N, "the claim carries an exception the evidence does not"),
    AdvCase("n11-evidence-condition", "held_out", "Kenji's retirement age is 63.", "Kenji's retirement age is 63 unless the sale closes.", N, "the evidence is conditional"),
    AdvCase("n12-extra-figure", "held_out", "Kenji's retirement age is 63 and his monthly expenses are 5 thousand.", "Kenji's retirement age is 63.", N, "the second figure is unsupported"),
    AdvCase("n13-off-by-one", "held_out", "Kenji's retirement age is 62.", "Kenji's retirement age is 63.", N, "62 is not 63"),
    # ---- structural -------------------------------------------------------------------------------------------------------------------------------
    AdvCase("s1-no-evidence-client", "structural", "Kenji's retirement age is 63.", "Kenji's retirement age is 63.", N, "evidence without an owner proves nothing", evidence_client=None),
    AdvCase("s2-ambiguous-subject", "structural", "The retirement age is 63.", "Kenji's retirement age is 63.", N, "two clients in the report: whose age?", plan=(KENJI, REBECCA)),
    AdvCase("s3-fabricated-quote", "structural", "Kenji's retirement age is 63.", "Kenji's retirement age is 63.", N, "the quotation is not in the original source", kind="fabricated_quote"),
    AdvCase("s4-description-is-not-proof", "structural", "Kenji's retirement age is 63.", "Kenji's retirement age is 60.", N, "only the quotation counts; the description claims 63", kind="description_only"),
    # ---- typed tool values --------------------------------------------------------------------------------------------------------------------------
    AdvCase("t1-runway-true", "tool", "Liquidity as of 2026-06-30: cash covers 15 months of expenses.", "", V, "matches cash_months", kind="tool", tool_data={"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0}),
    AdvCase("t2-runway-wrong", "tool", "Liquidity as of 2026-06-30: cash covers 12 months of expenses.", "", N, "12 is not 15", kind="tool", tool_data={"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0}),
    AdvCase("t3-target-true", "tool", "Liquidity as of 2026-06-30: the liquidity target is 6 months.", "", V, "matches target_months", kind="tool", tool_data={"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0}),
    AdvCase("t4-target-wrong", "tool", "Liquidity as of 2026-06-30: the liquidity target is 9 months.", "", N, "9 is not 6", kind="tool", tool_data={"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0}),
    AdvCase("t5-right-number-wrong-field", "tool", "Liquidity as of 2026-06-30: the liquidity target is 15 months.", "", N, "15 is the cash figure, not the target", kind="tool", tool_data={"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0}),
    AdvCase("t6-wrong-unit", "tool", "Liquidity as of 2026-06-30: cash covers 15 years of expenses.", "", N, "months, not years", kind="tool", tool_data={"cash_months": 15.0, "target_months": 6, "shortfall_amount": 0}),
    # ---- round 1 of UNSEEN phrasings (written after the extractor was frozen for that round).  On first measurement the structured verifier made 1 false positive (a list of alternatives
    # "63, 65 or 67 depending on the market") and 1 false negative (the label form "Retirement age: 63"); both were extraction bugs, fixed, and these cases are now regression fixtures.
    AdvCase("u01-cash-12-vs-15", "unseen_r1", "Kenji has 12 months of cash.", "Kenji has 15 months of cash on hand.", N, "12 is not 15"),
    AdvCase("u02-equity-vs-bond", "unseen_r1", "Kenji's equity allocation is 60%.", "Kenji's bond allocation is 60%.", N, "a different asset class"),
    AdvCase("u03-years-old", "unseen_r1", "Kenji will retire at 60 years old.", "Kenji will retire at 60.", V, "same fact"),
    AdvCase("u04-income-10x", "unseen_r1", "Kenji earns $10,000 a month.", "Kenji earns $1,000 a month.", N, "ten times too much"),
    AdvCase("u05-450k-vs-45k", "unseen_r1", "Kenji's mortgage balance is 450k.", "Kenji's mortgage balance is 45k.", N, "450,000 is not 45,000"),
    AdvCase("u06-months-vs-weeks", "unseen_r1", "The emergency fund covers 6 months.", "The emergency fund covers 6 weeks.", N, "weeks are not months"),
    AdvCase("u07-prefers-vs-will", "unseen_r1", "Kenji prefers to retire at 63.", "Kenji will retire at 63.", N, "the claim is weaker than the evidence: not presented as verified"),
    AdvCase("u08-true-with-contrast", "unseen_r1", "Kenji's retirement age is 63, not 65.", "Kenji's retirement age is 63.", N, "unclear negation scope: conservative"),
    AdvCase("u09-wants-vs-will", "unseen_r1", "Kenji will retire at 60.", "Kenji said he wants to retire at 60.", N, "a stated wish is not a plan"),
    AdvCase("u10-might-vs-will", "unseen_r1", "Kenji will retire at 60.", "Kenji might retire at 60.", N, "might"),
    AdvCase("u11-does-not-plan", "unseen_r1", "Kenji plans to retire at 65.", "Kenji does not plan to retire at 65.", N, "negated"),
    AdvCase("u12-label-form", "unseen_r1", "Kenji's retirement age is 63.", "Retirement age: 63", V, "a label before the value names its topic"),
    AdvCase("u13-around", "unseen_r1", "Kenji will retire at 63.", "He is planning on retiring around sixty-three.", N, "approximate evidence, plain claim"),
    AdvCase("u14-number-words", "unseen_r1", "Kenji's retirement age is 63.", "Kenji's retirement age is sixty-three.", V, "sixty-three = 63"),
    AdvCase("u15-savings", "unseen_r1", "Kenji saves 20% of his income.", "Kenji saves 20% of his income.", V, "identical"),
    AdvCase("u16-savings-other-client", "unseen_r1", "Kenji saves 20% of his income.", "Rebecca saves 20% of her income.", N, "the evidence names Rebecca"),
    AdvCase("u17-set-of-values", "unseen_r1", "Kenji's retirement age is 63.", "Kenji's retirement age is 63, 65 or 67 depending on the market.", N, "alternatives with a condition"),
    AdvCase("u18-currency", "unseen_r1", "Kenji owes $5,000 on his card.", "Kenji owes Rs 5,000 on his card.", N, "dollars are not rupees"),
    AdvCase("u19-historical-claim", "unseen_r1", "Kenji's retirement age was 60 until 2025.", "Kenji's retirement age is 60.", N, "the claim is about the past"),
    AdvCase("u20-extra-date", "unseen_r1", "Kenji will retire at 63 in 2031.", "Kenji will retire at 63.", N, "the claim adds a date the evidence lacks"),
]


class _Conv:
    def __init__(self) -> None:
        self.turns: dict = {}

    def add(self, client: Optional[str], text: str) -> int:
        idx = len(self.turns) + 1
        self.turns[("INT-ADV", idx)] = dict(client_id=client, speaker="Client", utterance=text)
        return idx

    def turn(self, iid, idx):
        return self.turns.get((iid, idx))

    def verify_excerpt(self, client_id, iid, idx, excerpt):
        t = self.turns.get((iid, idx))
        return bool(t) and t["client_id"] == client_id and " ".join(excerpt.lower().split()) in " ".join(t["utterance"].lower().split())


def _build(case: AdvCase):
    conv, tools = _Conv(), ToolSelectorState(request_id="ADV")
    if case.kind == "tool":
        env = Envelope(tool="calculate_liquidity_ratio", client_id=KENJI, as_of="2026-06-30", data=case.tool_data, provenance=Provenance(source="fixture"))
        tools.executions.append(ToolExecution(id="EX-ADV", iteration=1, tool=env.tool, arguments={"client_id": KENJI}, status="success", result=env))
        item = EvidenceItem(ref="E1", source_class="tool_calculation", description="calculate_liquidity_ratio as of 2026-06-30", tool_execution_id="EX-ADV", synthetic=True, client_id=KENJI)
        return conv, tools, {"E1": item}, ["E1"], "financial_analysis"
    if case.kind == "missing_handle":
        return conv, tools, {}, ["E9"], "key_findings"
    source = "We talked about the weather and lunch today." if case.kind == "fabricated_quote" else case.evidence
    idx = conv.add(case.evidence_client, source)
    desc = "Current knowledge; retirement_age; value 63" if case.kind == "description_only" else "Episode; " + case.evidence
    item = EvidenceItem(ref="E1", source_class="conversation_evidence", description=desc, interaction_id="INT-ADV", turn_index=idx, excerpt=case.evidence, client_id=case.evidence_client,
                        memory_type="episodic")
    return conv, tools, {"E1": item}, ["E1"], "key_findings"


def present(case: AdvCase, mode: str) -> dict:
    """What the system under test does with this claim.  mode: none | lexical | structured."""
    if mode == "none":
        return dict(presented_as_verified=True, label="presented", outcome=None, reasons=[])
    conv, tools, evidence, refs, section = _build(case)
    owns = (lambda cid: False) if case.kind == "inaccessible" else None
    v = EvidenceVerifier(conv, None, owns=owns, structured=(mode == "structured")).verify_finding(section, Finding(text=case.claim, refs=refs), evidence, set(case.plan), tools)
    return dict(presented_as_verified=v.verdict == "verified", label=v.verdict, outcome=v.outcome, reasons=list(v.reason_codes))


def run(mode: str) -> dict:
    rows, fp, fn = [], 0, 0
    for c in CASES:
        r = present(c, mode)
        actual = V if r["presented_as_verified"] else N
        fp += c.expected == N and actual == V
        fn += c.expected == V and actual == N
        rows.append(dict(id=c.id, group=c.group, claim=c.claim, evidence=c.evidence or "(tool result / none)", expected=c.expected, actual=actual, correct=actual == c.expected, label=r["label"],
                         outcome=r["outcome"], reasons=r["reasons"], why=c.why))
    n_neg, n_pos = sum(c.expected == N for c in CASES), sum(c.expected == V for c in CASES)
    return dict(mode=mode, cases=len(CASES), should_reject=n_neg, should_verify=n_pos, false_positive_verifications=fp, false_negative_verifications=fn,
                correct=sum(r["correct"] for r in rows), rows=rows)
