"""Retrieval, working memory, lifecycle, consolidation guards, leakage and database constraints."""
import sqlite3
import unittest
from datetime import timedelta

from test_memory_support import *  # noqa: F401,F403

from agents.memory import consolidate as cons  # noqa: E402
from agents.memory.config import EPISODE_CAP, OrchestratorConfig, RetrievalConfig  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from schemas.tools import Envelope, Provenance, ToolExecution  # noqa: E402


def ingest(orch, ids):
    for i in ids:
        orch.ingest_meeting(i)


class RetrievalTests(unittest.TestCase):
    def test_episode_cap_defaults_to_8_and_reports_omitted(self):
        self.assertEqual(EPISODE_CAP, 8)
        self.assertEqual(RetrievalConfig().episode_cap, 8)
        orch, _, _, _ = make(KENJI_SCRIPT, cfg=OrchestratorConfig(retrieval=RetrievalConfig(episode_cap=3)))
        ingest(orch, KENJI_MEETINGS)
        ctx = orch.retrieve(plan("How did Kenji's retirement target evolve?", [KENJI], ["retirement_age"], task_type="history", needs_history=True))[KENJI]
        self.assertEqual((len(ctx.episodes), ctx.episodes_omitted), (3, 4))
        self.assertTrue(any("4 further episode(s) omitted" in n for n in ctx.notes))
        self.assertEqual([e.event_date for e in ctx.episodes], sorted(e.event_date for e in ctx.episodes))   # still chronological
        self.assertIn("2026-05-01", [e.event_date for e in ctx.episodes])                                   # ranking favours the confirmed decision

    def test_memory_type_filter_follows_the_plan_categories(self):
        orch, _, _, _ = make(KENJI_SCRIPT)
        ingest(orch, KENJI_MEETINGS)
        ctx = orch.retrieve(plan("current target", [KENJI], ["retirement_age"], cats=("semantic",)))[KENJI]
        self.assertEqual(([m.value_text for m in ctx.current], ctx.episodes, ctx.history, ctx.procedures, ctx.working), (["63"], [], [], [], []))
        ctx = orch.retrieve(plan("what happened", [KENJI], ["retirement_age"], cats=("episodic",)))[KENJI]
        self.assertEqual((ctx.current, len(ctx.episodes)), ([], 7))

    def test_topic_falls_back_to_keywords_on_the_request(self):
        orch, _, _, _ = make(KENJI_SCRIPT)
        ingest(orch, KENJI_MEETINGS)
        ctx = orch.retrieve(plan("When does Kenji plan to retire?", [KENJI], []))[KENJI]
        self.assertEqual(ctx.topics[0], "retirement_age")
        self.assertEqual([m.value_text for m in ctx.current], ["63"])

    def test_no_cross_client_leakage(self):
        orch, store, _, _ = make({**KENJI_SCRIPT, **REBECCA_SCRIPT})
        ingest(orch, KENJI_MEETINGS + list(REBECCA_SCRIPT))
        both = orch.retrieve(plan("compare retirement and the sale", [KENJI, REBECCA], ["retirement_age", "business_sale", "care_costs", "liquidity_reserve"],
                                  cats=("semantic", "episodic", "procedural", "working")))
        for cid, ctx in both.items():
            items = ctx.current + ctx.history + ctx.episodes + ctx.unresolved + ctx.working
            self.assertTrue(items)
            self.assertEqual({m.client_id for m in items} - {cid}, set(), cid)
            for m in items:
                for ev in m.evidence:
                    self.assertEqual(CONV.turn(ev.interaction_id, ev.turn_index)["client_id"], cid)
            for r in ctx.relations:
                self.assertEqual({store.get(r.source_id)["client_id"], store.get(r.target_id)["client_id"]}, {cid})
        self.assertEqual([m.topic for m in both[KENJI].current], ["retirement_age"])
        self.assertNotIn("retirement_age", {m.topic for m in both[REBECCA].current + both[REBECCA].episodes})
        # Kenji-only request must not surface Rebecca's rows even for her topics
        ctx = orch.retrieve(plan("business sale", [KENJI], ["business_sale"], rid="REQ-K"))[KENJI]
        self.assertEqual((ctx.current, ctx.episodes), ([], []))
        with self.assertRaises(ValueError):                              # the store refuses cross-client relationships
            a = store.records(KENJI, "episodic")[0]["memory_id"]
            b = store.records(REBECCA, "episodic")[0]["memory_id"]
            store.add_relationship(a, b, "related_to")

    def test_unknown_client_is_reported_not_guessed(self):
        orch, _, _, _ = make()
        ctx = orch.retrieve(plan("x", ["CLIENT-999"], ["retirement_age"]))["CLIENT-999"]
        self.assertEqual((ctx.current, ctx.episodes), ([], []))
        self.assertIn("unknown client", ctx.notes[0])
        self.assertEqual(orch.retrieve(plan("x", [], [])), {})


class WorkingMemoryTests(unittest.TestCase):
    def setUp(self):
        self.orch, self.store, _, self.clock = make(KENJI_SCRIPT)
        ingest(self.orch, KENJI_MEETINGS)
        self.plan = plan("Can Kenji retire at the planned age?", [KENJI], ["retirement_age"], cats=("semantic", "working"), task_type="forecast_feasibility", rid="REQ-W")
        ex = ToolExecution(id="EX-REQ-W-01", iteration=1, tool="get_client_goals", arguments={"client_id": KENJI}, status="success",
                           result=Envelope(tool="get_client_goals", client_id=KENJI, data={"retirement_target_age": 62}, provenance=Provenance(source="cmf_ops")))
        self.summaries = ["Funding gap check pending", ex]

    def test_scope_ttl_and_expiry(self):
        sem_before = self.store.count("memory_records", "memory_type IN ('semantic','episodic')")
        ids = self.orch.store_working("REQ-W", self.plan, self.summaries)
        self.assertEqual(len(ids), 3)                                    # task context + 2 tool summaries
        ctx = self.orch.retrieve(self.plan, "REQ-W")[KENJI]
        texts = [w.content for w in ctx.working]
        self.assertTrue(any(t.startswith("Analysis uses retirement age 63") and "MEM-SEM-" in t for t in texts))          # values being used + derived-from id
        self.assertTrue(any("get_client_goals" in t and "synthetic operational data" in t for t in texts))
        self.assertTrue(all(w.session_id == "REQ-W" and w.certainty == "n/a" and w.source_role == "System" for w in ctx.working))
        # other session sees nothing
        other = self.orch.retrieve(plan("x", [KENJI], ["retirement_age"], cats=("working",), rid="REQ-OTHER"))[KENJI]
        self.assertEqual(other.working, [])
        # never promoted: no new episodic/semantic rows and no tool number stored as something the client said
        self.assertEqual(self.store.count("memory_records", "memory_type IN ('semantic','episodic')"), sem_before)
        self.assertEqual(self.store.count("memory_records", "memory_type='working' AND origin NOT IN ('orchestrator','tool_summary')"), 0)
        self.assertEqual(self.store.count("memory_evidence", "source_type='conversation' AND memory_id IN (SELECT memory_id FROM {S}memory_records WHERE memory_type='working')"), 0)
        exp = {w.expires_at for w in ctx.working}
        self.assertEqual(exp, {(self.clock.t + timedelta(hours=4)).replace(microsecond=0).isoformat()})                   # default TTL 4 h
        # still visible just before expiry, gone after, with lifecycle events and rows retained
        self.clock.advance(hours=3, minutes=59)
        self.assertTrue(self.orch.retrieve(self.plan, "REQ-W")[KENJI].working)
        self.clock.advance(minutes=2)
        self.assertEqual([w for w in self.orch.retrieve(self.plan, "REQ-W")[KENJI].working if w.session_id == "REQ-W" and w.status == "active"], [])
        rows = self.store.records(KENJI, "working")
        self.assertTrue(rows and all(r["status"] == "expired" for r in rows))
        ev = self.store.lifecycle_events(rows[0]["memory_id"])
        self.assertEqual([(e["from_status"], e["to_status"], e["actor"]) for e in ev], [(None, "active", "orchestrator"), ("active", "expired", "system")])

    def test_summary_for_another_client_or_ambiguous_scope_is_skipped(self):
        two = plan("compare", [KENJI, REBECCA], ["retirement_age"], cats=("working",), rid="REQ-W2")
        ids = self.orch.store_working("REQ-W2", two, ["no client named", dict(client_id=SOFIA, summary="Sofia only"), dict(client_id=REBECCA, summary="Rebecca balance check")])
        contents = [self.store.get(i)["content"] for i in ids]
        self.assertFalse(any("no client named" in c or "Sofia" in c for c in contents))
        self.assertTrue(any("Rebecca balance check" in c and self.store.get(i)["client_id"] == REBECCA for i, c in zip(ids, contents)))
        kenji_ctx = self.orch.retrieve(two, "REQ-W2")[KENJI]
        self.assertTrue(kenji_ctx.working)
        self.assertFalse(any("Rebecca" in w.content or w.client_id != KENJI for w in kenji_ctx.working))      # Rebecca's working note is not in Kenji's context

    def test_working_ttl_is_configurable(self):
        ids = self.orch.store_working("REQ-T", self.plan, [], ttl_hours=0.5)
        self.assertEqual(self.store.get(ids[0])["expires_at"], (self.clock.t + timedelta(minutes=30)).replace(microsecond=0).isoformat())


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.orch, self.store, _, _ = make(KENJI_SCRIPT)
        ingest(self.orch, KENJI_MEETINGS)

    def test_events_are_append_only_and_complete(self):
        v1 = self.store.records(KENJI, "semantic")[0]
        for r in self.store.query("SELECT memory_id FROM {S}memory_records"):
            self.assertGreaterEqual(len(self.store.lifecycle_events(r["memory_id"])), 1, "every memory has a creation event")
        sem = {s["version_no"]: s for s in self.store.records(KENJI, "semantic")}
        ev = self.store.lifecycle_events(sem[1]["memory_id"])
        self.assertEqual([(e["from_status"], e["to_status"], e["related_memory_id"]) for e in ev], [(None, "active", None), ("active", "superseded", sem[2]["memory_id"])])
        n = self.store.count("memory_lifecycle_events")
        if self.store.kind == "sqlite":
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.execute("UPDATE {S}memory_lifecycle_events SET reason='tampered'")
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.execute("DELETE FROM {S}memory_lifecycle_events")
        self.assertEqual(self.store.count("memory_lifecycle_events"), n)
        self.assertFalse(any(m for m in dir(self.store) if "delete" in m.lower()))   # the store exposes no delete at all

    def test_explicit_dispute_and_guarded_transitions(self):
        sem = next(s for s in self.store.records(KENJI, "semantic") if s["status"] == "active")
        for actor in ("", "llm", "consolidation"):
            with self.assertRaises(PermissionError):
                self.orch.update_memory_lifecycle(sem["memory_id"], "disputed", "x", actor)
        with self.assertRaises(PermissionError):
            self.orch.update_memory_lifecycle(sem["memory_id"], "disputed", "", "advisor:priya")
        r = self.orch.update_memory_lifecycle(sem["memory_id"], "disputed", "client says 62", "advisor:priya")
        self.assertEqual((r["from_status"], r["to_status"]), ("active", "disputed"))
        ctx = self.orch.retrieve(plan("target", [KENJI], ["retirement_age"]))[KENJI]
        self.assertEqual([m.status for m in ctx.current], ["disputed"])
        self.assertTrue(any("disputed" in c for c in ctx.contradictions))
        with self.assertRaises(ValueError):                              # no 'promotion' of an exploratory episode by lifecycle action
            expl = next(e for e in self.store.records(KENJI, "episodic") if e["certainty"] == "exploratory")
            self.orch.update_memory_lifecycle(expl["memory_id"], "active", "make it a decision", "advisor:priya")
        with self.assertRaises(ValueError):                              # approving a non-procedure
            self.orch.update_memory_lifecycle(sem["memory_id"], "active", "ok", "advisor:priya", approval_status="approved")

    def test_no_automatic_dispute(self):
        self.assertEqual(self.store.count("memory_records", "status='disputed'"), 0)


class ConsolidationGuardTests(unittest.TestCase):
    def test_only_confirmed_or_correction_with_a_usable_value_is_eligible(self):
        ep = lambda **k: dict(dict(certainty="confirmed", status="active", value_text="63", topic="retirement_age"), **k)
        self.assertTrue(cons.eligible(ep()))
        self.assertTrue(cons.eligible(ep(certainty="correction")))
        for c in ("exploratory", "hypothetical", "recommendation", "unconfirmed"):
            self.assertFalse(cons.eligible(ep(certainty=c)), c)
        self.assertFalse(cons.eligible(ep(status="unconfirmed")))
        self.assertFalse(cons.eligible(ep(value_text=None)))
        self.assertFalse(cons.eligible(ep(value_text="sometime soon")))        # numeric topic needs a number
        self.assertTrue(cons.eligible(ep(topic="business_sale", value_text="decline employee ownership")))

    def test_same_day_conflicting_confirmed_values_are_one_event(self):
        # the model mislabels the 65 in the same recap as the value: both excerpts are real, both are confirmed, same day, different values
        script = {"INT-090": [cand(2, K090_T2, "retirement_age", "confirmed", "63"), cand(11, K090_T11, "retirement_age", "confirmed", "65", role="Advisor")]}
        orch, store, _, _ = make(script)
        orch.ingest_meeting("INT-090")
        sem = store.records(KENJI, "semantic")
        self.assertEqual([(s["value_text"], s["version_no"]) for s in sem], [("63", 1)])       # merged: no second version, no supersession
        ctx = orch.retrieve(plan("target", [KENJI], ["retirement_age"]))[KENJI]
        self.assertTrue(any("65" in c and "63" in c for c in ctx.contradictions))

    def test_reconsolidation_is_idempotent_and_deterministic(self):
        orch, store, _, _ = make(KENJI_SCRIPT)
        ingest(orch, KENJI_MEETINGS)
        snap = {t: store.count(t) for t in ("memory_records", "memory_relationships", "memory_lifecycle_events", "memory_evidence")}
        for _ in range(3):
            orch.consolidator.consolidate_client(KENJI)
        self.assertEqual(snap, {t: store.count(t) for t in snap})
        # a second, independent database built from the same input produces the same chain ids
        orch2, store2, _, _ = make(KENJI_SCRIPT)
        ingest(orch2, KENJI_MEETINGS)
        self.assertEqual([s["memory_id"] for s in store.records(KENJI, "semantic")], [s["memory_id"] for s in store2.records(KENJI, "semantic")])

    def test_semantic_is_never_written_by_ingestion_itself(self):
        orch, store, _, _ = make(KENJI_SCRIPT)
        import agents.memory.ingest as ing
        ctx = ing.MeetingContext.load(CONV, "INT-090")
        d = ing.filter_memory_candidates([(ing.MemoryCandidate(**KENJI_SCRIPT["INT-090"][0]), "conversation_llm")], ctx, CONV)[0]
        self.assertNotIn("semantic", ing.route_memory(ing.classify_memory(d)))
        self.assertNotIn("working", ing.route_memory(ing.classify_memory(d)))


class DatabaseTests(unittest.TestCase):
    def test_constraints(self):
        orch, store, _, _ = make()
        base = dict(memory_id="M1", client_id=KENJI, memory_type="semantic", topic="retirement_age", content="x", certainty="confirmed", status="active", origin="consolidation",
                    content_hash="h1", valid_from="2026-05-01", valid_to="2026-01-01")
        det = dict(slot_key="retirement_age|retirement_age", basis="b", supporting_episodes=1)
        with self.assertRaises(sqlite3.IntegrityError):                  # valid_to >= valid_from
            store.insert_memory(base, det)
        for bad in (dict(status="bogus"), dict(certainty="maybe"), dict(memory_type="other"), dict(confidence=1.5), dict(origin="llm")):
            with self.assertRaises(sqlite3.IntegrityError):
                store.insert_memory(dict(base, valid_to=None, content_hash="h2", **bad), det)
        self.assertEqual(store.count("memory_records"), 3)               # only the 3 curated procedures: all failed inserts rolled back
        with self.assertRaises(sqlite3.IntegrityError):                  # self-supersession
            store.insert_memory(dict(base, valid_to=None, content_hash="h3", supersedes_id="M1"), det)
        with self.assertRaises(sqlite3.IntegrityError):                  # incomplete conversation evidence
            store.execute("INSERT INTO {S}memory_evidence (evidence_id, memory_id, source_type) VALUES ('e','x','conversation')")

    def test_unique_content_hash_per_client_and_type(self):
        orch, store, _, _ = make(KENJI_SCRIPT)
        ingest(orch, ["INT-090"])
        row = store.records(KENJI, "episodic")[0]
        again = {k: row[k] for k in ("memory_id", "client_id", "memory_type", "topic", "content", "certainty", "status", "origin", "content_hash")}
        mid, created = store.insert_memory(again, dict(interaction_id="INT-090", turn_index=2, event_ts="x", speaker_role="Client", event_type="decision"))
        self.assertFalse(created)

    def test_reset_and_file_database(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            url = "sqlite:///" + os.path.join(d, "m.db")
            orch, store, _, _ = make(KENJI_SCRIPT, url=url)
            ingest(orch, ["INT-090"])
            self.assertGreater(store.count("memory_records"), 3)
            store.close()
            s2 = MemoryStore(url=url, conv=CONV)
            self.assertGreater(s2.count("memory_records"), 3)            # persisted
            s2.init()                                                    # idempotent, keeps data
            self.assertGreater(s2.count("memory_records"), 3)
            s2.init(reset=True)
            self.assertEqual(s2.count("memory_records"), 0)
            self.assertEqual(s2.count("schema_migrations"), 1)

    def test_default_url_and_env_switch(self):
        from agents.memory.config import DEFAULT_DB_URL, database_url
        self.assertEqual(DEFAULT_DB_URL, "sqlite:///build/cmf_memory.db")
        os.environ["CMF_MEMORY_DATABASE_URL"] = "sqlite:///:memory:"
        try:
            self.assertEqual(database_url(), "sqlite:///:memory:")
        finally:
            del os.environ["CMF_MEMORY_DATABASE_URL"]
        with self.assertRaises(ValueError):
            MemoryStore(url="mysql://nope")

    def test_only_store_imports_a_database_driver(self):
        import pathlib, re
        for p in pathlib.Path(__file__).resolve().parents[1].joinpath("src/agents/memory").glob("*.py"):
            if p.name != "store.py":
                self.assertIsNone(re.search(r"^\s*(import|from)\s+(sqlite3|psycopg|psycopg2)", p.read_text(), re.M), p.name)

    def test_schema_files_cover_the_spec_tables(self):
        sql = (pathlib_root() / "sql" / "100_cmf_memory_schema.sql").read_text()
        for t in ("schema_migrations", "ingestion_log", "memory_records", "episodic_details", "semantic_details", "procedural_details", "working_context", "memory_evidence",
                  "memory_relationships", "memory_lifecycle_events", "memory_candidates"):
            self.assertIn(f"CREATE TABLE cmf_memory.{t} ", sql)
        self.assertNotIn("cmf_ops.", sql)
        self.assertEqual((pathlib_root() / "sql" / "199_drop_cmf_memory.sql").read_text().count("DROP SCHEMA IF EXISTS cmf_memory CASCADE"), 1)


def pathlib_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parents[1]


import os  # noqa: E402

if __name__ == "__main__":
    unittest.main()
