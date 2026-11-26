"""Phase 4A: report correctness and question answering.  Pure code, scripted models only; nothing here calls a live model.

  A  the question-answering executive block: five slots, chosen by relevance to the subject, never by position (agents/report/answer.py)
  B  the client's decision, signing, execution of an agreement and completion as four independent facts; a decision CONFIRMED by the client (agents/report/status.py)
  C  informativeness: a sentence must say something (agents/report/synthesis.py)
  D  correction display: what was recorded, what the client said, how the advisor corrected it (agents/report/corrections.py)
  E  the pronoun-role safeguard: who is "I" and who is "you" (agents/report/synthesis.py)

The Rebecca rows below are the real wording of the source turns (data/conversations.csv) wherever the report depends on them; the rest is neutral filler that only gives the claims a meeting around them."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_phase3 as P3  # noqa: E402
import test_report_semantic as S  # noqa: E402
import test_report_timeline as T  # noqa: E402
from agents.report import corrections as CO  # noqa: E402
from agents.report import status as ST  # noqa: E402
from agents.report import synthesis as SY  # noqa: E402
from agents.report.answer import assemble_answer  # noqa: E402
from agents.report.timeline import TAG, build_timeline  # noqa: E402
from schemas.report import EvidenceItem, TimelineClaim  # noqa: E402

A, C = "Advisor", "Client"
Q = "What is the history of Rebecca's decision about the Lakeshore company sale? Was the offer ever accepted, and what did Rebecca actually say versus what was recorded?"
NAMES = ["Rebecca"]

# (interaction, turn, speaker, date, wording)
BEGIN = ("INT-002", 4, C, "2026-01-02", "A private equity group called Lakeshore Partners contacted me about acquiring Holloway Precision Packaging.")
HOLIDAY = ("INT-001", 2, A, "2026-01-02", "Welcome back to the office. How was the holiday, and are the children well after the trip?")
AGENDA = ("INT-035", 2, C, "2026-02-17", "Fine. Net proceeds and the people side of a sale first.")
CHECK = ("INT-058", 3, A, "2026-03-24", "Before we begin, I want to confirm something. After our last meeting, my note says you had accepted the Lakeshore offer. Is that correct?")
SAID = ("INT-058", 4, C, "2026-03-24", "No, and I'm glad you asked. I said I was inclined to accept. My lawyer is still negotiating, and I haven't signed the letter of intent.")
ACK = ("INT-058", 5, A, "2026-03-24", "Thank you. That's an important distinction, and my note overstated it. I'll correct it so it says you are considering the offer and have not signed.")
FIX = ("INT-058", 13, A, "2026-03-24", "Let me say it back to you so we don't misremember it. I've corrected my own record: my earlier note that you had accepted the Lakeshore offer was wrong; "
                                         "you are inclined to accept and have not signed. Let's pencil in Thursday, March 26 for our next meeting.")
EXCL0 = ("INT-066", 1, A, "2026-04-02", "For today I have the exclusivity period and why to wait on personal moves while the Lakeshore offer is open.")
EXCL = ("INT-066", 11, A, "2026-04-02", "From your side, you'll send over the signed exclusivity agreement and your lawyer's summary of the key terms.")
ACCT = ("INT-070", 3, A, "2026-04-09", "Your accountant and I should speak before any agreement is final. How a sale is structured changes the tax a great deal.")
BOTH = ("INT-070", 6, C, "2026-04-09", "Please speak to Mei Lin. I'd like both of you to agree before I sign.")
DOUBT = ("INT-078", 2, C, "2026-04-20", "I've been thinking, and I'm not sure about selling now. Revenue is up this quarter, and maybe I should wait two years.")
D1 = ("INT-083", 1, A, "2026-04-22", "Morning, Rebecca. Let's get into it. What's on your mind first? On April 20 we covered second thoughts about timing; today the topic is decision on the Lakeshore sale.")
D2 = ("INT-083", 2, C, "2026-04-22", "I've decided. I'm going to sell to Lakeshore. I want the rollover reduced to ten percent, and I want written protections for the staff for at least two years.")
D3 = ("INT-083", 3, A, "2026-04-22", "Let me repeat it to be sure. You've decided to proceed with the sale, subject to those two conditions, and you want me to treat that as a firm decision.")
D4 = ("INT-083", 4, C, "2026-04-22", "Yes. The reasons are simple. Dorothy's care needs certainty, and I've been carrying this business alone for twenty-two years. Waiting two years doesn't remove either problem.")
CLOSING = ("INT-130", 3, A, "2026-06-11", "Let me outline how your financial plan will look if the sale closes as expected.")
WILL = ("INT-138", 3, A, "2026-06-18", "Please send over your current will and trust documents for review before the end of the month.")
OPEN = ("INT-150", 5, A, "2026-06-30", "Open items: the final purchase agreement draft, your estate documents, and the sale-proceeds investment policy for your signature.")

REBECCA = [HOLIDAY, BEGIN, AGENDA, CHECK, SAID, ACK, FIX, EXCL0, EXCL, ACCT, BOTH, DOUBT, D1, D2, D3, D4, CLOSING, WILL, OPEN]


def timeline(rows, topics=("lakeshore_offer", "business_sale")) -> list[TimelineClaim]:
    items = [EvidenceItem(ref=f"E{i}", source_class="conversation_evidence", description=f"Source conversation turn {r[0]} turn {r[1]}", interaction_id=r[0], turn_index=r[1], speaker=r[2], excerpt=r[4],
                          event_date=r[3], client_id="CLIENT-002", tool_execution_id="TX-1") for i, r in enumerate(rows, 1)]
    return build_timeline(items, list(topics))


def key(r) -> str:
    return f"{r[0]}#{r[1]}"


def by_key(claims):
    return {ST.key_of(c): c for c in claims}


# the sentences a careful model writes for the lines above (each is checked below by the same rules the report uses)
SENT = {
    key(BEGIN): "On 2 January, Rebecca said that a private equity group called Lakeshore Partners had contacted her about acquiring Holloway Precision Packaging.",
    key(DOUBT): "On 20 April, Rebecca said she was not sure about selling now, given revenue was up this quarter.",
    key(FIX): "On 24 March, the advisor corrected his own record: Rebecca was inclined to accept the offer but had not signed.",
    key(D2): "On 22 April, Rebecca said she had decided to sell to Lakeshore and wanted a reduced rollover and staff protections.",
    key(WILL): "On 18 June, the advisor reminded Rebecca to send over her current will and trust documents for review.",
}


# ------------------------------------------------------------------------------------------------------------------------------------ B: the decision, and the facets kept apart
class DecisionConfirmation(unittest.TestCase):
    def test_rebecca_april_22_is_confirmed_through_the_advisors_restatement_and_her_reply(self):
        tl = timeline(REBECCA)
        k = by_key(tl)
        d, r, c = k["INT-083#2"], k["INT-083#3"], k["INT-083#4"]
        self.assertEqual([x.decision_link["role"] for x in (d, r, c)], ["decision", "restatement", "confirmation"])
        self.assertEqual(d.decision_link["basis"], "client_stated_restated_confirmed")
        f = ST.decision_facet(tl, ST.subject_roots(Q, ["lakeshore_offer", "business_sale"], NAMES))
        self.assertEqual(f.status, "established")
        self.assertEqual([(e.role, e.where, e.speaker) for e in f.evidence],
                         [("decision", "INT-083 turn 2", TAG["client_statement"]), ("restatement", "INT-083 turn 3", TAG["advisor_reported"]), ("confirmation", "INT-083 turn 4", TAG["client_statement"])])
        self.assertIn("does not establish that any agreement was signed or that the sale completed", f.statement)
        self.assertEqual(f.as_of, "2026-04-22")

    def test_a_confirmed_decision_with_a_stored_memory_slot_is_the_current_position_of_that_slot(self):
        mem = EvidenceItem(ref="E90", source_class="conversation_evidence", description="Episode; business_sale", memory_id="MEM-1", memory_type="episodic", interaction_id="INT-083", turn_index=2, excerpt=D2[4],
                           topic="business_sale", certainty="confirmed", status="active", verification="unverified", evidence_class="unverified", event_date="2026-04-22", client_id="CLIENT-002")
        items = [EvidenceItem(ref=f"E{i}", source_class="conversation_evidence", description="turn", interaction_id=r[0], turn_index=r[1], speaker=r[2], excerpt=r[4], event_date=r[3], client_id="CLIENT-002",
                              tool_execution_id="TX-1") for i, r in enumerate([D1, D2, D3, D4], 1)]
        tl = build_timeline(items + [mem], ["business_sale"])
        d = by_key(tl)["INT-083#2"]
        self.assertFalse(d.confirmed_client_decision)                                                          # the stored memory is unverified: that verdict is untouched
        self.assertEqual(d.temporal_status, "current")                                                         # the client's explicit confirmation in the retrieved turns is what makes it current
        tl2 = build_timeline(items[:2] + [mem], ["business_sale"])                                             # without the restatement and the reply it stays unresolved
        self.assertEqual(by_key(tl2)["INT-083#2"].temporal_status, "unresolved")

    def test_the_decision_keeps_the_exact_wording_of_each_turn(self):
        f = ST.decision_facet(timeline(REBECCA), ST.subject_roots(Q, ["lakeshore_offer", "business_sale"], NAMES))
        self.assertEqual([e.excerpt for e in f.evidence], [D2[4], D3[4], D4[4]])

    def test_an_isolated_yes_establishes_nothing(self):
        rows = [("INT-083", 9, A, "2026-04-22", "Good question. It is the largest holding and the simplest way to own the market at very low cost."), ("INT-083", 10, C, "2026-04-22", "Yes. That makes sense to me.")]
        tl = timeline(rows)
        self.assertTrue(all(c.decision_link is None for c in tl))
        self.assertEqual(ST.decision_facet(tl).status, "unavailable")

    def test_a_restatement_the_client_does_not_confirm_establishes_nothing(self):
        for reply in ("Not exactly. I need to think about the staff protections first.", "Let me think about it.", "Yes, but I want to think about the rollover."):
            tl = timeline([D2, D3, ("INT-083", 4, C, "2026-04-22", reply)])
            self.assertTrue(all(c.decision_link is None for c in tl), reply)
            self.assertEqual(ST.decision_facet(tl).status, "partial", reply)                                    # her own decision-worded statement is shown, but NOT as confirmed

    def test_a_question_is_not_a_restatement(self):
        tl = timeline([D2, ("INT-083", 3, A, "2026-04-22", "Have you decided to proceed with the sale on those two conditions?"), ("INT-083", 4, C, "2026-04-22", "Yes.")])
        self.assertTrue(all(c.decision_link is None for c in tl))

    def test_a_restatement_that_adds_a_figure_is_not_an_accurate_restatement(self):
        tl = timeline([D2, ("INT-083", 3, A, "2026-04-22", "You've decided to sell to Lakeshore for twenty-two million with the rollover cut to ten percent."), ("INT-083", 4, C, "2026-04-22", "Yes.")])
        self.assertTrue(all(c.decision_link is None for c in tl))

    def test_a_restatement_about_something_else_is_not_a_restatement_of_this_decision(self):
        tl = timeline([D2, ("INT-083", 3, A, "2026-04-22", "You've decided to set aside a care reserve for your mother, invested conservatively."), ("INT-083", 4, C, "2026-04-22", "Yes.")])
        self.assertTrue(all(c.decision_link is None for c in tl))

    def test_a_tentative_statement_is_never_promoted_to_a_confirmed_decision(self):
        rows = [("INT-083", 2, C, "2026-04-22", "I'm inclined to sell to Lakeshore, but I'm not sure yet."), ("INT-083", 3, A, "2026-04-22", "So you've decided to sell to Lakeshore, is that right?"), ("INT-083", 4, C, "2026-04-22", "Yes.")]
        tl = timeline(rows)
        self.assertTrue(all(c.decision_link is None for c in tl))
        f = ST.decision_facet(tl)
        self.assertEqual(f.status, "partial")
        self.assertIn("tentative", f.statement)

    def test_the_three_turns_must_be_adjacent_and_in_one_meeting(self):
        tl = timeline([D2, D3, ("INT-084", 4, C, "2026-04-23", "Yes.")])                                      # the reply is in another meeting
        self.assertTrue(all(c.decision_link is None for c in tl))
        tl = timeline([D2, D3, ("INT-083", 6, C, "2026-04-22", "Yes.")])                                      # not the next turn
        self.assertTrue(all(c.decision_link is None for c in tl))

    def test_a_restated_and_confirmed_decision_without_the_clients_own_statement_is_confirmed_as_that(self):
        tl = timeline([("INT-099", 14, A, "2026-05-08", "So, to be clear: You've decided to sell to Lakeshore with the rollover cut to ten percent."), ("INT-099", 15, C, "2026-05-08", "Yes.")])
        f = ST.decision_facet(tl)
        self.assertEqual(f.status, "established")
        self.assertIn("the client's own first statement of it was not retrieved", f.statement)
        self.assertEqual([e.role for e in f.evidence], ["restatement", "confirmation"])


class IndependentFacets(unittest.TestCase):
    def setUp(self):
        self.tl = timeline(REBECCA)
        self.t = ST.transaction_status(self.tl, ST.subject_roots(Q, ["lakeshore_offer", "business_sale"], NAMES))

    def test_a_confirmed_decision_is_not_a_signing_an_execution_or_a_completion(self):
        t = self.t
        self.assertEqual((t.decision.status, t.signing.status, t.agreement.status, t.completion.status), ("established", "negative", "referenced", "pending"))
        self.assertNotIn("signed", t.decision.statement.replace("was signed", ""))                              # the decision statement says nothing about a signing except to deny that it proves one

    def test_signing_is_negative_as_of_the_clients_own_statement_and_nothing_later_says_it_was_signed(self):
        s = self.t.signing
        self.assertEqual((s.status, s.as_of), ("negative", "2026-03-24"))
        self.assertIn("No retrieved turn records it as signed", s.statement)
        self.assertTrue(any(e.where == "INT-058 turn 4" and "haven't signed the letter of intent" in e.excerpt for e in s.evidence))

    def test_a_signed_exclusivity_agreement_requested_by_the_advisor_is_referenced_not_established(self):
        a = self.t.agreement
        self.assertEqual(a.status, "referenced")
        self.assertIn("only in requests, plans or questions", a.statement)
        self.assertIn("no retrieved turn states that it was signed, sent or received", a.statement)
        docs = {d["document"]: d["status"] for d in a.documents}
        self.assertEqual(docs, {"the exclusivity agreement": "referenced", "the purchase agreement": "pending"})
        self.assertEqual(self.t.signing.status, "negative")                                                      # and it is not a signed letter of intent

    def test_an_exclusivity_agreement_that_was_signed_does_not_make_the_letter_of_intent_signed(self):
        tl = timeline([SAID, ("INT-080", 2, C, "2026-04-12", "I signed the exclusivity agreement on Tuesday.")])
        t = ST.transaction_status(tl)
        self.assertEqual((t.agreement.status, t.signing.status), ("established", "negative"))
        self.assertEqual(t.completion.status, "unavailable")

    def test_a_signed_letter_of_intent_is_not_a_completed_sale(self):
        t = ST.transaction_status(timeline([("INT-080", 2, C, "2026-04-12", "We signed the letter of intent on Friday.")]))
        self.assertEqual((t.signing.status, t.completion.status, t.agreement.status), ("established", "unavailable", "unavailable"))

    def test_a_completed_sale_is_stated_only_by_a_turn_that_says_so(self):
        t = ST.transaction_status(timeline([("INT-160", 2, A, "2026-07-02", "The sale closed on Friday and the proceeds arrived on Monday.")]))
        self.assertEqual(t.completion.status, "established")
        self.assertEqual(t.signing.status, "unavailable")

    def test_cash_at_closing_is_a_term_not_a_closing(self):
        t = ST.transaction_status(timeline([("INT-058", 7, A, "2026-03-24", "Cash at closing is about thirteen point five million after tax and fees.")]))
        self.assertEqual(t.completion.status, "unavailable")

    def test_a_scheduled_closing_is_pending_not_completed(self):
        t = ST.transaction_status(timeline([("INT-126", 6, C, "2026-06-08", "Closing in September, after the regulatory waiting period.")]))
        self.assertEqual(t.completion.status, "pending")
        self.assertIn("No retrieved turn records it as closed or completed", t.completion.statement)

    def test_a_question_about_signing_establishes_nothing_and_is_not_a_negative_finding_either(self):
        t = ST.transaction_status(timeline([("INT-052", 3, A, "2026-03-11", "That's a significant step. Have you signed anything?")]))
        self.assertEqual(t.signing.status, "unavailable")
        self.assertIn("absence of evidence, not a finding that it was not signed", t.signing.statement)

    def test_unavailable_and_negative_are_different_statuses_with_different_statements(self):
        none = ST.transaction_status(timeline([BEGIN]))
        self.assertEqual({f.status for f in (none.signing, none.agreement, none.completion)}, {"unavailable"})
        self.assertNotEqual(none.signing.statement, self.t.signing.statement)

    def test_the_legacy_outcome_no_longer_calls_a_request_for_a_signed_document_a_signing_event(self):
        o = SY.outcome_lines(self.tl)
        self.assertEqual([m["kind"] for m in o["mentions"] if "exclusivity" in m["excerpt"]], ["pending"])
        self.assertNotIn("without hedging", " ".join(o["not_established"]))

    def test_the_tag_vocabulary_matches_the_timelines(self):
        self.assertEqual(ST.TAG, TAG)


# ------------------------------------------------------------------------------------------------------------------------------------ A: the question-answering executive block
class AnswerSlots(unittest.TestCase):
    def setUp(self):
        self.tl = timeline(REBECCA)
        self.block, self.exec = assemble_answer(Q, self.tl, dict(SENT), NAMES, ["lakeshore_offer", "business_sale"])
        self.slot = {s.key: s for s in self.block.slots}

    def test_the_sentences_used_are_themselves_valid_propositions(self):
        k = by_key(self.tl)
        for kk, text in SENT.items():
            self.assertEqual(SY.proposition_problems(text, [k[kk]], NAMES), [], kk)

    def test_the_five_slots_cover_the_question(self):
        self.assertEqual([s.key for s in self.block.slots], ["beginning", "acceptance", "corrections", "decision", "transaction"])
        self.assertEqual({k: v.status for k, v in self.slot.items()}, {"beginning": "filled", "acceptance": "negative", "corrections": "filled", "decision": "filled", "transaction": "negative"})

    def test_the_beginning_is_the_earliest_record_about_the_subject_not_the_earliest_record(self):
        item = self.slot["beginning"].items[0]
        self.assertEqual(item.evidence[0].where, "INT-002 turn 4")                                              # the holiday small talk of the same day is earlier but is not about the sale
        self.assertEqual(item.sentences[0].text, SENT[key(BEGIN)])

    def test_an_unrelated_event_never_appears_even_when_a_sentence_for_it_exists(self):
        text = repr(self.block.model_dump()) + (self.exec.text if self.exec else "")
        self.assertNotIn("will and trust", text)
        self.assertNotIn("INT-138", text)
        self.assertIsNotNone(self.exec)
        self.assertNotIn(SENT[key(WILL)], [s.text for s in self.exec.narrative])

    def test_selection_does_not_depend_on_position(self):
        shuffled = [WILL, HOLIDAY] + [r for r in REBECCA if r not in (WILL, HOLIDAY)]
        block, _ = assemble_answer(Q, timeline(shuffled), dict(SENT), NAMES, ["lakeshore_offer", "business_sale"])
        self.assertEqual([(s.key, s.status) for s in block.slots], [(s.key, s.status) for s in self.block.slots])

    def test_the_decision_slot_is_the_confirmed_decision_with_its_three_turns_and_a_validated_sentence(self):
        it = self.slot["decision"].items[0]
        self.assertEqual(it.status, "filled")
        self.assertEqual(it.sentences[0].text, SENT[key(D2)])
        self.assertEqual([e.where for e in it.evidence], ["INT-083 turn 2", "INT-083 turn 3", "INT-083 turn 4"])
        self.assertIn("does not establish that any agreement was signed", it.note)

    def test_acceptance_separates_what_the_record_says_from_what_it_does_not_have(self):
        by = {it.label.split(" (")[0]: it for it in self.slot["acceptance"].items}
        self.assertEqual(by["Had not accepted or signed"].status, "negative")
        self.assertEqual(by["Had not accepted or signed"].evidence[0].where, "INT-058 turn 4")
        self.assertEqual(by["Hesitation before the decision"].status, "partial")
        self.assertEqual((by["Accepted"].status, by["Rejected"].status), ("unavailable", "unavailable"))
        self.assertIn("absence of evidence", by["Accepted"].note)
        self.assertIn("is not the same as accepting or signing an offer", by["Accepted"].note)

    def test_the_transaction_slot_reports_each_facet_on_its_own(self):
        labels = {it.label.split(":")[0]: it.status for it in self.slot["transaction"].items}
        self.assertEqual(labels, {"Signing of the letter of intent or offer": "negative", "Execution of the exclusivity and purchase agreements": "partial", "Completion of the sale": "partial"})

    def test_every_item_carries_the_exact_wording_speaker_and_place_of_its_sources(self):
        k = by_key(self.tl)
        for s in self.block.slots:
            for it in s.items:
                for e in it.evidence:
                    self.assertIn(e.key, k)
                    self.assertTrue(e.excerpt.rstrip("…") in k[e.key].wording, (s.key, e.excerpt))             # exact wording (or one exact sentence of it), never a paraphrase
                    self.assertEqual(e.speaker, TAG[k[e.key].statement_kind])
                    self.assertEqual(e.where, f"{k[e.key].interaction_id} turn {k[e.key].turn_index}")

    def test_the_executive_finding_is_made_only_of_validated_sentences(self):
        self.assertEqual((self.exec.window, self.exec.origin), (-1, "synthesis_checked"))
        used = {s.text for s in self.exec.narrative}
        self.assertEqual(used, {SENT[key(r)] for r in (BEGIN, DOUBT, FIX, D2)})
        self.assertEqual(SY.check_synthesis_finding(self.exec, self.tl, NAMES), [])

    def test_a_sentence_that_fails_the_checks_is_dropped_and_the_slot_shows_the_exact_wording_instead(self):
        bad = dict(SENT)
        bad[key(D2)] = "On 22 April, Rebecca said she had decided to sell to Lakeshore because of Dorothy's health."
        block, f = assemble_answer(Q, self.tl, bad, NAMES, ["lakeshore_offer", "business_sale"])
        dec = next(s for s in block.slots if s.key == "decision").items[0]
        self.assertEqual(dec.sentences, [])
        self.assertEqual(dec.evidence[0].excerpt, D2[4])
        self.assertNotIn("because of Dorothy", f.text)

    def test_with_no_sentences_at_all_the_block_still_answers_from_the_exact_wording(self):
        block, f = assemble_answer(Q, self.tl, {}, NAMES, ["lakeshore_offer", "business_sale"])
        self.assertIsNone(f)
        self.assertEqual({s.key: s.status for s in block.slots}["decision"], "filled")
        self.assertTrue(all(it.sentences == [] for s in block.slots for it in s.items))

    def test_a_subject_that_is_not_a_transaction_leaves_out_the_transaction_slots(self):
        rows = [("INT-027", 2, C, "2026-02-09", "Let's say I'm leaning toward 60 for now, but I'm not committing to it."), ("INT-090", 2, C, "2026-05-01", "We've decided: 63.")]
        block, _ = assemble_answer("What is the history of Kenji's retirement age?", timeline(rows, ["retirement_age"]), {}, ["Kenji"], ["retirement_age"])
        self.assertEqual(block.not_applicable, ["acceptance", "transaction"])
        self.assertEqual([s.key for s in block.slots], ["beginning", "corrections", "decision"])

    def test_a_tentative_decision_is_shown_as_not_confirmed_and_never_as_the_latest_confirmed_decision(self):
        rows = [BEGIN, ("INT-083", 2, C, "2026-04-22", "I'm inclined to sell to Lakeshore, but I'm not sure yet.")]
        block, _ = assemble_answer(Q, timeline(rows), {}, NAMES, ["lakeshore_offer", "business_sale"])
        dec = next(s for s in block.slots if s.key == "decision")
        self.assertEqual(dec.status, "partial")
        self.assertIn("NOT confirmed", dec.items[0].label)

    def test_no_subject_evidence_at_all_is_an_unavailable_slot_with_a_reason_not_an_invented_answer(self):
        block, f = assemble_answer(Q, timeline([HOLIDAY]), {}, NAMES, ["lakeshore_offer", "business_sale"])
        slot = {s.key: s for s in block.slots}
        self.assertEqual((slot["beginning"].status, slot["decision"].status, slot["corrections"].status), ("unavailable", "unavailable", "unavailable"))
        self.assertIn("absence of evidence", slot["corrections"].reason)
        self.assertIsNone(f)

    def test_the_block_is_deterministic(self):
        again, _ = assemble_answer(Q, timeline(REBECCA), dict(SENT), NAMES, ["lakeshore_offer", "business_sale"])
        self.assertEqual(again.model_dump(), self.block.model_dump())


# ------------------------------------------------------------------------------------------------------------------------------------ C: informativeness
class Informativeness(unittest.TestCase):
    def mk(self, wording, speaker=C, kind="client_statement", modality="statement"):
        return TimelineClaim(claim_id="C1", source="source_turn", wording=wording, speaker=speaker, speaker_role=speaker, statement_kind=kind, modality=modality, interaction_id="INT-035", turn_index=2,
                             event_date="2026-02-17", evidence_refs=["E1"], relevant=True, mentions_topic=True)

    def judge(self, text, wording, **kw):
        c = self.mk(wording, **kw)
        return {p.split(":")[0] for p in SY.proposition_problems(text, [c], NAMES)}

    def test_the_int_035_agenda_reply_is_rejected(self):
        got = self.judge("On 17 February, Rebecca said that fine, net proceeds and the people side of a sale first.", AGENDA[4])
        self.assertTrue({"empty_acknowledgement", "filler_clause"} <= got, got)

    def test_the_advisor_sentence_without_an_antecedent_is_rejected(self):
        wording = "It is. The more important question is what it would be like afterward."
        got = self.judge("The advisor said that it is, the more important question is what it would be like afterward.", wording, speaker=A, kind="advisor_interpretation")
        self.assertIn("elliptical_reply", got)

    def test_a_bare_pronoun_with_nothing_to_refer_to_is_rejected(self):
        self.assertIn("no_antecedent", self.judge("Rebecca said that it was lower than she expected, but still life-changing.", "That's lower than I expected, but still a life-changing number."))

    def test_a_clause_with_no_verb_is_a_fragment(self):
        self.assertIn("fragment", self.judge("Rebecca said that the people side of a sale first.", "The people side of a sale first, please, and the net proceeds."))

    def test_an_empty_acknowledgement_line_is_not_in_the_pool(self):
        c = self.mk(AGENDA[4])
        self.assertTrue(SY.acknowledgement_only(c.wording))
        self.assertEqual([x.claim_id for x in SY.synthesis_pool([c])], [])

    def test_ordinary_statements_are_kept(self):
        cases = [("Rebecca said she was not sure about selling now, given revenue was up this quarter.", DOUBT[4], dict(modality="tentative")),               # tentative
                 ("Rebecca asked whether she should move her personal portfolio now.", "Since the sale looks likely, should I move my personal portfolio now? Maybe put more into bonds?", {}),   # a question
                 ("Rebecca said she wanted written protections for the staff for at least two years.", "I want the rollover reduced to ten percent, and I want written protections for the staff for at least two years.", {}),   # a preference
                 ("The advisor corrected his own record: Rebecca was inclined to accept the offer but had not signed.", FIX[4], dict(speaker=A, kind="advisor_interpretation", modality="correction")),   # a correction
                 ("Rebecca said that Dorothy's care needs certainty.", "The reasons are simple. Dorothy's care needs certainty, and I've been carrying this business alone for twenty-two years.", {})]    # an explanation
        for text, wording, kw in cases:
            self.assertEqual({p for p in self.judge("On 1 March, " + text if False else text, wording, **kw) if p.startswith(("empty", "filler", "elliptical", "no_antecedent", "fragment"))}, set(), text)

    def test_a_valid_reply_that_opens_with_no_as_a_determiner_is_not_a_filler(self):
        self.assertEqual({p for p in self.judge("Rebecca said that no decision had been made about the offer.", "No decision has been made about the offer.") if p.startswith("filler")}, set())

    def test_a_pronoun_with_a_concrete_referent_in_the_clause_is_kept(self):
        got = self.judge("Rebecca said that it was twenty-two million at the floor.", "It was twenty-two million at the floor, no lower.")
        self.assertNotIn("no_antecedent", got)

    def test_the_other_checks_still_run_on_a_sentence_that_is_informative(self):
        got = self.judge("Rebecca said that she had decided to sell to Lakeshore because of her health.", "I'm thinking about selling to Lakeshore.", modality="tentative")
        self.assertTrue({"causal_explanation", "decision_language"} & got, got)

    def test_the_repair_hints_name_the_new_rules_without_quoting_text(self):
        h = SY.repair_hints(["filler_clause", "role_swap", "no_antecedent", "fragment", "elliptical_reply"], NAMES)
        for part in ("never with fine, yes, no or it is", "name both the advisor and the client", "say what \"it\" or \"that\" refers to", "write a full statement with a verb"):
            self.assertIn(part, h)


# ------------------------------------------------------------------------------------------------------------------------------------ D: correction display
class CorrectionDisplay(unittest.TestCase):
    def setUp(self):
        self.tl = timeline(REBECCA)
        self.recs = CO.correction_records(self.tl)

    def test_there_is_one_record_and_it_names_the_correcting_turn(self):
        self.assertEqual(len(self.recs), 1)
        r = self.recs[0]
        self.assertEqual((r.date, r.basis, r.correction.evidence.where, r.correction.evidence.speaker), ("2026-03-24", "shared_wording", "INT-058 turn 13", TAG["advisor_interpretation"]))

    def test_the_advisors_question_is_context_and_never_the_original_record(self):
        r = self.recs[0]
        self.assertEqual(r.check.evidence.where, "INT-058 turn 3")
        self.assertIn("Is that correct?", r.check.evidence.excerpt)
        self.assertNotEqual(r.original.evidence.excerpt if r.original.evidence else "", r.check.evidence.excerpt)
        self.assertNotIn("Is that correct?", r.original.evidence.excerpt if r.original.evidence else "")
        self.assertIn("a question about a record is not the record", r.check.label)

    def test_the_original_record_that_was_not_retrieved_is_marked_so_and_not_reconstructed(self):
        r = self.recs[0]
        self.assertEqual(r.original.status, "described")
        self.assertEqual(r.original.evidence.excerpt, "my earlier note that you had accepted the Lakeshore offer was wrong")        # the advisor's own words, an exact part of the correcting turn
        self.assertIn(r.original.evidence.excerpt, FIX[4])
        self.assertIn("not the note", r.original.note)
        self.assertIn("was not among the retrieved evidence", r.missing)
        self.assertIn("not reconstructed", r.missing)

    def test_what_the_client_actually_said_is_her_own_turn_with_her_attribution(self):
        r = self.recs[0]
        self.assertEqual((r.client_said.evidence.where, r.client_said.evidence.speaker), ("INT-058 turn 4", TAG["client_statement"]))
        self.assertEqual(r.client_said.evidence.excerpt, SAID[4])

    def test_the_advisors_acknowledgement_in_the_same_meeting_belongs_to_the_record(self):
        r = self.recs[0]
        self.assertEqual(r.acknowledgement.evidence.where, "INT-058 turn 5")
        self.assertIn("my note overstated it", r.acknowledgement.evidence.excerpt)

    def test_the_four_parts_are_four_different_turns_with_three_different_roles(self):
        r = self.recs[0]
        wheres = [p.evidence.where for p in (r.check, r.client_said, r.acknowledgement, r.correction)]
        self.assertEqual(len(set(wheres)), 4)
        self.assertEqual([p.role for p in (r.original, r.client_said, r.check, r.acknowledgement, r.correction)], ["original", "client_said", "check", "acknowledgement", "correction"])

    def test_when_the_earlier_record_itself_is_retrieved_it_is_shown_as_the_original(self):
        earlier = ("INT-052", 9, A, "2026-03-11", "For the file: you accepted the Lakeshore offer, and I will record it that way.")
        recs = CO.correction_records(timeline([earlier, CHECK, SAID, ACK, FIX]))
        self.assertEqual(recs[0].original.status, "retrieved")
        self.assertEqual((recs[0].original.evidence.where, recs[0].original.evidence.excerpt), ("INT-052 turn 9", earlier[4]))
        self.assertIsNone(recs[0].missing)

    def test_a_hedged_earlier_turn_is_not_taken_for_the_original_record(self):
        earlier = ("INT-052", 9, A, "2026-03-11", "You're still weighing accepting the Lakeshore offer, which I've recorded as an idea and not an instruction.")
        recs = CO.correction_records(timeline([earlier, CHECK, SAID, ACK, FIX]))
        self.assertEqual(recs[0].original.status, "described")

    def test_with_no_description_and_no_record_the_original_is_unavailable_and_nothing_is_invented(self):
        fix = ("INT-058", 13, A, "2026-03-24", "Let me say it back to you. You have not signed anything, and you accepted nothing. The Lakeshore offer is still open for your answer.")
        tl = timeline([CHECK, SAID, fix])
        tl_c = {c.claim_id: c for c in tl}
        # no linked correction exists for this wording, so there is no record at all rather than an invented one
        self.assertEqual(CO.correction_records(tl), [])
        self.assertTrue(all(c.relations == [] for c in tl_c.values()))

    def test_a_correction_that_cannot_say_what_it_corrected_leaves_the_original_unavailable(self):
        fix = ("INT-058", 13, A, "2026-03-24", "Correction to my record: it said you had accepted the Lakeshore offer, which was wrong; you are inclined to accept and have not signed.")
        recs = CO.correction_records(timeline([CHECK, SAID, ACK, fix]))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.original.status, "unavailable")
        self.assertIsNone(r.original.evidence)                                                                 # nothing is shown as the original: not the question, not a guess
        self.assertIn("was not among the retrieved evidence", r.original.note)
        self.assertIn("the original recorded position is unavailable", r.missing)
        self.assertEqual(r.check.evidence.where, "INT-058 turn 3")                                             # the question is still shown, labelled as context

    def test_the_validated_correction_links_are_unchanged(self):
        k = by_key(self.tl)
        self.assertEqual([(r["type"], r["claim_id"]) for r in k["INT-058#13"].relations], [("corrects", k["INT-058#3"].claim_id)])
        self.assertEqual(k["INT-058#13"].relations[0]["basis"], "shared_wording")
        self.assertEqual(k["INT-058#3"].temporal_status, "corrected")


# ------------------------------------------------------------------------------------------------------------------------------------ E: who is "I" and who is "you"
class PronounRoles(unittest.TestCase):
    def mk(self, wording, speaker=A, kind="advisor_interpretation"):
        return TimelineClaim(claim_id="C1", source="source_turn", wording=wording, speaker=speaker, speaker_role=speaker, statement_kind=kind, interaction_id="INT-070", turn_index=3, event_date="2026-04-09",
                             evidence_refs=["E1"], relevant=True, mentions_topic=True)

    def roles(self, text, wording, **kw):
        return SY.role_problems(text, [self.mk(wording, **kw)], NAMES)

    ACCT_LINE = ACCT[4]

    def test_the_april_9_accountant_line_must_not_swap_who_is_speaking(self):
        bad = "The advisor said that Rebecca and her accountant should speak before any agreement was final."
        got = self.roles(bad, self.ACCT_LINE)
        self.assertTrue(got and got[0].startswith("role_swap"), got)
        full = {p.split(":")[0] for p in SY.proposition_problems(bad, [self.mk(self.ACCT_LINE)], NAMES)}
        self.assertIn("role_swap", full)                                                                      # and the whole proposition check refuses it

    def test_the_correct_paraphrase_keeps_both_roles_and_is_accepted(self):
        good = "The advisor said that Rebecca's accountant and the advisor should speak before any agreement was final."
        self.assertEqual(self.roles(good, self.ACCT_LINE), [])
        self.assertEqual(SY.proposition_problems(good, [self.mk(self.ACCT_LINE)], NAMES), [])

    def test_a_paraphrase_that_gives_the_clients_part_to_the_advisor_is_refused(self):
        self.assertTrue(self.roles("The advisor said that the advisor would send over the draft.", "From your side, you'll send over the final purchase agreement draft."))

    def test_a_paraphrase_that_gives_the_advisors_part_to_the_client_is_refused(self):
        self.assertTrue(self.roles("The advisor said that Rebecca would model the cash at closing alone.", "From my side, I'll model the cash at closing alone under the Lakeshore offer."))

    def test_a_first_person_line_alone_is_not_rejected(self):
        self.assertEqual(self.roles("The advisor said that he would model the cash at closing alone.", "From my side, I'll model the cash at closing alone under the Lakeshore offer."), [])

    def test_a_second_person_line_alone_is_not_rejected(self):
        self.assertEqual(self.roles("The advisor asked Rebecca to send over the final purchase agreement draft.", "From your side, you'll send over the final purchase agreement draft."), [])

    def test_a_joint_we_requires_both_roles_in_the_sentence(self):
        line = "Last time we agreed you would send over the signed exclusivity agreement and your lawyer's summary of the key terms."
        self.assertTrue(self.roles("The advisor said that Rebecca had agreed to send the summary.", line))
        self.assertEqual(self.roles("The advisor said that the advisor and Rebecca had agreed that she would send the summary of the key terms.", line), [])

    def test_the_client_speaking_of_both_of_them_keeps_both_roles(self):
        line = BOTH[4]
        self.assertTrue(self.roles("Rebecca said that she wanted her accountant to agree before she signed.", line, speaker=C, kind="client_statement"))
        self.assertEqual(self.roles("Rebecca said that she wanted both the advisor and her accountant to agree before she signed.", line, speaker=C, kind="client_statement"), [])

    def test_a_sentence_of_the_same_turn_that_is_not_paraphrased_does_not_bind_the_paraphrase(self):
        # the turn contains "so we don't misremember it", but the correction sentence the paraphrase covers has no joint role
        self.assertEqual(self.roles(SENT[key(FIX)], FIX[4]), [])

    def test_a_pronoun_does_not_name_a_role(self):
        # "he" may be the advisor or someone else: it never satisfies a joint role
        line = "Last time we agreed you would send over the signed exclusivity agreement."
        self.assertTrue(self.roles("The advisor said that he and she had agreed that she would send it.", line))

    def test_the_reduced_wording_of_a_client_line_with_no_first_person_is_not_a_role_swap(self):
        self.assertEqual(self.roles("Rebecca said that Dorothy's care needs certainty.", "The reasons are simple. Dorothy's care needs certainty.", speaker=C, kind="client_statement"), [])


# ------------------------------------------------------------------------------------------------------------------------------------ the agent and the verifier
class AgentPhase4A(P3.Base):
    def run_agent(self, *outputs):
        a, llm = T.agent_for([T.llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[])] + list(outputs))
        rep = a.write(S.Q, self.plan, self.mem, T.tstate(*self.execs))
        return rep, llm

    def test_the_report_carries_the_answer_block_the_facets_and_the_corrections_and_adds_no_model_call(self):
        rep, llm = self.run_agent(P3.AgentSynthesis.WINDOW)
        self.assertEqual([c["agent"] for c in llm.calls], ["report"] * 2)                                      # main + one window: the answer block is code
        self.assertIsNotNone(rep.answer)
        self.assertEqual(rep.answer.not_applicable, ["acceptance", "transaction"])                              # a retirement age has no signing
        self.assertEqual([s.key for s in rep.answer.slots], ["beginning", "corrections", "decision"])
        t = rep.transaction
        self.assertEqual({t.decision.status, t.signing.status, t.agreement.status, t.completion.status} - {"unavailable", "established", "partial"}, set())
        self.assertIsInstance(rep.corrections, list)
        first = [f for f in rep.historical_context if f.origin == "synthesis_checked"][0]
        self.assertEqual(first.window, -1)
        used = {s.text for sl in rep.answer.slots for it in sl.items for s in it.sentences}
        self.assertEqual({s.text for s in first.narrative}, used)                                               # the executive finding is exactly the sentences the slots use

    def test_the_verifier_replaces_a_forged_status_with_the_one_it_derives(self):
        rep, _ = self.run_agent(P3.AgentSynthesis.WINDOW)
        forged = rep.transaction.model_copy(update={"signing": rep.transaction.signing.model_copy(update={"status": "established", "statement": "The letter of intent was signed."})})
        final, _res = self.verify(rep.model_copy(update={"transaction": forged}))
        self.assertEqual(final.transaction.signing.status, rep.transaction.signing.status)
        self.assertNotIn("The letter of intent was signed.", final.transaction.signing.statement)
        self.assertIn("transaction", final.validation["harness_phase4a"]["differs_from_agent"])

    def test_the_verifier_agrees_with_an_untampered_report(self):
        rep, _ = self.run_agent(P3.AgentSynthesis.WINDOW)
        final, _res = self.verify(rep)
        self.assertEqual(final.validation["harness_phase4a"], {"rederived": True, "differs_from_agent": []})
        self.assertEqual(final.transaction.model_dump(), rep.transaction.model_dump())
        self.assertEqual([(s.key, s.status) for s in final.answer.slots], [(s.key, s.status) for s in rep.answer.slots])

    def test_claim_numbers_alone_are_not_a_difference(self):
        # the verifier's timeline can hold fewer claims than the agent's, so C12 there may be C13 here: the identity of evidence is its turn key
        rep, _ = self.run_agent(P3.AgentSynthesis.WINDOW)
        dump = rep.transaction.model_dump()
        for f in dump.values():
            if isinstance(f, dict):
                for e in f.get("evidence", []):
                    e["claim_id"] = "C999"
        from schemas.report import TransactionStatus
        final, _res = self.verify(rep.model_copy(update={"transaction": TransactionStatus(**dump)}))
        self.assertEqual(final.validation["harness_phase4a"]["differs_from_agent"], [])
        from harness.evidence_verifier import _no_ids
        self.assertEqual(_no_ids({"a": [{"claim_id": "C1", "key": "INT-083#2", "n": {"claim_id": "C2", "x": 1}}]}), {"a": [{"key": "INT-083#2", "n": {"x": 1}}]})
        self.assertNotEqual(_no_ids({"key": "INT-083#2"}), _no_ids({"key": "INT-083#3"}))                       # the turn key is still compared

    def test_a_withheld_executive_sentence_cannot_reappear_in_a_slot(self):
        rep, _ = self.run_agent(P3.AgentSynthesis.WINDOW)
        forged = []
        for f in rep.historical_context:
            if f.window == -1:
                sn = [s.model_copy(update={"text": s.text.replace("only exploring the possibility", "planning to retire at 61 because of his health")}) for s in f.narrative]
                f = f.model_copy(update={"narrative": sn, "text": f.text.replace("only exploring the possibility", "planning to retire at 61 because of his health")})
            forged.append(f)
        final, _res = self.verify(rep.model_copy(update={"historical_context": forged}))
        shown = repr(final.answer.model_dump()) + " ".join(f.text for f in final.historical_context)
        self.assertNotIn("because of his health", shown)


if __name__ == "__main__":
    unittest.main()
