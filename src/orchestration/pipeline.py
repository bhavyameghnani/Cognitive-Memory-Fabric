"""Four-agent pipeline: Planner -> Memory Orchestrator -> Tool Selector -> Report Agent.

Each agent receives data only from the previous ones; the Report Agent gets no database or MCP handle.
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


def run_pipeline(request_text: str, llm: LLMClient, memory: MemoryOrchestrator, toolbox_factory=Toolbox, request_id: Optional[str] = None,
                 persist_trace: bool = True) -> PipelineResult:
    rid = request_id or "REQ-" + uuid.uuid4().hex[:8]
    tracer = Tracer(rid, persist=persist_trace)
    tm: dict = {}
    t = time.perf_counter()
    planner = Planner(llm, tracer=tracer)
    plan = planner.plan(request_text, list(TOOL_NAMES), request_id=rid)

    tm["planner"], t = int((time.perf_counter() - t) * 1000), time.perf_counter()
    mem = memory.retrieve(plan, request_id=rid)
    summary = {"clients": [m.summary() for m in mem.values()]}

    tm["memory"], t = int((time.perf_counter() - t) * 1000), time.perf_counter()
    uses_tools = any(s.agent == "tool_selector" for s in plan.subtasks)
    if uses_tools:
        tools = ToolSelector(llm, toolbox_factory(), tracer=tracer).run_sync(request_text, plan, summary)
        if tools.stop_reason in ("tool_discovery_failed", "transport_failed"):  # replan without tools, record the gap
            plan = planner.replan(plan, list(TOOL_NAMES), tools.stop_reason, [])
            tools.missing_information.append("tools were unavailable; financial data could not be collected")
    else:
        tools = ToolSelectorState(request_id=rid, completion_status="not_needed", stop_reason="planner did not request tools")

    tm["tool_selector"], t = int((time.perf_counter() - t) * 1000), time.perf_counter()
    memory.store_working(rid, plan, list(tools.executions))
    report = ReportAgent(llm, tracer=tracer).write(request_text, plan, mem, tools)
    tm["report"], tm["total"] = int((time.perf_counter() - t) * 1000), tracer.duration_ms()
    tracer.event("pipeline", "finished", report_validation=report.validation, ms=tracer.duration_ms())
    return PipelineResult(plan, mem, tools, report, timings_ms=tm, trace=list(tracer.events))
