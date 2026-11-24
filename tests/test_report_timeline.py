"""B2B: evidence classification and chronological claim construction (agents/report/timeline.py, integrated in agents/report/agent.py).

Synthetic scenarios cover each classification and relation; the INTEGRATION tests at the end use the REAL Kenji turns (data/conversations.csv) and the harness's own EvidenceVerifier, so the code-written
chronology is verified by the same authority as every other finding, not by itself.  Scripted model only; no database is opened.  These are MECHANISM tests: they show that wording, attribution,
certainty and verification are carried and combined as documented, not that the wording rules are accurate on all real conversations.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_report_agent import K, agent_for, env, ex, llm_out, mem, plan, tstate  # noqa: E402
from agents.report.evidence import build_evidence  # noqa: E402
from agents.report.narrative import STORY as STORY_RX, is_narrative  # noqa: E402
from agents.report.timeline import build_timeline, modality_from_cues, text_cues, timeline_findings, timeline_summary, timeline_uncertainties  # noqa: E402
from schemas.memory import EvidenceRef, MemoryContext  # noqa: E402
from schemas.report import is_source_turn  # noqa: E402

TOPIC = "retirement_age"


def turns_exec(n, iid, date, turns):
    """One get_source_evidence execution: turns = [(turn_index, speaker, text)]."""
    return ex(n, env("get_source_evidence", {"turns": [{"interaction_id": iid, "turn_index": i, "speaker": s, "text": x} for i, s, x in turns]}, as_of=date))


def episode(mid, iid, idx, role, text, certainty, value, date, verified=False, status="active", corrects=None, conflicts=None):
    m = mem(mid, "episodic", value, status, certainty, event=date, content=f"Memory {mid}.", topic=TOPIC, ev=[EvidenceRef(interaction_id=iid, turn_index=idx, speaker=role, excerpt=text)])
    return m.model_copy(update=dict(verification="verified" if verified else "unverified", source_role=role, corrects_value=corrects))


def timeline(episodes=(), execs=(), conflicts=None):
    ctx = MemoryContext(client_id=K, episodes=list(episodes), conflicts=conflicts or [])
    items, _ = build_evidence(plan(), {K: ctx}, tstate(*execs))
    return build_timeline(items, [TOPIC]), items


def by_turn(claims, iid, idx):
    return next(c for c in claims if (c.interaction_id, c.turn_index) == (iid, idx))


# ====================================================================================================================== classification
class Classification(unittest.TestCase):
    DECISION = "We've decided: 63."

    def test_an_explicit_client_decision_with_a_verified_confirmed_memory_is_a_confirmed_decision(self):
        e = episode("EP-1", "INT-090", 2, "Client", self.DECISION, "confirmed", "63", "2026-05-01", verified=True)
        claims, _ = timeline([e], [turns_exec(1, "INT-090", "2026-05-01", [(2, "Client", self.DECISION)])])
        (c,) = claims
        self.assertEqual((c.statement_kind, c.attribution, c.modality, c.verification, c.value, c.value_source), ("client_statement", "client_direct", "decision", "verified", "63", "memory"))
        self.assertTrue(c.confirmed_client_decision)
        self.assertEqual(c.temporal_status, "current")
        self.assertEqual((c.interaction_id, c.turn_index, c.event_date, c.speaker), ("INT-090", 2, "2026-05-01", "Client"))
        self.assertEqual(c.wording, self.DECISION)                                                           # the original wording, not a paraphrase

    def test_the_same_words_with_an_unverified_memory_are_not_a_confirmed_decision_and_nothing_is_current(self):
        e = episode("EP-1", "INT-090", 2, "Client", self.DECISION, "confirmed", "63", "2026-05-01", verified=False)
        claims, _ = timeline([e], [turns_exec(1, "INT-090", "2026-05-01", [(2, "Client", self.DECISION)])])
        (c,) = claims
        self.assertEqual((c.modality, c.verification, c.confirmed_client_decision), ("decision", "unverified", False))
        self.assertEqual(c.temporal_status, "unresolved")
        self.assertTrue(any("No current position is established" in u for u in timeline_uncertainties(claims)))

    def test_wording_alone_never_makes_a_confirmed_decision(self):
        claims, _ = timeline([], [turns_exec(1, "INT-090", "2026-05-01", [(2, "Client", self.DECISION)])])
        (c,) = claims
        self.assertEqual((c.modality, c.verification, c.confirmed_client_decision, c.value), ("decision", "no_memory", False, None))
        self.assertNotEqual(c.temporal_status, "current")

    def test_a_verified_memory_without_client_attribution_is_not_a_confirmed_client_decision(self):
        """M1 caps an advisor-sourced memory at 'unconfirmed'; even if a verified 'confirmed' advisor memory were handed over, it is not the client's decision."""
        t = "You've decided to retire at 60."
        e = episode("EP-1", "INT-1", 1, "Advisor", t, "confirmed", "60", "2026-02-10", verified=True)
        claims, _ = timeline([e], [turns_exec(1, "INT-1", "2026-02-10", [(1, "Advisor", t)])])
        (c,) = claims
        self.assertEqual((c.statement_kind, c.attribution), ("advisor_reported", "advisor_reported_client"))
        self.assertFalse(c.confirmed_client_decision)                                                        # no client reply was retrieved
        self.assertTrue(any("no client reply confirming" in n for n in c.notes))

    def test_tentative_wording_is_tentative_even_next_to_decision_wording(self):
        for text in ("I was only exploring the possibility of 60.", "We've decided: 63, but don't treat it as final yet.", "Maybe I'll retire at 60, I haven't decided."):
            claims, _ = timeline([], [turns_exec(1, "INT-1", "2026-03-04", [(1, "Client", text)])])
            self.assertEqual(claims[0].modality, "tentative", text)
            self.assertNotEqual(claims[0].temporal_status, "current")
        self.assertEqual(modality_from_cues(text_cues("Suppose I retired at 60 instead.", "Client")), "hypothetical")           # a purely hypothetical statement
        self.assertIn(modality_from_cues(text_cues("What if I retire at 60?", "Client")), ("tentative", "hypothetical"))        # "what if" is also an exploratory cue in M1's lexicon: never a decision

    def test_a_tentative_client_statement_stays_tentative_whatever_the_memory_says(self):
        e = episode("EP-1", "INT-1", 1, "Client", "I was only exploring 60.", "exploratory", "60", "2026-03-04")
        claims, _ = timeline([e], [turns_exec(1, "INT-1", "2026-03-04", [(1, "Client", "I was only exploring 60.")])])
        c = claims[0]
        self.assertEqual((c.modality, c.memory_certainty, c.confirmed_client_decision, c.statement_kind), ("tentative", "exploratory", False, "client_statement"))

    def test_an_advisor_summary_is_advisor_reported_never_a_client_statement(self):
        t = "You've decided to retire at 60. I'll record that as a confirmed decision."
        claims, _ = timeline([], [turns_exec(1, "THR-002", "2026-02-10", [(1, "Advisor", t)])])
        c = claims[0]
        self.assertEqual((c.statement_kind, c.speaker_role, c.attribution), ("advisor_reported", "Advisor", "advisor_reported_client"))
        self.assertFalse(c.confirmed_client_decision)
        self.assertTrue(any("Advisor-reported, not confirmed by the client" in u for u in timeline_uncertainties(claims)))
        self.assertTrue(any("the advisor's account, not a client statement" in f.text for f in timeline_findings(claims, timeline([], [turns_exec(1, "THR-002", "2026-02-10", [(1, "Advisor", t)])])[1])))

    def test_the_clients_immediate_confirming_reply_is_recorded_but_does_not_by_itself_make_a_confirmed_decision(self):
        claims, _ = timeline([], [turns_exec(1, "INT-001", "2026-01-02", [(13, "Advisor", "You've decided to retire at 65."), (14, "Client", "Correct.")])])
        a = by_turn(claims, "INT-001", 13)
        self.assertEqual(a.client_confirmation, "INT-001#14")
        self.assertIn(by_turn(claims, "INT-001", 14).evidence_refs[0], a.evidence_refs)
        self.assertFalse(a.confirmed_client_decision)                                                          # still needs the memory layer's verified verdict
        hedged, _ = timeline([], [turns_exec(1, "INT-001", "2026-01-02", [(13, "Advisor", "You've decided to retire at 65."), (14, "Client", "Yes, but I'm still considering it.")])])
        self.assertIsNone(by_turn(hedged, "INT-001", 13).client_confirmation)                                  # a hedged reply is not a confirmation
        question, _ = timeline([], [turns_exec(1, "INT-001", "2026-01-02", [(13, "Advisor", "You've decided to retire at 65."), (14, "Client", "Maybe 65?")])])
        self.assertIsNone(by_turn(question, "INT-001", 13).client_confirmation)

    def test_an_advisor_interpretation_or_recommendation_is_not_a_decision(self):
        claims, _ = timeline([], [turns_exec(1, "INT-1", "2026-04-01", [(1, "Advisor", "I recommend retiring at 62.")])])
        c = claims[0]
        self.assertEqual((c.statement_kind, c.modality, c.confirmed_client_decision), ("advisor_interpretation", "recommendation", False))

    def test_a_withdrawn_statement_is_classified_as_withdrawn_and_is_not_a_decision(self):
        claims, _ = timeline([], [turns_exec(1, "INT-1", "2026-04-01", [(1, "Client", "We've decided: 60. Scratch that, I misspoke.")])])
        self.assertEqual(claims[0].modality, "withdrawn")
        self.assertNotEqual(claims[0].temporal_status, "current")

    def test_an_unattributable_speaker_is_not_attributed_to_the_client_or_the_advisor(self):
        claims, _ = timeline([], [turns_exec(1, "INT-1", "2026-04-01", [(1, "Third party", "We've decided: 60.")])])
        self.assertEqual((claims[0].statement_kind, claims[0].attribution), ("unattributed", "ambiguous"))


# ====================================================================================================================== relations and chronology
class Chronology(unittest.TestCase):
    ADV = "You've decided to retire at 60. I'll record that as a confirmed decision."
    FIX = "I should clarify that. I was only exploring the possibility. I haven't made that decision. 65 is still the date we're planning against."
    NOTE = "The wording of my file note was too strong."

    def scenario(self, corrects="60", verified_fix=False):
        eps = [episode("EP-ADV", "THR-002", 1, "Advisor", self.ADV, "unconfirmed", "60", "2026-02-10"),
               episode("EP-FIX", "INT-049", 4, "Client", self.FIX, "correction", "65", "2026-03-04", verified=verified_fix, corrects=corrects)]
        execs = [turns_exec(1, "THR-002", "2026-02-10", [(1, "Advisor", self.ADV)]), turns_exec(2, "INT-049", "2026-03-04", [(4, "Client", self.FIX), (5, "Advisor", self.NOTE)])]
        return timeline(eps, execs)

    def test_a_correction_marks_the_earlier_claim_corrected_and_keeps_its_wording(self):
        claims, _ = self.scenario()
        adv, fix = by_turn(claims, "THR-002", 1), by_turn(claims, "INT-049", 4)
        self.assertEqual(adv.temporal_status, "corrected")
        self.assertEqual(adv.wording, self.ADV)                                                                 # never overwritten
        self.assertIn({"type": "corrected_by", "claim_id": fix.claim_id}, adv.relations)
        self.assertIn({"type": "corrects", "claim_id": adv.claim_id}, fix.relations)
        self.assertEqual((fix.statement_kind, fix.modality), ("client_statement", "tentative"))                  # the client's own correction is the client's, hedged as spoken

    def test_the_chronology_is_ordered_by_date_then_turn_and_ids_follow_it(self):
        claims, _ = self.scenario()
        self.assertEqual([(c.event_date, c.interaction_id, c.turn_index) for c in claims], sorted((c.event_date, c.interaction_id, c.turn_index) for c in claims))
        self.assertEqual([c.claim_id for c in claims], [f"C{i}" for i in range(1, len(claims) + 1)])

    def test_the_narrative_says_what_was_corrected_when_by_whom_and_keeps_the_earlier_words(self):
        claims, items = self.scenario()
        story = " ".join(f.text for f in timeline_findings(claims, items))
        self.assertIn("In meeting THR-002 (2026-02-10), in turn 1, the advisor recorded (the advisor's account, not a client statement; later corrected in INT-049 turn 4)", story)
        self.assertIn("You've decided to retire at 60.", story)                                                  # the corrected record is still told, in its own words
        self.assertIn("Later, in meeting INT-049 (2026-03-04), in turn 4, the client said, correcting the record tentatively, (the client's own words; corrects the record in THR-002 turn 1, which stays unchanged)", story)
        self.assertLess(story.index("2026-02-10"), story.index("2026-03-04"))

    def test_the_narrative_is_in_strict_date_order_across_slots_and_paragraphs(self):
        import re
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-01", verified=True), episode("EP-B", "INT-3", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)]
        execs = [turns_exec(1, "INT-1", "2026-01-01", [(1, "Client", "We've decided: 65."), (2, "Advisor", "You've decided to retire at 65, and I'll record it.")]),
                 turns_exec(2, "INT-2", "2026-03-04", [(1, "Client", "I was only exploring the possibility of 60 for retirement."), (2, "Advisor", "The wording of my file note was too strong.")]),
                 turns_exec(3, "INT-3", "2026-05-01", [(1, "Client", "We've decided: 63.")])]
        claims, items = timeline(eps, execs)
        story = " ".join(f.text for f in timeline_findings(claims, items))
        dates = re.findall(r"meeting INT-\d+ \((\d{4}-\d{2}-\d{2})\)", story)
        self.assertEqual(len(dates), 3)                                                                          # one paragraph per meeting
        self.assertEqual(dates, sorted(dates))                                                                   # one chronology, however many topics and paragraphs

    def test_a_correction_that_names_no_value_replaces_the_latest_earlier_decision_with_a_different_value(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-01", verified=True),
               episode("EP-B", "INT-2", 1, "Client", "I've corrected my own record: it is 63.", "correction", "63", "2026-02-01", verified=True)]
        claims, _ = timeline(eps, [turns_exec(1, "INT-1", "2026-01-01", [(1, "Client", "We've decided: 65.")]), turns_exec(2, "INT-2", "2026-02-01", [(1, "Client", "I've corrected my own record: it is 63.")])])
        self.assertEqual(by_turn(claims, "INT-1", 1).temporal_status, "corrected")
        self.assertEqual(by_turn(claims, "INT-2", 1).temporal_status, "current")                                # a verified, confirmed correction is the current position

    def test_a_correction_without_a_retrieved_target_is_reported_not_guessed(self):
        eps = [episode("EP-FIX", "INT-049", 4, "Client", self.FIX, "correction", "65", "2026-03-04", corrects="60")]
        claims, _ = timeline(eps, [turns_exec(1, "INT-049", "2026-03-04", [(4, "Client", self.FIX)])])
        self.assertFalse([r for r in claims[0].relations if r["type"] == "corrects"])
        self.assertTrue(any("Correction without its target" in u for u in timeline_uncertainties(claims)))

    def test_an_unverified_correction_does_not_make_anything_current(self):
        claims, _ = self.scenario(verified_fix=False)
        self.assertNotIn("current", {c.temporal_status for c in claims})
        self.assertEqual(by_turn(claims, "INT-049", 4).confirmed_client_decision, False)

    def test_two_different_decisions_on_one_day_are_an_unreconciled_contradiction(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 60.", "confirmed", "60", "2026-03-01", verified=True),
               episode("EP-B", "INT-1", 5, "Client", "We've decided: 63.", "confirmed", "63", "2026-03-01", verified=True)]
        claims, _ = timeline(eps, [turns_exec(1, "INT-1", "2026-03-01", [(1, "Client", "We've decided: 60."), (5, "Client", "We've decided: 63.")])])
        self.assertEqual({c.temporal_status for c in claims}, {"disputed"})
        self.assertTrue(all(any(r["type"] == "contradicts" for r in c.relations) for c in claims))
        self.assertNotIn("current", {c.temporal_status for c in claims})
        self.assertTrue(any("Unreconciled contradiction" in u for u in timeline_uncertainties(claims)))

    def test_claims_sharing_an_open_conflict_contradict_each_other(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 60.", "confirmed", "60", "2026-03-01"), episode("EP-B", "INT-2", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-04-01")]
        claims, _ = timeline(eps, [turns_exec(1, "INT-1", "2026-03-01", [(1, "Client", "We've decided: 60.")]), turns_exec(2, "INT-2", "2026-04-01", [(1, "Client", "We've decided: 63.")])],
                             conflicts=[dict(conflict_id="CF-1", memory_id="EP-A", other_memory_id="EP-B")])
        self.assertEqual({c.temporal_status for c in claims}, {"disputed"})

    def test_a_superseded_or_disputed_memory_status_is_carried(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-01", status="superseded"), episode("EP-B", "INT-2", 1, "Client", "We've decided: 60.", "confirmed", "60", "2026-02-01", status="disputed")]
        claims, _ = timeline(eps, [turns_exec(1, "INT-1", "2026-01-01", [(1, "Client", "We've decided: 65.")]), turns_exec(2, "INT-2", "2026-02-01", [(1, "Client", "We've decided: 60.")])])
        self.assertEqual((by_turn(claims, "INT-1", 1).temporal_status, by_turn(claims, "INT-2", 1).temporal_status), ("superseded", "disputed"))

    def test_an_expired_memory_is_no_longer_in_force_and_says_so(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-01", status="expired")]
        claims, _ = timeline(eps, [turns_exec(1, "INT-1", "2026-01-01", [(1, "Client", "We've decided: 65.")])])
        self.assertEqual(claims[0].temporal_status, "superseded")
        self.assertTrue(any("expired" in n for n in claims[0].notes))

    def test_one_current_claim_per_slot_the_latest_verified_decision(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-01", verified=True),
               episode("EP-B", "INT-2", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)]
        claims, _ = timeline(eps, [turns_exec(1, "INT-1", "2026-01-01", [(1, "Client", "We've decided: 65.")]), turns_exec(2, "INT-2", "2026-05-01", [(1, "Client", "We've decided: 63.")])])
        self.assertEqual([c.temporal_status for c in claims].count("current"), 1)
        self.assertEqual(by_turn(claims, "INT-2", 1).temporal_status, "current")
        self.assertEqual(by_turn(claims, "INT-1", 1).temporal_status, "historical")                             # an earlier decision is history, not erased


# ====================================================================================================================== unverified and unresolved
class Unverified(unittest.TestCase):
    def test_a_memory_without_its_source_turn_is_reported_as_unverified_and_never_narrated(self):
        e = episode("EP-9", "INT-9", 3, "Client", "Kenji has decided to retire at 63.", "confirmed", "63", "2026-05-01")
        claims, items = timeline([e], [])
        (c,) = claims
        self.assertEqual((c.source, c.verification, c.confirmed_client_decision), ("memory", "unverified", False))
        self.assertEqual(timeline_findings(claims, items), [])                                                   # a stored paraphrase is never a finding about what was said
        (u,) = [x for x in timeline_uncertainties(claims) if x.startswith("Unverified memory without a retrieved source turn")]
        self.assertIn("INT-9 turn 3", u)
        self.assertIn("it is not stated as fact", u)
        self.assertIn("Kenji has decided to retire at 63.", u)

    def test_a_stored_memory_cannot_stand_in_for_the_clients_own_turn(self):
        """The memory paraphrases a client decision; the retrieved turn is the advisor's recap.  The narrative states what the ADVISOR said, attributed to the advisor."""
        e = episode("EP-9", "INT-9", 3, "Client", "Kenji has decided to retire at 63.", "confirmed", "63", "2026-05-01")
        t = "You've decided to retire at 63."
        claims, items = timeline([e], [turns_exec(1, "INT-9", "2026-05-01", [(3, "Advisor", t)])])
        story = " ".join(f.text for f in timeline_findings(claims, items))
        self.assertIn("the advisor recorded (the advisor's account, not a client statement", story)
        self.assertNotIn("the client said", story)
        self.assertEqual(claims[0].statement_kind, "advisor_reported")

    def test_a_turn_with_an_unverified_memory_carries_the_note_and_the_narrative_does_not_cite_the_memory(self):
        e = episode("EP-9", "INT-9", 3, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01")
        claims, items = timeline([e], [turns_exec(1, "INT-9", "2026-05-01", [(3, "Client", "We've decided: 63.")])])
        self.assertTrue(any("unverified" in n for n in claims[0].notes))
        (f,) = timeline_findings(claims, items)
        cited = {x.ref: x for x in items}
        self.assertTrue(all(is_source_turn(cited[r]) for r in f.refs), f.refs)                                    # only source turns: an unverified memory is never cited as support

    def test_provenance_survives_into_the_narrative_and_the_structured_claim(self):
        e = episode("EP-1", "INT-090", 2, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)
        claims, items = timeline([e], [turns_exec(1, "INT-090", "2026-05-01", [(2, "Client", "We've decided: 63.")])])
        c = claims[0]
        self.assertTrue(set(c.evidence_refs) <= {x.ref for x in items})
        self.assertEqual(c.memory_refs, ["EP-1"])
        (f,) = timeline_findings(claims, items)
        self.assertIn("meeting INT-090 (2026-05-01), in turn 2", f.text)
        self.assertIn("2026-05-01", f.text)
        self.assertIn('"We\'ve decided: 63."', f.text)
        self.assertEqual(f.origin, "rendered")
        self.assertRegex(f.text, r"The memory layer holds it as a verified, confirmed client decision \[E\d+\]\.")
        self.assertTrue(any(items[int(r[1:]) - 1].memory_id == "EP-1" for r in f.refs))                          # the verified memory is cited, and only because it is verified


# ====================================================================================================================== relevance and determinism
class Relevance(unittest.TestCase):
    def test_pleasantries_and_other_meetings_are_not_narrated_but_are_not_dropped(self):
        execs = [turns_exec(1, "INT-1", "2026-01-02", [(1, "Advisor", "Good morning."), (2, "Client", "Yes. I'd also like ten minutes at the end for open items."),
                                                         (3, "Client", "Honestly, the plan has always been 65 for my retirement."), (4, "Client", "I was only exploring 60.")]),
                 turns_exec(2, "INT-2", "2026-01-09", [(1, "Client", "We've decided to repaint the kitchen next spring and keep the budget low.")])]
        claims, items = timeline([], execs)
        self.assertEqual(len(claims), 5)                                                                       # nothing is dropped from the structured timeline
        story = " ".join(f.text for f in timeline_findings(claims, items))
        self.assertIn("always been 65", story)
        self.assertIn("only exploring 60", story)
        self.assertNotIn("Good morning", story)
        self.assertNotIn("ten minutes", story)
        self.assertNotIn("repaint", story)                                                                     # a different meeting, not about the plan's topic
        self.assertFalse(by_turn(claims, "INT-2", 1).relevant)

    def test_the_timeline_is_deterministic(self):
        e = episode("EP-1", "INT-090", 2, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)
        runs = [timeline([e], [turns_exec(1, "INT-090", "2026-05-01", [(2, "Client", "We've decided: 63.")])]) for _ in range(2)]
        self.assertEqual([c.model_dump() for c in runs[0][0]], [c.model_dump() for c in runs[1][0]])
        self.assertEqual([f.text for f in timeline_findings(*runs[0])], [f.text for f in timeline_findings(*runs[1])])

    def test_the_summary_uses_neutral_wording_and_no_numbers_the_verifier_would_have_to_check(self):
        claims, _ = Chronology().scenario()
        s = timeline_summary(claims)
        self.assertIn("2026-02-10 to 2026-03-04", s)
        self.assertIn("later corrected", s)
        import re
        self.assertFalse(re.search(r"(?i)\b(decided|decision|agreed|confirmed|current|verified|established|final)\b", s), s)           # the report's source-summary rule
        self.assertEqual(timeline_summary([]), "")


# ====================================================================================================================== integration: the REAL Kenji turns and the harness verifier
class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from services.conversations import shared
        cls.conv = shared()

    def real(self, n, iid, idxs):
        rows = [self.conv.turn(iid, i) for i in idxs]
        return turns_exec(n, iid, str(rows[0]["interaction_datetime"])[:10], [(r["turn_index"], r["speaker"], r["utterance"]) for r in rows])

    def build(self):
        w = lambda iid, i: self.conv.turn(iid, i)
        eps = [episode("EP-PLAN", "INT-001", 9, "Advisor", w("INT-001", 9)["utterance"], "unconfirmed", "65", "2026-01-02"),
               episode("EP-FIX", "INT-049", 4, "Client", w("INT-049", 4)["utterance"], "correction", "65", "2026-03-04", corrects="60"),
               episode("EP-SIXTY", "INT-049", 3, "Advisor", w("INT-049", 3)["utterance"], "unconfirmed", "60", "2026-03-04"),
               episode("EP-63", "INT-090", 2, "Client", w("INT-090", 2)["utterance"], "confirmed", "63", "2026-05-01", verified=True)]
        execs = [self.real(1, "INT-001", [9, 10]), self.real(2, "INT-049", [3, 4, 5, 6, 7]), self.real(3, "INT-055", [1]), self.real(4, "INT-090", [2])]
        return eps, execs

    def report(self, summary="The record shows how the retirement age was discussed."):
        eps, execs = self.build()
        a, llm = agent_for(llm_out(summary=summary, key_findings=[]))
        pl = plan(task="history", text="Give me the chronology of Kenji's retirement age with the source conversation turns, including any corrections.")
        pl.memory_topics = [TOPIC]
        rep = a.write(pl.request_text, pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate(*execs))
        return rep, tstate(*execs), pl

    def test_the_kenji_chronology_is_built_from_the_real_turns(self):
        rep, _, _ = self.report()
        by = {(c.interaction_id, c.turn_index): c for c in rep.timeline}
        t4 = by[("INT-049", 4)]
        self.assertEqual((t4.speaker, t4.statement_kind, t4.modality, t4.event_date), ("Client", "client_statement", "tentative", "2026-03-04"))
        self.assertIn("I was only exploring the possibility", t4.wording)
        self.assertIn("65 is still the date we're planning against", t4.wording)
        t5 = by[("INT-049", 5)]
        self.assertEqual((t5.speaker, t5.statement_kind), ("Advisor", "advisor_interpretation"))
        self.assertIn("The wording of my file note was too strong", t5.wording)
        plan_claim = by[("INT-001", 9)]
        self.assertEqual((plan_claim.statement_kind, plan_claim.modality), ("advisor_reported", "tentative"))
        self.assertFalse(plan_claim.confirmed_client_decision)                                                  # "a working assumption, not a fixed decision" is never a client decision
        d63 = by[("INT-090", 2)]
        self.assertEqual((d63.statement_kind, d63.modality, d63.temporal_status, d63.confirmed_client_decision), ("client_statement", "decision", "current", True))
        self.assertEqual(rep.timeline, sorted(rep.timeline, key=lambda c: (c.event_date, c.interaction_id, c.turn_index)))

    def test_the_narrative_precedes_the_raw_source_records_and_distinguishes_client_from_advisor(self):
        rep, _, _ = self.report()
        kinds = ["story" if is_narrative(f) else "record" if f.text.startswith(("Source record", "Synthetic source record")) else "other" for f in rep.historical_context]
        self.assertIn("story", kinds)
        self.assertLess(kinds.index("story"), kinds.index("record"))
        story = " ".join(f.text for f in rep.historical_context if is_narrative(f))
        self.assertRegex(story, r"turn 4\)?, the client said, correcting the record tentatively, \(the client's own words[;)]")
        self.assertRegex(story, r"\(turn 5\), the advisor noted, acknowledging a correction, \(the advisor's interpretation or recommendation[;)]")
        self.assertIn("in meeting INT-090 (2026-05-01), in turn 2, the client said, in decisive wording, (the client's own words)", story)
        self.assertRegex(story, r"The memory layer holds it as a verified, confirmed client decision \[E\d+\]\.")

    def test_the_harness_verifier_accepts_the_code_written_chronology_and_still_rejects_an_overreaching_model_finding(self):
        from harness.evidence_verifier import EvidenceVerifier
        eps, execs = self.build()
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        overreach = {"text": "Kenji decided to retire at 60 and confirmed it on 2026-03-04.", "refs": ["E1"]}
        a, _ = agent_for(llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[], historical_context=[overreach]))
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate(*execs))
        final, res = EvidenceVerifier(self.conv).verify_report(rep, [K], tstate(*execs))
        story = [v for v in res.verdicts if STORY_RX.match(v.text)]
        self.assertTrue(story)
        self.assertTrue(all(v.verdict == "verified" for v in story), [(v.verdict, v.reasons) for v in story])      # the same authority that checks every other finding says so
        self.assertEqual(len(final.timeline), len(rep.timeline))                                                         # the structured chronology survives verification

    def test_unverified_and_unresolved_information_is_in_the_uncertainties_not_in_the_findings(self):
        rep, _, _ = self.report()
        text = " ".join(rep.uncertainties)
        self.assertIn("Advisor-reported, not confirmed by the client", text)                                         # INT-001 turn 9: the advisor's working assumption
        self.assertNotIn("EP-PLAN", " ".join(f.text for f in rep.historical_context))

    def test_the_summary_describes_the_chronology_without_overreaching(self):
        rep, _, _ = self.report()
        self.assertIn("The retrieved record contains dated statements from 2026-01-02 to 2026-05-01", rep.summary)
        self.assertIn("later corrected", rep.summary)
        self.assertNotRegex(rep.summary, r"(?i)\b(decided|agreed|confirmed|established|verified)\b")


class PromptBudget(unittest.TestCase):
    """Regression (found by tests/test_p15_evaluation.py, scenario 'budget pressure'): the timeline's quote-bearing uncertainty lines were put into the model's PROTECTED 'known gaps' block, which
    pushed the prompt over the report's 1,800-token ceiling; the model call failed and the report fell back to the deterministic path.  Those lines belong in the report, not in the prompt."""

    def test_timeline_uncertainties_reach_the_report_but_not_the_model_prompt(self):
        e = episode("EP-9", "INT-9", 3, "Client", "Kenji has decided to retire at 63.", "confirmed", "63", "2026-05-01")
        a, llm = agent_for(llm_out(summary="The memory holds one unverified record.", key_findings=[]))
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=[e])}, tstate())
        self.assertTrue(any(u.startswith("Unverified memory without a retrieved source turn") for u in rep.uncertainties), rep.uncertainties)
        self.assertNotIn("Unverified memory without a retrieved source turn", llm.calls[0]["user"])
        self.assertTrue(rep.validation["model_called"])                                                          # the model was reached and answered (no model_error); nothing in it was valid, which is a different, correct fallback
        self.assertNotIn("model_error", rep.validation)

    def test_the_known_gaps_block_of_the_prompt_is_not_larger_than_before_the_timeline_existed(self):
        eps = [episode(f"EP-{i}", f"INT-{i}", 1, "Advisor", "You've decided to retire at 60, and I recorded it with a long note about the plan " * 3, "unconfirmed", "60", f"2026-01-{i + 1:02d}") for i in range(8)]
        a, llm = agent_for(llm_out(summary="x", key_findings=[]))
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate())
        gaps = llm.calls[0]["user"].split("KNOWN GAPS", 1)[1].split("TOOL LIMITATIONS", 1)[0]
        self.assertLess(len(gaps), 1800)
        self.assertGreater(len([u for u in rep.uncertainties if u.startswith(("Unverified memory", "Advisor-reported"))]), 8)      # all of them are in the report


if __name__ == "__main__":
    unittest.main()
