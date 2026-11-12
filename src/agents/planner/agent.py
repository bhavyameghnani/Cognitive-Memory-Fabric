"""Planner Agent: advisor request -> validated, structured ExecutionPlan. Plans only; executes nothing, queries nothing."""
from __future__ import annotations

import re
import uuid
from typing import Optional

from agents.planner.topics import TOPICS, TOPIC_KEYWORDS, infer_topics
from llm.client import LLMClient, LLMError, StructuredOutputError, generate_structured
from llm.prompts import PLANNER
from orchestration.tracing import NullTracer, Tracer
from schemas.plan import ExecutionPlan, PlannerLLMOutput, Subtask
from services.conversations import Conversations, shared

TASK_HINTS = [("followup_review", r"\b(unresolved|follow.?up|open (items|questions|decisions)|pending|outstanding)\b"), ("comparison", r"\b(compare|versus|vs\.?|against)\b"),
              ("history", r"\b(evolve|change[ds]?|over the last|since|history|how did|how has)\b"), ("summarization", r"\b(summari[sz]e|summary|overview)\b"),
              ("forecast_feasibility", r"\b(can|could|should|is it possible|sufficient|enough|afford)\b"), ("calculation", r"\b(calculate|how much|ratio|allocation|concentration|return)\b")]


class PlanValidationError(ValueError):
    pass


class Planner:
    def __init__(self, llm: LLMClient, conv: Optional[Conversations] = None, tracer: Optional[Tracer] = None) -> None:
        self.llm, self.conv = llm, conv or shared()
        self.tracer = tracer or NullTracer()

    # ------------------------------------------------------------------
    def plan(self, request_text: str, available_tools: list[str], request_id: Optional[str] = None, feedback: Optional[str] = None,
             revision: int = 0, visible_clients=None) -> ExecutionPlan:
        """`visible_clients` (the harness passes the clients the caller may use) limits which clients the MODEL is told exist; None = all (no caller known).
        Clients named in the request are resolved by code, not by the model, so this changes no plan - it only stops another advisor's clients being listed in the prompt."""
        rid = request_id or "REQ-" + uuid.uuid4().hex[:8]
        clients, _ = self.conv.resolve_clients(request_text)
        notes: list[str] = []
        d = self.conv.directory()
        known = "; ".join(f"{c} = {v['first_name']}" for c, v in d.items() if visible_clients is None or c in visible_clients) or "none"
        user = (f"REQUEST: {request_text}\nKNOWN CLIENTS: {known}\nCLIENTS FOUND IN THE REQUEST: {', '.join(clients) or 'none'}\n"
                f"ALLOWED TOPICS: {', '.join(TOPICS)}\nAVAILABLE TOOLS: {', '.join(available_tools)}\n"
                f"SUBTASK AGENTS: memory_orchestrator, tool_selector, report_agent" + (f"\nREVISION FEEDBACK: {feedback}" if feedback else ""))
        try:
            out = generate_structured(self.llm, "planner", PLANNER, user, PlannerLLMOutput, retries=2, tracer=self.tracer,
                                      validator=lambda o: self._check_subtasks(o.subtasks))
        except (StructuredOutputError, LLMError) as e:
            self.tracer.event("planner", "llm_failure_heuristic_plan", error=str(e)[:300])
            notes.append("LLM planning failed; a heuristic plan was generated from keywords")
            out = self._heuristic(request_text, available_tools)
        plan = self._build(rid, request_text, out, clients, available_tools, notes, revision)
        self.tracer.event("planner", "plan_created", plan_id=plan.plan_id, clients=plan.client_ids, task_type=plan.task_type, topics=plan.memory_topics,
                          tools=plan.required_tools, order=plan.execution_order, parallel=plan.parallelizable_tasks, missing=plan.missing_information, notes=plan.planner_notes)
        return plan

    def replan(self, plan: ExecutionPlan, unavailable_tools: list[str], reason: str, available_tools: list[str], visible_clients=None) -> ExecutionPlan:
        """New plan after a tool or data source turned out to be unavailable. Never silently assumes the missing piece."""
        usable = [t for t in available_tools if t not in unavailable_tools]
        fb = f"Tools {unavailable_tools} are unavailable ({reason}). Re-plan without them and state in missing_information what can no longer be answered."
        new = self.plan(plan.request_text, usable, request_id=plan.request_id, feedback=fb, revision=plan.revision + 1, visible_clients=visible_clients)
        new.planner_notes.append(f"replanned: {reason}; unavailable tools: {unavailable_tools}")
        for t in unavailable_tools:
            if t in plan.required_tools:
                new.missing_information.append(f"tool '{t}' is unavailable, so its result cannot be provided")
        return new

    # ------------------------------------------------------------------
    @staticmethod
    def _check_subtasks(subs: list[Subtask]) -> None:
        ids = [s.id for s in subs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate subtask ids")
        for i, s in enumerate(subs):
            for dep in s.depends_on:
                if dep not in ids[:i]:
                    raise ValueError(f"subtask {s.id} depends on {dep}, which is not an earlier subtask")

    def _build(self, rid, text, out: PlannerLLMOutput, clients, available_tools, notes, revision) -> ExecutionPlan:
        topics = [t for t in out.memory_topics if t in TOPICS] or infer_topics(text)
        if len(topics) < len(out.memory_topics):
            notes.append("unknown topics dropped: " + ", ".join(t for t in out.memory_topics if t not in TOPICS))
        tools = [t for t in dict.fromkeys(out.required_tools) if t in available_tools]
        dropped = [t for t in out.required_tools if t not in available_tools]
        if dropped:
            notes.append("tools not in the catalogue were dropped: " + ", ".join(dropped))
        missing = list(out.missing_information)
        if not clients:
            missing.append("client not identified: ask the advisor which client the request is about")
        subs = list(out.subtasks)
        reports = [s for s in subs if s.agent == "report_agent"]
        others = [s for s in subs if s.agent != "report_agent"]
        if not reports:
            notes.append("report subtask added")
        elif subs[-1].agent != "report_agent" or len(reports) > 1:
            notes.append("report subtask moved last and made dependent on all others")
        # canonical shape: work subtasks in the model's order, then ONE report subtask depending on all of them (ids renumbered)
        idmap = {s.id: f"T{i}" for i, s in enumerate(others, 1)}
        subs = [s.model_copy(update={"id": idmap[s.id], "depends_on": [idmap[d] for d in s.depends_on if d in idmap]}) for s in others]
        rep_desc = reports[-1].description if reports else "Write the advisor-ready report from memory, evidence and tool results"
        subs.append(Subtask(id=f"T{len(subs) + 1}", description=rep_desc, agent="report_agent", depends_on=[s.id for s in subs]))
        if "calculation" in out.task_type or out.needs_calculation:
            if not tools:
                missing.append("calculation requested but no suitable tool was identified")
        deps = {s.id: list(s.depends_on) for s in subs}
        order, levels = self._topo(deps)
        return ExecutionPlan(
            request_id=rid, plan_id=f"PLAN-{rid}-r{revision}", request_text=text, client_ids=clients, user_intent=out.user_intent, task_type=out.task_type,
            time_period=out.time_period, needs_history=out.needs_history, needs_calculation=out.needs_calculation, subtasks=subs,
            required_memory_categories=list(dict.fromkeys(out.required_memory_categories)) or ["episodic", "semantic"], memory_topics=topics,
            required_data=out.required_data, required_tools=tools, dependencies=deps, execution_order=order,
            parallelizable_tasks=[lv for lv in levels if len(lv) > 1], missing_information=missing, success_criteria=out.success_criteria,
            revision=revision, planner_notes=notes)

    @staticmethod
    def _topo(deps: dict[str, list[str]]) -> tuple[list[str], list[list[str]]]:
        done: list[str] = []
        levels: list[list[str]] = []
        remaining = dict(deps)
        while remaining:
            ready = [k for k, v in remaining.items() if all(d in done for d in v)]
            if not ready:
                raise PlanValidationError("cyclic dependencies in plan")
            levels.append(ready)
            done += ready
            for k in ready:
                del remaining[k]
        return done, levels

    def _heuristic(self, text: str, tools: list[str]) -> PlannerLLMOutput:
        t = text.lower()
        task = next((k for k, rx in TASK_HINTS if re.search(rx, t)), "retrieval")
        topics = infer_topics(text)
        calc = task in ("forecast_feasibility", "calculation", "comparison") or bool(re.search(r"portfolio|allocation|liquidity|goal|return", t))
        want = []
        for kw, tool in [("portfolio", "get_client_portfolio"), ("allocation", "calculate_asset_allocation"), ("concentrat", "calculate_portfolio_concentration"),
                         ("liquid", "calculate_liquidity_ratio"), ("goal", "get_client_goals"), ("retire", "calculate_goal_funding_gap"), ("return", "calculate_portfolio_returns")]:
            if tool == "calculate_goal_funding_gap" and task not in ("forecast_feasibility", "calculation"):
                continue
            if kw in t and tool in tools:
                want.append(tool)
        subs = [Subtask(id="T1", description="Retrieve relevant client memories", agent="memory_orchestrator")]
        if calc:
            subs.append(Subtask(id="T2", description="Collect and calculate the needed financial data with tools", agent="tool_selector"))
        subs.append(Subtask(id="T3" if calc else "T2", description="Write the advisor-ready report", agent="report_agent", depends_on=[s.id for s in subs]))
        return PlannerLLMOutput(user_intent=text[:280], task_type=task, time_period=None, needs_history=task in ("history", "summarization", "forecast_feasibility"),
                                needs_calculation=calc, required_memory_categories=["episodic", "semantic"], memory_topics=topics, required_data=[],
                                required_tools=want, subtasks=subs, missing_information=[], success_criteria=["answer the question using cited evidence"])
