"""B3: the chronology as a coherent, evidence-grounded account (agents/report/narrative.py, integrated in agents/report/agent.py).

What these tests protect: the account must read as one story told in date order; it must say who spoke in what capacity, keep every correction beside what it corrects without removing the earlier
words, say what is unresolved, contradicted, tentative, unverified or missing; every sentence must be traceable to the evidence it cites (meeting, turn, date, speaker, original wording, handle);
and the narration must never add a fact, a reason, a certainty or a status of its own.  Citation tests PARSE each sentence and compare it with the cited evidence item - they do not just look for strings.
Synthetic fixtures plus the real Kenji turns; scripted model only (the one live-model run is reported separately).  No database is opened.
"""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_report_agent import K, agent_for, llm_out, mem, plan, tstate  # noqa: E402
from test_report_timeline import TOPIC, episode, timeline, turns_exec  # noqa: E402
from agents.report.narrative import CAUSAL, MAX_PER_PARAGRAPH, STORY, is_narrative, narrate, narrative_summary, summary_conflict  # noqa: E402
from agents.report.timeline import build_timeline, timeline_findings  # noqa: E402
from schemas.memory import MemoryContext  # noqa: E402
from schemas.report import is_source_turn  # noqa: E402

CLAIM_SENTENCE = re.compile(r'^(?P<lead>.*?)(?P<who>the client|the advisor|the speaker \([^)]*\)) (?P<verb>[^("]*?)\s?\((?P<inside>[^)]*)\): "(?P<q>.*)" \[(?P<ref>E\d+)\]\.$')
MEETING = re.compile(r"meeting (?P<iid>[A-Z]+-\d+) \((?P<date>\d{4}-\d{2}-\d{2})\)")
FORBIDDEN_OUTSIDE_QUOTES = re.compile(r"\b(?:because|therefore|hence|thus|consequently|as a result|due to|owing to|led to|so that|in order to|which is why)\b", re.I)


def strip_quotes(text):
    return re.sub(r'"[^"]*"', '""', text)


def sentences(paragraph):
    return [s for s in re.split(r"(?<=\]\.) ", paragraph) if s]


def norm(s):
    return " ".join((s or "").replace("“", '"').replace("”", '"').split())


def story(claims, items):
    return narrate(claims, items)


def checked_citations(test, findings, items):
    """Parse every claim sentence of every narrative finding and compare it with the evidence item its handle names.  Returns the number of sentences checked."""
    by_ref = {e.ref: e for e in items}
    n = 0
    for f in findings:
        meeting = None
        for s in sentences(f.text):
            m = MEETING.search(s)
            if m:
                meeting = m.groupdict()
            c = CLAIM_SENTENCE.match(s)
            if not c:
                continue
            n += 1
            e = by_ref[c["ref"]]
            test.assertTrue(is_source_turn(e), s)                                                       # a statement about what was said rests on a retrieved source turn
            test.assertIn(e.ref, f.refs)                                                                # and the finding cites it
            test.assertEqual((meeting["iid"], meeting["date"]), (e.interaction_id, e.event_date), s)   # meeting and date of the paragraph are the evidence item's
            test.assertEqual(int(re.search(r"turn (\d+)", c["lead"]).group(1)), e.turn_index, s)       # the turn named is the evidence item's
            test.assertEqual(c["who"], {"Client": "the client", "Advisor": "the advisor"}[e.speaker], s)   # the speaker named is the recorded speaker
            q = c["q"][:-3] if c["q"].endswith("...") else c["q"]
            test.assertTrue(norm(e.excerpt).startswith(norm(q)), (q, e.excerpt))                      # the quotation is the original wording
    return n


# ============================================================================================================ the account as a story
class Story(unittest.TestCase):
    def three_meetings(self):
        eps = [episode("EP-A", "INT-1", 2, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-02", verified=True),
               episode("EP-B", "INT-2", 3, "Client", "I was only exploring 60 as a possibility for retirement.", "exploratory", "60", "2026-03-04"),
               episode("EP-C", "INT-3", 2, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)]
        execs = [turns_exec(1, "INT-1", "2026-01-02", [(1, "Advisor", "Where do you want to land on retirement?"), (2, "Client", "We've decided: 65.")]),
                 turns_exec(2, "INT-2", "2026-03-04", [(3, "Client", "I was only exploring 60 as a possibility for retirement.")]),
                 turns_exec(3, "INT-3", "2026-05-01", [(2, "Client", "We've decided: 63.")])]
        return timeline(eps, execs)

    def test_a_multi_meeting_account_is_one_paragraph_per_meeting_in_date_order_with_factual_connectives(self):
        claims, items = self.three_meetings()
        fs = story(claims, items)
        self.assertEqual(len(fs), 3)
        self.assertTrue(all(is_narrative(f) for f in fs))
        leads = [STORY.match(f.text).group(0) for f in fs]
        self.assertEqual(leads, ["In meeting ", "Later, in meeting ", "Later, in meeting "])
        self.assertEqual([MEETING.search(f.text).group("date") for f in fs], ["2026-01-02", "2026-03-04", "2026-05-01"])
        self.assertEqual(checked_citations(self, fs, items), 3)

    def test_two_meetings_on_the_same_day_are_told_as_the_same_day(self):
        execs = [turns_exec(1, "INT-1", "2026-03-04", [(1, "Client", "I was only exploring 60 as a possibility for retirement.")]),
                 turns_exec(2, "INT-2", "2026-03-04", [(1, "Client", "I'm still weighing 63 for my retirement, nothing is settled.")])]
        claims, items = timeline([], execs)
        fs = story(claims, items)
        self.assertEqual([STORY.match(f.text).group(0) for f in fs], ["In meeting ", "On the same day, in meeting "])

    def test_turns_of_one_meeting_are_linked_by_their_turn_numbers_not_by_an_assumed_reply(self):
        t = [(1, "Advisor", "You've decided to retire at 60. Is that right?"), (2, "Client", "I was only exploring the possibility."), (5, "Advisor", "The wording of my file note was too strong.")]
        claims, items = timeline([], [turns_exec(1, "INT-9", "2026-03-04", t)])
        (f,) = story(claims, items)
        self.assertIn("In the next turn (turn 2), the client said", f.text)                                     # turn 2 follows turn 1 and is spoken by someone else
        self.assertIn("Then, in turn 5, the advisor noted", f.text)                                              # turn 5 does not follow turn 2: no claim of adjacency, no claim of a reply
        self.assertNotRegex(strip_quotes(f.text), r"\b(?:replied|responded|answered|in response)\b")

    def test_a_long_meeting_continues_in_a_second_paragraph_and_stays_in_order(self):
        t = [(i, "Client" if i % 2 else "Advisor", f"I was only exploring {55 + i} as a retirement age, nothing is decided.") for i in range(1, MAX_PER_PARAGRAPH + 4)]
        claims, items = timeline([], [turns_exec(1, "INT-7", "2026-03-04", t)])
        fs = story(claims, items)
        self.assertEqual(len(fs), 2)
        self.assertTrue(fs[0].text.startswith("In meeting INT-7 (2026-03-04), in turn 1,"))
        self.assertTrue(fs[1].text.startswith("Continuing in meeting INT-7 (2026-03-04), in turn "))
        self.assertEqual(checked_citations(self, fs, items), MAX_PER_PARAGRAPH + 3)

    def test_the_account_is_deterministic_and_does_not_change_the_claims(self):
        claims, items = self.three_meetings()
        before = [c.model_dump() for c in claims]
        a, b = story(claims, items), story(copy.deepcopy(claims), items)
        self.assertEqual([f.text for f in a], [f.text for f in b])
        self.assertEqual([c.model_dump() for c in claims], before)                                              # narrating never touches a status, a certainty or a relation


# ============================================================================================================ corrections
class Corrections(unittest.TestCase):
    ADV = "You've decided to retire at 60. I'll record that as a confirmed decision."

    def double_correction(self):
        eps = [episode("EP-ADV", "THR-2", 1, "Advisor", self.ADV, "unconfirmed", "60", "2026-02-10"),
               episode("EP-C1", "INT-49", 4, "Client", "I should clarify that. I was only exploring 60 and I haven't made that decision. 65 is still the date.", "correction", "65", "2026-03-04", corrects="60"),
               episode("EP-C2", "INT-90", 2, "Client", "I've corrected my own record: we've decided 63, not 60.", "correction", "63", "2026-05-01", verified=True, corrects="60")]
        execs = [turns_exec(1, "THR-2", "2026-02-10", [(1, "Advisor", self.ADV)]),
                 turns_exec(2, "INT-49", "2026-03-04", [(4, "Client", "I should clarify that. I was only exploring 60 and I haven't made that decision. 65 is still the date.")]),
                 turns_exec(3, "INT-90", "2026-05-01", [(2, "Client", "I've corrected my own record: we've decided 63, not 60.")])]
        return timeline(eps, execs)

    def test_a_decision_corrected_twice_says_so_and_keeps_its_original_words(self):
        claims, items = self.double_correction()
        adv = next(c for c in claims if c.interaction_id == "THR-2")
        self.assertEqual(sum(r["type"] == "corrected_by" for r in adv.relations), 2)
        self.assertEqual(adv.wording, self.ADV)
        text = " ".join(f.text for f in story(claims, items))
        self.assertIn("later corrected in INT-49 turn 4; and then corrected again in INT-90 turn 2", text)
        self.assertIn(self.ADV, text)                                                                           # the corrected statement is still told, in its own words
        self.assertIn("corrects the record in THR-2 turn 1, which stays unchanged", text)
        self.assertLess(text.index("2026-02-10"), text.index("2026-03-04"))
        self.assertLess(text.index("2026-03-04"), text.index("2026-05-01"))
        self.assertIn("corrected more than once", narrative_summary(claims))

    def test_the_correction_is_told_beside_the_statement_it_corrects_with_both_dates_and_turns(self):
        claims, items = self.double_correction()
        fs = story(claims, items)
        self.assertEqual(checked_citations(self, fs, items), 3)
        first = fs[0].text
        self.assertIn("THR-2", first)
        self.assertIn("(2026-02-10)", first)
        self.assertIn("INT-49 turn 4", first)                                                                   # the later correction is named where the earlier statement is told

    def test_a_chain_of_corrections_marks_each_as_corrected_by_the_next(self):
        eps = [episode("EP-1", "INT-1", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-01", verified=True),
               episode("EP-2", "INT-2", 1, "Client", "I've corrected my own record: it is 60.", "correction", "60", "2026-02-01", corrects="65"),
               episode("EP-3", "INT-3", 1, "Client", "I've corrected my own record again: it is 63.", "correction", "63", "2026-03-01", verified=True, corrects="60")]
        execs = [turns_exec(i, f"INT-{i}", f"2026-0{i}-01", [(1, "Client", t)]) for i, t in ((1, "We've decided: 65."), (2, "I've corrected my own record: it is 60."), (3, "I've corrected my own record again: it is 63."))]
        claims, items = timeline(eps, execs)
        self.assertEqual([c.temporal_status for c in claims], ["corrected", "corrected", "current"])
        text = " ".join(f.text for f in story(claims, items))
        self.assertIn("later corrected in INT-2 turn 1", text)
        self.assertIn("later corrected in INT-3 turn 1", text)


# ============================================================================================================ who said what
class Attribution(unittest.TestCase):
    def mixed(self):
        execs = [turns_exec(1, "INT-1", "2026-01-02", [(1, "Advisor", "You've decided to retire at 65, I'll record that."), (2, "Client", "I was only exploring the possibility of 60 for retirement."),
                                                        (3, "Advisor", "I recommend retiring at 62 given your retirement plans.")])]
        return timeline([], execs)

    def test_every_sentence_names_the_capacity_of_the_speaker(self):
        claims, items = self.mixed()
        (f,) = story(claims, items)
        parts = [CLAIM_SENTENCE.match(s) for s in sentences(f.text) if CLAIM_SENTENCE.match(s)]
        self.assertEqual(len(parts), 3)
        self.assertEqual([p["who"] for p in parts], ["the advisor", "the client", "the advisor"])
        self.assertIn("the advisor's account, not a client statement", parts[0]["inside"])
        self.assertIn("the client's own words", parts[1]["inside"])
        self.assertIn("the advisor's interpretation or recommendation", parts[2]["inside"])

    def test_advisor_words_are_never_called_the_clients_and_the_client_is_never_called_the_advisor(self):
        claims, items = self.mixed()
        for s in sentences(story(claims, items)[0].text):
            c = CLAIM_SENTENCE.match(s)
            if c and c["who"] == "the advisor":
                self.assertNotIn("the client's own words", c["inside"])
                self.assertNotRegex(c["verb"], r"\bsaid\b" if "account" in c["inside"] else r"\Z\A")
            if c and c["who"] == "the client":
                self.assertNotIn("advisor", c["inside"])

    def test_an_advisor_recap_is_not_reported_as_what_the_client_said_even_when_a_memory_calls_it_the_clients(self):
        e = episode("EP-9", "INT-9", 3, "Client", "Kenji has decided to retire at 63.", "confirmed", "63", "2026-05-01", verified=True)
        claims, items = timeline([e], [turns_exec(1, "INT-9", "2026-05-01", [(3, "Advisor", "You've decided to retire at 63.")])])
        text = " ".join(f.text for f in story(claims, items))
        self.assertIn("the advisor recorded (the advisor's account, not a client statement", text)
        self.assertNotIn("the client said", text)
        self.assertNotIn("holds it as a verified, confirmed client decision", text)                              # an advisor's recap with no client reply is not that, whatever a memory says

    def test_a_confirming_client_reply_is_reported_as_retrieved_but_does_not_upgrade_anything(self):
        claims, items = timeline([], [turns_exec(1, "INT-1", "2026-01-02", [(13, "Advisor", "You've decided to retire at 65."), (14, "Client", "Correct.")])])
        text = " ".join(f.text for f in story(claims, items))
        self.assertIn("The client's own reply (INT-1#14) was retrieved.", text)
        self.assertNotIn("holds it as a verified, confirmed client decision", text)


# ============================================================================================================ contradictions, tentative, unresolved, unverified
class Unresolved(unittest.TestCase):
    def test_contradictory_decisions_are_told_as_unresolved_and_the_summary_says_so(self):
        eps = [episode("EP-A", "INT-1", 1, "Client", "We've decided: 60.", "confirmed", "60", "2026-03-01", verified=True),
               episode("EP-B", "INT-1", 5, "Client", "We've decided: 63.", "confirmed", "63", "2026-03-01", verified=True)]
        claims, items = timeline(eps, [turns_exec(1, "INT-1", "2026-03-01", [(1, "Client", "We've decided: 60."), (5, "Client", "We've decided: 63.")])])
        text = " ".join(f.text for f in story(claims, items))
        self.assertEqual(text.count("conflicts with the record in INT-1 turn"), 2)
        self.assertIn("unresolved", text)
        self.assertNotIn("holds it as a verified, confirmed client decision", text)                              # nothing is HELD as the position while the records conflict ...
        self.assertEqual(text.count("is disputed and is not held as the current position"), 2)                     # ... and the account says why, following B2B's status
        self.assertIn("Some records conflict and the conflict is not resolved.", narrative_summary(claims))

    def test_a_latest_decision_without_a_verified_confirmed_memory_is_told_as_unresolved(self):
        e = episode("EP-1", "INT-1", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01")
        claims, items = timeline([e], [turns_exec(1, "INT-1", "2026-05-01", [(1, "Client", "We've decided: 63.")])])
        (f,) = story(claims, items)
        self.assertIn("Whether this is the current position is unresolved: no verified, confirmed client decision supports it.", f.text)
        self.assertEqual(claims[0].temporal_status, "unresolved")

    def test_tentative_statements_are_told_as_tentative_and_the_summary_says_some_were(self):
        claims, items = timeline([], [turns_exec(1, "INT-1", "2026-03-04", [(1, "Client", "I was only exploring the possibility of 60 for retirement.")])])
        (f,) = story(claims, items)
        self.assertIn("the client said, tentatively,", f.text)
        self.assertNotRegex(strip_quotes(f.text), r"\b(?:decided|decision|agreed|settled|committed)\b(?! client decision supports)")
        self.assertIn("Some of the client's statements were tentative.", narrative_summary(claims))

    def test_a_withdrawn_statement_is_told_as_withdrawn_and_stays_in_the_account(self):
        claims, items = timeline([], [turns_exec(1, "INT-1", "2026-04-01", [(1, "Client", "We've decided: 60 for retirement. Scratch that, I misspoke.")])])
        (f,) = story(claims, items)
        self.assertIn("withdrawing an earlier statement", f.text)
        self.assertIn("We've decided: 60", f.text)
        self.assertIn("taken back by its speaker", narrative_summary(claims))

    def test_unverified_memory_without_its_turn_is_not_in_the_account_but_is_in_the_uncertainties(self):
        e = episode("EP-9", "INT-9", 3, "Client", "Kenji has decided to retire at 63.", "confirmed", "63", "2026-05-01")
        a, _ = agent_for(llm_out(summary="x", key_findings=[]))
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=[e])}, tstate())
        self.assertFalse([f for f in rep.historical_context if is_narrative(f)])
        self.assertTrue(any(u.startswith("Unverified memory without a retrieved source turn") for u in rep.uncertainties))

    def test_advisor_reported_statements_without_a_client_reply_are_flagged_in_summary_and_uncertainties(self):
        claims, items = timeline([], [turns_exec(1, "THR-2", "2026-02-10", [(1, "Advisor", "You've decided to retire at 60. I'll record that as a confirmed decision.")])])
        self.assertIn("no confirming reply from the client was retrieved", narrative_summary(claims))


# ============================================================================================================ missing evidence and incomplete retrieval (B1, B2A)
class Incomplete(unittest.TestCase):
    GAP = "Not retrieved: INT-022 (2026-02-03; matching turn(s) 1) because the tool-call budget of 12 was used"
    REF = "Reference not resolved: INT-090#1 mentions 'april 22' (ambiguous: 2 meetings of this client fall on that date (INT-082, THR-017)); no meeting was assumed"

    def write(self, retrieval_report=None, missing=(), episodes=()):
        eps = list(episodes) or [episode("EP-1", "INT-1", 1, "Client", "I was only exploring the possibility of 60 for retirement.", "exploratory", "60", "2026-03-04")]
        execs = [turns_exec(1, "INT-1", "2026-03-04", [(1, "Client", "I was only exploring the possibility of 60 for retirement.")])]
        a, _ = agent_for(llm_out(summary="The record shows tentative statements.", key_findings=[]))
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        ts = tstate(*execs, missing_information=list(missing))
        ts.retrieval_report = retrieval_report or {}
        return a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, ts)

    def test_b1_retrieval_gaps_are_kept_and_the_summary_says_the_account_may_be_incomplete(self):
        rep = self.write({"not_retrieved": [{"interaction_id": "INT-022"}]}, [self.GAP, "The chronology may be incomplete: 1 relevant meeting(s) or turn(s) were identified but not retrieved", self.REF])
        self.assertIn("Missing information: " + self.GAP, rep.uncertainties)
        self.assertIn("Missing information: " + self.REF, rep.uncertainties)
        self.assertIn("The account may be incomplete", rep.summary)

    def test_a_complete_retrieval_does_not_claim_incompleteness(self):
        rep = self.write()
        self.assertNotIn("may be incomplete", rep.summary)

    def test_b2a_omitted_episodes_are_kept_and_make_the_account_say_it_may_be_incomplete(self):
        eps = [episode(f"EP-{i}", f"INT-{i}", 1, "Client", "We've decided: 63.", "confirmed", "63", f"2026-02-{1 + i:02d}") for i in range(20)]
        rep = self.write(episodes=eps)
        self.assertTrue(any("stored episodes were retrieved but only 16 are in the evidence list" in u for u in rep.uncertainties), rep.uncertainties)
        self.assertIn("The account may be incomplete", rep.summary)

    def test_no_dates_values_or_explanations_are_filled_in_for_a_turn_without_a_date(self):
        claims, items = timeline([], [turns_exec(1, "INT-1", "", [(1, "Client", "I was only exploring the possibility of 60 for retirement.")])])
        (f,) = story(claims, items)
        self.assertIn("(an unrecorded date)", f.text)
        self.assertNotRegex(f.text, r"\b20\d\d-\d\d-\d\d\b")


# ============================================================================================================ nothing is added: no reasons, no upgrades, no invented facts
class NothingIsAdded(unittest.TestCase):
    def rich(self):
        eps = [episode("EP-ADV", "THR-2", 1, "Advisor", "You've decided to retire at 60. I'll record that as a confirmed decision.", "unconfirmed", "60", "2026-02-10"),
               episode("EP-FIX", "INT-49", 4, "Client", "I should clarify that. I was only exploring 60 and I haven't made that decision. 65 is still the date.", "correction", "65", "2026-03-04", corrects="60"),
               episode("EP-63", "INT-90", 2, "Client", "We've decided: 63. Mariko wants to keep teaching until 62.", "confirmed", "63", "2026-05-01", verified=True)]
        execs = [turns_exec(1, "THR-2", "2026-02-10", [(1, "Advisor", "You've decided to retire at 60. I'll record that as a confirmed decision.")]),
                 turns_exec(2, "INT-49", "2026-03-04", [(4, "Client", "I should clarify that. I was only exploring 60 and I haven't made that decision. 65 is still the date."),
                                                         (5, "Advisor", "The wording of my file note was too strong.")]),
                 turns_exec(3, "INT-90", "2026-05-01", [(2, "Client", "We've decided: 63. Mariko wants to keep teaching until 62.")])]
        return timeline(eps, execs)

    def test_no_sentence_supplies_a_reason_or_a_cause(self):
        claims, items = self.rich()
        for f in story(claims, items):
            self.assertIsNone(FORBIDDEN_OUTSIDE_QUOTES.search(strip_quotes(f.text)), f.text)
        self.assertIsNone(FORBIDDEN_OUTSIDE_QUOTES.search(narrative_summary(claims, True)))
        self.assertIsNone(CAUSAL.search(narrative_summary(claims, True)))

    def test_outside_the_quotations_the_account_contains_only_dates_ids_and_turns_from_the_evidence(self):
        claims, items = self.rich()
        evidence_dates = {e.event_date for e in items if e.event_date}
        known_ids = {e.interaction_id for e in items if e.interaction_id}
        for f in story(claims, items):
            bare = strip_quotes(f.text)
            self.assertLessEqual(set(re.findall(r"\d{4}-\d{2}-\d{2}", bare)), evidence_dates)
            self.assertLessEqual(set(re.findall(r"\b(?:INT|THR|MTG|CHT)-\d+\b", bare)), known_ids)
            self.assertEqual(re.sub(r"\b(?:INT|THR|MTG|CHT)-\d+(?:#\d+)?|\d{4}-\d{2}-\d{2}|turn \d+|\[E\d+\]", "", bare).count("0"), 0)       # no other figure appears outside a quotation
            self.assertEqual(re.findall(r"\d+", re.sub(r"\b(?:INT|THR|MTG|CHT)-\d+(?:#\d+)?|\d{4}-\d{2}-\d{2}|turn \d+|\[E\d+\]", "", bare)), [])

    def test_a_tentative_or_advisor_statement_is_never_described_as_a_confirmed_decision(self):
        claims, items = self.rich()
        text = story(claims, items)
        confirmed_sentences = [s for f in text for s in sentences(f.text) if "verified, confirmed client decision" in s and not s.startswith("The memory layer")]
        self.assertEqual(confirmed_sentences, [])
        holds = [s for f in text for s in sentences(f.text) if s.startswith("The memory layer holds it as a verified, confirmed client decision")]
        self.assertEqual(len(holds), 1)                                                                          # exactly the one verified, confirmed, client-attributable claim
        self.assertTrue(next(c for c in claims if c.confirmed_client_decision).interaction_id == "INT-90")

    def test_statuses_certainties_and_relations_are_exactly_b2bs(self):
        claims, items = self.rich()
        snap = [(c.claim_id, c.temporal_status, c.modality, c.confirmed_client_decision, c.verification, c.statement_kind, json.dumps(c.relations, sort_keys=True)) for c in claims]
        story(claims, items)
        narrative_summary(claims, True)
        self.assertEqual(snap, [(c.claim_id, c.temporal_status, c.modality, c.confirmed_client_decision, c.verification, c.statement_kind, json.dumps(c.relations, sort_keys=True)) for c in claims])
        self.assertEqual({c.claim_id: c.temporal_status for c in claims if c.interaction_id == "THR-2"}, {"C1": "corrected"})

    def test_the_summary_uses_no_verdict_words_and_no_numbers_the_verifier_would_have_to_prove(self):
        claims, _ = self.rich()
        s = narrative_summary(claims, True)
        self.assertNotRegex(s, r"(?i)\b(decided|decision|agreed|confirmed|current|verified|established|final|accepted|signed)\b")
        self.assertEqual(re.findall(r"\d+", s), ["2026", "02", "10", "2026", "05", "01"])                       # only the first and last dates of the record


# ============================================================================================================ the summary stays consistent with the timeline
class SummaryConsistency(unittest.TestCase):
    def claims(self, verified=True):
        old = episode("EP-0", "INT-0", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-02", verified=True)
        e = episode("EP-1", "INT-1", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=verified)
        return timeline([old, e], [turns_exec(1, "INT-0", "2026-01-02", [(1, "Client", "We've decided: 65.")]), turns_exec(2, "INT-1", "2026-05-01", [(1, "Client", "We've decided: 63.")])])[0]

    def test_a_summary_stating_the_held_value_stands(self):
        self.assertIsNone(summary_conflict("The client's retirement age is 63.", self.claims()))

    def test_a_summary_stating_a_value_nothing_holds_is_in_conflict(self):
        why = summary_conflict("The client's retirement age is 65.", self.claims())
        self.assertIn("65", why)

    def test_a_summary_speaking_of_an_earlier_value_in_the_past_tense_stands(self):
        self.assertIsNone(summary_conflict("The retirement age was 65 and is now 63.", self.claims()))

    def test_with_nothing_current_a_summary_may_not_state_a_value_as_the_position(self):
        c = self.claims(verified=False)
        c[0].temporal_status = "historical"                                                                       # the earlier verified decision is history; the later one is not verified
        self.assertIsNotNone(summary_conflict("The client's retirement age is 63.", c))
        self.assertIsNone(summary_conflict("The record shows tentative discussion of 63.", c))

    def test_a_value_held_by_verified_semantic_memory_is_accepted(self):
        self.assertIsNone(summary_conflict("The client's retirement age is 63.", self.claims(verified=False), held={"63"}))

    def test_the_report_replaces_a_conflicting_model_summary_and_says_so(self):
        eps = [episode("EP-0", "INT-0", 1, "Client", "We've decided: 65.", "confirmed", "65", "2026-01-02", verified=True),
               episode("EP-1", "INT-1", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)]
        execs = [turns_exec(1, "INT-0", "2026-01-02", [(1, "Client", "We've decided: 65.")]), turns_exec(2, "INT-1", "2026-05-01", [(1, "Client", "We've decided: 63.")])]
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        a, _ = agent_for(llm_out(summary="Kenji's retirement age is 65.", key_findings=[]))
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate(*execs))
        self.assertNotIn("retirement age is 65", rep.summary)
        self.assertIn("semantic validation removed summary sentence(s): value_conflict", rep.validation["summary_replaced"])      # B4 removes the sentence itself (B3 replaced the whole summary)
        self.assertIn("The retrieved record contains dated statements from 2026-01-02 to 2026-05-01", rep.summary)

    def test_a_consistent_model_summary_is_kept(self):
        eps = [episode("EP-1", "INT-1", 1, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)]
        execs = [turns_exec(1, "INT-1", "2026-05-01", [(1, "Client", "We've decided: 63.")])]
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        a, _ = agent_for(llm_out(summary="The record shows the retirement age discussed.", key_findings=[]))
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate(*execs))
        self.assertTrue(rep.summary.startswith("The record shows the retirement age discussed."))
        self.assertFalse(rep.validation["summary_replaced"])


# ============================================================================================================ isolation and the harness verifier
class Safeguards(unittest.TestCase):
    def test_another_clients_turns_never_enter_the_account(self):
        other = "CLIENT-009"
        own = turns_exec(1, "INT-1", "2026-03-04", [(1, "Client", "I was only exploring the possibility of 60 for retirement.")])
        foreign = turns_exec(2, "INT-X", "2026-03-05", [(1, "Client", "We've decided: 99 for retirement.")])
        foreign.result.client_id = other
        claims, items = timeline([], [own, foreign])
        text = " ".join(f.text for f in story(claims, items))
        self.assertNotIn("INT-X", text)
        self.assertNotIn("99", text)

    def test_the_harness_verifier_verifies_the_new_account_on_the_real_kenji_turns(self):
        from test_report_timeline import Integration
        Integration.setUpClass()
        t = Integration("test_the_kenji_chronology_is_built_from_the_real_turns")
        from harness.evidence_verifier import EvidenceVerifier
        eps, execs = t.build()
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        a, _ = agent_for(llm_out(summary="The record shows how the retirement age was discussed.", key_findings=[]))
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate(*execs))
        final, res = EvidenceVerifier(t.conv).verify_report(rep, [K], tstate(*execs))
        paragraphs = [v for v in res.verdicts if STORY.match(v.text)]
        self.assertGreaterEqual(len(paragraphs), 3)
        self.assertTrue(all(v.verdict == "verified" for v in paragraphs), [(v.verdict, v.reasons) for v in paragraphs])
        self.assertEqual(checked_citations(self, [f for f in rep.historical_context if is_narrative(f)], rep.evidence), len([c for c in rep.timeline if c.source == "source_turn" and c.relevant
                                                                                                                              and (c.memory_refs or [x for x in c.cues if x != "hypothetical"])]))

    def test_the_account_is_ahead_of_the_raw_source_records_and_the_records_remain(self):
        claims, items = Story().three_meetings()
        a, _ = agent_for(llm_out(summary="x", key_findings=[]))
        pl = plan(task="history")
        pl.memory_topics = [TOPIC]
        eps = [episode("EP-C", "INT-3", 2, "Client", "We've decided: 63.", "confirmed", "63", "2026-05-01", verified=True)]
        rep = a.write("q", pl, {K: MemoryContext(client_id=K, episodes=eps)}, tstate(turns_exec(1, "INT-3", "2026-05-01", [(2, "Client", "We've decided: 63.")])))
        kinds = ["story" if is_narrative(f) else "record" if f.text.startswith(("Source record", "Synthetic source record")) else "other" for f in rep.historical_context]
        self.assertLess(kinds.index("story"), kinds.index("record"))


if __name__ == "__main__":
    unittest.main()
