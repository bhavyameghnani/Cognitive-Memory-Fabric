"""Harness integrated with the real pipeline: one trace / budget / telemetry across the four agents, permission enforcement, controlled exhaustion,
plan-driven execution, human approval, observability.  Scripted models only (no network)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from test_memory_support import CONV, KENJI, KENJI_MEETINGS, KENJI_SCRIPT, REBECCA, make  # noqa: E402

from agents.memory.agent import MemoryOrchestrator  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from harness import AuthContext, Harness, HarnessConfig, PermissionDenied  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from orchestration.pipeline import plan_order, run_pipeline  # noqa: E402

K_ADV, R_ADV = "ADVISOR-001", "ADVISOR-002"


def planner_out(task="history", tools=False, memory=True, topics=("retirement_age",), calc=False, required_tools=()):
    subs = []
    if memory:
        subs.append({"id": "T1", "description": "retrieve client memory", "agent": "memory_orchestrator"})
    if tools:
        subs.append({"id": f"T{len(subs) + 1}", "description": "collect and calculate data", "agent": "tool_selector", "depends_on": [s["id"] for s in subs]})
    subs.append({"id": f"T{len(subs) + 1}", "description": "write the report", "agent": "report_agent", "depends_on": [s["id"] for s in subs]})
    return dict(user_intent="why did the retirement age change", task_type=task, time_period=None, needs_history=True, needs_calculation=calc,
                required_memory_categories=["semantic", "episodic"], memory_topics=list(topics), required_data=[], required_tools=list(required_tools),
                subtasks=subs, missing_information=[], success_criteria=["answer with cited evidence"])


def kenji_memory():
    orch, store, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    return orch


def report_out(findings=None, summary="Kenji's retirement age changed from 65 to 63."):
    return {"summary": summary, "key_findings": findings or [], "historical_context": [], "financial_analysis": [], "uncertainties": [], "suggested_followups": [], "claims": []}


def current_ref(res_or_report):
    rep = getattr(res_or_report, "report", res_or_report)
    return next(e.ref for e in rep.evidence if e.memory_type == "semantic" and e.status == "active")


def harness(**kw):
    return Harness(HarnessConfig.for_test(**kw))


ADMIN = AuthContext.for_test("admin-1", "admin")
KENJI_ADVISOR = AuthContext.for_test(K_ADV, "advisor")


# ------------------------------------------------------------------------------------------------------------------ shared trace
def test_all_four_agents_and_memory_events_share_one_request_trace():
    mem = kenji_memory()
    tool_calls = [{"action": "call_tools", "reasoning": "r", "missing_information": [], "calls": [
        {"tool": "get_client_goals", "arguments": {"client_id": KENJI}, "rationale": "goals"}]}, {"action": "finish", "calls": [], "missing_information": [], "reasoning": "done"}]
    llm = MockLLM(handlers={"planner": [planner_out(tools=True, required_tools=["get_client_goals"])], "tool_selector": tool_calls, "report": [report_out()]})
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, request_id="REQ-SHARED", harness=harness(), auth=ADMIN)
    comps = {e["component"] for e in res.trace}
    assert {"planner", "memory", "tool_selector", "report", "pipeline"} <= comps                      # all four agents, ONE trace
    assert {"harness", "harness.llm", "harness.permission", "harness.tool", "harness.context", "harness.evidence", "harness.trace"} <= comps
    assert {e["request_id"] for e in res.trace} == {"REQ-SHARED"}
    assert any(e["component"] == "memory" and e["event"] == "retrieved" for e in res.trace)          # the old private-NullTracer bug
    assert res.harness["budget"]["llm_calls"] == 4                                                    # planner 1 + tool selector 2 + report 1
    by = res.harness["budget"]["llm_calls_by_agent"]
    assert set(by) == {"planner", "tool_selector", "report"} and by["tool_selector"] == 2


def test_memory_events_reach_the_request_trace_even_without_a_harness():
    res = run_pipeline("Why did Kenji's retirement age change?", MockLLM(handlers={"planner": [planner_out()], "report": [report_out()]}), kenji_memory(), persist_trace=False)
    assert any(e["component"] == "memory" and e["event"] == "retrieved" for e in res.trace)
    assert res.harness == {} and res.released is True


def test_trace_answers_which_memories_and_evidence_influenced_the_response():
    mem = kenji_memory()
    p = planner_out()
    llm = MockLLM(handlers={"planner": [p], "report": lambda s, u, sc: report_out()})
    first = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(), auth=ADMIN)
    ref = current_ref(first)
    llm2 = MockLLM(handlers={"planner": [p], "report": [report_out([{"text": "Kenji's current retirement age is 63", "refs": [ref]}])]})
    res = run_pipeline("Why did Kenji's retirement age change?", llm2, mem, persist_trace=False, harness=harness(), auth=ADMIN)
    inf = next(e["payload"] for e in res.trace if e["event"] == "influence")
    assert inf["memories_retrieved"] and inf["memories_shown_to_model"] and set(inf["memories_shown_to_model"]) <= set(inf["memories_retrieved"])
    assert inf["memories_cited_by_final_claims"] and set(inf["memories_cited_by_final_claims"]) <= set(inf["memories_shown_to_model"])
    assert inf["claim_evidence"] and inf["claim_evidence"][0]["refs"] == [ref] and inf["claim_evidence"][0]["memory_ids"]
    assert res.report.key_findings and res.report.key_findings[0].refs == [ref]
    tok = [e["payload"] for e in res.trace if e["component"] == "harness.llm"]
    assert tok and all("input_tokens" in t and t["input_tokens"] > 0 and "latency_ms" in t and "usage_exact" in t for t in tok)   # token counts survive redaction


def test_unsupported_claim_is_removed_before_the_final_response():
    mem = kenji_memory()
    p = planner_out()
    probe = run_pipeline("Why did Kenji's retirement age change?", MockLLM(handlers={"planner": [p], "report": lambda s, u, sc: report_out()}), mem, persist_trace=False,
                         harness=harness(), auth=ADMIN)
    ref = current_ref(probe)
    llm = MockLLM(handlers={"planner": [p], "report": [report_out([{"text": "Kenji's current retirement age is 59", "refs": [ref]}])]})
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(), auth=ADMIN)
    assert not any("59" in f.text for f in res.report.key_findings)
    # P6.2.1: the uncertainty names the reason CODE; the refused figure (59) is not repeated in it (the detailed reason stays in the server-side trace)
    withheld = [u for u in res.report.uncertainties if u.startswith("Withheld unsupported")]
    assert withheld and all("VALUE_MISMATCH" in u and "59" not in u for u in withheld)
    assert res.harness["evidence"]["unsupported"] >= 1


# ------------------------------------------------------------------------------------------------------------------ permissions
def test_cross_client_request_is_denied_before_any_model_call():
    llm = MockLLM(handlers={"planner": [planner_out()]})
    mem = kenji_memory()
    with pytest.raises(PermissionDenied) as e:
        run_pipeline("What did Rebecca decide about her business?", llm, mem, persist_trace=False, harness=harness(), auth=KENJI_ADVISOR)
    assert e.value.code == "client_not_in_scope" and llm.calls == []                               # nothing spent, nothing retrieved


def test_authorized_advisor_succeeds_and_production_refuses_missing_identity():
    mem = kenji_memory()
    ok = run_pipeline("Why did Kenji's retirement age change?", MockLLM(handlers={"planner": [planner_out()], "report": [report_out()]}), mem, persist_trace=False,
                      harness=harness(), auth=KENJI_ADVISOR)
    assert ok.report is not None
    prod = Harness(HarnessConfig())
    for auth in (None, AuthContext(K_ADV, "advisor", authenticated=False, source="header-asserted")):
        llm = MockLLM(handlers={"planner": [planner_out()]})
        with pytest.raises(PermissionDenied):
            run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=prod, auth=auth)
        assert llm.calls == []


def test_unauthorized_tool_call_never_reaches_the_tool_server():
    mem = kenji_memory()
    calls = [{"action": "call_tools", "reasoning": "r", "missing_information": [], "calls": [{"tool": "get_client_goals", "arguments": {"client_id": KENJI}, "rationale": "g"}]},
             {"action": "finish", "calls": [], "missing_information": [], "reasoning": "done"}]
    llm = MockLLM(handlers={"planner": [planner_out(tools=True, required_tools=["get_client_goals"])], "tool_selector": calls, "report": [report_out()]})
    auth = AuthContext.for_test(K_ADV, "advisor", tools=["get_client_profile"])                        # goals not granted
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(), auth=auth)
    ex = res.tools.executions[0]
    assert ex.status == "error" and "permission denied" in ex.error and res.harness["budget"]["tool_calls"] == 0


# ------------------------------------------------------------------------------------------------------------------ budgets
def test_budget_exhaustion_gives_a_controlled_partial_result_not_a_fabricated_one():
    mem = kenji_memory()
    llm = MockLLM(handlers={"planner": [planner_out()], "report": lambda s, u, sc: report_out([{"text": "made up", "refs": ["E1"]}])})
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(max_llm_calls=1), auth=ADMIN)
    assert res.harness["budget"]["exhausted"][0]["reason"] == "max_llm_calls"
    assert res.report.validation["mode"].startswith("deterministic")                                  # report fell back to code, the model was never asked
    assert any("Resource budget exhausted" in x for x in res.report.limitations)
    assert res.report.evidence                                                                         # already-verified evidence is preserved
    assert not any(c["agent"] == "report" for c in llm.calls)


def test_tool_call_cap_flows_into_the_selector_and_the_shared_budget():
    mem = kenji_memory()
    calls = [{"action": "call_tools", "reasoning": "r", "missing_information": [], "calls": [
        {"tool": "get_client_goals", "arguments": {"client_id": KENJI}, "rationale": "a"},
        {"tool": "get_client_liabilities", "arguments": {"client_id": KENJI}, "rationale": "b"},
        {"tool": "get_client_profile", "arguments": {"client_id": KENJI}, "rationale": "c"}]},
        {"action": "finish", "calls": [], "missing_information": [], "reasoning": "done"}]
    llm = MockLLM(handlers={"planner": [planner_out(tools=True)], "tool_selector": calls, "report": [report_out()]})
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(max_tool_calls=1), auth=ADMIN)
    st = sorted(e.status for e in res.tools.executions)
    assert st.count("success") == 1 and st.count("rejected") == 2 and res.harness["budget"]["tool_calls"] == 1     # SelectorConfig.max_executions derived from the shared cap


def test_harness_toolbox_enforces_the_shared_tool_budget_even_if_the_selector_is_permissive():
    import asyncio
    from agents.tool_selector.toolbox import ToolCallError
    from harness.budget import RequestBudget
    from harness.permission_gate import PermissionGate
    from harness.tools import HarnessToolbox
    from orchestration.tracing import Tracer

    class Inner:
        n = 0

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

        async def call(self, name, args):
            Inner.n += 1
            return {"tool": name, "client_id": args.get("client_id"), "data": {}}

    cfg = HarnessConfig.for_test(max_tool_calls=2, max_tool_calls_per_agent=2)
    tb = HarnessToolbox(Inner(), budget=RequestBudget(cfg), gate=PermissionGate(cfg, CONV.directory), auth=ADMIN, emit=Tracer("T", persist=False).event)

    async def go():
        out = []
        for _ in range(3):
            try:
                out.append(await tb.call("get_client_goals", {"client_id": KENJI}))
            except ToolCallError as e:
                out.append(str(e))
        return out
    out = asyncio.run(go())
    assert isinstance(out[0], dict) and isinstance(out[1], dict) and "max_tool_calls" in out[2] and Inner.n == 2
    assert tb.budget.exhausted[0]["reason"] == "max_tool_calls"
    # a result that belongs to a different client than the authorised one is dropped
    auth = AuthContext.for_test(K_ADV, "advisor")

    class Leaky(Inner):
        async def call(self, name, args):
            return {"tool": name, "client_id": REBECCA, "data": {}}
    tb2 = HarnessToolbox(Leaky(), budget=RequestBudget(cfg), gate=PermissionGate(cfg, CONV.directory), auth=auth, emit=lambda *a, **k: None)
    with pytest.raises(ToolCallError):
        asyncio.run(tb2.call("get_client_goals", {"client_id": KENJI}))


def test_selector_limits_are_enforced_inside_the_shared_budget():
    from harness import RequestBudget
    rt = harness(max_tool_iterations=2, max_tool_calls=3, max_tool_seconds=7.0).start("R", __import__("orchestration.tracing", fromlist=["Tracer"]).Tracer("R", persist=False), ADMIN)
    sc = rt.selector_config()
    assert (sc.max_iterations, sc.max_executions, sc.call_timeout_s) == (2, 3, 7.0) and isinstance(rt.budget, RequestBudget)


def test_retries_inside_the_pipeline_count_toward_the_shared_budget():
    mem = kenji_memory()
    llm = MockLLM(handlers={"planner": ["bad", "bad", "bad", planner_out()], "report": lambda s, u, sc: report_out()})
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(max_retries=1), auth=ADMIN)
    assert res.harness["budget"]["retries"] == 1 and res.harness["budget"]["exhausted"][0]["reason"] == "max_retries"
    assert any("heuristic plan" in n for n in res.plan.planner_notes)                                  # planner degraded gracefully


# ------------------------------------------------------------------------------------------------------------------ plan-driven execution
def test_execution_follows_the_validated_plan():
    mem = kenji_memory()
    plan_no_memory = planner_out(memory=False, tools=False)
    llm = MockLLM(handlers={"planner": [plan_no_memory], "report": [report_out()]})
    res = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=harness(), auth=ADMIN)
    assert res.memory == {} and any(e["event"] == "memory_skipped" for e in res.trace)
    assert not any(e["component"] == "memory" and e["event"] == "retrieved" for e in res.trace)
    assert plan_order(res.plan) == []
    llm2 = MockLLM(handlers={"planner": [planner_out(tools=True)], "tool_selector": [{"action": "finish", "calls": [], "missing_information": [], "reasoning": "x"}], "report": [report_out()]})
    res2 = run_pipeline("Why did Kenji's retirement age change?", llm2, mem, persist_trace=False, harness=harness(), auth=ADMIN)
    assert plan_order(res2.plan) == ["memory_orchestrator", "tool_selector"] and res2.memory


# ------------------------------------------------------------------------------------------------------------------ approval
def test_suitability_question_is_withheld_until_a_supervisor_approves():
    mem = kenji_memory()
    h = harness()
    p = planner_out(task="forecast_feasibility", topics=("retirement_age",))
    llm = MockLLM(handlers={"planner": [p], "report": lambda s, u, sc: report_out()})
    res = run_pipeline("Should Kenji retire at 60?", llm, mem, persist_trace=False, harness=h, auth=KENJI_ADVISOR)
    assert res.released is False and res.report.key_findings == [] and "withheld pending human approval" in res.report.summary
    apr = res.harness["approval"]
    assert apr["status"] == "pending" and apr["requested_by"] == K_ADV
    with pytest.raises(PermissionDenied):
        h.decide_approval(apr["approval_id"], KENJI_ADVISOR, True, "self")                              # advisors cannot approve
    h.decide_approval(apr["approval_id"], AuthContext.for_test("SUP-1", "supervisor"), True, "reviewed")
    draft = h.release(apr["approval_id"], res.harness_dv if hasattr(res, "harness_dv") else next(e["payload"]["data_version"] for e in res.trace if e["event"] == "request_started"))
    assert draft.summary and "withheld" not in draft.summary
    with pytest.raises(PermissionDenied) as e:
        h.release(apr["approval_id"], "mem:CHANGED|ops:CHANGED")                                       # data changed after approval -> invalid
    assert e.value.code == "approval_invalidated"


def test_non_sensitive_question_is_released_without_approval():
    res = run_pipeline("Why did Kenji's retirement age change?", MockLLM(handlers={"planner": [planner_out()], "report": [report_out()]}), kenji_memory(), persist_trace=False,
                       harness=harness(), auth=ADMIN)
    assert res.released and res.harness["approval"] is None


# ------------------------------------------------------------------------------------------------------------------ caching in the pipeline
def test_second_identical_request_is_served_from_cache_with_no_model_call():
    mem = kenji_memory()
    h = harness()
    calls = {"n": 0}

    def planner(s, u, sc):
        calls["n"] += 1
        return planner_out()
    llm = MockLLM(handlers={"planner": planner, "report": lambda s, u, sc: report_out()})
    run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=h, auth=ADMIN)
    second = run_pipeline("Why did Kenji's retirement age change?", llm, mem, persist_trace=False, harness=h, auth=ADMIN)
    assert calls["n"] == 1 and second.harness["telemetry"]["cache_hits"] >= 1
    assert second.harness["budget"]["llm_calls"] < 2


# ------------------------------------------------------------------------------------------------------------------ temporal conflicts reach the report
def test_conflicts_are_exposed_to_the_report_agent():
    mem = kenji_memory()
    res = run_pipeline("Why did Kenji's retirement age change?", MockLLM(handlers={"planner": [planner_out()], "report": [report_out()]}), mem, persist_trace=False,
                       harness=harness(), auth=ADMIN)
    ev = [e["payload"] for e in res.trace if e["event"] == "conflicts"]
    assert ev and any(c["kind"] == "replacement" for c in ev[0]["found"])
    assert res.memory[KENJI].history and res.memory[KENJI].history[0].value_text == "65"                  # history preserved, not overwritten
