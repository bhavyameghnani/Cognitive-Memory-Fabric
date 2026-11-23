"""A withheld (pending-approval) answer must still say WHAT RAN - without leaking anything derived from the answer.  Approval stays required; this only removes the black box."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE.parent))

from test_p0_security import assert_no_content, ask_withheld, world  # noqa: E402,F401  (world is a fixture)

from api.agent_run import execution_status  # noqa: E402
from harness.runtime import withheld_view  # noqa: E402

ALLOWED_KEYS = {"agents", "client_identified", "harness", "note"}


def test_a_withheld_response_reports_which_agents_ran_and_the_harness_state(world):
    build, m, db = world
    body = ask_withheld(build("production"), db)
    assert body["released"] is False and body["status"] == "pending_approval"
    assert body["agent_run"] is None and body["harness"] is None and body["retrieved_memory_ids"] == []        # the existing withholding contract is unchanged
    ex = body["execution"]
    assert set(ex) == ALLOWED_KEYS
    assert [a["agent"] for a in ex["agents"]] == ["Planner", "Memory", "Tool Selector", "Report"]
    assert all(isinstance(a["ran"], bool) for a in ex["agents"])
    assert ex["harness"]["active"] is True and ex["harness"]["approval"]["category"] == "suitability_conclusion"
    assert ex["harness"]["permissions"]["allowed"] >= 1
    assert_no_content(body, db)                                                                                      # nothing derived from stored memories or withheld text
    blob = json.dumps({k: v for k, v in ex.items() if k != "note"})          # the fixed explanatory sentence is not data
    for forbidden in ("MEM-", "evidence", "excerpt", "get_client", "get_source", "missing_information", "retirement_age"):
        assert forbidden not in blob, forbidden


def test_execution_status_is_codes_and_numbers_only():
    class R:
        timings_ms = {"planner": 12, "memory": 3, "tool_selector": 40, "report": 5, "total": 60}
        plan = type("P", (), {"client_ids": ["CLIENT-001"]})()
        tools = type("T", (), {"completion_status": "partial", "stop_reason": "no_new_valid_call", "missing_information": ["SECRET WORDS"], "executions": [object()]})()
        harness = {"profile": "production", "outcome": "completed", "budget": {"llm_calls_by_agent": {"planner": 1, "report": 1}}, "degraded_reasons": [],
                   "permission_decisions": [{"allowed": True, "operation": "ask", "code": "ok"}, {"allowed": False, "operation": "run_tool", "code": "tool_not_allowed"}],
                   "approval": {"category": "suitability_conclusion", "status": "pending", "reason": "SECRET WORDS"}}
    ex = execution_status(R())
    assert [(a["agent"], a["ran"], a["llm_calls"]) for a in ex["agents"]] == [("Planner", True, 1), ("Memory", True, 0), ("Tool Selector", True, 0), ("Report", True, 1)]
    assert ex["agents"][2]["status"] == "partial" and ex["agents"][2]["stop_reason"] == "no_new_valid_call"
    assert ex["client_identified"] is True and ex["harness"]["permissions"] == {"allowed": 1, "denied": 1, "denied_codes": ["tool_not_allowed"]}
    assert "SECRET" not in json.dumps(ex)


def test_an_agent_that_did_not_run_is_reported_as_not_run():
    class R:
        timings_ms = {"planner": 9}
        plan = type("P", (), {"client_ids": []})()
        tools = None
        harness = {}
    ex = execution_status(R())
    assert [(a["agent"], a["ran"]) for a in ex["agents"]] == [("Planner", True), ("Memory", False), ("Tool Selector", False), ("Report", False)]
    assert ex["client_identified"] is False and ex["harness"]["active"] is False


def test_the_view_still_withholds_everything_else_and_execution_is_optional():
    v = withheld_view({"status": "pending", "approval_id": "APR-x", "category": "suitability_conclusion"}, "REQ-t")
    assert v["execution"] is None and v["released"] is False and v["agent_run"] is None and v["harness"] is None and v["retrieved_memory_ids"] == []
