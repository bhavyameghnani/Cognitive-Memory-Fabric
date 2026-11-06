"""P1.4 - memory integrity boundary: write boundary, provenance, client isolation, temporal integrity, idempotency, corrections, audit, migration.
Deterministic: real transcript turns from data/conversations.csv, in-memory or temp-file SQLite, Barriers instead of sleeps.  No network, no LLM (MockLLM scripts only)."""
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory.errors import (EvidenceError, EvidenceRejected, InvalidRecord, MemoryIntegrityUnavailable, MemoryWriteRejected, ScopeDenied)  # noqa: E402
from agents.memory.integrity import Principal, classify_temporal  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from test_memory_support import (CONV, K090_T2, K090_T11, KENJI, KENJI_MEETINGS, KENJI_SCRIPT, REBECCA, REBECCA_SCRIPT, Clock, make)  # noqa: E402

KEV = dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt=K090_T2)
KEV2 = dict(source_type="conversation", interaction_id="INT-090", turn_index=11, speaker="Advisor", excerpt=K090_T11)
REB_EX = REBECCA_SCRIPT["INT-061"][0]["excerpt"]
RTS = CONV.turn("INT-061", 11)["interaction_datetime"]            # the real time of Rebecca's cited turn
REV = dict(source_type="conversation", interaction_id="INT-061", turn_index=11, speaker="Advisor", excerpt=REB_EX)
EDET = dict(interaction_id="INT-090", turn_index=2, event_ts="2026-05-01T08:15:00", speaker_role="Client", event_type="decision")
RDET = dict(interaction_id="INT-061", turn_index=11, event_ts=RTS, speaker_role="Advisor", event_type="decision")                      # Rebecca's real cited turn
KP = Principal.user("advisor-kenji", [KENJI], request_id="REQ-P14", authenticated=True)             # a verified person: may transition / correct / supersede, never create
RP = Principal.user("advisor-rebecca", [REBECCA], request_id="REQ-P14R", authenticated=True)
KI = Principal.service("ingestion", [KENJI], request_id="REQ-P14")                                # the ingestion service for Kenji: the ONLY way a transcript-derived memory is created
RI = Principal.service("ingestion", [REBECCA], request_id="REQ-P14R")


TURNS = {   # label -> (interaction, turn, speaker, excerpt, interaction_datetime, value stated in the excerpt)   (all real rows of data/conversations.csv)
    "JAN": ("INT-012", 4, "Client", "But it's got me thinking: what if I retired at 60 instead of 65?", "2026-01-16T08:00:00", "60"),
    "MAR": ("INT-049", 17, "Advisor", "I've corrected my own record: my earlier note that you wanted to retire at 60 was too strong; you are exploring it, and 65 remains the planning date.",
            "2026-03-04T08:30:00", "65"),
    "APR": ("INT-074", 13, "Advisor", "You're still weighing retiring at 63 instead of 60, which I've recorded as an idea and not an instruction.", "2026-04-16T08:15:00", "63"),
    "MAY": ("INT-090", 2, "Client", "We've decided: 63.", "2026-05-01T08:15:00", "63"),
}


def at(store, label, mid, principal=KI, **kw):
    """Create an episodic memory that quotes the real turn `label` (verbatim content, the value the turn states, the turn's own date and time)."""
    iid, turn, speaker, ex, ts, val = TURNS[label]
    ev = dict(source_type="conversation", interaction_id=iid, turn_index=turn, speaker=speaker, excerpt=ex)
    base = dict(content=ex, value_text=val, event_date=ts[:10], source_role=speaker, certainty="exploratory" if label in ("JAN", "MAR", "APR") else "confirmed")
    base.update(kw)
    det = dict(interaction_id=iid, turn_index=turn, event_ts=ts, speaker_role=speaker, event_type="decision")
    return store.insert_memory(erec(mid, **base), det, [ev], principal=principal)


def erec(mid, client=KENJI, **kw):
    base = dict(memory_id=mid, client_id=client, memory_type="episodic", topic="retirement_age", subject="retirement_age", content="We've decided: 63.", value_text="63",
                certainty="confirmed", source_role="Client", status="active", origin="conversation_llm", event_date="2026-05-01", content_hash=kw.pop("h", mid))
    base.update(kw)
    return base


def put(store, mid, ev=(KEV,), principal=KI, det=EDET, **kw):
    return store.insert_memory(erec(mid, **kw), dict(det), list(ev), principal=principal)


def rig(url="sqlite:///:memory:"):
    orch, store, _, clock = make(url=url)
    log = []
    store.boundary.emit_fn = lambda c, n, **k: log.append((n, k))
    return store, log, clock


def row(store, mid):
    return store.get(mid)


def codes(store):
    return [(r["op"], r["outcome"], json.loads(r["reason_codes"])) for r in store.query("SELECT * FROM {S}memory_write_log ORDER BY log_id")]


def names(log):
    return [n for n, _ in log]


# ===================================================================================================== write boundary
def test_every_mutator_goes_through_the_boundary(monkeypatch):
    orch, store, _, _ = make(KENJI_SCRIPT)
    seen = []
    for m in ("create", "transition", "link", "support"):
        orig = getattr(store.boundary, m)
        monkeypatch.setattr(store.boundary, m, (lambda o, n: lambda *a, **k: (seen.append(n), o(*a, **k))[1])(orig, m))
    for i in KENJI_MEETINGS:                                  # ingestion + consolidation + supersession + supporting evidence + support counts
        orch.ingest_meeting(i)
    store.set_approval(store.records("GLOBAL", "procedural")[0]["memory_id"], "approved", "approver-1", principal=Principal.user("approver-1", ["GLOBAL"], authenticated=True))
    orch.update_memory_lifecycle(store.records(KENJI, "episodic")[0]["memory_id"], "disputed", "checking", "reviewer-1", principal=Principal.user("reviewer-1", [KENJI], authenticated=True))
    orch.store_working("REQ-1", __import__("test_memory_support").plan("t", [KENJI], []), [])
    assert {"create", "transition", "link", "support"} <= set(seen)
    ops = {r["op"] for r in store.query("SELECT DISTINCT op FROM {S}memory_write_log")}
    assert {"create", "transition", "link", "approval"} <= ops                  # and every one of them left a durable audit row


def test_raw_sql_cannot_write_the_guarded_tables():
    store, _, _ = rig()
    put(store, "M1")
    before = {t: store.count(t) for t in ("memory_records", "memory_evidence", "memory_relationships", "memory_lifecycle_events", "episodic_details")}
    attempts = [
        "INSERT INTO {S}memory_records (memory_id, client_id, memory_type, topic, content, certainty, status, origin, content_hash, created_at, updated_at) "
        "VALUES ('RAW', 'CLIENT-001', 'episodic', 't', 'c', 'confirmed', 'active', 'conversation_llm', 'rawhash', 'x', 'x')",
        "UPDATE {S}memory_records SET status='expired' WHERE memory_id='M1'",
        "UPDATE {S}memory_records SET row_version=99 WHERE memory_id='M1'",
        "DELETE FROM {S}memory_records WHERE memory_id='M1'",
        "INSERT INTO {S}memory_relationships (rel_id, source_id, target_id, rel_type, created_at) VALUES ('r','M1','M1x','related_to','x')",
        "INSERT INTO {S}memory_lifecycle_events (event_id, memory_id, to_status, reason, actor, created_at) VALUES ('e','M1','active','r','a','x')",
        "DELETE FROM {S}memory_evidence", "DELETE FROM {S}episodic_details", "UPDATE {S}memory_evidence SET excerpt='forged'",
    ]
    for sql in attempts:
        with pytest.raises(sqlite3.IntegrityError, match="outside the integrity boundary"):
            store.execute(sql)
    assert before == {t: store.count(t) for t in before}
    assert row(store, "M1")["status"] == "active"


def test_content_is_immutable_even_inside_the_gate():
    store, _, _ = rig()
    put(store, "M1")
    for col, val in (("content", "'edited'"), ("value_text", "'99'"), ("client_id", "'CLIENT-002'"), ("verification", "'verified'"), ("origin", "'curated_file'")):
        with store._gate(), pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.execute(f"UPDATE {{S}}memory_records SET {col}={val} WHERE memory_id='M1'")


def test_a_foreign_connection_fails_closed(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    store, _, _ = rig(url)
    put(store, "M1")
    other = sqlite3.connect(tmp_path / "m.db")                       # any other tool or process: it has no cmf_write_allowed() function
    for sql in ("UPDATE memory_records SET status='expired'", "DELETE FROM memory_records", "INSERT INTO memory_evidence (evidence_id, memory_id, source_type) VALUES ('z','M1','curated')"):
        with pytest.raises(sqlite3.Error):
            other.execute(sql)
    other.close()
    assert row(store, "M1")["status"] == "active"


def test_invalid_writes_never_reach_persistence_and_carry_structured_reasons():
    store, log, _ = rig()
    n = store.count("memory_records")
    for bad, code in ((dict(status="bogus"), "invalid_record"), (dict(confidence=1.5), "invalid_record"), (dict(origin="llm"), "invalid_record"),
                      (dict(valid_from="2026-05-01", valid_to="2026-01-01"), "invalid_record"), (dict(verification="verified"), "protected_field_supplied"),
                      (dict(created_by="admin"), "protected_field_supplied"), (dict(content="CLIENT-002 said hello"), "forged_identity")):
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(erec("BAD", **bad), dict(EDET), [KEV], principal=KI)
        assert e.value.code == code and e.value.reasons == [code] and e.value.operation_id.startswith("OP-")
    assert store.count("memory_records") == n and store.get("BAD") is None
    assert [c[1] for c in codes(store)].count("rejected") == 7
    assert "memory_write_committed" not in names(log) and names(log).count("memory_write_rejected") == 7


def test_a_failure_during_persistence_leaves_nothing_behind(monkeypatch):
    store, log, _ = rig()
    monkeypatch.setattr(store, "_p_evidence", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    n = {t: store.count(t) for t in ("memory_records", "episodic_details", "memory_lifecycle_events", "memory_write_log")}
    with pytest.raises(RuntimeError):
        put(store, "M1")
    assert n == {t: store.count(t) for t in n}                                  # record, detail row, lifecycle event and log row all rolled back
    assert "memory_write_committed" not in names(log)
    monkeypatch.undo()
    assert put(store, "M1")[1] is True                                           # the retry works and is not mistaken for a replay


def _offline(path, statements):
    """What an offline tool (or an old build) does: its OWN connection, not the application's store."""
    raw = sqlite3.connect(path)
    for stmt in statements:
        raw.execute(stmt)
    raw.commit()
    raw.close()


def test_an_unmigrated_database_refuses_writes(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    s = MemoryStore(url=url, conv=CONV)
    _offline(tmp_path / "old.db", s._script("100_cmf_memory_schema.sql"))
    with pytest.raises(MemoryIntegrityUnavailable):
        put(s, "M1")
    s.close()


# ===================================================================================================== provenance
def test_missing_and_unresolvable_sources_are_rejected():
    store, log, _ = rig()
    with pytest.raises(MemoryWriteRejected) as e:
        put(store, "M1", ev=())
    assert e.value.code == "provenance_missing" and "memory_provenance_missing" in names(log)
    for bad in (dict(KEV, excerpt="We have decided to retire at 55"), dict(KEV, turn_index=99), dict(KEV, speaker="Advisor")):
        with pytest.raises(EvidenceRejected) as e2:                              # an EvidenceError too: existing callers keep working
            put(store, "M2", ev=(bad,))
        assert e2.value.code == "evidence_unresolved" and isinstance(e2.value, EvidenceError)
    assert store.count("memory_records", "memory_id IN ('M1','M2')") == 0


def test_a_cross_client_evidence_handle_is_refused_without_naming_the_other_client():
    store, log, _ = rig()
    with pytest.raises(EvidenceRejected) as e:
        store.insert_memory(erec("M1", client=REBECCA), dict(EDET), [KEV], principal=RI)       # a real turn, but Kenji's
    assert "CLIENT-001" not in str(e.value) and "CLIENT-001" not in json.dumps(log, default=str)
    with pytest.raises(EvidenceRejected):
        store.insert_memory(erec("M2"), dict(EDET), [REV], principal=KI)                      # Rebecca's turn for Kenji's memory


def test_llm_extraction_is_verified_only_when_the_extracted_value_is_supported_by_the_excerpt():
    store, _, _ = rig()
    put(store, "OK")
    put(store, "NOVAL", h="n2", value_text="99")
    ok, bad = row(store, "OK"), row(store, "NOVAL")
    assert (ok["verification"], bad["verification"]) == ("verified", "unverified") and "not supported" in bad["verification_basis"]
    assert ok["source_class"] == "llm_extraction" and "paraphrase" in ok["verification_basis"]


def test_summaries_and_working_context_never_inherit_verification_and_tool_refs_resolve_only_for_this_request():
    store, _, _ = rig()
    wrec = lambda mid, **kw: dict(memory_id=mid, client_id=KENJI, memory_type="working", topic="other", content="tool said 63", certainty="n/a", status="active", origin="tool_summary",
                                  content_hash=mid, **kw)
    wdet = dict(session_id="S", plan_id="P", derived_from_id=None, expires_at="2099-01-01T00:00:00")
    own = Principal.service("orchestrator", [KENJI], evidence_refs=["EXEC-1"])
    store.insert_memory(wrec("W1"), wdet, [dict(source_type="tool", ref="EXEC-1", role="primary")], principal=own)
    store.insert_memory(wrec("W2"), wdet, [dict(source_type="tool", ref="EXEC-OTHER-REQUEST", role="primary")], principal=own)
    assert row(store, "W1")["verification"] == row(store, "W2")["verification"] == "unverified"
    res = {r["memory_id"]: r["resolution"] for r in store.query("SELECT memory_id, resolution FROM {S}memory_evidence WHERE memory_id IN ('W1','W2')")}
    assert res == {"W1": "resolved", "W2": "unresolvable"}                      # the limitation is recorded, not hidden
    assert "cannot be independently retrieved" in row(store, "W2")["verification_basis"]


def test_a_confirmed_memory_without_resolvable_evidence_is_refused():
    store, _, _ = rig()
    with pytest.raises(MemoryWriteRejected) as e:
        store.insert_memory(erec("M1", origin="conversation_llm"), dict(EDET), [], principal=KI)
    assert e.value.code in ("certainty_requires_evidence", "provenance_missing")


def test_provenance_columns_are_filled_by_the_boundary_not_the_caller():
    store, _, clock = rig()
    put(store, "M1")
    r = row(store, "M1")
    assert (r["created_by"], r["request_id"], r["row_version"], r["source_class"]) == ("ingestion", "REQ-P14", 1, "llm_extraction")
    assert r["observed_at"] == "2026-05-01T08:15:00" and r["created_at"] == store.now() and r["observed_at"] != r["created_at"]       # observation vs ingestion time
    assert r["operation_id"].startswith("OP-") and r["temporal_status"] == "current"
    ev = store.query("SELECT resolution, verified_at FROM {S}memory_evidence WHERE memory_id='M1'")[0]
    assert ev["resolution"] == "resolved" and ev["verified_at"]


def test_consolidated_memory_inherits_only_from_a_verified_episode_and_keeps_provenance_through_supersession():
    orch, store, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    sem = store.records(KENJI, "semantic")
    assert sem and all(s["source_class"] == "derived" and s["verification"] == "verified" and s["created_by"] == "consolidation" for s in sem)
    sup = [s for s in sem if s["status"] == "superseded"]
    assert sup
    for s in sup:                                                                   # superseded history keeps its source and provenance
        assert store.count("memory_evidence", "memory_id=?", (s["memory_id"],)) >= 1 and s["verification_basis"]
        assert store.query("SELECT 1 AS x FROM {S}memory_lifecycle_events WHERE memory_id=? AND to_status='superseded'", (s["memory_id"],))


# ===================================================================================================== authorization and client isolation
def test_cross_client_write_is_denied_and_audited():
    store, log, _ = rig()
    with pytest.raises(ScopeDenied) as e:
        store.insert_memory(erec("M1"), dict(EDET), [KEV], principal=RI)
    assert e.value.code == "client_scope_denied" and store.get("M1") is None
    assert ("create", "rejected", ["client_scope_denied"]) in codes(store) and "memory_scope_denied" in names(log)


def test_missing_identity_and_missing_scope_fail_closed():
    store, _, _ = rig()
    for kwargs, code in ((dict(actor=""), "missing_principal"), (dict(actor=None), "missing_principal"), (dict(principal=Principal("", "user", frozenset([KENJI]))), "malformed_principal"),
                         (dict(principal=Principal.service("ingestion", [])), "client_scope_denied")):
        with pytest.raises(ScopeDenied) as e:
            store.insert_memory(erec("M1"), dict(EDET), [KEV], **kwargs)
        assert e.value.code == code
    with pytest.raises(ScopeDenied) as e:
        store.insert_memory({k: v for k, v in erec("M1").items() if k != "client_id"}, dict(EDET), [KEV], principal=KI)
    assert e.value.code == "missing_client_scope" and store.get("M1") is None


def test_foreign_and_nonexistent_memories_look_identical_to_the_caller():
    store, log, _ = rig()
    put(store, "M1")
    errs = []
    for target in ("M1", "DOES-NOT-EXIST"):
        with pytest.raises(MemoryWriteRejected) as e:
            store.boundary.transition(target, "disputed", "because", principal=RP)
        errs.append((e.value.code, e.value.message))
        with pytest.raises(MemoryWriteRejected) as e2:
            store.boundary.correct(target, principal=RP, expected_version=1, new_value="60", evidence=[KEV], reason="because")
        errs.append((e2.value.code, e2.value.message))
    assert errs[0] == errs[2] and errs[1] == errs[3] and errs[0][0] == "memory_not_found"
    assert "M1" not in json.dumps([k for n, k in log if n == "memory_scope_denied"], default=str).replace("DOES-NOT-EXIST", "")
    assert row(store, "M1")["status"] == "active"


def test_relationships_and_supersession_never_cross_clients():
    store, _, _ = rig()
    put(store, "K1")
    store.insert_memory(erec("R1", client=REBECCA, h="r1", content="decline", event_date=RTS[:10]), dict(EDET, interaction_id="INT-061", turn_index=11, speaker_role="Advisor", event_ts=RTS), [REV], principal=RI)
    with pytest.raises(ValueError):
        store.add_relationship("K1", "R1", "related_to", principal=KP)
    with pytest.raises(ValueError):
        store.add_relationship("K1", "R1", "related_to", principal=Principal.service("consolidation", [KENJI, REBECCA]))
    with pytest.raises(MemoryWriteRejected):                                       # a consolidation job scoped to ANOTHER client cannot touch Kenji's rows
        store.set_status("K1", "superseded", "x", "consolidation", principal=Principal.service("consolidation", [REBECCA]))


def test_services_are_limited_to_their_operations_and_unknown_services_do_not_exist():
    store, _, _ = rig()
    put(store, "K1")
    with pytest.raises(ScopeDenied) as e:
        store.set_status("K1", "disputed", "x", "ingestion", principal=KI)
    assert e.value.code == "operation_not_permitted"
    with pytest.raises(ValueError):
        Principal.service("made-up-service", [KENJI])


def test_forged_identity_in_generated_content_is_not_trusted():
    store, _, _ = rig()
    with pytest.raises(MemoryWriteRejected) as e:
        store.insert_memory(erec("M1", content="CLIENT-002 asked to retire at 63", h="f1"), dict(EDET), [KEV], principal=KI)
    assert e.value.code == "forged_identity"
    with pytest.raises(MemoryWriteRejected) as e2:                                  # a generated record cannot assert its own author or verification
        store.insert_memory(erec("M2", created_by="supervisor-1", verification="verified"), dict(EDET), [KEV], principal=KI)
    assert e2.value.code == "protected_field_supplied"
    put(store, "OK")
    assert row(store, "OK")["created_by"] == "ingestion"


def test_concurrent_writes_with_conflicting_scopes_cannot_cross(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    a, _, _ = rig(url)
    b = MemoryStore(url=url, conv=CONV)
    barrier, out = threading.Barrier(2), {}

    def go(name, st, who):
        barrier.wait()
        try:
            st.insert_memory(erec("RACE"), dict(EDET), [KEV], principal=who)
            out[name] = "ok"
        except MemoryWriteRejected as e:
            out[name] = e.code
    ts = [threading.Thread(target=go, args=("kenji", a, KI)), threading.Thread(target=go, args=("rebecca", b, RI))]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert out == {"kenji": "ok", "rebecca": "client_scope_denied"}
    assert a.count("memory_records", "memory_id='RACE'") == 1 and row(a, "RACE")["client_id"] == KENJI
    b.close()


# ===================================================================================================== temporal integrity
def test_planned_intentions_are_never_read_as_completed_and_completed_events_are_not_current():
    assert classify_temporal(dict(certainty="confirmed", event_date="2026"), "The client plans to retire at 60") == "planned"
    assert classify_temporal(dict(certainty="confirmed", event_date="2026"), "The client retired at 60") == "historical"
    assert classify_temporal(dict(certainty="confirmed", event_date="2026"), "We've decided: 63.") == "current"
    assert classify_temporal(dict(certainty="exploratory", event_date="2026"), "still weighing it") == "planned"
    assert classify_temporal(dict(certainty="confirmed", event_date="2026", valid_to="2026-06-01"), "anything") == "historical"
    assert classify_temporal(dict(certainty="unconfirmed"), "no markers at all") == "unknown"
    assert classify_temporal(dict(certainty="confirmed", status="superseded"), "x") == "superseded"


def test_a_plan_and_its_later_completion_are_both_kept_without_a_false_contradiction():
    store, log, _ = rig()
    at(store, "JAN", "PLAN", h="p1", certainty="exploratory")                      # "what if I retired at 60 ..." (January)
    at(store, "MAY", "DONE", h="p2")                                               # "We've decided: 63." (May)
    assert row(store, "PLAN")["status"] == "active" and row(store, "DONE")["status"] == "active"                          # nothing overwritten
    assert row(store, "PLAN")["temporal_status"] == "planned"
    assert not [k["conflict"] for n, k in log if n == "memory_conflict_detected" and k["conflict"].startswith("contradiction")]      # a plan and its later completion are not a contradiction


def test_contradictory_observations_on_the_same_date_are_both_preserved_and_flagged():
    store, log, _ = rig()
    put(store, "A", h="a", value_text="63")
    at(store, "MAY", "B", h="b", value_text="65", content="We've decided: 63. (65)")
    ks = [k["conflict"] for n, k in log if n == "memory_conflict_detected"]
    assert "contradiction_same_period" in ks
    assert row(store, "A")["status"] == row(store, "B")["status"] == "active" and row(store, "A")["value_text"] == "63"
    assert any("contradiction_same_period" in c[2] for c in codes(store) if c[1] == "new")


def test_a_late_arriving_older_observation_does_not_become_current():
    store, log, _ = rig()
    at(store, "MAY", "NEW", h="n")                                                 # the May decision is stored first ...
    at(store, "JAN", "OLD", h="o", certainty="confirmed")                          # ... and a January observation arrives late (stated as a decision here; its verification is not the point)
    assert row(store, "OLD")["temporal_status"] == "historical" and row(store, "NEW")["status"] == "active" and row(store, "OLD")["created_at"] >= row(store, "NEW")["created_at"]
    assert "late_arriving_historical" in [k["conflict"] for n, k in log if n == "memory_conflict_detected"]


def test_an_unverified_statement_conflicting_with_a_verified_fact_is_flagged_and_cannot_supersede_it():
    store, log, _ = rig()
    put(store, "V", h="v")
    at(store, "APR", "U", h="u", value_text="99", content="maybe 99")                # a value its source does not support: stored, but unverified
    assert row(store, "U")["verification"] == "unverified" and row(store, "V")["status"] == "active"
    assert "unverified_vs_verified" in [k["conflict"] for n, k in log if n == "memory_conflict_detected"]
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.supersede("V", "U", "newer", principal=KP, expected_version=1)
    assert e.value.code == "unverified_cannot_override_verified" and row(store, "V")["status"] == "active"


def test_the_existing_temporal_detector_is_what_flags_unconsolidated_updates():
    orch, store, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS[:4]:                                  # everything BEFORE the May decision is consolidated ...
        orch.ingest_meeting(i)
    log = []
    store.boundary.emit_fn = lambda c, n, **k: log.append((n, k))
    at(store, "MAY", "LATE", h="late", certainty="confirmed")                  # ... so the May decision is a newer, unconsolidated confirmed value
    assert "unconsolidated_update" in [k["conflict"] for n, k in log if n == "memory_conflict_detected"]       # harness.temporal_conflict.detect, not a second implementation


# ===================================================================================================== idempotency
def test_the_same_payload_twice_creates_one_memory_and_replays_the_original_outcome():
    store, log, _ = rig()
    r1 = store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-1")
    r2 = store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-1")
    assert (r1.outcome, r1.replayed, r2.replayed, r2.outcome, r2.memory_id, r2.operation_id) == ("new", False, True, "new", "M1", r1.operation_id)
    assert store.count("memory_records", "memory_id='M1'") == 1 and "memory_ingestion_replayed" in names(log)
    assert put(store, "M1")[1] is False                                              # the plain facade reports created=False


def test_reusing_a_key_with_a_different_payload_is_rejected():
    store, _, _ = rig()
    store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-1")
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.create(erec("M2", h="m2", value_text="60"), dict(EDET), [KEV], principal=KI, idempotency_key="K-1")
    assert e.value.code == "idempotency_key_conflict" and store.get("M2") is None
    # keys are scoped to the client and operation: the same key text for another client is a different key
    store.boundary.create(erec("R1", client=REBECCA, content="decline", h="r1", event_date=RTS[:10]), dict(EDET, interaction_id="INT-061", turn_index=11, speaker_role="Advisor", event_ts=RTS), [REV], principal=RI, idempotency_key="K-1")
    assert store.get("R1") is not None


def test_concurrent_identical_ingestions_create_exactly_one_memory(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    a, _, _ = rig(url)
    stores = [a, MemoryStore(url=url, conv=CONV), MemoryStore(url=url, conv=CONV), MemoryStore(url=url, conv=CONV)]
    barrier, out = threading.Barrier(len(stores)), []

    def go(st):
        barrier.wait()
        out.append(st.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-C"))
    ts = [threading.Thread(target=go, args=(s,)) for s in stores]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(r.outcome for r in out) == ["new"] * 4 and sum(not r.replayed for r in out) == 1 and sum(r.replayed for r in out) == 3
    assert a.count("memory_records", "memory_id='M1'") == 1 and a.count("memory_write_log", "op='create' AND outcome='new' AND memory_id='M1'") == 1
    for s in stores[1:]:
        s.close()


def test_a_crash_before_commit_leaves_no_trace_and_the_retry_runs_fresh(monkeypatch):
    store, log, _ = rig()
    monkeypatch.setattr(store, "_p_event", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-X")
    assert store.get("M1") is None and store.count("memory_write_log", "idempotency_key='K-X'") == 0 and "memory_write_committed" not in names(log)
    monkeypatch.undo()
    r = store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-X")
    assert r.outcome == "new" and not r.replayed


def test_a_crash_after_commit_but_before_the_response_is_recovered_by_the_retry():
    store, log, _ = rig()
    store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-Y")     # committed; the caller 'never saw' the response
    assert "memory_write_committed" in names(log)
    again = store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-Y")
    assert again.replayed and again.memory_id == "M1" and store.count("memory_records", "memory_id='M1'") == 1


def test_a_rejected_write_replays_its_original_rejection():
    store, log, _ = rig()
    bad = erec("M1", status="bogus")
    with pytest.raises(MemoryWriteRejected) as first:
        store.boundary.create(bad, dict(EDET), [KEV], principal=KI, idempotency_key="K-R")
    with pytest.raises(MemoryWriteRejected) as second:
        store.boundary.create(bad, dict(EDET), [KEV], principal=KI, idempotency_key="K-R")
    assert second.value.replayed and second.value.code == first.value.code == "invalid_record" and second.value.operation_id == first.value.operation_id
    assert store.count("memory_write_log", "idempotency_key='K-R'") == 1


def test_a_retry_cannot_bypass_authorization_or_provenance_validation():
    store, _, _ = rig()
    store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=KI, idempotency_key="K-Z")
    with pytest.raises(ScopeDenied):                                                 # right key, wrong principal: denied BEFORE the replay lookup
        store.boundary.create(erec("M1"), dict(EDET), [KEV], principal=RI, idempotency_key="K-Z")
    with pytest.raises(MemoryWriteRejected):                                         # the same key with the provenance stripped is a different payload
        store.boundary.create(erec("M1"), dict(EDET), [], principal=KI, idempotency_key="K-Z")
    squat = store.count("memory_write_log", "idempotency_key='K-Z'")
    assert squat == 1                                                                # a denied stranger never reserved or changed the key


def test_duplicate_sources_and_similar_but_distinct_facts_are_not_merged():
    store, _, _ = rig()
    put(store, "A", h="a")
    at(store, "JAN", "B", h="b")
    put(store, "C", h="c", value_text="63", content="We've decided: 63 (restated)", event_date="2026-05-01")                                                       # near-identical text
    assert {r["memory_id"] for r in store.records(KENJI, "episodic")} == {"A", "B", "C"}
    assert store.count("memory_evidence", "interaction_id='INT-090' AND turn_index=2") == 2


# ===================================================================================================== corrections
def test_an_authorized_correction_preserves_history_and_provenance():
    store, log, _ = rig()
    at(store, "JAN", "OLD")                                                                     # January: "what if I retired at 60 ..." (an exploration)
    before = dict(row(store, "OLD"))
    res = store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="63", evidence=[KEV2], reason="the client restated the date")      # May: "decided to retire at 63"
    new, old = row(store, res.memory_id), row(store, "OLD")
    assert res.outcome == "corrected" and new["supersedes_id"] == "OLD" and new["value_text"] == "63" and new["corrects_value"] == "60"
    assert (new["source_class"], new["created_by"], new["verification"]) == ("human_correction", "advisor-kenji", "verified")
    assert old["status"] == "superseded" and old["row_version"] == 2
    for k in ("content", "value_text", "verification", "created_by", "observed_at", "source_class", "created_at"):
        assert old[k] == before[k]                                                       # the previous version is untouched
    assert store.count("memory_evidence", "memory_id='OLD'") == 1                        # ... with its original source
    rels = {(r["source_id"], r["target_id"], r["rel_type"]) for r in store.query("SELECT * FROM {S}memory_relationships")}
    assert {(new["memory_id"], "OLD", "corrects"), (new["memory_id"], "OLD", "supersedes")} <= rels
    ev = store.lifecycle_events("OLD")[-1]
    assert (ev["actor"], ev["to_status"], ev["related_memory_id"]) == ("advisor-kenji", "superseded", new["memory_id"]) and "restated" in ev["reason"]
    assert ("correct", "corrected", []) in codes(store) and "memory_write_corrected" in names(log)


def test_unauthorized_corrections_are_refused():
    store, _, _ = rig()
    put(store, "OLD")
    base = dict(expected_version=1, new_value="65", evidence=[KEV2], reason="r")
    for who, code in ((Principal.service("consolidation", [KENJI]), "operation_not_permitted"), (Principal.user("x", [KENJI], authenticated=False), "unauthenticated_actor"),
                      (RP, "memory_not_found")):
        with pytest.raises(MemoryWriteRejected) as e:
            store.boundary.correct("OLD", principal=who, **base)
        assert e.value.code == code
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.correct("OLD", principal=None, **base)
    assert e.value.code == "missing_principal" and row(store, "OLD")["status"] == "active" and store.count("memory_records") == 4        # 3 curated + OLD


def test_stale_and_missing_versions_are_refused_and_nothing_changes():
    store, _, _ = rig()
    put(store, "OLD")
    store.set_status("OLD", "disputed", "checking", "reviewer-1", principal=KP)                 # version 1 -> 2
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="65", evidence=[KEV2], reason="r")
    assert e.value.code == "stale_version"
    with pytest.raises(MemoryWriteRejected) as e2:
        store.boundary.correct("OLD", principal=KP, expected_version=None, new_value="65", evidence=[KEV2], reason="r")
    assert e2.value.code == "version_required"
    with pytest.raises(MemoryWriteRejected) as e3:
        store.set_status("OLD", "expired", "r", "reviewer-1", principal=KP, expected_version=1)
    assert e3.value.code == "stale_version" and row(store, "OLD")["status"] == "disputed" and store.count("memory_records", "memory_id LIKE 'MEM-COR-%'") == 0


def test_corrections_need_provenance_and_cannot_target_superseded_memories():
    store, _, _ = rig()
    at(store, "JAN", "OLD")
    for ev, cls in (([], "provenance_missing"), ([REV], "evidence_unresolved")):
        with pytest.raises(MemoryWriteRejected) as e:
            store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="65", evidence=ev, reason="r")
        assert e.value.code == cls
    res = store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="63", evidence=[KEV2], reason="r")
    with pytest.raises(MemoryWriteRejected) as e2:
        store.boundary.correct("OLD", principal=KP, expected_version=2, new_value="62", evidence=[KEV2], reason="again")
    assert e2.value.code == "target_not_correctable"
    assert row(store, res.memory_id)["status"] == "active"


def test_concurrent_corrections_cannot_silently_overwrite_each_other(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    a, _, _ = rig(url)
    at(a, "JAN", "OLD")
    b = MemoryStore(url=url, conv=CONV)
    barrier, out = threading.Barrier(2), []

    def go(st, value):
        barrier.wait()
        try:
            out.append(("ok", st.boundary.correct("OLD", principal=KP, expected_version=1, new_value=value, evidence=[KEV2], reason=f"to {value}").memory_id))
        except MemoryWriteRejected as e:
            out.append((e.code, None))
    ts = [threading.Thread(target=go, args=(a, "63")), threading.Thread(target=go, args=(b, "63"))]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(c for c, _ in out) == ["ok", "stale_version"] or sorted(c for c, _ in out) == ["ok", "target_not_correctable"]
    assert a.count("memory_records", "memory_id LIKE 'MEM-COR-%'") == 1 and row(a, "OLD")["status"] == "superseded"
    b.close()


def test_dispute_and_retirement_are_logical_and_physical_deletion_is_impossible():
    store, _, _ = rig()
    put(store, "M1")
    assert store.set_status("M1", "disputed", "conflicting statement", "reviewer-1", principal=KP) is True
    assert row(store, "M1")["status"] == "disputed" and row(store, "M1")["content"]                                  # still there, still readable
    store.set_status("M1", "expired", "retired", "reviewer-1", principal=KP)
    with pytest.raises(MemoryWriteRejected) as e:
        store.set_status("M1", "active", "resurrect", "reviewer-1", principal=KP)
    assert e.value.code == "invalid_transition"
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("DELETE FROM {S}memory_records WHERE memory_id='M1'")
    assert store.count("memory_lifecycle_events", "memory_id='M1'") == 3


# ===================================================================================================== audit events
def test_events_distinguish_attempt_validation_and_commit_and_follow_the_commit():
    store, log, _ = rig()
    put(store, "M1")
    seq = [n for n in names(log) if n.startswith("memory_write")]
    assert seq == ["memory_write_requested", "memory_write_validated", "memory_write_committed"]
    log.clear()
    with pytest.raises(RuntimeError):                                              # an OUTER transaction rolls back: 'committed' must never have been reported
        with store.transaction():
            put(store, "M2", h="m2")
            raise RuntimeError("caller failed after the write")
    assert store.get("M2") is None
    assert "memory_write_committed" not in names(log) and "memory_write_rolled_back" in names(log)


def test_events_carry_correlation_ids_and_never_memory_content_or_other_clients():
    store, log, _ = rig()
    put(store, "M1")
    with pytest.raises(ScopeDenied):
        store.insert_memory(erec("M9", h="m9"), dict(EDET), [KEV], principal=RI)
    blob = json.dumps(log, default=str)
    assert K090_T2 not in blob and "We've decided" not in blob and REB_EX not in blob
    c = next(k for n, k in log if n == "memory_write_committed")
    assert c["request_id"] == "REQ-P14" and c["actor"] == "ingestion" and c["client_id"] == KENJI and c["memory_id"] == "M1" and c["operation_id"].startswith("OP-")
    d = next(k for n, k in log if n == "memory_scope_denied")
    assert d["reason"] == "client_scope_denied" and "ingestion" == d["actor"] and d["request_id"] == "REQ-P14R"


def test_event_names_are_a_stable_closed_set():
    store, log, _ = rig()
    at(store, "JAN", "M1")
    at(store, "JAN", "M1")                                                            # same payload: a replay
    at(store, "JAN", "M1b", h="M1", content="different text, same content hash")      # same hash, different payload: a duplicate
    put(store, "M2", h="m2", value_text="63", content="We've decided: 63. again")
    store.boundary.correct("M1", principal=KP, expected_version=1, new_value="63", evidence=[KEV2], reason="r")
    allowed = {"memory_write_requested", "memory_write_validated", "memory_write_rejected", "memory_write_committed", "memory_write_duplicate", "memory_write_corrected",
               "memory_write_superseded", "memory_conflict_detected", "memory_provenance_missing", "memory_scope_denied", "memory_ingestion_replayed", "memory_write_rolled_back"}
    assert set(names(log)) <= allowed and {"memory_write_duplicate", "memory_ingestion_replayed", "memory_write_corrected", "memory_conflict_detected"} <= set(names(log))


def test_the_write_log_is_append_only():
    store, _, _ = rig()
    put(store, "M1")
    for sql in ("UPDATE {S}memory_write_log SET outcome='rejected'", "DELETE FROM {S}memory_write_log"):
        with pytest.raises(sqlite3.IntegrityError):
            store.execute(sql)
        with store._gate(), pytest.raises(sqlite3.IntegrityError):
            store.execute(sql)


# ===================================================================================================== SQLite migration
def _legacy_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    s = MemoryStore(url=url, conv=CONV)
    _offline(tmp_path / "legacy.db", s._script("100_cmf_memory_schema.sql"))
    s = MemoryStore(url=url, conv=CONV)
    _offline(tmp_path / "legacy.db", ["INSERT INTO schema_migrations (version, description, applied_at) VALUES ('100','initial','2026-01-01T00:00:00')",
              "INSERT INTO memory_records (memory_id, client_id, memory_type, topic, subject, content, value_text, certainty, status, origin, content_hash, event_date, created_at, updated_at) "
              "VALUES ('LEG1','CLIENT-001','episodic','retirement_age','retirement_age','legacy fact','63','confirmed','active','conversation_llm','lh','2026-05-01','2026-05-02T00:00:00','2026-05-02T00:00:00')",
              "INSERT INTO episodic_details (memory_id, interaction_id, turn_index, event_ts, speaker_role, event_type) VALUES ('LEG1','INT-090',2,'2026-05-01T08:00:00','Client','decision')",
              "INSERT INTO memory_lifecycle_events (event_id, memory_id, to_status, reason, actor, created_at) VALUES ('EV1','LEG1','active','created','ingestion','2026-05-02T00:00:00')"])
    return s, url


def test_migration_on_an_existing_database_keeps_data_and_invents_no_verification(tmp_path):
    s, url = _legacy_db(tmp_path)
    s.init()
    r = row(s, "LEG1")
    assert r["content"] == "legacy fact" and r["status"] == "active"
    assert (r["verification"], r["source_class"], r["created_by"], r["observed_at"], r["row_version"], r["temporal_status"]) == ("unverified", "llm_extraction", "ingestion", "2026-05-01T08:00:00", 1, "unknown")
    assert "predates" in r["verification_basis"] and s.query("PRAGMA user_version")[0]["user_version"] == 123
    assert s.count("schema_migrations") == 1                                           # the original migration history is untouched
    with pytest.raises(sqlite3.IntegrityError):
        s.execute("UPDATE {S}memory_records SET status='expired' WHERE memory_id='LEG1'")        # and the guard is live
    s.close()


def test_migration_is_idempotent_and_a_fresh_database_gets_the_same_schema(tmp_path):
    s, _ = _legacy_db(tmp_path)
    s.init()
    snap = (s.count("memory_records"), [r["name"] for r in s.query("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")],
            [r["name"] for r in s.query("PRAGMA table_info(memory_records)")])
    s.init()
    s.init()
    assert snap == (s.count("memory_records"), [r["name"] for r in s.query("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")],
                    [r["name"] for r in s.query("PRAGMA table_info(memory_records)")])
    fresh, _, _ = rig()
    assert [r["name"] for r in fresh.query("PRAGMA table_info(memory_records)")] == snap[2]
    assert {"lifecycle_no_update", "lifecycle_no_delete", "immutable_memory_content"} <= set(snap[1])           # pre-existing triggers kept, new ones added
    s.close()


def test_an_interrupted_migration_changes_nothing_and_can_be_rerun(tmp_path, monkeypatch):
    s, _ = _legacy_db(tmp_path)
    orig = s.execute

    def flaky(sql, params=()):
        if sql.startswith("UPDATE memory_records SET source_class"):
            raise RuntimeError("power cut during the back-fill")
        return orig(sql, params)
    monkeypatch.setattr(s, "execute", flaky)
    with pytest.raises(RuntimeError):
        s.init()
    monkeypatch.undo()
    assert s.query("PRAGMA user_version")[0]["user_version"] == 0
    assert "row_version" not in {r["name"] for r in s.query("PRAGMA table_info(memory_records)")} and s.count("memory_records") == 1
    assert not s.query("SELECT 1 AS x FROM sqlite_master WHERE name='memory_write_log'")
    s.init()
    assert row(s, "LEG1")["source_class"] == "llm_extraction" and s.query("PRAGMA user_version")[0]["user_version"] == 123
    s.close()


def test_an_offline_tool_that_dropped_the_triggers_is_healed_by_the_next_init(tmp_path):
    store, _, _ = rig(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    raw = sqlite3.connect(tmp_path / "m.db")
    raw.execute("DROP TRIGGER guard_memory_records_update")
    raw.commit()
    raw.close()
    assert store.integrity_ready() is False                                                      # a missing trigger now refuses durable writes (fail closed) ...
    with pytest.raises(MemoryIntegrityUnavailable):
        put(store, "M9", h="m9")
    store.init()                                                                                 # ... until init() restores it
    assert store.integrity_ready() is True
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE {S}memory_records SET status='expired'")                            # ... but restored by init()
