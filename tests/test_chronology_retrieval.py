"""Stage B1: chronology-aware retrieval (agents/tool_selector/source_discovery.py + ToolSelector._discover_sources).

Two kinds of test.  SYNTHETIC: a small disposable conversation file with a meeting that can ONLY be reached through a reference (its title and text never name the subject), a date that is
ambiguous, a date that matches nothing, another client's meeting id, and a future booking.  FROZEN: the real data/conversations.csv (its sha256 is checked against the committed lock),
the real get_source_evidence tool, and the Kenji retirement chronology; there the decisive INT-049 turns must be present with their real text, not merely listed.
Scripted model only (the selector's model step is a 'finish'); disposable databases from tests/conftest.py; nothing here writes to the repository.
"""
import csv
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.tool_selector import source_discovery as SD  # noqa: E402
from agents.tool_selector.agent import SelectorConfig  # noqa: E402
from agents.tool_selector.validator import CallValidator  # noqa: E402
from schemas.tools import PlannedCall  # noqa: E402
from services.conversations import Conversations, shared  # noqa: E402
import test_tool_selector as TS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
R, K = "CLIENT-002", "CLIENT-001"          # R is the synthetic client; K is "somebody else" (and, in the frozen tests, Kenji)
FIELDS = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index", "speaker", "utterance", "meeting_context", "source_type", "consent_status"]
ASK = "For CLIENT-002, give a chronology of the Zanzibar villa purchase with the source conversation turns, including any corrections."
KENJI = ("Give me the chronology of Kenji's retirement age with the source conversation turns, including any corrections. Distinguish what Kenji explicitly stated from what the advisor reported "
         "or inferred. Show the dates and evidence for each change, and identify anything that remains unverified or uncertain.")

# (interaction, client, date, title, [(speaker, text), ...])
MEETINGS = [
    ("INT-9100", K, "2026-02-12", "Somebody else's meeting", [("Advisor", "Another client's private matter."), ("Client", "Yes.")]),
    ("INT-9101", R, "2026-02-01", "Zanzibar villa first look", [("Advisor", "We looked at the Zanzibar villa."), ("Client", "I like the Zanzibar villa."), ("Advisor", "Noted."), ("Client", "Let me think.")]),
    # reachable ONLY through the reference in INT-9104: neither its title nor any turn names the subject
    ("INT-9103", R, "2026-02-15", "Budget corrections", [("Advisor", "Before we start, I want to correct my earlier note."), ("Client", "The price was only a rough guess; nothing is agreed."),
                                                       ("Advisor", "I will fix the note and keep the original on record."), ("Client", "Good.")]),
    ("INT-9104", R, "2026-02-20", "Zanzibar villa follow-up",
     [("Advisor", "Since February 15, when we focused on correcting the Zanzibar villa record, the next topic is the survey."),
      ("Client", "Fine."),
      ("Advisor", "Please also see INT-9100 and INT-9999 for the earlier file."),                           # another client's id, and an id that does not exist
      ("Advisor", "Since February 9, when we spoke about the Zanzibar villa, nothing else changed."),        # no meeting of this client on that date
      ("Advisor", "I'll book us for Tuesday, March 31 to talk about the Zanzibar villa again.")]),         # a booking, never a reference
    ("INT-9106", R, "2026-03-03", "Insurance renewal", [("Advisor", "Insurance renewal today."), ("Client", "Ok.")]),
    ("INT-9107", R, "2026-03-03", "Garden", [("Advisor", "Garden plans today."), ("Client", "Ok.")]),
    ("INT-9105", R, "2026-03-20", "Zanzibar villa decision", [("Advisor", "Since March 3, when we discussed the Zanzibar villa, the next topic is the decision."), ("Client", "I decided on the Zanzibar villa.")]),
] + [(f"INT-92{n:02d}", R, f"2026-01-{n + 1:02d}", f"Zanzibar villa note {n}", [("Advisor", f"Routine note number {n}."), ("Client", "Ok.")]) for n in range(1, 8)]
PADDING = [(f"INT-8{n:03d}", K, "2026-02-03", "Ordinary", [("Advisor", "Thanks, see you next week.")]) for n in range(300)]      # ordinary turns, so that a word's rarity means something


def synthetic(meetings=MEETINGS) -> Conversations:
    d = tempfile.mkdtemp()
    p = Path(d) / "conversations.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for iid, cid, day, title, turns in list(meetings) + PADDING:
            for i, (speaker, text) in enumerate(turns, 1):
                w.writerow([iid, cid, "ADV-1", f"{day}T10:00:00", "meeting", i, speaker, text, title, "synthetic", "granted"])
    return Conversations(p)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.conv = synthetic()
        p = mock.patch("services.conversations.shared", lambda: self.conv)
        p.start()
        self.addCleanup(p.stop)
        self.plan = TS.plan(clients=(R,), tools=("get_source_evidence",), task_type="history", request_text=ASK)

    def run_sel(self, cfg=None, text=ASK):
        return TS.run([TS.finish()], p=self.plan, text=text, cfg=cfg)

    def reads(self, st):
        return [(e.arguments["interaction_id"], e.arguments["turn_indices"]) for e in st.executions if "interaction_id" in e.arguments and e.status == "success"]

    # ------------------------------------------------------------------------------------------------------------ 1-2 title relevance and ranking
    def test_a_correction_title_outranks_a_passing_mention_when_the_request_asks_for_corrections(self):
        conv = synthetic([("INT-1", R, "2026-01-01", "Quarterly review", [("Advisor", "We mentioned the Zanzibar villa once.")]),
                          ("INT-2", R, "2026-02-01", "Zanzibar villa", [("Advisor", "Plain talk.")]),
                          ("INT-3", R, "2026-03-01", "Correcting the Zanzibar villa record", [("Advisor", "Plain talk."), ("Client", "Ok.")])])
        m = SD.enrich_meetings({"INT-1": {"date": "2026-01-01", "turns": {1: 1}, "cue": False, "cue_turns": set()}}, conv, R, ["zanzibar villa"], ASK)
        self.assertEqual({i: m[i]["tier"] for i in m}, {"INT-1": 0, "INT-2": 2, "INT-3": 3})
        chosen, omitted = SD.plan_meetings(m, 1)
        self.assertEqual(chosen, ["INT-3"])                                                            # the correction meeting, although it has no matching turn at all
        self.assertEqual({o["interaction_id"] for o in omitted}, {"INT-1", "INT-2"})
        plain = SD.enrich_meetings({}, conv, R, ["zanzibar villa"], "Give a chronology of the Zanzibar villa")           # no word about corrections: about-the-subject is tier 2, not 3
        self.assertEqual({i: plain[i]["tier"] for i in plain}, {"INT-2": 2, "INT-3": 2})

    def test_a_meeting_about_the_subject_beats_a_meeting_that_only_mentions_it(self):
        m = {"INT-A": {"date": "2026-01-01", "turns": {1: 1, 2: 1, 3: 1}, "cue": False, "cue_turns": set(), "tier": 0},        # three mentions, passing
             "INT-B": {"date": "2026-02-01", "turns": {}, "cue": False, "cue_turns": set(), "tier": 2}}                           # its TITLE is about the subject
        self.assertEqual(SD.plan_meetings(m, 1)[0], ["INT-B"])

    def test_without_any_relevance_signal_ranking_is_exactly_what_it_was(self):
        m = {f"INT-{i}": {"date": f"2026-0{i}-01", "turns": {1: 1}, "cue": False, "cue_turns": set()} for i in range(1, 6)}
        self.assertEqual(SD.plan_meetings(m, 2)[0], ["INT-1", "INT-5"])                                  # the earliest and the latest: the old two-ends rule
        untouched = SD.enrich_meetings(dict(m), self.conv, R, [], ASK)                                    # no subject stem: nothing is added or ranked
        self.assertTrue(all("tier" not in v and "window" not in v for v in untouched.values()))

    def test_a_meeting_found_only_by_its_title_is_retrieved_and_its_omission_is_worded_correctly(self):
        st, _, _ = self.run_sel()
        self.assertIn("INT-9101", [i for i, _ in self.reads(st)])
        note = SD.omission_note({"interaction_id": "INT-9201", "date": "2026-01-02", "turns": [], "reason": "tool-call budget"}, 12)
        self.assertIn("its title is about the subject; no search hit", note)

    # ------------------------------------------------------------------------------------------------------------ 3-6 references
    def test_a_date_reference_resolves_to_the_one_meeting_of_that_client_on_that_date(self):
        refs, _ = SD.find_references([dict(interaction_id="INT-9104", turn_index=1, text="Since February 15, when we focused on correcting the Zanzibar villa record, the next topic is the survey.")], self.conv, R)
        self.assertEqual([(r["interaction_id"], r["date"], r["kind"], r["quote"]) for r in refs], [("INT-9103", "2026-02-15", "date", "february 15")])
        self.assertIn("zanzibar", refs[0]["clause"])
        self.assertNotIn("survey", refs[0]["clause"])                                                     # the clause describes the REFERRED meeting only

    def test_date_forms_booking_sentences_and_non_meeting_sentences(self):
        def one(text):
            return SD.find_references([dict(interaction_id="INT-9104", turn_index=9, text=text)], self.conv, R)
        for form in ("Since February 15th, when we met, nothing changed.", "At the 15 February meeting we agreed it.", "Per our February 15 call it stays.", "As discussed on Feb 15, the plan holds."):
            self.assertEqual([r["interaction_id"] for r in one(form)[0]], ["INT-9103"], form)
        self.assertEqual(one("I'll book us for Tuesday, March 31 to talk about it again."), ([], []))        # a booking, and later than the referring meeting
        self.assertEqual(one("The invoice is dated February 15 and is due.")[0], [])                         # a date, but the sentence is not about a meeting
        self.assertEqual(one("Since February 15, 2025, when we met, nothing changed.")[0], [])              # an explicit other year is not guessed
        same_day = SD.find_references([dict(interaction_id="INT-9104", turn_index=1, text="Earlier today, February 20, we met.")], self.conv, R)
        self.assertEqual(same_day, ([], []))                                                                 # the referring meeting's own day is not a reference to an earlier one

    def test_an_ambiguous_date_is_recorded_as_unresolved_never_guessed(self):
        refs, unres = SD.find_references([dict(interaction_id="INT-9105", turn_index=1, text="Since March 3, when we discussed the Zanzibar villa, the next topic is the decision.")], self.conv, R)
        self.assertEqual(refs, [])
        self.assertEqual(len(unres), 1)
        self.assertIn("ambiguous: 2 meetings", unres[0]["why"])
        self.assertIn("INT-9106", unres[0]["why"])
        self.assertIn("INT-9107", unres[0]["why"])

    def test_unresolved_references_are_recorded_and_reach_the_report_as_missing_information(self):
        st, _, _ = self.run_sel()
        un = {(u["quote"], u["why"].split(":")[0]) for u in st.retrieval_report["references_unresolved"]}
        self.assertIn(("february 9", "no meeting of this client falls on that date"), un)
        self.assertIn(("INT-9999", "not a meeting of this client"), un)
        self.assertIn(("INT-9100", "not a meeting of this client"), un)                                     # another client's id: refused as unresolved, not looked up
        self.assertTrue(any(m.startswith("Reference not resolved:") and "'february 9'" in m for m in st.missing_information))
        self.assertTrue(any("INT-9100" in m and "no meeting was assumed" in m for m in st.missing_information))

    def test_a_reference_in_a_retrieved_turn_brings_in_a_meeting_no_search_or_title_could_find(self):
        st, _, _ = self.run_sel()
        got = dict(self.reads(st))
        self.assertIn("INT-9103", got)                                                                       # the only route to it is INT-9104 turn 1
        self.assertEqual(got["INT-9103"], [1, 2, 3, 4])                                                       # read with its context (a correction meeting is read widely)
        rep = st.retrieval_report
        self.assertIn(("INT-9104#1", "february 15", "INT-9103", True), [(r["from"], r["quote"], r["interaction_id"], r["retrieved"]) for r in rep["references_resolved"]])
        self.assertEqual(next(m["found_by"] for m in rep["retrieved"] if m["interaction_id"] == "INT-9103"), "reference")
        texts = {t["turn_index"]: t["text"] for e in st.executions if e.arguments.get("interaction_id") == "INT-9103" for t in e.result.data["turns"]}
        self.assertIn("rough guess", texts[2])                                                                # the decisive text itself, not just the id

    # ------------------------------------------------------------------------------------------------------------ 7 turn windows
    def test_the_window_holds_the_hits_and_their_neighbours_nearest_first_client_before_advisor(self):
        rows = [dict(turn_index=i, speaker="Advisor" if i % 2 else "Client", utterance="subject here" if i == 5 else "other") for i in range(1, 10)]
        self.assertEqual(SD.expand_turns(rows, {5}, ["subje"], cap=12), [4, 5, 6])
        self.assertEqual(SD.expand_turns(rows, {5}, ["subje"], cap=2), [4, 5])                            # cap: the hit, then its CLIENT neighbour (4) before the advisor's (6)
        self.assertEqual(SD.expand_turns(rows, {5}, ["subje"], cap=12, correction=True), [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(SD.expand_turns(rows, set(), ["zzzz"], cap=3), [1, 2, 3])                         # nothing matches: the leading turns of a meeting chosen by its title
        self.assertEqual(SD.expand_turns(rows, {5}, ["subje"], cap=12), SD.expand_turns(list(rows), {5}, ["subje"], cap=12))

    def test_a_window_never_exceeds_the_tools_per_call_limit(self):
        rows = [dict(turn_index=i, speaker="Client", utterance="subject") for i in range(1, 40)]
        self.assertEqual(len(SD.expand_turns(rows, set(), ["subje"], cap=SD.MAX_TURNS_PER_CALL)), SD.MAX_TURNS_PER_CALL)

    # ------------------------------------------------------------------------------------------------------------ 8 duplicates and overlaps
    def test_no_turn_is_retrieved_twice_and_no_call_repeats(self):
        st, _, _ = self.run_sel()
        turns = [(i, t) for i, ts in self.reads(st) for t in ts]
        self.assertEqual(len(turns), len(set(turns)))
        keys = [(e.tool, tuple(sorted((k, str(v)) for k, v in e.arguments.items()))) for e in st.executions if e.status == "success"]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertFalse([e for e in st.executions if e.status == "duplicate"])

    def test_the_validator_still_rejects_a_repeat_of_a_window_already_read(self):
        st, _, _ = self.run_sel()
        v = CallValidator(self._catalog(), [R])
        e = next(x for x in st.executions if x.arguments.get("interaction_id") == "INT-9103")
        v.done.add(__import__("agents.tool_selector.validator", fromlist=["call_key"]).call_key(e.tool, e.arguments, v.tools[e.tool]["input_schema"]))
        v.record_success(e.tool, e.arguments)
        verdict, _, _ = v.validate(PlannedCall(tool=e.tool, arguments=dict(e.arguments)), set())
        self.assertEqual(verdict, "duplicate")

    def _catalog(self):
        import asyncio

        async def tools():
            tb = TS.Toolbox()
            async with tb:
                return await tb.list_tools()
        return asyncio.run(tools())

    # ------------------------------------------------------------------------------------------------------------ 9 budget
    def test_calls_held_back_for_references_are_used_for_them(self):
        """8 meetings are about the subject; with a budget of 8 calls (1 search + 7) two calls are held back, so the referenced INT-9103 is still read."""
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=8))
        self.assertEqual(st.retrieval_report["budget"]["reserved_for_references"], 2)
        self.assertIn("INT-9103", [i for i, _ in self.reads(st)])
        self.assertLessEqual(sum(1 for e in st.executions if e.status in ("success", "error", "timeout")), 8)

    def test_without_the_reserve_the_referenced_meeting_is_reported_not_silently_dropped(self):
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=6))                                       # 1 search + 5: fewer than MIN_BUDGET_FOR_RESERVE, nothing held back
        self.assertEqual(st.retrieval_report["budget"]["reserved_for_references"], 0)
        self.assertNotIn("INT-9103", [i for i, _ in self.reads(st)])
        self.assertTrue(any(m.startswith("Referenced but not retrieved: INT-9103") for m in st.missing_information), st.missing_information)
        self.assertTrue(any(m.startswith("The chronology may be incomplete") for m in st.missing_information))
        self.assertTrue(any(n["interaction_id"] == "INT-9103" and n["found_by"] == "reference" for n in st.retrieval_report["not_retrieved"]))

    def test_the_twelve_call_maximum_is_never_exceeded_and_exhaustion_is_disclosed(self):
        for cap in (3, 6, 8, 12):
            st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=cap))
            used = sum(1 for e in st.executions if e.status in ("success", "error", "timeout"))
            self.assertLessEqual(used, cap, cap)
            self.assertEqual(st.retrieval_report["budget"]["used"], used)
            if cap <= 8:
                self.assertTrue(any(m.startswith("Not retrieved:") for m in st.missing_information), cap)
                self.assertEqual(st.completion_status, "partial")

    def test_searches_are_counted_against_the_same_budget(self):
        st, _, _ = self.run_sel(cfg=SelectorConfig(max_executions=4))
        searches = [e for e in st.executions if "search_text" in e.arguments]
        reads = [e for e in st.executions if "interaction_id" in e.arguments and e.status == "success"]
        self.assertEqual(len(searches) + len(reads), 4)

    # ------------------------------------------------------------------------------------------------------------ 10 permissions and client isolation
    def test_only_the_clients_own_meetings_are_ever_considered_or_retrieved(self):
        st, _, _ = self.run_sel()
        self.assertNotIn("INT-9100", [i for i, _ in self.reads(st)])
        self.assertTrue(all(e.arguments["client_id"] == R for e in st.executions))
        self.assertNotIn("INT-9100", SD.client_meetings(self.conv, R))
        self.assertEqual(set(SD.client_meetings(self.conv, K)) & {"INT-9101", "INT-9103"}, set())

    def test_the_tool_itself_still_refuses_another_clients_turns(self):
        from mcp_server.services import ServiceError
        from mcp_server.tools import SERVICES
        with self.assertRaises(ServiceError):
            SERVICES.get_source_evidence(R, interaction_id="INT-9100", turn_indices=[1])

    def test_a_plan_for_another_client_cannot_be_redirected_by_text(self):
        v = CallValidator(self._catalog(), [R])
        verdict, msg, _ = v.validate(PlannedCall(tool="get_source_evidence", arguments={"client_id": K, "interaction_id": "INT-9100", "turn_indices": [1]}), set())
        self.assertNotEqual(verdict, "ok")

    # ------------------------------------------------------------------------------------------------------------ 11 determinism
    def test_retrieval_is_deterministic(self):
        runs = []
        for _ in range(2):
            st, _, _ = self.run_sel()
            runs.append(([(e.arguments.get("search_text"), e.arguments.get("interaction_id"), e.arguments.get("turn_indices")) for e in st.executions], list(st.missing_information),
                         st.retrieval_report["references_resolved"], st.retrieval_report["references_unresolved"]))
        self.assertEqual(runs[0], runs[1])
        dates = {iid: d for iid, _, d, _, _ in MEETINGS}
        first_round = [m["interaction_id"] for m in st.retrieval_report["retrieved"] if m["found_by"] != "reference"]
        self.assertEqual([dates[i] for i in first_round], sorted(dates[i] for i in first_round))                       # the report lists what was retrieved oldest first
        in_calls = [i for i, _ in self.reads(st) if i not in {m["interaction_id"] for m in st.retrieval_report["retrieved"] if m["found_by"] == "reference"}]
        self.assertEqual([dates[i] for i in in_calls], sorted(dates[i] for i in in_calls))                              # and the first round is called oldest first
        self.assertEqual([m["date"] for m in st.retrieval_report["retrieved"]], sorted(m["date"] for m in st.retrieval_report["retrieved"]))
        # LIMITATION (documented, B3): a meeting reached through a reference is read in a SECOND round, so the execution order of the calls is not globally chronological;
        # every turn carries its date and the report above is chronological.


# ==================================================================================================================================================
class FrozenKenji(unittest.TestCase):
    """The real conversation record (data/conversations.csv, checked against its committed sha256) and the real tool."""

    @classmethod
    def setUpClass(cls):
        cls.st, _, _ = TS.run([TS.finish()], p=TS.plan(clients=(K,), tools=("get_source_evidence",), task_type="history", request_text=KENJI), text=KENJI)
        cls.conv = shared()
        cls.got = {}
        for e in cls.st.executions:
            if e.status == "success" and e.arguments.get("interaction_id"):
                for t in e.result.data["turns"]:
                    cls.got[(t["interaction_id"], t["turn_index"])] = t

    def test_the_source_data_is_the_frozen_one(self):
        lock = (ROOT / "datasets" / "calls" / "conversations.sha256").read_text(encoding="utf-8").split()[0]
        self.assertEqual(hashlib.sha256((ROOT / "data" / "conversations.csv").read_bytes()).hexdigest(), lock)

    def test_generic_request_words_are_not_searched(self):
        self.assertEqual([e.arguments["search_text"] for e in self.st.executions if "search_text" in e.arguments], ["retirement"])

    def test_int_049_is_retrieved_with_its_decisive_turns_and_their_real_text(self):
        for i in (4, 5, 6, 7):
            self.assertIn(("INT-049", i), self.got, f"turn {i} of INT-049 was not retrieved")
        t4, t5, t6, t7 = (self.got[("INT-049", i)] for i in (4, 5, 6, 7))
        self.assertEqual((t4["speaker"], t5["speaker"], t6["speaker"], t7["speaker"]), ("Client", "Advisor", "Client", "Advisor"))
        self.assertIn("I was only exploring the possibility", t4["text"])
        self.assertIn("65 is still the date we're planning against", t4["text"])
        self.assertIn("The wording of my file note was too strong", t5["text"])
        self.assertIn("I don't want someone looking at that note later and assuming I'd committed", t6["text"])
        self.assertIn("The original discussion stays on record exactly as it happened", t7["text"])
        for (iid, i), t in self.got.items():                                                                  # every retrieved turn is genuine, attributed and dated
            row = self.conv.turn(iid, i)
            self.assertEqual((t["speaker"], t["text"]), (row["speaker"], row["utterance"]))
            self.assertEqual(row["client_id"], K)

    def test_every_meeting_whose_title_is_about_retirement_is_retrieved(self):
        titled = {r["interaction_id"] for r in self.conv.rows if r["client_id"] == K and "retir" in (r.get("meeting_context") or "").lower()}
        self.assertEqual(titled, {"INT-001", "INT-012", "INT-027", "INT-049", "INT-074", "INT-090"})
        self.assertLessEqual(titled, {i for i, _ in self.got})

    def test_int_055s_reference_resolves_to_int_049_in_the_real_data(self):
        t = self.conv.turn("INT-055", 1)
        self.assertIn("Since March 4, when we focused on correcting the retirement-age record", t["utterance"])
        refs, unres = SD.find_references([dict(interaction_id="INT-055", turn_index=1, text=t["utterance"])], self.conv, K)
        self.assertEqual([(r["interaction_id"], r["date"], r["kind"]) for r in refs], [("INT-049", "2026-03-04", "date")])
        self.assertEqual(unres, [])
        self.assertTrue(SD.reference_is_relevant(refs[0], self.conv, K, SD.subject_stems(["retirement"])))
        self.assertTrue(refs[0]["clause"].startswith("march 4") and "retirement-age record" in refs[0]["clause"] and "portfolio risk" not in refs[0]["clause"])

    def test_a_reference_to_a_meeting_about_something_else_is_not_followed(self):
        t = self.conv.turn("INT-049", 1)                                           # "We last spoke on March 2, about share vesting and tax planning."
        refs, _ = SD.find_references([dict(interaction_id="INT-049", turn_index=1, text=t["utterance"])], self.conv, K)
        r = next(r for r in refs if r["interaction_id"] == "INT-046")
        self.assertFalse(SD.reference_is_relevant(r, self.conv, K, SD.subject_stems(["retirement"])))
        self.assertNotIn("INT-046", {i for i, _ in self.got})

    def test_budget_and_transparency(self):
        used = sum(1 for e in self.st.executions if e.status in ("success", "error", "timeout"))
        self.assertLessEqual(used, 12)
        rep = self.st.retrieval_report
        self.assertEqual(rep["budget"]["used"], used)
        self.assertEqual(rep["budget"]["reserved_for_references"], 2)
        self.assertTrue(rep["not_retrieved"])                                                                  # the budget cannot hold everything: the rest is named, not hidden
        self.assertTrue(any(m.startswith("Not retrieved:") for m in self.st.missing_information))
        self.assertIn(("INT-055#1", "march 4", "INT-049"), [(r["from"], r["quote"], r["interaction_id"]) for r in rep["references_resolved"]])
        self.assertEqual(rep["references_unresolved"], [])

    def test_retrieval_is_deterministic_on_the_real_data(self):
        st2, _, _ = TS.run([TS.finish()], p=TS.plan(clients=(K,), tools=("get_source_evidence",), task_type="history", request_text=KENJI), text=KENJI)
        sig = lambda s: [(e.arguments.get("search_text"), e.arguments.get("interaction_id"), e.arguments.get("turn_indices")) for e in s.executions]
        self.assertEqual(sig(self.st), sig(st2))
        self.assertEqual(self.st.retrieval_report, st2.retrieval_report)

    def test_no_turn_is_retrieved_twice(self):
        turns = [(e.arguments["interaction_id"], t) for e in self.st.executions if e.status == "success" and e.arguments.get("interaction_id") for t in e.arguments["turn_indices"]]
        self.assertEqual(len(turns), len(set(turns)))


if __name__ == "__main__":
    unittest.main()
