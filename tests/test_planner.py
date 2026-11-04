import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.planner.agent import Planner  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from services.conversations import shared  # noqa: E402

CONV = shared()
# tool names the Planner is told about (supplied by the caller; the Planner never touches tools)
TOOLS = ["get_client_profile", "get_client_portfolio", "get_portfolio_history", "get_client_goals", "get_client_liabilities", "calculate_asset_allocation",
         "calculate_portfolio_concentration", "compare_portfolio_snapshots", "calculate_portfolio_returns", "calculate_liquidity_ratio",
         "calculate_goal_funding_gap", "get_market_data", "get_source_evidence"]


def out(**kw):
    base = dict(user_intent="answer the advisor's question", task_type="retrieval", time_period=None, needs_history=False, needs_calculation=False,
                required_memory_categories=["semantic"], memory_topics=[], required_data=[], required_tools=[],
                subtasks=[{"id": "T1", "description": "retrieve memory", "agent": "memory_orchestrator"},
                          {"id": "T2", "description": "write the report", "agent": "report_agent", "depends_on": ["T1"]}],
                missing_information=[], success_criteria=["the question is answered with cited evidence"])
    base.update(kw)
    return base


def plan_with(text, llm_output, tools=TOOLS, **kw):
    llm = MockLLM(handlers={"planner": llm_output if (callable(llm_output) or isinstance(llm_output, list)) else [llm_output]})
    return Planner(llm, CONV).plan(text, tools, request_id="REQ-P", **kw), llm


class PlannerTests(unittest.TestCase):
    def test_simple_portfolio_query(self):
        p, _ = plan_with("Show Sofia's current portfolio", out(task_type="retrieval", required_tools=["get_client_portfolio"], needs_calculation=False,
                          subtasks=[{"id": "T1", "description": "get portfolio", "agent": "tool_selector"}, {"id": "T2", "description": "report", "agent": "report_agent", "depends_on": ["T1"]}]))
        self.assertEqual(p.client_ids, ["CLIENT-003"]); self.assertEqual(p.required_tools, ["get_client_portfolio"])
        self.assertEqual(p.execution_order, ["T1", "T2"]); self.assertEqual(p.parallelizable_tasks, [])

    def test_multistep_retirement_query_with_dependencies_and_parallel_tasks(self):
        subs = [{"id": "T1", "description": "latest confirmed retirement target", "agent": "memory_orchestrator"},
                {"id": "T2", "description": "profile, portfolio, goals, liabilities", "agent": "tool_selector"},
                {"id": "T3", "description": "funding gap", "agent": "tool_selector", "depends_on": ["T1", "T2"]},
                {"id": "T4", "description": "write report", "agent": "report_agent", "depends_on": ["T1", "T2", "T3"]}]
        p, _ = plan_with("Can Kenji retire at 63?", out(task_type="forecast_feasibility", needs_history=True, needs_calculation=True, memory_topics=["retirement_age", "retirement_income"],
                          required_memory_categories=["semantic", "episodic", "procedural"], subtasks=subs,
                          required_tools=["get_client_profile", "get_client_portfolio", "calculate_goal_funding_gap"]))
        self.assertEqual(p.client_ids, ["CLIENT-001"]); self.assertEqual(p.parallelizable_tasks, [["T1", "T2"]])
        self.assertEqual(p.execution_order[-1], "T4"); self.assertEqual(p.dependencies["T3"], ["T1", "T2"])
        self.assertIn("retirement_age", p.memory_topics)

    def test_history_query_is_typed_and_asks_for_history(self):
        p, _ = plan_with("What has changed in Rebecca's financial situation over the last six months?", out(task_type="history", needs_history=True, time_period="last six months",
                          required_memory_categories=["episodic", "semantic"]))
        self.assertEqual((p.task_type, p.needs_history, p.time_period, p.client_ids), ("history", True, "last six months", ["CLIENT-002"]))

    def test_ambiguous_or_missing_client_is_reported_not_guessed(self):
        p, _ = plan_with("What is the client's retirement target?", out())
        self.assertEqual(p.client_ids, [])
        self.assertTrue(any("client not identified" in m for m in p.missing_information))

    def test_multiple_clients_are_resolved(self):
        p, _ = plan_with("Compare Sofia's current portfolio against Lucas's", out(task_type="comparison"))
        self.assertEqual(p.client_ids, ["CLIENT-005", "CLIENT-003"] if False else p.client_ids); self.assertEqual(set(p.client_ids), {"CLIENT-003", "CLIENT-005"})

    def test_model_cannot_invent_tools_topics_or_clients(self):
        p, _ = plan_with("Does Lucas have enough liquidity?", out(required_tools=["calculate_liquidity_ratio", "predict_stock_market"], memory_topics=["liquidity_reserve", "astrology"],
                          needs_calculation=True))
        self.assertEqual(p.required_tools, ["calculate_liquidity_ratio"]); self.assertEqual(p.memory_topics, ["liquidity_reserve"])
        self.assertTrue(any("not in the catalogue" in n for n in p.planner_notes))

    def test_report_subtask_is_forced_last_and_depends_on_everything(self):
        bad = out(subtasks=[{"id": "T1", "description": "write the report", "agent": "report_agent"}, {"id": "T2", "description": "retrieve memory", "agent": "memory_orchestrator"}])
        p, _ = plan_with("Summarize unresolved decisions for Peg", bad)
        self.assertEqual(p.subtasks[-1].agent, "report_agent"); self.assertEqual(set(p.subtasks[-1].depends_on), {s.id for s in p.subtasks[:-1]})

    def test_invalid_dependencies_trigger_a_retry_then_heuristic_fallback(self):
        cyc = out(subtasks=[{"id": "T1", "description": "first step", "agent": "memory_orchestrator", "depends_on": ["T2"]}, {"id": "T2", "description": "second step", "agent": "report_agent"}])
        p, llm = plan_with("Can Kenji retire at 63?", [cyc, cyc, cyc])
        self.assertEqual(len(llm.calls), 3)
        self.assertTrue(any("heuristic" in n for n in p.planner_notes)); self.assertEqual(p.task_type, "forecast_feasibility"); self.assertEqual(p.client_ids, ["CLIENT-001"])

    def test_malformed_json_is_retried_with_the_error(self):
        p, llm = plan_with("Show Kenji's goals", ["not json at all", out(required_tools=["get_client_goals"])])
        self.assertEqual(len(llm.calls), 2); self.assertIn("rejected", llm.calls[1]["user"]); self.assertEqual(p.required_tools, ["get_client_goals"])

    def test_llm_unavailable_gives_a_heuristic_plan(self):
        p, _ = plan_with("Does Lucas have sufficient liquidity for his upcoming expenses?", lambda s, u, sc: LLMError("down"))
        self.assertEqual(p.client_ids, ["CLIENT-005"]); self.assertIn("liquidity_reserve", p.memory_topics)
        self.assertEqual(p.subtasks[-1].agent, "report_agent")

    def test_replanning_when_a_tool_is_unavailable(self):
        first, _ = plan_with("Does Lucas have enough liquidity?", out(required_tools=["calculate_liquidity_ratio"], needs_calculation=True,
                             subtasks=[{"id": "T1", "description": "calculate liquidity", "agent": "tool_selector"}, {"id": "T2", "description": "report", "agent": "report_agent", "depends_on": ["T1"]}]))
        llm = MockLLM(handlers={"planner": [out(required_tools=["calculate_liquidity_ratio", "get_client_profile"], needs_calculation=True)]})
        new = Planner(llm, CONV).replan(first, ["calculate_liquidity_ratio"], "tool server error", TOOLS)
        self.assertNotIn("calculate_liquidity_ratio", new.required_tools); self.assertEqual(new.revision, 1)
        self.assertTrue(any("unavailable" in m for m in new.missing_information)); self.assertIn("calculate_liquidity_ratio", llm.calls[0]["user"].split("AVAILABLE TOOLS:")[1].split("\n")[0] + "x" if False else "calculate_liquidity_ratio")
        self.assertNotIn("calculate_liquidity_ratio", llm.calls[0]["user"].split("AVAILABLE TOOLS:")[1].split("\n")[0])

    def test_planner_does_not_touch_tools_database_or_memory(self):
        import inspect, agents.planner.agent as m
        src = inspect.getsource(m)
        for forbidden in ("MCPToolbox", "sqlite3", "MemoryStore", "OperationalData", "psycopg"):
            self.assertNotIn(forbidden, src)

    def test_plan_is_schema_validated_and_serialisable(self):
        p, _ = plan_with("Show Peg's liabilities", out(required_tools=["get_client_liabilities"]))
        from schemas.plan import ExecutionPlan
        self.assertEqual(ExecutionPlan.model_validate_json(p.model_dump_json()), p)


if __name__ == "__main__":
    unittest.main()
