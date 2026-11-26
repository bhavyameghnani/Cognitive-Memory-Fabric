"""Phase 4A follow-up audit: regression tests for four findings checked against the source turns (data/conversations.csv).

  1  a later CONCERN (INT-121 turn 2, "I feel sick about it. What if they leave?") is not a reversal of the April 22 decision
  2  what each transaction turn actually establishes (INT-058 4/5/13, INT-066 11, INT-070 11, INT-038 9, INT-130 3, INT-150 5), time-bounded
  3  the INT-070 "your accountant and I" pronoun roles, and the speaker-unknown case
  4  a promise is not a deed (INT-070 turn 11: "I'll arrange a call" reported as "arranged a call")
The wording below is the real wording of the source turns."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_phase4a as P  # noqa: E402
from agents.report import status as ST  # noqa: E402
from agents.report import synthesis as SY  # noqa: E402
from agents.report.answer import assemble_answer  # noqa: E402
from agents.report.timeline import build_timeline  # noqa: E402
from schemas.report import EvidenceItem, TimelineClaim  # noqa: E402

A, C = P.A, P.C
timeline, by_key, NAMES, Q = P.timeline, P.by_key, P.NAMES, P.Q
D1, D2, D3, D4, BEGIN, CHECK, SAID, ACK, ACCT, BOTH = P.D1, P.D2, P.D3, P.D4, P.BEGIN, P.CHECK, P.SAID, P.ACK, P.ACCT, P.BOTH
ROOTS = ST.subject_roots(Q, ["lakeshore_offer", "business_sale"], NAMES)

# INT-121 (2026-06-03), the turns that matter for the "later concern" question
I121 = [("INT-121", 1, A, "2026-06-03", "It's Daniel. Morning, Rebecca. Let's get into it. What's on your mind first? We last spoke on May 21, about review of open items. For today I have staff announcement and key-employee retention."),
        ("INT-121", 2, C, "2026-06-03", "Lakeshore wants to announce to the staff next month. I feel sick about it. What if they leave?"),
        ("INT-121", 3, A, "2026-06-03", "That's a natural reaction. Buyers expect some turnover, and retention pools help. How many people actually matter to the operation?"),
        ("INT-121", 4, C, "2026-06-03", "Luis, the plant manager, and six supervisors. If they go, the customers go."),
        ("INT-121", 6, C, "2026-06-03", "A million, a million two. Out of the price. I'm not taking it from the staff to pay the staff."),
        ("INT-121", 8, C, "2026-06-03", "Dorothy's reserve is covered, so probably yes. I haven't asked Lakeshore yet."),
        ("INT-121", 9, A, "2026-06-03", "Then it's an idea, not a decision. They may offer to fund part of it. I'd raise it with your lawyer before the announcement, so it isn't a surprise to anyone."),
        ("INT-121", 10, C, "2026-06-03", "Fine. Not decided."),
        ("INT-121", 14, C, "2026-06-03", "Correct. Maybes are maybes.")]


def decision_after(*later_rows):
    return ST.decision_facet(timeline([D1, D2, D3, D4, *later_rows]), ROOTS)


# ------------------------------------------------------------------------------------------------------------------------------------ 1. a later concern is not a reversal
class LaterConcernIsNotReversal(unittest.TestCase):
    def test_the_real_int_121_wording_is_a_concern_and_nothing_else(self):
        k = by_key(timeline(I121))
        self.assertEqual(ST.later_signal(k["INT-121#2"]), "concern")                                            # "I feel sick about it. What if they leave?"
        for t in (4, 6, 8, 10, 14):                                                                              # the retention-pool talk: "Not decided." and "Maybes are maybes." are about the pool, not the sale
            self.assertIsNone(ST.later_signal(k[f"INT-121#{t}"]), t)

    def test_the_april_22_decision_stands_and_the_concern_is_named_as_a_concern(self):
        f = decision_after(*I121)
        self.assertEqual((f.status, f.as_of), ("established", "2026-04-22"))
        self.assertIn("a worry or a 'what if' question; a concern is not a withdrawal of the decision", f.statement)
        self.assertIn("INT-121 turn 2", f.statement)
        self.assertNotIn("worded tentatively", f.statement)                                                      # the earlier, misleading wording
        self.assertNotIn("changes the decision", f.statement)
        later = [e for e in f.evidence if e.role == "later"]
        self.assertEqual([(e.where, e.kind) for e in later], [("INT-121 turn 2", "concern")])
        self.assertEqual(later[0].excerpt, I121[1][4])

    def test_the_absence_of_a_withdrawal_is_stated_as_an_absence_and_bounded_in_time(self):
        f = decision_after(*I121)
        self.assertIn("No retrieved client statement after 22 April is worded as a withdrawal or reversal of it", f.statement)
        self.assertIn("the absence of such a statement, not proof that nothing changed", f.statement)

    def test_an_explicit_reversal_after_the_decision_is_not_hidden(self):
        f = decision_after(("INT-130", 2, C, "2026-06-11", "I've changed my mind. I'm not going to sell to Lakeshore."))
        self.assertEqual(f.status, "partial")
        self.assertIn("worded as a withdrawal or reversal of it, so it is not established as the current decision", f.statement)
        self.assertEqual([e.kind for e in f.evidence if e.role == "later"], ["withdrawal"])

    def test_renewed_doubt_reopens_the_question_without_claiming_a_reversal_either(self):
        f = decision_after(("INT-130", 2, C, "2026-06-11", "I keep having second thoughts about selling to Lakeshore."))
        self.assertEqual(f.status, "established")                                                                # not withdrawn
        self.assertIn("neither a withdrawal nor a reaffirmation, so the retrieved turns do not show whether the decision changed", f.statement)
        self.assertEqual([e.kind for e in f.evidence if e.role == "later"], ["reconsideration"])

    def test_a_withdrawal_stated_with_a_worry_is_still_a_withdrawal(self):
        row = ("INT-130", 2, C, "2026-06-11", "I'm worried, and I'm calling it off. I'm not selling to Lakeshore.")
        self.assertEqual(ST.later_signal(timeline([row])[0]), "withdrawal")

    def test_a_statement_that_is_not_about_the_subject_is_ignored(self):
        f = decision_after(("INT-130", 2, C, "2026-06-11", "I'm worried about the cottage and what if the roof leaks again?"))
        self.assertEqual([e for e in f.evidence if e.role == "later"], [])
        self.assertNotIn("concern", f.statement.replace("a concern is not", ""))

    def test_a_concern_before_the_confirmation_is_not_a_later_concern(self):
        f = ST.decision_facet(timeline([("INT-077", 2, C, "2026-04-17", "I've been lying awake over Lakeshore. What if they cut the price?"), D1, D2, D3, D4]), ROOTS)
        self.assertEqual([e for e in f.evidence if e.role == "later"], [])

    def test_the_timeline_keeps_the_decision_current_after_a_concern(self):
        mem = EvidenceItem(ref="E90", source_class="conversation_evidence", description="Episode; business_sale", memory_id="MEM-1", memory_type="episodic", interaction_id="INT-083", turn_index=2, excerpt=D2[4],
                           topic="business_sale", certainty="confirmed", status="active", verification="unverified", evidence_class="unverified", event_date="2026-04-22", client_id="CLIENT-002")
        items = [EvidenceItem(ref=f"E{i}", source_class="conversation_evidence", description="turn", interaction_id=r[0], turn_index=r[1], speaker=r[2], excerpt=r[4], event_date=r[3], client_id="CLIENT-002",
                              tool_execution_id="TX-1") for i, r in enumerate([D1, D2, D3, D4, *I121], 1)]
        d = by_key(build_timeline(items + [mem], ["business_sale"]))["INT-083#2"]
        self.assertEqual(d.temporal_status, "current")

    def test_the_answer_block_carries_the_same_precise_statement(self):
        block, _ = assemble_answer(Q, timeline([BEGIN, D1, D2, D3, D4, *I121]), {}, NAMES, ["lakeshore_offer", "business_sale"])
        dec = next(s for s in block.slots if s.key == "decision").items[0]
        self.assertEqual(dec.status, "filled")
        self.assertIn("a concern is not a withdrawal of the decision", dec.note)


# ------------------------------------------------------------------------------------------------------------------------------------ 2. what each transaction turn establishes
T_FIX = ("INT-058", 13, A, "2026-03-24", "Let me say it back to you so we don't misremember it. I've corrected my own record: my earlier note that you had accepted the Lakeshore offer was wrong; you are inclined to accept and have not signed. "
                                          "Let's pencil in Thursday, March 26 for our next meeting.")
T_EXCL66 = ("INT-066", 11, A, "2026-04-02", "That's a real consideration, and it's one that belongs in your decision alongside the price. So, to be clear: We agreed to hold off on repositioning your personal portfolio until the deal terms are final. "
                                             "From your side, you'll send over the signed exclusivity agreement and your lawyer's summary of the key terms. Let's pencil in Thursday, April 9 for our next meeting. You'll have my notes in the portal this afternoon.")
T_EXCL70 = ("INT-070", 11, A, "2026-04-09", "Quick recap: No decisions were needed today. We went through tax planning around a sale and everything is on track. From my side, I'll arrange a call with your accountant Mei Lin Chu about the tax structure of the sale. "
                                             "From your side, you'll send over the signed exclusivity agreement and your lawyer's summary of the key terms. Does Friday, April 17 work for the next one?")
T_CLOSE38 = ("INT-038", 9, A, "2026-02-19", "No. Don't run a multi-million-dollar decision around a form. I'd just tell you if a closing date firms up, so we can look at it.")
T_CLOSE130 = ("INT-130", 3, A, "2026-06-11", "Let me outline how your financial plan will look if the sale closes as expected. Your income will no longer include a salary, so we'll move to drawing from a portfolio. Your spending is about fourteen thousand five hundred a month plus the care cost.")
T_OPEN150 = ("INT-150", 5, A, "2026-06-30", "Open items: the final purchase agreement draft, your estate documents, and the sale-proceeds investment policy for your signature.")
AUDIT = [("INT-032", 4, C, "2026-02-12", "My accountant, Mei Lin Chu, sent three years of statements. Lakeshore offer talk."), T_CLOSE38, CHECK, SAID, ACK, T_FIX, T_EXCL66, T_EXCL70, T_CLOSE130, T_OPEN150]


class TransactionAudit(unittest.TestCase):
    def setUp(self):
        self.t = ST.transaction_status(timeline(AUDIT), ROOTS)

    def test_the_march_24_negative_is_one_client_statement_and_the_advisors_restatements_of_it(self):
        s = self.t.signing
        self.assertEqual((s.status, s.as_of), ("negative", "2026-03-24"))
        self.assertIn("the client's own statement (INT-058 turn 4)", s.statement)
        self.assertIn("the advisor's restatement of it (INT-058 turn 5, INT-058 turn 13)", s.statement)          # not three independent confirmations
        self.assertNotIn("3 retrieved turn(s)", s.statement)

    def test_the_march_24_negative_stays_time_bounded_and_does_not_prove_the_loi_stayed_unsigned(self):
        s = self.t.signing.statement
        self.assertIn("This is as of 24 March: nothing in the retrieved turns settles whether it was signed after that date, in either direction.", s)
        self.assertIn("No retrieved turn records it as signed.", s)
        self.assertNotIn("remained unsigned", s)
        self.assertNotIn("never signed", s)

    def test_an_advisor_only_negative_is_labelled_an_advisors_account(self):
        t = ST.transaction_status(timeline([T_FIX]))
        self.assertIn("the advisor's account (INT-058 turn 13), not a client statement, says", t.signing.statement)

    def test_a_request_to_send_the_signed_exclusivity_agreement_is_not_a_signing_or_a_sending(self):
        a = self.t.agreement
        ex = {d["document"]: d for d in a.documents}["the exclusivity agreement"]
        self.assertEqual((ex["status"], ex["keys"]), ("referenced", ["INT-066#11", "INT-070#11"]))
        self.assertIn("no retrieved turn states that it was signed, sent or received", ex["statement"])
        self.assertIn("nothing in the retrieved turns settles whether it was signed or executed after that date", ex["statement"])
        self.assertNotEqual(a.status, "established")

    def test_a_question_that_presupposes_a_signed_agreement_is_not_proof_of_execution(self):
        t = ST.transaction_status(timeline([("INT-093", 3, A, "2026-05-04", "Quick follow-up from our last meeting: were you able to send over the signed exclusivity agreement and your lawyer's summary of the key terms?")]))
        self.assertEqual(t.agreement.status, "unavailable")

    def test_a_draft_purchase_agreement_is_not_an_executed_one(self):
        p = {d["document"]: d for d in self.t.agreement.documents}["the purchase agreement"]
        self.assertEqual((p["status"], p["keys"]), ("pending", ["INT-150#5"]))
        self.assertIn("described as still to come, conditional or in draft", p["statement"])
        self.assertIn("No retrieved turn records it as signed or executed", p["statement"])

    def test_the_open_items_line_does_not_make_the_purchase_agreement_the_thing_awaiting_signature(self):
        self.assertNotIn("for your signature", self.t.agreement.statement)                                       # "for your signature" belongs to the last item, the investment policy
        self.assertEqual(self.t.agreement.documents[-1]["status"], "pending")

    def test_a_conditional_closing_mention_does_not_establish_completion_or_non_completion(self):
        c = self.t.completion
        self.assertEqual(c.status, "pending")
        self.assertEqual([e.where for e in c.evidence if e.kind == "pending"], ["INT-038 turn 9", "INT-130 turn 3"])
        self.assertIn("No retrieved turn records it as closed or completed", c.statement)
        self.assertIn("nothing in the retrieved turns settles whether it was closed or completed after that date, in either direction", c.statement)
        self.assertNotIn("did not complete", c.statement)

    def test_a_decision_to_sell_is_not_acceptance_signing_or_completion(self):
        t = ST.transaction_status(timeline([D1, D2, D3, D4]), ROOTS)
        self.assertEqual((t.decision.status, t.signing.status, t.agreement.status, t.completion.status), ("established", "unavailable", "unavailable", "unavailable"))
        self.assertIn("does not establish that any agreement was signed or that the sale completed", t.decision.statement)

    def test_every_turn_a_statement_names_is_in_that_facets_evidence(self):
        for f in (self.t.signing, self.t.agreement, self.t.completion):
            allowed = {e.where for e in f.evidence} | {k.replace("#", " turn ") for d in f.documents for k in d["keys"]}
            for w in re.findall(r"INT-\d{3} turn \d+", f.statement):
                self.assertIn(w, allowed, (f.key, w))


# ------------------------------------------------------------------------------------------------------------------------------------ 3. the pronoun roles
class PronounRoleRegression(unittest.TestCase):
    def mk(self, wording, speaker=A, kind="advisor_interpretation", i=3):
        return TimelineClaim(claim_id=f"C{i}", source="source_turn", wording=wording, speaker=speaker, speaker_role=speaker, statement_kind=kind, interaction_id="INT-070", turn_index=i, event_date="2026-04-09",
                             evidence_refs=[f"E{i}"], relevant=True, mentions_topic=True)

    def problems(self, text, *cited):
        return {p.split(":")[0] for p in SY.proposition_problems(text, list(cited), NAMES)}

    def test_int_070_turn_3_real_wording_a_faithful_paraphrase_is_accepted(self):
        c = self.mk(ACCT[4])
        self.assertEqual(SY.proposition_problems("On 9 April, the advisor said that Rebecca's accountant and the advisor should speak before any agreement was final.", [c], NAMES), [])
        self.assertEqual(SY.proposition_problems("On 9 April, the advisor said that the advisor and Rebecca's accountant should speak before an agreement was final.", [c], NAMES), [])

    def test_int_070_turn_3_a_swap_of_the_participants_is_rejected_whichever_way_it_is_worded(self):
        c = self.mk(ACCT[4])
        for text in ("On 9 April, the advisor said that Rebecca and her accountant should speak before any agreement was final.",
                     "On 9 April, the advisor said that Rebecca's accountant and Rebecca should speak before any agreement was final.",
                     "On 9 April, the advisor said that the client and her accountant should speak before any agreement was final."):
            self.assertIn("role_swap", self.problems(text, c), text)

    def test_int_070_turn_6_the_client_speaking_of_both_of_them_keeps_both_roles(self):
        c = self.mk(BOTH[4], speaker=C, kind="client_statement", i=6)
        self.assertIn("role_swap", self.problems("On 9 April, Rebecca said that she would like Mei Lin to agree before she would sign.", c))
        self.assertEqual(SY.proposition_problems("On 9 April, Rebecca said that she would like both the advisor and Mei Lin to agree before she would sign.", [c], NAMES), [])

    def test_a_line_whose_speaker_is_not_established_cannot_be_paraphrased_where_i_or_you_matter(self):
        c = self.mk(ACCT[4], speaker="Unknown", kind="unattributed")
        got = {p.split(":")[0] for p in SY.role_problems("The advisor said that Rebecca's accountant and the advisor should speak before any agreement was final.", [c], NAMES)}
        self.assertEqual(got, {"role_unknown"})
        mixed = [self.mk(ACCT[4]), self.mk(BOTH[4], speaker=C, kind="client_statement", i=6)]
        self.assertEqual({p.split(":")[0] for p in SY.role_problems("The advisor said that the accountant should speak with both of them before anyone signed.", mixed, NAMES)}, {"role_unknown"})

    def test_an_unknown_speaker_without_i_or_you_is_not_rejected_for_roles(self):
        c = self.mk("How a sale is structured changes the tax a great deal.", speaker="Unknown", kind="unattributed")
        self.assertEqual(SY.role_problems("The advisor said that the structure of a sale changes the tax a great deal.", [c], NAMES), [])

    def test_ordinary_sentences_with_i_you_we_and_your_in_their_sources_are_still_accepted(self):
        cases = [("The advisor said he would model the cash at closing alone.", "From my side, I'll model the cash at closing alone under the Lakeshore offer.", A, "advisor_interpretation"),
                 ("The advisor asked Rebecca to send over the final purchase agreement draft.", "From your side, you'll send over the final purchase agreement draft.", A, "advisor_interpretation"),
                 ("Rebecca said that she had run the company for twenty-two years.", "I've run the company for twenty-two years and I honestly don't know if I want to sell.", C, "client_statement"),
                 ("Rebecca asked whether she should move her personal portfolio now.", "Since the sale looks likely, should I move my personal portfolio now?", C, "client_statement"),
                 ("The advisor said that the plan stayed as it was.", "The plan stays as it is. Today was about valuation documents.", A, "advisor_interpretation")]
        for text, wording, spk, kind in cases:
            got = self.problems("On 2 April, " + text, self.mk(wording, speaker=spk, kind=kind))
            self.assertFalse({"role_swap", "role_unknown"} & got, (text, got))

    def test_a_joint_we_is_paraphrased_only_with_both_roles_named(self):
        c = self.mk("Last time we agreed you would send over the signed exclusivity agreement and your lawyer's summary of the key terms.")
        self.assertIn("role_swap", self.problems("On 9 April, the advisor said that Rebecca had agreed to send the summary.", c))
        self.assertNotIn("role_swap", self.problems("On 9 April, the advisor said that the advisor and Rebecca had agreed that she would send the summary of the key terms.", c))


# ------------------------------------------------------------------------------------------------------------------------------------ 4. a promise is not a deed
class PromiseNotDeed(unittest.TestCase):
    def mk(self, wording=T_EXCL70[4], i=11):
        return TimelineClaim(claim_id=f"C{i}", source="source_turn", wording=wording, speaker=A, speaker_role=A, statement_kind="advisor_interpretation", interaction_id="INT-070", turn_index=i,
                             event_date="2026-04-09", evidence_refs=[f"E{i}"], relevant=True, mentions_topic=True)

    def test_the_real_int_070_sentence_that_reported_a_promise_as_done_is_rejected(self):
        bad = "On 9 April, the advisor arranged a call with the client's accountant and reminded the client to send over the signed exclusivity agreement and lawyer's summary."
        self.assertIn("tense_shift", {p.split(":")[0] for p in SY.proposition_problems(bad, [self.mk()], NAMES)})

    def test_the_same_content_kept_as_a_promise_is_accepted(self):
        for good in ("On 9 April, the advisor said he would arrange a call with the client's accountant and asked the client to send over the signed exclusivity agreement and lawyer's summary.",
                     "On 9 April, the advisor said he planned to arrange a call with Rebecca's accountant about the tax structure of the sale."):
            self.assertEqual({p.split(":")[0] for p in SY.proposition_problems(good, [self.mk()], NAMES)} & {"tense_shift"}, set(), good)

    def test_a_deed_the_source_states_as_done_is_not_flagged(self):
        c = self.mk("I had taken the action to arrange a call with your accountant Mei Lin Chu about the tax structure of the sale. I spoke with Mei Lin this morning.", i=7)
        self.assertEqual(SY.tense_problems("On 20 April, the advisor said he had arranged a call with Rebecca's accountant about the tax structure of the sale.", [c], NAMES), [])

    def test_a_promise_and_the_same_verb_done_in_the_same_source_is_not_flagged(self):
        c = self.mk("I'll send the model tomorrow. I sent the earlier draft on Monday.", i=8)
        self.assertEqual(SY.tense_problems("The advisor said he sent the earlier draft on Monday.", [c], NAMES), [])

    def test_the_repair_hints_name_the_new_rules(self):
        h = SY.repair_hints(["tense_shift", "role_unknown"], NAMES)
        self.assertIn("a promise is not a deed", h)
        self.assertIn("skip the line", h)


if __name__ == "__main__":
    unittest.main()
