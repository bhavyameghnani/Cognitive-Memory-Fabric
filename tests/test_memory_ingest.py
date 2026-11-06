"""Stages 2-4 and 8: extraction, validation, classification, persistence, idempotency, evidence traceability."""
import unittest

from test_memory_support import *  # noqa: F401,F403

from agents.memory import ingest as ing  # noqa: E402
from agents.memory.store import EvidenceError  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from schemas.memory import MemoryCandidate  # noqa: E402


def mc(**kw):
    return MemoryCandidate(**cand(**kw))


class ValidationTests(unittest.TestCase):
    def run_filter(self, iid, cands, shown=None):
        ctx = ing.MeetingContext.load(CONV, iid)
        return ing.filter_memory_candidates([(c, "conversation_llm") for c in cands], ctx, CONV, shown)

    def test_unshown_turn_rejected(self):
        d = self.run_filter("INT-090", [mc(turn=11, excerpt=K090_T11, topic="retirement_age", certainty="confirmed", value="63", role="Advisor")], shown={2})
        self.assertEqual(d[0].decision, "rejected")
        self.assertIn("not shown", d[0].reason)

    def test_fabricated_excerpt_rejected_and_near_miss_repaired(self):
        fake = self.run_filter("INT-090", [mc(turn=2, excerpt="We have fully decided to retire at sixty", topic="retirement_age", certainty="confirmed", value="60")])
        self.assertEqual(fake[0].decision, "rejected")
        near = self.run_filter("INT-090", [mc(turn=2, excerpt="We've decided: 63", topic="retirement_age", certainty="confirmed", value="63")])
        self.assertTrue(near[0].kept)                                   # trailing period missing is still a real substring
        sloppy = self.run_filter("INT-090", [mc(turn=11, excerpt="You've decided to retire at 63 replacing the earlier plan of 65 because the role changed", topic="retirement_age",
                                                certainty="confirmed", value="63", role="Advisor")])
        self.assertTrue(sloppy[0].kept)
        self.assertTrue(CONV.verify_excerpt("CLIENT-001", "INT-090", 11, sloppy[0].final.excerpt))

    def test_speaker_role_comes_from_the_data(self):
        d = self.run_filter("INT-090", [mc(turn=2, excerpt=K090_T2, topic="retirement_age", certainty="confirmed", value="63", role="Advisor")])
        self.assertEqual(d[0].final.speaker_role, "Client")

    def test_topic_other_and_empty_value_are_inferred(self):
        d = self.run_filter("INT-001", [mc(turn=13, excerpt=K001_T13, topic="other", certainty="confirmed", value=None, role="Advisor")])
        self.assertEqual((d[0].final.topic, d[0].final.value), ("retirement_age", "65"))
        d = self.run_filter("INT-001", [mc(turn=13, excerpt=K001_T13, topic="retirement_income", certainty="confirmed", value="a month", role="Advisor")])
        self.assertEqual((d[0].final.topic, d[0].final.value), ("retirement_age", "65"))      # unsupported value dropped, then inferred

    def test_unsupported_value_dropped(self):
        d = self.run_filter("INT-012", [mc(turn=11, excerpt=K012_T11, topic="retirement_age", certainty="exploratory", value="70", role="Advisor")])
        self.assertEqual(d[0].final.value, "60")
        self.assertTrue(any("unsupported" in n for n in d[0].notes))

    def test_certainty_downgrade_is_recorded(self):
        d = self.run_filter("INT-012", [mc(turn=4, excerpt=K012_T4, topic="retirement_age", certainty="confirmed", value="60")])
        self.assertEqual((d[0].decision, d[0].certainty_in, d[0].certainty_out), ("downgraded", "confirmed", "exploratory"))

    def test_duplicates_merged_and_low_relevance_rejected(self):
        c = mc(turn=11, excerpt=K090_T11, topic="retirement_age", certainty="confirmed", value="63", role="Advisor")
        d = self.run_filter("INT-090", [c, c])
        self.assertEqual([x.decision for x in d], ["accepted", "merged"])
        noise = self.run_filter("INT-099", [mc(turn=11, excerpt="Good question.", topic="other", certainty="confirmed", role="Advisor"),
                                            mc(turn=2, excerpt="Good. Keep it tight, I've got a plant to run.", topic="other", certainty="unconfirmed")])
        self.assertTrue(all(x.decision == "rejected" for x in noise))


class ClassificationTests(unittest.TestCase):
    def classify(self, iid, c, origin="conversation_llm"):
        ctx = ing.MeetingContext.load(CONV, iid)
        d = ing.filter_memory_candidates([(mc(**c), origin)], ctx, CONV)[0]
        return d, ing.classify_memory(d)

    def test_routing_buckets_and_status(self):
        d, cls = self.classify("INT-090", dict(turn=11, excerpt=K090_T11, topic="retirement_age", certainty="confirmed", value="63", role="Advisor"))
        self.assertEqual((ing.route_memory(cls), cls.status, cls.event_type, cls.semantic_eligible), (["episodic"], "active", "decision", True))
        d, cls = self.classify("INT-012", dict(turn=11, excerpt=K012_T11, topic="retirement_age", certainty="exploratory", value="60", role="Advisor"))
        self.assertFalse(cls.semantic_eligible)                         # exploratory never reaches semantic memory
        d, cls = self.classify("INT-001", dict(turn=6, excerpt="In retirement I think we'd want fifteen thousand, with more travel and health costs I haven't budgeted for.",
                                               topic="retirement_income", certainty="confirmed"))
        self.assertEqual((d.final.certainty, cls.status), ("unconfirmed", "unconfirmed"))

    def test_procedural_restrictions(self):
        whenever = "It is there for stability, and the interest is generally exempt from federal income tax, so we compare it against Treasuries whenever we rebalance."
        _, adv = self.classify("INT-090", dict(turn=9, excerpt=whenever, topic="asset_allocation", certainty="recommendation", role="Advisor", proc=True))
        self.assertTrue(adv.procedure_proposal)
        self.assertEqual(adv.buckets, ["episodic", "procedural"])
        _, client = self.classify("INT-090", dict(turn=2, excerpt=K090_T2, topic="retirement_age", certainty="confirmed", value="63", proc=True))
        self.assertFalse(client.procedure_proposal)                     # a client statement can never create a procedure
        _, ordinary = self.classify("INT-090", dict(turn=5, excerpt="I'll change the formal planning date from 65 to 63.", topic="retirement_age",
                                                    certainty="recommendation", role="Advisor", proc=True))
        self.assertFalse(ordinary.procedure_proposal)                   # ordinary advisor instruction: no reusable-procedure wording
        _, rule = self.classify("INT-090", dict(turn=9, excerpt=whenever, topic="asset_allocation", certainty="recommendation", role="Advisor", proc=True), origin="conversation_rule")
        self.assertFalse(rule.procedure_proposal)

    def test_proposed_procedure_is_stored_unapproved_and_hidden_from_retrieval(self):
        whenever = "It is there for stability, and the interest is generally exempt from federal income tax, so we compare it against Treasuries whenever we rebalance."
        orch, store, _, _ = make({"INT-090": [cand(9, whenever, "asset_allocation", "recommendation", role="Advisor", proc=True)]})
        orch.ingest_meeting("INT-090")
        pro = [r for r in store.records("CLIENT-001", "procedural")]
        self.assertEqual([(r["status"], r["approval_status"], r["origin"]) for r in pro], [("candidate", "proposed", "conversation_llm")])
        ctx = orch.retrieve(plan("how do we rebalance", ["CLIENT-001"], ["asset_allocation"], cats=("procedural", "episodic")))["CLIENT-001"]
        self.assertEqual([p for p in ctx.procedures if p.origin == "conversation_llm"], [])
        # explicit human approval makes it retrievable
        orch.update_memory_lifecycle(pro[0]["memory_id"], "active", "reviewed with compliance", "advisor:priya", approval_status="approved", principal=person("advisor:priya", "CLIENT-001"))
        ctx = orch.retrieve(plan("how do we rebalance", ["CLIENT-001"], ["asset_allocation"], cats=("procedural",), rid="REQ-2"))["CLIENT-001"]
        self.assertIn(pro[0]["memory_id"], [p.memory_id for p in ctx.procedures])

    def test_curated_procedures_loaded_as_approved_and_labelled(self):
        orch, store, _, _ = make()
        rows = store.records("CLIENT-001", "procedural", include_global=True)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["approval_status"] == "approved" and r["origin"] == "curated_file" and r["title"].startswith("[curated]") and r["client_id"] == "GLOBAL" for r in rows))
        ctx = orch.retrieve(plan("retirement readiness", ["CLIENT-001"], ["retirement_age"], cats=("procedural",)))["CLIENT-001"]
        self.assertEqual([p.procedure_name for p in ctx.procedures], ["Retirement readiness review"])
        self.assertIn("not a projection", " ".join(ctx.procedures[0].steps))


class IngestionRunTests(unittest.TestCase):
    def test_rule_safety_net_recovers_what_the_model_missed(self):
        orch, store, _, _ = make({})                                    # model returns nothing at all
        r = orch.ingest_meeting("INT-090")
        eps = store.records("CLIENT-001", "episodic")
        self.assertEqual({e["origin"] for e in eps}, {"conversation_rule"})
        self.assertEqual({(e["certainty"], e["topic"], e["value_text"]) for e in eps}, {("confirmed", "retirement_age", "63")})
        self.assertEqual(store.count("semantic_details"), 1)            # via consolidation, from rule-origin confirmed episodes that passed the same validation
        r = orch.ingest_meeting("INT-012")
        self.assertEqual([e["certainty"] for e in store.records("CLIENT-001", "episodic") if e["interaction_id"] == "INT-012"], ["exploratory"])

    def test_degraded_when_model_output_is_invalid_then_retried(self):
        bad = {"candidates": [cand(10, "What would make you recommend a change?", "retirement_age", "confirmed")]}   # turn 10 is never shown in INT-090
        orch, store, _, _ = make({})
        orch.llm = MockLLM(handlers={"memory": [bad, bad, bad]})
        r = orch.ingest_meeting("INT-090")
        self.assertIsNotNone(r["llm_error"])
        self.assertEqual(store.ingestion_status("INT-090"), "degraded")
        self.assertGreaterEqual(store.count("memory_records", "memory_type='episodic'"), 1)   # the rule net still worked
        orch.llm = MockLLM(handlers={"memory": lambda *a: {"candidates": []}})
        self.assertFalse(orch.ingest_meeting("INT-090")["skipped"])                           # degraded meetings are retried
        self.assertEqual(store.ingestion_status("INT-090"), "completed")

    def test_llm_down_does_not_crash_ingestion(self):
        orch, store, _, _ = make({})
        orch.llm = MockLLM(handlers={"memory": lambda *a: LLMError("endpoint unavailable")})
        r = orch.ingest_meeting("INT-090")
        self.assertIn("unavailable", r["llm_error"])
        self.assertEqual(store.ingestion_status("INT-090"), "degraded")

    def test_idempotent_reingestion(self):
        orch, store, script, _ = make(KENJI_SCRIPT)
        for i in KENJI_MEETINGS:
            orch.ingest_meeting(i)
        snap = {t: store.count(t) for t in ("memory_records", "memory_evidence", "memory_relationships", "memory_lifecycle_events", "memory_candidates", "ingestion_log")}
        calls = len(orch.llm.calls)
        for i in KENJI_MEETINGS:
            self.assertTrue(orch.ingest_meeting(i)["skipped"])
        self.assertEqual(len(orch.llm.calls), calls)                    # skipped meetings cost no LLM call
        self.assertEqual(snap, {t: store.count(t) for t in snap})
        self.assertEqual(store.count("ingestion_log"), len(KENJI_MEETINGS))
        # even a forced re-run (e.g. after the log was cleared) does not duplicate memories: content-hash deduplication
        for i in KENJI_MEETINGS:
            orch.ingest_meeting(i, force=True)
        self.assertEqual(snap["memory_records"], store.count("memory_records"))
        self.assertEqual(snap["memory_evidence"], store.count("memory_evidence"))
        self.assertEqual(snap["memory_relationships"], store.count("memory_relationships"))

    def test_every_candidate_and_reason_is_audited(self):
        orch, store, _, _ = make(KENJI_SCRIPT)
        orch.ingest_meeting("INT-012")
        rows = store.query("SELECT * FROM {S}memory_candidates WHERE interaction_id='INT-012' ORDER BY origin, turn_index")
        decisions = {(r["origin"], r["turn_index"], r["decision"]) for r in rows}
        self.assertIn(("conversation_llm", 4, "downgraded"), decisions)
        self.assertIn(("conversation_rule", 11, "merged"), decisions)
        self.assertTrue(all(r["reason"] for r in rows))

    def test_ingest_client_checks_ownership(self):
        orch, _, _, _ = make()
        with self.assertRaises(ValueError):
            orch.ingest_client("CLIENT-002", ["INT-090"])
        with self.assertRaises(ValueError):
            orch.ingest_client("CLIENT-999")

    def test_extraction_prompt_has_no_sql_or_credentials(self):
        orch, _, script, _ = make(KENJI_SCRIPT)
        orch.ingest_meeting("INT-090")
        system, user = script.prompts[0]
        text = (system + user).lower()
        for banned in ("select ", "insert ", "postgres", "password", "cmf_memory", "sqlite", "api_key"):
            self.assertNotIn(banned, text)
        self.assertIn("[turn 11 | advisor]", user.lower())
        self.assertNotIn("portfolio is roughly", user)                  # read-out sentences are not shown to the model


class EvidenceTests(unittest.TestCase):
    def test_every_conversation_memory_is_traceable_to_a_real_turn(self):
        orch, store, _, _ = make({**KENJI_SCRIPT, **REBECCA_SCRIPT})
        for i in KENJI_MEETINGS + list(REBECCA_SCRIPT):
            orch.ingest_meeting(i)
        rows = store.query("SELECT m.client_id, m.memory_type, m.origin, e.* FROM {S}memory_evidence e JOIN {S}memory_records m ON m.memory_id=e.memory_id WHERE e.source_type='conversation'")
        self.assertGreater(len(rows), 10)
        for r in rows:
            self.assertTrue(CONV.verify_excerpt(r["client_id"], r["interaction_id"], r["turn_index"], r["excerpt"]), r)
            self.assertEqual(CONV.turn(r["interaction_id"], r["turn_index"])["speaker"], r["speaker"])
        for m in store.query("SELECT memory_id, memory_type FROM {S}memory_records WHERE origin IN ('conversation_llm','conversation_rule','consolidation') AND memory_type IN ('episodic','semantic')"):
            self.assertGreaterEqual(store.count("memory_evidence", "memory_id=?", (m["memory_id"],)), 1, m)

    def test_store_refuses_fake_or_foreign_evidence(self):
        orch, store, _, _ = make()
        rec = lambda cid, h_: dict(memory_id="MEM-X-" + h_, client_id=cid, memory_type="episodic", topic="retirement_age", content="x", certainty="confirmed", status="active", origin="conversation_llm", content_hash=h_)
        det = dict(interaction_id="INT-090", turn_index=2, event_ts="2026-05-01T08:15:00", speaker_role="Client", event_type="decision")
        good = dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt=K090_T2)
        self.assertTrue(store.insert_memory(rec("CLIENT-001", "a"), det, [good], principal=svc("ingestion", "CLIENT-001"))[1])
        for bad in (dict(good, excerpt="We have decided to retire at 55"), dict(good, turn_index=99), dict(good, speaker="Advisor")):
            with self.assertRaises(EvidenceError):
                store.insert_memory(rec("CLIENT-001", "b"), det, [bad], principal=svc("ingestion", "CLIENT-001"))
        with self.assertRaises(EvidenceError):                           # real turn, wrong client
            store.insert_memory(rec("CLIENT-002", "c"), det, [good], principal=svc("ingestion", "CLIENT-002"))
        self.assertEqual(store.count("memory_records", "memory_type='episodic'"), 1)       # failed inserts rolled back completely
        self.assertEqual(store.count("memory_lifecycle_events", "memory_id='MEM-X-b'"), 0)


if __name__ == "__main__":
    unittest.main()
