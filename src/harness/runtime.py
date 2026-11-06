"""Harness runtime.

Harness          process-level: configuration, the shared response cache, the approval gate/store.  Built once (API start-up / CLI run).
RequestRuntime   one per request, created at the top of run_pipeline() next to the request id and the Tracer.  It owns the ONE budget, the ONE telemetry,
                 the permission gate bound to the request's actor, and hands the SAME objects to all four agents (via wrap_llm / wrap_toolbox_factory /
                 the tracer).  It is not an agent: it makes no decisions about the advisor's question.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from harness.approval import ApprovalGate, ApprovalPolicy, ApprovalProposal, ApprovalRequired, RiskCategory
from harness.approval_store import open_approval_store
from harness.budget import RequestBudget
from harness.cache import ResponseCache, sha
from harness.config import HarnessConfig
from harness.context_manager import ContextBudgetController, MemorySelection
from harness.evidence_verifier import EvidenceVerifier
from harness.llm import HarnessLLM
from harness.memory_write_validator import MemoryWriteValidator
from harness.permission_gate import AuthContext, PermissionDenied, PermissionGate
from harness.reliability import safe_error
from harness.workers import CANCELS, REGISTRY
from harness.telemetry import Telemetry
from harness.temporal_conflict import detect, publish
from harness.token_counter import for_model
from harness.tools import wrap_factory
from harness.trace_recorder import influence_record, prune_traces
from schemas.report import AdvisorReport

ROOT = Path(__file__).resolve().parents[2]


def data_version(store=None) -> str:
    """Changes whenever memory or the operational database changes; part of every cache key and every approval fingerprint."""
    parts = []
    try:
        if store is not None:
            # working memory is per-request scratch (written by every ask); it must not count as a change of the durable knowledge base
            r = store.query("SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS m FROM {S}memory_records WHERE memory_type <> 'working'")[0]
            e = store.query("SELECT COUNT(*) AS n FROM {S}memory_lifecycle_events l JOIN {S}memory_records r ON r.memory_id = l.memory_id WHERE r.memory_type <> 'working'")[0]
            # P1.5: conflicts, their resolutions and every row_version are part of the knowledge base too.  Without them a newly recorded or resolved conflict (which changes no memory row
            # and no lifecycle event) left the version unchanged, so an approval granted BEFORE the conflict stayed releasable and cached results stayed "current".
            extra = store.query("SELECT (SELECT COUNT(*) FROM {S}memory_conflicts) AS c, (SELECT COUNT(*) FROM {S}memory_conflict_resolutions) AS x, "
                                "(SELECT COALESCE(SUM(row_version), 0) FROM {S}memory_records WHERE memory_type <> 'working') AS v")[0]
            parts.append(f"mem:{r['n']}:{r['m']}:{e['n']}:v{extra['v']}:c{extra['c']}:x{extra['x']}")
    except Exception:   # a store that cannot answer must not look 'unchanged'
        parts.append("mem:unknown")
    url = os.environ.get("CMF_DATABASE_URL", "sqlite:///build/cmf_ops_smoke.db")
    if url.startswith("sqlite:///"):
        p = Path(url[len("sqlite:///"):])
        p = p if p.is_absolute() else ROOT / p
        try:
            st = p.stat()
            parts.append(f"ops:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("ops:missing")
    else:
        parts.append("ops:external")
    return "|".join(parts)


class Harness:
    def __init__(self, config: Optional[HarnessConfig] = None, conv=None, decider: Optional[Callable] = None, approval_store=None) -> None:
        from services.conversations import shared
        self.config = config or HarnessConfig.from_env()
        self.conv = conv or shared()
        self.cache = ResponseCache(self.config.cache_max_entries, self.config.cache_ttl_seconds, self.config.cache_enabled)
        policy = ApprovalPolicy(required=self.config.approval_required_for_high_risk, sensitive_memory_topics=tuple(self.config.approval_sensitive_memory_topics))
        # DURABLE approvals (SQLite).  An unusable database raises here - at start-up - so the process never runs with approvals it cannot persist.  `approval_store` lets the
        # evaluation runner pass a volatile one; the production profile refuses that (open_approval_store).
        if approval_store is None:
            approval_store = open_approval_store(self.config)
        elif not approval_store.durable and self.config.profile == "production":
            from harness.config import ConfigError
            raise ConfigError("the production profile requires a durable approval store")
        self.approvals = ApprovalGate(policy, store=approval_store, gate=PermissionGate(self.config, self.conv.directory), decider=decider)
        approval_store.purge_expired()
        self._n = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ per request
    def start(self, request_id: str, tracer, auth: Optional[AuthContext], store=None) -> "RequestRuntime":
        with self._lock:
            self._n += 1
            first = self._n == 1 or self._n % 50 == 0
        if first and self.config.trace_enabled:
            prune_traces(None, self.config.trace_retention_days, self.config.trace_max_files)
        return RequestRuntime(self, request_id, tracer, auth, store)

    # ------------------------------------------------------------------ memory writes (ingestion / consolidation)
    def write_validator(self, tracer=None, store=None) -> Optional[MemoryWriteValidator]:
        if not self.config.memory_write_validation:
            return None
        emit = tracer.event if tracer is not None else (lambda *a, **k: None)
        return MemoryWriteValidator(self.conv, store, emit, approval=self.approvals, sensitive_topics=tuple(self.config.approval_sensitive_memory_topics))

    # ------------------------------------------------------------------ approval workflow entry points (API / UI)
    def decide_approval(self, approval_id: str, approver: AuthContext, approve: bool, reason: str):
        return self.approvals.decide(approval_id, approver, approve, reason)

    def release(self, approval_id: str, current_data_version: str, actor: Optional[AuthContext] = None):
        """Release a withheld report: ONLY to its requester (`actor`, the verified caller), ONLY while the approval is valid, and ONLY ONCE.  The proposal is rebuilt from the
        STORED draft and the CURRENT data version, so a changed payload, client, requester or data version no longer matches the approved fingerprint."""
        rec = self.approvals.store.get(approval_id)
        if rec is None:
            raise KeyError(approval_id)
        if rec.draft is None:                                      # consumed, rejected or expired (the payload is scrubbed): not releasable; the caller sees the status
            if rec.data_version != current_data_version:           # same precedence as the store: a mismatch is reported as such, whatever the status
                self.approvals.store.record_denial(approval_id, getattr(actor, "actor_id", "") or rec.requested_by, getattr(actor, "role", "") or "", "release_denied", "approval_invalidated")
                raise PermissionDenied("approval_invalidated", "the data version changed after approval; a fresh approval is required")
            raise ApprovalRequired(rec)
        prop = _report_proposal(rec.draft, rec.requested_by, RiskCategory(rec.category), rec.reason, current_data_version, rec.client_ids, getattr(rec.draft, "question", ""),
                                rec.requester_role, rec.request_id)
        return self.approvals.release(approval_id, prop, actor=actor)


# ---------------------------------------------------------------------------------------------------------------------------------------------
# Withholding policy.  ONE place decides what may leave the process while an answer is not released; the pipeline scrubs the report at its source and every API
# path builds its response with the functions below.  Nothing derived from the withheld content (findings, evidence, memory ids/labels, conflicts, tool results,
# agent-run details) is part of these shapes.  The real draft lives only in the approval record, server side.
SAFE_APPROVAL_KEYS = ("approval_id", "status", "category", "created_at")
WITHHELD_MESSAGE = "This answer is withheld pending human approval. It will be released only if a supervisor approves it."
REJECTED_MESSAGE = "This answer was not approved and cannot be released."
INVALIDATED_MESSAGE = "The approval no longer matches the current data; a new approval is required."
EXPIRED_MESSAGE = "The approval expired before this answer was released; a new approval is required."
CONSUMED_MESSAGE = "This answer was already released under its approval and cannot be released again."


def safe_approval(approval: dict, status: Optional[str] = None) -> dict:
    out = {k: approval.get(k) for k in SAFE_APPROVAL_KEYS}
    if status:
        out["status"] = status
    return out


def withheld_report(report: AdvisorReport, approval: dict, message: str = WITHHELD_MESSAGE) -> AdvisorReport:
    """A report with the same shape (the UI reads these fields) and NO content."""
    return AdvisorReport(request_id=report.request_id, client_id="", question="", summary=message,
                         validation={"withheld_for_approval": True, "harness_approval": safe_approval(approval)})


def withheld_view(approval: dict, request_id: str = "", status: Optional[str] = None) -> dict:
    """The only API representation of an answer that is pending, rejected or invalidated."""
    st = status or ("rejected" if approval.get("status") == "rejected" else "pending_approval")
    message = {"rejected": REJECTED_MESSAGE, "approval_invalidated": INVALIDATED_MESSAGE, "approval_expired": EXPIRED_MESSAGE, "already_released": CONSUMED_MESSAGE}.get(st, WITHHELD_MESSAGE)
    stub = withheld_report(AdvisorReport(request_id=request_id, client_id="", question="", summary=""), approval, message)
    return dict(request_id=request_id, client_ids=[], report=stub.model_dump(), retrieved_memory_ids=[], agent_run=None, harness=None, released=False, status=st,
                approval=safe_approval(approval), message=message)


def released_view(report: AdvisorReport) -> dict:
    """API representation of an answer whose approval is valid for the current data."""
    return dict(request_id=report.request_id, client_ids=[c for c in report.client_id.split(",") if c], report=report.model_dump(), retrieved_memory_ids=[], agent_run=None,
                harness=None, released=True, status="released")


_RUNTIME_LIMITATION_PREFIXES = ("This answer was produced in degraded mode", "Resource budget exhausted")


def _report_digest(report) -> str:
    body = dict(summary=report.summary, key_findings=[f.model_dump() for f in report.key_findings], historical_context=[f.model_dump() for f in report.historical_context],
                financial_analysis=[f.model_dump() for f in report.financial_analysis], evidence=[e.ref for e in report.evidence],
                # P1.3.1 (F7): a degraded answer is a DIFFERENT answer.  Only the runtime state and the harness-written degradation limitations are bound here; the other H1 omissions
                # (uncertainties, assumptions, follow-ups, validation results, P1.1 verification outcomes, evidence content/provenance) are NOT covered by this change.
                runtime=(getattr(report, "validation", None) or {}).get("harness_runtime"),
                runtime_limitations=[x for x in (getattr(report, "limitations", None) or []) if x.startswith(_RUNTIME_LIMITATION_PREFIXES)])
    import json
    return sha(json.dumps(body, sort_keys=True, default=str), 32)


def _report_proposal(report, actor: str, category: RiskCategory, reason: str, dv: str, clients, question: str, role: str = "", request_id: str = "") -> ApprovalProposal:
    """`actor` is the requester's stable subject id; it is part of the fingerprint, so the approval is useless to anyone else."""
    return ApprovalProposal(category, "release_report", actor, reason, dict(question=question, report_digest=_report_digest(report)), dv, tuple(clients), requester_role=role,
                            request_id=request_id)


class RequestRuntime:
    def __init__(self, harness: Harness, request_id: str, tracer, auth: Optional[AuthContext], store=None) -> None:
        self.harness, self.cfg, self.request_id, self.tracer, self.auth, self.store = harness, harness.config, request_id, tracer, auth, store
        self.budget = RequestBudget(self.cfg)
        self.telemetry = Telemetry()
        self.gate = PermissionGate(self.cfg, harness.conv.directory, emit=tracer.event)
        self.client_ids: tuple = ()
        self.data_version = data_version(store)
        self.selection: Optional[MemorySelection] = None
        self.conflicts: list = []
        self.verification = None
        self.approval: Optional[dict] = None
        self.released = True
        self._counter = None
        self.finished = False
        self._report_fallback = False
        self.tracer.event("harness", "request_started", profile=self.cfg.profile, actor=getattr(auth, "actor_id", None), role=getattr(auth, "role", None),
                          identity_source=getattr(auth, "source", None), data_version=self.data_version, warnings=list(self.cfg.warnings),
                          limits=self.budget.snapshot()["limits"])

    # ------------------------------------------------------------------ wiring for the four agents
    def wrap_llm(self, inner: Any) -> HarnessLLM:
        counter = for_model(getattr(inner, "model", ""))
        self._counter = counter
        ctx = ContextBudgetController(self.cfg, counter, emit=self.tracer.event)
        self.context = ctx
        return HarnessLLM(inner, config=self.cfg, budget=self.budget, telemetry=self.telemetry, counter=counter, context=ctx, cache=self.harness.cache,
                          emit=self.tracer.event, scope=lambda: (getattr(self.auth, "actor_id", "") or "", self.client_ids), data_version=lambda: self.data_version,
                          request_id=self.request_id)

    def wrap_toolbox_factory(self, factory: Callable) -> Callable:
        return wrap_factory(factory, budget=self.budget, gate=self.gate, auth=self.auth, emit=self.tracer.event)

    def selector_config(self):
        from agents.tool_selector.agent import SelectorConfig
        c = self.cfg
        return SelectorConfig(max_iterations=min(5, c.max_tool_iterations), max_executions=min(12, c.max_tool_calls), call_timeout_s=min(90.0, c.max_tool_seconds))

    # ------------------------------------------------------------------ gates
    def authorize_request(self) -> None:
        self.gate.check(self.auth, "ask")

    def authorize_plan(self, plan) -> None:
        self.client_ids = tuple(plan.client_ids)
        self.gate.check(self.auth, "read_memory", plan.client_ids)
        self.gate.check(self.auth, "generate_report", plan.client_ids)

    # ------------------------------------------------------------------ memory stage
    def process_memory(self, plan, mem: dict) -> dict:
        allowed = None if not self.cfg.permission_gate else self.gate.authorized_clients(self.auth)
        mem, self.selection = self.context.select_memory(plan, mem, allowed)
        if self.cfg.temporal_conflict_detection:
            for cid, ctx in mem.items():
                found = detect(ctx, self.cfg.stale_after_days)
                self.conflicts += found
                added = publish(ctx, found)
                self.tracer.event("harness.temporal", "conflicts", client_id=cid, found=[c.as_dict() for c in found], published=added)
        return mem

    # ------------------------------------------------------------------ report stage
    def finalize_report(self, plan, mem: dict, tools, report, request_text: str):
        from agents.report.validate import deterministic_summary
        if self.cfg.evidence_verification:
            verifier = EvidenceVerifier(self.harness.conv, self.store, owns=lambda cid: self.gate.owns(self.auth, cid), structured=self.cfg.structured_claim_verification)
            report, self.verification = verifier.verify_report(report, plan.client_ids, tools, lambda: deterministic_summary(report.evidence, plan))
            self.tracer.event("harness.evidence", "verified", counts=self.verification.counts(), summary_replaced=self.verification.summary_replaced,
                              withheld=[v.as_dict() for v in self.verification.verdicts if v.verdict == "unsupported"])
        exhausted = self.budget.exhausted
        if exhausted:
            note = "Resource budget exhausted (" + ", ".join(e["reason"] for e in exhausted) + "); this result is partial."
            report = report.model_copy(update=dict(limitations=list(report.limitations) + [note]))
            self.tracer.event("harness.budget", "exhausted", reasons=exhausted)
        # P1.3: say plainly whether this answer is COMPLETE.  A timeout, an overflow, a truncated reply, a missing hook or a report-agent fallback never passes as a normal
        # success: the state travels with the report (and into the approval draft), and a limitation is added for causes the budget note above does not already cover.
        rstate = self._runtime_state(report)
        extra = [r for r in rstate["degraded_reasons"] if r not in {e["reason"] for e in exhausted}]
        if extra:
            report = report.model_copy(update=dict(limitations=list(report.limitations) + ["This answer was produced in degraded mode (" + ", ".join(extra) + "); treat it as incomplete."]))
        report.validation["harness_runtime"] = rstate
        # human approval: fail closed
        full_report = report
        cats = self.harness.approvals.policy.classify_report(plan, request_text) if self.cfg.approval_required_for_high_risk else []
        if cats:
            proposal = _report_proposal(report, getattr(self.auth, "actor_id", None) or "unknown", cats[0],
                                        "suitability-related conclusion: " + (request_text or "")[:140], self.data_version, plan.client_ids, request_text,
                                        getattr(self.auth, "role", "") or "", self.request_id)
            try:
                # consume=True: an already-approved identical answer is released NOW and the approval is used up - asking again can never replay it
                rec = self.harness.approvals.require(proposal, draft=report, consume=True)
                self.approval = rec.public()
            except ApprovalRequired as e:
                rec = e.record
                self.approval, self.released = rec.public(), False
                full_report = report                                   # server-side audit only; the draft itself is held by the approval record
                report = withheld_report(report, self.approval)
            else:
                report.validation["harness_approval"] = self.approval
            self.tracer.event("harness.approval", "gate", released=self.released, **self.approval)
        influence = influence_record(plan, mem, self.selection, full_report if not self.released else report, self.verification, tools)
        self.tracer.event("harness.trace", "influence", **influence)
        self.influence = influence
        return report

    # ------------------------------------------------------------------ runtime state
    def _runtime_state(self, report=None) -> dict:
        """complete / degraded, with the reasons: degrading reliability incidents, exhausted budgets, and a report-agent fallback caused by a failed model call."""
        # 'budget_exhausted' is the umbrella incident; the concrete reasons come from the budget itself
        reasons = (set(self.telemetry.degrading_kinds()) - {"budget_exhausted"}) | {e["reason"] for e in self.budget.exhausted}
        if report is not None and (getattr(report, "validation", None) or {}).get("mode") == "deterministic_model_failure":
            self._report_fallback = True                           # remembered for the final request summary
        if self._report_fallback:
            reasons.add("report_agent_fallback")
        reasons = sorted(reasons)
        return dict(complete=not reasons, outcome="degraded" if reasons else "completed", degraded_reasons=reasons)

    # ------------------------------------------------------------------ summary
    def finish(self, error: Optional[BaseException] = None) -> dict:
        """Close the request.  error=None: it ran to the end (outcome completed | degraded).  error given: the request was ABANDONED by an exception - the summary says so, and
        any model call still running is reported as pending rather than ignored."""
        if self.finished:
            return getattr(self, "_summary", {})
        self.finished = True
        if self.budget.exhausted:
            self.telemetry.incident("budget_exhausted", reasons=[e["reason"] for e in self.budget.exhausted])
        state = self._runtime_state()
        denied = isinstance(error, PermissionDenied)
        outcome = (("denied" if denied else "abandoned") if error is not None else state["outcome"])    # a refused request is neither completed nor an abandoned run
        s = dict(request_id=self.request_id, profile=self.cfg.profile, actor=getattr(self.auth, "actor_id", None), budget=self.budget.snapshot(),
                 telemetry=self.telemetry.summary(), token_method=self._counter.method if self._counter else None,
                 cache=dict(enabled=self.harness.cache.enabled, hit_rate=self.harness.cache.stats.hit_rate, **vars(self.harness.cache.stats)),
                 memory_selection=self.selection.report() if self.selection else None, conflicts=[c.as_dict() for c in self.conflicts],
                 evidence=(self.verification.counts() if self.verification else None), approval=self.approval, released=self.released,
                 permission_decisions=[dict(allowed=d.allowed, operation=d.operation, code=d.code, detail=d.detail) for d in self.gate.decisions],
                 warnings=list(self.cfg.warnings), outcome=outcome, degraded_reasons=state["degraded_reasons"],
                 workers=dict(provider=REGISTRY.snapshot(), cancel=CANCELS.snapshot()),        # PROCESS-WIDE counters (not per request)
                 error=(safe_error(error) if error is not None else None))
        self._summary = s
        self.tracer.event("harness", "request_finished", **{k: s[k] for k in ("budget", "telemetry", "cache", "evidence", "released", "outcome", "degraded_reasons")})
        if error is not None:
            self.tracer.event("harness.reliability", "request_denied" if denied else "request_abandoned", error=s["error"], ghost_pending=s["telemetry"].get("ghost_pending", 0))
        return s
