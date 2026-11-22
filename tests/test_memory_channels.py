"""Multichannel source adapter (services/conversations.py, CMF_MEMORY_CHANNELS): generated meetings, email threads and chats behind the Conversations
interface, so the integrity boundary verifies them with its existing checks.  Scripted models only; every excerpt is genuine source text."""
import re
import unittest

from test_memory_support import Clock, MemoryOrchestrator, MemoryStore, OrchestratorConfig  # noqa: F401
from test_llm_first import Extract, Verify  # noqa: E402

from agents.memory import ingest as ing  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from schemas.memory import MemoryCandidate  # noqa: E402
from services.conversations import CHANNELS, Conversations, MultiChannelConversations, memory_channels, restricted_records  # noqa: E402

MC = MultiChannelConversations()
SOFIA = "CLIENT-003"
CORRECTION = "My January 26 email said you had decided to buy within two years."          # THR-001 message 4, sent 2026-04-13


class Extract2(Extract):
    def __call__(self, system, user, schema):
        self.prompts.append(user)
        return {"candidates": self.by_iid.get(re.search(r"Conversation ((?:INT|THR|CHT|MTG)-\d+)", user).group(1), [])}


def cand(turn, excerpt, topic, certainty, role, value=None, statement=None):
    return dict(topic=topic, subject="home_purchase_timing", statement=statement or excerpt, value=value, certainty=certainty, speaker_role=role, turn_index=turn,
                excerpt=excerpt, corrects_value=None, reusable_procedure=False)


class AdapterTests(unittest.TestCase):
    def test_default_is_the_frozen_conversations_only(self):
        self.assertEqual(memory_channels({}), ())
        self.assertEqual(memory_channels({"CMF_MEMORY_CHANNELS": "conversations"}), ())
        self.assertEqual(memory_channels({"CMF_MEMORY_CHANNELS": "all"}), CHANNELS)
        self.assertEqual(memory_channels({"CMF_MEMORY_CHANNELS": "email, chat, bogus"}), ("email", "chat"))
        plain = Conversations()
        self.assertEqual((len(plain.meetings), len(plain.rows)), (150, 2199))

    def test_extra_channels_load_with_original_rows_untouched(self):
        ids = set(MC.meetings)
        self.assertTrue({i for i in ids if i.startswith("INT-")} == set(Conversations().meetings))
        self.assertTrue(any(i.startswith(p) for i in ids) for p in ("THR-", "CHT-", "MTG-"))
        self.assertEqual({r["channel"] for r in MC.rows if not r["interaction_id"].startswith("INT-")}, {"meeting", "email", "chat"})

    def test_records_restricted_from_agents_are_not_loaded(self):
        blocked = restricted_records()
        self.assertTrue(blocked)
        self.assertEqual({b for b in blocked if not b.startswith("INT-")} & set(MC.meetings), set())

    def test_each_message_keeps_its_own_timestamp(self):
        ts = [r["interaction_datetime"][:10] for r in MC.meeting("THR-001")]
        self.assertEqual(ts[0], "2026-01-26")
        self.assertEqual(ts[3], "2026-04-13")

    def test_third_parties_are_labelled(self):
        tp = [r for r in MC.rows if r["speaker"] == "Third party"]
        self.assertTrue(tp)
        self.assertTrue(all(not r["speaker_id"].startswith(("CLIENT-", "ADVISOR-")) for r in tp))


class EmailIngestTests(unittest.TestCase):
    def orch(self, by_iid, verify=None):
        m = MockLLM(handlers={"memory": Extract2(by_iid), "memory_verifier": verify or Verify("correction")})
        store = MemoryStore(url="sqlite:///:memory:", conv=MC, clock=Clock())
        o = MemoryOrchestrator(m, store=store, conv=MC, config=OrchestratorConfig(ingest_mode="llm_first"))
        o.init()
        return o, store

    def test_email_memory_passes_the_boundary_dated_by_its_own_message(self):
        self.assertTrue(MC.verify_excerpt(SOFIA, "THR-001", 4, CORRECTION))
        o, store = self.orch({"THR-001": [cand(4, CORRECTION, "housing_property", "correction", "Advisor",
                                               statement="The advisor corrected the January 26 email that said Sofia had decided to buy within two years.")]})
        out = o.ingest_meeting("THR-001")
        self.assertEqual(out["stored"], 1)
        ep = store.records(SOFIA, "episodic")
        self.assertEqual([(e["event_date"], e["certainty"]) for e in ep], [("2026-04-13", "correction")])
        ev = store.evidence_map([ep[0]["memory_id"]])[ep[0]["memory_id"]]
        self.assertEqual([(e.interaction_id, e.turn_index) for e in ev], [("THR-001", 4)])

    def test_third_party_message_cannot_be_cited(self):
        row = next(r for r in MC.rows if r["speaker"] == "Third party")
        from agents.memory import rules
        sent = next(x for x in rules.split_sentences(row["utterance"]) if len(x) > 40)
        self.assertTrue(MC.verify_excerpt(row["client_id"], row["interaction_id"], row["turn_index"], sent))
        c = cand(row["turn_index"], sent, "tax_planning", "confirmed", "Client")
        ctx = ing.MeetingContext.load(MC, row["interaction_id"])
        from agents.memory import llm_first
        final, why, _, _ = llm_first.fact_check(MemoryCandidate(**c), ctx, MC)
        self.assertIsNone(final)
        self.assertIn("third-party", why)
        rules_path = ing.filter_memory_candidates([(MemoryCandidate(**c), "conversation_llm")], ctx, MC)
        self.assertEqual(rules_path[0].decision, "rejected")
        self.assertIn("third-party", rules_path[0].reason)

    def test_email_transcript_shows_sender_and_date(self):
        from agents.memory import llm_first
        body = llm_first.transcript(ing.MeetingContext.load(MC, "THR-001"))
        self.assertIn("[turn 4 | Advisor: Elena Rossi | 2026-04-13 10:20]", body)


if __name__ == "__main__":
    unittest.main()


class ConsolidationFallbackTests(unittest.TestCase):
    """A version whose earliest creator is rejected by the write validator (a reported preference) is created by the next confirmed episode with the
    same value, not lost.  Real case: MTG-004 (Apr 28, "She'd be happier with 63 than 65.") before the INT-090 decision (May 1, "We've decided: 63.")."""

    def test_rejected_creator_falls_back_to_the_later_decision(self):
        from harness.memory_write_validator import MemoryWriteValidator
        pref = dict(topic="retirement_age", subject="retirement_age", statement="Kenji and Mariko discussed that she would prefer him to retire at 63 rather than 65.",
                    value="63", certainty="confirmed", speaker_role="Client", turn_index=8, excerpt="She'd be happier with 63 than 65.", corrects_value=None,
                    reusable_procedure=False)
        dec = dict(pref, statement="Kenji and Mariko have decided to retire at 63.", turn_index=2, excerpt="We've decided: 63.")
        self.assertTrue(MC.verify_excerpt("CLIENT-001", "MTG-004", 8, pref["excerpt"]))
        m = MockLLM(handlers={"memory": Extract2({"MTG-004": [pref], "INT-090": [dec]}), "memory_verifier": Verify("confirmed")})
        store = MemoryStore(url="sqlite:///:memory:", conv=MC, clock=Clock())
        o = MemoryOrchestrator(m, store=store, conv=MC, config=OrchestratorConfig(ingest_mode="llm_first"), write_validator=MemoryWriteValidator(MC, store))
        o.init()
        o.ingest_meeting("MTG-004")
        self.assertEqual(store.records("CLIENT-001", "semantic"), [])          # the preference alone never becomes a current value
        out = o.ingest_meeting("INT-090")
        sem = [(s["value_text"], s["valid_from"], s["status"]) for s in store.records("CLIENT-001", "semantic")]
        self.assertEqual(sem, [("63", "2026-05-01", "active")])
        self.assertGreaterEqual(out["consolidation"].get("rejected_versions", 0), 1)
