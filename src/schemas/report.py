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
    origin: SkipJsonSchema[Optional[Literal["rendered", "tool_code", "narrative_checked"]]] = None
    # "narrative_checked" = MODEL-written prose that code validated sentence by sentence against the timeline claims it cites.  It is NOT code-rendered: the verifier re-derives the timeline from the
    # source evidence itself and judges every sentence in full (harness/evidence_verifier.py, _narrative).  `narrative` holds those sentences; both fields are set only by the Report Agent.
    narrative: SkipJsonSchema[Optional[list[NarrativeSentence]]] = None


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


class NarrativeLLMOutput(BaseModel):
    """Output of the dedicated narrative call (Report Agent, second call): the chronology organised into paragraphs.  Everything is required; nothing here reaches the report unless code and the
    EvidenceVerifier both accept it sentence by sentence."""
    paragraphs: list[NarrativeParagraphOut] = Field(..., min_length=1, max_length=4)


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
