"""Plain data structures shared by the four channel parsers and the store.  No database access, no I/O."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedTurn:
    turn_id: str
    seq: int
    ts: Optional[str]
    ts_basis: str                       # message | turn | container_start_only
    speaker_id: Optional[str]
    speaker_name: Optional[str]
    speaker_role: Optional[str]
    attribution_status: str             # source_stated | derived_from_role_label
    original_text: str
    source_ref: str
    email: Optional[dict] = None        # email_message_details fields
    chat: Optional[dict] = None         # chat_message_details fields


@dataclass
class ParsedContainer:
    container_id: str
    layer: str                          # original
    channel: str                        # Meetings | Calls | Email | Chat
    kind: str                           # email_thread | meeting | call | chat_conversation
    client_id: str
    advisor_id: Optional[str]
    subject: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    participant_ids: str
    source_platform: Optional[str]
    source_file: str
    source_ref: str
    classification_basis: Optional[str]
    metadata: dict
    turns: list = field(default_factory=list)
    email_thread: Optional[dict] = None
    provenance: dict = field(default_factory=dict)
    # filled by semantics, not by the parser
    segments: list = field(default_factory=list)
    annotations: list = field(default_factory=list)
    relations: list = field(default_factory=list)

    def content_hash(self, parser_version: str) -> str:
        canon = dict(c=self.container_id, ch=self.channel, k=self.kind, cl=self.client_id, ad=self.advisor_id, s=self.subject,
                     st=self.started_at, en=self.ended_at, p=self.participant_ids, m=self.metadata, pv=parser_version,
                     t=[(t.turn_id, t.seq, t.ts, t.speaker_id, t.speaker_role, t.original_text, t.email, t.chat) for t in self.turns])
        return hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


@dataclass
class ParsedCrm:
    crm_record_id: str
    crm_file: str
    artifact_type: str
    channel: str
    client_id: str
    advisor_id: Optional[str]
    author_id: Optional[str]
    created_at: str
    subject: Optional[str]
    text: str
    source_refs: list
    transfer_method: Optional[str]
    layer_flag: str                     # the dataset's record_layer, expected crm_derived
    is_original_source_evidence: bool

    def content_hash(self, parser_version: str) -> str:
        return hashlib.sha256(json.dumps([self.__dict__, parser_version], sort_keys=True, default=str).encode("utf-8")).hexdigest()


class ParseError(ValueError):
    """A source row is structurally unusable; the container is recorded as failed instead of being guessed."""
