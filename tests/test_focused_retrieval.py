"""A question about ONE exchange must be answered from that exchange.

The live question was: "What did Daniel and Rebecca agree should happen regarding a conversation with Rebecca's accountant? Identify who proposed speaking, who was supposed to participate, and what the
source actually establishes. Cite the original conversation turns."  The report came back as the client's whole Lakeshore history: source discovery searched for "daniel" and "says" and missed INT-070 turn 6;
the narrative pool dropped the advisor's own words because the plan had no topic; and the answer block switched its sale slots on because "proposed" stems to "propos".

These tests pin the four fixes on the REAL conversation data (read-only):
  C  retrieval: advisor names and generic reporting verbs are not search phrases, simple inflections are matched, a narrow subject reads a short meeting whole
  A  synthesis pool: with no plan topic the pool is the claims ABOUT the grounded subject, advisor and client alike, and nothing else
  B  answer block: "who proposed speaking" is not a sale question; it gets one code-written slot made of the turns themselves
plus attribution (who proposed, who answered, who participates, a promise is not a call that was held) and the negative safeguards (an unsupported claim about participants stays rejected)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_phase4a as P  # noqa: E402
import test_phase4a_followup as F  # noqa: E402
import test_tool_selector as TS  # noqa: E402
from agents.report import status as ST  # noqa: E402
from agents.report import synthesis as SY  # noqa: E402
from agents.report.agent import ReportAgent  # noqa: E402
from agents.report.answer import _TXN_ROOTS, assemble_answer  # noqa: E402
from agents.report.evidence import build_evidence  # noqa: E402
from agents.report.timeline import build_timeline  # noqa: E402
from agents.tool_selector import source_discovery as SD  # noqa: E402
from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from schemas.report import ClauseOut, Finding  # noqa: E402
from services.conversations import shared  # noqa: E402
from test_source_turn_evidence import plan as report_plan  # noqa: E402

R = "CLIENT-002"
Q = ("What did Daniel and Rebecca agree should happen regarding a conversation with Rebecca's accountant? Identify who proposed speaking, who was supposed to participate, and what the source actually "
     "establishes. Cite the original conversation turns.")
CONV = shared()
NAMES = {v["first_name"] for v in CONV.directory().values()}


def discover(question=Q):
    """The meetings and windows source discovery chooses for `question` on the real data, exactly as the Tool Selector computes them (search results -> enrich_meetings -> plan_meetings)."""
    from mcp_server.tools import SERVICES
    plan = SD.source_search_plan(question, R, CONV, NAMES)
    terms = [t for t, _ in plan]
    meetings = {}
    for t, w in plan:
        res = SERVICES.get_source_evidence(R, search_text=t)["data"]["search"]
        for m in res["matches"]:
            mt = meetings.setdefault(m["interaction_id"], {"date": m["date"], "turns": {}, "cue": False, "cue_turns": set()})
            mt["turns"][m["turn_index"]] = mt["turns"].get(m["turn_index"], 0) + w
    SD.enrich_meetings(meetings, CONV, R, terms, question)
    chosen, _ = SD.plan_meetings(meetings, 25)
    return terms, meetings, chosen


# ------------------------------------------------------------------------------------------------------------------------------------ C: retrieval
class SearchPhrases(unittest.TestCase):
    def test_the_accountant_question_searches_its_subject_and_its_relation_words_not_names_or_reporting_verbs(self):
        terms = SD.source_search_terms(Q, R, CONV, NAMES)
        self.assertIn("accountant", terms)
        self.assertIn("agree", terms)                                              # the relation word that finds INT-070 turn 6 ("I'd like both of you to agree before I sign")
        for noise in ("daniel", "says", "say", "actually", "establishes", "participate", "happen"):
            self.assertNotIn(noise, terms)

    def test_an_advisors_own_first_name_is_never_a_search_phrase(self):
        self.assertEqual(SD.advisor_first_names(CONV), {"daniel", "priya", "elena"})              # taken from "It's Daniel." at the start of meetings, three times or more
        self.assertNotIn("daniel", SD.source_search_terms("Daniel said something about the accountant", R, CONV, NAMES))

    def test_reporting_verbs_and_framing_words_are_stop_words(self):
        for w in ("says", "said", "actually", "mention", "unless", "cite", "original", "regarding", "happen", "supposed", "participate", "participants", "establishes"):
            self.assertIn(w, SD._STOP, w)
        for w in ("agree", "speak", "accountant", "propose"):                              # the relation words and the subject are still searchable
            self.assertNotIn(w, SD._STOP, w)


class Inflections(unittest.TestCase):
    def test_simple_inflections_only(self):
        self.assertTrue({"speak", "speaks", "speaking"} <= SD.inflections("speaking"))
        self.assertTrue({"agree", "agrees", "agreed", "agreeing"} <= SD.inflections("agreed"))
        self.assertTrue({"propose", "proposed", "proposing"} <= SD.inflections("proposed"))

    def test_no_fuzzy_or_irregular_matching(self):
        self.assertNotIn("spoke", SD.inflections("speak"))
        self.assertNotIn("said", SD.inflections("say"))
        self.assertNotIn("agreement", SD.inflections("agree"))                             # a different word, not an inflection
        self.assertEqual(SD.inflections("a b"), {"a b"})

    def test_a_request_word_that_the_client_never_uses_is_searched_in_the_form_she_does(self):
        # "speaking" occurs in no turn of this client; "speak" occurs in two (INT-070 turns 3 and 6): the search phrase is the form the data contains
        plan = dict(SD.source_search_plan("Who was speaking about the call?", R, CONV, NAMES))
        self.assertIn("speak", plan)
        self.assertNotIn("speaking", plan)

    def test_the_request_form_is_kept_when_the_data_contains_it(self):
        self.assertIn("agree", SD.source_search_terms("Who wanted to agree?", R, CONV, NAMES))


class RetrievalOfTheExchange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terms, cls.meetings, cls.chosen = discover()

    def test_int_070_turns_3_6_and_11_are_all_retrieved(self):
        window = self.meetings["INT-070"]["window"]
        for t in (3, 6, 11):
            self.assertIn(t, window, t)

    def test_a_narrow_subject_reads_a_short_meeting_whole_in_one_call(self):
        self.assertEqual(self.meetings["INT-070"]["window"], list(range(1, 13)))
        self.assertTrue(self.meetings["INT-070"].get("whole_meeting"))
        self.assertLessEqual(len(self.meetings["INT-070"]["window"]), SD.MAX_TURNS_PER_CALL)

    def test_a_meeting_longer_than_one_call_is_never_read_whole(self):
        long_ones = [i for i in self.chosen if len(CONV.meeting(i)) > SD.MAX_TURNS_PER_CALL]
        self.assertTrue(long_ones)
        for i in long_ones:
            self.assertFalse(self.meetings[i].get("whole_meeting"), i)
            self.assertLessEqual(len(self.meetings[i]["window"]), SD.MAX_TURNS_PER_CALL)

    def test_a_broad_subject_is_never_widened(self):
        terms, meetings, chosen = discover("Give me the chronology of the Lakeshore sale with the source conversation turns, including any corrections.")
        self.assertGreater(len(meetings), SD.MAX_FOCUS_MEETINGS)
        self.assertFalse([i for i, m in meetings.items() if m.get("whole_meeting")])

    def test_fewer_meetings_and_turns_than_the_unfocused_search_retrieved(self):
        self.assertLess(len(self.chosen), 16)                                                      # 16 meetings and 73 turns before: "daniel" and "says" matched unrelated meetings
        self.assertLess(sum(len(self.meetings[i]["window"]) for i in self.chosen), 73)

    def test_only_this_clients_meetings_are_considered(self):
        for i in self.chosen:
            self.assertTrue(all(r["client_id"] == R for r in CONV.meeting(i)), i)


# ------------------------------------------------------------------------------------------------------------------------------------ A / B / evidence and attribution, on the real retrieved turns
class FocusedReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "synthesis"}):
            cls.st, _, _ = TS.run([TS.finish()], p=TS.plan(clients=(R,), tools=("get_source_evidence",), task_type="history"), text=Q)
        cls.plan = report_plan((R,))
        cls.plan.memory_topics = []                                                              # the live plan: no topic
        cls.plan.task_type = "history"                                                            # the live plan: a history task ("conversation turns" asks for a chronology)
        cls.evidence, _ = build_evidence(cls.plan, {}, cls.st)
        cls.tl = build_timeline(cls.evidence, [])
        cls.byk = {ST.key_of(c): c for c in cls.tl if c.source == "source_turn"}
        cls.terms = list(cls.st.retrieval_report["terms"])
        cls.roots = ST.subject_roots(Q, [], ["Rebecca"], terms=cls.terms)
        cls.main = ST.grounded_roots(cls.terms[:1])

    # ---- the plan's own retrieval report
    def test_the_tool_selector_retrieved_the_whole_of_int_070(self):
        got = {r["interaction_id"]: r["turns"] for r in self.st.retrieval_report["retrieved"]}
        self.assertEqual(got["INT-070"], list(range(1, 13)))
        for k in ("INT-070#3", "INT-070#6", "INT-070#11"):
            self.assertIn(k, self.byk, k)

    # ---- A: the pool
    def test_the_three_relevant_turns_are_in_the_synthesis_pool_with_no_plan_topic(self):
        pool = {ST.key_of(c) for c in SY.synthesis_pool(self.tl, self.roots, self.main)}
        self.assertTrue({"INT-070#3", "INT-070#6", "INT-070#11"} <= pool, pool)
        old = {ST.key_of(c) for c in SY.synthesis_pool(self.tl)}
        self.assertFalse({"INT-070#3", "INT-070#11"} & old, "documents the defect: without roots both advisor lines were dropped")

    def test_an_unrelated_advisor_statement_is_still_excluded(self):
        pool = {ST.key_of(c) for c in SY.synthesis_pool(self.tl, self.roots, self.main)}
        unrelated = [k for k, c in self.byk.items() if c.statement_kind != "client_statement" and len(c.wording.split()) >= 10 and "accountant" not in c.wording.lower() and "agree" not in c.wording.lower()]
        self.assertTrue(unrelated)
        self.assertFalse(set(unrelated) & pool, set(unrelated) & pool)

    def test_a_relation_word_counts_only_near_a_line_about_the_subject(self):
        pool = {ST.key_of(c) for c in SY.synthesis_pool(self.tl, self.roots, self.main)}
        self.assertNotIn("INT-070#7", pool)                      # "Last time we agreed you would send over the signed exclusivity agreement": 'agree', four turns from any accountant line
        self.assertNotIn("INT-078#13", pool)                     # "I'd only propose changes if the mix drifts": 'propose' in another meeting

    def test_the_pool_is_much_narrower_than_the_clients_history(self):
        self.assertLess(len(SY.synthesis_pool(self.tl, self.roots, self.main)), len(SY.synthesis_pool(self.tl)))

    # ---- B: the answer block
    def test_who_proposed_speaking_does_not_activate_the_sale_slots(self):
        self.assertFalse(ST.subject_roots("Who proposed speaking?", [], []) & _TXN_ROOTS)
        block, _ = assemble_answer(Q, self.tl, {}, ["Rebecca"], [], terms=self.terms)
        self.assertEqual([s.key for s in block.slots], ["subject"])
        for gone in ("acceptance", "transaction", "decision", "corrections", "beginning"):
            self.assertIn(gone, block.not_applicable)

    def test_the_answer_slot_is_the_three_turns_in_order_with_their_speakers(self):
        block, _ = assemble_answer(Q, self.tl, {}, ["Rebecca"], [], terms=self.terms)
        items = block.slots[0].items
        shown = [(e.where, e.speaker.split(",")[0]) for it in items for e in it.evidence[:1] if "INT-070" in e.where]
        self.assertEqual([w for w, _ in shown], ["INT-070 turn 3", "INT-070 turn 6", "INT-070 turn 11"])
        label = {it.evidence[0].where: it.label for it in items if it.evidence}
        self.assertIn("Advisor", label["INT-070 turn 3"])
        self.assertIn("Client", label["INT-070 turn 6"])
        self.assertIn("Advisor", label["INT-070 turn 11"])
        text = {it.evidence[0].where: it.evidence[0].excerpt for it in items if it.evidence}
        self.assertIn("Your accountant and I should speak", text["INT-070 turn 3"])
        self.assertIn("I'd like both of you to agree before I sign", text["INT-070 turn 6"])
        self.assertIn("I'll arrange a call with your accountant", text["INT-070 turn 11"])

    def test_a_promised_call_is_not_presented_as_a_call_that_was_held(self):
        block, _ = assemble_answer(Q, self.tl, {}, ["Rebecca"], [], terms=self.terms)
        it = next(i for i in block.slots[0].items if i.evidence and i.evidence[0].where == "INT-070 turn 11")
        self.assertIn("WILL do", it.note)
        self.assertIn("no retrieved turn is cited here as showing that it was done", it.note)

    def test_a_later_record_is_labelled_as_not_shown_to_concern_the_same_exchange(self):
        block, _ = assemble_answer(Q, self.tl, {}, ["Rebecca"], [], terms=self.terms)
        later = [i for i in block.slots[0].items if i.label.startswith("Later record")]
        self.assertTrue(later)
        self.assertIn("not shown to concern the same exchange", later[0].label)
        self.assertTrue(any("I spoke with Mei Lin this morning" in e.excerpt for i in later for e in i.evidence))      # INT-078 turn 7: said, later, by the advisor himself

    def test_every_excerpt_is_an_exact_part_of_its_source_turn(self):
        block, _ = assemble_answer(Q, self.tl, {}, ["Rebecca"], [], terms=self.terms)
        for it in block.slots[0].items:
            for e in it.evidence:
                self.assertIn(e.excerpt, self.byk[e.key].wording, e.key)

    def test_a_genuine_sale_question_still_gets_the_sale_slots(self):
        sale = "Was the Lakeshore offer ever accepted? Who proposed the exclusivity agreement, and what does the source establish about signing?"
        block, _ = assemble_answer(sale, P.timeline(P.REBECCA), {}, ["Rebecca"], ["lakeshore_offer", "business_sale"])
        self.assertTrue({"acceptance", "transaction"} <= {s.key for s in block.slots})
        self.assertNotIn("subject", [s.key for s in block.slots])

    def test_a_non_sale_non_exchange_question_keeps_the_old_slots(self):
        block, _ = assemble_answer("What is the history of Kenji's retirement age?", P.timeline([("INT-027", 2, P.C, "2026-02-09", "I'm leaning toward 60 but not committing.")], ["retirement_age"]), {},
                                   ["Kenji"], ["retirement_age"])
        self.assertEqual([s.key for s in block.slots], ["beginning", "corrections", "decision"])

    # ---- the synthesis itself: the sentences a careful model writes, judged by the existing rules
    def judge(self, key, text):
        return {p.split(":")[0] for p in SY.proposition_problems(text, [self.byk[key]], ["Rebecca"])}

    def test_the_faithful_sentences_survive_synthesis_and_the_verifier(self):
        good = {"INT-070#3": "The advisor said that Rebecca's accountant and the advisor should speak before any agreement was final.",
                "INT-070#6": "Rebecca said that she would like both the advisor and Mei Lin to agree before she would sign.",
                "INT-070#11": "The advisor said he would arrange a call with Rebecca's accountant about the tax structure of the sale."}
        for k, t in good.items():
            self.assertEqual(self.judge(k, "On 9 April, " + t), set(), (k, t))
        lines = [ClauseOut(claim=self.byk[k].claim_id, sentence=t) for k, t in good.items()]
        findings, rejected = SY.build_lines(lines, self.tl, ["Rebecca"], window=[self.byk[k].claim_id for k in good])
        self.assertEqual(rejected, [])
        self.assertEqual(len(findings), 1)                                                         # one meeting, one finding, three sentences
        self.assertEqual([s.sources for s in findings[0].narrative], [["INT-070#3"], ["INT-070#6"], ["INT-070#11"]])
        self.assertEqual(SY.check_synthesis_finding(findings[0], self.tl, ["Rebecca"]), [])

    def test_daniels_proposal_is_attributed_to_daniel_and_rebeccas_reply_to_rebecca(self):
        self.assertIn("attribution_mismatch", self.judge("INT-070#3", "On 9 April, Rebecca said that Rebecca's accountant and the advisor should speak before any agreement was final."))
        self.assertIn("attribution_mismatch", self.judge("INT-070#6", "On 9 April, the advisor said that he would like both the advisor and Mei Lin to agree before she would sign."))

    def test_the_proposed_participants_are_not_swapped(self):
        for t in ("On 9 April, the advisor said that Rebecca and her accountant should speak before any agreement was final.",
                  "On 9 April, the advisor said that Rebecca's accountant and Rebecca should speak before any agreement was final."):
            self.assertIn("role_swap", self.judge("INT-070#3", t), t)
        self.assertIn("role_swap", self.judge("INT-070#6", "On 9 April, Rebecca said that she would like her accountant to agree before she would sign."))

    def test_a_future_promise_is_not_narrated_as_a_completed_call(self):
        for t in ("On 9 April, the advisor arranged a call with Rebecca's accountant about the tax structure of the sale.", "On 9 April, the advisor held a call with Rebecca's accountant."):
            self.assertTrue({"tense_shift", "unsupported_assertion"} & self.judge("INT-070#11", t), t)

    def test_an_unsupported_claim_about_who_participated_stays_rejected(self):
        for key, t in (("INT-070#3", "On 9 April, the advisor said that Rebecca and her accountant were the participants."),
                       ("INT-070#6", "On 9 April, Rebecca said that she and her accountant would speak before she signed."),
                       ("INT-070#11", "On 9 April, the advisor said that Rebecca and Mei Lin Chu had spoken.")):
            self.assertTrue(self.judge(key, t), t)

    def test_the_evidence_verifier_still_withholds_an_inference_about_participants(self):
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "synthesis"}):
            llm = MockLLM(handlers={"report": [{"summary": "The turns record a proposal to speak.", "key_findings": [], "historical_context": [], "financial_analysis": [], "uncertainties": [],
                                                "suggested_followups": [], "claims": []}, {"lines": []}, {"lines": []}]})
            rep = ReportAgent(llm).write(Q, self.plan, {}, self.st)
        ref = next(e.ref for e in rep.evidence if e.interaction_id == "INT-070" and e.turn_index == 3)
        forged = rep.model_copy(update={"key_findings": [Finding(text="Rebecca and her accountant were the participants in the conversation.", refs=[ref])]})
        with mock.patch("services.conversations.shared", lambda: CONV):
            final, res = EvidenceVerifier(CONV).verify_report(forged, [R], self.st, topics=[])
        self.assertEqual(final.key_findings, [])
        self.assertTrue([v for v in res.verdicts if v.verdict == "unsupported" and v.section == "key_findings"])

    def test_the_verifier_rederives_the_focused_answer_block(self):
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "synthesis"}):
            llm = MockLLM(handlers={"report": [{"summary": "The turns record a proposal to speak.", "key_findings": [], "historical_context": [], "financial_analysis": [], "uncertainties": [],
                                                "suggested_followups": [], "claims": []}] + [{"lines": []}] * 6})
            rep = ReportAgent(llm).write(Q, self.plan, {}, self.st)
        self.assertEqual([s.key for s in rep.answer.slots], ["subject"])
        with mock.patch("services.conversations.shared", lambda: CONV):
            final, _ = EvidenceVerifier(CONV).verify_report(rep, [R], self.st, topics=[])
        self.assertEqual(final.validation["harness_phase4a"], {"rederived": True, "differs_from_agent": []})
        self.assertEqual([s.key for s in final.answer.slots], ["subject"])

    def test_a_timed_out_overview_call_does_not_take_the_narrative_down_with_it(self):
        """Live: the first (overview) call timed out at the 300 s cap and the report fell back to the code-only version, although the narrative windows are separate calls."""
        from harness.budget import BudgetExceeded
        good = {"INT-070#3": "The advisor said that Rebecca's accountant and the advisor should speak before any agreement was final.",
                "INT-070#6": "Rebecca said that she would like both the advisor and Mei Lin to agree before she would sign.",
                "INT-070#11": "The advisor said he would arrange a call with Rebecca's accountant about the tax structure of the sale."}
        window = {"lines": [{"claim": self.byk[k].claim_id, "sentence": t} for k, t in good.items()]}
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "synthesis"}):
            llm = MockLLM(handlers={"report": [BudgetExceeded("model_call_timeout", "report exceeded 300s"), window, window, window]})
            rep = ReportAgent(llm).write(Q, self.plan, {}, self.st)
        self.assertEqual(rep.validation["mode"], "llm")
        self.assertTrue(rep.validation["main_call_timeout"])
        self.assertTrue(rep.summary.strip())
        self.assertTrue(any("overview call did not finish" in u for u in rep.uncertainties))
        used = {sn.sources[0] for f in rep.historical_context for sn in (f.narrative or [])}
        self.assertTrue({"INT-070#3", "INT-070#6", "INT-070#11"} <= used)

    def test_any_other_overview_failure_keeps_the_deterministic_report(self):
        from llm.client import LLMError
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "synthesis"}):
            llm = MockLLM(handlers={"report": [LLMError("connection refused")] * 3})
            rep = ReportAgent(llm).write(Q, self.plan, {}, self.st)
        self.assertEqual(rep.validation["mode"], "deterministic_model_failure")
        self.assertEqual(len(llm.calls), 0)

    def test_no_model_call_was_added(self):
        good = {"INT-070#3": "The advisor said that Rebecca's accountant and the advisor should speak before any agreement was final.",
                "INT-070#6": "Rebecca said that she would like both the advisor and Mei Lin to agree before she would sign.",
                "INT-070#11": "The advisor said he would arrange a call with Rebecca's accountant about the tax structure of the sale."}
        window = {"lines": [{"claim": self.byk[k].claim_id, "sentence": t} for k, t in good.items()]}
        main = {"summary": "s", "key_findings": [], "historical_context": [], "financial_analysis": [], "uncertainties": [], "suggested_followups": [], "claims": []}
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "synthesis"}):
            llm = MockLLM(handlers={"report": [main, window, window, window]})
            rep = ReportAgent(llm).write(Q, self.plan, {}, self.st)
        windows = SY.synthesis_windows(self.tl, roots=self.roots, main=self.main)
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(llm.calls), 2)                                                          # one main call and one window call: the focused pool, the answer block and the verifier add none
        self.assertTrue({"INT-070#3", "INT-070#6", "INT-070#11"} <= {s.sources[0] for f in rep.historical_context if f.window == 0 for s in (f.narrative or [])})
        self.assertEqual([s.key for s in rep.answer.slots], ["subject"])
        used = {k for it in rep.answer.slots[0].items for sn in it.sentences for k in sn.sources}
        self.assertTrue({"INT-070#3", "INT-070#6", "INT-070#11"} <= used)                            # the validated sentences are attached to the turns they rest on


if __name__ == "__main__":
    unittest.main()
