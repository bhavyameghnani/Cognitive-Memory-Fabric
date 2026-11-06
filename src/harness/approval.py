"""Human approval gate.  Fails closed.

An approval is bound to ONE exact proposal: its fingerprint is a SHA-256 over (category, operation, clients, payload, data version, REQUESTER).  Changing the
payload (for example a different report body), the client, the data version or the person asking changes the fingerprint, so an earlier approval no longer matches and a
fresh decision is required.  Missing, pending, rejected, expired, consumed or mismatching approvals are all treated as NOT approved.

Requests and decisions live in a durable SQLite store (harness/approval_store.py, sql/110_cmf_approvals_schema.sql): approvals survive a restart, expire, are decided
atomically with their audit event, and a released approval is CONSUMED - it can release its report exactly once.  An optional `decider` callable lets tests or a CLI supply
a decision synchronously.  The MCP tools are read-only, so no external action exists to approve today; EXTERNAL_ACTION / IRREVERSIBLE_OPERATION / HIGH_RISK_TOOL
categories exist so a future write-capable tool is gated from day one.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from harness.approval_store import APPROVER_ROLES, ApprovalRecord, ApprovalStore, ApprovalStoreError, NotApproved, SqliteApprovalStore, norm_subject  # noqa: F401
from harness.permission_gate import AuthContext, PermissionDenied


class RiskCategory(str, Enum):
    SUITABILITY_CONCLUSION = "suitability_conclusion"
    SENSITIVE_MEMORY_WRITE = "sensitive_memory_write"
    EXTERNAL_ACTION = "external_action"
    IRREVERSIBLE_OPERATION = "irreversible_operation"
    HIGH_RISK_TOOL = "high_risk_tool"


class ApprovalRequired(PermissionError):
    def __init__(self, record: "ApprovalRecord") -> None:
        super().__init__(f"human approval required ({record.category}); approval id {record.approval_id}, status {record.status}")
        self.record = record


SUITABILITY_Q = re.compile(r"\b(should|recommend\w*|suitab\w*|appropriate for|advis(?:e|able)|rebalanc\w*|switch|sell|buy|invest in|can (?:he|she|they|the client) afford|is it (?:safe|wise))\b", re.I)


@dataclass(frozen=True)
class ApprovalProposal:
    category: RiskCategory
    operation: str
    requested_by: str               # the stable authenticated subject id of the requester (the JWT `sub`), never a display name
    reason: str
    payload: Any
    data_version: str = ""
    client_ids: tuple = ()
    requester_role: str = ""        # audit metadata; NOT part of the fingerprint (the stable subject is what is bound)
    request_id: str = ""            # audit metadata; NOT part of the fingerprint (every ask has a new one)

    def fingerprint(self) -> str:
        """SHA-256 over category, operation, clients, payload, data version AND the requester: an approval cannot be reused by, or released for, anybody else."""
        blob = json.dumps(dict(category=self.category.value, operation=self.operation, clients=sorted(self.client_ids), payload=self.payload,
                               data_version=self.data_version, requester=(self.requested_by or "").strip()), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ApprovalPolicy:
    """Which categories need a human.  required=False for everything switches the framework off (development only)."""
    required: bool = True
    categories: tuple = tuple(RiskCategory)
    sensitive_memory_topics: tuple = ()

    def needs(self, category: RiskCategory) -> bool:
        return self.required and category in self.categories

    def classify_report(self, plan, request_text: str) -> list:
        out = []
        if getattr(plan, "task_type", "") == "forecast_feasibility" or SUITABILITY_Q.search(request_text or ""):
            out.append(RiskCategory.SUITABILITY_CONCLUSION)
        return [c for c in out if self.needs(c)]


class ApprovalGate:
    def __init__(self, policy: Optional[ApprovalPolicy] = None, store: Optional[SqliteApprovalStore] = None, gate=None,
                 decider: Optional[Callable[[ApprovalRecord], Optional[tuple]]] = None, emit: Callable[..., None] = lambda *a, **k: None) -> None:
        # the default store is VOLATILE (SQLite ':memory:'): it exists for unit tests.  Harness always passes the durable store, and the production profile refuses a volatile one.
        self.policy, self.store, self.gate, self.decider, self.emit = policy or ApprovalPolicy(), store or ApprovalStore(), gate, decider, emit

    # ------------------------------------------------------------------ requesting
    def submit(self, proposal: ApprovalProposal, draft: Any = None) -> ApprovalRecord:
        if not (proposal.requested_by or "").strip():
            raise PermissionDenied("missing_identity", "an approval request needs the stable subject id of the requester")
        rec, created = self.store.create(fingerprint=proposal.fingerprint(), category=proposal.category.value, operation=proposal.operation, requested_by=proposal.requested_by,
                                         requester_role=proposal.requester_role, reason=proposal.reason, client_ids=tuple(proposal.client_ids), request_id=proposal.request_id,
                                         data_version=proposal.data_version, draft=draft)
        if created:
            self.emit("harness.approval", "requested", **rec.public())
        if rec.status == "pending" and self.decider is not None:
            decision = self.decider(rec)
            if decision is not None:
                approver, ok, why = decision
                self.decide(rec.approval_id, approver, ok, why)
        return self.store.get(rec.approval_id) or rec

    def decide(self, approval_id: str, approver: AuthContext, approve: bool, reason: str) -> ApprovalRecord:
        rec = self.store.get(approval_id)
        if rec is None:
            raise KeyError(approval_id)
        try:
            self._check_approver(approver, rec)
            if norm_subject(approver.actor_id) == norm_subject(rec.requested_by):
                raise PermissionDenied("self_approval", "the requester cannot approve their own request")
            if not (reason or "").strip():
                raise PermissionDenied("reason_required", "an approval decision needs a reason")
        except PermissionDenied as e:
            self.store.record_denial(approval_id, getattr(approver, "actor_id", "") or "", getattr(approver, "role", "") or "", "decision_denied", e.code)
            raise
        rec = self.store.decide(approval_id, approver.actor_id, approver.role, approve, reason)      # the atomic, conditional write; it re-checks self-approval and state
        self.emit("harness.approval", rec.status, **rec.public())
        return rec

    def _check_approver(self, approver: Optional[AuthContext], rec: ApprovalRecord) -> None:
        """Identity, authentication, role and client scope of the APPROVER.  Fails closed on anything missing."""
        if approver is None or not (getattr(approver, "actor_id", "") or "").strip():
            raise PermissionDenied("missing_identity", "the approver has no stable subject id")
        if not approver.authenticated:
            raise PermissionDenied("unauthenticated", "an approval decision needs an authenticated identity")
        if self.gate is not None:
            self.gate.check(approver, "approve", rec.client_ids)             # role + client scope
        elif approver.role not in APPROVER_ROLES:
            raise PermissionDenied("role_not_permitted", f"role {approver.role} may not approve")

    # ------------------------------------------------------------------ enforcing
    def is_approved(self, proposal: ApprovalProposal) -> bool:
        return any(r.status == "approved" for r in self.store.find(proposal.fingerprint()))

    def require(self, proposal: ApprovalProposal, draft: Any = None, consume: bool = False) -> ApprovalRecord:
        """Raise ApprovalRequired unless an approval for exactly this proposal exists.  Never returns for pending / rejected / expired / consumed / missing.

        consume=True makes the approval single-use: the release is recorded and the approval can never release anything again (the report pipeline uses this)."""
        rec = self.submit(proposal, draft)
        if rec.status != "approved" or rec.fingerprint != proposal.fingerprint():
            raise ApprovalRequired(rec)
        if not consume:
            return rec
        try:
            self.store.consume(rec.approval_id, proposal.fingerprint(), proposal.requested_by, proposal.requester_role)
        except (PermissionDenied, KeyError):
            raise ApprovalRequired(self.store.get(rec.approval_id) or rec) from None
        self.emit("harness.approval", "released", approval_id=rec.approval_id)
        return self.store.get(rec.approval_id) or rec

    def release(self, approval_id: str, proposal: ApprovalProposal, actor: Optional[AuthContext] = None) -> Any:
        """Hand back the draft - exactly once - if the stored approval still matches the CURRENT proposal (payload, client, requester, data version), has not expired,
        and `actor` (the verified caller; the requester when omitted by library callers) is the requester."""
        if actor is not None and (not (actor.actor_id or "").strip() or not actor.authenticated):
            raise PermissionDenied("unauthenticated", "releasing an answer needs a verified identity")
        subject = actor.actor_id if actor is not None else proposal.requested_by
        role = actor.role if actor is not None else proposal.requester_role
        try:
            draft = self.store.consume(approval_id, proposal.fingerprint(), subject, role)
        except NotApproved as e:
            raise ApprovalRequired(e.record) from None
        except PermissionDenied as e:
            if e.code == "approval_invalidated":
                self.emit("harness.approval", "invalidated", approval_id=approval_id, reason="proposal, requester or data version changed since approval")
            raise
        self.emit("harness.approval", "released", approval_id=approval_id)
        return draft

    def check_memory_write(self, client_id: str, topic: str, subject: str, value: str, creator_id: str, event_date: str) -> tuple:
        if not self.policy.needs(RiskCategory.SENSITIVE_MEMORY_WRITE) or topic not in self.policy.sensitive_memory_topics:
            return True, None
        prop = ApprovalProposal(RiskCategory.SENSITIVE_MEMORY_WRITE, "write_semantic_memory", "ingestion", f"durable change to sensitive topic {topic}",
                                dict(topic=topic, subject=subject, value=value, creator=creator_id, event_date=event_date), "", (client_id,), requester_role="service")
        rec = self.submit(prop)
        return (rec.status == "approved"), (None if rec.status == "approved" else f"sensitive memory write needs human approval ({rec.approval_id}, {rec.status})")
