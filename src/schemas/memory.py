"""Memory Orchestrator contracts (docs/agent_prompts/memory_agent.md, sections A, B, D, E, G)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

MemoryType = Literal["episodic", "semantic", "procedural", "working"]
Certainty = Literal["confirmed", "unconfirmed", "recommendation", "exploratory", "hypothetical", "correction", "n/a"]
Status = Literal["candidate", "active", "superseded", "disputed", "expired", "unconfirmed"]
RelType = Literal["derived_from", "supersedes", "corrects", "explores", "supports", "follows", "related_to"]
SourceRole = Literal["Client", "Advisor", "System"]
Origin = Literal["conversation_llm", "conversation_rule", "consolidation", "curated_file", "tool_summary", "orchestrator"]


class EvidenceRef(BaseModel):
    interaction_id: str
    turn_index: int
    speaker: str
    excerpt: str
    role: str = "primary"


# ---- LLM extraction contract (one call per meeting) ---------------------------------------------------------------
class MemoryCandidate(BaseModel):
    topic: str
    subject: str = Field("", max_length=120, description="short slot subject, e.g. 'retirement_age'")
    statement: str = Field(..., min_length=3, max_length=400, description="one sentence")
    value: Optional[str] = Field(None, max_length=60, description="short normalised value, e.g. '63'")
    certainty: Certainty
    speaker_role: Literal["Client", "Advisor"]
    turn_index: int
    excerpt: str = Field(..., min_length=3, max_length=500)
    corrects_value: Optional[str] = Field(None, max_length=60)
    reusable_procedure: bool = False


class ExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=12)


# ---- stored / returned memory --------------------------------------------------------------------------------------
class MemoryItem(BaseModel):
    memory_id: str
    client_id: str
    memory_type: MemoryType
    topic: str
    subject: str = ""
    title: str = ""
    content: str
    value_text: Optional[str] = None
    corrects_value: Optional[str] = None
    certainty: Certainty = "n/a"
    source_role: Optional[SourceRole] = None
    status: Status = "active"
    confidence: float = Field(0.5, ge=0, le=1)
    event_date: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    version_no: Optional[int] = None
    supersedes_id: Optional[str] = None
    origin: Origin = "conversation_llm"
    supporting_episodes: int = 0
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # additions by the Memory Orchestrator (all optional; nothing renamed or removed)
    expires_at: Optional[str] = None          # working memory
    session_id: Optional[str] = None          # working memory
    approval_status: Optional[str] = None     # procedural: proposed | approved | rejected
    procedure_name: Optional[str] = None      # procedural
    steps: list[str] = Field(default_factory=list)  # procedural


class Relationship(BaseModel):
    source_id: str
    target_id: str
    rel_type: RelType
    note: str = ""


class MemoryContext(BaseModel):
    """What the Memory Orchestrator hands to the Tool Selector and Report Agent for ONE client."""
    client_id: str
    request_id: str = ""
    topics: list[str] = Field(default_factory=list)
    as_of: Optional[str] = None
    current: list[MemoryItem] = Field(default_factory=list, description="active semantic memories (or valid at as_of)")
    history: list[MemoryItem] = Field(default_factory=list, description="superseded semantic versions")
    episodes: list[MemoryItem] = Field(default_factory=list, description="chronological, capped")
    episodes_omitted: int = 0
    procedures: list[MemoryItem] = Field(default_factory=list, description="approved only")
    working: list[MemoryItem] = Field(default_factory=list)
    relations: list[Relationship] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    unresolved: list[MemoryItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> dict:
        """Compact form for the Tool Selector prompt: current value per topic + unresolved count."""
        return {"client_id": self.client_id, "current": {m.topic: m.value_text for m in self.current if m.value_text},
                "unresolved": len(self.unresolved)}
