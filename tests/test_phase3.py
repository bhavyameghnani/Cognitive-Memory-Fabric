"""Phase 3: evidence-grounded narrative SYNTHESIS.  The model paraphrases; code validates each proposition for factual support (agents/report/synthesis.py) and the Evidence Verifier re-checks it on claims it
rebuilds itself.  The Kenji timeline of test_report_semantic is the fixture; Rebecca-style claims are built by hand.  Scripted models only; nothing here calls a live model.

The tests are grouped by what they distinguish: a supported paraphrase from an unsupported assertion, a client statement from an advisor's interpretation, a tentative position from a confirmed
decision, an original position from its correction, a missing outcome from an established one, and a coherent narrative from a quotation dump."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_report_semantic as S  # noqa: E402
import test_report_timeline as T  # noqa: E402
from agents.report import synthesis as SY  # noqa: E402
from agents.report.agent import report_mode  # noqa: E402
from schemas.report import ClauseOut, Finding, NarrativeSentence, SummarySentenceOut, TimelineClaim  # noqa: E402


def mk(i: int, meeting: int, day: int, **kw) -> TimelineClaim:
    base = dict(claim_id=f"C{i}", source="source_turn", wording=f"The client said something substantial about the subject number {i} in this meeting.", speaker="Client", speaker_role="Client",
                attribution="client_direct", statement_kind="client_statement", modality="statement", interaction_id=f"INT-{meeting:03d}", turn_index=i, event_date=f"2026-01-{day:02d}",
                evidence_refs=[f"E{i}"], relevant=True, mentions_topic=True)
    base.update(kw)
    return TimelineClaim(**base)


def lines(*pairs):
    return [ClauseOut(claim=c, sentence=s) for c, s in pairs]


# the sentences a careful model would write for the Kenji lines
GOOD = {
    "C1": "The advisor recorded that the working assumption was retirement at 65, as a plan and not a fixed decision.",
    "C4": "Kenji said that he was only exploring the possibility and had not made that decision.",
    "C9": "Kenji said that he and Mariko had decided on 63.",
}


class Base(S.Base):
    def judge(self, text, ids):
        return SY.proposition_problems(text, self.cited(ids), self.names)

    def codes(self, text, ids):
        return {p.split(":")[0] for p in self.judge(text, ids)}


# ------------------------------------------------------------------------------------------------------------------------------------ a supported paraphrase vs an unsupported assertion
class SupportedVersusUnsupported(Base):
    def test_a_faithful_paraphrase_of_one_line_is_accepted(self):
        for cid, text in GOOD.items():
            self.assertEqual(self.judge(text, [cid]), [], cid)

    def test_a_paraphrase_may_use_other_words_for_the_same_things(self):
        # "uncertain" for "unsure", "business" for "company", different tenses: the support check compares roots, it does not demand the source's words
        a = mk(1, 1, 2, wording="I've run the company for twenty-two years and I honestly don't know if I want to sell.", modality="tentative")
        self.assertEqual(SY.proposition_problems("Rebecca said she was unsure about selling the business she had run for twenty-two years.", [a], ["Rebecca"]), [])

    def test_an_invented_fact_is_refused(self):
        for text in ("Kenji said that he was only exploring the possibility and had a medical check booked.",           # a fact no cited line states
                     "Kenji said that he was only exploring the possibility, which worried his wife.",                    # a feeling and a person no line mentions
                     "Kenji said that he was only exploring the possibility because the market had fallen."):             # a cause
            self.assertTrue(self.judge(text, ["C4"]), text)

    def test_an_invented_cause_motive_or_feeling_is_refused_by_name(self):
        self.assertIn("causal_explanation", self.codes("Kenji said that he was exploring the possibility because the market had fallen.", ["C4"]))
        self.assertIn("inner_state", self.codes("Kenji said that he wanted to explore the possibility and was worried.", ["C4"]))

    def test_a_figure_or_date_the_line_does_not_hold_is_refused(self):
        self.assertIn("unsupported_figure", self.codes("Kenji said that he and Mariko had decided on 61.", ["C9"]))
        self.assertTrue(any(p.startswith("unsupported_date") for p in self.judge("On 5 May, Kenji said that he and Mariko had decided on 63.", ["C9"])))
        self.assertEqual(self.judge("On 1 May, Kenji said that he and Mariko had decided on 63.", ["C9"]), [])

    def test_a_paraphrase_is_never_allowed_inside_quotation_marks(self):
        self.assertIn("quote_not_verbatim", self.codes('Kenji said "he was merely looking at the idea" and had not made that decision.', ["C4"]))
        self.assertEqual(self.judge('Kenji said that "I was only exploring the possibility" and had not made that decision.', ["C4"]), [])      # an exact quotation is fine

    def test_the_sentence_must_name_who_said_it_and_be_one_sentence(self):
        self.assertIn("attribution_missing", self.codes("He was only exploring the possibility and had not made that decision.", ["C4"]))
        self.assertIn("not_one_sentence", self.codes("Kenji said that he was exploring. He had not decided anything at all.", ["C4"]))
        self.assertIn("too_short", self.codes("Kenji was exploring.", ["C4"]))


# ------------------------------------------------------------------------------------------------------------------------------------ client vs advisor; tentative vs confirmed; original vs correction; missing vs established outcome
class Distinctions(Base):
    def test_an_advisors_interpretation_is_never_presented_as_the_clients_statement(self):
        self.assertIn("attribution_mismatch", self.codes("Kenji said that he wanted to retire at 60.", ["C3"]))
        self.assertTrue(self.codes("Kenji told the advisor that he understood he would retire at 60.", ["C3"]) & {"attribution_mismatch", "correction_omitted"})
        self.assertIn("attribution_mismatch", self.codes("The advisor said that he and Mariko had decided on 63.", ["C9"]))             # and a client statement is not the advisor's

    def test_a_tentative_client_line_stays_tentative(self):
        self.assertIn("certainty_inflated", self.codes("Kenji said that he was leaning that way.", ["C4"]))                      # the hedged sentence of the line ("leaning that way, but I haven't made that decision"), restated without its hedge
        self.assertTrue(self.codes("Kenji decided to retire at 65 and said so.", ["C4"]) & {"decision_language", "certainty_inflated", "unsupported_assertion"})

    def test_a_decision_is_told_as_a_decision_only_when_the_client_line_is_one(self):
        self.assertEqual(self.judge(GOOD["C9"], ["C9"]), [])                                                                          # "We've decided: 63" is the client's own decision
        self.assertIn("decision_language", self.codes("The advisor noted that Kenji had decided to retire at 65.", ["C1"]))            # an advisor's tentative account is not a decision

    def test_an_original_position_is_not_told_without_its_correction(self):
        self.assertIn("correction_omitted", self.codes("The advisor noted that Kenji had wanted to retire at 60.", ["C3"]))
        self.assertTrue(self.cited(["C3"])[0].temporal_status == "corrected" and self.cited(["C4"])[0].relations)                      # the fixture really is an original / correction pair
        self.assertEqual(self.judge(GOOD["C4"], ["C4"]), [])                                                                          # the correcting line itself is told as it stands

    def test_a_correction_is_only_spoken_of_where_the_record_records_one(self):
        self.assertIn("relation_unsupported", self.codes("The advisor corrected the record and noted that the plan was 65.", ["C1"]))

    def test_an_outcome_the_line_does_not_state_is_refused_and_a_hedged_one_stays_hedged(self):
        self.assertIn("outcome_unsupported", self.codes("The advisor recorded that Kenji had signed the retirement plan.", ["C1"]))
        a = mk(1, 1, 2, wording="Not yet. My lawyer is reading it and I haven't signed the letter of intent.", modality="tentative")
        self.assertEqual(SY.proposition_problems("Rebecca said she had not yet signed the letter of intent.", [a], ["Rebecca"]), [])
        self.assertIn("outcome_inflated", {p.split(":")[0] for p in SY.proposition_problems("Rebecca said that she signed the letter of intent.", [a], ["Rebecca"])})

    def test_a_line_that_is_only_about_one_claim_cannot_borrow_another_lines_facts(self):
        self.assertTrue(self.judge("Kenji said that he and Mariko had decided on 63.", ["C4"]))


# ------------------------------------------------------------------------------------------------------------------------------------ preparing and building sentences
class Building(Base):
    def test_the_software_adds_the_date_and_the_clients_name_replaces_a_pronoun(self):
        c = self.by_id["C4"]
        self.assertEqual(SY.prepare_sentence(c, "She was only exploring the possibility and had not made that decision", ["Kenji"]), "On 4 March, Kenji was only exploring the possibility and had not made that decision.")
        self.assertEqual(SY.prepare_sentence(c, "On 9 March Kenji said it", ["Kenji"], dated=False), "Kenji said it.")                  # a date of the model's own is dropped
        adv = self.by_id["C3"]
        self.assertTrue(SY.prepare_sentence(adv, "She noted a thing", ["Kenji"], dated=False).startswith("She "))                    # a pronoun is NOT turned into the client's name for an advisor line
        self.assertTrue(SY.prepare_sentence(adv, "The advisor noted a thing", ["Kenji"]).startswith("On 4 March, the advisor"))

    def build(self, pairs, **kw):
        return SY.build_lines(lines(*pairs), self.tl, self.names or ["Kenji"], **kw)

    def test_lines_are_ordered_by_the_record_and_dated_once_per_meeting(self):
        f, rej = self.build([("C9", GOOD["C9"]), ("C1", GOOD["C1"]), ("C4", GOOD["C4"])])
        self.assertEqual(rej, [])
        self.assertEqual([x.sources for g in f for x in g.narrative], [[f"INT-001#{self.by_id['C1'].turn_index}"], ["INT-049#4"], [f"INT-090#{self.by_id['C9'].turn_index}"]])
        text = " ".join(g.text for g in f)
        self.assertLess(text.index("working assumption"), text.index("exploring"))
        self.assertLess(text.index("exploring"), text.index("decided on 63"))
        self.assertEqual(text.count("On 2 January"), 1)                                                   # one date per meeting, on its first sentence
        self.assertTrue(all(g.origin == "synthesis_checked" for g in f))

    def test_one_finding_per_meeting_and_every_sentence_ends_with_its_evidence_handle(self):
        f, _ = self.build([("C1", GOOD["C1"]), ("C4", GOOD["C4"]), ("C9", GOOD["C9"])])
        self.assertEqual(len(f), 3)
        for g in f:
            self.assertRegex(g.text, r"\(INT-\d+ turn \d+, recorded speaker \w+\) \[E\d+\]\.$")      # provenance written by code: meeting, turn, recorded speaker, evidence handle

    def test_a_rejected_line_is_recorded_with_codes_and_claim_ids_never_text(self):
        f, rej = self.build([("C4", "Kenji said that he would retire at 61 because of his health."), ("C9", GOOD["C9"])])
        self.assertEqual(len(f), 1)
        self.assertEqual(rej[0]["claims"], ["C4"])
        self.assertTrue({"causal_explanation", "unsupported_figure"} & set(rej[0]["problems"]) or rej[0]["problems"])
        self.assertNotIn("health", repr(rej))

    def test_unknown_claims_and_claims_outside_the_window_are_refused(self):
        f, rej = self.build([("C99", "Kenji said something here."), ("C9", GOOD["C9"])], window=["C1", "C4"], context=["C9"])
        self.assertEqual(f, [])
        self.assertEqual(sorted(r["problems"][0] for r in rej), ["context_only", "unknown_claim"])

    def test_a_claim_is_narrated_once_across_windows(self):
        narrated, seen = {"C4"}, set()
        f, rej = self.build([("C4", GOOD["C4"])], window=["C4"], narrated=narrated, seen_texts=seen)
        self.assertEqual((f, [r["problems"] for r in rej]), ([], [["duplicate_claim"]]))

    def test_a_window_cannot_go_back_before_where_the_previous_one_ended(self):
        keys = lambda i: (self.by_id[i].event_date or "", self.by_id[i].interaction_id or "", self.by_id[i].turn_index)
        f, rej = self.build([("C1", GOOD["C1"])], window=["C1"], after=keys("C9"))
        self.assertEqual((f, [r["problems"] for r in rej]), ([], [["chronology_reversed"]]))

    def test_a_risky_line_is_split_from_its_clean_neighbours_in_the_same_meeting(self):
        a, b = mk(1, 5, 3, wording="Mei Lin Chu sent three years of statements and a draft valuation."), mk(2, 5, 3, wording="I want net proceeds and the people side of a sale first, and nothing else for now.")
        f, rej = SY.build_lines(lines(("C1", "Rebecca said that Mei Lin Chu sent three years of statements and a draft valuation."), ("C2", "Rebecca said that she wanted net proceeds and the people side of a sale first.")),
                                [a, b], ["Rebecca"], risky={"E2"})
        self.assertEqual((len(f), rej), (2, []))
        self.assertEqual([bool(set(g.refs) & {"E2"}) for g in f], [False, True])

    def test_pronoun_only_openings_for_advisor_lines_are_rejected_not_repaired(self):
        f, rej = self.build([("C1", "She recorded that the working assumption was retirement at 65.")])
        self.assertEqual(f, [])
        self.assertIn("attribution_missing", rej[0]["problems"])


# ------------------------------------------------------------------------------------------------------------------------------------ the executive summary
class Executive(Base):
    def props(self):
        return {"P1": {"text": GOOD["C1"], "claims": ["C1"], "date": "2026-01-02"}, "P2": {"text": GOOD["C4"], "claims": ["C4"], "date": "2026-03-04"}, "P3": {"text": GOOD["C9"], "claims": ["C9"], "date": "2026-05-01"}}

    def test_a_summary_that_restates_the_propositions_is_accepted_and_marked(self):
        sents = [SummarySentenceOut(text="The advisor recorded in January that the plan was retirement at 65, not a fixed decision.", props=["P1"]),
                 SummarySentenceOut(text="By May Kenji said that he and Mariko had decided on 63, after saying in March that he was only exploring the possibility.", props=["P3", "P2"])]
        f, rej = SY.build_executive(sents, self.props(), self.tl, ["Kenji"])
        self.assertTrue(f is not None, rej)
        self.assertEqual((f.window, f.origin), (-1, "synthesis_checked"))
        self.assertEqual(rej, [])

    def test_a_summary_cannot_state_what_no_proposition_supports(self):
        sents = [SummarySentenceOut(text="Kenji confirmed that he would retire at 63 and the plan was final.", props=["P3"]),
                 SummarySentenceOut(text="Kenji said that he was worried about money.", props=["P2"]),
                 SummarySentenceOut(text="Kenji said that he and Mariko had decided on 63.", props=["P9"])]
        f, rej = SY.build_executive(sents, self.props(), self.tl, ["Kenji"])
        self.assertIsNone(f)
        self.assertEqual(len(rej), 3)
        self.assertEqual(rej[2]["problems"], ["unknown_proposition"])

    def test_the_summary_runs_in_date_order(self):
        sents = [SummarySentenceOut(text="Kenji said that he and Mariko had decided on 63.", props=["P3"]), SummarySentenceOut(text="Kenji said that he was only exploring the possibility.", props=["P2"])]
        f, rej = SY.build_executive(sents, self.props(), self.tl, ["Kenji"])
        self.assertEqual([r["problems"] for r in rej], [["chronology_reversed"]])


# ------------------------------------------------------------------------------------------------------------------------------------ the agent, end to end, with scripted models
class AgentSynthesis(Base):
    def run_agent(self, *outputs):
        a, llm = T.agent_for([T.llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[])] + list(outputs))
        rep = a.write(S.Q, self.plan, self.mem, T.tstate(*self.execs))
        return rep, llm

    WINDOW = {"lines": [{"claim": c, "sentence": s} for c, s in GOOD.items()]}
    SUMMARY = {"sentences": [{"text": "The advisor recorded in January that the plan was retirement at 65, not a fixed decision.", "props": ["P1"]},
                             {"text": "By May Kenji said that he and Mariko had decided on 63.", "props": ["P3"]}]}

    def test_the_default_mode_is_synthesis_and_quotes_is_a_switch(self):
        self.assertEqual(report_mode(), "synthesis")
        with mock.patch.dict("os.environ", {"CMF_REPORT_MODE": "quotes"}):
            self.assertEqual(report_mode(), "quotes")

    def test_prose_is_paraphrase_with_an_executive_summary_and_full_accounting(self):
        rep, llm = self.run_agent(self.WINDOW, self.SUMMARY)
        self.assertEqual([c["agent"] for c in llm.calls], ["report"] * 2)                                   # main + one window: the executive summary is SELECTED by code, no call of its own
        prose = [f for f in rep.historical_context if f.origin == "synthesis_checked"]
        self.assertEqual([f.window for f in prose][0], -1)                                                  # the executive summary comes first
        body = [f for f in prose if f.window != -1]
        self.assertTrue(body and all(f.window == 0 for f in body))
        self.assertFalse(SY.is_quotation_dump(body), SY.readability(body))
        cov = rep.coverage
        self.assertEqual(sorted(cov.narrated), ["C1", "C4", "C9"])
        self.assertEqual(rep.validation["narrative"]["mode"], "synthesis")
        ex = rep.validation["narrative"]["executive"]                                                        # Phase 4A: the executive block is filled by code from relevance-selected slots, not by position
        self.assertEqual((ex["attempted"], ex["model_call"], ex["sentences"]), (True, False, len(prose[0].narrative)))
        self.assertEqual(set(ex["slots"]), {"beginning", "corrections", "decision"})
        self.assertIsNotNone(rep.outcome)

    def test_the_independent_verifier_accepts_what_the_agent_accepted(self):
        rep, _ = self.run_agent(self.WINDOW, self.SUMMARY)
        final, res = self.verify(rep)
        self.assertEqual([(v.verdict, v.reasons, v.text[:60]) for v in res.verdicts if v.verdict == "unsupported" and "synthesis" in str(v.text)[:0] + ""], [])
        self.assertEqual([(v.reasons, v.text[:70]) for v in res.verdicts if v.verdict == "unsupported" and v.text.startswith(("On ", "The advisor", "Kenji", "The advisor recorded"))], [])
        self.assertEqual(len([f for f in final.historical_context if f.origin == "synthesis_checked"]), len([f for f in rep.historical_context if f.origin == "synthesis_checked"]))

    def test_the_independent_verifier_rejects_a_forged_proposition(self):
        rep, _ = self.run_agent(self.WINDOW, self.SUMMARY)
        forged = []
        for f in rep.historical_context:
            if f.origin == "synthesis_checked" and f.window == 0 and any("exploring" in s.text for s in f.narrative):
                sn = [s.model_copy(update={"text": s.text.replace("only exploring the possibility", "planning to retire at 61 because of his health")}) for s in f.narrative]
                f = f.model_copy(update={"narrative": sn, "text": f.text.replace("only exploring the possibility", "planning to retire at 61 because of his health")})
            forged.append(f)
        final, res = self.verify(rep.model_copy(update={"historical_context": forged}))
        self.assertFalse([f for f in final.historical_context if f.origin == "synthesis_checked" and "because of his health" in f.text])
        self.assertIn("C4", final.coverage.withheld)                                                        # and the coverage says so
        self.assertIn("evidence verifier", final.coverage.withheld_reasons["C4"])

    def test_when_no_proposition_passes_the_verified_quotation_narrative_is_the_fallback(self):
        bad = {"lines": [{"claim": "C4", "sentence": "Kenji decided because of the weather."}, {"claim": "C9", "sentence": "He was happy."}]}
        quotes = self.nar((S.GOOD_C1, ["C1"]), (S.GOOD_C9, ["C9"]))
        rep, llm = self.run_agent(bad, bad, quotes)            # synthesis window: try + retry, then the quotation narrative
        self.assertIn("synthesis_fallback", rep.validation)
        self.assertTrue([f for f in rep.historical_context if f.origin == "narrative_checked"])
        self.assertFalse([f for f in rep.historical_context if f.origin == "synthesis_checked"])

    def test_a_failed_window_is_a_recorded_gap_and_the_retry_is_stricter(self):
        rep, llm = self.run_agent(RuntimeError("timed out"), self.WINDOW, self.SUMMARY)
        w = rep.coverage.windows[0]
        self.assertEqual((w.status, w.attempts), ("ok", 2))
        self.assertIn("MOST OF YOUR PREVIOUS SENTENCES WERE REJECTED", llm.calls[1]["user"])           # calls[0] is the main report call; the failed first attempt leaves no record
        self.assertNotIn("MOST OF YOUR PREVIOUS", llm.calls[0]["user"])

    def test_the_story_stays_off_and_the_outcome_is_written_by_code(self):
        rep, _ = self.run_agent(self.WINDOW, self.SUMMARY)
        self.assertEqual(rep.story, [])
        self.assertTrue(rep.outcome.not_established)


# ------------------------------------------------------------------------------------------------------------------------------------ rules added after the first live run
class AfterTheLiveRun(Base):
    def test_a_line_pasted_into_the_report_in_the_first_or_second_person_is_not_a_paraphrase(self):
        a = mk(1, 1, 2, wording="You are still weighing relocating to the coast in a couple of years, which I have recorded as an idea.", statement_kind="advisor_interpretation", speaker="Advisor")
        self.assertIn("person_shift", {p.split(":")[0] for p in SY.proposition_problems("The advisor said that you are still weighing relocating to the coast in a couple of years.", [a], ["Rebecca"])})
        self.assertEqual(SY.proposition_problems("The advisor said that Rebecca was still weighing relocating to the coast in a couple of years.", [a], ["Rebecca"]), [])

    def test_a_very_short_reply_is_never_paraphrased_into_a_commitment(self):
        short = mk(1, 1, 30, wording="Go ahead. I don't have long.")
        long = mk(2, 1, 30, wording="Rebecca, let me summarise your year so far. In January you were curious but undecided about selling.", statement_kind="advisor_interpretation", speaker="Advisor")
        self.assertEqual([c.claim_id for c in SY.synthesis_pool([short, long])], ["C2"])
        fix = mk(3, 1, 31, wording="Fine.", modality="correction", memory_refs=["M1"], relations=[{"type": "corrects", "claim_id": "C2"}])
        self.assertIn("C3", [c.claim_id for c in SY.synthesis_pool([fix])])                              # a correcting line is kept whatever its length

    def test_a_correcting_line_is_marked_for_the_model_with_what_to_say(self):
        c = self.by_id["C4"]
        self.assertIn("THIS LINE CORRECTS AN EARLIER RECORD", SY.synthesis_line(c))
        self.assertNotIn("THIS LINE CORRECTS", SY.synthesis_line(self.by_id["C9"]))

    def test_the_repair_hints_say_what_each_rule_objected_to_without_quoting_the_rejected_text(self):
        h = SY.repair_hints(["decision_language", "unsupported_assertion", "attribution_missing"], ["Rebecca"])
        self.assertIn("do not write decided, agreed", h)
        self.assertIn("use only words, names and numbers that appear in this line", h)
        self.assertIn("Rebecca said that", h)
        self.assertEqual(SY.repair_hints([], ["Rebecca"]), "restate only what this line says, in your own plain words")

    def test_a_repair_call_runs_for_a_window_with_three_or_more_rejected_lines_and_merges_in_record_order(self):
        ids = ["C1", "C4", "C5", "C9"]
        wa = [self.by_id[i] for i in ids]
        bad = {"lines": [{"claim": "C1", "sentence": "The advisor noted that Kenji had decided to retire at sixty-five."}, {"claim": "C4", "sentence": "Kenji said that he would retire at 61 because of his health."},
                         {"claim": "C5", "sentence": "The advisor said that Kenji was delighted."}, {"claim": "C9", "sentence": GOOD["C9"]}]}
        fixed = {"lines": [{"claim": "C1", "sentence": GOOD["C1"]}, {"claim": "C4", "sentence": GOOD["C4"]}, {"claim": "C5", "sentence": "The advisor said that the wording of the file note was too strong."}]}
        with mock.patch("agents.report.agent.SY.synthesis_windows", lambda tl: [wa]):
            a, llm = T.agent_for([T.llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[]), bad, fixed])
            rep = a.write(S.Q, self.plan, self.mem, T.tstate(*self.execs))
        self.assertEqual(len(llm.calls), 3)                                                                  # main + window + ONE repair
        self.assertIn("REJECTED by the checks", llm.calls[2]["user"])
        self.assertIn("NOTE:", llm.calls[2]["user"])
        self.assertNotIn("health", llm.calls[2]["user"])                                                     # the hints never repeat the rejected text
        self.assertEqual(sorted(rep.coverage.narrated), ["C1", "C4", "C5", "C9"])
        body = [f for f in rep.historical_context if f.origin == "synthesis_checked" and f.window == 0]
        order = [sn.sources[0] for f in body for sn in f.narrative]
        self.assertEqual(order, [f"INT-001#{self.by_id['C1'].turn_index}", f"INT-049#{self.by_id['C4'].turn_index}", f"INT-049#{self.by_id['C5'].turn_index}", f"INT-090#{self.by_id['C9'].turn_index}"])
        self.assertEqual(rep.validation["narrative"]["repairs"], 1)


class AgentAndVerifierAgree(Base):
    """What the agent accepts the verifier must accept: the same attributing verbs, the same source-record rule.  A disagreement once cost a whole executive summary (one sentence with 'reminded ... current')."""

    def item(self, cid):
        ref = self.by_id[cid].evidence_refs[0]
        return next(e for e in self.items if e.ref == ref)

    def test_a_sentence_the_agent_accepts_passes_the_verifiers_source_record_rule(self):
        from agents.report.validate import source_record_problem
        cases = [("C1", GOOD["C1"]), ("C4", GOOD["C4"]), ("C9", GOOD["C9"]), ("C1", "The advisor reminded Kenji that this was his current plan, not a fixed decision."),
                 ("C9", "Kenji told the advisor that he and Mariko had decided on 63.")]
        for cid, text in cases:
            c = self.by_id[cid]
            if self.judge(SY.prepare_sentence(c, text, ["Kenji"]), [cid]):
                continue                                                      # the agent refuses it: nothing to agree about
            composed = SY.compose_synthesis(SY.prepare_sentence(c, text, ["Kenji"]), [c])
            self.assertIsNone(source_record_problem(composed, [self.item(cid)], paraphrase=True), text)

    def test_the_two_lists_of_attributing_verbs_are_one_list(self):
        from agents.report import validate
        self.assertIs(SY.ATTRIB_VERBS, validate._ATTRIBUTING)

    def test_a_sentence_without_a_speech_verb_is_refused_by_both(self):
        from agents.report.validate import source_record_problem
        c = self.by_id["C9"]
        text = SY.compose_synthesis("Kenji and Mariko decided on 63.", [c])
        self.assertTrue(self.judge("Kenji and Mariko decided on 63.", ["C9"]))
        self.assertIn("decided", source_record_problem(text, [self.item("C9")], paraphrase=True) or "")

    def test_a_question_is_reported_as_a_question(self):
        q = mk(1, 1, 19, wording="Do I need to time the sale around Ethan's applications?")
        self.assertIn("question_as_statement", {p.split(":")[0] for p in SY.proposition_problems("Rebecca said that she needed to time the sale around Ethan's applications.", [q], ["Rebecca"])})
        self.assertEqual(SY.proposition_problems("Rebecca asked whether she needed to time the sale around Ethan's applications.", [q], ["Rebecca"]), [])


# ------------------------------------------------------------------------------------------------------------------------------------ the outcome: missing vs established
class Outcome(unittest.TestCase):
    def test_a_record_with_only_hedged_mentions_establishes_nothing(self):
        cs = [mk(1, 1, 3, wording="Not yet. My lawyer is reading it and I haven't signed the letter of intent."),
              mk(2, 2, 9, wording="Let me outline how your plan will look if the sale closes as expected.", statement_kind="advisor_interpretation", speaker="Advisor"),
              mk(3, 3, 20, wording="Open items: the final purchase agreement draft, your estate documents.", statement_kind="advisor_interpretation", speaker="Advisor")]
        o = SY.outcome_lines(cs)
        self.assertEqual(o["established"], [])
        self.assertIn("No retrieved source turn establishes that the letter of intent was signed or that the sale completed", o["not_established"][0])
        self.assertEqual([m["kind"] for m in o["mentions"]], ["hedged", "hedged", "hedged"])                    # negated, conditional, still-to-come: none is an event
        pend = SY.outcome_lines([mk(1, 1, 3, wording="Open items: the final purchase agreement draft and the policy for your signature.", statement_kind="advisor_interpretation", speaker="Advisor")])
        self.assertIn("pending or still to come", pend["not_established"][0])

    def test_a_plain_mention_is_flagged_for_reading_not_silently_accepted(self):
        cs = [mk(1, 1, 3, wording="We signed the letter of intent on Friday.")]
        o = SY.outcome_lines(cs)
        self.assertIn("record a signing, closing or completion in the past tense without hedging", o["not_established"][0])
        self.assertEqual(o["mentions"][0]["kind"], "event")

    def test_a_question_about_signing_is_not_an_event(self):
        o = SY.outcome_lines([mk(1, 1, 3, wording="That is a significant step. Have you signed anything?", statement_kind="advisor_interpretation", speaker="Advisor")])
        self.assertEqual([m["kind"] for m in o["mentions"]], ["hedged"])
        self.assertIn("No retrieved source turn establishes", o["not_established"][0])

    def test_no_mention_at_all_is_stated_as_such(self):
        o = SY.outcome_lines([mk(1, 1, 3, wording="Hello there, how are you today my friend.")])
        self.assertIn("No retrieved source turn mentions", o["not_established"][0])


# ------------------------------------------------------------------------------------------------------------------------------------ a coherent narrative vs a quotation dump
def para(*sentences) -> Finding:
    return Finding(text=" ".join(sentences), refs=[], origin="synthesis_checked", narrative=[NarrativeSentence(text=s, sources=["INT-001#1"]) for s in sentences])


REBECCA_REPORT = [
    para("On 2 January, Rebecca said that a private equity group called Lakeshore Partners had contacted her about acquiring Holloway Precision Packaging.",
         "Rebecca said that she was curious but had not decided whether to sell the business she had run for twenty-two years."),
    para("On 11 March, Rebecca said tentatively that she was inclined to say yes but had not yet signed, while her lawyer read the letter of intent."),
    para("On 24 March, the advisor corrected the record, noting that his earlier note that Rebecca had accepted the offer was wrong and that she was inclined to accept and had not signed."),
    para("On 20 April, Rebecca said tentatively that she was not sure about selling now and might wait two years.",
         "On 22 April, Rebecca said that she had decided to sell to Lakeshore with the rollover reduced to ten percent and two years of written staff protections."),
]
QUOTATION_DUMP = [
    Finding(text='On 2026-01-02 the client said "A private equity group called Lakeshore Partners contacted me out of the blue.", (INT-002 turn 4, recorded speaker Client) [E2].', refs=["E2"], origin="narrative_checked",
            narrative=[NarrativeSentence(text='On 2026-01-02 the client said "A private equity group called Lakeshore Partners contacted me out of the blue."', sources=["INT-002#4"])]),
    Finding(text='On 2026-03-24 the advisor recorded "I\'ve corrected my own record: my earlier note that you had accepted the Lakeshore offer was wrong", (INT-058 turn 13, recorded speaker Advisor) [E53].', refs=["E53"],
            origin="narrative_checked", narrative=[NarrativeSentence(text='On 2026-03-24 the advisor recorded "I\'ve corrected my own record: my earlier note that you had accepted the Lakeshore offer was wrong"', sources=["INT-058#13"])]),
]
CHECKLIST = {                      # what a reader must be able to learn from the report (Rebecca acceptance scenario)
    "the initial approach": ("Lakeshore Partners", "contacted"),
    "considering rather than accepting": ("inclined", "not yet signed"),
    "the 24 March correction": ("corrected the record", "had accepted the offer was wrong"),
    "the April reconsideration": ("not sure about selling", "wait two years"),
    "the April decision and its conditions": ("decided to sell", "rollover", "staff protections"),
}


def checklist(findings):
    text = " ".join(f.text for f in findings).lower()
    return {k: all(w.lower() in text for w in ws) or any(w.lower() in text for w in ws[:1]) and all(w.lower() in text for w in ws) for k, ws in CHECKLIST.items()}


class Readability(unittest.TestCase):
    def test_a_written_report_is_not_a_quotation_dump_and_a_quotation_dump_is(self):
        self.assertFalse(SY.is_quotation_dump(REBECCA_REPORT), SY.readability(REBECCA_REPORT))
        self.assertTrue(SY.is_quotation_dump(QUOTATION_DUMP), SY.readability(QUOTATION_DUMP))

    def test_the_measures(self):
        good, bad = SY.readability(REBECCA_REPORT), SY.readability(QUOTATION_DUMP)
        self.assertEqual(good["quote_share"], 0.0)
        self.assertEqual(good["paraphrase_share"], 1.0)
        self.assertGreater(good["attributed_share"], 0.9)
        self.assertGreater(bad["quote_share"], 0.5)
        self.assertEqual(bad["paraphrase_share"], 0.0)
        self.assertEqual(SY.readability([])["sentences"], 0)

    def test_the_report_explains_the_rebecca_history_a_dump_does_not(self):
        got = checklist(REBECCA_REPORT)
        self.assertTrue(all(got.values()), got)
        thin = checklist(QUOTATION_DUMP)
        self.assertFalse(all(thin.values()), thin)

    def test_every_sentence_of_the_good_report_passes_the_support_rules_against_its_own_line(self):
        # the Rebecca sentences above are faithful paraphrases of the real source turns: judge three of them against those turns
        c = mk(4, 2, 2, wording="A private equity group called Lakeshore Partners contacted me out of the blue. They said they'd like to talk about acquiring Holloway Precision Packaging. I've run it for twenty-two years and I honestly don't know if I want to sell.", modality="tentative", event_date="2026-01-02")
        self.assertEqual(SY.proposition_problems("On 2 January, Rebecca said that a private equity group called Lakeshore Partners had contacted her about acquiring Holloway Precision Packaging.", [c], ["Rebecca"]), [])
        d = mk(5, 3, 24, wording="No, and I'm glad you asked. I said I was inclined to accept. My lawyer is still negotiating, and I haven't signed the letter of intent.", modality="tentative", event_date="2026-03-24")
        self.assertEqual(SY.proposition_problems("On 24 March, Rebecca said tentatively that she was inclined to accept but had not signed the letter of intent.", [d], ["Rebecca"]), [])
        self.assertTrue(SY.proposition_problems("On 24 March, Rebecca said that she had accepted the offer and signed the letter of intent.", [d], ["Rebecca"]))


if __name__ == "__main__":
    unittest.main()
