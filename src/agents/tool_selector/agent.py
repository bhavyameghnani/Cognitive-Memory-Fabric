"""Tool Selector Agent: decides which MCP tools to call, with which arguments, in what order, and when to stop.

The model only chooses; code validates (validator.py), executes concurrently through the Toolbox and enforces every limit (spec section F).
There is no fixed call sequence. The only non-LLM path is the iteration-1 fallback to the tools the Planner suggested.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional

from agents.tool_selector.digest import digest
from agents.tool_selector.prompts import SELECTOR, render_catalogue
from agents.tool_selector.toolbox import ToolCallError, Toolbox
from agents.tool_selector.validator import CallValidator, call_key
from llm.client import LLMClient, LLMError, StructuredOutputError, generate_structured
from orchestration.tracing import NullTracer, Tracer
from schemas.plan import ExecutionPlan
from schemas.tools import PlannedCall, SelectorDecision, ToolExecution, ToolSelectorState

COMPONENT = "tool_selector"


@dataclass
class SelectorConfig:
    max_iterations: int = 5
    max_executions: int = 12
    max_calls_per_iteration: int = 5
    output_retries: int = 2
    call_timeout_s: float = 90.0
    max_idle_iterations: int = 2  # stop after this many consecutive iterations without a new valid call
    digest_chars: int = 600
    history_chars: int = 3500


class ToolSelector:
    def __init__(self, llm: LLMClient, toolbox: Toolbox, tracer: Optional[Tracer] = None, config: Optional[SelectorConfig] = None) -> None:
        self.llm, self.toolbox = llm, toolbox
        self.tracer = tracer or NullTracer()
        self.cfg = config or SelectorConfig()

    def run_sync(self, request_text: str, plan: ExecutionPlan, memory_summary: dict) -> ToolSelectorState:
        return asyncio.run(self.run(request_text, plan, memory_summary))

    # ------------------------------------------------------------------
    async def run(self, request_text: str, plan: ExecutionPlan, memory_summary: dict) -> ToolSelectorState:
        state = ToolSelectorState(request_id=plan.request_id)
        t0 = time.perf_counter()
        try:
            async with self.toolbox:
                await self._loop(request_text, plan, memory_summary, state)
        except (Exception, BaseExceptionGroup) as e:  # tool discovery / transport failure: the orchestrator may replan without tools
            state.completion_status, state.stop_reason = "failed", "tool_discovery_failed" if not state.tools_discovered else "transport_failed"
            state.errors.append("; ".join(f"{type(x).__name__}: {str(x)[:300]}" for x in _leaves(e)))
        self.tracer.event(COMPONENT, "finished", status=state.completion_status, stop_reason=state.stop_reason, iterations=state.iterations,
                          fallback_used=state.fallback_used, errors=state.errors, duration_ms=int((time.perf_counter() - t0) * 1000),
                          calls=[{"id": e.id, "tool": e.tool, "status": e.status} for e in state.executions], missing_information=state.missing_information)
        return state

    async def _loop(self, request_text: str, plan: ExecutionPlan, memory: dict, state: ToolSelectorState) -> None:
        catalog = await self.toolbox.list_tools()
        state.tools_discovered = [t["name"] for t in catalog]
        self.tracer.event(COMPONENT, "tools_discovered", tools=state.tools_discovered)
        if not plan.client_ids:
            state.completion_status, state.stop_reason = "failed", "no_client_in_plan"
            state.missing_information.append("client not identified: ask the advisor which client the request is about")
            return
        validator = CallValidator(catalog, plan.client_ids)
        counter = _Counter()
        idle = 0
        for it in range(1, self.cfg.max_iterations + 1):
            state.iterations = it
            try:
                decision = await asyncio.to_thread(self._decide, request_text, plan, memory, catalog, state, it)
            except (StructuredOutputError, LLMError) as e:
                state.errors.append(f"model unavailable at iteration {it}: {str(e)[:300]}")
                self.tracer.event(COMPONENT, "model_failure", iteration=it, error=str(e)[:300])
                if it == 1:
                    state.fallback_used = True
                    await self._fallback(plan, catalog, validator, state, counter)
                    state.completion_status = "partial" if state.successes() else "failed"
                    state.stop_reason = "model_unavailable_fallback"
                else:
                    state.completion_status = "partial" if state.successes() else "failed"
                    state.stop_reason = "model_failed"
                return
            self.tracer.event(COMPONENT, "decision", iteration=it, action=decision.action, reasoning=decision.reasoning, missing_information=decision.missing_information,
                              calls=[{"tool": c.tool, "arguments": c.arguments, "rationale": c.rationale} for c in decision.calls])
            if decision.action == "finish":
                self._merge_missing(state, decision.missing_information)
                state.stop_reason = "model_finished"
                state.completion_status = ("not_needed" if not state.executions else "failed" if not state.successes() else
                                           "partial" if state.missing_information else "complete")
                return
            valid = await self._process(decision.calls, it, validator, state, counter, catalog)
            idle = 0 if valid else idle + 1
            executed = sum(1 for e in state.executions if e.status in ("success", "error", "timeout"))
            if executed >= self.cfg.max_executions:
                state.stop_reason, state.completion_status = "max_executions", "partial"
                return
            if idle >= self.cfg.max_idle_iterations:
                state.stop_reason, state.completion_status = "no_new_valid_call", "partial"
                return
        state.stop_reason, state.completion_status = "max_iterations", "partial"

    # ------------------------------------------------------------------ model step (sync, run in a thread)
    def _decide(self, request_text, plan, memory, catalog, state, it) -> SelectorDecision:
        def check(d: SelectorDecision) -> None:
            if d.action == "call_tools" and not d.calls:
                raise ValueError("action 'call_tools' needs at least one call; use 'finish' if nothing more is needed")

        return generate_structured(self.llm, "tool_selector", SELECTOR, self._prompt(request_text, plan, memory, catalog, state, it), SelectorDecision,
                                   retries=self.cfg.output_retries, tracer=self.tracer, validator=check)

    def _prompt(self, request_text, plan, memory, catalog, state, it) -> str:
        done = sum(1 for e in state.executions if e.status in ("success", "error", "timeout"))
        parts = [f"REQUEST: {request_text}",
                 f"PLAN: clients={plan.client_ids}; task_type={plan.task_type}; needs: {', '.join(plan.required_data) or 'n/a'}; "
                 f"suggested_tools={plan.required_tools}; planner_missing={plan.missing_information}",
                 f"MEMORY (what the client already told us): {json.dumps(memory, ensure_ascii=False, default=str)[:1200]}",
                 f"TOOL CATALOGUE:\n{render_catalogue(catalog)}",
                 f"ITERATION {it} of {self.cfg.max_iterations}; executions used {done} of {self.cfg.max_executions}; max {self.cfg.max_calls_per_iteration} calls per turn."]
        hist = self._history(state)
        parts.append("RESULTS SO FAR:\n" + hist if hist else "RESULTS SO FAR: none yet.")
        parts.append("Return the JSON decision now.")
        return "\n".join(parts)

    def _history(self, state: ToolSelectorState) -> str:
        lines, used = [], 0
        for e in reversed(state.executions):  # newest first so truncation drops the oldest
            body = digest(e.result.model_dump(mode="json"), self.cfg.digest_chars) if e.result else (e.error or "")
            line = f"[{e.id}] it{e.iteration} {e.tool}({json.dumps(e.arguments, separators=(',', ':'))}) -> {e.status}: {body}"
            if used + len(line) > self.cfg.history_chars and lines:
                lines.append("(older results omitted)")
                break
            lines.append(line)
            used += len(line)
        return "\n".join(reversed(lines))

    # ------------------------------------------------------------------ validate and execute
    async def _process(self, calls: list[PlannedCall], it: int, validator: CallValidator, state: ToolSelectorState, counter: "_Counter", catalog: list[dict], limit: Optional[int] = None) -> bool:
        executed = sum(1 for e in state.executions if e.status in ("success", "error", "timeout"))
        batch_keys: set[str] = set()
        records: list[ToolExecution] = []
        to_run: list[ToolExecution] = []
        for n, c in enumerate(calls):
            ex = ToolExecution(id=counter.next(state.request_id), iteration=it, tool=c.tool, arguments=dict(c.arguments), status="rejected", rationale=c.rationale)
            verdict, msg, args = validator.validate(c, batch_keys)
            ex.arguments = args or ex.arguments
            if verdict == "ok" and n >= (limit or self.cfg.max_calls_per_iteration):
                verdict, msg = "rejected", f"too many calls in one turn (max {limit or self.cfg.max_calls_per_iteration})"
            if verdict == "ok" and executed + len(to_run) >= self.cfg.max_executions:
                verdict, msg = "rejected", "execution budget exhausted"
            if verdict == "ok":
                batch_keys.add(call_key(c.tool, args, validator.tools[c.tool]["input_schema"]))
                to_run.append(ex)
            else:
                ex.status, ex.error = verdict, msg  # 'rejected' | 'duplicate'
                self.tracer.event(COMPONENT, "call_duplicate" if verdict == "duplicate" else "call_rejected", id=ex.id, tool=c.tool, arguments=c.arguments, message=msg)
            records.append(ex)
        state.executions.extend(records)
        if to_run:
            await asyncio.gather(*(self._execute(ex, validator) for ex in to_run))
        return bool(to_run)

    async def _execute(self, ex: ToolExecution, validator: CallValidator) -> None:
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(self.toolbox.call(ex.tool, ex.arguments), timeout=self.cfg.call_timeout_s)
            from schemas.tools import Envelope
            ex.result, ex.status = Envelope.model_validate(res), "success"
            validator.done.add(call_key(ex.tool, ex.arguments, validator.tools[ex.tool]["input_schema"]))
        except asyncio.TimeoutError:
            ex.status, ex.error = "timeout", f"no answer within {self.cfg.call_timeout_s:g} s"
        except ToolCallError as e:
            ex.status, ex.error = "error", str(e)
        except Exception as e:
            ex.status, ex.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
        ex.duration_ms = int((time.perf_counter() - t0) * 1000)
        self.tracer.event(COMPONENT, "tool_result", id=ex.id, tool=ex.tool, arguments=ex.arguments, status=ex.status, duration_ms=ex.duration_ms,
                          digest=digest(ex.result.model_dump(mode="json"), 300) if ex.result else ex.error)

    async def _fallback(self, plan: ExecutionPlan, catalog: list[dict], validator: CallValidator, state: ToolSelectorState, counter: "_Counter") -> None:
        """Model unavailable on iteration 1: run only the Planner's suggested tools that need nothing but client_id."""
        by = {t["name"]: t for t in catalog}
        calls = [PlannedCall(tool=t, arguments={"client_id": c}, rationale="fallback: tool suggested by the Planner")
                 for t in plan.required_tools if t in by and set(by[t]["input_schema"].get("required", [])) == {"client_id"} for c in plan.client_ids]
        self.tracer.event(COMPONENT, "fallback", calls=[{"tool": c.tool, "arguments": c.arguments} for c in calls])
        if calls:
            await self._process(calls, 1, validator, state, counter, catalog, limit=self.cfg.max_executions)
        else:
            state.errors.append("fallback: the plan suggests no tool that needs only client_id")

    @staticmethod
    def _merge_missing(state: ToolSelectorState, items: list[str]) -> None:
        for m in items:
            if m and m not in state.missing_information:
                state.missing_information.append(m)


def _leaves(e: BaseException) -> list[BaseException]:
    """Flatten the ExceptionGroups that the MCP client's task groups wrap errors in."""
    if isinstance(e, BaseExceptionGroup):
        return [x for sub in e.exceptions for x in _leaves(sub)]
    return [e]


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self, request_id: str) -> str:
        self.n += 1
        return f"EX-{request_id}-{self.n:02d}"
