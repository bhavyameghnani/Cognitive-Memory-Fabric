"""Four-agent pipeline: Planner -> Memory Orchestrator -> Tool Selector -> Report Agent.

Each agent receives data only from the previous ones; the Report Agent gets no database or MCP handle.

Harness (optional, `harness=` / `auth=`): when a harness.Harness is supplied, ONE RequestRuntime is created here, next to the request id and the Tracer, and
its objects are shared by every agent: the same tracer (memory events included), the same request budget (all LLM calls, retries, tool calls, deadline),
the same telemetry, the same actor/client scope.  Stages then follow the validated plan's execution_order instead of a fixed sequence.
Without a harness the behaviour is the original one (apart from the memory orchestrator now writing into the request's tracer).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from agents.memory.agent import MemoryOrchestrator
from agents.planner.agent import Planner
from agents.report.agent import ReportAgent
from agents.tool_selector.agent import ToolSelector
from agents.tool_selector.toolbox import Toolbox
from llm.client import LLMClient
from orchestration.tracing import Tracer
from schemas.plan import ExecutionPlan
from schemas.report import AdvisorReport
from schemas.tools import TOOL_NAMES, ToolSelectorState


@dataclass
class PipelineResult:
    plan: ExecutionPlan
    memory: dict
    tools: ToolSelectorState
    report: AdvisorReport
    timings_ms: dict = field(default_factory=dict)  # per-stage wall time, for the UI's agent-performance panel
    trace: list = field(default_factory=list)       # the tracer's in-memory events
    harness: dict = field(default_factory=dict)     # harness summary (budget, telemetry, cache, evidence, approval); empty without a harness
    released: bool = True                           # False when the report is withheld pending human approval


def plan_order(plan: ExecutionPlan) -> list[str]:
    """Agents to run, in the validated plan's execution order (report_agent excluded: it always runs last)."""
    by_id = {s.id: s.agent for s in plan.subtasks}
    ids = [i for i in plan.execution_order if i in by_id] or [s.id for s in plan.subtasks]
    return [a for a in dict.fromkeys(by_id[i] for i in ids) if a != "report_agent"]


def run_pipeline(request_text: str, llm: LLMClient, memory: MemoryOrchestrator, toolbox_factory=Toolbox, request_id: Optional[str] = None,
                 persist_trace: bool = True, harness=None, auth=None) -> PipelineResult:
    rid = request_id or "REQ-" + uuid.uuid4().hex[:8]
    use_h = harness is not None and harness.config.enabled
    tracer = Tracer(rid, persist=persist_trace and (not use_h or harness.config.trace_enabled))
    rt = harness.start(rid, tracer, auth, getattr(memory, "store", None)) if use_h else None
    prev_tracer = memory.tracer
    memory.tracer = tracer          # memory events belong to the request's trace, not to a private NullTracer
    try:
        return _run(request_text, llm, memory, toolbox_factory, rid, tracer, rt)
    except BaseException as e:
        if rt is not None and not rt.finished:      # an exception ended the request: record it as ABANDONED instead of leaving the trace without an outcome
            rt.finish(error=e)
        raise
    finally:
        memory.tracer = prev_tracer


def _run(request_text, llm, memory, toolbox_factory, rid, tracer, rt) -> PipelineResult:
    if rt is not None:
        from harness.permission_gate import PermissionDenied
        try:
            rt.authorize_request()
            pre, _ = memory.conv.resolve_clients(request_text)   # deny BEFORE any model call is spent
            # An explicit client id that does not exist must look exactly like one the caller may not use (otherwise the answer tells a restricted caller which ids exist).  Callers whose scope
            # is every client (supervisor / admin / operator / service) can list them all anyway, so for them nothing changes.
            if set(rt.gate.authorized_clients(rt.auth)) != set(memory.conv.directory()):
                pre = pre + [c for c in memory.conv.explicit_client_ids(request_text) if c not in memory.conv.directory()]
            if pre:
                rt.gate.check(rt.auth, "read_memory", pre)
        except PermissionDenied as e:
            tracer.event("pipeline", "denied", code=e.code, detail=e.detail)
            rt.finish(error=e)      # outcome 'denied', not 'completed'
            raise
        llm = rt.wrap_llm(llm)
        toolbox_factory = rt.wrap_toolbox_factory(toolbox_factory)

    tm: dict = {}
    t = time.perf_counter()
    planner = Planner(llm, tracer=tracer)
    # the model is told only about the clients this caller may use (the directory lists every advisor's clients); without a harness there is no caller, so no restriction
    visible = rt.gate.authorized_clients(rt.auth) if rt is not None and rt.cfg.permission_gate else None
    plan = planner.plan(request_text, list(TOOL_NAMES), request_id=rid, visible_clients=visible)
    if rt is not None:
        try:
            rt.authorize_plan(plan)
        except PermissionDenied as e:
            tracer.event("pipeline", "denied", code=e.code, detail=e.detail)
            rt.finish(error=e)      # outcome 'denied', not 'completed'
            raise
    tm["planner"], t = int((time.perf_counter() - t) * 1000), time.perf_counter()

    if rt is not None:
        order = plan_order(plan)
    else:   # original fixed sequence: memory always, tools only when the plan has a tool_selector subtask
        order = ["memory_orchestrator"] + (["tool_selector"] if any(s.agent == "tool_selector" for s in plan.subtasks) else [])
    mem: dict = {}
    summary: dict = {"clients": []}
    tools: Optional[ToolSelectorState] = None
    tm["memory"] = tm["tool_selector"] = 0
    for step in order:
        t = time.perf_counter()
        if step == "memory_orchestrator":
            mem = memory.retrieve(plan, request_id=rid)
            if rt is not None:
                mem = rt.process_memory(plan, mem)
            summary = {"clients": [m.summary() for m in mem.values()]}
            tm["memory"] = int((time.perf_counter() - t) * 1000)
        elif step == "tool_selector":
            sel = ToolSelector(llm, toolbox_factory(), tracer=tracer, config=rt.selector_config() if rt is not None else None)
            tools = sel.run_sync(request_text, plan, summary)
            if tools.stop_reason in ("tool_discovery_failed", "transport_failed"):  # replan without tools, record the gap
                plan = planner.replan(plan, list(TOOL_NAMES), tools.stop_reason, [], visible_clients=visible)
                tools.missing_information.append("tools were unavailable; financial data could not be collected")
            tm["tool_selector"] = int((time.perf_counter() - t) * 1000)
    if rt is not None and "memory_orchestrator" not in order:
        tracer.event("pipeline", "memory_skipped", reason="the validated plan has no memory_orchestrator subtask")
    if tools is None:
        tools = ToolSelectorState(request_id=rid, completion_status="not_needed", stop_reason="planner did not request tools")

    t = time.perf_counter()
    memory.store_working(rid, plan, list(tools.executions))
    report = ReportAgent(llm, tracer=tracer).write(request_text, plan, mem, tools)
    if rt is not None:
        report = rt.finalize_report(plan, mem, tools, report, request_text)
    tm["report"], tm["total"] = int((time.perf_counter() - t) * 1000), tracer.duration_ms()
    tracer.event("pipeline", "finished", report_validation=report.validation, ms=tracer.duration_ms())
    hsum = rt.finish() if rt is not None else {}
    return PipelineResult(plan, mem, tools, report, timings_ms=tm, trace=list(tracer.events), harness=hsum, released=rt.released if rt is not None else True)
