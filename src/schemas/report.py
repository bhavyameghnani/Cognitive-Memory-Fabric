"""Report Agent contracts (docs/agent_prompts/report_agent.md, section B)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

SourceClass = Literal["conversation_evidence", "memory", "operational_data", "tool_calculation", "assumption"]
ClaimStatus = Literal["current", "superseded", "exploratory", "recommendation"]


class NarrativeSentence(BaseModel):
    """Follow-up to B4: one sentence of a checked narrative as CODE recorded it: the model's own words (quotation marks kept) and the source turns it says it rests on, each written "INT-049#4".  The
    verifier re-derives the claims at those turns from the report's evidence and judges the sentence against them; the claim ids the model used are never part of the report."""
    text: str
    sources: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)
    # Provenance, set ONLY by code in the report package: "rendered" = prose assembled from the cited evidence item's own fields, "tool_code" = figures formatted from a tool result.
    # None = free text written by a model.  Hidden from the JSON schema the model sees and stripped from model output (ReportLLMOutput), so a model cannot claim code provenance.
    origin: SkipJsonSchema[Optional[Literal["rendered", "tool_code", "narrative_checked", "synthesis_checked"]]] = None
    # "narrative_checked" = MODEL-written prose that code validated sentence by sentence against the timeline claims it cites.  It is NOT code-rendered: the verifier re-derives the timeline from the
    # source evidence itself and judges every sentence in full (harness/evidence_verifier.py, _narrative).  `narrative` holds those sentences; both fields are set only by the Report Agent.
    narrative: SkipJsonSchema[Optional[list[NarrativeSentence]]] = None
    # B (Phase 2): the narrative window this paragraph was written for (0-based), set only by the Report Agent; None for every other finding.
    window: SkipJsonSchema[Optional[int]] = None


class EvidenceItem(BaseModel):
    ref: str  # E1, E2, ...
    source_class: SourceClass
    description: str
    interaction_id: Optional[str] = None
    turn_index: Optional[int] = None
    memory_id: Optional[str] = None
    tool_execution_id: Optional[str] = None
    excerpt: Optional[str] = None
    synthetic: bool = False
    # carried so the claim validator can check meaning, not only citations
    topic: Optional[str] = None
    value: Optional[str] = None
    certainty: Optional[str] = None
    status: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    client_id: Optional[str] = None
    memory_type: Optional[str] = None
    event_date: Optional[str] = None
    # P1.4.3: the memory integrity boundary's verdict, carried to the report.  evidence_class is what the report may DO with the item:
    #   verified | unverified | contradicted | ambiguous | open_conflict   (None for tool results, which are not memories)
    verification: Optional[str] = None
    verification_basis: Optional[str] = None
    memory_source_class: Optional[str] = None
    temporal_status: Optional[str] = None
    evidence_class: Optional[str] = None
    conflict_ids: list[str] = Field(default_factory=list)
    # retrieved source conversation turn (get_source_evidence, conversation mode): who is RECORDED as speaking and where the record came from, exactly as the tool returned them
    speaker: Optional[str] = None
    provenance: Optional[str] = None
    # B2B (additive, optional): what the timeline needs to classify a memory without re-reading the store.  Copied from the memory item, never derived.
    source_role: Optional[str] = None       # who the memory records as the speaker of its primary evidence: Client | Advisor | System
    subject: Optional[str] = None           # the slot subject (with `topic`, the memory's slot)
    corrects_value: Optional[str] = None    # the value this memory says it corrects


class Claim(BaseModel):
    """Structured claim emitted by the model; code validates it before any prose is rendered."""
    subject: str
    topic: str
    value: Optional[str] = None
    status: ClaimStatus
    evidence_ref: str
    text: str = Field(..., min_length=3, max_length=400)


class ReportLLMOutput(BaseModel):
    """The model's (smaller) output schema; everything else is assembled by code."""
    summary: str = Field(..., max_length=600)
    key_findings: list[Finding] = Field(default_factory=list, max_length=8)
    historical_context: list[Finding] = Field(default_factory=list, max_length=8)
    financial_analysis: list[Finding] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=6)
    suggested_followups: list[str] = Field(default_factory=list, max_length=5)
    claims: list[Claim] = Field(default_factory=list, max_length=10)

    @field_validator("key_findings", "historical_context", "financial_analysis", mode="after")
    @classmethod
    def _no_model_supplied_provenance(cls, v: list[Finding]) -> list[Finding]:
        return [Finding(text=f.text, refs=f.refs) for f in v]


TemporalStatus = Literal["current", "historical", "corrected", "superseded", "disputed", "unresolved"]


class TimelineClaim(BaseModel):
    """B2B: one dated claim of the chronology, with its classification, provenance and relations (built by code, agents/report/timeline.py; a model never writes one).

    `source` says what the claim RESTS on: "source_turn" = an actual retrieved conversation turn (its wording is real text and may be stated as what was said); "memory" = only a stored memory
    whose own excerpt was not retrieved as a turn (never stated as fact: it is reported as unverified).  `wording` is always the ORIGINAL wording, never a paraphrase."""
    claim_id: str
    source: Literal["source_turn", "memory"]
    topic: Optional[str] = None
    subject: Optional[str] = None
    value: Optional[str] = None
    value_source: Optional[Literal["memory"]] = None         # a value is only ever taken from a memory record, never guessed from wording
    wording: str
    speaker: Optional[str] = None
    speaker_role: Optional[str] = None
    attribution: str = "ambiguous"                           # agents.memory.attribution: client_direct | advisor_reported_client | advisor_interpretation | system_inference | ambiguous
    statement_kind: str = "unattributed"                     # client_statement | advisor_reported | advisor_interpretation | unattributed
    modality: str = "statement"                              # decision | tentative | hypothetical | recommendation | correction | statement
    cues: list[str] = Field(default_factory=list)            # wording cues seen in the text: exploratory, hypothetical, unconfirmed, decision, correction, recommendation, not_final
    confirmed_client_decision: bool = False                  # True ONLY for a verified, confirmed memory whose evidence is client-attributable (M1) - never from wording alone
    client_confirmation: Optional[str] = None                # "INT-001#14": the client's own immediate reply that confirms an advisor-reported statement (retrieved turns only)
    verification: str = "no_memory"                          # verified | unverified | no_memory (no stored memory describes this turn)
    memory_certainty: Optional[str] = None
    evidence_class: Optional[str] = None
    interaction_id: Optional[str] = None
    turn_index: Optional[int] = None
    event_date: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)   # report evidence handles (E-numbers) the claim rests on
    memory_refs: list[str] = Field(default_factory=list)     # memory ids that describe the same turn
    temporal_status: TemporalStatus = "historical"
    relevant: bool = True                                    # False: a turn of a meeting that is not about the plan's topic (kept in the timeline, not narrated)
    mentions_topic: Optional[bool] = None                    # does the wording itself mention the plan's topic?  None when the plan names no topic
    relations: list[dict] = Field(default_factory=list)      # {"type": corrects | corrected_by | contradicts, "claim_id": ...}
    notes: list[str] = Field(default_factory=list)


class NarrativeSentenceOut(BaseModel):
    """What the narrative call returns for one sentence: the words, and the timeline claim ids it rests on (a JSON list, so a small model cannot forget a marker)."""
    text: str = Field(..., min_length=3, max_length=420)
    claims: list[str] = Field(..., min_length=1, max_length=4)


class NarrativeParagraphOut(BaseModel):
    sentences: list[NarrativeSentenceOut] = Field(..., min_length=1, max_length=6)


class StoryLineOut(BaseModel):
    """One line of the readable story: the one or two timeline claims it paraphrases (listed first) and ONE plain sentence saying what they say.  One claim per sentence keeps it checkable."""
    claims: list[str] = Field(..., min_length=1, max_length=2)
    sentence: str = Field(..., min_length=3, max_length=260)


class StoryLLMOutput(BaseModel):
    """Output of the story call: the chronology as a sequence of single-claim sentences, oldest first.  Code groups them into paragraphs by meeting.  Model-written and unverified (agents/report/semantic.py)."""
    lines: list[StoryLineOut] = Field(..., min_length=1, max_length=14)


class ClauseOut(BaseModel):
    """What ONE source line says, as one plain sentence that opens with the speaker.  Code adds the date, orders the lines by the record and validates every sentence against that one line."""
    claim: str = Field(..., min_length=2, max_length=12)
    sentence: str = Field(..., min_length=3, max_length=360)


class ClausesLLMOutput(BaseModel):
    """Output of one synthesis window: one sentence for each source line that matters (agents/report/synthesis.py)."""
    lines: list[ClauseOut] = Field(..., min_length=1, max_length=20)


class SynthesisSentenceOut(BaseModel):
    """One paraphrased proposition: a sentence in the model's own words and the one to three source claims it rests on.  Code validates it for factual support (agents/report/synthesis.py)."""
    text: str = Field(..., min_length=3, max_length=420)
    claims: list[str] = Field(..., min_length=1, max_length=3)


class SynthesisParagraphOut(BaseModel):
    sentences: list[SynthesisSentenceOut] = Field(..., min_length=1, max_length=6)


class SynthesisLLMOutput(BaseModel):
    """Output of one synthesis window (Report Agent): paragraphs of paraphrased propositions.  Nothing here reaches the report unless every sentence passes proposition validation, twice."""
    paragraphs: list[SynthesisParagraphOut] = Field(..., min_length=1, max_length=4)


class SummarySentenceOut(BaseModel):
    text: str = Field(..., min_length=3, max_length=420)
    props: list[str] = Field(..., min_length=1, max_length=8)


class ExecutiveLLMOutput(BaseModel):
    """Output of the executive-summary call: sentences that each name the accepted propositions (P1, P2 ...) they summarise."""
    sentences: list[SummarySentenceOut] = Field(..., min_length=2, max_length=7)


class NarrativeLLMOutput(BaseModel):
    """Output of the dedicated narrative call (Report Agent, second call): the chronology organised into paragraphs.  Everything is required; nothing here reaches the report unless code and the
    EvidenceVerifier both accept it sentence by sentence."""
    paragraphs: list[NarrativeParagraphOut] = Field(..., min_length=1, max_length=4)


class NarrativeWindow(BaseModel):
    """One chronological window of the narrative (about ten relevant source claims) and what became of it.  `status`: ok (every returned sentence accepted), partial (some rejected), empty (the call
    succeeded but no sentence passed the checks), failed (the model call failed or timed out), skipped (no call was made: budget or call cap)."""
    index: int
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    claim_ids: list[str] = Field(default_factory=list)
    context_ids: list[str] = Field(default_factory=list)          # related claims of other windows shown so a correction can name what it corrects
    status: Literal["ok", "partial", "empty", "failed", "skipped"] = "skipped"
    attempts: int = 0
    sentences_returned: int = 0
    sentences_accepted: int = 0
    sentences_rejected: list[dict] = Field(default_factory=list)   # {attempt, paragraph, sentence, problems, claims}: codes and claim ids only, never the rejected text
    quotes_repaired: int = 0
    duration_ms: int = 0
    error: Optional[str] = None
    sentences_withheld: int = 0                                  # accepted by code, then withheld by a later safeguard (see NarrativeCoverage.withheld_reasons)


class NarrativeCoverage(BaseModel):
    """Where every relevant claim stands (Phase 2, B).  `relevant` = retrieved source turns that matter to the chronology (timeline.significant); each is in exactly one of narrated / rejected /
    not_cited / window_failed.  `memory_only` are claims whose source turn was NOT retrieved: they are never narrated and never presented as source-verified."""
    window_size: int = 10
    relevant: int = 0
    narrated: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)       # cited only by sentences that failed a check
    not_cited: list[str] = Field(default_factory=list)      # the window was written but no sentence used this claim
    window_failed: list[str] = Field(default_factory=list)  # the window's model call failed / was skipped: nothing was written for it
    withheld: list[str] = Field(default_factory=list)       # a verified sentence was written, then withheld by a later safeguard (unverified memory evidence, the evidence verifier)
    withheld_reasons: dict[str, str] = Field(default_factory=dict)
    memory_only: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)     # relevant claims the record itself leaves unresolved, disputed or corrected
    model_calls: int = 0
    windows: list[NarrativeWindow] = Field(default_factory=list)


class ReportOverview(BaseModel):
    """D: the code-written opening of a history report: counts and dates from what was actually retrieved and classified (agents/report/overview.py).  No model, no inference: `verified`
    holds established facts, `unresolved` holds what is missing or unestablished; nothing here says what a client wanted, decided or holds now."""
    origin: Literal["code"] = "code"
    client_id: str
    client_name: Optional[str] = None
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    meetings_identified: Optional[int] = None
    meetings_retrieved: Optional[int] = None
    meetings_not_retrieved: Optional[int] = None
    not_retrieved: list[dict] = Field(default_factory=list)
    source_turns: int = 0
    claims: int = 0
    corrections: int = 0
    contradictions: int = 0
    references_unresolved: int = 0
    memory_only_claims: int = 0
    relevant_claims: Optional[int] = None
    narrated_claims: Optional[int] = None
    windows: Optional[int] = None
    windows_incomplete: Optional[int] = None
    narrative_verified: list[str] = Field(default_factory=list)       # what the verified narrative covers (from the coverage record)
    narrative_unresolved: list[str] = Field(default_factory=list)     # what it does not cover
    verified: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class OutcomeStatement(BaseModel):
    """What the retrieved record does and does not establish about signing and completion, written by code from the cited wording (agents/report/synthesis.py: outcome_lines)."""
    established: list[str] = Field(default_factory=list)
    not_established: list[str] = Field(default_factory=list)
    mentions: list[dict] = Field(default_factory=list)       # {claim_id, date, where, speaker, excerpt, kind: hedged|plain, ref}


class AdvisorReport(BaseModel):
    request_id: str
    client_id: str
    question: str
    summary: str
    key_findings: list[Finding] = Field(default_factory=list)
    historical_context: list[Finding] = Field(default_factory=list)
    financial_analysis: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    timeline: list[TimelineClaim] = Field(default_factory=list)       # B2B (additive): the structured chronology the narrative findings were written from
    outcome: Optional[OutcomeStatement] = None                         # Phase 3: signing / completion status, code-written
    coverage: Optional[NarrativeCoverage] = None                       # Phase 2: window-by-window and claim-by-claim account of the verified narrative
    overview: Optional[ReportOverview] = None                          # D: code-written opening (counts and dates only); absent for a request without a chronology
    story: list[Finding] = Field(default_factory=list)                 # B6: model-written readable account, NOT verified (never origin-labelled, never in a findings section); see agents/report/semantic.py
    assumptions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    suggested_followups: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


def is_source_turn(e: "EvidenceItem") -> bool:
    """A retrieved source conversation turn: a conversation_evidence item that came from a tool execution rather than from a stored memory.  It records WHAT WAS SAID, by whom, in which
    synthetic source; it is not a memory, so it can never back a current / superseded / exploratory / recommendation claim."""
    return e.source_class == "conversation_evidence" and bool(e.tool_execution_id) and not e.memory_id
