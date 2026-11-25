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

from agents.planner.agent import is_source_chronology, names_source_turns
from agents.tool_selector.digest import digest
from agents.tool_selector.source_discovery import (MAX_REFERENCE_CALLS, MIN_BUDGET_FOR_RESERVE, SOURCE_TOOL, add_referenced_meeting, enrich_meetings, find_references, meeting_calls, meetings_from,
                                                   omission_note, plan_meetings, reference_is_relevant, search_calls, source_search_plan, subject_stems)
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
        self._all_duplicate = False
        self._productive = False

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
        await self._discover_sources(request_text, plan, catalog, validator, state, counter)
        if state.retrieval_report and state.successes() and set(plan.required_tools) <= {SOURCE_TOOL}:
            # Source discovery IS the whole job when the plan asks for nothing but source turns: the model would only be asked for more of the same calls (which a 3B model writes with mixed
            # arguments the validator refuses, and which add nothing to a chronology whose meetings were already read).  What was and was not retrieved is already in missing_information.
            state.stop_reason = "max_executions" if self._used(state) >= self.cfg.max_executions else "source_discovery_complete"
            state.completion_status = "partial" if state.missing_information else "complete"
            self.tracer.event(COMPONENT, "model_loop_skipped", reason="source discovery covered the plan", used=self._used(state), max=self.cfg.max_executions)
            return
        for it in range(1, self.cfg.max_iterations + 1):
            state.iterations = it
            if self._used(state) >= self.cfg.max_executions:          # the budget is spent (e.g. by source discovery): do not ask the model for calls that can only be refused
                state.stop_reason, state.completion_status = "max_executions", "partial"
                self.tracer.event(COMPONENT, "budget_exhausted_before_decision", iteration=it, used=self._used(state), max=self.cfg.max_executions)
                return
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
            if self._all_duplicate and state.successes():       # every call repeated a SUCCESSFUL one: nothing new can come of another turn (errors, timeouts and rejections are not duplicates)
                self._merge_missing(state, decision.missing_information)
                state.stop_reason = "no_new_valid_call"
                state.completion_status = "partial" if state.missing_information else "complete"
                return
            idle = 0 if self._productive else idle + 1
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
        ok = [f"{e.tool}({json.dumps(e.arguments, separators=(',', ':'))})" for e in state.executions if e.status == "success"]
        if ok:
            parts.append("ALREADY SUCCEEDED - never call these again: " + "; ".join(x[:110] for x in ok[-12:]))
        waiting = [t for t in plan.required_tools if t not in {e.tool for e in state.successes()}]
        if waiting and state.executions:
            parts.append(f"PLANNED TOOLS NOT YET SUCCESSFULLY CALLED: {', '.join(waiting)} - call one if the task needs it, otherwise finish.")
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
            if verdict == "ok" and msg:
                ex.rationale = (ex.rationale + " | " if ex.rationale else "") + msg
            if verdict == "ok" and n >= (limit or self.cfg.max_calls_per_iteration):
                verdict, msg = "rejected", f"too many calls in one turn (max {limit or self.cfg.max_calls_per_iteration})"
            if verdict == "ok" and executed + len(to_run) >= self.cfg.max_executions:
                verdict, msg = "rejected", f"execution budget exhausted ({executed + len(to_run)} of {self.cfg.max_executions} executions used; invalid and repeated calls do not count)"
            if verdict == "ok":
                batch_keys.add(call_key(c.tool, args, validator.tools[c.tool]["input_schema"]))
                to_run.append(ex)
            else:
                ex.status, ex.error = verdict, msg  # 'rejected' | 'duplicate'
                self.tracer.event(COMPONENT, "call_duplicate" if verdict == "duplicate" else "call_rejected", id=ex.id, tool=c.tool, arguments=c.arguments, message=msg)
            records.append(ex)
        state.executions.extend(records)
        self._all_duplicate = bool(records) and all(r.status == "duplicate" for r in records)
        blocked = len(records) - len(to_run)                                    # calls refused before running (repeats, invalid, over budget)
        self._productive = bool(to_run) and len(to_run) >= blocked           # one new call does not make a turn productive when most of its calls were repeats or invalid
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
            validator.record_success(ex.tool, ex.arguments)
        except asyncio.TimeoutError:
            ex.status, ex.error = "timeout", f"no answer within {self.cfg.call_timeout_s:g} s"
        except ToolCallError as e:
            ex.status, ex.error = "error", str(e)
        except Exception as e:
            ex.status, ex.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
        if ex.status in ("error", "timeout"):
            validator.record_failure(ex.tool, ex.arguments)
        ex.duration_ms = int((time.perf_counter() - t0) * 1000)
        self.tracer.event(COMPONENT, "tool_result", id=ex.id, tool=ex.tool, arguments=ex.arguments, status=ex.status, duration_ms=ex.duration_ms,
                          digest=digest(ex.result.model_dump(mode="json"), 300) if ex.result else ex.error)

    def _used(self, state: ToolSelectorState) -> int:
        return sum(1 for e in state.executions if e.status in ("success", "error", "timeout"))

    @staticmethod
    def _turn_texts(executions: list) -> list[dict]:
        """The turns that SUCCESSFUL conversation-mode calls returned, as {interaction_id, turn_index, speaker, text} (what find_references reads)."""
        out = []
        for e in executions:
            data = (e.result.data or {}) if e.status == "success" and e.result is not None else {}
            for t in ([data["turn"]] if isinstance(data.get("turn"), dict) else list(data.get("turns") or [])):
                if isinstance(t, dict) and t.get("interaction_id") and isinstance(t.get("turn_index"), int) and t.get("text"):
                    out.append(dict(interaction_id=t["interaction_id"], turn_index=t["turn_index"], speaker=t.get("speaker"), text=t["text"]))
        return out

    async def _retrieve_meetings(self, client: str, meetings: dict, chosen: list[str], validator: CallValidator, state: ToolSelectorState, counter: "_Counter",
                                 catalog: list[dict], retrieved: dict, failed: list, cut: list) -> int:
        """One get_source_evidence call per chosen meeting (conversation mode, through the normal validator and budget).  Records what came back in `retrieved`
        ({interaction_id: sorted turn indices}) and what failed in `failed`.  Returns the number of calls issued."""
        room = max(self.cfg.max_executions - self._used(state), 0)
        if len(chosen) > room:                                                   # never schedule a call the budget will refuse: the rest are reported as not retrieved (budget), not attempted
            for i in chosen[room:]:
                failed.append({"interaction_id": i, "date": meetings.get(i, {}).get("date", ""), "turns": sorted(meetings.get(i, {}).get("window") or meetings.get(i, {}).get("turns") or []),
                               "reason": "tool-call budget"})
            chosen = chosen[:room]
        calls, cut_now = meeting_calls(client, meetings, chosen)
        cut.extend(cut_now)
        n = len(state.executions)
        if calls:
            await self._process(calls, 0, validator, state, counter, catalog, limit=len(calls))
        for e in state.executions[n:]:
            iid = e.arguments.get("interaction_id")
            if e.status == "success":
                retrieved[iid] = sorted(set(retrieved.get(iid, [])) | set(e.arguments.get("turn_indices") or []))
            else:
                failed.append({"interaction_id": iid, "date": meetings.get(iid, {}).get("date", ""), "turns": e.arguments.get("turn_indices") or [],
                               "reason": f"the retrieval call failed ({e.status}: {str(e.error)[:120]})"})
        return len(calls)

    async def _discover_sources(self, request_text: str, plan: ExecutionPlan, catalog: list[dict], validator: CallValidator, state: ToolSelectorState, counter: "_Counter") -> None:
        """A request about what the conversations record needs real interaction ids and turn indices, which the model cannot know.  Find them with the search mode of get_source_evidence, then
        retrieve the relevant meetings, one call per MEETING, inside the tool-call budget.  Runs only when the Planner chose get_source_evidence, the request asks for a chronology / source
        turns (the Planner's own test) and it does not already name interaction turns.

        B1 (chronology-aware):
          1. search for the subject (generic request words are never searched);
          2. enrich the hits with what a phrase search cannot see: meetings whose TITLE is about the subject (a correction of the subject's record ranks first), and the turns AROUND each hit;
          3. retrieve the best meetings with all but MAX_REFERENCE_CALLS of the remaining budget;
          4. read the retrieved turns for references to other meetings (an interaction id, or a date like "March 4" that matches exactly one meeting of THIS client) and retrieve the relevant
             ones first with the calls held back (plus whatever is left);
          5. say, in `missing_information` and `retrieval_report`, what was retrieved, what was resolved, what could not be resolved and what the budget left out.
        Every call goes through the same validator and budget as a model-chosen call."""
        if SOURCE_TOOL not in plan.required_tools or len(plan.client_ids) != 1 or names_source_turns(request_text) or not is_source_chronology(request_text):
            return
        by = {t["name"]: t for t in catalog}
        props = by.get(SOURCE_TOOL, {}).get("input_schema", {}).get("properties", {})
        if "search_text" not in props or "turn_indices" not in props:
            return
        from services.conversations import shared
        conv = shared()
        client = plan.client_ids[0]
        plan_terms = source_search_plan(request_text, client, conv, {v["first_name"] for v in conv.directory().values()})
        terms = [t for t, _ in plan_terms]
        report: dict = {"terms": terms, "meetings_identified": [], "retrieved": [], "not_retrieved": [], "references_resolved": [], "references_unresolved": [],
                        "budget": {"max": self.cfg.max_executions, "searches": 0, "reserved_for_references": 0, "used": 0}}
        state.retrieval_report = report
        if not terms:
            state.missing_information.append("no searchable subject was found in the request (nothing in it matches this client's conversations), so no source turns were located")
            self.tracer.event(COMPONENT, "source_discovery", terms=[], searched=0, retrieved=0)
            return
        n0 = len(state.executions)
        await self._process(search_calls(client, terms), 0, validator, state, counter, catalog)
        found = state.executions[n0:]
        report["budget"]["searches"] = sum(1 for e in found if e.status in ("success", "error", "timeout"))
        for e in found:
            if e.status != "success":
                state.missing_information.append(f"the source search for {e.arguments.get('search_text')!r} did not run ({e.status}: {str(e.error)[:120]}), so turns that mention it may be missing")
        meetings, notes = meetings_from(found, dict(plan_terms))
        state.missing_information.extend(n[0].upper() + n[1:] for n in notes)
        enrich_meetings(meetings, conv, client, terms, request_text)
        stems = subject_stems(terms)
        report["meetings_identified"] = [{"interaction_id": i, "date": meetings[i]["date"], "found_by": meetings[i].get("found_by", "search"), "tier": meetings[i].get("tier", 0)}
                                         for i in sorted(meetings, key=lambda i: (meetings[i]["date"], i))]

        budget = max(self.cfg.max_executions - self._used(state), 0)
        reserve = MAX_REFERENCE_CALLS if (stems and budget >= MIN_BUDGET_FOR_RESERVE) else 0
        report["budget"]["reserved_for_references"] = reserve
        chosen, _ = plan_meetings(meetings, max(budget - reserve, 0))
        retrieved: dict[str, list[int]] = {}
        failed: list[dict] = []
        cut: list[dict] = []
        n1 = len(state.executions)
        issued = await self._retrieve_meetings(client, meetings, chosen, validator, state, counter, catalog, retrieved, failed, cut)
        attempted = set(chosen)

        # ---- references inside what was retrieved
        refs, unresolved = find_references(self._turn_texts(state.executions[n1:]), conv, client)
        for r in refs:
            r["relevant"], r["already_retrieved"] = reference_is_relevant(r, conv, client, stems), r["interaction_id"] in retrieved
            if r["relevant"] and not r["already_retrieved"] and r["interaction_id"] not in attempted:
                add_referenced_meeting(meetings, r, conv, client, stems)
        # ---- second round: the referenced meetings first (tier 3), then whatever the budget still allows of the rest
        candidates = {i: m for i, m in meetings.items() if i not in attempted}
        budget2 = max(self.cfg.max_executions - self._used(state), 0)
        chosen2, omitted = plan_meetings(candidates, budget2) if candidates else ([], [])
        n2 = len(state.executions)
        issued += await self._retrieve_meetings(client, meetings, chosen2, validator, state, counter, catalog, retrieved, failed, cut)
        # references found only inside the second round are recorded, not followed (no chains)
        refs2, unresolved2 = find_references(self._turn_texts(state.executions[n2:]), conv, client)
        seen_refs = {(r["interaction_id"], r["from"]) for r in refs}
        for r in refs2:
            if (r["interaction_id"], r["from"]) not in seen_refs:
                r["relevant"], r["already_retrieved"] = reference_is_relevant(r, conv, client, stems), r["interaction_id"] in retrieved
                r["not_followed"] = "found inside a referenced meeting: references are followed one level only"
                refs.append(r)
        seen_unres = {(u["from"], u["quote"]) for u in unresolved}
        unresolved += [u for u in unresolved2 if (u["from"], u["quote"]) not in seen_unres]

        # ---- what is reported
        for r in refs:
            r["retrieved"] = r["interaction_id"] in retrieved
        omitted = [o for o in omitted if o["interaction_id"] not in retrieved] + failed
        for o in omitted:
            o["found_by"] = meetings.get(o["interaction_id"], {}).get("found_by", "search")
        state.missing_information.extend(omission_note(o, self.cfg.max_executions) for o in omitted + cut)
        for r in refs:
            if r.get("relevant") and not r["retrieved"] and not r.get("not_followed"):
                state.missing_information.append(f"Referenced but not retrieved: {r['interaction_id']} ({r['date']}) is named in {r['from']} ({r['quote']!r}); the tool-call budget of {self.cfg.max_executions} was used")
        for u in unresolved:
            state.missing_information.append(f"Reference not resolved: {u['from']} mentions {u['quote']!r} ({u['why']}); no meeting was assumed")
        relevant_left = [o for o in omitted if meetings.get(o["interaction_id"], {}).get("tier", 0) >= 2 or o["found_by"] == "reference"]
        if relevant_left or cut:
            state.missing_information.append(f"The chronology may be incomplete: {len(relevant_left) + len(cut)} relevant meeting(s) or turn(s) were identified but not retrieved")
        if not meetings and not any(e.status != "success" for e in found):
            state.missing_information.append(f"no turn of this client's conversations mentions {', '.join(repr(t) for t in terms)}")

        report["retrieved"] = [{"interaction_id": i, "date": meetings[i]["date"], "turns": retrieved[i], "found_by": meetings[i].get("found_by", "search"), "tier": meetings[i].get("tier", 0)}
                               for i in sorted(retrieved, key=lambda i: (meetings[i]["date"], i))]
        report["not_retrieved"] = [{"interaction_id": o["interaction_id"], "date": o["date"], "reason": o["reason"], "found_by": o["found_by"], "tier": meetings.get(o["interaction_id"], {}).get("tier", 0)} for o in omitted]
        report["references_resolved"] = [{k: r.get(k) for k in ("from", "quote", "kind", "interaction_id", "date", "relevant", "retrieved", "not_followed")} for r in refs]
        report["references_unresolved"] = unresolved
        report["meetings_identified"] = [{"interaction_id": i, "date": meetings[i]["date"], "found_by": meetings[i].get("found_by", "search"), "tier": meetings[i].get("tier", 0)}
                                         for i in sorted(meetings, key=lambda i: (meetings[i]["date"], i))]       # refreshed: meetings added through references after the first listing are identified too
        report["budget"]["used"] = self._used(state)
        got = sum(len(v) for v in retrieved.values())
        report["counts"] = {"identified": len(meetings), "retrieved": len(retrieved), "not_retrieved": len(report["not_retrieved"]), "references_unresolved": len(unresolved),
                            "references_not_retrieved": sum(1 for r in refs if r.get("relevant") and not r["retrieved"] and not r.get("not_followed")), "calls_issued": issued, "turns_retrieved": got}
        self.tracer.event(COMPONENT, "source_discovery", terms=terms, searched=len(found), meetings_found=len(meetings), meetings_retrieved=len(retrieved), calls_issued=issued, turns_retrieved=got, budget=budget,
                          counts=report["counts"],
                          references_resolved=len(refs), references_unresolved=len(unresolved),
                          omitted=[{"interaction_id": o["interaction_id"], "turns": o["turns"], "reason": o["reason"][:60]} for o in omitted + cut])

    async def _fallback(self, plan: ExecutionPlan, catalog: list[dict], validator: CallValidator, state: ToolSelectorState, counter: "_Counter") -> None:
        """Model unavailable on iteration 1: run only the Planner's suggested tools that need nothing but client_id."""
        by = {t["name"]: t for t in catalog}
        calls = [PlannedCall(tool=t, arguments={"client_id": c}, rationale="fallback: tool suggested by the Planner")
                 for t in plan.required_tools if t in by and t != SOURCE_TOOL and set(by[t]["input_schema"].get("required", [])) == {"client_id"} for c in plan.client_ids]
        # get_source_evidence needs a selector (a search phrase or interaction/turn ids) that only discovery or a tool result can supply: a call with client_id alone is never valid
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
