"""get_source_evidence argument construction, repeat handling and budget (Tool Selector).

The tool has four MUTUALLY EXCLUSIVE modes (search / several turns of one meeting / one turn / entity evidence).  A model call that mixes them used to pass validation, get EXECUTED, fail in the
service and spend one of the 12 execution slots; three such calls plus repeats starved the useful retrievals ("execution budget exhausted").  These tests pin the corrected behaviour:
a call that violates the contract is refused BEFORE it runs and costs no budget; the same call written differently is a repeat; a turn already read is not read again; the budget is exact.
All tests use scripted models and in-process tools over the isolated test databases.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from agents.tool_selector.agent import SelectorConfig, ToolSelector                              # noqa: E402
from agents.tool_selector.digest import digest                                                    # noqa: E402
from agents.tool_selector.source_discovery import meetings_from                                   # noqa: E402
from agents.tool_selector.toolbox import Toolbox                                                  # noqa: E402
from agents.tool_selector.validator import CallValidator, call_key, source_mode_problem          # noqa: E402
from schemas.tools import PlannedCall                                                             # noqa: E402
from test_tool_selector import R, _EvidenceToolbox, call, decide, finish, plan, run              # noqa: E402

TOOL = "get_source_evidence"
SEARCH = dict(client_id=R, search_text="retirement")
TURN = dict(client_id=R, interaction_id="INT-1", turn_index=2)
TURNS = dict(client_id=R, interaction_id="INT-1", turn_indices=[1, 2, 3])
ENTITY = dict(client_id=R, entity_type="goal", entity_id="GOAL-1")
P = plan(clients=(R,))


def go(script, cfg=None, tb=None):
    tb = tb or _EvidenceToolbox()
    st, tr, llm = run(script, p=P, toolbox=tb, cfg=cfg)
    return st, tb, llm


def statuses(st):
    return [e.status for e in st.executions]


class Modes(unittest.TestCase):
    def test_every_valid_mode_is_accepted(self):
        for args in (SEARCH, TURN, TURNS, ENTITY):
            self.assertIsNone(source_mode_problem(args), args)
        st, tb, _ = go([decide(call(TOOL, **SEARCH), call(TOOL, **TURN), call(TOOL, **TURNS), call(TOOL, **ENTITY)), finish()])
        self.assertEqual(statuses(st), ["success"] * 4)
        self.assertEqual(len(tb.seen), 4)

    def test_search_text_combined_with_any_structured_selector_is_refused_before_it_runs(self):
        combos = [dict(SEARCH, interaction_id="INT-1"), dict(SEARCH, turn_index=2), dict(SEARCH, turn_indices=[1]), dict(SEARCH, entity_type="goal"), dict(SEARCH, entity_id="GOAL-1"),
                  dict(SEARCH, interaction_id="INT-1", turn_index=2)]
        for bad in combos:
            msg = source_mode_problem(bad)
            self.assertIsNotNone(msg, bad)
            self.assertIn("mutually exclusive", msg)
        st, tb, _ = go([decide(*[call(TOOL, **b) for b in combos[:5]]), finish()])
        self.assertEqual(statuses(st), ["rejected"] * 5)
        self.assertEqual(tb.seen, [])                                          # nothing reached the tool
        self.assertIn("search_text alone", st.executions[0].error)             # the model is told the correct shape

    def test_other_incompatible_or_incomplete_combinations_are_refused(self):
        bad = [dict(client_id=R),                                              # no selector at all
               dict(client_id=R, interaction_id="INT-1"),                      # a meeting without a turn
               dict(client_id=R, turn_index=2),                                # a turn without a meeting
               dict(TURN, turn_indices=[1]),                                   # turn_index and turn_indices together
               dict(TURN, entity_type="goal", entity_id="GOAL-1"),             # conversation mode mixed with entity mode
               dict(client_id=R, entity_type="goal"),                          # entity mode without entity_id
               dict(client_id=R, turn_indices=[1, 2])]                         # turn_indices without interaction_id
        for args in bad:
            self.assertIsNotNone(source_mode_problem(args), args)
        st, tb, _ = go([decide(*[call(TOOL, **b) for b in bad[:5]]), finish()])
        self.assertEqual(statuses(st), ["rejected"] * 5)
        self.assertEqual(tb.seen, [])

    def test_empty_values_do_not_count_as_selectors(self):
        self.assertIsNone(source_mode_problem(dict(client_id=R, search_text="retirement", interaction_id="", turn_index=None, turn_indices=[], entity_type="")))

    def test_invalid_calls_cost_no_execution_budget(self):
        cfg = SelectorConfig(max_executions=3, max_iterations=6, max_idle_iterations=10)
        mixed = [dict(SEARCH, interaction_id=f"INT-{i}", turn_index=i) for i in range(1, 4)]
        st, tb, _ = go([decide(*[call(TOOL, **m) for m in mixed]),                         # three contract violations ...
                        decide(call(TOOL, **SEARCH), call(TOOL, **TURN), call(TOOL, **ENTITY)),   # ... leave all three executions for the useful calls
                        finish()], cfg=cfg)
        self.assertEqual(statuses(st), ["rejected"] * 3 + ["success"] * 3)
        self.assertEqual(len(tb.seen), 3)

    def test_the_old_behaviour_would_have_starved_the_useful_calls(self):
        """Documents WHY: a mixed call that REACHED the tool was counted as an execution.  Here the same three useful calls fit only because the invalid ones never ran."""
        cfg = SelectorConfig(max_executions=3, max_iterations=6, max_idle_iterations=10)
        st, tb, _ = go([decide(call(TOOL, **dict(SEARCH, turn_index=1)), call(TOOL, **dict(SEARCH, turn_index=2))), decide(call(TOOL, **SEARCH), call(TOOL, **TURN), call(TOOL, **TURNS)), finish()], cfg=cfg)
        self.assertEqual(sum(1 for e in st.executions if e.status == "success"), 3)
        self.assertFalse(any("budget" in (e.error or "") for e in st.executions))

    def test_a_corrected_call_after_a_refusal_runs(self):
        st, tb, _ = go([decide(call(TOOL, **dict(SEARCH, interaction_id="INT-1"))), decide(call(TOOL, **SEARCH)), finish()])
        self.assertEqual(statuses(st), ["rejected", "success"])


class Fallback(unittest.TestCase):
    def test_the_fallback_never_builds_a_selector_free_source_call(self):
        from llm.client import LLMError
        st, tr, _ = run([LLMError("endpoint down")], p=plan(clients=(R,), tools=(TOOL, "get_client_profile")))
        self.assertTrue(st.fallback_used)
        self.assertEqual([(e.tool, e.status) for e in st.executions], [("get_client_profile", "success")])      # no get_source_evidence({client_id}) call at all
        self.assertFalse(any(e.status == "rejected" for e in st.executions))                                  # not even a refused one


class Repeats(unittest.TestCase):
    def test_the_same_call_written_differently_is_a_repeat(self):
        a, b = dict(TURNS, turn_indices=[3, 1, 2]), dict(TURNS, turn_indices=[1, 2, 3, 3])
        self.assertEqual(call_key(TOOL, a), call_key(TOOL, b))
        self.assertEqual(call_key(TOOL, dict(SEARCH, search_text="Retirement  Age")), call_key(TOOL, dict(SEARCH, search_text="retirement age")))
        st, tb, _ = go([decide(call(TOOL, **a)), decide(call(TOOL, **b), call(TOOL, **dict(SEARCH, search_text="X Y"))), decide(call(TOOL, **dict(SEARCH, search_text="x  y"))), finish()])
        self.assertEqual(statuses(st), ["success", "duplicate", "success", "duplicate"])
        self.assertEqual(len(tb.seen), 2)

    def test_a_turn_already_retrieved_is_not_retrieved_again(self):
        st, tb, _ = go([decide(call(TOOL, **TURNS)), decide(call(TOOL, **TURN), call(TOOL, **dict(TURNS, turn_indices=[3, 1]))), finish()])
        self.assertEqual(statuses(st), ["success", "duplicate", "duplicate"])
        self.assertIn("already retrieved", st.executions[1].error)
        self.assertEqual(len(tb.seen), 1)

    def test_a_new_turn_of_the_same_meeting_is_still_retrieved(self):
        st, tb, _ = go([decide(call(TOOL, **TURNS)), decide(call(TOOL, **dict(TURN, turn_index=9)), call(TOOL, **dict(TURNS, turn_indices=[3, 4]))), finish()])
        self.assertEqual(statuses(st), ["success", "success", "success"])

    def test_duplicates_and_refusals_do_not_consume_budget(self):
        cfg = SelectorConfig(max_executions=2, max_iterations=6, max_idle_iterations=10)
        st, tb, _ = go([decide(call(TOOL, **TURN)),
                        decide(call(TOOL, **TURN), call(TOOL, **dict(SEARCH, turn_index=1))),                 # a duplicate and a contract violation
                        decide(call(TOOL, **ENTITY)),                                                         # the second (last) execution
                        finish()], cfg=cfg)
        self.assertEqual(statuses(st), ["success", "duplicate", "rejected", "success"])


class Budget(unittest.TestCase):
    def test_exhaustion_is_exact_and_explained(self):
        cfg = SelectorConfig(max_executions=2, max_iterations=6, max_idle_iterations=10)
        third = dict(client_id=R, interaction_id="INT-9", turn_index=1)
        st, tb, _ = go([decide(call(TOOL, **SEARCH), call(TOOL, **TURN), call(TOOL, **third)), finish()], cfg=cfg)
        self.assertEqual(statuses(st), ["success", "success", "rejected"])
        self.assertIn("execution budget exhausted (2 of 2", st.executions[2].error)
        self.assertEqual(len(tb.seen), 2)
        self.assertEqual(st.stop_reason, "max_executions")

    def test_repeating_a_budget_rejected_call_in_a_later_turn_is_still_deterministic(self):
        cfg = SelectorConfig(max_executions=1, max_iterations=4, max_idle_iterations=10)
        st, tb, _ = go([decide(call(TOOL, **SEARCH), call(TOOL, **TURN)), finish()], cfg=cfg)
        self.assertEqual(statuses(st), ["success", "rejected"])
        self.assertIn("1 of 1", st.executions[1].error)


class SearchToRetrieval(unittest.TestCase):
    """A search returns turn references; the next call must read them with the right interaction_id and turn_index.  Real tools, real (isolated) data."""

    def real(self, phrase="retirement"):
        from services.conversations import shared
        conv = shared()
        return conv, [r for r in conv.rows if r["client_id"] == "CLIENT-001" and phrase in r["utterance"].lower()]

    def test_the_search_digest_lists_exact_copyable_references(self):
        conv, rows = self.real()
        self.assertTrue(rows)

        async def search():
            async with Toolbox() as tb:
                return await tb.call(TOOL, {"client_id": "CLIENT-001", "search_text": "retirement"})
        env = asyncio.run(search())
        text = digest(env)
        refs = [m for m in env["data"]["search"]["matches"][:8]]
        for m in refs[:5]:
            self.assertIn(f"{m['interaction_id']}#{m['turn_index']}", text)               # exact ids, not a truncated object dump
        self.assertIn("turn_refs", text)
        self.assertLessEqual(len(text), 700)

    def test_retrieval_by_listed_references_returns_the_cited_turns_with_provenance(self):
        conv, _ = self.real()

        async def flow():
            async with Toolbox() as tb:
                found = await tb.call(TOOL, {"client_id": "CLIENT-001", "search_text": "retirement"})
                m = found["data"]["search"]["matches"][0]
                same = [x["turn_index"] for x in found["data"]["search"]["matches"] if x["interaction_id"] == m["interaction_id"]][:12]
                got = await tb.call(TOOL, {"client_id": "CLIENT-001", "interaction_id": m["interaction_id"], "turn_indices": same})
                return m, same, got
        m, same, got = asyncio.run(flow())
        turns = got["data"]["turns"]
        self.assertEqual([t["turn_index"] for t in turns], sorted(same))
        for t in turns:
            src = conv.turn(m["interaction_id"], t["turn_index"])
            self.assertEqual((t["text"], t["speaker"]), (src["utterance"], src["speaker"]))      # exact text and recorded speaker: provenance intact
            self.assertEqual(src["client_id"], "CLIENT-001")

    def test_code_driven_discovery_builds_retrieval_calls_from_search_results_only(self):
        from agents.tool_selector.source_discovery import meeting_calls, plan_meetings
        from schemas.tools import Envelope, ToolExecution
        env = Envelope.model_validate(dict(tool=TOOL, client_id="CLIENT-001", as_of=None, data={"search": {"query": "x", "total": 3, "truncated": False, "matches": [
            {"interaction_id": "INT-007", "turn_index": 4, "speaker": "Client", "date": "2026-02-01", "cue": None},
            {"interaction_id": "INT-007", "turn_index": 6, "speaker": "Client", "date": "2026-02-01", "cue": "correction"},
            {"interaction_id": "INT-012", "turn_index": 2, "speaker": "Client", "date": "2026-03-01", "cue": None}]}}, provenance=dict(source="conversations.csv", evidence_refs=[]), limitations=[]))
        meetings, _ = meetings_from([ToolExecution(id="EX", iteration=0, tool=TOOL, arguments={}, status="success", result=env)])
        chosen, omitted = plan_meetings(meetings, 5)
        calls, cut = meeting_calls("CLIENT-001", meetings, chosen)
        self.assertEqual([(c.arguments["interaction_id"], c.arguments["turn_indices"]) for c in calls], [("INT-007", [4, 6]), ("INT-012", [2])])
        for c in calls:
            self.assertIsNone(source_mode_problem(c.arguments))                           # the code-driven calls obey the same contract
        self.assertEqual((omitted, cut), ([], []))


class ValidatorUnit(unittest.TestCase):
    CAT = [{"name": TOOL, "description": "", "input_schema": {"type": "object", "properties": {k: {} for k in ("client_id", "entity_type", "entity_id", "interaction_id", "turn_index", "turn_indices", "search_text")},
                                                              "required": ["client_id"]}}]

    def test_verdicts(self):
        v = CallValidator(self.CAT, [R])
        self.assertEqual(v.validate(PlannedCall(tool=TOOL, arguments=dict(SEARCH, turn_index=1)), set())[0], "rejected")
        self.assertEqual(v.validate(PlannedCall(tool=TOOL, arguments=SEARCH), set())[0], "ok")
        v.record_success(TOOL, TURNS)
        self.assertEqual(v.validate(PlannedCall(tool=TOOL, arguments=TURN), set())[0], "duplicate")
        self.assertEqual(v.validate(PlannedCall(tool=TOOL, arguments=dict(TURN, turn_index=7)), set())[0], "ok")

    def test_other_tools_are_untouched(self):
        cat = self.CAT + [{"name": "get_client_profile", "description": "", "input_schema": {"type": "object", "properties": {"client_id": {}}, "required": ["client_id"]}}]
        v = CallValidator(cat, [R])
        self.assertEqual(v.validate(PlannedCall(tool="get_client_profile", arguments={"client_id": R}), set())[0], "ok")


if __name__ == "__main__":
    unittest.main()
