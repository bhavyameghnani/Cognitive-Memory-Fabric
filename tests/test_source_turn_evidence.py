"""Retrieved source conversation turns (get_source_evidence, conversation mode) as report evidence.

A source turn is a SYNTHETIC record of what a named speaker is recorded as saying.  It may support a narrow historical statement about the record; it is never a memory, so it cannot
support current / superseded / exploratory / recommendation claims, and operational data keeps its restriction.  Reads data/conversations.csv only; no database, no live model.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.report.agent import ReportAgent  # noqa: E402
from agents.report.assemble import SYNTHETIC_STATEMENT  # noqa: E402
from agents.report.claims import validate_claims  # noqa: E402
from agents.report.evidence import build_evidence, prompt_lines  # noqa: E402
from agents.report.prompts import REPORT_SYSTEM  # noqa: E402
from agents.report.validate import source_summary_problem  # noqa: E402
from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from schemas.plan import ExecutionPlan, Subtask  # noqa: E402
from schemas.report import AdvisorReport, Claim, EvidenceItem, Finding, is_source_turn  # noqa: E402
from schemas.tools import Envelope, Provenance, ToolExecution, ToolSelectorState  # noqa: E402
from services.conversations import shared  # noqa: E402

CONV = shared()
R, K = "CLIENT-002", "CLIENT-001"
TURNS = [("INT-052", 13), ("INT-058", 5), ("INT-058", 13)]
LIMIT = "Conversation text is synthetic; only the referenced turn is returned."


def plan(clients=(R,)):
    return ExecutionPlan(request_id="REQ-t", plan_id="P", request_text="Retrieve the source turns", client_ids=list(clients), user_intent="source turns", task_type="retrieval",
                         needs_history=False, needs_calculation=False,
                         subtasks=[Subtask(id="T1", description="fetch the turns", agent="tool_selector"), Subtask(id="T2", description="write the report", agent="report_agent", depends_on=["T1"])],
                         required_memory_categories=["episodic"], memory_topics=[], required_data=[], required_tools=["get_source_evidence"], dependencies={"T1": [], "T2": ["T1"]},
                         execution_order=["T1", "T2"], parallelizable_tasks=[], missing_information=[], success_criteria=["state what the turns record"])


def turn_exec(n, iid, idx, client=R):
    row = CONV.turn(iid, idx)
    env = Envelope(tool="get_source_evidence", client_id=client, as_of=row["interaction_datetime"][:10],
                   data={"turn": {"interaction_id": iid, "turn_index": idx, "speaker": row["speaker"], "text": row["utterance"]}},
                   provenance=Provenance(source="conversations.csv"), limitations=[LIMIT])
    return ToolExecution(id=f"EX-{n}", iteration=1, tool="get_source_evidence", arguments={"client_id": client, "interaction_id": iid, "turn_index": idx}, status="success", result=env)


def tool_exec(n, tool, data, client=R):
    env = Envelope(tool=tool, client_id=client, as_of="2026-06-30", data=data, provenance=Provenance(source="cmf_ops"), limitations=[])
    return ToolExecution(id=f"EX-{n}", iteration=1, tool=tool, arguments={"client_id": client}, status="success", result=env)


def state(*execs):
    st = ToolSelectorState(request_id="REQ-t")
    st.executions.extend(execs)
    return st


def three_turns():
    return state(*[turn_exec(i, iid, idx) for i, (iid, idx) in enumerate(TURNS, 1)])


def verify(report, st, clients=(R,)):
    return EvidenceVerifier(CONV).verify_report(report, list(clients), st)[1]


def report_with(evidence, **sections):
    return AdvisorReport(request_id="REQ-t", client_id=R, question="q", summary="s", evidence=evidence, **sections)


class Classification(unittest.TestCase):
    def test_conversation_mode_becomes_conversation_evidence(self):
        ev, _ = build_evidence(plan(), {}, three_turns())
        self.assertEqual([e.source_class for e in ev], ["conversation_evidence"] * 3)
        self.assertTrue(all(is_source_turn(e) for e in ev))

    def test_item_preserves_the_record_exactly(self):
        ev, _ = build_evidence(plan(), {}, three_turns())
        for e, (iid, idx) in zip(ev, TURNS):
            row = CONV.turn(iid, idx)
            self.assertEqual((e.interaction_id, e.turn_index, e.speaker, e.client_id, e.event_date), (iid, idx, row["speaker"], R, row["interaction_datetime"][:10]))
            self.assertEqual(e.excerpt, row["utterance"])                      # exact, untruncated
            self.assertTrue(e.synthetic)
            self.assertIn("conversations.csv", e.provenance)
            self.assertIn("synthetic", e.provenance)
            self.assertTrue(e.tool_execution_id.startswith("EX-"))
            self.assertIsNone(e.memory_id)
            self.assertIsNone(e.evidence_class)

    def test_speaker_is_taken_from_the_record_never_assumed_to_be_the_client(self):
        ev, _ = build_evidence(plan(), {}, state(turn_exec(1, "INT-052", 13)))
        self.assertEqual(ev[0].speaker, "Advisor")
        self.assertIn("recorded speaker Advisor", ev[0].description)

    def test_entity_mode_stays_operational_data(self):
        ent = tool_exec(1, "get_source_evidence", {"entity_type": "goal", "entity_id": "G1", "evidence": []})
        ev, _ = build_evidence(plan(), {}, state(ent))
        self.assertEqual([(e.source_class, is_source_turn(e)) for e in ev], [("operational_data", False)])

    def test_calculation_stays_tool_calculation_and_other_tools_operational(self):
        ev, _ = build_evidence(plan(), {}, state(tool_exec(1, "calculate_liquidity_ratio", {"cash_months": 13.2}), tool_exec(2, "get_client_profile", {"age": 60})))
        self.assertEqual([e.source_class for e in ev], ["tool_calculation", "operational_data"])

    def test_incomplete_turn_is_not_promoted(self):
        ex = turn_exec(1, "INT-052", 13)
        ex.result.data["turn"].pop("speaker")
        ev, _ = build_evidence(plan(), {}, state(ex))
        self.assertEqual(ev[0].source_class, "operational_data")               # nothing is filled in

    def test_cross_client_turn_is_removed_by_the_ownership_filter(self):
        ev, removed = build_evidence(plan(), {}, state(turn_exec(1, "INT-052", 13, client=K)))
        self.assertEqual(ev, [])
        self.assertTrue(removed["ownership_removed"])


class Verification(unittest.TestCase):
    def setUp(self):
        self.st = three_turns()
        self.ev, _ = build_evidence(plan(), {}, self.st)

    def finding(self, text, ref="E2", section="historical_context", origin=None):
        return report_with(self.ev, **{section: [Finding(text=text, refs=[ref], origin=origin)]})

    def test_attributed_model_statement_is_not_refused_by_the_source_rules(self):
        # The generic clause check (unchanged) still judges a model-written paraphrase and is deliberately strict ('Advisor' is a named person that is not in the turn text), so such a
        # statement may be withheld for THAT reason; what must not happen is a refusal by the source-record rules when speaker, interaction and turn are all named.
        text = 'In INT-058 turn 5 the recorded speaker Advisor says the note overstated it.'
        res = verify(self.finding(text), self.st)
        self.assertNotIn("SOURCE_NOT_PERMITTED", res.verdicts[0].reason_codes)

    def test_code_rendered_source_record_findings_verify(self):
        from agents.report.validate import source_record_findings
        fs = source_record_findings(self.ev)
        self.assertEqual(len(fs), 3)
        res = verify(report_with(self.ev, historical_context=fs), self.st)
        self.assertEqual([v.verdict for v in res.verdicts], ["verified"] * 3)
        self.assertIn("recorded speaker Advisor", fs[0].text)
        self.assertIn("does not establish that the statement is true", fs[0].text)

    def test_missing_attribution_or_location_is_refused(self):
        for text in ("The client said they are considering the offer.", "The Advisor said the note overstated it.", "In INT-058 turn 5 someone corrected the note."):
            res = verify(self.finding(text), self.st)
            self.assertEqual(res.verdicts[0].verdict, "unsupported", text)
            self.assertIn("SOURCE_NOT_PERMITTED", res.verdicts[0].reason_codes)

    def test_statement_cannot_turn_the_record_into_fact_decision_or_agreement(self):
        for text in ("In INT-058 turn 5 the Advisor confirmed that the client signed the Lakeshore letter.",
                     "In INT-058 turn 5 the Advisor said the client has decided to accept and agreed.",
                     "In INT-058 turn 5 the Advisor recorded the client's current position."):
            res = verify(self.finding(text), self.st)
            self.assertEqual(res.verdicts[0].verdict, "unsupported", text)
            self.assertIn("SOURCE_NOT_PERMITTED", res.verdicts[0].reason_codes)

    def test_negated_wording_is_not_treated_as_overreach(self):
        text = "In INT-058 turn 5 the recorded speaker Advisor says the offer has not been signed."
        self.assertEqual(verify(self.finding(text), self.st).verdicts[0].reason_codes.count("SOURCE_NOT_PERMITTED"), 0)

    def test_source_turn_alone_cannot_support_key_findings_or_analysis(self):
        text = "In INT-058 turn 5 the Advisor said the note overstated it."
        for sec in ("key_findings", "financial_analysis"):
            res = verify(self.finding(text, section=sec), self.st)
            self.assertEqual(res.verdicts[0].verdict, "unsupported", sec)
            self.assertIn("SOURCE_NOT_PERMITTED", res.verdicts[0].reason_codes)

    def test_tampered_excerpt_fails_source_verification(self):
        self.ev[1] = self.ev[1].model_copy(update={"excerpt": "I have corrected it: you signed the letter of intent."})
        res = verify(self.finding("In INT-058 turn 5 the recorded speaker Advisor says the note overstated it."), self.st)
        self.assertEqual(res.verdicts[0].verdict, "unsupported")
        self.assertIn("QUOTE_NOT_IN_SOURCE", res.verdicts[0].reason_codes)

    def test_cross_client_source_evidence_is_rejected(self):
        foreign = self.ev[1].model_copy(update={"client_id": K})
        rep = report_with([foreign], historical_context=[Finding(text="In INT-058 turn 5 the Advisor said the note overstated it.", refs=[foreign.ref])])
        res = verify(rep, self.st)
        self.assertEqual(res.verdicts[0].verdict, "unsupported")
        self.assertIn("CLIENT_MISMATCH", res.verdicts[0].reason_codes)

    def test_operational_only_historical_and_client_state_claims_still_fail(self):
        st = state(tool_exec(1, "get_client_profile", {"age": 60}))
        ev, _ = build_evidence(plan(), {}, st)
        for sec in ("historical_context", "key_findings"):
            rep = report_with(ev, **{sec: [Finding(text="The client was 60 earlier.", refs=["E1"])]})
            res = verify(rep, st)
            self.assertEqual(res.verdicts[0].verdict, "unsupported", sec)
            self.assertIn("SOURCE_NOT_PERMITTED", res.verdicts[0].reason_codes)

    def test_source_turn_cannot_support_any_memory_state_claim(self):
        for status in ("current", "superseded", "exploratory", "recommendation"):
            ok, rejected = validate_claims([Claim(subject="Rebecca", topic="business_sale", value=None, status=status, evidence_ref="E2", text="The offer is being considered.")],
                                           self.ev, plan())
            self.assertEqual(ok, [], status)
            self.assertIn("source conversation turn", rejected[0]["reason"])


class Wording(unittest.TestCase):
    def test_synthetic_statement_does_not_call_turns_the_clients_own_statements(self):
        self.assertNotIn("client's own statements", SYNTHETIC_STATEMENT)
        for part in ("synthetic", "recorded speaker", "not that the statement is true", "not original recordings"):
            self.assertIn(part, SYNTHETIC_STATEMENT)

    def test_prompt_keeps_classification_and_source_limitation(self):
        ev, _ = build_evidence(plan(), {}, three_turns())
        line = prompt_lines(ev)[0]
        self.assertTrue(line.startswith("[E1 conversation_evidence, synthetic]"), line)
        self.assertIn("recorded speaker Advisor", line)
        self.assertIn("not that it is true", line)
        for part in ("Source conversation turn", "naming its recorded speaker", "never as true, agreed, decided or signed", "historical_context"):
            self.assertIn(part, REPORT_SYSTEM)


class EndToEnd(unittest.TestCase):
    """Stub model + real evidence builder + real structured verifier, for the three requested turns."""

    def run_report(self, output):
        st = three_turns()
        llm = MockLLM(handlers={"report": [output]})
        rep = ReportAgent(llm).write("Retrieve INT-052 turn 13, INT-058 turn 5 and INT-058 turn 13", plan(), {}, st)
        new, res = EvidenceVerifier(CONV).verify_report(rep, [R], st)
        return new, res, llm

    def test_report_describes_what_the_synthetic_records_contain(self):
        out = {"summary": "The three requested source turns are retrieved from synthetic meeting records; all three are recorded as spoken by the Advisor.",
               "key_findings": [], "financial_analysis": [], "uncertainties": [], "suggested_followups": [], "claims": [],
               "historical_context": [{"text": "In INT-058 turn 5 the recorded speaker Advisor says the earlier note overstated it.", "refs": ["E2"]}]}
        new, res, llm = self.run_report(out)
        text = " ".join(f.text for f in new.historical_context)
        self.assertEqual(len(new.historical_context), 3)                       # the 3 code-rendered source records (the model's paraphrase is left to the strict clause check)
        for iid, idx in TURNS:
            self.assertIn(f"{iid} turn {idx}", text)
        self.assertEqual(text.count("recorded speaker Advisor"), 3)
        self.assertIn("synthetic source record", text.lower())
        self.assertIn("have not signed", text)                                 # the exact words of INT-058, quoted, not paraphrased into a fact
        asserted = re.sub(r"This shows what the turn records; it does not establish[^.]*\.", "", text)      # the code-written disclaimer names these words in order to deny them
        asserted = re.sub("“[^”]*”", "", asserted)                                           # and the quoted source words are the record itself
        self.assertNotRegex(asserted, r"(?i)\b(the client|rebecca) (has |had )?(signed|accepted|agreed|decided)")
        self.assertNotRegex(text, r"(?i)original recording|verified client conversation")
        self.assertEqual([v.verdict for v in res.verdicts], ["verified"] * 3 + ["unsupported"])      # the code-rendered records verify; the model paraphrase fails the unchanged clause check
        self.assertNotIn("SOURCE_NOT_PERMITTED", res.verdicts[-1].reason_codes)
        self.assertIn(SYNTHETIC_STATEMENT, new.assumptions)
        self.assertIn("get_source_evidence: " + LIMIT, new.limitations)       # the tool's exact limitation, unaltered, behind the tool-name prefix every limitation carries
        self.assertIn("conversation_evidence, synthetic", llm.calls[0]["user"])

    def test_overreaching_model_output_is_withheld_but_the_source_records_remain(self):
        out = {"summary": "Rebecca signed the letter.", "key_findings": [{"text": "Rebecca accepted the Lakeshore offer.", "refs": ["E1"]}], "financial_analysis": [],
               "uncertainties": [], "suggested_followups": [],
               "claims": [{"subject": "Rebecca", "topic": "business_sale", "value": None, "status": "current", "evidence_ref": "E1", "text": "Rebecca accepted the offer."}],
               "historical_context": [{"text": "In INT-058 turn 5 the Advisor confirmed the client signed the letter.", "refs": ["E2"]}]}
        new, res, _ = self.run_report(out)
        self.assertEqual(new.key_findings, [])
        self.assertEqual(new.validation["claims_accepted"], 0)
        texts = " ".join(f.text for f in new.historical_context)
        self.assertNotIn("confirmed the client signed", texts)
        self.assertEqual(texts.count("recorded speaker Advisor"), 3)
        self.assertTrue(any("Withheld" in u for u in new.uncertainties))

    def test_deterministic_fallback_still_states_the_records(self):
        rep = ReportAgent(MockLLM(handlers={"report": [LLMError("down")] * 3})).write("q", plan(), {}, three_turns())
        text = " ".join(f.text for f in rep.historical_context)
        self.assertEqual(text.count("recorded speaker Advisor"), 3)
        self.assertTrue(re.search(r"does not establish that the statement is true", text))


class SummaryAndLimitation(unittest.TestCase):
    """Follow-up: the summary is not claim-verified, so it gets the source-turn restriction directly; the tool's limitation reaches the final report."""

    UNSAFE = ["Rebecca has signed the Lakeshore letter.", "Rebecca decided to accept the offer.", "The Advisor's statement proves the client agreed.",
              "Rebecca's instruction to proceed is now current.", "The synthetic records show the client authorised the sale."]

    def report(self, summary):
        st = three_turns()
        ev, _ = build_evidence(plan(), {}, st)
        return AdvisorReport(request_id="REQ-t", client_id=R, question="q", summary=summary, evidence=ev), st

    def test_limitation_reaches_the_final_report_exactly_and_no_source_type_is_invented(self):
        st = three_turns()
        rep = ReportAgent(MockLLM(handlers={"report": [LLMError("down")] * 3})).write("q", plan(), {}, st)
        self.assertEqual(rep.limitations, ["get_source_evidence: " + LIMIT])
        for e in rep.evidence:
            self.assertFalse(hasattr(e, "source_type"))

    def test_unsafe_summaries_are_replaced_by_the_deterministic_source_summary(self):
        for text in self.UNSAFE:
            rep, st = self.report(text)
            new, res = EvidenceVerifier(CONV).verify_report(rep, [R], st)
            self.assertNotEqual(new.summary, text, text)
            self.assertIn("recorded speaker Advisor", new.summary)
            self.assertIn("synthetic source records", new.summary)
            self.assertIn("not that any statement is true", new.summary)
            self.assertIn("no decision, no agreement and no present client state", new.summary)
            self.assertNotIn("Other evidence", new.summary)
            self.assertIsNone(source_summary_problem(new.summary, [e for e in new.evidence if is_source_turn(e)]))
            self.assertIn("source turn", res.summary_replaced)
            self.assertNotRegex(new.summary, r"(?i)signed|decided|proves")

    def test_unsafe_summary_is_replaced_even_with_a_runtime_fallback_that_is_itself_unsafe(self):
        rep, st = self.report("Rebecca has signed the Lakeshore letter.")
        new, _ = EvidenceVerifier(CONV).verify_report(rep, [R], st, lambda: "Rebecca decided to accept.")
        self.assertIn("recorded speaker Advisor", new.summary)

    def test_runtime_fallback_is_source_aware(self):
        from agents.report.validate import deterministic_summary
        rep, st = self.report("Rebecca has signed the Lakeshore letter.")
        new, _ = EvidenceVerifier(CONV).verify_report(rep, [R], st, lambda: deterministic_summary(rep.evidence, plan()))
        self.assertIn("recorded speaker Advisor", new.summary)
        self.assertNotIn("Synthetic tool results", new.summary)

    def test_safe_summary_describing_the_records_is_kept(self):
        safe = ("Three synthetic source records were retrieved; each turn is recorded as spoken by the Advisor, and the summary says only what those turns record, "
                "with no decision and no agreement implied.")
        rep, st = self.report(safe)
        new, res = EvidenceVerifier(CONV).verify_report(rep, [R], st)
        self.assertEqual(new.summary, safe)
        self.assertIsNone(res.summary_replaced)

    def test_quoting_the_turn_itself_in_a_summary_is_not_overreach(self):
        q = shared().turn("INT-058", 13)["utterance"]
        rep, st = self.report(f"INT-058 turn 13, recorded speaker Advisor, contains: \"{q}\"")
        new, _ = EvidenceVerifier(CONV).verify_report(rep, [R], st)
        self.assertIn("contains:", new.summary)

    def test_summary_without_source_turns_is_untouched(self):
        st = state(tool_exec(1, "get_client_profile", {"retirement_age": 60}))
        ev, _ = build_evidence(plan(), {}, st)
        rep = AdvisorReport(request_id="REQ-t", client_id=R, question="q", summary="The current retirement age is 60.", evidence=ev)
        new, _ = EvidenceVerifier(CONV).verify_report(rep, [R], st)
        self.assertEqual(new.summary, "The current retirement age is 60.")


class NegationAndQuotation(unittest.TestCase):
    """A negation covers only the overreach expression in ITS OWN clause; only a genuine, whole-word quotation of the cited turn is exempt."""

    @classmethod
    def setUpClass(cls):
        ev, _ = build_evidence(plan(), {}, three_turns())
        cls.turn5 = [e for e in ev if e.interaction_id == "INT-058" and e.turn_index == 5]
        cls.head = "In INT-058 turn 5 the recorded speaker Advisor says "

    def problem(self, text):
        from agents.report.validate import source_record_problem
        return source_record_problem(self.head + text, self.turn5)

    def summary_problem(self, text):
        return source_summary_problem(text, self.turn5)

    def test_negation_in_an_earlier_clause_does_not_exempt_a_later_assertion(self):
        for text in ("Rebecca did not sign the letter, but the Advisor confirmed she signed it.",
                     "The records do not prove the offer was accepted; Rebecca accepted it."):
            self.assertIsNotNone(self.problem(text), text)
            self.assertIsNotNone(self.summary_problem(text), text)

    def test_negation_in_the_same_clause_still_exempts_the_negated_expression(self):
        for text in ("Rebecca has not signed the letter.", "The records do not establish that Rebecca agreed."):
            self.assertIsNone(self.problem(text), text)
            self.assertIsNone(self.summary_problem(text), text)

    def test_negation_does_not_cross_conjunctions_or_punctuation(self):
        for text in ("Rebecca never hesitated and she signed.", "Nothing was said about price: the client decided."):
            self.assertIsNotNone(self.summary_problem(text), text)

    def test_exact_quotation_from_the_cited_turn_is_exempt(self):
        q = "you are considering the offer and have not signed"
        self.assertIsNone(self.problem(f'"{q}".'))
        self.assertIsNone(self.summary_problem(f'INT-058 turn 5, Advisor: “{q}”'))

    def test_fabricated_quotation_with_an_overreach_term_is_not_exempt(self):
        for q in ("the client has signed the Lakeshore letter", "you are considering the offer and have signed"):
            self.assertIsNotNone(self.problem(f'"{q}".'), q)
            self.assertIsNotNone(self.summary_problem(f'"{q}"'), q)

    def test_short_quoted_fragment_cannot_hide_an_overreach_term(self):
        self.assertIsNotNone(self.problem('Rebecca "signed" the letter.'))        # "signed" does occur in the turn, but as a negated fragment of a longer sentence

    def test_quotation_that_drops_the_turns_negation_is_not_exempt(self):
        from agents.report.validate import _genuine_quote
        self.assertFalse(_genuine_quote("I'll correct it so it says you are considering the offer", ["I'll correct it so it says you are considering the offer and have not signed."]))
        self.assertTrue(_genuine_quote("you are considering the offer and have not signed", ["I'll correct it so it says you are considering the offer and have not signed."]))
        self.assertFalse(_genuine_quote("agreed to the offer today", ["Rebecca has not agreed to the offer today."]))

    def test_quote_followed_by_an_unsupported_assertion_is_rejected(self):
        text = 'The Advisor says "you are considering the offer and have not signed", and Rebecca confirmed she signed it.'
        self.assertIsNotNone(self.problem(text))
        self.assertIsNotNone(self.summary_problem(text))


class ScopeQuoteAndFallbackFixes(unittest.TestCase):
    """Review follow-up.  Each test goes through the public paths (verify_finding / verify_report via EvidenceVerifier) and names the old failure mode it discriminates against."""

    HEAD5 = "In INT-058 turn 5 the recorded speaker Advisor says "
    HEAD13 = "In INT-058 turn 13 the recorded speaker Advisor says "

    def setUp(self):
        self.st = three_turns()
        self.ev, _ = build_evidence(plan(), {}, self.st)                  # E1 INT-052/13, E2 INT-058/5, E3 INT-058/13

    def codes(self, text, ref="E2"):
        rep = report_with(self.ev, historical_context=[Finding(text=text, refs=[ref])])
        return verify(rep, self.st).verdicts[0].reason_codes

    def summary_after(self, text, fallback=None, ev=None, st=None):
        ev, st = ev or self.ev, st or self.st
        rep = AdvisorReport(request_id="REQ-t", client_id=R, question="q", summary=text, evidence=ev)
        return EvidenceVerifier(CONV).verify_report(rep, [R], st, fallback)

    # ---- 1. negation scope (old failure: any earlier negation in the clause exempted the term) -----------------------------------------------
    FLAGGED = ["No one doubts that Rebecca signed the letter.", "I do not doubt that Rebecca agreed to the offer.", "Rebecca is not hesitating about the offer she accepted.",
               "It is not true that Rebecca has not signed.", "Rebecca did not sign the letter, but the Advisor confirmed she signed it.",
               "The records do not prove the offer was accepted; Rebecca accepted it."]
    NEGATED = ["Rebecca has not signed the letter.", "The records do not establish that Rebecca agreed.", "The offer has not been signed."]

    def test_unclear_negation_scope_is_treated_as_overreach_in_findings_and_summaries(self):
        for text in self.FLAGGED:
            self.assertIn("SOURCE_NOT_PERMITTED", self.codes(self.HEAD5 + text), text)                     # verify_finding
            new, res = self.summary_after(text)                                                            # verify_report
            self.assertNotEqual(new.summary, text, text)
            self.assertTrue(res.summary_replaced, text)

    def test_explicit_negation_is_not_flagged_for_the_negation_alone(self):
        for text in self.NEGATED:
            self.assertNotIn("SOURCE_NOT_PERMITTED", self.codes(self.HEAD5 + text), text)
            new, res = self.summary_after(text)
            self.assertEqual(new.summary, text, text)                                                      # old: 'do not establish that Rebecca agreed' was replaced
            self.assertIsNone(res.summary_replaced)

    # ---- 2. quotations (old failures: trailing retraction, mismatched delimiters) -------------------------------------------------------------
    def test_trailing_retraction_quotation_is_not_exempt(self):
        q = "my earlier note that you had accepted the Lakeshore offer"                                     # the turn goes on: '... was wrong'
        self.assertIn("SOURCE_NOT_PERMITTED", self.codes(self.HEAD13 + f'"{q}".', "E3"))
        new, _ = self.summary_after(f'INT-058 turn 13, Advisor: "{q}"')
        self.assertNotIn(q, new.summary)

    def test_source_continuation_with_not_wrong_or_corrected_defeats_the_exemption(self):
        from agents.report.validate import _genuine_quote
        q = "you had accepted the Lakeshore offer"
        for cont in ("and I did not record it.", "and that was wrong.", "and I corrected it."):
            self.assertFalse(_genuine_quote(q, [f"I noted that {q}, {cont}"]), cont)
            self.assertFalse(_genuine_quote(q, [f"I noted that {q} {cont}"]), cont)
        self.assertTrue(_genuine_quote(q, [f"I noted that {q}."]))                                           # control: a clean full-clause quotation is exempt

    def test_genuine_full_clause_quotations_stay_exempt(self):
        for head, q, ref in ((self.HEAD5, "you are considering the offer and have not signed", "E2"),
                             (self.HEAD13, "my earlier note that you had accepted the Lakeshore offer was wrong", "E3")):
            for o, c in (('"', '"'), ("“", "”")):
                self.assertNotIn("SOURCE_NOT_PERMITTED", self.codes(f"{head}{o}{q}{c}.", ref), q)

    def test_mismatched_quotation_delimiters_are_not_a_quotation(self):
        q = "my earlier note that you had accepted the Lakeshore offer was wrong"                            # exempt when the delimiters match (above)
        for o, c in (('"', "”"), ("“", '"')):
            self.assertIn("SOURCE_NOT_PERMITTED", self.codes(f"{self.HEAD13}{o}{q}{c}.", "E3"), (o, c))

    # ---- 3. source_summary accuracy (old failure: 'N records retrieved' while listing six; pointer to historical context) -----------------------
    def eight(self):
        st = state(*[turn_exec(i, "INT-058", i) for i in range(1, 9)])
        ev, _ = build_evidence(plan(), {}, st)
        return ev, st

    def test_summary_of_more_than_six_turns_says_how_many_are_listed(self):
        from agents.report.validate import source_summary
        ev, st = self.eight()
        text = source_summary(ev)
        self.assertEqual(len([e for e in ev if is_source_turn(e)]), 8)
        self.assertIn("8 synthetic source records retrieved; first 6 of 8 listed", text)
        self.assertEqual(text.count("recorded speaker"), 6)
        self.assertNotIn("[E7]", text)
        self.assertNotIn("historical context", text)
        self.assertIn(LIMIT, text)
        self.assertIsNone(source_summary_problem(text, ev))
        new, _ = self.summary_after("Rebecca has signed the Lakeshore letter.", ev=ev, st=st)               # and through verify_report
        self.assertIn("first 6 of 8 listed", new.summary)

    def test_three_turn_summary_keeps_speaker_turn_limitation_and_disclaims_truth(self):
        new, _ = self.summary_after("Rebecca decided to accept the offer.")
        for part in ("3 synthetic source records retrieved:", "INT-052 turn 13 (recorded speaker Advisor)", "INT-058 turn 5 (recorded speaker Advisor)", "INT-058 turn 13 (recorded speaker Advisor)",
                     "not that any statement is true", "no decision, no agreement and no present client state", LIMIT):
            self.assertIn(part, new.summary)
        self.assertNotIn("first", new.summary)
        self.assertNotIn("historical context", new.summary)

    # ---- 4. mixed evidence (old failure: the fallback kept memory only and silently dropped the source turns, or the reverse) ------------------
    def mixed(self):
        st = state(*[turn_exec(i, iid, idx) for i, (iid, idx) in enumerate(TURNS, 1)], tool_exec(4, "calculate_funding_gap", {"gap": 1000}))
        ev, _ = build_evidence(plan(), {}, st)
        mem = EvidenceItem(ref="", source_class="memory", description="Current knowledge; retirement_age 60", memory_type="semantic", status="active", certainty="confirmed",
                           evidence_class="verified", topic="retirement_age", value="60", valid_from="2026-01-31", client_id=R, memory_id="M-1")
        items = ev + [mem]
        for i, it in enumerate(items, 1):
            it.ref = f"E{i}"
        return items, st

    def test_mixed_report_fallback_keeps_every_category_and_the_limitation(self):
        from agents.report.validate import deterministic_summary
        items, st = self.mixed()
        self.assertEqual({e.source_class for e in items}, {"conversation_evidence", "tool_calculation", "memory"})
        new, res = self.summary_after("Rebecca has signed the Lakeshore letter.", lambda: deterministic_summary(items, plan()), ev=items, st=st)
        self.assertTrue(res.summary_replaced)
        for part in ("retirement_age is 60", "Synthetic tool results are available", "recorded speaker Advisor", LIMIT, "no decision, no agreement and no present client state"):
            self.assertIn(part, new.summary)
        self.assertIsNone(source_summary_problem(new.summary, [e for e in items if is_source_turn(e)]))
        self.assertNotRegex(new.summary, r"(?i)\bsigned\b")

    def test_mixed_report_without_a_safe_composition_says_the_rest_is_not_summarised(self):
        items, st = self.mixed()
        for fb in (None, lambda: "Rebecca decided to accept."):
            new, _ = self.summary_after("Rebecca has signed the Lakeshore letter.", fb, ev=items, st=st)
            self.assertIn("Other evidence (memory or calculations) is not summarised here", new.summary)
            self.assertIn("recorded speaker Advisor", new.summary)
            self.assertIn(LIMIT, new.summary)

    def test_deterministic_summary_without_source_turns_is_unchanged(self):
        from agents.report.validate import deterministic_summary
        items, _ = self.mixed()
        plain = [e for e in items if not is_source_turn(e)]
        out = deterministic_summary(plain, plan())
        self.assertTrue(out.startswith("Based on the available memory: retirement_age is 60"))
        self.assertNotIn("Synthetic tool results", out)                      # memory-only, exactly as before the change


class QuotedFramingAndPointers(unittest.TestCase):
    """Final pre-commit fixes: a verbatim quotation of a QUESTION or a HEDGE does not license asserting it; fallback pointers name only what is guaranteed to exist (the evidence list)."""

    HEAD_Q = 'In INT-052 turn 3 the recorded speaker Advisor says '
    HEAD_H = 'In INT-058 turn 6 the recorded speaker Client says '
    Q = '"Have you signed anything"'                                  # real INT-052 turn 3 (Advisor): "That's a significant step. Have you signed anything?"
    H = '"If my accountant saw a note saying I\'d accepted"'          # real INT-058 turn 6 (Client): "Please. If my accountant saw a note saying I'd accepted, it would confuse things."

    def setUp(self):
        self.st = state(turn_exec(1, "INT-052", 3), turn_exec(2, "INT-058", 6), turn_exec(3, "INT-052", 13))
        self.ev, _ = build_evidence(plan(), {}, self.st)                  # E1 INT-052/3, E2 INT-058/6, E3 INT-052/13

    def codes(self, text, ref):
        return verify(report_with(self.ev, historical_context=[Finding(text=text, refs=[ref])]), self.st).verdicts[0].reason_codes

    def summary_after(self, text):
        rep = AdvisorReport(request_id="REQ-t", client_id=R, question="q", summary=text, evidence=self.ev)
        return EvidenceVerifier(CONV).verify_report(rep, [R], self.st)

    # ---- fix 2 through verify_finding (old failure: both quotations were exempt, so 'signed' / 'accepted' passed as quoted text) ----------------
    def test_verbatim_question_is_not_exempt_in_a_finding(self):
        self.assertIn("SOURCE_NOT_PERMITTED", self.codes(f"{self.HEAD_Q}{self.Q}.", "E1"))
        self.assertNotIn("SOURCE_NOT_PERMITTED", self.codes(f'{self.HEAD_Q}"That\'s a significant step".', "E1"))        # a real statement of the same turn is still fine

    def test_verbatim_hedge_is_not_exempt_in_a_finding(self):
        self.assertIn("SOURCE_NOT_PERMITTED", self.codes(f"{self.HEAD_H}{self.H}.", "E2"))

    # ---- fix 2 through verify_report (summary) ------------------------------------------------------------------------------------------------
    def test_summary_quoting_a_question_or_hedge_is_replaced(self):
        for text in (f"INT-052 turn 3, recorded speaker Advisor: {self.Q}", f"INT-058 turn 6, recorded speaker Client: {self.H}"):
            new, res = self.summary_after(text)
            self.assertNotEqual(new.summary, text, text)
            self.assertTrue(res.summary_replaced, text)
            self.assertIn("recorded speaker Advisor", new.summary)

    def test_summary_quoting_a_plain_statement_is_kept(self):
        text = "INT-052 turn 3, recorded speaker Advisor: \"That's a significant step\""
        new, res = self.summary_after(text)
        self.assertEqual(new.summary, text)
        self.assertIsNone(res.summary_replaced)

    def test_full_real_turn_quotation_with_unrelated_uncertainty_stays_exempt(self):
        # INT-052 turn 13 contains 'If I think a trade is warranted' and the words 'agreement' / 'instruction' in OTHER clauses: only the clause that holds the term is assessed
        full = CONV.turn("INT-052", 13)["utterance"]
        new, res = self.summary_after(f'INT-052 turn 13, recorded speaker Advisor: "{full}"')
        self.assertIn(full, new.summary)
        self.assertIsNone(res.summary_replaced)

    # ---- helper-level examples from the review ----------------------------------------------------------------------------------------------
    def test_hedge_and_question_examples_at_helper_level(self):
        from agents.report.validate import _genuine_quote as g
        hedge = ["I think you might have accepted the Lakeshore offer."]
        self.assertFalse(g("you might have accepted the Lakeshore offer", hedge))
        self.assertFalse(g("have accepted the Lakeshore offer", hedge))                                      # hedge dropped from the quote
        self.assertFalse(g("you accepted the Lakeshore offer", ["Have you accepted the Lakeshore offer?"]))
        self.assertFalse(g("Have you accepted the Lakeshore offer", ["Have you accepted the Lakeshore offer?"]))
        self.assertTrue(g("you accepted the Lakeshore offer", ["I noted that you accepted the Lakeshore offer."]))                              # control
        self.assertTrue(g("you accepted the Lakeshore offer", ["I think it is late, but you accepted the Lakeshore offer."]))                  # hedge in ANOTHER clause
        self.assertTrue(g("you accepted the Lakeshore offer", ["Is it late? You accepted the Lakeshore offer."]))                              # question in ANOTHER sentence
        self.assertTrue(g("you are considering the offer and have not signed", ["I'll correct it so it says you are considering the offer and have not signed."]))   # negation kept

    def test_month_may_is_not_a_hedge(self):
        from agents.report.validate import _genuine_quote as g
        self.assertTrue(g("on May 1 we covered the retirement decision", ["Hello. On May 1 we covered the retirement decision."]))

    # ---- fix 1 (old failure: 'see the findings' / 'see the financial analysis' pointers that are not guaranteed) ------------------------------
    def mixed(self):
        st = state(*[turn_exec(i, iid, idx) for i, (iid, idx) in enumerate(TURNS, 1)], tool_exec(4, "calculate_funding_gap", {"gap": 1000}))
        ev, _ = build_evidence(plan(), {}, st)
        mem = EvidenceItem(ref="", source_class="memory", description="Current knowledge; retirement_age 60", memory_type="semantic", status="active", certainty="confirmed",
                           evidence_class="verified", topic="retirement_age", value="60", valid_from="2026-01-31", client_id=R, memory_id="M-1")
        items = ev + [mem]
        for i, it in enumerate(items, 1):
            it.ref = f"E{i}"
        return items, st

    def test_no_fallback_callable_points_to_the_evidence_list_only(self):
        items, st = self.mixed()
        rep = AdvisorReport(request_id="REQ-t", client_id=R, question="q", summary="Rebecca has signed the Lakeshore letter.", evidence=items)
        new, _ = EvidenceVerifier(CONV).verify_report(rep, [R], st)                                          # no fallback callable
        self.assertIn("Other evidence (memory or calculations) is not summarised here; see the evidence list.", new.summary)
        self.assertNotIn("see the findings", new.summary)
        self.assertNotIn("financial analysis", new.summary)
        for part in ("3 synthetic source records retrieved:", "INT-052 turn 13 (recorded speaker Advisor)", LIMIT, "not that any statement is true"):
            self.assertIn(part, new.summary)

    def test_composed_fallback_does_not_promise_a_financial_analysis_finding(self):
        from agents.report.validate import deterministic_summary
        items, st = self.mixed()
        out = deterministic_summary(items, plan())
        self.assertIn("Synthetic tool results are available [E4]; see the evidence list", out)
        self.assertNotIn("financial analysis", out)
        self.assertNotIn("see the findings", out)
        plain = deterministic_summary([e for e in items if not is_source_turn(e)], plan())                   # without source turns the previous wording is unchanged
        self.assertTrue(plain.startswith("Based on the available memory: retirement_age is 60"))


if __name__ == "__main__":
    unittest.main()
