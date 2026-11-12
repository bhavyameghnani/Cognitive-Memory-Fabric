"""P1.2 - persistent human approval and one-time release.  Written BEFORE the implementation.

SQLite only (temporary files), no network, no real LLM.  The store/gate tests exercise harness.approval + harness.approval_store directly; the API tests
use the real FastAPI app with signed tokens (the fixtures of tests/test_p0_security.py).
"""
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from harness import ApprovalGate, ApprovalPolicy, ApprovalProposal, ApprovalRequired, AuthContext, ConfigError, Harness, HarnessConfig, PermissionDenied, PermissionGate  # noqa: E402
from harness.approval import RiskCategory  # noqa: E402
from harness.approval_store import ApprovalStoreError, SqliteApprovalStore  # noqa: E402

KENJI, REBECCA = "CLIENT-001", "CLIENT-002"
ADV, ADV2, SUP, SUP2 = "ADVISOR-001", "ADVISOR-002", "SUP-1", "SUP-2"
DIRECTORY = {KENJI: {"advisor_id": ADV, "first_name": "Kenji"}, REBECCA: {"advisor_id": ADV2, "first_name": "Rebecca"}}


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


def new_store(path, clock=None, ttl=3600):
    return SqliteApprovalStore(f"sqlite:///{Path(path).as_posix()}", ttl_seconds=ttl, clock=clock or Clock())


def new_gate(store):
    return ApprovalGate(ApprovalPolicy(), store=store, gate=PermissionGate(HarnessConfig(), lambda: DIRECTORY))


def prop(payload="report body v1", dv="dv1", by=ADV, client=KENJI, role="advisor", cat=RiskCategory.SUITABILITY_CONCLUSION):
    return ApprovalProposal(cat, "release_report", by, "suitability-related conclusion", dict(body=payload), dv, (client,), requester_role=role)


def ctx(sub, role="supervisor"):
    return AuthContext.for_test(sub, role)


def approved(tmp_path, **kw):
    s = new_store(tmp_path / "a.db", **kw)
    g = new_gate(s)
    rec = g.submit(prop(), draft={"report": "the confidential draft"})
    g.decide(rec.approval_id, ctx(SUP), True, "reviewed the sources")
    return s, g, rec


def raw(store):
    con = sqlite3.connect(store.path, isolation_level=None)          # autocommit: a failed statement must not leave a write lock behind
    con.execute("PRAGMA foreign_keys = ON")
    return con


def event_types(store, aid):
    return [e["event_type"] for e in store.events(aid)]


# ================================================================================================================== persistence
def test_pending_approval_survives_restart(tmp_path):
    s = new_store(tmp_path / "a.db")
    rec = new_gate(s).submit(prop(), draft={"report": "draft v1"})
    s2 = new_store(tmp_path / "a.db")                                    # a "restart": a brand-new store object on the same file
    again = s2.get(rec.approval_id)
    assert again.status == "pending" and again.requested_by == ADV and again.draft == {"report": "draft v1"}


def test_approved_approval_survives_restart_and_is_released_once_afterwards(tmp_path):
    s, g, rec = approved(tmp_path)
    g2 = new_gate(new_store(tmp_path / "a.db"))
    assert g2.store.get(rec.approval_id).status == "approved"
    assert g2.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor")) == {"report": "the confidential draft"}


def test_rejected_approval_survives_restart_and_stays_blocked(tmp_path):
    s = new_store(tmp_path / "a.db")
    g = new_gate(s)
    rec = g.submit(prop(), draft={"report": "x"})
    g.decide(rec.approval_id, ctx(SUP), False, "not appropriate")
    g2 = new_gate(new_store(tmp_path / "a.db"))
    assert g2.store.get(rec.approval_id).status == "rejected"
    with pytest.raises(ApprovalRequired):
        g2.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))


def test_a_restart_never_approves_or_releases_anything(tmp_path):
    s = new_store(tmp_path / "a.db")
    rec = new_gate(s).submit(prop(), draft={"report": "x"})
    g2 = new_gate(new_store(tmp_path / "a.db"))
    assert g2.store.get(rec.approval_id).status == "pending"
    with pytest.raises(ApprovalRequired):
        g2.require(prop(), draft={"report": "x"}, consume=True)


def test_corrupt_or_unavailable_database_fails_closed(tmp_path):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is definitely not a sqlite database " * 50)
    with pytest.raises(ApprovalStoreError) as e:
        new_store(bad)
    assert "not a database" not in str(e.value) and str(tmp_path) not in str(e.value)               # no driver text, no filesystem paths
    with pytest.raises(ApprovalStoreError):
        SqliteApprovalStore(f"sqlite:///{tmp_path.as_posix()}")                                       # a directory is not a database
    s = new_store(tmp_path / "ok.db")
    s.path = str(tmp_path / "gone" / "missing.db")                                                    # the database becomes unavailable after start-up
    with pytest.raises(ApprovalStoreError):
        new_gate(s).submit(prop(), draft="x")


def test_production_uses_a_durable_store_and_refuses_memory(tmp_path, monkeypatch):
    h = Harness(HarnessConfig.for_test(), conv=_Conv())
    assert h.approvals.store.durable and h.approvals.store.path.endswith("approvals.db")
    monkeypatch.setenv("CMF_APPROVAL_DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(ConfigError):
        Harness(HarnessConfig(), conv=_Conv())                                                         # production profile: no in-memory approvals
    assert not SqliteApprovalStore(":memory:").durable                                                 # allowed only for unit tests / development tooling


class _Conv:
    def directory(self):
        return DIRECTORY


# ================================================================================================================== identity and authorization
def test_requester_cannot_approve_their_own_request(tmp_path):
    g = new_gate(new_store(tmp_path / "a.db"))
    rec = g.submit(prop(by=SUP, role="supervisor"), draft="x")
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, ctx(SUP), True, "approving my own")
    assert e.value.code == "self_approval"


@pytest.mark.parametrize("variant", ["sup-1", " SUP-1 ", "Sup-1"])
def test_self_approval_is_detected_on_the_stable_subject_not_its_spelling(tmp_path, variant):
    g = new_gate(new_store(tmp_path / "a.db"))
    rec = g.submit(prop(by=SUP, role="supervisor"), draft="x")
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, AuthContext.for_test(variant, "supervisor"), True, "same person, different spelling")
    assert e.value.code == "self_approval"


def test_the_database_itself_refuses_self_approval(tmp_path):
    s = new_store(tmp_path / "a.db")
    rec = new_gate(s).submit(prop(), draft="x")
    con = raw(s)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE approval_requests SET status='approved', decided_by=?, decided_role='supervisor', decided_at='2026-11-05T12:00:00Z', decision_reason='x' WHERE approval_id=?",
                    (ADV, rec.approval_id))


def test_advisor_role_cannot_decide(tmp_path):
    g = new_gate(new_store(tmp_path / "a.db"))
    rec = g.submit(prop(), draft="x")
    with pytest.raises(PermissionDenied):
        g.decide(rec.approval_id, ctx(ADV2, "advisor"), True, "an advisor cannot approve")
    assert g.store.get(rec.approval_id).status == "pending"


def test_missing_stable_subject_or_unauthenticated_identity_fails_closed(tmp_path):
    g = new_gate(new_store(tmp_path / "a.db"))
    rec = g.submit(prop(), draft="x")
    for who in (AuthContext("", "supervisor", True), AuthContext("  ", "supervisor", True), AuthContext(SUP, "supervisor", False), AuthContext(SUP, "root", True), None):
        with pytest.raises((PermissionDenied, AttributeError, TypeError)):
            g.decide(rec.approval_id, who, True, "should not be accepted")
    assert g.store.get(rec.approval_id).status == "pending"
    with pytest.raises(PermissionDenied):                                                              # a proposal with no requester subject cannot even be recorded
        g.submit(prop(by=""), draft="x")


def test_decision_reason_is_mandatory_and_decisions_are_final(tmp_path):
    g = new_gate(new_store(tmp_path / "a.db"))
    rec = g.submit(prop(), draft="x")
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, ctx(SUP), True, "   ")
    assert e.value.code == "reason_required"
    g.decide(rec.approval_id, ctx(SUP), True, "reviewed")
    for approve in (True, False):
        with pytest.raises(PermissionDenied) as e:
            g.decide(rec.approval_id, ctx(SUP2), approve, "second opinion")
        assert e.value.code == "already_decided"


# ================================================================================================================== fingerprint and expiry
def test_fingerprint_contains_the_requester_and_every_bound_field():
    base = prop()
    assert base.fingerprint() == prop().fingerprint()
    for other in (prop(payload="report body v2"), prop(dv="dv2"), prop(client=REBECCA), prop(by=ADV2), prop(cat=RiskCategory.EXTERNAL_ACTION)):
        assert other.fingerprint() != base.fingerprint()
    assert prop(role="advisor").fingerprint() == prop(role="supervisor").fingerprint()                 # role is audit metadata; the stable subject is what is bound


@pytest.mark.parametrize("changed", [dict(payload="report body v2"), dict(dv="dv2"), dict(client=REBECCA)])
def test_a_changed_payload_data_version_or_client_cannot_release_under_the_old_approval(tmp_path, changed):
    s, g, rec = approved(tmp_path)
    with pytest.raises(PermissionDenied) as e:
        g.release(rec.approval_id, prop(**changed), actor=ctx(ADV, "advisor"))
    assert e.value.code == "approval_invalidated"
    assert s.get(rec.approval_id).status == "approved"                                                 # the failed attempt consumed nothing
    with pytest.raises(ApprovalRequired):
        g.require(prop(**changed), draft="other draft")                                                # a changed proposal needs a FRESH approval


def test_a_different_requester_cannot_reuse_an_approval(tmp_path):
    s, g, rec = approved(tmp_path)
    with pytest.raises(ApprovalRequired) as e:
        g.require(prop(by=ADV2), draft="other")                                                        # same report, other requester: a new pending request, not the approved one
    assert e.value.record.approval_id != rec.approval_id and e.value.record.status == "pending"
    with pytest.raises(PermissionDenied) as e2:
        g.release(rec.approval_id, prop(), actor=ctx(ADV2, "advisor"))                                 # right proposal, wrong caller
    assert e2.value.code == "not_requester"
    assert s.get(rec.approval_id).status == "approved"


def test_expired_approval_cannot_release_or_be_decided(tmp_path):
    clk = Clock()
    s, g, rec = approved(tmp_path, clock=clk, ttl=600)
    clk.advance(seconds=601)
    with pytest.raises(PermissionDenied) as e:
        g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))
    assert e.value.code == "approval_expired"
    assert s.get(rec.approval_id).status == "expired" and s.get(rec.approval_id).draft is None         # expired payloads are scrubbed
    g2 = new_gate(new_store(tmp_path / "b.db", clock=clk, ttl=600))
    pend = g2.submit(prop(), draft="x")
    clk.advance(seconds=601)
    with pytest.raises(PermissionDenied) as e:
        g2.decide(pend.approval_id, ctx(SUP), True, "too late")
    assert e.value.code == "approval_expired"


def test_rejected_and_pending_approvals_cannot_release(tmp_path):
    g = new_gate(new_store(tmp_path / "a.db"))
    pend = g.submit(prop(), draft="x")
    with pytest.raises(ApprovalRequired):
        g.release(pend.approval_id, prop(), actor=ctx(ADV, "advisor"))
    g.decide(pend.approval_id, ctx(SUP), False, "no")
    with pytest.raises(ApprovalRequired):
        g.release(pend.approval_id, prop(), actor=ctx(ADV, "advisor"))
    assert g.store.get(pend.approval_id).draft is None                                                 # a rejected payload is not kept


def test_stored_payload_tampering_is_detected_at_release(tmp_path):
    s, g, rec = approved(tmp_path)
    con = raw(s)
    con.execute("DROP TRIGGER IF EXISTS approval_requests_bound_fields")                               # an attacker with file access removes the guard...
    con.execute("UPDATE approval_requests SET report_payload = ? WHERE approval_id = ?", (json.dumps({"kind": "json", "data": {"report": "TAMPERED"}}), rec.approval_id))
    con.commit()
    with pytest.raises(PermissionDenied) as e:                                                         # ...but the stored hash no longer matches
        g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))
    assert e.value.code == "approval_invalidated"


# ================================================================================================================== replay and concurrency
def test_release_succeeds_once_and_the_second_attempt_fails(tmp_path):
    s, g, rec = approved(tmp_path)
    assert g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor")) == {"report": "the confidential draft"}
    with pytest.raises(PermissionDenied) as e:
        g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))
    assert e.value.code == "already_consumed"
    assert s.get(rec.approval_id).status == "consumed" and s.get(rec.approval_id).consumed_at and s.get(rec.approval_id).draft is None


def test_concurrent_releases_yield_exactly_one_success(tmp_path):
    s, g, rec = approved(tmp_path)
    n, results, barrier = 12, [], threading.Barrier(12)

    def worker():
        gate = new_gate(new_store(tmp_path / "a.db"))                                                  # each thread has its own connection, like separate requests / processes
        barrier.wait()
        try:
            results.append(("ok", gate.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))))
        except PermissionDenied as e:
            results.append(("denied", e.code))
        except Exception as e:                                                                         # noqa: BLE001
            results.append(("error", type(e).__name__))
    threads = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r[0] == "ok" for r in results) == 1, results
    assert all(r == ("denied", "already_consumed") for r in results if r[0] != "ok"), results
    assert event_types(s, rec.approval_id).count("released") == 1


def test_a_decision_cannot_change_after_consumption(tmp_path):
    s, g, rec = approved(tmp_path)
    g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, ctx(SUP2), False, "changed my mind after release")
    assert e.value.code == "already_decided"
    con = raw(s)
    for sql in ("UPDATE approval_requests SET status='approved', consumed_at=NULL WHERE approval_id=?", "UPDATE approval_requests SET status='rejected' WHERE approval_id=?"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql, (rec.approval_id,))


def test_duplicate_decision_events_cannot_be_inserted(tmp_path):
    s, g, rec = approved(tmp_path)
    con = raw(s)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO approval_events (approval_id, actor_subject, actor_role, event_type, reason, fingerprint, created_at) VALUES (?,?,?,?,?,?,?)",
                    (rec.approval_id, SUP2, "supervisor", "approved", "duplicate", rec.fingerprint, "2026-11-05T12:00:00Z"))
    with pytest.raises(PermissionDenied):
        g.decide(rec.approval_id, ctx(SUP2), True, "duplicate")
    assert event_types(s, rec.approval_id).count("approved") == 1


# ================================================================================================================== audit trail
def test_decision_events_record_the_verified_actor_and_reason(tmp_path):
    s = new_store(tmp_path / "a.db")
    g = new_gate(s)
    a, b = g.submit(prop(payload="a"), draft="a"), g.submit(prop(payload="b"), draft="b")
    g.decide(a.approval_id, ctx(SUP), True, "reviewed a")
    g.decide(b.approval_id, ctx(SUP2, "admin"), False, "rejected b")
    ev = {e["event_type"]: e for aid in (a.approval_id, b.approval_id) for e in s.events(aid)}
    assert ev["requested"]["actor_subject"] == ADV and ev["requested"]["actor_role"] == "advisor"
    assert (ev["approved"]["actor_subject"], ev["approved"]["actor_role"], ev["approved"]["reason"]) == (SUP, "supervisor", "reviewed a")
    assert (ev["rejected"]["actor_subject"], ev["rejected"]["actor_role"], ev["rejected"]["reason"]) == (SUP2, "admin", "rejected b")
    assert ev["approved"]["fingerprint"] == a.fingerprint and ev["approved"]["created_at"]


def test_release_event_and_consumption_commit_atomically(tmp_path, monkeypatch):
    s, g, rec = approved(tmp_path)
    real = s._insert_event

    def boom(con, *a, **k):
        if a and a[1] == "released" or k.get("event_type") == "released":
            raise sqlite3.OperationalError("disk full")
        return real(con, *a, **k)
    monkeypatch.setattr(s, "_insert_event", boom)
    with pytest.raises(ApprovalStoreError):
        g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))
    cur = s.get(rec.approval_id)
    assert cur.status == "approved" and cur.consumed_at is None and cur.draft is not None            # consumption rolled back with the failed event
    assert "released" not in event_types(s, rec.approval_id)
    monkeypatch.undo()
    assert g.release(rec.approval_id, prop(), actor=ctx(ADV, "advisor"))                               # and the approval is still usable exactly once
    assert event_types(s, rec.approval_id).count("released") == 1


def test_failed_attempts_do_not_create_successful_events(tmp_path):
    s, g, rec = approved(tmp_path)
    for bad in (dict(p=prop(dv="dv2"), who=ctx(ADV, "advisor")), dict(p=prop(), who=ctx(ADV2, "advisor"))):
        with pytest.raises(PermissionDenied):
            g.release(rec.approval_id, bad["p"], actor=bad["who"])
    with pytest.raises(PermissionDenied):
        g.decide(rec.approval_id, ctx(SUP2), False, "late")
    kinds = event_types(s, rec.approval_id)
    assert kinds.count("released") == 0 and kinds.count("approved") == 1 and "rejected" not in kinds
    assert kinds.count("release_denied") == 2 and "decision_denied" in kinds                           # refusals are logged as refusals, with a code, never as successes
    denied = [e for e in s.events(rec.approval_id) if e["event_type"].endswith("_denied")]
    assert {e["reason"] for e in denied} >= {"approval_invalidated", "not_requester", "already_decided"}


def test_old_audit_events_and_requests_cannot_be_mutated_or_deleted(tmp_path):
    s, g, rec = approved(tmp_path)
    con = raw(s)
    for sql in ("UPDATE approval_events SET reason='rewritten'", "DELETE FROM approval_events", "DELETE FROM approval_requests",
                "UPDATE approval_requests SET fingerprint='f' WHERE 1=1", "UPDATE approval_requests SET requester_subject='someone-else'",
                "UPDATE approval_requests SET expires_at='2999-01-01T00:00:00Z'", "UPDATE approval_requests SET data_version='dvX'"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    public = [n for n in dir(SqliteApprovalStore) if not n.startswith("_")]
    assert not [n for n in public if n.startswith(("delete", "update_event", "remove", "purge_event"))]   # the API offers no way to rewrite history


def test_schema_has_constraints_and_indexes(tmp_path):
    s = new_store(tmp_path / "a.db")
    con = raw(s)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"approval_requests", "approval_events", "approval_schema_migrations"} <= names
    assert any("fingerprint" in n for n in names) and any("expires" in n for n in names)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO approval_requests (approval_id, request_id, requester_subject, requester_role, client_id, payload_hash, data_version, fingerprint, category, operation, "
                    "reason, status, created_at, expires_at) VALUES ('X','R','s','advisor','C','h','dv','fp','cat','op','why','bogus','2026-01-01T00:00:00Z','2026-01-02T00:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):                                                         # events need a parent request
        con.execute("INSERT INTO approval_events (approval_id, actor_subject, actor_role, event_type, reason, fingerprint, created_at) VALUES ('NOPE','a','advisor','requested','','','t')")
    assert s.get("APR-" + "0" * 32) is None


def test_approval_ids_are_unpredictable(tmp_path):
    g = new_gate(new_store(tmp_path / "a.db"))
    ids = {g.submit(prop(payload=str(i)), draft="x").approval_id for i in range(20)}
    assert len(ids) == 20 and all(i.startswith("APR-") and len(i) >= 36 for i in ids)


def test_memory_write_approvals_are_persistent_but_store_no_value(tmp_path):
    s = new_store(tmp_path / "a.db")
    g = ApprovalGate(ApprovalPolicy(sensitive_memory_topics=("retirement_age",)), store=s)
    ok, why = g.check_memory_write(KENJI, "retirement_age", "retirement", "SECRET-VALUE-63", "MEM-1", "2026-01-01")
    assert not ok and "needs human approval" in why
    assert "SECRET-VALUE-63" not in Path(s.path).read_bytes().decode("latin-1")                        # the sensitive value is hashed into the fingerprint, never stored
    assert new_store(tmp_path / "a.db").all()[0].status == "pending"


# ================================================================================================================== API
from test_p0_security import ASK_SENSITIVE, assert_no_content, ask_withheld, bearer, world  # noqa: E402,F401  (fixtures + helpers)


def approve_url(aid, how="decision"):
    return f"/api/approvals/{aid}/{how}"


def decide(c, aid, sub=SUP, approve=True, reason="reviewed the sources", role="supervisor", **extra):
    return c.post(approve_url(aid), headers=bearer(sub, role), json={"approve": approve, "reason": reason, **extra})


def test_api_unauthenticated_and_advisor_cannot_list_approvals(world):
    build, m, db = world
    c = build("production")
    assert c.get("/api/approvals").status_code == 401
    assert c.get("/api/approvals", headers={"X-CMF-Actor-Id": "SUP-1", "X-CMF-Actor-Role": "supervisor"}).status_code == 401
    assert c.get("/api/approvals", headers=bearer(ADV, "advisor")).status_code == 403


def test_api_supervisor_lists_pending_approvals_without_report_content(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    r = c.get("/api/approvals", headers=bearer(SUP, "supervisor"))
    assert r.status_code == 200
    rows = r.json()
    assert [x["approval_id"] for x in rows] == [aid] and rows[0]["status"] == "pending" and rows[0]["expires_at"]
    assert set(rows[0]) <= {"approval_id", "category", "status", "created_at", "expires_at", "requested_by", "client_ids", "reason", "operation"}
    assert_no_content(rows, db)
    decide(c, aid)
    assert c.get("/api/approvals", headers=bearer(SUP, "supervisor")).json() == []                      # no longer pending


def test_api_unknown_and_unauthorized_approval_ids_look_the_same(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    other = bearer(ADV2, "advisor")                                                                      # another advisor's book
    unknown = "APR-" + "0" * 32
    for method, path in (("get", f"/api/approvals/{aid}"), ("post", f"/api/approvals/{aid}/release")):
        a, b = getattr(c, method)(path, headers=other), getattr(c, method)(path.replace(aid, unknown), headers=other)
        assert (a.status_code, a.json()) == (b.status_code, b.json()) == (404, {"detail": "unknown approval"})
    assert c.get(f"/api/approvals/{aid}").status_code == 401 and c.get(f"/api/approvals/{unknown}").status_code == 401   # identity before lookup


def test_api_body_supplied_identity_is_ignored(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    r = decide(c, aid, sub=SUP, actor_id="SOMEONE-ELSE", actor="SOMEONE-ELSE", role="admin", decided_by="SOMEONE-ELSE", requested_by="SOMEONE-ELSE")
    assert r.status_code == 200 and r.json()["decided_by"] == SUP
    ev = m.harness().approvals.store.events(aid)
    assert {e["actor_subject"] for e in ev if e["event_type"] == "approved"} == {SUP}
    assert "SOMEONE-ELSE" not in json.dumps(ev)


def test_api_requester_cannot_self_approve_even_with_a_different_display_name(world):
    build, m, db = world
    c = build("production")
    asked = c.post("/api/ask", headers=bearer(SUP, "supervisor"), json=ASK_SENSITIVE)                    # the supervisor asked the question
    assert asked.status_code == 200 and asked.json()["released"] is False
    aid = asked.json()["approval"]["approval_id"]
    for hdr in (bearer(SUP, "supervisor"), bearer(SUP, "supervisor", name="Dr. Somebody Else", preferred_username="other.person"), bearer(" sup-1 ", "supervisor")):
        r = c.post(approve_url(aid), headers=hdr, json={"approve": True, "reason": "approving my own request"})
        assert r.status_code == 403 and "self_approval" in r.text
    assert m.harness().approvals.store.get(aid).status == "pending"
    assert decide(c, aid, sub=SUP2).status_code == 200                                                  # a different verified subject can


def test_api_approve_and_reject_routes(world):
    build, m, db = world
    c = build("production")
    a = ask_withheld(c, db)["approval"]["approval_id"]
    assert c.post(approve_url(a, "approve"), headers=bearer(ADV, "advisor"), json={"reason": "advisors cannot"}).status_code == 403
    assert c.post(approve_url(a, "reject"), headers=bearer(SUP, "supervisor"), json={"reason": "  "}).status_code in (400, 403, 422)
    r = c.post(approve_url(a, "reject"), headers=bearer(SUP, "supervisor"), json={"reason": "not suitable for this client"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert c.post(approve_url(a, "approve"), headers=bearer(SUP2, "supervisor"), json={"reason": "second opinion"}).status_code == 403


def test_api_full_lifecycle_releases_the_report_exactly_once(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    pending = c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()
    assert pending["released"] is False and pending["status"] == "pending_approval"
    assert decide(c, aid).status_code == 200
    assert c.post(approve_url(aid, "release"), headers=bearer(SUP, "supervisor")).status_code == 404   # a supervisor is not the requester
    first = c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()
    assert first["released"] is True and first["status"] == "released" and first["report"]["evidence"]
    second = c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()
    assert second["released"] is False and second["status"] == "already_released" and second["report"]["evidence"] == []
    assert_no_content(second, db)
    ev = event_types(m.harness().approvals.store, aid)
    assert ev.count("released") == 1 and ev[0] == "requested"


def test_api_pending_approval_survives_an_api_restart_and_still_cannot_release(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    c2 = build("production")                                                                             # restart: new Harness, new store object, same file
    assert c2.get(f"/api/approvals/{aid}", headers=bearer(ADV, "advisor")).json()["status"] == "pending"
    assert c2.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()["released"] is False
    assert decide(c2, aid).status_code == 200
    c3 = build("production")                                                                             # restart again, now approved
    assert c3.get(f"/api/approvals/{aid}", headers=bearer(ADV, "advisor")).json()["status"] == "approved"
    assert c3.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()["released"] is True
    c4 = build("production")
    assert c4.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()["status"] == "already_released"


def test_api_rejected_approval_survives_restart_and_cannot_release(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    decide(c, aid, approve=False, reason="not appropriate for this client")
    c2 = build("production")
    body = c2.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()
    assert body["released"] is False and body["status"] == "rejected"


def test_api_expired_approval_cannot_release(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    decide(c, aid)
    m.harness().approvals.store.clock = lambda: datetime.now(timezone.utc) + timedelta(days=30)
    body = c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()
    assert body["released"] is False and body["status"] == "approval_expired"
    assert_no_content(body, db)


def test_api_data_version_change_invalidates_the_approval(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    decide(c, aid)
    m.store().execute("UPDATE {S}memory_records SET updated_at = '2099-01-01T00:00:00' WHERE client_id = 'CLIENT-001' AND memory_type = 'semantic'")
    body = c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()
    assert body["released"] is False and body["status"] == "approval_invalidated"
    assert m.harness().approvals.store.get(aid).status == "approved"                                    # nothing was consumed by the refused attempt


def test_api_asking_again_never_replays_a_consumed_approval_and_a_repeat_ask_is_idempotent_while_pending(world):
    build, m, db = world
    c = build("production")
    a1 = ask_withheld(c, db)["approval"]["approval_id"]
    a1b = ask_withheld(c, db)["approval"]["approval_id"]                                                  # same requester, same content, still pending: same request (cache hit or not)
    assert a1 == a1b
    decide(c, a1)
    assert c.post(approve_url(a1, "release"), headers=bearer(ADV, "advisor")).json()["released"] is True
    again = ask_withheld(c, db)                                                                           # the consumed approval must not release a second report
    assert again["released"] is False and again["approval"]["approval_id"] != a1


def test_api_asking_again_after_approval_releases_and_consumes_in_one_step(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    decide(c, aid)
    r = c.post("/api/ask", headers=bearer(ADV, "advisor"), json=ASK_SENSITIVE)
    assert r.status_code == 200 and r.json()["released"] is True                                          # identical content, requester, data version: released...
    assert c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor")).json()["status"] == "already_released"   # ...and consumed, so it cannot be replayed
    other = c.post("/api/ask", headers=bearer(ADV, "advisor"), json=ASK_SENSITIVE).json()
    assert other["released"] is False                                                                      # a third ask needs a new approval


def test_api_store_failure_never_returns_report_content(world, monkeypatch):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    decide(c, aid)
    store = m.harness().approvals.store

    def broken(*a, **k):
        raise ApprovalStoreError("approval store unavailable")
    for name in ("create", "consume", "get"):
        monkeypatch.setattr(store, name, broken)
    r1 = c.post("/api/ask", headers=bearer(ADV2, "advisor"), json={"question": "Can Rebecca afford to retire early?"})
    r2 = c.post(approve_url(aid, "release"), headers=bearer(ADV, "advisor"))
    for r in (r1, r2):
        assert r.status_code == 503 and "evidence" not in r.text and "MEM-" not in r.text and "sqlite" not in r.text.lower()
    assert_no_content(r2.json(), db)


def test_api_pending_and_status_responses_do_not_expose_the_draft_or_client_text(world):
    build, m, db = world
    c = build("production")
    body = ask_withheld(c, db)
    aid = body["approval"]["approval_id"]
    assert body["client_ids"] == [] and "CLIENT-001" not in json.dumps(body)
    for r in (c.get(f"/api/approvals/{aid}", headers=bearer(ADV, "advisor")), c.get(f"/api/approvals/{aid}", headers=bearer(SUP, "supervisor")),
              c.get("/api/approvals", headers=bearer(SUP, "supervisor"))):
        assert r.status_code == 200 and "draft" not in r.text and "report_payload" not in r.text and "payload_hash" not in r.text and "fingerprint" not in r.text
        assert_no_content(r.json(), db)
    for status in ("approval_expired", "already_released", "approval_invalidated", "rejected"):
        from harness.runtime import withheld_view
        v = withheld_view({"approval_id": aid, "status": "x", "category": "c", "created_at": "t"}, "REQ", status)
        assert v["released"] is False and v["client_ids"] == [] and v["report"]["evidence"] == [] and v["status"] == status


def test_logs_and_traces_do_not_record_tokens_secrets_or_the_draft(world, caplog):
    import logging
    from test_p0_security import SECRET
    build, m, db = world
    c = build("production")
    caplog.set_level(logging.DEBUG)
    hdr = bearer(ADV, "advisor")
    token = hdr["Authorization"].split()[1]
    body = c.post("/api/ask", headers=hdr, json=ASK_SENSITIVE).json()
    aid = body["approval"]["approval_id"]
    decide(c, aid)
    c.post(approve_url(aid, "release"), headers=hdr)
    blob = caplog.text
    assert token not in blob and SECRET not in blob and "Bearer" not in blob
    from orchestration.tracing import Tracer
    from harness.runtime import Harness as H
    t = Tracer("REQ-X", persist=False)
    ev = H(HarnessConfig.for_test(), conv=_Conv()).approvals
    rec = ev.submit(prop(), draft={"report": "KENJI CONFIDENTIAL DRAFT TEXT"})
    seen = []
    ev2 = ApprovalGate(ApprovalPolicy(), store=ev.store, emit=lambda comp, name, **p: seen.append((comp, name, p)))
    ev2.decide(rec.approval_id, ctx(SUP), True, "reviewed")
    assert seen and "KENJI CONFIDENTIAL DRAFT TEXT" not in json.dumps(seen, default=str) and "report_payload" not in json.dumps(seen, default=str)


def test_config_exposes_ttl_and_database_settings(monkeypatch):
    cfg = HarnessConfig.from_env({"HARNESS_PROFILE": "production", "HARNESS_APPROVAL_TTL_SECONDS": "900", "CMF_APPROVAL_DATABASE_URL": "sqlite:///build/x.db"})
    assert cfg.approval_ttl_seconds == 900 and cfg.approval_database_url == "sqlite:///build/x.db"
    with pytest.raises(ConfigError):
        HarnessConfig.from_env({"HARNESS_PROFILE": "production", "HARNESS_APPROVAL_TTL_SECONDS": "5"})   # an approval that lives seconds is useless; one that lives forever is unsafe
    with pytest.raises(ConfigError):
        HarnessConfig.from_env({"HARNESS_PROFILE": "production", "HARNESS_APPROVAL_TTL_SECONDS": str(60 * 60 * 24 * 365)})
    with pytest.raises(ConfigError):
        HarnessConfig.from_env({"HARNESS_PROFILE": "production", "CMF_APPROVAL_DATABASE_URL": "postgresql://u:p@h/db"})   # SQLite only in this phase


def test_api_refuses_to_start_with_a_corrupt_approval_database(world, monkeypatch):
    build, m, db = world
    bad = Path(os.environ["CMF_APPROVAL_DATABASE_URL"][len("sqlite:///"):])
    bad.write_bytes(b"garbage, not sqlite " * 100)
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HARNESS_PROFILE", "production")
    monkeypatch.setattr(m, "_harness", None)
    with pytest.raises(ApprovalStoreError):
        with TestClient(m.app):
            pass
