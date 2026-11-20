"""Regression tests for the first live end-to-end run (trace REQ-a7c3eee5): a chronology request for the Lakeshore letter of intent.

Observed failures, one test class each: (1) the Planner chose unrelated portfolio topics / a follow-up review, (2) the Tool Selector wasted iterations on repeated and malformed calls,
(3) no real conversation turn was retrieved because the model guessed entity ids, (4) the Report Agent's draft left nothing valid and the trace could not say why, (5) the approval gate
fired on the words 'recommendations' and 'suitability' inside a PROHIBITION, plus the UI's '0 memories retrieved' for a withheld answer.

Everything is synthetic and in memory: a tiny controlled conversations file, scripted models, the in-process read-only tools.  No live API, no ingestion, no database is written.
"""
import asyncio
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from agents.planner.agent import Planner, is_source_chronology  # noqa: E402
from agents.planner.topics import infer_topics  # noqa: E402
from agents.report.agent import ReportAgent  # noqa: E402
from agents.report.evidence import build_evidence  # noqa: E402
from agents.tool_selector.agent import SelectorConfig  # noqa: E402
from agents.tool_selector.toolbox import ToolCallError, Toolbox  # noqa: E402
from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from harness.approval import ApprovalPolicy, RiskCategory, asks_for_advice  # noqa: E402
from harness.runtime import withheld_view  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from orchestration.tracing import Tracer  # noqa: E402
from schemas.report import is_source_turn  # noqa: E402
from schemas.tools import Envelope, Provenance, ToolExecution  # noqa: E402
from services.conversations import Conversations  # noqa: E402

import test_planner as TP  # noqa: E402
import test_tool_selector as TS  # noqa: E402
from test_source_turn_evidence import plan as report_plan, state as report_state, tool_exec as report_tool_exec  # noqa: E402

K, R = "CLIENT-001", "CLIENT-002"
QUERY = ("For CLIENT-002, prepare a factual, evidence-backed chronology of the Lakeshore letter of intent. Separate exploratory discussions from confirmed decisions. "
         "Identify unresolved questions and open items. Include relevant dates, source meetings and conversation-turn evidence where available. State explicitly whether any source "
         "establishes that Rebecca has signed the letter. Preserve corrections to earlier statements and distinguish Rebecca's statements from advisor-authored notes. If the evidence does "
         "not establish something, say so rather than infer it. Do not make investment recommendations, suitability judgments, forecasts or transaction decisions.")
FIELDS = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index", "speaker", "utterance", "meeting_context", "source_type", "consent_status"]
ROWS = [  # (interaction, client, datetime, turn, speaker, utterance)
    ("INT-9001", R, "2026-02-01T10:00:00", 1, "Advisor", "Thanks for coming in. Let's talk about the Lakeshore letter of intent."),
    ("INT-9001", R, "2026-02-01T10:00:00", 2, "Client", "I am only exploring the Lakeshore letter of intent, nothing is decided."),
    ("INT-9001", R, "2026-02-01T10:00:00", 3, "Advisor", "Understood. We can look at the retirement date another day."),
    ("INT-9002", R, "2026-03-01T10:00:00", 1, "Advisor", "My file note: the Lakeshore letter of intent was sent to counsel for review."),
    ("INT-9002", R, "2026-03-01T10:00:00", 2, "Client", "I have not signed the Lakeshore letter of intent yet."),
    ("INT-9003", R, "2026-04-01T10:00:00", 1, "Advisor", "Correction to my earlier note: the Lakeshore letter of intent was not sent to counsel until today."),
    ("INT-9003", R, "2026-04-01T10:00:00", 2, "Client", "Counsel is reviewing the Lakeshore letter of intent."),
    ("INT-9100", K, "2026-02-02T10:00:00", 1, "Client", "A different client also mentions Lakeshore here."),
] + [(f"INT-8{n:03d}", K, "2026-02-03T10:00:00", 1, "Advisor", "Thanks, see you next week.") for n in range(300)]      # ordinary turns, so that a word's rarity means something


def _phrases(text):
    import re
    words = re.findall(r"[a-z][a-z'\-]*", text.lower())
    return [" ".join(words[i:i + n]) for n in (1, 2, 3) for i in range(len(words) - n + 1)]


def synthetic_conversations() -> Conversations:
    d = tempfile.mkdtemp()
    p = Path(d) / "conversations.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for iid, cid, when, t, sp, text in ROWS:
            w.writerow([iid, cid, "ADV-1", when, "meeting", t, sp, text, "synthetic", "synthetic", "granted"])
    return Conversations(p)


# --------------------------------------------------------------------------------------------------------------------------------------------------
class Issue1Planner(unittest.TestCase):
    OBSERVED = TP.out(task_type="followup_review", memory_topics=["liquidity_reserve", "business_sale"], needs_calculation=True,
                      required_tools=["get_client_profile", "get_client_portfolio", "get_portfolio_history", "get_client_goals", "get_client_liabilities", "get_source_evidence"],
                      subtasks=[{"id": "T1", "description": "memory", "agent": "memory_orchestrator"}, {"id": "T2", "description": "tools", "agent": "tool_selector", "depends_on": ["T1"]},
                                {"id": "T3", "description": "report", "agent": "report_agent", "depends_on": ["T1", "T2"]}])

    def test_the_observed_misrouting_is_corrected(self):
        p, _ = TP.plan_with(QUERY, self.OBSERVED)
        self.assertEqual(p.client_ids, [R])
        self.assertEqual(p.task_type, "history")                                                   # observed: followup_review
        self.assertTrue(p.needs_history)
        self.assertFalse(p.needs_calculation)
        self.assertEqual(p.memory_topics[0], "business_sale")                                      # the subject of the request
        self.assertNotIn("liquidity_reserve", p.memory_topics)                                     # observed: an unrelated portfolio topic
        self.assertEqual(p.required_tools, ["get_source_evidence"])                                # observed: profile, portfolio, history, goals, liabilities
        self.assertTrue(any(s.agent == "tool_selector" for s in p.subtasks))
        self.assertEqual(p.subtasks[-1].agent, "report_agent")                                     # still Planner -> Memory -> Tool Selector -> Report
        self.assertEqual(p.request_text, QUERY)                                                    # the question itself is preserved
        self.assertTrue(any("replaced by history" in n for n in p.planner_notes))

    def test_stray_topic_came_from_a_substring_match(self):
        self.assertNotIn("liquidity_reserve", infer_topics("Preserve corrections to earlier statements about the Lakeshore letter of intent"))
        self.assertEqual(infer_topics("Lakeshore letter of intent")[0], "business_sale")
        self.assertIn("liquidity_reserve", infer_topics("What is the cash reserve?"))              # real use of the keyword still works
        self.assertIn("retirement_age", infer_topics("when will she retire?"))                      # stems still match from the start of a word

    def test_a_portfolio_request_is_left_alone(self):
        p, _ = TP.plan_with("What is Rebecca's current liquidity and how should I prepare for her portfolio review?",
                            TP.out(task_type="followup_review", memory_topics=["liquidity_reserve"], required_tools=["get_client_portfolio", "calculate_liquidity_ratio"]))
        self.assertEqual((p.task_type, p.memory_topics, p.required_tools), ("followup_review", ["liquidity_reserve"], ["get_client_portfolio", "calculate_liquidity_ratio"]))
        self.assertFalse(is_source_chronology("How should I prepare for the portfolio review?"))

    def test_a_chronology_that_asks_for_goals_keeps_that_tool(self):
        p, _ = TP.plan_with("Give a chronology of the Lakeshore letter of intent and the goals it affects for Rebecca",
                            TP.out(task_type="followup_review", required_tools=["get_client_goals", "get_client_portfolio", "get_source_evidence"]))
        self.assertEqual(p.required_tools, ["get_source_evidence", "get_client_goals"])

    def test_without_the_source_tool_nothing_is_rewritten(self):
        tools = [t for t in TP.TOOLS if t != "get_source_evidence"]
        p, _ = TP.plan_with(QUERY, self.OBSERVED, tools=tools)
        self.assertEqual(p.task_type, "followup_review")


# --------------------------------------------------------------------------------------------------------------------------------------------------
class Issue2ToolSelector(unittest.TestCase):
    def test_repeated_malformed_calls_stop_the_loop_and_are_called_out(self):
        p = TS.plan(clients=(K, R))                                                                # two clients: a missing client_id is ambiguous, so it is NOT filled in
        st, _, llm = TS.run([TS.decide(TS.call("get_client_profile")), TS.decide(TS.call("get_client_profile")), TS.decide(TS.call("get_client_goals", client_id=K))], p=p)
        self.assertEqual([e.status for e in st.executions], ["rejected", "rejected"])
        self.assertIn("required", st.executions[0].error)
        self.assertTrue(st.executions[1].error.startswith("repeated invalid call"))
        self.assertEqual((st.stop_reason, len(llm.calls)), ("no_new_valid_call", 2))               # observed: kept going to max_iterations

    def test_a_missing_client_id_is_filled_from_a_single_client_plan(self):
        st, _, _ = TS.run([TS.decide(TS.call("get_client_profile")), TS.decide(TS.call("get_client_profile", client_id=K)), TS.finish()])
        self.assertEqual([e.status for e in st.executions], ["success", "duplicate"])
        self.assertEqual(st.executions[0].arguments, {"client_id": K})
        self.assertIn("filled in from the plan", st.executions[0].rationale)                       # visible in the trace

    def test_a_missing_client_id_for_a_client_the_plan_does_not_have_is_not_invented(self):
        st, _, _ = TS.run([TS.decide(TS.call("get_client_profile", client_id=R)), TS.finish()])    # explicit wrong client: still rejected
        self.assertEqual(st.executions[0].status, "rejected")

    def test_one_new_call_does_not_disguise_a_turn_of_repeats(self):
        P = TS.plan()
        script = [TS.decide(TS.call("get_client_profile", client_id=K), TS.call("get_client_goals", client_id=K), TS.call("get_client_liabilities", client_id=K)),
                  TS.decide(TS.call("get_client_goals", client_id=K), TS.call("get_client_profile", client_id=K), TS.call("get_client_portfolio", client_id=K),
                            TS.call("get_client_liabilities", client_id=K)),                                  # 1 new, 3 repeats
                  TS.decide(TS.call("get_client_portfolio", client_id=K), TS.call("get_client_profile", client_id=K), TS.call("calculate_asset_allocation", client_id=K),
                            TS.call("get_client_goals", client_id=K)),                                        # 1 new, 3 repeats
                  TS.decide(TS.call("get_portfolio_history", client_id=K))]                                  # must never be asked for
        st, _, llm = TS.run(script, p=P)
        self.assertEqual((st.stop_reason, st.iterations, len(llm.calls)), ("no_new_valid_call", 3, 3))   # observed: all 5 iterations
        self.assertNotIn("get_portfolio_history", [e.tool for e in st.executions])

    def test_a_productive_turn_with_one_repeat_does_not_stop_the_loop(self):
        st, _, _ = TS.run([TS.decide(TS.call("get_client_profile", client_id=K)),
                           TS.decide(TS.call("get_client_profile", client_id=K), TS.call("get_client_goals", client_id=K)), TS.finish()])
        self.assertEqual(st.stop_reason, "model_finished")

    def test_a_failed_call_is_corrected_and_the_retry_runs(self):
        class Flaky(Toolbox):
            failed = False

            async def call(self, name, arguments):
                if name == "get_client_goals" and not Flaky.failed:
                    Flaky.failed = True
                    raise ToolCallError("temporarily unavailable")
                return await super().call(name, arguments)
        st, _, _ = TS.run([TS.decide(TS.call("get_client_goals", client_id=K)), TS.decide(TS.call("get_client_goals", client_id=K, include_history=True)), TS.finish()], toolbox=Flaky())
        self.assertEqual([e.status for e in st.executions], ["error", "success"])
        self.assertEqual((st.stop_reason, st.completion_status), ("model_finished", "complete"))

    def test_the_identical_failed_call_stays_blocked(self):                                          # earlier, deliberate behaviour: an unchanged repeat of a failed call is not re-run
        class Down(Toolbox):
            async def call(self, name, arguments):
                raise ToolCallError("temporarily unavailable")
        st, _, _ = TS.run([TS.decide(TS.call("get_client_goals", client_id=K)), TS.decide(TS.call("get_client_goals", client_id=K)), TS.finish()], toolbox=Down())
        self.assertEqual([e.status for e in st.executions], ["error", "rejected"])

    def test_maximum_iterations_are_respected_when_every_turn_is_productive(self):
        script = [TS.decide(TS.call("get_client_profile", client_id=K)), TS.decide(TS.call("get_client_goals", client_id=K)),
                  TS.decide(TS.call("get_client_liabilities", client_id=K)), TS.decide(TS.call("get_client_portfolio", client_id=K))]
        st, _, llm = TS.run(script, cfg=SelectorConfig(max_iterations=3))
        self.assertEqual((st.stop_reason, st.completion_status, st.iterations, len(llm.calls)), ("max_iterations", "partial", 3, 3))
        self.assertEqual(len(st.successes()), 3)

    def test_the_model_is_shown_what_already_succeeded_and_which_planned_tools_remain(self):
        P = TS.plan(tools=("get_client_profile", "get_client_liabilities"))
        _, _, llm = TS.run([TS.decide(TS.call("get_client_profile", client_id=K)), TS.finish()], p=P)
        second = llm.calls[1]["user"]
        self.assertIn('ALREADY SUCCEEDED - never call these again: get_client_profile({"client_id":"CLIENT-001"})', second)
        line = [x for x in second.splitlines() if x.startswith("PLANNED TOOLS NOT YET")][0]
        self.assertIn("get_client_liabilities", line)
        self.assertNotIn("get_client_profile", line)
        self.assertNotIn("PLANNED TOOLS NOT YET", llm.calls[0]["user"])                          # nothing has been called yet on the first turn


# --------------------------------------------------------------------------------------------------------------------------------------------------
class Issue3SourceTurns(unittest.TestCase):
    def setUp(self):
        self.conv = synthetic_conversations()
        patcher = mock.patch("services.conversations.shared", lambda: self.conv)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = TS.plan(clients=(R,), tools=("get_source_evidence",), task_type="history", request_text=QUERY)

    def run_sel(self, script=None, text=QUERY, p=None, cfg=None):
        return TS.run(script or [TS.finish()], p=p or self.plan, text=text, cfg=cfg)

    def test_search_mode_lists_real_references_for_that_client_only(self):
        from mcp_server.tools import SERVICES
        r = SERVICES.get_source_evidence(R, search_text="Lakeshore")
        s = r["data"]["search"]
        self.assertEqual([(m["interaction_id"], m["turn_index"]) for m in s["matches"]],
                         [("INT-9001", 1), ("INT-9001", 2), ("INT-9002", 1), ("INT-9002", 2), ("INT-9003", 1), ("INT-9003", 2)])      # not K's INT-9100, not the decoy turn
        self.assertNotIn("text", s["matches"][0])                                                                                  # references only
        self.assertEqual([m["cue"] for m in s["matches"]], [None, None, None, None, "correction", None])                           # the correction cue is a ranking hint on the turn that says "Correction"
        self.assertEqual(SERVICES.get_source_evidence(K, search_text="Lakeshore")["data"]["search"]["total"], 1)

    def test_search_matches_a_possessive(self):
        self.conv.rows.append({**self.conv.rows[0], "interaction_id": "INT-9005", "turn_index": 1, "utterance": "Lakeshore's revised terms arrived.", "interaction_datetime": "2026-05-01T10:00:00"})
        from mcp_server.tools import SERVICES
        self.assertIn("INT-9005", [m["interaction_id"] for m in SERVICES.get_source_evidence(R, search_text="Lakeshore")["data"]["search"]["matches"]])

    def test_search_cannot_be_mixed_with_other_modes_or_be_trivial(self):
        from mcp_server.services import ServiceError
        from mcp_server.tools import SERVICES
        for kw in (dict(entity_type="goal", entity_id="GOAL-001"), dict(interaction_id="INT-9001", turn_index=1), dict(interaction_id="INT-9001", turn_indices=[1])):
            with self.assertRaises(ServiceError):
                SERVICES.get_source_evidence(R, search_text="Lakeshore", **kw)
        with self.assertRaises(ServiceError):
            SERVICES.get_source_evidence(R, search_text="a")

    def test_several_turns_of_one_meeting_in_one_call_keep_isolation_and_limits(self):
        from mcp_server.services import ServiceError
        from mcp_server.tools import SERVICES
        r = SERVICES.get_source_evidence(R, interaction_id="INT-9002", turn_indices=[2, 1, 2])
        self.assertEqual([(t["turn_index"], t["speaker"]) for t in r["data"]["turns"]], [(1, "Advisor"), (2, "Client")])
        self.assertEqual(r["data"]["turns"][1]["text"], "I have not signed the Lakeshore letter of intent yet.")
        self.assertEqual(r["as_of"], "2026-03-01")
        for kw in (dict(client_id=K, interaction_id="INT-9002", turn_indices=[1]), dict(client_id=R, interaction_id="INT-9002", turn_indices=[1, 99]),      # other client's / missing turn
                   dict(client_id=R, interaction_id="INT-9002", turn_indices=[]), dict(client_id=R, interaction_id="INT-9002", turn_indices=list(range(1, 14))),
                   dict(client_id=R, interaction_id="INT-9002", turn_indices=[1], turn_index=1), dict(client_id=R, turn_indices=[1])):
            with self.assertRaises(ServiceError, msg=str(kw)):
                SERVICES.get_source_evidence(**kw)

    # ---- subject extraction ---------------------------------------------------------------------------------------------------------------
    def terms(self, text, client=R):
        from agents.tool_selector.source_discovery import source_search_terms
        return source_search_terms(text, client, self.conv, {"Rebecca", "Kenji"})

    def test_subject_of_the_observed_request_is_found_without_capitalisation_rules(self):
        self.assertEqual(self.terms(QUERY), ["letter of intent"])                                    # finds exactly the turns 'Lakeshore' finds here, with the more specific wording
        self.assertNotIn("rebecca", self.terms(QUERY))                                               # the client's name is not a subject
        self.assertTrue(set(self.terms(QUERY)) <= set(_phrases(QUERY)))                              # and it is a phrase of the request itself, not invented

    def test_a_sentence_initial_subject_is_found(self):
        self.assertEqual(self.terms("Lakeshore letter of intent: what happened and when?"), ["letter of intent"])
        self.assertEqual(self.terms("Lakeshore: what happened?"), ["lakeshore"])                       # a subject that is the FIRST word of the request
        self.assertEqual(self.terms("Counsel notes, please."), ["counsel"])                  # capitalised only because it starts the sentence

    def test_a_lowercase_subject_without_proper_nouns_is_found(self):
        self.conv.rows += [{**self.conv.rows[0], "interaction_id": "INT-9006", "client_id": R, "turn_index": i, "speaker": "Client",
                            "utterance": u, "interaction_datetime": "2026-05-01T10:00:00"} for i, u in enumerate(
            ["The earnout schedule worries me.", "Please explain the earnout schedule again."], 1)]
        import agents.tool_selector.source_discovery as sd
        sd._CORPUS.clear()                                                                           # the cached word statistics belong to the old rows
        self.assertEqual(self.terms("what did she say about the earnout schedule?"), ["earnout schedule"])            # one search finds both turns; 'earnout' and 'schedule' alone add nothing
        self.assertEqual(self.terms("how did the earnout evolve?"), ["earnout"])

    def test_a_subject_that_is_not_in_the_clients_conversations_yields_no_query(self):
        self.assertEqual(self.terms("Give a chronology of the Zanzibar term sheet"), [])
        self.assertEqual(self.terms("Please summarise it."), [])

    def test_a_narrower_phrase_is_not_searched_when_a_broader_one_already_finds_its_turns(self):
        self.conv.rows.append({**self.conv.rows[0], "interaction_id": "INT-9004", "turn_index": 1, "utterance": "Lakeshore asked for a call.", "interaction_datetime": "2026-05-01T10:00:00"})
        import agents.tool_selector.source_discovery as sd
        sd._CORPUS.clear()
        self.assertEqual(self.terms(QUERY), ["lakeshore"])                                           # 'letter of intent' finds nothing 'lakeshore' does not

    # ---- meeting selection: coverage, corrections, budget -------------------------------------------------------------------------------------
    @staticmethod
    def meetings(n, correction_at=None, per=2):
        return {f"INT-{k:03d}": {"date": f"2026-01-{k:02d}", "turns": {t: 1 for t in range(1, per + 1)}, "cue": k == correction_at} for k in range(1, n + 1)}

    def test_everything_is_retrieved_when_the_budget_allows(self):
        from agents.tool_selector.source_discovery import plan_meetings
        chosen, omitted = plan_meetings(self.meetings(9), budget=10)
        self.assertEqual((len(chosen), omitted), (9, []))
        self.assertEqual(chosen, sorted(chosen))                                                      # oldest first

    def test_under_budget_pressure_the_earliest_latest_and_corrections_are_kept_and_the_rest_is_named(self):
        from agents.tool_selector.source_discovery import omission_note, plan_meetings
        m = self.meetings(12, correction_at=5)
        chosen, omitted = plan_meetings(m, budget=4)
        self.assertIn("INT-001", chosen)                                                              # earliest
        self.assertIn("INT-012", chosen)                                                              # latest
        self.assertIn("INT-005", chosen)                                                              # the meeting with the correction
        self.assertEqual(len(chosen), 4)
        self.assertEqual(sorted(chosen + [o["interaction_id"] for o in omitted]), sorted(m))          # nothing silently discarded: every meeting is either retrieved or named
        self.assertTrue(all(o["reason"] == "tool-call budget" and o["turns"] == [1, 2] for o in omitted))
        self.assertEqual(omission_note(omitted[0], 12), f"Not retrieved: {omitted[0]['interaction_id']} ({omitted[0]['date']}; matching turn(s) 1, 2) because the tool-call budget of 12 was used")

    def test_one_call_budget_keeps_the_latest_meeting(self):
        from agents.tool_selector.source_discovery import plan_meetings
        chosen, omitted = plan_meetings(self.meetings(5), budget=1)
        self.assertEqual((chosen, len(omitted)), (["INT-005"], 4))

    def test_all_matching_turns_of_every_meeting_are_retrieved_in_the_real_flow(self):
        st, tr, llm = self.run_sel()
        searches = [e for e in st.executions if "search_text" in e.arguments]
        reads = [e for e in st.executions if "interaction_id" in e.arguments]
        self.assertEqual({e.arguments["search_text"] for e in searches}, {"letter of intent"})
        self.assertTrue(all(e.status == "success" for e in st.executions))
        self.assertEqual([(e.arguments["interaction_id"], e.arguments["turn_indices"]) for e in reads], [("INT-9001", [1, 2]), ("INT-9002", [1, 2]), ("INT-9003", [1, 2])])   # earliest to latest, every matching turn
        self.assertFalse([e for e in st.executions if "entity_id" in e.arguments])                                      # no guessed entity ids
        self.assertEqual(st.missing_information, [])
        self.assertEqual(len(llm.calls), 1)                                                                             # the model is only asked whether anything is missing
        for e in reads:
            for t in e.result.data["turns"]:
                row = self.conv.turn(t["interaction_id"], t["turn_index"])
                self.assertEqual((t["text"], t["speaker"], e.result.as_of), (row["utterance"], row["speaker"], row["interaction_datetime"][:10]))
        ev = [x["payload"] for x in tr.events if x["event"] == "source_discovery"][0]
        self.assertEqual((ev["meetings_found"], ev["meetings_retrieved"], ev["turns_retrieved"], ev["omitted"]), (3, 3, 6, []))

    def test_every_retrieved_id_came_from_a_search_result(self):
        st, _, _ = self.run_sel()
        listed = {(m["interaction_id"], m["turn_index"]) for e in st.executions if "search_text" in e.arguments for m in e.result.data["search"]["matches"]}
        asked = {(e.arguments["interaction_id"], i) for e in st.executions if "interaction_id" in e.arguments for i in e.arguments["turn_indices"]}
        self.assertEqual(asked, listed)

    def test_when_the_budget_is_too_small_exactly_what_was_left_out_is_disclosed(self):
        cap = len(self.terms(QUERY)) + 1
        st, tr, _ = self.run_sel(cfg=SelectorConfig(max_executions=cap))                              # the searches, then ONE retrieval
        reads = [e for e in st.executions if "interaction_id" in e.arguments]
        self.assertEqual([e.arguments["interaction_id"] for e in reads], ["INT-9003"])                # the latest meeting, which carries the correction
        notes = [m for m in st.missing_information if m.startswith("Not retrieved:")]
        self.assertEqual(notes, [f"Not retrieved: INT-9001 (2026-02-01; matching turn(s) 1, 2) because the tool-call budget of {cap} was used",
                                 f"Not retrieved: INT-9002 (2026-03-01; matching turn(s) 1, 2) because the tool-call budget of {cap} was used"])
        self.assertEqual(st.completion_status, "partial")
        omitted = [x["payload"]["omitted"] for x in tr.events if x["event"] == "source_discovery"][0]
        self.assertEqual([o["interaction_id"] for o in omitted], ["INT-9001", "INT-9002"])
        rep = ReportAgent(MockLLM(handlers={"report": [LLMError("down")] * 3})).write(QUERY, report_plan((R,)), {}, st)
        self.assertEqual([u for u in rep.uncertainties if "Not retrieved" in u][0], "Missing information: " + notes[0])      # reaches the report

    def test_a_correction_meeting_survives_truncation(self):
        cap = len(self.terms(QUERY)) + 2
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=cap))                               # the searches, then TWO retrievals: earliest + latest; the correction is in the latest
        self.assertEqual([e.arguments["interaction_id"] for e in st.executions if "interaction_id" in e.arguments], ["INT-9001", "INT-9003"])
        retrieved = " ".join(t["text"] for e in st.executions if "interaction_id" in e.arguments for t in e.result.data["turns"])
        self.assertIn("Correction to my earlier note", retrieved)
        self.assertEqual([m for m in st.missing_information if m.startswith("Not retrieved:")], [f"Not retrieved: INT-9002 (2026-03-01; matching turn(s) 1, 2) because the tool-call budget of {cap} was used"])

    def test_a_failed_retrieval_is_disclosed_not_dropped(self):
        class Flaky(Toolbox):
            async def call(self, name, arguments):
                if arguments.get("interaction_id") == "INT-9002":
                    raise ToolCallError("temporarily unavailable")
                return await super().call(name, arguments)
        st, _, _ = TS.run([TS.finish()], p=self.plan, text=QUERY, toolbox=Flaky())
        notes = [m for m in st.missing_information if m.startswith("Not retrieved:")]
        self.assertEqual(len(notes), 1)
        self.assertIn("INT-9002", notes[0])
        self.assertIn("the retrieval call failed", notes[0])

    def test_a_truncated_search_is_disclosed(self):
        from agents.tool_selector.source_discovery import meetings_from
        env = Envelope(tool="get_source_evidence", client_id=R, data={"search": {"query": "x", "total": 80, "truncated": True, "matches": [
            {"interaction_id": "INT-1", "turn_index": 1, "speaker": "Client", "date": "2026-01-01", "cue": None}]}}, provenance=Provenance(source="conversations.csv"))
        m, notes = meetings_from([ToolExecution(id="EX-1", iteration=0, tool="get_source_evidence", arguments={}, status="success", result=env)])
        self.assertEqual(list(m), ["INT-1"])
        self.assertEqual(notes, ["the search for 'x' matched 80 turns but lists only the first 1"])

    def test_no_discovery_for_an_explicit_turn_request_or_without_the_planned_tool(self):
        st, _, _ = self.run_sel(text="Retrieve INT-9002 turn 2 for Rebecca")
        self.assertEqual(st.executions, [])
        st, _, _ = self.run_sel(p=TS.plan(clients=(R,), tools=(), task_type="history"))
        self.assertEqual(st.executions, [])

    def test_no_discovery_for_a_request_that_is_not_a_chronology(self):
        st, _, _ = self.run_sel(text="What is the evidence for Rebecca's Lakeshore goal?")            # the model may still use the search mode itself; code does not spend the budget unasked
        self.assertEqual(st.executions, [])

    def test_a_subject_with_no_matching_turn_is_reported_not_invented(self):
        st, _, _ = self.run_sel(text="Give a chronology of the Zanzibar term sheet")
        self.assertEqual(st.executions, [])
        self.assertTrue(any("no searchable subject" in m for m in st.missing_information))

    # ---- what the Report Agent then does with it -------------------------------------------------------------------------------------------
    def evidence(self):
        st, _, _ = self.run_sel()
        return st, build_evidence(report_plan((R,)), {}, st)[0]

    def test_retrieved_turns_are_conversation_evidence_with_speaker_and_provenance_and_the_search_is_not_evidence(self):
        st, ev = self.evidence()
        self.assertEqual([e.source_class for e in ev], ["conversation_evidence"] * 6)                  # the two search envelopes are not evidence; each turn of a multi-turn call is its own item
        by = {(e.interaction_id, e.turn_index): e for e in ev}
        self.assertEqual(by[("INT-9002", 1)].speaker, "Advisor")                                      # an advisor-authored note
        self.assertEqual(by[("INT-9002", 2)].speaker, "Client")                                       # the client's own words
        self.assertEqual(by[("INT-9002", 2)].excerpt, "I have not signed the Lakeshore letter of intent yet.")
        self.assertEqual(by[("INT-9003", 1)].event_date, "2026-04-01")
        self.assertEqual(by[("INT-9002", 1)].tool_execution_id, by[("INT-9002", 2)].tool_execution_id)  # one execution, two cited turns
        self.assertTrue(all(is_source_turn(e) and e.synthetic and "conversations.csv" in e.provenance and e.tool_execution_id for e in ev))

    def test_a_turn_is_backed_only_by_its_own_text_not_by_another_turn_of_the_same_call(self):
        st, ev = self.evidence()
        a, b = [e for e in ev if e.interaction_id == "INT-9002"]
        text = EvidenceVerifier._source_text([a], st)
        self.assertIn("file note", text)
        self.assertNotIn("have not signed", text)                                                     # the other turn's text is not available to support a claim about this one

    def test_report_writes_every_retrieved_turn_and_says_when_more_exist_than_it_writes(self):
        st, _ = self.evidence()
        self.assertEqual(len(ReportAgent(MockLLM(handlers={"report": [LLMError("down")] * 3})).write(QUERY, report_plan((R,)), {}, st).historical_context), 6)
        env = Envelope(tool="get_source_evidence", client_id=R, as_of="2026-02-01", provenance=Provenance(source="conversations.csv"),
                       data={"turns": [{"interaction_id": "INT-9001", "turn_index": i, "speaker": "Client", "text": f"Lakeshore item number {i}."} for i in range(1, 26)]})
        from schemas.tools import ToolSelectorState
        big = ToolSelectorState(request_id="REQ-t")
        big.executions.append(ToolExecution(id="EX-1", iteration=0, tool="get_source_evidence", arguments={}, status="success", result=env))
        rep = ReportAgent(MockLLM(handlers={"report": [LLMError("down")] * 3})).write(QUERY, report_plan((R,)), {}, big)
        self.assertEqual(len(rep.historical_context), 24)
        self.assertEqual(len([e for e in rep.evidence if is_source_turn(e)]), 25)
        self.assertTrue(any("25 source turns were retrieved but only the first 24 are written out" in u for u in rep.uncertainties))

    def test_report_fallback_is_chronological_attributed_and_never_says_signed(self):
        st, _ = self.evidence()
        rep = ReportAgent(MockLLM(handlers={"report": [LLMError("down")] * 3})).write(QUERY, report_plan((R,)), {}, st)
        text = [f.text for f in rep.historical_context]
        self.assertEqual(len(text), 6)
        self.assertEqual([t.split(",")[0] for t in text], sorted(t.split(",")[0] for t in text))      # oldest first
        self.assertTrue(any("recorded speaker Advisor" in t and "Correction to my earlier note" in t for t in text))   # corrections and advisor notes are kept and attributed
        self.assertTrue(any("recorded speaker Client" in t and "have not signed" in t for t in text))
        self.assertIn("not that any statement is true", rep.summary)
        self.assertNotIn("signed", rep.summary)
        self.assertEqual([t for t in text if "signed" in t], [t for t in text if "have not signed" in t])           # 'signed' appears only inside the client's own recorded denial


# --------------------------------------------------------------------------------------------------------------------------------------------------
class Issue4Report(unittest.TestCase):
    """The observed run had no conversation evidence: only empty entity lookups, so a model draft about the letter had nothing to rest on."""

    def observed_state(self):
        empty = [report_tool_exec(i, "get_source_evidence", {"entity_type": t, "entity_id": e, "evidence": []}) for i, (t, e) in enumerate(
            [("goal", "GOAL-001"), ("holding", "HOLDING-001"), ("transaction", "TRANSACTION-001"), ("letter_of_intent", "LAKESHORE-letter-of-intent")], 1)]
        st = report_state(*empty, report_tool_exec(5, "get_client_goals", {"goals": [{"goal_id": "GOL-002-01a", "type": "Business sale"}]}))
        st.missing_information.append("no turn of this client's conversations mentions 'Lakeshore'")
        return st

    DRAFT = {"summary": "Rebecca signed the Lakeshore letter of intent on 1 March.", "key_findings": [], "financial_analysis": [], "uncertainties": [], "suggested_followups": [], "claims": [],
             "historical_context": [{"text": "Rebecca signed the Lakeshore letter of intent after meeting counsel.", "refs": ["E99"]}]}

    def test_nothing_valid_draft_is_explained_in_the_trace_and_the_fallback_states_the_gap(self):
        tr = Tracer("REQ-t", persist=False)
        rep = ReportAgent(MockLLM(handlers={"report": [self.DRAFT]}), tracer=tr).write(QUERY, report_plan((R,)), {}, self.observed_state())
        self.assertEqual(rep.validation["mode"], "deterministic_nothing_valid")
        ev = [x["payload"] for x in tr.events if x["event"] == "report_written"][0]
        self.assertEqual((ev["mode"], ev["model_called"], ev["findings_dropped"]), ("deterministic_nothing_valid", True, 1))
        self.assertEqual(ev["finding_drop_reasons"], {"no valid evidence reference": 1})              # the precise reason: the cited evidence does not exist
        self.assertNotIn("signed", str(ev))                                                           # the trace carries reasons and counts, never the model's sentences
        self.assertNotIn("Rebecca signed", rep.summary)
        self.assertTrue(any("no turn of this client's conversations mentions" in u for u in rep.uncertainties))
        self.assertFalse(any(is_source_turn(e) for e in rep.evidence))

    def test_a_draft_with_no_valid_claim_still_leaves_the_retrieved_turns_in_the_report(self):
        conv = synthetic_conversations()
        with mock.patch("services.conversations.shared", lambda: conv):
            st, _, _ = TS.run([TS.finish()], p=TS.plan(clients=(R,), tools=("get_source_evidence",), task_type="history"), text=QUERY)
        rep = ReportAgent(MockLLM(handlers={"report": [self.DRAFT]})).write(QUERY, report_plan((R,)), {}, st)
        self.assertEqual(rep.validation["findings_dropped"][0]["reason"], "no valid evidence reference")
        self.assertEqual(len(rep.historical_context), 6)                                              # the draft's invalid finding is dropped; the code-rendered records stay
        self.assertNotIn("Rebecca signed", " ".join(f.text for f in rep.historical_context))
        with mock.patch("services.conversations.shared", lambda: conv):                                   # the harness then verifies the report: the unsupported summary is replaced
            final, _ = EvidenceVerifier(conv).verify_report(rep, [R], st)
        self.assertNotIn("Rebecca signed", final.summary)
        self.assertIn("recorded speaker Client", final.summary)


# --------------------------------------------------------------------------------------------------------------------------------------------------
class Issue5ApprovalClassification(unittest.TestCase):
    policy = ApprovalPolicy()

    def cats(self, text, task="history"):
        return self.policy.classify_report(SimpleNamespace(task_type=task), text)

    def test_the_observed_chronology_request_is_not_a_suitability_request(self):
        self.assertEqual(self.cats(QUERY), [])
        self.assertFalse(asks_for_advice(QUERY))

    def test_prohibitions_of_advice_are_not_requests_for_advice(self):
        for t in ("Summarise the meetings. Do not make investment recommendations.", "List the events, without any recommendation or suitability judgment.",
                  "Do not give financial advice, forecasts or transaction decisions.", "No investment recommendations, please. Just the chronology."):
            self.assertEqual(self.cats(t), [], t)

    def test_genuine_advice_requests_still_require_approval(self):
        for t in ("Should Rebecca sell the Lakeshore shares?", "What do you recommend for the Lakeshore offer?", "Is the Lakeshore offer suitable for her?",
                  "Can she afford to accept the offer?", "Is it wise to buy more equities?", "Please rebalance the portfolio."):
            self.assertEqual(self.cats(t), [RiskCategory.SUITABILITY_CONCLUSION], t)

    def test_a_prohibition_does_not_launder_a_genuine_advice_request_in_the_same_text(self):
        for t in ("Do not make investment recommendations. Should we sell?", "Without any recommendation, tell me whether she should sell", "Do not hesitate to recommend a sale.",
                  "Do not recommend anything, but is it wise to buy?", "No recommendations please, but should I switch funds?"):
            self.assertEqual(self.cats(t), [RiskCategory.SUITABILITY_CONCLUSION], t)

    def test_a_forecast_feasibility_plan_is_gated_whatever_the_wording(self):
        self.assertEqual(self.cats("Do not make recommendations.", task="forecast_feasibility"), [RiskCategory.SUITABILITY_CONCLUSION])

    def test_the_gate_can_still_be_switched_off_only_by_policy(self):
        self.assertEqual(ApprovalPolicy(required=False).classify_report(SimpleNamespace(task_type="forecast_feasibility"), "Should she sell?"), [])


class UiMemoryCount(unittest.TestCase):
    def test_a_withheld_answer_deliberately_carries_no_memory_ids(self):
        """The UI showed '0 memories retrieved' because the answer was held for approval: the API returns NO ids for a withheld answer (nothing but the stub may leave the process).
        The released path counts episodes and unresolved items as well (api/main.py ask()); only the UI wording was wrong."""
        v = withheld_view({"status": "pending", "approval_id": "APR-x", "category": "suitability_conclusion"}, "REQ-t")
        self.assertEqual((v["retrieved_memory_ids"], v["released"], v["status"]), ([], False, "pending_approval"))
        src = (HERE.parent / "ui" / "cmf-brain-ui" / "src" / "components" / "AdvisorQuery.tsx").read_text(encoding="utf-8")
        self.assertIn("result!.released === false ? 'Memory details are withheld until the answer is approved'", src)
        api = (HERE.parent / "api" / "main.py").read_text(encoding="utf-8")
        self.assertIn("ctx.current, ctx.history, ctx.episodes, ctx.procedures, ctx.working, ctx.unresolved", api)


if __name__ == "__main__":
    unittest.main()
