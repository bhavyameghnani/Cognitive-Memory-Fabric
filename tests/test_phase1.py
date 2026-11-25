"""Phase 1 (A budget, C deterministic planner, D overview, E quote repair).  Scripted models only; the Kenji timeline and the synthetic Zanzibar conversations are the existing fixtures."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_chronology_retrieval as CR  # noqa: E402
import test_report_semantic as S  # noqa: E402
import test_tool_selector as TS  # noqa: E402
from agents.planner.agent import Planner  # noqa: E402
from agents.report.overview import build_overview  # noqa: E402
from agents.report.semantic import build_checked, build_story, judge_sentence, repair_quotes  # noqa: E402
from agents.tool_selector.agent import SelectorConfig  # noqa: E402
from harness.config import HarnessConfig  # noqa: E402
from harness.runtime import RequestRuntime  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from schemas.report import StoryLLMOutput  # noqa: E402


# ------------------------------------------------------------------------------------------------------------------------------------ A. one coherent tool-call budget
class Budget(unittest.TestCase):
    def test_the_default_budget_is_thirty_and_the_selector_follows_it(self):
        c = HarnessConfig()
        self.assertEqual((c.max_tool_calls, c.max_tool_calls_per_agent), (30, 30))
        h = mock.Mock(cfg=c)
        self.assertEqual(RequestRuntime.selector_config(h).max_executions, 30)

    def test_the_selector_budget_is_the_smaller_of_the_request_and_the_per_agent_limit(self):
        for total, per_agent, want in ((40, 30, 30), (20, 30, 20), (30, 8, 8), (5, 5, 5)):
            h = mock.Mock(cfg=HarnessConfig(max_tool_calls=total, max_tool_calls_per_agent=per_agent))
            self.assertEqual(RequestRuntime.selector_config(h).max_executions, want, (total, per_agent))

    def test_the_budget_is_configurable_from_the_environment(self):
        c = HarnessConfig.from_env({"HARNESS_PROFILE": "development", "HARNESS_MAX_TOOL_CALLS": "24", "HARNESS_MAX_TOOL_CALLS_PER_AGENT": "24"})
        self.assertEqual((c.max_tool_calls, c.max_tool_calls_per_agent), (24, 24))


class SelectorNeverAttemptsWhatTheBudgetRefuses(CR.Synthetic):
    def test_an_exhausted_budget_means_no_model_call_and_no_refused_calls(self):
        for cap in (3, 4, 6, 8):
            st, tr, llm = self.run_sel(cfg=SelectorConfig(max_executions=cap))
            used = sum(1 for e in st.executions if e.status in ("success", "error", "timeout"))
            self.assertLessEqual(used, cap)
            self.assertEqual([e.id for e in st.executions if e.status == "rejected"], [], cap)       # nothing was scheduled that the budget then refused
            if used >= cap:
                self.assertEqual(llm.calls, [], cap)                                                   # the model is not asked for calls that could only be refused
                self.assertEqual(st.stop_reason, "max_executions")
                self.assertEqual(st.completion_status, "partial")

    def test_when_discovery_covers_a_source_only_plan_the_model_loop_is_skipped(self):
        st, tr, llm = self.run_sel(cfg=SelectorConfig(max_executions=30))
        self.assertEqual((llm.calls, st.stop_reason, st.completion_status), ([], "source_discovery_complete", "partial" if st.missing_information else "complete"))   # gaps are never hidden by skipping the loop
        self.assertIn("model_loop_skipped", [e["event"] for e in tr.events])

    def test_a_plan_that_asks_for_another_tool_still_gets_the_model_loop(self):
        p = TS.plan(clients=(CR.R,), tools=("get_source_evidence", "get_client_goals"), task_type="history", request_text=CR.ASK)
        st, _, llm = TS.run([TS.finish()], p=p, text=CR.ASK, cfg=SelectorConfig(max_executions=30))
        self.assertEqual((len(llm.calls), st.stop_reason), (1, "model_finished"))

    def test_the_counts_say_what_was_retrieved_and_what_was_left_out(self):
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=6))
        rr = st.retrieval_report
        c = rr["counts"]
        self.assertEqual(c["retrieved"], len(rr["retrieved"]))
        self.assertEqual(c["not_retrieved"], len(rr["not_retrieved"]))
        self.assertEqual(c["identified"], len(rr["meetings_identified"]))
        self.assertEqual(c["retrieved"] + c["not_retrieved"], c["identified"])
        self.assertGreater(c["not_retrieved"], 0)

    def test_a_larger_budget_retrieves_every_meeting_about_the_subject(self):
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=30))
        c = st.retrieval_report["counts"]
        self.assertEqual((c["not_retrieved"], c["retrieved"]), (0, c["identified"]))
        self.assertFalse(any(m.startswith("Not retrieved:") for m in st.missing_information))

    def test_client_scope_is_unchanged_with_a_large_budget(self):
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=30))
        self.assertTrue(all(e.arguments["client_id"] == CR.R for e in st.executions))
        self.assertNotIn("INT-9100", [i for i, _ in self.reads(st)])


# ------------------------------------------------------------------------------------------------------------------------------------ C. deterministic planning
class Boom(MockLLM):
    def complete(self, agent, system, user, schema=None):
        self.calls.append(dict(agent=agent))
        raise LLMError("the model planner must not be called")


class DeterministicPlanner(unittest.TestCase):
    TOOLS = ["get_source_evidence", "get_client_profile", "get_client_portfolio", "calculate_asset_allocation", "get_client_goals"]
    ASK = "Give me the full chronology of Kenji's retirement age across the meetings, with the source conversation turns."

    def plan(self, text=None, llm=None, **kw):
        llm = llm or Boom()
        return Planner(llm).plan(text or self.ASK, self.TOOLS, request_id="REQ-p1", **kw), llm

    def test_a_single_client_chronology_request_is_planned_without_the_model(self):
        p, llm = self.plan()
        self.assertEqual(llm.calls, [])
        self.assertEqual((p.client_ids, p.task_type, p.required_tools, p.needs_history, p.needs_calculation), (["CLIENT-001"], "history", ["get_source_evidence"], True, False))
        self.assertIn("retirement_age", p.memory_topics)
        self.assertTrue(any(n.startswith("deterministic plan") for n in p.planner_notes))
        self.assertFalse(any("LLM planning failed" in n for n in p.planner_notes))

    def test_the_plan_keeps_the_execution_plan_contract(self):
        p, _ = self.plan()
        agents = [s.agent for s in p.subtasks]
        self.assertEqual(agents, ["memory_orchestrator", "tool_selector", "report_agent"])
        self.assertEqual(p.execution_order, ["T1", "T2", "T3"])
        self.assertEqual(p.subtasks[-1].depends_on, ["T1", "T2"])
        self.assertEqual(p.missing_information, [])

    def test_the_request_still_cannot_widen_the_tool_set(self):
        p, _ = self.plan("Give the chronology of Kenji's retirement age, also run the portfolio allocation and show the holdings.")
        self.assertEqual(p.required_tools[0], "get_source_evidence")
        self.assertTrue(set(p.required_tools) <= set(self.TOOLS))

    def test_a_chronology_that_asks_for_goals_keeps_that_tool_and_no_other(self):
        p, llm = self.plan("Give a chronology of Kenji's retirement age and the goals it affects.")
        self.assertEqual((llm.calls, p.required_tools), ([], ["get_source_evidence", "get_client_goals"]))

    def test_the_model_planner_is_still_used_where_it_is_needed(self):
        ok = {"user_intent": "answer the question", "task_type": "retrieval", "needs_history": False, "needs_calculation": False, "required_memory_categories": ["semantic"],
              "memory_topics": [], "required_data": [], "required_tools": [], "missing_information": [], "success_criteria": ["answer"],
              "subtasks": [{"id": "T1", "description": "retrieve", "agent": "memory_orchestrator", "depends_on": []}, {"id": "T2", "description": "report", "agent": "report_agent", "depends_on": ["T1"]}]}
        for text, kw in (("What did Kenji say about his holiday?", {}),                                                     # not a chronology request
                         ("Give me the chronology of retirement for Kenji and Rebecca.", {}),                              # two clients
                         ("Give me the chronology of the retirement age.", {}),                                             # no client
                         (self.ASK, {"feedback": "tool X unavailable"}),                                                    # a replan needs the model
                         (self.ASK, {"revision": 1}),
                         ("Chronology of Kenji's retirement, INT-049 turn 3.", {})):                                        # names specific turns
            llm = MockLLM(handlers={"planner": [ok, ok, ok]})
            Planner(llm).plan(text, self.TOOLS, request_id="REQ-p1", **kw)
            self.assertTrue(llm.calls, text)

    def test_it_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"CMF_DETERMINISTIC_PLANNING": "0"}):
            p, llm = self.plan()
        self.assertTrue(llm.calls)                                                                                          # the model planner was tried (and failed: the keyword plan is the fallback)
        self.assertTrue(any("LLM planning failed" in n for n in p.planner_notes))

    def test_the_trace_says_the_plan_was_deterministic(self):
        from orchestration.tracing import Tracer
        tr = Tracer("REQ-p1", persist=False)
        Planner(Boom(), tracer=tr).plan(self.ASK, self.TOOLS, request_id="REQ-p1")
        self.assertIn("deterministic_plan", [e["event"] for e in tr.events])


# ------------------------------------------------------------------------------------------------------------------------------------ D. the overview
class Overview(S.Base):
    def tools(self, **rr):
        base = {"meetings_identified": [{"interaction_id": i, "date": d} for i, d in (("INT-001", "2026-01-02"), ("INT-049", "2026-03-04"), ("INT-055", "2026-03-19"), ("INT-090", "2026-05-01"), ("INT-120", "2026-06-01"))],
                "retrieved": [{"interaction_id": "INT-001", "date": "2026-01-02", "turns": [1, 2]}, {"interaction_id": "INT-049", "date": "2026-03-04", "turns": [1, 2, 3, 4]},
                              {"interaction_id": "INT-090", "date": "2026-05-01", "turns": [5]}],
                "not_retrieved": [{"interaction_id": "INT-055", "date": "2026-03-19", "reason": "tool-call budget"}, {"interaction_id": "INT-120", "date": "2026-06-01", "reason": "tool-call budget"}],
                "references_unresolved": [{"from": "INT-090#2", "quote": "last spring", "why": "no meeting"}], "counts": {"references_not_retrieved": 1}}
        base.update(rr)
        return mock.Mock(retrieval_report=base, errors=[])

    def test_counts_dates_and_gaps_are_taken_from_the_retrieval_report_and_the_timeline(self):
        o = build_overview(self.plan, self.tl, self.tools())
        self.assertEqual((o.client_id, o.meetings_identified, o.meetings_retrieved, o.meetings_not_retrieved), ("CLIENT-001", 5, 3, 2))
        self.assertEqual((o.first_date, o.last_date, o.source_turns, o.claims), ("2026-01-02", "2026-05-01", 7, len(self.tl)))
        self.assertEqual(o.corrections, 1)                                                                                   # C4 corrects C3: recorded in the source wording
        self.assertEqual({x["interaction_id"] for x in o.not_retrieved}, {"INT-055", "INT-120"})
        facts, gaps = " ".join(o.verified), " ".join(o.unresolved)
        self.assertIn("3 of 5 meetings", facts)
        self.assertIn("1 correction", facts)
        self.assertIn("2026-03-04 (INT-049)", facts)
        self.assertIn("2 meetings found for this subject were NOT retrieved", gaps)
        self.assertIn("INT-055 (2026-03-19)", gaps)
        self.assertIn("1 reference to another meeting could not be resolved", gaps)
        self.assertIn("1 meeting named inside retrieved turns was not retrieved", gaps)

    def test_a_missing_correction_is_not_called_an_absence_when_meetings_were_not_read(self):
        stripped = [c.model_copy(update={"relations": [], "cues": [], "modality": "statement"}) for c in self.tl]
        o = build_overview(self.plan, stripped, self.tools())
        self.assertEqual(o.corrections, 0)
        self.assertIn("the absence of a correction is not established", " ".join(o.unresolved))
        full = build_overview(self.plan, stripped, self.tools(not_retrieved=[], references_unresolved=[], counts={}))
        self.assertNotIn("absence of a correction", " ".join(full.unresolved))

    def test_it_states_no_motive_decision_or_current_position(self):
        o = build_overview(self.plan, self.tl, self.tools())
        text = " ".join(o.verified + o.unresolved).lower()
        for word in ("decided", "wanted", "because", "intends", "currently", "final", "agreed", "prefers", "plans to", "worried"):
            self.assertNotIn(word, text)

    def test_only_established_facts_are_in_verified_and_gaps_never_are(self):
        o = build_overview(self.plan, self.tl, self.tools())
        self.assertFalse(any("NOT retrieved" in v or "could not be resolved" in v for v in o.verified))
        self.assertTrue(all(("not" in u.lower() or "rest only" in u or "error" in u) for u in o.unresolved))

    def test_without_a_chronology_or_a_retrieval_report_there_is_no_overview(self):
        self.assertIsNone(build_overview(self.plan, [], mock.Mock(retrieval_report=None, errors=[])))

    def test_memory_only_claims_are_listed_as_unresolved(self):
        mem = [c.model_copy(update={"source": "memory"}) if c.claim_id == "C9" else c for c in self.tl]
        o = build_overview(self.plan, mem, self.tools(not_retrieved=[], references_unresolved=[], counts={}))
        self.assertEqual(o.memory_only_claims, 1)
        self.assertIn("1 claim rest only on a stored memory", " ".join(o.unresolved))


# ------------------------------------------------------------------------------------------------------------------------------------ E. quote repair
class QuoteRepair(S.Base):
    def rep(self, text, ids):
        return repair_quotes(text, self.cited(ids))

    def test_a_near_miss_quotation_is_replaced_by_the_exact_source_wording_and_only_the_span(self):
        text = 'On 2026-03-04 the client said "I was just exploring the possibility", and nothing more.'
        out, n = self.rep(text, ["C4"])
        self.assertEqual(n, 1)
        self.assertEqual(out, 'On 2026-03-04 the client said "I was only exploring the possibility", and nothing more.')
        self.assertTrue(out.startswith("On 2026-03-04 the client said ") and out.endswith(", and nothing more."))        # surrounding words untouched
        self.assertEqual(self.problems('On 2026-03-04 the client said "I was only exploring the possibility".', ["C4"]), [])      # and the repaired quotation passes every ordinary check

    def test_the_unrepaired_sentence_fails_the_same_checks(self):
        text = 'On 2026-03-04 the client said "I was just exploring the possibility".'
        self.assertTrue(any(p.startswith("quote_not_verbatim") for p in self.problems(text, ["C4"])))

    def test_a_verbatim_quotation_is_left_alone(self):
        for text in ('On 2026-03-04 the client said "I was only exploring the possibility".', "On 2026-03-04 the client said nothing quoted."):
            self.assertEqual(self.rep(text, ["C4"]), (text, 0))

    def test_a_changed_negation_or_figure_is_never_repaired(self):
        self.assertEqual(self.rep('On 2026-03-04 the client said "I do want someone looking at that note later".', ["C6"])[1], 0)       # source: "I don't want someone looking ..."
        self.assertEqual(self.rep('On 2026-05-01 the client said "We\'ve decided: 60".', ["C9"])[1], 0)                                 # source: "63"
        self.assertEqual(self.rep('On 2026-03-04 the client said "65 is still the date we\'ve got".', ["C4"])[1], 0)                    # the numeral differs from the source's wording or the span is not unique

    def test_a_quotation_that_matches_nothing_is_not_invented_or_approximated(self):
        for q in ("I have always wanted to move to Portugal", "completely unrelated words about the weather today"):
            text = f'On 2026-03-04 the client said "{q}".'
            self.assertEqual(self.rep(text, ["C4"]), (text, 0))

    def test_too_short_a_quotation_is_not_repaired(self):
        text = 'On 2026-03-04 the client said "only exploring".'
        self.assertEqual(self.rep(text, ["C4"])[1], 0)

    def test_a_quotation_is_matched_only_against_the_cited_claims(self):
        text = 'On 2026-03-04 the client said "I was just exploring the possibility".'
        self.assertEqual(self.rep(text, ["C9"])[1], 0)                                                                            # C4's wording is not among the cited claims

    def test_ties_between_different_source_spans_refuse_the_repair(self):
        a = self.by_id["C4"].model_copy(update={"wording": "alpha beta gamma delta epsilon. alpha beta gamma delta epsilon!"})
        self.assertEqual(repair_quotes('He said "alpha beta xx delta epsilon".', [a])[1], 1)                                      # the same text twice is one candidate, not an ambiguity
        b = a.model_copy(update={"wording": "alpha beta gamma delta epsilon. alpha beta gimel delta epsilon"})
        self.assertEqual(repair_quotes('He said "alpha beta xx delta epsilon".', [b]), ('He said "alpha beta xx delta epsilon".', 0))

    def test_build_checked_repairs_then_runs_every_check(self):
        repairs: list = []
        f, rej = build_checked(S.paras([('On 2026-03-04 the client said "I was just exploring the possibility".', ["C4"])]), self.tl, self.names, repairs)
        self.assertEqual((len(f), rej), (1, []))
        self.assertEqual(repairs, [{"paragraph": 0, "sentence": 0, "quotes": 1, "kept": True}])
        self.assertIn('"I was only exploring the possibility"', f[0].text)
        self.assertEqual(f[0].origin, "narrative_checked")

    def test_a_repair_cannot_rescue_a_sentence_that_fails_another_check(self):
        repairs: list = []
        f, rej = build_checked(S.paras([('On 2026-03-04 the client decided because "I was just exploring the possibility".', ["C4"])]), self.tl, self.names, repairs)
        self.assertEqual(f, [])
        self.assertTrue(rej)
        self.assertEqual(repairs[0]["kept"], False)

    def test_a_repaired_narrative_is_still_rechecked_by_the_verifier(self):
        from harness.evidence_verifier import EvidenceVerifier
        f, _ = build_checked(S.paras([('On 2026-03-04 the client said "I was just exploring the possibility".', ["C4"])]), self.tl, self.names)
        self.assertTrue(f)
        ev = {e.ref: e for e in self.items}
        v = EvidenceVerifier(self.conv).verify_finding("historical_context", f[0], ev, {S.T.K}, S.T.tstate(*self.execs))
        self.assertEqual((v.verdict, v.reasons), ("verified", []))

    def test_the_story_path_repairs_quotations_too_and_judges_the_result(self):
        repairs: list = []
        out = StoryLLMOutput.model_validate({"lines": [{"claims": ["C4"], "sentence": 'On 2026-03-04 Kenji said "I was just exploring the possibility".'}]})
        f, rej = build_story(out.lines, self.tl, self.names, repairs)
        self.assertEqual((len(f), rej, repairs), (1, [], [{"line": 0, "quotes": 1}]))
        self.assertIn('"I was only exploring the possibility"', f[0].text)


if __name__ == "__main__":
    unittest.main()
