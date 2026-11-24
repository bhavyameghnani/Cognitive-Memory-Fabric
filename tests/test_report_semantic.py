"""B4 + follow-up: independently verified model-generated narrative.  Adversarial cases run against the REAL Kenji conversation turns (INT-001/049/090) and the real timeline builder; the scripted model
returns the narrative call's JSON, code and the EvidenceVerifier decide what survives.  Nothing here calls a live model."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_report_timeline as T  # noqa: E402
from agents.report.evidence import build_evidence  # noqa: E402
from agents.report.semantic import (build_checked, check_narrative_finding, check_sentence, client_names, closure_problems, compose, filter_summary, judge_sentence,  # noqa: E402
                                    narrative_user, NARRATIVE_SYSTEM, split_sentences, timeline_prompt)
from agents.report.timeline import build_timeline  # noqa: E402
from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from llm.client import LLMError  # noqa: E402
from schemas.memory import MemoryContext  # noqa: E402
from schemas.report import Finding, NarrativeLLMOutput, NarrativeSentence, ReportLLMOutput  # noqa: E402

Q = "Give me the chronology of Kenji's retirement age with the source conversation turns, including any corrections."
GOOD_C4 = 'On 2026-03-04 the client said "I was only exploring the possibility".'
GOOD_C5 = 'On 2026-03-04 the advisor noted "The wording of my file note was too strong".'
GOOD_C1 = 'On 2026-01-02 the advisor recorded "I\'ll record that as your current plan, not a fixed decision".'
GOOD_C9 = 'On 2026-05-01 the client said "Mariko and I talked it through over the weekend. We\'ve decided: 63".'
GOOD_PAIR = 'On 2026-03-04 the advisor recorded "I understood that you wanted to retire at 60", and the client replied "I was only exploring the possibility".'


def paras(*paragraphs):
    """paragraphs: each a list of (text, [claim ids])."""
    return NarrativeLLMOutput.model_validate({"paragraphs": [{"sentences": [{"text": t, "claims": c} for t, c in p]} for p in paragraphs]}).paragraphs


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from services.conversations import shared
        cls.conv = shared()
        t = T.Integration("build")
        T.Integration.conv = cls.conv
        cls.eps, cls.execs = t.build()
        pl = T.plan(task="history", text=Q)
        pl.memory_topics = [T.TOPIC]
        cls.plan = pl
        cls.mem = {T.K: MemoryContext(client_id=T.K, episodes=cls.eps)}
        cls.items, _ = build_evidence(pl, cls.mem, T.tstate(*cls.execs))
        cls.tl = build_timeline(cls.items, pl.memory_topics)
        cls.by_id = {c.claim_id: c for c in cls.tl}
        cls.names = client_names(Q)

    def cited(self, ids):
        return [self.by_id[i] for i in ids]

    def problems(self, text, ids):
        return judge_sentence(text, self.cited(ids), self.names)

    def codes(self, text, ids):
        return {p.split(":")[0] for p in self.problems(text, ids)}

    def build(self, *paragraphs):
        return build_checked(paras(*paragraphs), self.tl, self.names)

    def report(self, narrative_out, main_summary="The record shows how the retirement age was discussed."):
        """Run the Report Agent with a scripted main call and a scripted narrative call (a JSON dict, a string, an Exception, or a list of them)."""
        outs = narrative_out if isinstance(narrative_out, list) else [narrative_out]
        a, llm = T.agent_for([T.llm_out(summary=main_summary, key_findings=[])] + outs)
        rep = a.write(Q, self.plan, self.mem, T.tstate(*self.execs))
        return rep, llm

    def verify(self, rep, plan_clients=None, owns=None):
        v = EvidenceVerifier(self.conv, owns=owns)
        return v.verify_report(rep, plan_clients or [T.K], T.tstate(*self.execs))

    @staticmethod
    def nar(*sentences):
        return {"paragraphs": [{"sentences": [{"text": t, "claims": c} for t, c in sentences]}]}

    def verdict_of(self, f, plan_clients=None, owns=None):
        ev = {e.ref: e for e in self.rep0.evidence}
        return EvidenceVerifier(self.conv, owns=owns).verify_finding("historical_context", f, ev, set(plan_clients or [T.K]), T.tstate(*self.execs))


class Fixture(Base):
    def test_the_fixture_is_the_real_conversation(self):
        self.assertTrue({"C1", "C3", "C4", "C5", "C9"} <= set(self.by_id), sorted(self.by_id))
        self.assertEqual(self.by_id["C4"].statement_kind, "client_statement")
        self.assertEqual(self.by_id["C5"].statement_kind, "advisor_interpretation")
        self.assertEqual(self.by_id["C3"].temporal_status, "corrected")


class Accepts(Base):
    def test_quote_led_sentences_pass_every_rule(self):
        for text, ids in ((GOOD_C4, ["C4"]), (GOOD_C5, ["C5"]), (GOOD_C1, ["C1"]), (GOOD_C9, ["C9"]), (GOOD_PAIR, ["C3", "C4"])):
            self.assertEqual(self.problems(text, ids), [], text)

    def test_survivors_become_one_checked_finding_composed_by_code(self):
        f, rej = self.build([(GOOD_C4, ["C4"]), (GOOD_C5, ["C5"])])
        self.assertEqual(rej, [])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].origin, "narrative_checked")          # never "rendered": the verifier reads it as model-written prose
        self.assertIn("INT-049 turn 4, recorded speaker Client", f[0].text)
        self.assertIn("INT-049 turn 5, recorded speaker Advisor", f[0].text)
        self.assertEqual([s.sources for s in f[0].narrative], [["INT-049#4"], ["INT-049#5"]])
        self.assertEqual(set(f[0].refs), {self.by_id["C4"].evidence_refs[0], self.by_id["C5"].evidence_refs[0]})
        self.assertEqual(check_narrative_finding(f[0], self.tl, self.names), [])

    def test_a_model_cannot_supply_provenance_in_its_output(self):
        out = ReportLLMOutput.model_validate({"summary": "s", "key_findings": [{"text": "x", "refs": ["E1"], "origin": "narrative_checked", "narrative": [{"text": "y", "sources": ["INT-049#4"]}]}]})
        self.assertIsNone(out.key_findings[0].origin)
        self.assertIsNone(out.key_findings[0].narrative)


class FabricatedAndMissing(Base):
    def test_a_fabricated_claim_id_removes_the_sentence_without_a_crash(self):
        f, rej = self.build([(GOOD_C4, ["C99"])])
        self.assertEqual(f, [])
        self.assertIn("unknown_claim", rej[0]["problems"][0])

    def test_a_sentence_citing_no_claim_is_refused_by_the_schema(self):
        with self.assertRaises(Exception):
            paras([(GOOD_C4, [])])

    def test_an_empty_or_garbage_narrative_does_not_crash_the_builder(self):
        self.assertEqual(build_checked([], self.tl, self.names), ([], []))
        self.assertEqual(build_checked(None, self.tl, self.names), ([], []))

    def test_a_claim_resting_only_on_an_unretrieved_memory_is_refused(self):
        tl = [c.model_copy(update={"source": "memory"}) if c.claim_id == "C4" else c for c in self.tl]
        f, rej = build_checked(paras([(GOOD_C4, ["C4"])]), tl, self.names)
        self.assertEqual(f, [])
        self.assertIn("unverified_source", rej[0]["problems"][0])

    def test_a_corrected_claim_cited_without_its_correction_is_refused(self):
        self.assertIn("correction_omitted", self.codes('On 2026-03-04 the advisor recorded "I understood that you wanted to retire at 60".', ["C3"]))

    def test_a_disputed_claim_must_say_so(self):
        c = self.by_id["C9"].model_copy(update={"temporal_status": "disputed"})
        silent = check_sentence(GOOD_C9, [c], {}, ())
        self.assertIn("dispute_omitted", {p.split(":")[0] for p in silent})
        said = check_sentence('On 2026-05-01 the client said "We\'ve decided: 63", a statement in unresolved conflict.', [c], {}, ())
        self.assertNotIn("dispute_omitted", {p.split(":")[0] for p in said})


class WrongAttribution(Base):
    def test_an_advisor_account_presented_as_the_clients_words_is_refused(self):
        self.assertIn("attribution_mismatch", self.codes('On 2026-03-04 the client said "The wording of my file note was too strong".', ["C5"]))

    def test_a_client_statement_attributed_to_the_advisor_is_refused(self):
        self.assertIn("attribution_mismatch", self.codes('On 2026-03-04 the advisor noted "I was only exploring the possibility".', ["C4"]))

    def test_a_quotation_borrowed_from_the_other_speaker_is_refused_when_two_claims_are_cited(self):
        self.assertIn("attribution_mismatch", self.codes('On 2026-03-04 the client said "The wording of my file note was too strong".', ["C4", "C5"]))

    def test_swapped_quotations_in_a_sentence_naming_both_speakers_are_refused(self):
        s = 'On 2026-03-04 the advisor recorded "I was only exploring the possibility", and the client said "The wording of my file note was too strong".'
        self.assertIn("attribution_mismatch", self.codes(s, ["C4", "C5"]))
        ok = 'On 2026-03-04 the client said "I was only exploring the possibility", and the advisor noted "The wording of my file note was too strong".'
        self.assertNotIn("attribution_mismatch", self.codes(ok, ["C4", "C5"]))

    def test_a_wrong_client_name_is_not_an_allowed_word(self):
        self.assertIn("unrecorded_wording", self.codes('On 2026-03-04 Tanaka said "I was only exploring the possibility".', ["C4"]))


class WrongDateFigure(Base):
    def test_a_date_that_is_not_the_claims_date_is_refused(self):
        self.assertIn("unsupported_date", self.codes('On 2026-04-04 the client said "I was only exploring the possibility".', ["C4"]))

    def test_a_figure_outside_the_cited_wording_is_refused(self):
        self.assertIn("unsupported_figure", self.codes('On 2026-03-04 the client said "I was only exploring the possibility" at 61.', ["C4"]))

    def test_an_unverified_memorys_value_is_not_a_figure_the_narrative_may_state(self):
        s = 'On 2026-03-04 the client said "I was only exploring the possibility" at 61.'
        unverified = self.by_id["C4"].model_copy(update={"value": "61", "verification": "unverified"})
        verified = self.by_id["C4"].model_copy(update={"value": "61", "verification": "verified"})
        self.assertIn("unsupported_figure", {p.split(":")[0] for p in check_sentence(s, [unverified], {}, self.names)})
        self.assertNotIn("unsupported_figure", {p.split(":")[0] for p in check_sentence(s, [verified], {}, self.names)})

    def test_a_paraphrased_quotation_is_refused(self):
        self.assertIn("quote_not_verbatim", self.codes('On 2026-03-04 the client said "I had definitely not decided anything yet".', ["C4"]))


class TentativeAndRelations(Base):
    def test_a_tentative_statement_presented_as_confirmed_or_decided_is_refused(self):
        for w in ("confirmed", "decided", "agreed"):
            self.assertIn("decision_language", self.codes(f'On 2026-03-04 the client {w} "I was only exploring the possibility".', ["C4"]), w)

    def test_a_correction_nobody_recorded_is_refused(self):
        self.assertIn("relation_unsupported", self.codes('On 2026-05-01 the client corrected the plan with "We\'ve decided: 63".', ["C9"]))

    def test_supersession_nobody_recorded_is_refused(self):
        self.assertIn("supersession_unsupported", self.codes('On 2026-05-01 the client said "We\'ve decided: 63", replacing the earlier plan.', ["C9"]))

    def test_clarified_and_superseding_on_a_supported_value_is_refused_end_to_end(self):
        f, rej = self.build([('The retirement age was clarified to 63, superseding previous exploratory and corrected plans.', ["C9"])])
        self.assertEqual(f, [])
        self.assertTrue({"relation_unsupported", "supersession_unsupported", "no_quotation"} <= {p.split(":")[0] for p in rej[0]["problems"]})

    def test_replaced_is_allowed_only_for_a_claim_recorded_as_superseded_or_corrected(self):
        ok = self.codes('On 2026-03-04 the advisor recorded "I understood that you wanted to retire at 60", replaced when the client replied "I was only exploring the possibility".', ["C3", "C4"])
        self.assertNotIn("supersession_unsupported", ok)


class Causal(Base):
    def test_a_causal_explanation_is_refused(self):
        self.assertIn("causal_explanation", self.codes('On 2026-03-04 the advisor noted "The wording of my file note was too strong" because the client was exploring.', ["C5"]))

    def test_reasons_in_other_words_are_refused(self):
        for w in ("as a result of the conversation", "which led to the change", "in response to the question", "prompted by the discussion"):
            self.assertIn("causal_explanation", self.codes(f'On 2026-03-04 the advisor noted "The wording of my file note was too strong" {w}.', ["C5"]), w)

    def test_an_invented_intention_or_unrecorded_word_is_refused(self):
        self.assertIn("inner_state", self.codes('On 2026-05-01 the client hoped, saying "We\'ve decided: 63".', ["C9"]))
        self.assertIn("unrecorded_wording", self.codes('On 2026-03-04 the client reluctantly said "I was only exploring the possibility".', ["C4"]))

    def test_a_sentence_without_a_verbatim_quotation_is_refused(self):
        self.assertIn("no_quotation", self.codes("On 2026-03-04 the client said he was exploring the possibility.", ["C4"]))


class Time(Base):
    def test_outcome_and_continuity_words_are_refused(self):
        for w in ("finally", "ultimately", "originally", "still"):
            self.assertIn("temporal_unsupported", self.codes(f'On 2026-03-04 the client {w} said "I was only exploring the possibility".', ["C4"]), w)

    def test_ordering_words_need_two_distinct_claims(self):
        self.assertIn("temporal_unsupported", self.codes('Later the client said "I was only exploring the possibility".', ["C4"]))

    def test_a_narrative_that_runs_backwards_is_cut(self):
        f, rej = self.build([(GOOD_C9, ["C9"]), (GOOD_C4, ["C4"])])
        self.assertEqual(len(rej), 1)
        self.assertIn("chronology_reversed", rej[0]["problems"][0])
        self.assertIn("INT-090", f[0].text)
        self.assertNotIn("INT-049", f[0].text)


class QuoteStyle(Base):
    def test_single_quoted_verbatim_spans_are_redelimited_and_then_judged_like_any_other(self):
        from agents.report.semantic import normalise_quotes
        s = "On 2026-03-04 the client said 'I was only exploring the possibility'."
        self.assertEqual(normalise_quotes(s, self.cited(["C4"])), GOOD_C4)
        f, rej = self.build([(s, ["C4"])])
        self.assertEqual((len(f), rej), (1, []))

    def test_a_single_quoted_span_that_is_not_verbatim_is_not_repaired(self):
        from agents.report.semantic import normalise_quotes
        s = "On 2026-03-04 the client said 'I had definitely decided on sixty'."
        self.assertEqual(normalise_quotes(s, self.cited(["C4"])), s)
        f, rej = self.build([(s, ["C4"])])
        self.assertEqual(f, [])
        self.assertIn("no_quotation", rej[0]["problems"])

    def test_apostrophes_inside_a_quotation_are_kept(self):
        from agents.report.semantic import normalise_quotes
        s = "On 2026-03-04 the client said 'I said I was leaning that way, but I haven't made that decision'."
        self.assertIn("\"I said I was leaning that way, but I haven't made that decision\"", normalise_quotes(s, self.cited(["C4"])))


class Story(Base):
    """The readable story is model prose that code cannot verify; these tests pin what code DOES refuse: invented causes, intentions, decisions, outcomes, relations, speakers, numbers, dates and names."""

    def sp(self, text, ids):
        from agents.report.semantic import story_problems
        return {p.split(":")[0] for p in story_problems(text, self.cited(ids), self.names)}

    def story(self, *sentences):
        from agents.report.semantic import build_story
        from schemas.report import StoryLLMOutput
        return build_story(StoryLLMOutput.model_validate({"lines": [{"claims": c, "sentence": t} for t, c in sentences]}).lines, self.tl, self.names)

    def test_plain_paraphrase_of_what_was_said_is_accepted(self):
        self.assertEqual(self.sp("On 2026-03-04 Kenji said he had only been exploring the possibility and that 65 was the planning date.", ["C4"]), set())
        self.assertEqual(self.sp("On 2026-03-04 the advisor noted that Kenji might want to retire at 60, and Kenji replied that he had only been exploring the possibility.", ["C3", "C4"]), set())

    def test_a_decision_is_allowed_only_as_the_clients_own_reported_words(self):
        self.assertEqual(self.sp("On 2026-05-01 Kenji said he had decided on 63.", ["C9"]), set())
        self.assertEqual(self.sp("Kenji decided to retire at 63.", ["C9"]), set())                                    # the cited line IS the client's own decision-worded statement: faithful to it
        self.assertIn("decision_language", self.sp("On 2026-03-04 Kenji decided to retire at 65.", ["C4"]))   # a tentative line never becomes a decision
        self.assertIn("decision_language", self.sp("On 2026-05-01 Kenji said he had agreed to retire at 63.", ["C9"]))   # "agreed" is not in the line
        self.assertIn("decision_language", self.sp("On 2026-03-04 the advisor said Kenji had decided to retire at 60.", ["C3"]))
        self.assertIn("decision_language", self.sp("On 2026-03-04 Kenji said he had confirmed the plan.", ["C4"]))

    def test_invented_causes_intentions_and_outcomes_are_refused(self):
        self.assertIn("causal_explanation", self.sp("On 2026-03-04 Kenji said he was only exploring the possibility because the market had fallen.", ["C4"]))
        self.assertIn("inner_state", self.sp("On 2026-03-04 Kenji hoped to retire early.", ["C4"]))
        self.assertIn("temporal_unsupported", self.sp("On 2026-03-04 Kenji finally said he was only exploring the possibility.", ["C4"]))
        self.assertIn("present_state_unsupported", self.sp("Kenji now plans to retire at 65.", ["C4"]))

    def test_the_wrong_speaker_is_refused(self):
        self.assertIn("attribution_mismatch", self.sp("On 2026-03-04 Kenji said the wording of the file note was too strong.", ["C5"]))
        self.assertIn("attribution_mismatch", self.sp("On 2026-03-04 the advisor said he was only exploring the possibility.", ["C4"]))

    def test_invented_figures_number_words_names_and_months_are_refused(self):
        self.assertIn("unsupported_figure", self.sp("On 2026-03-04 Kenji said he was only exploring 61.", ["C4"]))
        self.assertIn("unsupported_figure", self.sp("On 2026-03-04 Kenji said the plan was worth twenty million.", ["C4"]))
        self.assertIn("unsupported_name", self.sp("On 2026-03-04 Kenji said he had spoken to Daniel about it.", ["C4"]))
        self.assertIn("unsupported_name", self.sp("On 2026-03-04 Kenji said Rebecca had spoken about the possibility.", ["C4"]))      # another client's name is never in the cited wording
        self.assertNotIn("unsupported_name", self.sp("On 2026-05-01 Kenji said he and Mariko had decided on 63.", ["C9"]))             # a name that IS in the line is fine
        self.assertIn("unsupported_date", self.sp("In June Kenji said he was only exploring the possibility.", ["C4"]))
        self.assertNotIn("unsupported_date", self.sp("In March Kenji said he was only exploring the possibility.", ["C4"]))

    def test_possessive_names_and_whole_number_phrases_are_compared_correctly(self):
        from agents.report.semantic import story_problems
        c = self.by_id["C4"].model_copy(update={"wording": "Do I need to time the sale around Ethan's applications? My valuation range is nineteen to twenty-three million."})
        codes = lambda s: {p.split(":")[0] for p in story_problems(s, [c], self.names)}
        self.assertEqual(codes("On 2026-03-04 the client asked whether to time the sale around Ethan's applications."), set())             # "Ethan's" is in the line
        self.assertEqual(codes("On 2026-03-04 the client gave a valuation range of nineteen to twenty-three million."), set())
        self.assertIn("unsupported_figure", codes("On 2026-03-04 the client gave a valuation range of twenty to twenty-four million."))      # "twenty" alone is in the line, "twenty four million" is not
        self.assertIn("unsupported_name", codes("On 2026-03-04 the client asked about David's applications."))
        self.assertIn("inner_state", codes("On 2026-03-04 the client expressed a desire to sell."))

    def test_corrections_and_supersession_need_a_recorded_relation(self):
        self.assertIn("relation_unsupported", self.sp("On 2026-05-01 Kenji corrected his earlier statement.", ["C9"]))
        self.assertIn("supersession_unsupported", self.sp("On 2026-05-01 Kenji said this replaced the earlier plan.", ["C9"]))
        self.assertIn("correction_omitted", self.sp("On 2026-03-04 the advisor noted that Kenji might want to retire at 60.", ["C3"]))

    def test_the_story_keeps_passing_sentences_adds_code_written_handles_and_carries_no_provenance(self):
        f, rej = self.story(("On 2026-03-04 Kenji said he had only been exploring the possibility.", ["C4"]), ("Kenji decided to retire at 63 because of the weather.", ["C9"]),
                            ("On 2026-05-01 Kenji said he had decided on 63.", ["C9"]))
        self.assertEqual(len(f), 2)                                                                                  # one paragraph per meeting
        self.assertEqual(len(rej), 1)
        self.assertIn("causal_explanation", rej[0]["problems"])
        self.assertTrue(all(x.origin is None and x.narrative is None for x in f))
        self.assertRegex(f[0].text, r"exploring the possibility\. \[E\d+\]$")
        self.assertRegex(f[1].text, r"decided on 63\. \[E\d+\]$")
        self.assertNotIn("weather", repr(rej))                                                                       # the rejection record never carries the rejected text
        f2, rej2 = self.story(("On 2026-03-04 Kenji said he was only exploring. Kenji then said he would retire at 61.", ["C4"]))
        self.assertEqual((f2, rej2[0]["problems"]), ([], ["not_one_sentence"]))                                      # a block of sentences cannot be traced to its claims

    def test_unknown_claims_memory_only_claims_and_reversed_order_are_refused_without_a_crash(self):
        from agents.report.semantic import build_story
        from schemas.report import StoryLLMOutput
        self.assertEqual(self.story(("On 2026-03-04 Kenji said he was only exploring the possibility.", ["C99"]))[0], [])
        tl = [c.model_copy(update={"source": "memory"}) if c.claim_id == "C4" else c for c in self.tl]
        f, rej = build_story(StoryLLMOutput.model_validate({"lines": [{"claims": ["C4"], "sentence": "On 2026-03-04 Kenji said he was only exploring the possibility."}]}).lines, tl, self.names)
        self.assertEqual(f, [])
        self.assertIn("unverified_source", rej[0]["problems"])
        f, rej = self.story(("On 2026-05-01 Kenji said he had decided on 63.", ["C9"]), ("On 2026-03-04 Kenji said he had only been exploring the possibility.", ["C4"]))
        self.assertEqual(len(f), 1)
        self.assertIn("chronology_reversed", rej[0]["problems"])
        self.assertEqual(build_story(None, self.tl, self.names), ([], []))

    def test_the_agent_returns_the_story_in_its_own_field_and_survives_a_failing_story_call(self):
        good = {"lines": [{"claims": ["C4"], "sentence": "On 2026-03-04 Kenji said he had only been exploring the possibility."}]}
        a, llm = T.agent_for([T.llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[]), self.nar((GOOD_C4, ["C4"])), good])
        rep = a.write(Q, self.plan, self.mem, T.tstate(*self.execs))
        self.assertEqual([c["agent"] for c in llm.calls], ["report", "report", "report"])
        self.assertEqual(len(rep.story), 1)
        self.assertEqual(rep.validation["story"]["paragraphs_accepted"], 1)
        self.assertFalse([f for f in rep.historical_context if "exploring the possibility. [E" in f.text])         # not mixed into the verified sections
        a, _ = T.agent_for([T.llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[]), self.nar((GOOD_C4, ["C4"])), RuntimeError("down")])
        rep = a.write(Q, self.plan, self.mem, T.tstate(*self.execs))
        self.assertEqual(rep.story, [])
        self.assertTrue(rep.validation["story"].get("error"))
        self.assertTrue([f for f in rep.historical_context if f.origin == "narrative_checked"])                    # the verified narrative is unaffected

    def test_the_verifier_does_not_check_or_alter_the_story(self):
        good = {"lines": [{"claims": ["C4"], "sentence": "On 2026-03-04 Kenji said he had only been exploring the possibility."}]}
        a, _ = T.agent_for([T.llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[]), self.nar((GOOD_C4, ["C4"])), good])
        rep = a.write(Q, self.plan, self.mem, T.tstate(*self.execs))
        final, res = self.verify(rep)
        self.assertEqual([f.text for f in final.story], [f.text for f in rep.story])
        self.assertNotIn(rep.story[0].text, [v.text for v in res.verdicts])                                        # it was never a finding the verifier judged


class ModelFormatting(Base):
    def test_a_claim_id_the_model_copied_into_its_sentence_is_removed_but_never_a_foreign_one(self):
        from agents.report.semantic import strip_claim_ids
        self.assertEqual(strip_claim_ids('On 2026-03-04 the client said "I was only exploring the possibility", C4.', ["C4"]), 'On 2026-03-04 the client said "I was only exploring the possibility".')
        self.assertEqual(strip_claim_ids('On 2026-03-04 the client said "x yz" [C4].', ["C4"]), 'On 2026-03-04 the client said "x yz".')
        self.assertIn("C9", strip_claim_ids("the client said C9.", ["C4"]))
        f, rej = self.build([('On 2026-03-04 the client said "I was only exploring the possibility", C4.', ["C4"])])
        self.assertEqual((len(f), rej), (1, []))
        self.assertNotIn("C4", f[0].text.replace("INT-049", ""))
        f, rej = self.build([('On 2026-03-04 the client said "I was only exploring the possibility", C9.', ["C4"])])
        self.assertEqual(f, [])                                                      # an id of ANOTHER claim is not metadata of this sentence: it stays, and the sentence fails


class HistoryQuestionsAskForTheConversationRecord(unittest.TestCase):
    def test_plain_history_questions_are_treated_as_chronology_requests(self):
        from agents.planner.agent import is_source_chronology as f
        for q in ("What is the history of Rebecca's decision about the Lakeshore company sale?", "How have Kenji's retirement preferences changed over time?",
                  "What has Sofia said tentatively or left unresolved in our conversations?", "What did Kenji actually say about retiring at 60?",
                  "Prepare a comprehensive report on Rebecca's Lakeshore company sale planning history. Explain how her preferences and plans have evolved across meetings, identify important decisions and corrections, distinguish her own statements from advisor interpretations, and highlight unresolved questions.",
                  "Prepare a comprehensive report on Kenji's planning history across all meetings."):
            self.assertTrue(f(q), q)
        for q in ("What is Kenji's retirement age?", "Show the portfolio allocation", "How does the market change?", "Calculate the funding gap for the retirement goal",
                  "Summarise Kenji's asset allocation history", "How should I prepare for Kenji's next portfolio review?", "What did Rebecca say about selling the company?"):
            self.assertFalse(f(q), q)


class RelationTargetsNotRetrieved(Base):
    def test_a_related_claim_whose_turn_was_not_retrieved_is_named_without_meeting_or_turn_numbers(self):
        """B5 defect: a chronology paragraph said "corrects the record in INT-027 turn 8" for a stored record whose turn was never retrieved; the verifier rightly found 8 in no cited evidence."""
        from agents.report.narrative import narrate
        tl = [c.model_copy(update={"source": "memory"}) if c.claim_id == "C4" else c for c in self.tl]
        text = " ".join(f.text for f in narrate(tl, self.items))
        self.assertIn("later corrected in a stored record whose source turn was not retrieved", text)
        self.assertNotIn("later corrected in INT-049 turn 4", text)
        full = " ".join(f.text for f in narrate(self.tl, self.items))
        self.assertIn("later corrected in INT-049 turn 4", full)            # a retrieved target is still named, and cited


class CrossMeeting(Base):
    def test_a_sentence_may_not_join_claims_of_different_meetings(self):
        s = 'On 2026-03-04 the client said "I was only exploring the possibility", and the advisor noted "Good. So the working assumption is retirement at 65".'
        self.assertIn("cross_meeting", self.codes(s, ["C4", "C1"]))
        f, rej = self.build([(s, ["C4", "C1"])])
        self.assertEqual(f, [])
        self.assertIn("cross_meeting", rej[0]["problems"])


class RejectionRecordsNoText(Base):
    def test_the_rejection_record_never_carries_the_rejected_sentence(self):
        _, rej = self.build([('The retirement age was clarified to 63, superseding previous plans.', ["C9"])])
        self.assertNotIn("clarified", repr(rej))
        self.assertNotIn("superseding previous", repr(rej))

    def test_sentences_split_around_quoted_full_stops(self):
        self.assertEqual(len(split_sentences('The client said "That is it. Done." [C4]. Next sentence [C5].')), 2)

    def test_closure_allows_function_words_names_and_cited_wording_only(self):
        self.assertEqual(closure_problems('On 2026-03-04 Kenji said "x".', self.cited(["C4"]), self.names), [])
        self.assertTrue(closure_problems("On 2026-03-04 Kenji reluctantly agreed.", self.cited(["C4"]), self.names))


class PromptBlock(Base):
    def test_the_block_is_compact_dated_and_carries_status_and_relations(self):
        block, shown, left = timeline_prompt(self.tl)
        self.assertEqual(left, 0)
        self.assertIn("corrected by C4", block)
        self.assertIn("the advisor's account, not a client statement", block)
        dates = [c.event_date for c in shown]
        self.assertEqual(dates, sorted(dates))

    def test_when_too_many_claims_the_chronology_critical_ones_are_kept(self):
        _, shown, left = timeline_prompt(self.tl, max_claims=3)
        ids = {c.claim_id for c in shown}
        self.assertTrue({"C4", "C9"} <= ids, ids)             # the correction and the current decision outrank the opening working assumption; one best claim per meeting comes first
        self.assertGreater(left, 0)

    def test_every_meeting_of_a_long_history_is_represented_before_any_meeting_gets_a_second_claim(self):
        _, shown, _ = timeline_prompt(self.tl, max_claims=3)
        self.assertEqual(len({c.interaction_id for c in shown}), 3)

    def test_the_narrative_call_shows_only_the_timeline_and_the_rules(self):
        block, _, _ = timeline_prompt(self.tl)
        user = narrative_user(Q, block)
        self.assertIn("[C4]", user)
        self.assertNotIn("EVIDENCE:", user)
        self.assertIn("claims", NARRATIVE_SYSTEM)
        self.assertIn("Never give a reason", NARRATIVE_SYSTEM)

    def test_the_summary_filter_removes_unsupported_relations_and_keeps_descriptions(self):
        kept, why = filter_summary("Kenji's retirement age has been clarified to 63, superseding previous exploratory and corrected plans.", self.tl, self.names, {"63"})
        self.assertEqual(kept, "")
        self.assertTrue(why)
        kept, why = filter_summary("The record shows how the retirement age was discussed across four meetings.", self.tl, self.names, {"63"})
        self.assertTrue(kept and not why)


class AgentLevel(Base):
    def test_a_good_narrative_is_accepted_with_counts_and_the_deterministic_chronology_is_unchanged(self):
        rep, llm = self.report(self.nar((GOOD_C1, ["C1"]), (GOOD_PAIR, ["C3", "C4"]), (GOOD_C9, ["C9"])))
        rec = rep.validation["narrative"]
        self.assertEqual((rec["paragraphs_returned"], rec["sentences_returned"], rec["paragraphs_accepted"], rec["sentences_rejected"]), (1, 3, 1, []))
        self.assertEqual([c["agent"] for c in llm.calls], ["report", "report"])
        checked = [f for f in rep.historical_context if f.origin == "narrative_checked"]
        self.assertEqual(len(checked), 1)
        plain, _ = self.report(RuntimeError("no narrative"))
        det = lambda r: [f.text for f in r.historical_context if f.origin == "rendered"]
        self.assertEqual(det(rep), det(plain))                        # B3's chronology is untouched by the narrative
        self.assertEqual([c.model_dump() for c in rep.timeline], [c.model_dump() for c in plain.timeline])

    def test_bad_sentences_are_removed_and_good_ones_survive(self):
        rep, _ = self.report(self.nar((GOOD_C4, ["C4"]), ("The retirement age was clarified to 63, superseding previous plans.", ["C9"])))
        rec = rep.validation["narrative"]
        self.assertEqual(rec["paragraphs_accepted"], 1)
        self.assertEqual(len(rec["sentences_rejected"]), 1)
        text = " ".join(f.text for f in rep.historical_context)
        self.assertNotIn("superseding", text)
        self.assertIn("INT-049 turn 4", text)

    def test_the_narrative_call_failing_leaves_the_b3_report(self):
        for bad in (LLMError("down"), RuntimeError("boom"), "not json at all", json.dumps({"paragraphs": []})):
            rep, _ = self.report([bad, bad])
            self.assertEqual(rep.validation["mode"], "llm")
            self.assertTrue(rep.validation["narrative"].get("error"), repr(bad))
            self.assertFalse([f for f in rep.historical_context if f.origin == "narrative_checked"])
            self.assertTrue([f for f in rep.historical_context if f.origin == "rendered"])      # the deterministic chronology stands

    def test_a_narrative_whose_every_sentence_is_rejected_leaves_the_b3_report_and_does_not_crash(self):
        rep, _ = self.report(self.nar(("It was agreed because of the weather.", ["C4"]), (GOOD_C4, ["C99"])))
        self.assertEqual(rep.validation["narrative"]["paragraphs_accepted"], 0)
        self.assertEqual(len(rep.validation["narrative"]["sentences_rejected"]), 2)
        self.assertFalse([f for f in rep.historical_context if f.origin == "narrative_checked"])

    def test_a_non_history_report_makes_one_call_only(self):
        pl = T.plan(task="retrieval", text=Q)
        a, llm = T.agent_for(T.llm_out(summary="The record shows the position.", key_findings=[]))
        rep = a.write(Q, pl, self.mem, T.tstate(*self.execs))
        self.assertFalse(rep.validation["narrative"]["attempted"])
        self.assertEqual(len(llm.calls), 1)

    def test_a_summary_with_an_unsupported_relation_loses_that_sentence_and_is_recorded(self):
        rep, _ = self.report(RuntimeError("x"), main_summary="Kenji's retirement age has been clarified to 63, superseding previous exploratory and corrected plans.")
        self.assertNotIn("superseding", rep.summary)
        self.assertIn("semantic validation removed summary sentence(s)", rep.validation["summary_replaced"])


class VerifierIsIndependent(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rep0, _ = cls.report(cls, cls.nar((GOOD_C1, ["C1"]), (GOOD_PAIR, ["C3", "C4"]), (GOOD_C9, ["C9"])))
        cls.good = next(f for f in cls.rep0.historical_context if f.origin == "narrative_checked")

    def test_the_untampered_narrative_is_verified_by_the_verifier(self):
        v = self.verdict_of(self.good)
        self.assertEqual((v.verdict, v.reasons), ("verified", []))

    def test_the_final_report_keeps_the_narrative_and_b3_side_by_side(self):
        final, res = self.verify(self.rep0)
        self.assertTrue([f for f in final.historical_context if f.origin == "narrative_checked"])
        self.assertTrue([f for f in final.historical_context if f.origin == "rendered"])
        self.assertFalse([v for v in res.verdicts if v.text == self.good.text and v.verdict != "verified"])

    def test_a_forged_source_turn_is_not_found(self):
        f = self.good.model_copy(update={"narrative": [NarrativeSentence(text=s.text, sources=["INT-049#99"]) for s in self.good.narrative]})
        v = self.verdict_of(f)
        self.assertEqual(v.verdict, "unsupported")
        self.assertIn("EVIDENCE_NOT_FOUND", set(v.reason_codes))

    def test_a_sentence_edited_after_composition_is_refused(self):
        for new in ('On 2026-03-04 the client said "I was only exploring the possibility" because of the market.', GOOD_C9.replace("63", "61")):
            sents = [NarrativeSentence(text=new if i == 1 else s.text, sources=s.sources) for i, s in enumerate(self.good.narrative)]
            self.assertEqual(self.verdict_of(self.good.model_copy(update={"narrative": sents})).verdict, "unsupported", new)

    def test_text_that_does_not_compose_from_the_sentences_is_refused(self):
        f = self.good.model_copy(update={"text": self.good.text + " The client was certain."})
        self.assertEqual(self.verdict_of(f).verdict, "unsupported")

    def test_wrong_speaker_date_or_figure_smuggled_past_the_agent_is_caught_by_the_verifier(self):
        for wrong, cid in (('On 2026-03-04 the client said "The wording of my file note was too strong".', "C5"), ('On 2026-04-04 the client said "I was only exploring the possibility".', "C4"),
                           ('On 2026-03-04 the client said "I was only exploring the possibility" at 61.', "C4"), ('On 2026-03-04 the client confirmed "I was only exploring the possibility".', "C4")):
            c = self.by_id[cid]
            f = Finding(text=compose(wrong, [c]), refs=[c.evidence_refs[0]], origin="narrative_checked", narrative=[NarrativeSentence(text=wrong, sources=[f"{c.interaction_id}#{c.turn_index}"])])
            self.assertEqual(self.verdict_of(f).verdict, "unsupported", wrong)

    def test_a_narrative_in_the_wrong_order_is_refused_by_the_verifier(self):
        c9, c4 = self.by_id["C9"], self.by_id["C4"]
        sents = [NarrativeSentence(text=GOOD_C9, sources=["INT-090#2"]), NarrativeSentence(text=GOOD_C4, sources=["INT-049#4"])]
        f = Finding(text=" ".join([compose(GOOD_C9, [c9]), compose(GOOD_C4, [c4])]), refs=[c9.evidence_refs[0], c4.evidence_refs[0]], origin="narrative_checked", narrative=sents)
        v = self.verdict_of(f)
        self.assertEqual(v.verdict, "unsupported")
        self.assertIn("TEMPORAL_MISMATCH", set(v.reason_codes))

    def test_uncited_forged_or_foreign_evidence_cannot_become_the_authority(self):
        real = next(e for e in self.rep0.evidence if (e.interaction_id, e.turn_index) == ("INT-049", 4) and e.excerpt)
        foreign = real.model_copy(update={"ref": "E90", "client_id": "CLI-SOMEONE-ELSE", "excerpt": "Entirely invented words that nobody said."})
        forged = real.model_copy(update={"ref": "E91", "excerpt": "Entirely invented words that nobody said."})
        rep = self.rep0.model_copy(update={"evidence": [foreign, forged] + list(self.rep0.evidence)})
        final, _ = self.verify(rep)
        self.assertTrue([f for f in final.historical_context if f.origin == "narrative_checked"], "uncited, unauthorised or unverifiable items must not displace the real turn")

    def test_the_verifier_refuses_a_cross_meeting_sentence_that_got_past_the_agent(self):
        c4, c1 = self.by_id["C4"], self.by_id["C1"]
        s = 'On 2026-03-04 the client said "I was only exploring the possibility", and the advisor noted "Good. So the working assumption is retirement at 65".'
        f = Finding(text=compose(s, [c4, c1]), refs=[c4.evidence_refs[0], c1.evidence_refs[0]], origin="narrative_checked",
                    narrative=[NarrativeSentence(text=s, sources=["INT-049#4", "INT-001#9"])])
        v = self.verdict_of(f)
        self.assertEqual(v.verdict, "unsupported")
        self.assertIn("TEMPORAL_MISMATCH", set(v.reason_codes))

    def test_the_agents_timeline_is_not_trusted(self):
        forged = [c.model_copy(update={"statement_kind": "advisor_interpretation", "wording": "x", "interaction_id": "INT-777"}) for c in self.rep0.timeline]
        final, _ = self.verify(self.rep0.model_copy(update={"timeline": forged}))
        self.assertTrue([f for f in final.historical_context if f.origin == "narrative_checked"], "the verifier rebuilt the timeline; the supplied one is ignored")

    def test_cross_client_evidence_is_refused(self):
        v = self.verdict_of(self.good, plan_clients=["CLI-SOMEONE-ELSE"])
        self.assertEqual(v.verdict, "unsupported")
        self.assertIn("CLIENT_MISMATCH", set(v.reason_codes))

    def test_a_permission_violation_is_refused(self):
        v = self.verdict_of(self.good, owns=lambda cid: False)
        self.assertEqual(v.verdict, "unsupported")
        self.assertTrue({"EVIDENCE_INACCESSIBLE", "CLIENT_MISMATCH"} & set(v.reason_codes))

    def test_evidence_whose_quotation_is_not_the_source_turn_is_refused(self):
        ev = [e.model_copy(update={"excerpt": "Entirely invented words that nobody said."}) if e.ref == self.good.refs[1] else e for e in self.rep0.evidence]
        final, _ = self.verify(self.rep0.model_copy(update={"evidence": ev}))
        self.assertFalse([f for f in final.historical_context if f.origin == "narrative_checked"])

    def test_unverified_memory_cannot_be_cited_alongside_the_turn(self):
        mem = next(e for e in self.rep0.evidence if e.evidence_class in ("unverified", "contradicted", "open_conflict"))
        v = self.verdict_of(self.good.model_copy(update={"refs": list(self.good.refs) + [mem.ref]}))
        self.assertEqual(v.verdict, "unsupported")
        self.assertIn("EVIDENCE_NOT_VERIFIED", set(v.reason_codes))

    def test_a_finding_without_recorded_sentences_is_refused(self):
        self.assertEqual(self.verdict_of(self.good.model_copy(update={"narrative": None})).verdict, "unsupported")

    def test_when_the_narrative_fails_verification_the_b3_chronology_is_still_in_the_final_report(self):
        bad = self.good.model_copy(update={"text": self.good.text + " Extra."})
        rep = self.rep0.model_copy(update={"historical_context": [bad] + [f for f in self.rep0.historical_context if f.origin != "narrative_checked"]})
        final, _ = self.verify(rep)
        self.assertFalse([f for f in final.historical_context if f.origin == "narrative_checked"])
        self.assertTrue([f for f in final.historical_context if f.origin == "rendered"])
        self.assertTrue(any("Withheld unsupported statement" in u for u in final.uncertainties))


if __name__ == "__main__":
    unittest.main()
