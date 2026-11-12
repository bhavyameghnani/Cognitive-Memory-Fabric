"""P1.5 labelled claim corpus.  EVERY label is hand-written below from what the sentences MEAN ("63 months is not 63 years"), never computed by any verifier.

Authorship disclosure (an evaluation-bias note, see docs): the labels were written by the same engineer-agent that built the systems under test, in one sitting, after
reading the P1.4 audit findings.  They are therefore NOT independent in the sense of a second human reviewer, and the cases partly mirror known failure modes.  The corpus is
synthetic one-sentence evidence, not conversation transcripts, and it is small.  Treat the numbers as regression evidence for a deterministic component, not as a
measure of production accuracy.

Label convention: `expected` is V when the claim is TRUE and SUPPORTED by the evidence sentence (a verifier should present it as verified) and N otherwise.  A faithful
paraphrase is therefore labelled V even though a deterministic matcher is expected to miss some of them: the miss is reported as a false negative, which is the honest cost of
refusing to loosen matching.
"""
from __future__ import annotations

from dataclasses import dataclass

V, N = "VERIFIED", "NOT_VERIFIED"
K, R, S, G, L, P = "CLIENT-001", "CLIENT-002", "CLIENT-003", "CLIENT-004", "CLIENT-005", "CLIENT-006"
NAME = {K: "Kenji", R: "Rebecca", S: "Sofia", G: "Grace", L: "Lucas", P: "Peg"}


@dataclass(frozen=True)
class Case15:
    id: str
    category: str            # negation | wrong_client | numeric_unit | paraphrase | temporal | modality
    client: str              # the client the claim is about
    claim: str
    evidence: str
    value: str               # the value the claim extracts ("" = none)
    expected: str            # V | N
    why: str                 # expected reason for pass/fail
    evidence_client: str = ""   # owner of the evidence ("" = same as `client`; "-" = no owner)
    open_item: bool = False     # the memory is stated as exploratory / hypothetical (a hedged number is then its legitimate value)
    contradiction: bool = False  # the evidence contradicts the claim (for the contradiction-detection rate)
    ambiguity: bool = False      # the evidence is ambiguous / insufficient (for the ambiguity-detection rate)

    @property
    def owner(self) -> str:
        return self.client if not self.evidence_client else ("" if self.evidence_client == "-" else self.evidence_client)

    @property
    def expected_security(self) -> str:
        return "evidence owned by another client is rejected before any matching" if self.owner != self.client else "client scope unchanged; evidence owned by the claim's client"

    @property
    def expected_report(self) -> str:
        return "shown as verified with its citation" if self.expected == V else "withheld or qualified; never shown as verified"


def C(id, cat, client, claim, evidence, value, exp, why, **kw):
    return Case15(id, cat, client, claim, evidence, value, exp, why, **kw)


CORPUS: list[Case15] = [
    # ---- A. negation --------------------------------------------------------------------------------------------------------------------------------
    C("a01", "negation", K, "Kenji will retire at 63.", "Kenji will retire at 63.", "63", V, "identical positive statement"),
    C("a02", "negation", K, "Kenji will retire at 63.", "Kenji will not retire at 63.", "63", N, "the evidence denies the claim", contradiction=True),
    C("a03", "negation", K, "Kenji has decided when to retire.", "Kenji has not decided whether to retire.", "", N, "undecided is not decided", contradiction=True),
    C("a04", "negation", K, "Kenji plans to retire at 63.", "Kenji previously considered retiring at 63 but rejected the idea.", "63", N, "the idea was rejected", contradiction=True),
    C("a05", "negation", R, "Rebecca is selling the business at 3 million.", "Rebecca isn't selling the business at 3 million.", "3 million", N, "contraction negation", contradiction=True),
    C("a06", "negation", S, "Sofia will retire at 62.", "Sofia will never retire at 62.", "62", N, "never", contradiction=True),
    C("a07", "negation", K, "Kenji does not plan to retire at 63.", "Kenji does not plan to retire at 63.", "63", V, "a negated claim supported by a negated source"),
    C("a08", "negation", G, "Grace does not plan to retire at 65.", "Grace plans to retire at 65.", "65", N, "the claim negates what the evidence asserts", contradiction=True),
    C("a09", "negation", L, "Lucas no longer plans to retire at 60.", "Lucas no longer plans to retire at 60.", "60", V, "negated both sides"),
    C("a10", "negation", P, "Peg has decided to retire at 64.", "Peg has not decided to retire at 64 yet.", "64", N, "not yet decided", contradiction=True),
    # ---- B. wrong-client evidence --------------------------------------------------------------------------------------------------------------------
    C("b01", "wrong_client", K, "Kenji's retirement age is 63.", "Kenji's retirement age is 63.", "63", N, "correct sentence, but it belongs to Rebecca's record", evidence_client=R),
    C("b02", "wrong_client", R, "Rebecca's retirement age is 60.", "Rebecca's retirement age is 60.", "60", V, "own evidence"),
    C("b03", "wrong_client", R, "Rebecca's retirement age is 60.", "Rebecca's retirement age is 60.", "60", N, "correct sentence, but it belongs to Kenji's record", evidence_client=K),
    C("b04", "wrong_client", S, "Sofia's retirement age is 62.", "Sofia's retirement age is 62.", "62", N, "evidence with no owner proves nothing", evidence_client="-"),
    C("b05", "wrong_client", G, "Grace's retirement age is 65.", "Grace's retirement age is 65.", "65", V, "own evidence"),
    C("b06", "wrong_client", P, "Peg's retirement age is 64.", "Peg's retirement age is 64.", "64", N, "evidence belongs to Lucas", evidence_client=L),
    C("b07", "wrong_client", L, "Lucas's retirement age is 60.", "Lucas's retirement age is 60.", "60", V, "own evidence"),
    C("b08", "wrong_client", K, "Kenji's retirement age is 63.", "Rebecca's retirement age is 63.", "63", N, "the quoted sentence is about a different person", evidence_client=R, contradiction=True),
    # ---- C. numerical and unit ambiguity ---------------------------------------------------------------------------------------------------------------
    C("c01", "numeric_unit", K, "Kenji will retire after 63 years of work.", "Kenji will retire after 63 months of work.", "63 years", N, "years are not months", contradiction=True),
    C("c02", "numeric_unit", K, "Kenji's equity allocation is 63 percent.", "Kenji's retirement age is 63.", "63 percent", N, "an age is not a percentage", contradiction=True),
    C("c03", "numeric_unit", R, "Rebecca's mortgage balance is 63 lakh rupees.", "Rebecca's mortgage balance is 63 crore rupees.", "63 lakh", N, "a lakh is not a crore", contradiction=True),
    C("c04", "numeric_unit", S, "Sofia earns $10,000 a month.", "Sofia earns $10,000 a year.", "$10,000", N, "monthly is not annual", contradiction=True),
    C("c05", "numeric_unit", S, "Sofia earns $120,000 a year.", "Sofia earns $10,000 a month.", "$120,000", N, "the conversion is not stated in the evidence", ambiguity=True),
    C("c06", "numeric_unit", K, "Kenji's portfolio is worth 2 million dollars.", "Kenji's portfolio was worth 2 million dollars in 2019.", "2 million dollars", N, "a historical value is not the current value", contradiction=True),
    C("c07", "numeric_unit", K, "Kenji's portfolio is worth 2 million dollars.", "Kenji's portfolio is worth 2 million dollars.", "2 million dollars", V, "identical"),
    C("c08", "numeric_unit", K, "Kenji will retire at 62.", "Kenji's wife Mariko wants to teach until 62.", "62", N, "the number belongs to a different person and purpose", contradiction=True),
    C("c09", "numeric_unit", G, "Grace will retire at 60.", "In the rejected plan Grace would retire at 60.", "60", N, "a rejected plan", contradiction=True),
    C("c10", "numeric_unit", G, "Grace will retire at 60.", "In a hypothetical scenario Grace would retire at 60.", "60", N, "a hypothetical plan", contradiction=True),
    C("c11", "numeric_unit", L, "Lucas's cash covers 15 months of expenses.", "Lucas's cash covers 15 months of expenses.", "15 months", V, "identical"),
    C("c12", "numeric_unit", L, "Lucas's cash covers 1.5 months of expenses.", "Lucas's cash covers 15 months of expenses.", "1.5 months", N, "1.5 is not 15", contradiction=True),
    C("c13", "numeric_unit", P, "Peg's monthly rent is 63,000 rupees.", "Peg's monthly rent is 63,000 rupees.", "63,000 rupees", V, "identical, with currency"),
    C("c14", "numeric_unit", P, "Peg's fund is worth $220 thousand.", "Peg's fund is worth $220 million.", "$220 thousand", N, "thousand is not million", contradiction=True),
    C("c15", "numeric_unit", R, "Rebecca's care reserve is 6 lakh rupees.", "Rebecca's care reserve is 6 lakh rupees.", "6 lakh rupees", V, "identical, Indian units"),
    C("c16", "numeric_unit", R, "Rebecca's cash covers 18 months.", "Rebecca's cash covers 18 weeks.", "18 months", N, "weeks are not months", contradiction=True),
    # ---- D. paraphrased evidence -----------------------------------------------------------------------------------------------------------------------
    C("d01", "paraphrase", K, "Kenji will retire at 63.", "Kenji will retire at 63.", "63", V, "exact"),
    C("d02", "paraphrase", K, "Kenji retires at 63.", "Kenji will retire at 63.", "63", V, "minor grammatical variation"),
    C("d03", "paraphrase", K, "Kenji's retirement age is sixty-three.", "Kenji's retirement age is 63.", "63", V, "number words"),
    C("d04", "paraphrase", K, "Kenji plans to stop working at 63.", "Kenji will retire at 63.", "63", V, "major paraphrase of a true statement (a deterministic matcher is expected to miss it)"),
    C("d05", "paraphrase", K, "Kenji hopes to retire at 63.", "Kenji will retire at 63.", "63", N, "weaker than the evidence: a hope is not a plan"),
    C("d06", "paraphrase", K, "Kenji will retire at 65.", "Kenji will retire at 63.", "65", N, "similar but unsupported number", contradiction=True),
    C("d07", "paraphrase", K, "Kenji will retire at 63 and then travel.", "Kenji will retire at 63.", "63", N, "an unsupported addition"),
    C("d08", "paraphrase", K, "At 63 Kenji will retire.", "Kenji will retire at 63.", "63", V, "reordered"),
    C("d09", "paraphrase", K, "Kenji is going to retire at 63.", "Kenji will retire at 63.", "63", V, "faithful paraphrase"),
    C("d10", "paraphrase", K, "Kenji's savings will fund a retirement at 63.", "Kenji will retire at 63.", "63", N, "unsupported causal addition"),
    C("d11", "paraphrase", K, "The client intends to leave work at 63.", "Kenji will retire at 63.", "63", V, "major paraphrase, same client (expected to be missed)"),
    C("d12", "paraphrase", K, "Kenji will retire at 63 with a full pension.", "Kenji will retire at 63.", "63", N, "unsupported addition"),
    C("d13", "paraphrase", G, "Grace will retire at age 65.", "Grace will retire at 65.", "65", V, "minor variation"),
    C("d14", "paraphrase", G, "Grace will retire at 65 in 2031.", "Grace will retire at 65.", "65", N, "adds a date the evidence lacks"),
    # ---- E. temporal contradictions --------------------------------------------------------------------------------------------------------------------
    C("e01", "temporal", K, "Kenji's current retirement age is 63.", "As of 2026-05-01 Kenji's retirement age is 63.", "63", V, "current and dated"),
    C("e02", "temporal", K, "Kenji's current retirement age is 65.", "Kenji's retirement age was 65 before 2026-05-01.", "65", N, "a replaced value is not the current value", contradiction=True),
    C("e03", "temporal", K, "Kenji's retirement age was 65 until 2026-05-01.", "Kenji's retirement age was 65 until 2026-05-01.", "65", V, "a historical claim from historical evidence"),
    C("e04", "temporal", K, "Kenji's retirement age is 60.", "Kenji replaced the earlier plan of 60 with 63.", "60", N, "60 is the replaced plan", contradiction=True),
    C("e05", "temporal", K, "Kenji's retirement age is 63.", "Kenji replaced the earlier plan of 60 with 63.", "63", V, "63 is the replacing value (a deterministic matcher may withhold this)"),
    C("e06", "temporal", R, "Rebecca will sell on 2026-05-01 for 3 million.", "On 2026-05-01 Rebecca said 3 million and also said 4 million.", "3 million", N, "two conflicting statements on the same date", ambiguity=True),
    C("e07", "temporal", S, "Sofia's retirement age is 60.", "I corrected my record: Sofia's retirement age is not 60.", "60", N, "a corrected-away value", contradiction=True),
    C("e08", "temporal", S, "Sofia's retirement age is 62.", "I corrected my record: Sofia's retirement age is 62, not 60.", "62", V, "the corrected value (contrast wording; a matcher may withhold)"),
    C("e09", "temporal", K, "Kenji's portfolio is worth 1.2 million dollars.", "In 2019 Kenji's portfolio was worth 1.2 million dollars.", "1.2 million dollars", N, "historical value as current", contradiction=True),
    C("e10", "temporal", L, "Lucas will retire at 60.", "Lucas will retire at 60.", "60", V, "no temporal marker: nothing contradicts it"),
    # ---- F. modality and uncertainty -------------------------------------------------------------------------------------------------------------------
    C("f01", "modality", K, "Kenji has retired at 63.", "Kenji plans to retire at 63.", "63", N, "planned is not completed", contradiction=True),
    C("f02", "modality", K, "Kenji will retire at 63.", "Kenji proposes to retire at 63, subject to approval.", "63", N, "proposed is not decided"),
    C("f03", "modality", K, "Kenji will retire at 63.", "Suppose Kenji retired at 63.", "63", N, "hypothetical"),
    C("f04", "modality", K, "Kenji decided to retire at 63.", "Kenji is exploring retiring at 63.", "63", N, "exploratory is not a commitment"),
    C("f05", "modality", K, "Kenji rejected retiring at 63.", "Kenji rejected retiring at 63.", "63", V, "rejected, stated as rejected"),
    C("f06", "modality", K, "Kenji will retire at 63.", "Kenji will retire at 63 if the sale closes.", "63", N, "conditional"),
    C("f07", "modality", K, "Kenji will retire at 63.", "It is unknown whether Kenji will retire at 63.", "63", N, "unknown", ambiguity=True),
    C("f08", "modality", K, "Kenji is exploring retiring at 63.", "Kenji is exploring retiring at 63.", "63", V, "exploratory stated as exploratory", open_item=True),
    C("f09", "modality", K, "Kenji is considering retiring at 63.", "Kenji is exploring retiring at 63.", "63", V, "a faithful restatement of an exploration (a matcher may withhold)", open_item=True),
    C("f10", "modality", K, "Kenji will retire at 63.", "Kenji wants to retire at 63.", "63", N, "a wish is not a commitment"),
    C("f11", "modality", R, "Rebecca will sell the business.", "Rebecca might sell the business.", "", N, "might"),
    C("f12", "modality", R, "Rebecca is still weighing the offer.", "Rebecca is still weighing the offer.", "", V, "unresolved stated as unresolved", open_item=True),
]

CATEGORIES = ("negation", "wrong_client", "numeric_unit", "paraphrase", "temporal", "modality")
assert len({c.id for c in CORPUS}) == len(CORPUS)
