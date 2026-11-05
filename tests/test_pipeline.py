"""End-to-end pipeline with a scripted model: Planner -> Memory -> Tool Selector (real in-process MCP) -> Report.
Mocks prove wiring and validation, not the reliability of a real Qwen model."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.memory.agent import MemoryOrchestrator  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from orchestration.pipeline import run_pipeline  # noqa: E402
from services.conversations import shared  # noqa: E402

CONV = shared()
R = "CLIENT-002"


def planner_out(calc=True):
    subs = [{"id": "T1", "description": "retrieve client memory", "agent": "memory_orchestrator"}]
    if calc:
        subs.append({"id": "T2", "description": "collect and calculate data", "agent": "tool_selector", "depends_on": ["T1"]})
    subs.append({"id": "T3" if calc else "T2", "description": "write the report", "agent": "report_agent", "depends_on": [s["id"] for s in subs]})
    return dict(user_intent="how concentrated is the portfolio", task_type="calculation", time_period=None, needs_history=False, needs_calculation=calc,
                required_memory_categories=["semantic"], memory_topics=["concentrated_position"], required_data=["portfolio"],
                required_tools=["calculate_portfolio_concentration"], subtasks=subs, missing_information=[], success_criteria=["answer with cited evidence"])


def memory():
    m = MemoryOrchestrator(MockLLM(), store=MemoryStore(url="sqlite:///:memory:", conv=CONV), conv=CONV)
    m.init()
    return m


class PipelineTest(unittest.TestCase):
    def test_rebecca_concentration_uses_tools_and_cites_them(self):
        calls = [{"action": "call_tools", "reasoning": "r", "missing_information": [], "calls": [
            {"tool": "calculate_portfolio_concentration", "arguments": {"client_id": R, "dimension": d}, "rationale": "concentration"} for d in ("issuer", "sector", "instrument")]},
            {"action": "finish", "calls": [], "missing_information": [], "reasoning": "done"}]
        report_out = {"summary": "Concentration was calculated by the tools.", "key_findings": [], "historical_context": [],
                      "financial_analysis": [{"text": "Top holdings concentration was calculated.", "refs": ["E1"]}], "uncertainties": [], "suggested_followups": [], "claims": []}
        llm = MockLLM(handlers={"planner": [planner_out()], "tool_selector": calls, "report": [report_out]})
        res = run_pipeline("How concentrated is Rebecca's portfolio?", llm, memory(), persist_trace=False)
        self.assertEqual(res.plan.client_ids, [R])
        ok = [e for e in res.tools.executions if e.status == "success"]
        self.assertEqual(sorted(e.arguments["dimension"] for e in ok), ["instrument", "issuer", "sector"])
        self.assertTrue(res.report.evidence)
        self.assertTrue(any(e.source_class == "tool_calculation" and e.synthetic for e in res.report.evidence))
        self.assertTrue(res.report.limitations)

    def test_no_evidence_means_no_report_model_call(self):
        llm = MockLLM(handlers={"planner": [planner_out(calc=False)]})
        res = run_pipeline("What did Kenji decide?", llm, memory(), persist_trace=False)
        self.assertEqual(res.tools.completion_status, "not_needed")
        self.assertFalse([c for c in llm.calls if c["agent"] == "report"])
        self.assertIn("reliabl", res.report.summary.lower())


if __name__ == "__main__":
    unittest.main()


class AgentRunSummaryTest(unittest.TestCase):
    """api/agent_run.py over a real PipelineResult (what the UI's performance panel shows)."""

    def test_summary_from_real_run_with_model_failure(self):
        from api.agent_run import build_agent_run
        res = run_pipeline("How concentrated is Rebecca's portfolio?", MockLLM(), memory(), persist_trace=False)  # unscripted mock -> fallbacks
        run = build_agent_run(res)
        self.assertEqual(set(run["timings_ms"]), {"planner", "memory", "tool_selector", "report", "total"})
        self.assertEqual(run["plan"]["subtasks"][0]["agent"], res.plan.subtasks[0].agent)
        self.assertEqual(run["memory"]["total"], len(run["memory"]["items"]))
        self.assertEqual(len(run["tools"]["executions"]), len(res.tools.executions))
        self.assertIsInstance(run["degraded"], list)
