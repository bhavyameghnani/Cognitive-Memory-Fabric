"""Memory Write Validator: the last independent check before a memory becomes durable.

Wired into the EXISTING write paths, not around them:
  * MemoryOrchestrator.ingest_meeting  -> review() for every candidate that survived the ingestion filter, before ingest.persist_decision stores the episode
  * Consolidator._semantic             -> approve_semantic() before a semantic version is created (semantic memory is still written ONLY by the Consolidator)
The validator is deliberately not a re-use of the ingestion guards: it re-derives everything from the source data, so a bug or a bypass upstream does not
silently produce a durable fact.

Rules (all deterministic; none can raise certainty):
  reject    turn missing / belongs to another client / speaker differs from the source row / excerpt not in the source turn / topic unknown /
            statement unsupported by its excerpt (no shared content, value or topic evidence) -> an invented memory
  downgrade hypothesis wording (might, could, considering, ...) on a 'confirmed' item   -> hypothetical
            reported preference ('I prefer ...') without decision wording               -> unconfirmed   (kept as a reported preference, never a constraint)
            advisor-side inference ('it sounds like you ...')                           -> unconfirmed
  semantic  a version may only be created from an active confirmed/correction episode whose evidence re-verifies for the same client and whose wording is
            not a hypothesis, preference or inference (decision wording is required for those kinds)
Memory kinds recorded in the audit: fact | reported_preference | hypothesis | inference | recommendation | unresolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from agents.memory import rules
from agents.memory.config import CERTAINTY_RANK
from agents.memory.store import EvidenceError

HYPOTHESIS = re.compile(r"\b(might|may|could|maybe|perhaps|possibly|thinking (?:about|of)|considering|consider|wondering|what if|open to|exploring|toying)\b", re.I)
PREFERENCE = re.compile(r"\b(prefer\w*|would rather|i(?:'d| would) like|i like|favou?r\w*|comfortable with|leaning (?:to|toward)|rather than|dislike|not keen)\b", re.I)
INFERENCE = re.compile(r"\b(it sounds like|you seem|seems like you|i get the sense|i assume|it appears|i gather|i take it|presumably)\b", re.I)
WORD = re.compile(r"[a-z]{3,}")
STOP = set("the a an and or of to in on for with at by from is are was were be been this that it its as not client his her they their has have had will would".split())
NO_SUPPORT_TOPICS = ("other",)


def classify_statement(text: str, speaker: str, certainty: str, origin: str = "") -> str:
    t = rules.norm(text)
    decided = rules.has_decision(t)
    if certainty == "recommendation":
        return "recommendation"
    if HYPOTHESIS.search(text) and not decided:
        return "hypothesis"
    if speaker == "Advisor" and INFERENCE.search(text) and not decided:
        return "inference"
    if PREFERENCE.search(text) and not decided:
        return "reported_preference"
    if certainty in ("exploratory", "hypothetical", "unconfirmed"):
        return "unresolved"
    return "fact"


@dataclass
class WriteVerdict:
    action: str                       # accept | downgrade | reject
    kind: str
    certainty_in: str
    certainty_out: str
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(action=self.action, kind=self.kind, certainty_in=self.certainty_in, certainty_out=self.certainty_out, reasons=self.reasons)


def _support(statement: str, excerpt: str, value: Optional[str], topic: str) -> bool:
    if value and rules.value_supported(value, excerpt):
        return True
    if rules.infer_topic(excerpt) == topic:
        return True
    s = {w for w in WORD.findall(statement.lower()) if w not in STOP}
    e = set(WORD.findall(excerpt.lower()))
    return bool(s) and len(s & e) / len(s) >= 0.25


class MemoryWriteValidator:
    def __init__(self, conv, store=None, emit: Callable[..., None] = lambda *a, **k: None, approval=None, sensitive_topics: tuple = ()) -> None:
        self.conv, self.store, self.emit = conv, store, emit
        self.approval = approval                       # optional ApprovalGate for sensitive semantic changes
        self.sensitive_topics = tuple(sensitive_topics)
        self.log: list = []

    # ------------------------------------------------------------------ episodic candidates
    def review(self, d, ctx) -> WriteVerdict:
        c = d.final
        reasons: list = []
        if c is None:
            return self._done(ctx, d, WriteVerdict("reject", "unresolved", "n/a", "n/a", ["no final candidate"]))
        cin = c.certainty
        row = self.conv.turn(ctx.interaction_id, c.turn_index)
        if row is None:
            return self._done(ctx, d, WriteVerdict("reject", "fact", cin, cin, ["cited turn does not exist"]))
        if row["client_id"] != ctx.client_id:
            return self._done(ctx, d, WriteVerdict("reject", "fact", cin, cin, [f"turn belongs to {row['client_id']}, not {ctx.client_id}"]))
        if row["speaker"] != c.speaker_role:
            return self._done(ctx, d, WriteVerdict("reject", "fact", cin, cin, [f"speaker is {row['speaker']} in the source, candidate says {c.speaker_role}"]))
        if not c.excerpt or not self.conv.verify_excerpt(ctx.client_id, ctx.interaction_id, c.turn_index, c.excerpt):
            return self._done(ctx, d, WriteVerdict("reject", "fact", cin, cin, ["excerpt does not exist in the cited turn"]))
        if c.topic in NO_SUPPORT_TOPICS or c.topic not in rules.TOPICS:
            return self._done(ctx, d, WriteVerdict("reject", "fact", cin, cin, [f"unknown topic {c.topic!r}"]))
        if not _support(c.statement, c.excerpt, c.value, c.topic):
            return self._done(ctx, d, WriteVerdict("reject", "fact", cin, cin, ["statement is not supported by its excerpt (invented memory)"]))
        kind = classify_statement(c.excerpt, c.speaker_role, cin, d.origin)
        out = cin
        if cin in ("confirmed", "correction"):
            if kind == "hypothesis":
                out = "hypothetical"
                reasons.append("hypothesis wording without decision wording cannot be a confirmed item")
            elif kind in ("reported_preference", "inference"):
                out = "unconfirmed"
                reasons.append(f"a {kind.replace('_', ' ')} is kept as unconfirmed, not as a confirmed fact or constraint")
        if CERTAINTY_RANK.get(out, 0) > CERTAINTY_RANK.get(cin, 0):   # defensive: upgrades are impossible by construction
            out = cin
        return self._done(ctx, d, WriteVerdict("downgrade" if out != cin else "accept", kind, cin, out, reasons))

    def _done(self, ctx, d, v: WriteVerdict) -> WriteVerdict:
        rec = dict(interaction_id=ctx.interaction_id, client_id=ctx.client_id, turn=getattr(d.final or d.candidate, "turn_index", None), **v.as_dict())
        self.log.append(rec)
        self.emit("harness.memory_write", {"accept": "accepted", "downgrade": "downgraded", "reject": "rejected"}[v.action], **rec)
        return v

    @staticmethod
    def apply(d, v: WriteVerdict) -> None:
        """Mutate the ingestion Decision in place; the original candidate stays untouched for the audit table."""
        if v.action == "reject":
            d.decision, d.reason = "rejected", "write validator: " + "; ".join(v.reasons)
            return
        d.notes.append(f"memory_kind={v.kind}")
        if v.action == "downgrade":
            d.final = d.final.model_copy(update={"certainty": v.certainty_out})
            d.decision, d.certainty_out = "downgraded", v.certainty_out
            d.reason = "write validator: " + "; ".join(v.reasons)

    # ------------------------------------------------------------------ semantic versions (called by Consolidator)
    def approve_semantic(self, client_id: str, version, store) -> tuple:
        ep = version.creator
        topic = ep["topic"]
        why = None
        if ep["certainty"] not in ("confirmed", "correction"):
            why = f"creating episode is {ep['certainty']}, not confirmed"
        elif ep["status"] != "active":
            why = f"creating episode status is {ep['status']}"
        else:
            kind = classify_statement(ep["content"], ep["speaker_role"], ep["certainty"])
            if kind in ("hypothesis", "reported_preference", "inference"):
                why = f"creating episode is a {kind.replace('_', ' ')}, which cannot become a verified fact"
        if why is None:
            try:
                evs = store.evidence_map([ep["memory_id"]]).get(ep["memory_id"], [])
                if not evs:
                    why = "creating episode has no evidence"
                for e in evs:
                    store.verify_evidence(client_id, dict(source_type="conversation", interaction_id=e.interaction_id, turn_index=e.turn_index, speaker=e.speaker, excerpt=e.excerpt))
            except EvidenceError as e:
                why = f"evidence no longer verifies: {e}"
        if why is None and topic in self.sensitive_topics and self.approval is not None:
            ok, why = self.approval.check_memory_write(client_id, topic, ep["subject"] or topic, version.value, ep["memory_id"], ep["event_date"])
            why = None if ok else why
        rec = dict(client_id=client_id, topic=topic, value=version.value, creator=ep["memory_id"], approved=why is None, reason=why or "ok")
        self.log.append(rec)
        self.emit("harness.memory_write", "semantic_approved" if why is None else "semantic_rejected", **rec)
        return why is None, why
