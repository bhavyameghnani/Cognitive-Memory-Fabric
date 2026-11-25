"""Phase 2: B (windowed narration with a bounded retry policy and claim-by-claim coverage) and the wording link of a correction to the statement it corrects.  Scripted models only; the Kenji
timeline of test_report_semantic is the fixture, windows are forced with a patched `narrative_windows` where the real cut would put everything in one window."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_report_semantic as S  # noqa: E402
import test_report_timeline as T  # noqa: E402
from agents.report.agent import MAX_WINDOW_CALLS, coverage_notes  # noqa: E402
from agents.report.semantic import WINDOW_HARD, build_checked, narrative_windows, window_context  # noqa: E402
from agents.report.timeline import link_wording_corrections  # noqa: E402
from llm.client import LLMError  # noqa: E402
from schemas.report import TimelineClaim  # noqa: E402


def mk(i: int, meeting: int, day: int, **kw) -> TimelineClaim:
    base = dict(claim_id=f"C{i}", source="source_turn", wording=f"The client said something substantial about the subject number {i} in this meeting.", speaker="Client", speaker_role="Client",
                attribution="client_direct", statement_kind="client_statement", modality="statement", interaction_id=f"INT-{meeting:03d}", turn_index=i, event_date=f"2026-01-{day:02d}",
                evidence_refs=[f"E{i}"], relevant=True, mentions_topic=True)
    base.update(kw)
    return TimelineClaim(**base)


class BudgetExceeded(LLMError):          # the harness' own class has this name; the agent recognises it by name and stops asking
    pass


# ------------------------------------------------------------------------------------------------------------------------------------ the cut into windows
class Windows(unittest.TestCase):
    def test_every_relevant_claim_is_in_exactly_one_window_in_date_order(self):
        tl = [mk(i, i // 3, 1 + i // 3) for i in range(36)]
        ws = narrative_windows(tl)
        ids = [c.claim_id for w in ws for c in w]
        self.assertEqual(sorted(ids, key=lambda x: int(x[1:])), [f"C{i}" for i in range(36)])
        self.assertEqual(len(ids), len(set(ids)))
        keys = [(c.event_date, c.interaction_id, c.turn_index) for w in ws for c in w]
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(all(len(w) <= WINDOW_HARD for w in ws))

    def test_a_window_ends_at_a_meeting_boundary_once_it_has_ten_claims(self):
        tl = [mk(i, i // 4, 1 + i // 4) for i in range(40)]                   # meetings of 4 claims
        ws = narrative_windows(tl)
        for w in ws[:-1]:
            self.assertGreaterEqual(len(w), 10)
            self.assertEqual(len(w) % 4, 0)                                    # never cut inside a meeting
        self.assertEqual({len(w) for w in ws[:-1]} <= {12}, True)

    def test_one_huge_meeting_is_split_at_the_hard_limit(self):
        ws = narrative_windows([mk(i, 1, 1) for i in range(30)])
        self.assertEqual([len(w) for w in ws], [12, 12, 6])

    def test_a_tiny_last_window_is_merged_into_the_one_before(self):
        ws = narrative_windows([mk(i, i // 5, 1 + i // 5) for i in range(22)])  # 10, 10, then 2 left over
        self.assertEqual([len(w) for w in ws], [10, 12])

    def test_claims_that_are_not_relevant_source_turns_are_not_windowed(self):
        tl = [mk(1, 1, 1), mk(2, 1, 1, relevant=False), mk(3, 2, 2, source="memory"), mk(4, 2, 2, wording="Thanks.", mentions_topic=False)]
        self.assertEqual([c.claim_id for w in narrative_windows(tl) for c in w], ["C1"])

    def test_a_related_claim_in_another_window_is_given_as_context(self):
        a, b = mk(1, 1, 1), mk(2, 2, 2, relations=[{"type": "corrects", "claim_id": "C1"}])
        a.relations.append({"type": "corrected_by", "claim_id": "C2"})
        self.assertEqual([c.claim_id for c in window_context([b], [a, b])], ["C1"])
        self.assertEqual(window_context([a, b], [a, b]), [])                      # both inside: nothing to add


# ------------------------------------------------------------------------------------------------------------------------------------ joining windows
class Joining(S.Base):
    def sent(self, text, ids):
        return S.paras([(text, ids)])

    def test_a_sentence_may_cite_only_its_own_window_and_its_context(self):
        f, rej = build_checked(self.sent(S.GOOD_C9, ["C9"]), self.tl, self.names, window=["C1", "C3"], context=["C4"])
        self.assertEqual((f, [(r["problems"], r["claims"]) for r in rej]), ([], [(["outside_window"], ["C9"])]))
        f, rej = build_checked(self.sent(S.GOOD_C4, ["C4"]), self.tl, self.names, window=["C1", "C3"], context=["C4"])
        self.assertEqual((f, [r["problems"] for r in rej]), ([], [["context_only"]]))      # a context claim is never narrated on its own

    def test_a_repeat_of_what_an_earlier_window_narrated_is_a_duplicate(self):
        narrated, seen = {"C1"}, set()
        f, rej = build_checked(self.sent(S.GOOD_C1, ["C1"]), self.tl, self.names, window=["C1", "C3"], narrated=narrated, seen_texts=seen)
        self.assertEqual((f, [r["problems"] for r in rej]), ([], [["duplicate_claim"]]))

    def test_the_same_sentence_twice_is_a_duplicate(self):
        narrated, seen = set(), set()
        f1, r1 = build_checked(self.sent(S.GOOD_C1, ["C1"]), self.tl, self.names, window=["C1"], narrated=narrated, seen_texts=seen)
        self.assertEqual((len(f1), r1), (1, []))
        f2, r2 = build_checked(self.sent(S.GOOD_C1, ["C1"]), self.tl, self.names, window=["C1", "C3"], narrated=set(), seen_texts=seen)
        self.assertEqual((f2, [r["problems"] for r in r2]), ([], [["duplicate_sentence"]]))

    def test_a_window_cannot_go_back_before_where_the_previous_one_ended(self):
        keys = lambda i: (self.by_id[i].event_date or "", self.by_id[i].interaction_id or "", self.by_id[i].turn_index)
        f, rej = build_checked(self.sent(S.GOOD_C1, ["C1"]), self.tl, self.names, window=["C1"], after=keys("C9"))
        self.assertEqual((f, [r["problems"] for r in rej]), ([], [["chronology_reversed"]]))

    def test_a_correction_across_a_window_boundary_names_what_it_corrects(self):
        acc: list = []
        f, rej = build_checked(self.sent(S.GOOD_PAIR, ["C3", "C4"]), self.tl, self.names, window=["C4"], context=["C3"], narrated=set(), accepted=acc)
        self.assertEqual((len(f), rej), (1, []))
        self.assertEqual(acc, [{"claims": ["C3", "C4"], "primary": ["C4"]}])             # only C4 counts as narrated by this window
        from harness.evidence_verifier import EvidenceVerifier
        v = EvidenceVerifier(self.conv).verify_finding("historical_context", f[0], {e.ref: e for e in self.items}, {T.K}, T.tstate(*self.execs))
        self.assertEqual((v.verdict, v.reasons), ("verified", []))                         # and the independent verifier judges it against the whole timeline

    def test_rejections_carry_codes_and_claim_ids_never_the_text(self):
        f, rej = build_checked(self.sent("Kenji decided because of the weather.", ["C9"]), self.tl, self.names, window=["C9"])
        self.assertEqual(f, [])
        self.assertNotIn("weather", repr(rej))
        self.assertEqual(rej[0]["claims"], ["C9"])


# ------------------------------------------------------------------------------------------------------------------------------------ the agent: policy, failure and accounting
class AgentWindows(S.Base):
    A = ["C1", "C3", "C4"]
    B = ["C9"]

    def setUp(self):
        wa, wb = [self.by_id[i] for i in self.A], [self.by_id[i] for i in self.B]
        p = mock.patch("agents.report.agent.narrative_windows", lambda tl: [wa, wb])
        p.start()
        self.addCleanup(p.stop)

    def win_a(self):
        return self.nar((S.GOOD_C1, ["C1"]), (S.GOOD_PAIR, ["C3", "C4"]))

    def win_b(self):
        return self.nar((S.GOOD_C9, ["C9"]))

    BAD = {"paragraphs": [{"sentences": [{"text": "Kenji decided because of the weather.", "claims": ["C9"]}]}]}

    def narrative(self, rep):
        return [f for f in rep.historical_context if f.origin == "narrative_checked"]

    def test_two_windows_are_joined_in_order_with_full_accounting(self):
        rep, llm = self.report([self.win_a(), self.win_b()])
        cov = rep.coverage
        self.assertEqual([c["agent"] for c in llm.calls], ["report"] * 3)
        self.assertEqual([w.status for w in cov.windows], ["ok", "ok"])
        self.assertEqual((cov.relevant, sorted(cov.narrated), cov.rejected, cov.not_cited, cov.window_failed, cov.model_calls), (4, ["C1", "C3", "C4", "C9"], [], [], [], 2))
        self.assertEqual([f.window for f in self.narrative(rep)], [0, 1])
        text = " ".join(f.text for f in self.narrative(rep))
        self.assertLess(text.index("2026-01-02"), text.index("2026-05-01"))                  # window order is date order
        self.assertEqual(text.count("I was only exploring the possibility"), 1)                # nothing repeated
        final, res = self.verify(rep)
        self.assertEqual([v.verdict for v in res.verdicts if v.verdict == "unsupported"], [])
        self.assertEqual(len([f for f in final.historical_context if f.origin == "narrative_checked"]), 2)       # both windows' paragraphs survive the independent verifier

    def test_a_window_that_fails_is_retried_once_and_then_succeeds(self):
        rep, llm = self.report([RuntimeError("model hiccup"), self.win_a(), self.win_b()])
        self.assertEqual([(w.status, w.attempts) for w in rep.coverage.windows], [("ok", 2), ("ok", 1)])
        self.assertEqual(rep.coverage.model_calls, 3)

    def test_a_window_that_keeps_failing_is_a_recorded_gap_and_the_rest_still_stands(self):
        rep, _ = self.report([RuntimeError("timed out"), RuntimeError("timed out"), self.win_b()])
        cov = rep.coverage
        w0, w1 = cov.windows
        self.assertEqual((w0.status, w0.attempts, w1.status), ("failed", 2, "ok"))
        self.assertEqual((sorted(cov.window_failed), cov.narrated, cov.relevant), (["C1", "C3", "C4"], ["C9"], 4))
        self.assertIn("RuntimeError", w0.error)
        self.assertTrue(any("Narrative window 1 of 2" in u and "not written" in u for u in rep.uncertainties), rep.uncertainties)          # the gap is stated, not silent
        self.assertTrue(any("Narrative window 1 of 2" in u for u in rep.overview.narrative_unresolved))
        self.assertEqual((rep.overview.narrated_claims, rep.overview.relevant_claims, rep.overview.windows_incomplete), (1, 4, 1))
        self.assertEqual([f.window for f in self.narrative(rep)], [1])
        final, res = self.verify(rep)
        self.assertEqual([v.verdict for v in res.verdicts if v.verdict == "unsupported"], [])

    def test_a_window_whose_sentences_all_fail_the_checks_is_empty_after_one_retry(self):
        rep, llm = self.report([self.BAD, self.BAD, self.win_b()])
        w0 = rep.coverage.windows[0]
        self.assertEqual((w0.status, w0.attempts, w0.sentences_accepted), ("empty", 2, 0))
        self.assertEqual(sorted(rep.coverage.rejected + rep.coverage.not_cited + rep.coverage.window_failed), ["C1", "C3", "C4"])
        self.assertIn("the model answered but no sentence passed the checks", w0.error)
        self.assertNotIn("weather", repr(rep.coverage.model_dump()))                    # rejection records hold codes and claim ids only

    def test_an_exhausted_request_budget_stops_further_windows_instead_of_trying_them(self):
        rep, llm = self.report([BudgetExceeded("max_llm_calls")])
        cov = rep.coverage
        self.assertEqual([w.status for w in cov.windows], ["failed", "skipped"])
        self.assertEqual(cov.model_calls, 1)
        self.assertEqual(len(cov.window_failed), 4)
        self.assertEqual(rep.validation["mode"], "llm")                                   # the report itself still stands

    def test_the_number_of_narrative_calls_is_capped(self):
        many = [[self.by_id["C1"]]] * 20
        with mock.patch("agents.report.agent.narrative_windows", lambda tl: many):
            rep, llm = self.report([RuntimeError("x")] * 40)
        self.assertLessEqual(rep.coverage.model_calls, MAX_WINDOW_CALLS)
        self.assertTrue(all(w.status in ("failed", "skipped") for w in rep.coverage.windows))
        self.assertIn("skipped", {w.status for w in rep.coverage.windows})

    def test_every_relevant_claim_is_in_exactly_one_state(self):
        rep, _ = self.report([self.win_a(), self.nar(("Kenji decided because of the weather.", ["C9"])), self.nar(("Kenji decided because of the weather.", ["C9"]))])
        cov = rep.coverage
        states = cov.narrated + cov.rejected + cov.not_cited + cov.window_failed
        self.assertEqual(sorted(states), sorted(self.A + self.B))
        self.assertEqual(len(states), len(set(states)))
        self.assertEqual(cov.rejected, ["C9"])

    def test_coverage_notes_state_counts_and_gaps(self):
        rep, _ = self.report([RuntimeError("a"), RuntimeError("b"), self.win_b()])
        notes = coverage_notes(rep.coverage)
        self.assertEqual(len(notes), 1)
        self.assertIn("Narrative window 1 of 2", notes[0])
        self.assertEqual((rep.overview.relevant_claims, rep.overview.narrated_claims), (4, 1))
        self.assertTrue(any("1 of 4 relevant source claims is narrated" in x for x in rep.overview.narrative_verified), rep.overview.narrative_verified)

    def test_the_story_is_off_unless_asked_for(self):
        rep, llm = self.report([self.win_a(), self.win_b()])
        self.assertEqual((rep.story, rep.validation["story"]["attempted"]), ([], False))
        self.assertEqual(len(llm.calls), 3)


# ------------------------------------------------------------------------------------------------------------------------------------ the coverage tells the truth about what is IN the report
class Reconcile(S.Base):
    def two(self):
        wa, wb = [self.by_id[i] for i in ("C1", "C3", "C4")], [self.by_id["C9"]]
        p = mock.patch("agents.report.agent.narrative_windows", lambda tl: [wa, wb])
        p.start()
        self.addCleanup(p.stop)
        return self.report([self.nar((S.GOOD_C1, ["C1"]), (S.GOOD_PAIR, ["C3", "C4"])), self.nar((S.GOOD_C9, ["C9"]))])[0]

    def test_a_risky_sentence_is_isolated_so_its_clean_neighbours_survive_the_authority_rule(self):
        risky = {self.by_id["C4"].evidence_refs[0]}
        para = S.paras([(S.GOOD_C1, ["C1"]), (S.GOOD_C4, ["C4"]), (S.GOOD_C9, ["C9"])])
        plain, _ = build_checked(para, self.tl, self.names)
        split, rej = build_checked(para, self.tl, self.names, risky=risky)
        self.assertEqual((len(plain), rej), (1, []))
        self.assertEqual(len(split), 3)                                                          # clean / risky / clean
        self.assertEqual([bool(set(f.refs) & risky) for f in split], [False, True, False])
        from agents.report.validate import authority_split
        evid = []
        for e in self.items:
            evid.append(e.model_copy(update={"evidence_class": "unverified"}) if e.ref in risky else e)
        kept, moved = authority_split(split, evid, {})
        self.assertEqual((len(kept), len(moved)), (2, 1))                                           # the rule still withholds the risky finding, and only that one

    def test_a_group_that_fails_the_independent_recheck_is_not_counted_as_narrated(self):
        narrated, acc = set(), []
        with mock.patch("agents.report.semantic.check_narrative_finding", lambda f, c, n=(): ["unsupported_clause: forced"]):
            f, rej = build_checked(S.paras([(S.GOOD_C1, ["C1"])]), self.tl, self.names, window=["C1"], narrated=narrated, accepted=acc)
        self.assertEqual((f, narrated, acc), ([], set(), []))
        self.assertEqual(rej[0]["claims"], ["C1"])

    def test_claims_whose_sentence_was_withheld_later_move_out_of_narrated_with_the_reason(self):
        from agents.report.semantic import reconcile_coverage
        rep = self.two()
        keep = [f for f in rep.historical_context if f.origin != "narrative_checked" or f.window == 0]
        cov = reconcile_coverage(rep.coverage, rep.timeline, keep, "because of a test")
        self.assertEqual((sorted(cov.narrated), cov.withheld, cov.withheld_reasons), (["C1", "C3", "C4"], ["C9"], {"C9": "because of a test"}))
        self.assertEqual(reconcile_coverage(cov, rep.timeline, keep, "again"), cov)               # nothing more to move: unchanged
        self.assertEqual(rep.coverage.narrated, ["C1", "C3", "C4", "C9"])                         # the input was not modified

    def test_the_independent_verifier_withholding_a_sentence_updates_the_coverage_and_the_overview(self):
        rep = self.two()
        forged = [f.model_copy(update={"text": f.text.replace("2026-05-01", "2026-05-02")}) if f.origin == "narrative_checked" and f.window == 1 else f for f in rep.historical_context]
        final, res = self.verify(rep.model_copy(update={"historical_context": forged}))
        self.assertEqual([f.window for f in final.historical_context if f.origin == "narrative_checked"], [0])
        cov = final.coverage
        self.assertEqual((sorted(cov.narrated), cov.withheld), (["C1", "C3", "C4"], ["C9"]))
        self.assertIn("evidence verifier", cov.withheld_reasons["C9"])
        self.assertEqual((final.overview.narrated_claims, final.overview.relevant_claims), (3, 4))
        self.assertTrue(any("withheld by a later safeguard" in x for x in final.overview.narrative_unresolved), final.overview.narrative_unresolved)
        self.assertEqual(rep.coverage.narrated, ["C1", "C3", "C4", "C9"])


# ------------------------------------------------------------------------------------------------------------------------------------ raising the yield without loosening a check
class Yield(S.Base):
    def test_a_quotation_whose_tail_the_model_invented_is_cut_back_to_the_verbatim_sentences(self):
        from agents.report.semantic import repair_quotes
        text = 'On 2026-03-04 the client said "I should clarify that. I was only exploring the possibility. And I will retire at sixty-one.", as recorded.'
        out, n = repair_quotes(text, self.cited(["C4"]))
        self.assertEqual(n, 1)
        self.assertEqual(out, 'On 2026-03-04 the client said "I should clarify that. I was only exploring the possibility.", as recorded.')
        self.assertNotIn("sixty", out)                                                           # the invented tail is gone, nothing was added
        self.assertEqual(self.problems('On 2026-03-04 the client said "I should clarify that. I was only exploring the possibility.".', ["C4"]), [])

    def test_a_cut_needs_whole_sentences_and_enough_words(self):
        from agents.report.semantic import repair_quotes
        for q in ("I see. And then something that nobody said.", "I should clarify that and then some invented words follow here.", "Hmm. Nothing like it was ever said anywhere."):
            text = f'On 2026-03-04 the client said "{q}".'
            self.assertEqual(repair_quotes(text, self.cited(["C4"])), (text, 0), q)

    def test_the_cut_never_borrows_from_a_claim_that_is_not_cited(self):
        from agents.report.semantic import repair_quotes
        text = 'On 2026-03-04 the client said "I should clarify that. I was only exploring the possibility. And I will retire at sixty-one.".'
        self.assertEqual(repair_quotes(text, self.cited(["C9"]))[1], 0)

    def test_the_window_prompt_carries_an_example_made_of_the_windows_own_words_and_the_retry_is_stricter(self):
        from agents.report.semantic import narrative_user_window, window_example
        ex = window_example([self.by_id["C4"]])
        self.assertEqual(ex, 'On 2026-03-04 the client said "I should clarify that". with claims ["C4"]')
        first = narrative_user_window("q", "[C4] line", "", 0, 3, "2026-03-04", "2026-03-04", ex)
        again = narrative_user_window("q", "[C4] line", "", 0, 3, "2026-03-04", "2026-03-04", ex, retry=True)
        self.assertIn("EXAMPLE of one sentence", first)
        self.assertNotIn("PREVIOUS ANSWER WAS REJECTED", first)
        self.assertIn("PREVIOUS ANSWER WAS REJECTED", again)
        self.assertIn("part 1 of 3", first)

    def test_the_agent_sends_the_stricter_prompt_only_on_the_retry(self):
        wa, wb = [self.by_id[i] for i in ("C1", "C3", "C4")], [self.by_id["C9"]]
        with mock.patch("agents.report.agent.narrative_windows", lambda tl: [wa, wb]):
            rep, llm = self.report([self.nar(("Kenji decided because of the weather.", ["C1"])), self.nar((S.GOOD_C1, ["C1"]), (S.GOOD_PAIR, ["C3", "C4"])), self.nar((S.GOOD_C9, ["C9"]))])
        users = [c["user"] for c in llm.calls[1:]]
        self.assertEqual(["PREVIOUS ANSWER WAS REJECTED" in u for u in users], [False, True, False])
        self.assertEqual([(w.status, w.attempts) for w in rep.coverage.windows], [("ok", 2), ("ok", 1)])


# ------------------------------------------------------------------------------------------------------------------------------------ linking a correction to the statement it corrects
class WordingLink(unittest.TestCase):
    def setUp(self):
        self.note = mk(47, 58, 24, statement_kind="advisor_interpretation", wording="Before we begin, I want to confirm something. After our last meeting, my note says you had accepted the Lakeshore offer. Is that correct?")
        self.reply = mk(48, 58, 24, wording="No, and I'm glad you asked. I said I was inclined to accept. My lawyer is still negotiating, and I haven't signed the letter of intent.")
        self.fix = mk(56, 58, 24, statement_kind="advisor_interpretation", modality="correction", cues=["correction"],
                      wording="I've corrected my own record: my earlier note that you had accepted the Lakeshore offer was wrong; you are inclined to accept and have not signed.")

    def link(self, *claims):
        for i, c in enumerate(claims):
            c.turn_index = i + 1
        link_wording_corrections(list(claims))
        return claims

    def test_a_correction_is_linked_to_the_one_earlier_turn_that_states_what_it_corrects(self):
        note, reply, fix = self.link(self.note, self.reply, self.fix)
        self.assertEqual(fix.relations, [{"type": "corrects", "claim_id": "C47", "basis": "shared_wording", "phrase": "you had accepted the lakeshore offer"}])
        self.assertEqual(note.relations, [{"type": "corrected_by", "claim_id": "C56", "basis": "shared_wording", "phrase": "you had accepted the lakeshore offer"}])
        self.assertEqual((note.temporal_status, reply.relations), ("corrected", []))
        self.assertTrue(any("shared wording" in n for n in fix.notes))
        self.assertEqual(note.wording, self.note.wording)                                      # the earlier claim is marked, never edited

    def test_only_turns_worded_as_corrections_are_linked(self):
        note, reply, fix = self.link(self.note, self.reply, self.fix.model_copy(update={"modality": "statement", "cues": []}))
        self.assertEqual((note.relations, fix.relations), ([], []))

    def test_a_short_or_function_word_overlap_links_nothing(self):
        fix = self.fix.model_copy(update={"wording": "I have corrected my record: it was wrong and I am sorry about that."})
        note, reply, fix = self.link(self.note, self.reply, fix)
        self.assertEqual((note.relations, fix.relations), ([], []))

    def test_a_tie_between_two_earlier_turns_links_nothing(self):
        twin = self.note.model_copy(update={"claim_id": "C46", "wording": self.note.wording})
        a, b, fix = self.link(twin, self.note, self.fix)
        self.assertEqual((a.relations, b.relations, fix.relations), ([], [], []))
        self.assertTrue(any("more than one earlier turn" in n for n in fix.notes))

    def test_a_link_made_by_the_memory_layer_is_never_overridden(self):
        fix = self.fix.model_copy(update={"relations": [{"type": "corrects", "claim_id": "C48"}]})
        note, reply, fix = self.link(self.note, self.reply, fix)
        self.assertEqual((fix.relations, note.relations), ([{"type": "corrects", "claim_id": "C48"}], []))

    def test_a_later_turn_is_never_the_target(self):
        fix, note = self.link(self.fix, self.note)
        self.assertEqual((fix.relations, note.relations), ([], []))

    def test_the_kenji_links_still_come_from_memory_only(self):
        from services.conversations import shared  # noqa: F401  (the fixture builds the real timeline)
        S.Base.setUpClass()
        links = [r for c in S.Base.tl for r in c.relations]
        self.assertTrue(links and all(r.get("basis") is None for r in links))


if __name__ == "__main__":
    unittest.main()
