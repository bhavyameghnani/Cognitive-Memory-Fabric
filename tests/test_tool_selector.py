"""Tool Selector tests with a SCRIPTED model and the real in-process MCP server.

A scripted model proves the loop, validation, limits and trace behave as specified and that the three spec scenarios are *possible*;
it does NOT prove that the real Qwen2.5 3B model selects these tools reliably. Measure that separately with the real model.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.tool_selector.agent import SelectorConfig, ToolSelector  # noqa: E402
from agents.tool_selector.toolbox import Toolbox  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from orchestration.tracing import Tracer  # noqa: E402
from schemas.memory import MemoryContext, MemoryItem  # noqa: E402
from schemas.plan import ExecutionPlan, Subtask  # noqa: E402

K, R = "CLIENT-001", "CLIENT-002"


def plan(clients=(K,), tools=(), rid="REQ-test", **kw):
    base = dict(request_id=rid, plan_id="PLAN-1", request_text="q", client_ids=list(clients), user_intent="answer", task_type="calculation", needs_history=False,
                needs_calculation=True, subtasks=[Subtask(id="T1", description="collect data", agent="tool_selector")], required_memory_categories=["semantic"],
                memory_topics=[], required_data=[], required_tools=list(tools), dependencies={"T1": []}, execution_order=["T1"], parallelizable_tasks=[],
                missing_information=[], success_criteria=["done"])
    base.update(kw)
    return ExecutionPlan(**base)


def call(tool, rationale="needed", **args):
    return {"tool": tool, "arguments": args, "rationale": rationale}


def decide(*calls, finish=False, missing=()):
    return {"action": "finish" if finish else "call_tools", "calls": list(calls), "missing_information": list(missing), "reasoning": "r"}


def finish(*missing):
    return decide(finish=True, missing=missing)


def run(script, p=None, cfg=None, memory=None, toolbox=None, text="question"):
    llm = MockLLM(handlers={"tool_selector": script if isinstance(script, list) else script})
    tr = Tracer("REQ-test", persist=False)
    sel = ToolSelector(llm, toolbox or Toolbox(), tracer=tr, config=cfg or SelectorConfig())
    st = sel.run_sync(text, p or plan(), memory or {})
    return st, tr, llm


def tools_by_iter(st):
    out = {}
    for e in st.executions:
        out.setdefault(e.iteration, []).append((e.tool, e.status))
    return out


class Loop(unittest.TestCase):
    def test_sequential_then_finish(self):
        st, tr, llm = run([decide(call("get_client_profile", client_id=K)), decide(call("calculate_liquidity_ratio", client_id=K)), finish()])
        self.assertEqual([(e.id, e.iteration, e.tool, e.status) for e in st.executions],
                         [("EX-REQ-test-01", 1, "get_client_profile", "success"), ("EX-REQ-test-02", 2, "calculate_liquidity_ratio", "success")])
        self.assertEqual((st.completion_status, st.stop_reason, st.iterations, st.fallback_used), ("complete", "model_finished", 3, False))
        self.assertEqual(st.executions[1].result.data["cash_months"], 13.21)
        self.assertEqual(len(st.tools_discovered), 13)
        # results are fed back into the next prompt
        self.assertIn("EX-REQ-test-01", llm.calls[1]["user"])
        self.assertIn("get_client_profile", llm.calls[1]["user"])

    def test_parallel_independent_calls_one_iteration(self):
        st, _, _ = run([decide(call("get_client_profile", client_id=K), call("get_client_goals", client_id=K), call("get_client_portfolio", client_id=K),
                               call("get_client_liabilities", client_id=K)), finish()])
        self.assertEqual({e.tool for e in st.executions if e.iteration == 1}, {"get_client_profile", "get_client_goals", "get_client_portfolio", "get_client_liabilities"})
        self.assertTrue(all(e.status == "success" for e in st.executions))

    def test_calls_actually_run_concurrently(self):
        class Slow(Toolbox):
            async def call(self, name, arguments):
                await asyncio.sleep(0.3)
                return await super().call(name, arguments)
        import time
        t0 = time.perf_counter()
        st, _, _ = run([decide(*[call("get_client_profile", client_id=K), call("get_client_goals", client_id=K), call("get_client_liabilities", client_id=K)]), finish()],
                       toolbox=Slow())
        self.assertLess(time.perf_counter() - t0, 0.8)  # three 0.3 s calls would take 0.9 s sequentially
        self.assertEqual(len(st.successes()), 3)

    def test_duplicate_prevention_across_iterations_and_in_batch(self):
        st, tr, _ = run([decide(call("get_client_profile", client_id=K), call("get_client_profile", client_id=K)),
                         decide(call("get_client_profile", client_id=K, ), call("calculate_asset_allocation", client_id=K, group_by="asset_class")),
                         decide(call("calculate_asset_allocation", client_id=K)),  # same call, default filled in
                         finish()])
        status = [(e.iteration, e.tool, e.status) for e in st.executions]
        self.assertEqual(status, [(1, "get_client_profile", "success"), (1, "get_client_profile", "duplicate"), (2, "get_client_profile", "duplicate"),
                                  (2, "calculate_asset_allocation", "success"), (3, "calculate_asset_allocation", "duplicate")])
        self.assertEqual(sum(e["event"] == "call_duplicate" for e in tr.events), 3)

    def test_failed_call_may_be_retried_but_success_may_not(self):
        st, _, _ = run([decide(call("get_client_portfolio", client_id=K, as_of_date="2026-07-31")),
                        decide(call("get_client_portfolio", client_id=K, as_of_date="2026-06-30")), finish()])
        self.assertEqual([e.status for e in st.executions], ["error", "success"])
        self.assertIn("not a snapshot date", st.executions[0].error)
        self.assertEqual(st.completion_status, "complete")

    def test_invalid_tool_argument_and_client_rejected_then_recovered(self):
        st, tr, llm = run([decide(call("run_sql", query="select 1"), call("calculate_asset_allocation", client_id=K, group_by="colour"),
                                  call("get_client_profile", client_id=R), call("get_client_profile", client_id=K, evil=1),
                                  call("calculate_portfolio_returns", client_id=K, start_date="2026-01-31")),
                           decide(call("calculate_asset_allocation", client_id=K, group_by="sector")), finish()])
        rej = [e for e in st.executions if e.iteration == 1]
        self.assertEqual([e.status for e in rej], ["rejected"] * 5)
        msgs = " | ".join(e.error for e in rej)
        for part in ("unknown tool 'run_sql'", "colour", "not in the plan", "unknown argument", "end_date"):
            self.assertIn(part, msgs)
        self.assertIn("rejected", llm.calls[1]["user"])  # the model sees why
        self.assertEqual([e.status for e in st.executions if e.iteration == 2], ["success"])
        self.assertEqual(sum(e["event"] == "call_rejected" for e in tr.events), 5)
        self.assertEqual(st.completion_status, "complete")

    def test_tool_error_recorded_and_loop_continues(self):
        st, _, _ = run([decide(call("calculate_goal_funding_gap", client_id=K, goal_type="Yacht"), call("get_client_profile", client_id=K)),
                        decide(call("calculate_goal_funding_gap", client_id=K, goal_type="Retirement")), finish()])
        self.assertEqual([(e.tool, e.status) for e in st.executions],
                         [("calculate_goal_funding_gap", "error"), ("get_client_profile", "success"), ("calculate_goal_funding_gap", "success")])
        self.assertIn("available types", st.executions[0].error)

    def test_timeout_recorded(self):
        class Hang(Toolbox):
            async def call(self, name, arguments):
                await asyncio.sleep(5)
        st, _, _ = run([decide(call("get_client_profile", client_id=K)), finish()], cfg=SelectorConfig(call_timeout_s=0.05), toolbox=Hang())
        self.assertEqual(st.executions[0].status, "timeout")
        self.assertEqual(st.completion_status, "failed")

    def test_max_iterations(self):
        names = ["get_client_profile", "get_client_goals", "get_client_liabilities", "get_portfolio_history"]
        st, _, _ = run([decide(call(n, client_id=K)) for n in names], cfg=SelectorConfig(max_iterations=3))
        self.assertEqual((st.iterations, st.stop_reason, st.completion_status, len(st.executions)), (3, "max_iterations", "partial", 3))

    def test_max_executions_budget(self):
        st, _, _ = run([decide(call("get_client_profile", client_id=K), call("get_client_goals", client_id=K), call("get_client_liabilities", client_id=K),
                               call("get_portfolio_history", client_id=K))], cfg=SelectorConfig(max_executions=3))
        self.assertEqual([e.status for e in st.executions], ["success", "success", "success", "rejected"])
        self.assertIn("budget", st.executions[-1].error)
        self.assertEqual(st.stop_reason, "max_executions")

    def test_calls_per_iteration_limit(self):
        # schema: more than 5 calls is an invalid model output and is retried with the error
        six = decide(*[call("get_client_profile", client_id=K)] * 6)
        st, _, llm = run([six, decide(call("get_client_profile", client_id=K)), finish()])
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(len(st.successes()), 1)
        # configured lower than 5: surplus calls are recorded as rejected
        st, _, _ = run([decide(call("get_client_profile", client_id=K), call("get_client_goals", client_id=K), call("get_client_liabilities", client_id=K)), finish()],
                       cfg=SelectorConfig(max_calls_per_iteration=2))
        self.assertEqual([e.status for e in st.executions], ["success", "success", "rejected"])

    def test_stop_after_two_idle_iterations(self):
        st, _, _ = run([decide(call("get_client_profile", client_id=K)), decide(call("get_client_profile", client_id=K)), decide(call("get_client_profile", client_id=K)),
                        decide(call("get_client_goals", client_id=K))])
        self.assertEqual((st.stop_reason, st.iterations, st.completion_status), ("no_new_valid_call", 3, "partial"))
        self.assertEqual(len(st.successes()), 1)

    def test_empty_call_tools_is_retried(self):
        st, _, llm = run([decide(), decide(call("get_client_profile", client_id=K)), finish()])
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(len(st.successes()), 1)

    def test_missing_information_reported(self):
        st, _, _ = run([decide(call("get_client_goals", client_id=K)), finish("no projection tool exists, so feasibility cannot be confirmed")])
        self.assertEqual(st.missing_information, ["no projection tool exists, so feasibility cannot be confirmed"])
        self.assertEqual(st.completion_status, "partial")

    def test_no_client_in_plan(self):
        st, _, llm = run([finish()], p=plan(clients=()))
        self.assertEqual((st.completion_status, st.stop_reason, len(llm.calls)), ("failed", "no_client_in_plan", 0))
        self.assertTrue(st.missing_information)


class Fallback(unittest.TestCase):
    TOOLS = ("get_client_profile", "calculate_portfolio_returns", "get_client_portfolio", "no_such_tool")

    def test_model_unavailable_on_first_iteration(self):
        st, tr, _ = run([LLMError("endpoint down")], p=plan(tools=self.TOOLS))
        self.assertTrue(st.fallback_used)
        # returns tool needs dates, so it is skipped; unknown tools are ignored
        self.assertEqual([(e.tool, e.status, e.iteration) for e in st.executions], [("get_client_profile", "success", 1), ("get_client_portfolio", "success", 1)])
        self.assertEqual((st.completion_status, st.stop_reason), ("partial", "model_unavailable_fallback"))
        self.assertTrue(st.errors)
        self.assertIn("fallback", [e["event"] for e in tr.events])

    def test_invalid_output_exhausts_retries_then_fallback(self):
        st, _, llm = run(["not json", "{}", "still not"], p=plan(tools=self.TOOLS))
        self.assertTrue(st.fallback_used)
        self.assertEqual(len(llm.calls), 3)  # 1 + 2 retries
        self.assertEqual(len(st.successes()), 2)

    def test_fallback_runs_per_plan_client(self):
        st, _, _ = run([LLMError("down")], p=plan(clients=(K, R), tools=("get_client_profile",)))
        self.assertEqual([e.arguments["client_id"] for e in st.executions], [K, R])

    def test_model_failure_later_is_not_fallback(self):
        st, _, _ = run([decide(call("get_client_profile", client_id=K)), LLMError("down")], p=plan(tools=("get_client_goals",)))
        self.assertFalse(st.fallback_used)
        self.assertEqual((st.completion_status, st.stop_reason, len(st.executions)), ("partial", "model_failed", 1))

    def test_fallback_without_suggested_tools_fails(self):
        st, _, _ = run([LLMError("down")], p=plan(tools=()))
        self.assertEqual((st.completion_status, st.fallback_used, st.executions), ("failed", True, []))

    def test_tool_discovery_failure(self):
        class Broken(Toolbox):
            async def list_tools(self):
                raise RuntimeError("server down")
        st, _, llm = run([finish()], toolbox=Broken())
        self.assertEqual((st.completion_status, st.stop_reason, len(llm.calls)), ("failed", "tool_discovery_failed", 0))
        self.assertIn("server down", st.errors[0])


class Trace(unittest.TestCase):
    def test_trace_events(self):
        st, tr, _ = run([decide(call("get_client_profile", client_id=K), call("bogus", x=1), call("get_client_profile", client_id=K)), finish("gap")])
        names = [e["event"] for e in tr.events]
        for n in ("tools_discovered", "decision", "call_rejected", "call_duplicate", "tool_result", "finished", "llm_call"):
            self.assertIn(n, names)
        res = next(e for e in tr.events if e["event"] == "tool_result")["payload"]
        self.assertEqual((res["id"], res["tool"], res["status"]), ("EX-REQ-test-01", "get_client_profile", "success"))
        self.assertIn("arguments", res)
        self.assertIn("digest", res)
        fin = tr.events[-1]["payload"]
        self.assertEqual((tr.events[-1]["event"], fin["status"], fin["stop_reason"], fin["iterations"]), ("finished", "partial", "model_finished", 2))
        self.assertEqual(len(fin["calls"]), 3)

    def test_trace_written_without_secrets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tr = Tracer("REQ-x", trace_dir=Path(d))
            llm = MockLLM(handlers={"tool_selector": [decide(call("get_client_profile", client_id=K)), finish()]})
            ToolSelector(llm, Toolbox(), tracer=tr).run_sync("q", plan(), {})
            text = (Path(d) / "REQ-x.jsonl").read_text()
            self.assertIn("tool_result", text)
            self.assertNotIn("CMF_DATABASE_URL", text)


class Prompt(unittest.TestCase):
    def test_prompt_is_compact_and_contains_catalogue_and_memory(self):
        mem = MemoryContext(client_id=K, current=[MemoryItem(memory_id="M1", client_id=K, memory_type="semantic", topic="retirement_goal", content="retire at 63",
                                                             value_text="63")]).summary()
        st, _, llm = run([decide(call("get_client_portfolio", client_id=K), call("get_client_goals", client_id=K), call("get_client_liabilities", client_id=K)), finish()],
                         memory=mem)
        p1, p2 = llm.calls[0]["user"], llm.calls[1]["user"]
        self.assertIn("calculate_asset_allocation(client_id*: str", p1)
        self.assertIn("group_by: asset_class|liquidity_category|sector|account_type=asset_class", p1)
        self.assertIn('"retirement_goal": "63"', p1)
        self.assertLess(len(p1), 6000)
        self.assertLess(len(p2), 9000)  # three results digested, not dumped
        self.assertNotIn("row_provenance", p2)


class Scenarios(unittest.TestCase):
    """Spec section E with a scripted model: which tools, in which iteration. Mocks cannot show the real model would choose this."""

    def test_kenji_retirement_target_memory_only(self):
        mem = {"client_id": K, "current": {"retirement_goal": "63"}, "unresolved": 0}
        st, _, _ = run([finish()], memory=mem, text="What is Kenji's current retirement target, and how did it evolve?")
        self.assertEqual(st.executions, [])
        self.assertEqual((st.completion_status, st.stop_reason), ("not_needed", "model_finished"))
        self.assertFalse([e for e in st.executions if "portfolio" in e.tool])

    def test_kenji_feasibility_two_iterations_then_finish(self):
        gap = "no projection tool exists, so retirement at 63 cannot be confirmed; only a simple funding gap is available"
        st, _, llm = run([
            decide(call("get_client_profile", client_id=K), call("get_client_goals", client_id=K), call("get_client_portfolio", client_id=K),
                   call("get_client_liabilities", client_id=K)),
            decide(call("calculate_goal_funding_gap", client_id=K, goal_type="Retirement"), call("calculate_asset_allocation", client_id=K),
                   call("calculate_liquidity_ratio", client_id=K)),
            finish(gap)], p=plan(task_type="forecast_feasibility"), text="Can Kenji retire at 63 based on the available financial data?")
        it = tools_by_iter(st)
        self.assertEqual({t for t, _ in it[1]}, {"get_client_profile", "get_client_goals", "get_client_portfolio", "get_client_liabilities"})
        self.assertEqual({t for t, _ in it[2]}, {"calculate_goal_funding_gap", "calculate_asset_allocation", "calculate_liquidity_ratio"})
        self.assertTrue(all(s == "success" for v in it.values() for _, s in v))
        self.assertEqual(st.iterations, 3)
        self.assertEqual(st.missing_information, [gap])
        self.assertEqual(st.completion_status, "partial")
        g = next(e for e in st.executions if e.tool == "calculate_goal_funding_gap").result
        self.assertIn("NOT a projection", " ".join(g.limitations))
        # iteration 2 prompt shows iteration 1 results
        self.assertIn("get_client_goals", llm.calls[1]["user"])

    def test_rebecca_concentration_same_iteration(self):
        st, _, _ = run([decide(call("calculate_portfolio_concentration", client_id=R, dimension="issuer"),
                               call("calculate_portfolio_concentration", client_id=R, dimension="sector"),
                               call("calculate_portfolio_concentration", client_id=R, dimension="instrument"),
                               call("get_client_portfolio", client_id=R)),
                        finish("the company stake is outside the investable portfolio; funds are not looked through")],
                       p=plan(clients=(R,)), text="How concentrated is Rebecca's portfolio?")
        it = tools_by_iter(st)
        self.assertEqual(list(it), [1])
        self.assertEqual([t for t, _ in it[1]].count("calculate_portfolio_concentration"), 3)
        dims = {e.arguments["dimension"] for e in st.executions if e.tool == "calculate_portfolio_concentration"}
        self.assertEqual(dims, {"issuer", "sector", "instrument"})
        self.assertTrue(all(e.arguments["client_id"] == R for e in st.executions))


if __name__ == "__main__":
    unittest.main()
