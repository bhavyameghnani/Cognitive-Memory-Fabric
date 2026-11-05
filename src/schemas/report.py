"""Report Agent contracts (docs/agent_prompts/report_agent.md, section B)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SourceClass = Literal["conversation_evidence", "memory", "operational_data", "tool_calculation", "assumption"]
ClaimStatus = Literal["current", "superseded", "exploratory", "recommendation"]


class Finding(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)


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


class AdvisorReport(BaseModel):
    request_id: str
    client_id: str
    question: str
    summary: str
    key_findings: list[Finding] = Field(default_factory=list)
    historical_context: list[Finding] = Field(default_factory=list)
    financial_analysis: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    suggested_followups: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
