"""P0 security regression tests (audit findings S-1 .. S-7).  Written BEFORE the fixes: each one failed against the audited code for the reason stated in its docstring.

Everything runs on SQLite against a throw-away COPY of the seed memory database; no network, no real LLM (mock backend), no PostgreSQL.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from harness import ConfigError, HarnessConfig  # noqa: E402

SECRET = "unit-test-signing-secret-0123456789abcdef"      # >= 32 chars
OTHER_SECRET = "a-completely-different-secret-0123456789"
KENJI, REBECCA = "CLIENT-001", "CLIENT-002"
ASK_SENSITIVE = {"question": "Should Kenji retire at 60?"}


# ------------------------------------------------------------------------------------------------------------------ helpers
def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def raw_token(claims: dict, secret: str = SECRET, alg: str = "HS256", sign: bool = True) -> str:
    """Independent re-implementation of a JWT encoder so the tests do not trust the code under test to build their tokens."""
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()) if sign else ""
    return f"{head}.{body}.{sig}"


def claims(sub="ADVISOR-001", role="advisor", ttl=600, **extra) -> dict:
    now = int(time.time())
    c = {"sub": sub, "role": role, "iat": now, "exp": now + ttl}
    c.update(extra)
    return c


def bearer(sub="ADVISOR-001", role="advisor", **kw) -> dict:
    return {"Authorization": "Bearer " + raw_token(claims(sub, role, **kw))}


FORGED = {"X-CMF-Actor-Id": "mallory", "X-CMF-Actor-Role": "admin"}
FORGED_SUP = {"X-CMF-Actor-Id": "SUP-1", "X-CMF-Actor-Role": "supervisor"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A factory that builds the real FastAPI app under a given profile, with a copy of the seed memory DB and the mock LLM backend."""
    db = tmp_path / "mem.db"
    shutil.copyfile(ROOT / "data" / "seed" / "cmf_memory_demo.db", db)
    for k in ("HARNESS_PROFILE", "CMF_AUTH_TRUSTED_GATEWAY", "CMF_AUTH_JWT_SECRET", "CMF_API_HOST", "CMF_API_DOCS_ENABLED", "HARNESS_ALLOW_ANONYMOUS_DEV",
              "HARNESS_ALLOW_ASSERTED_IDENTITY", "HARNESS_PERMISSION_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("CMF_LLM_BACKEND", "mock")
    monkeypatch.setenv("HARNESS_TRACE_ENABLED", "false")
    import api.main as m
    monkeypatch.setattr(m, "_store", None)
    monkeypatch.setattr(m, "_harness", None)
    clients = []

    def build(profile="production", secret=SECRET, **env):
        monkeypatch.setenv("HARNESS_PROFILE", profile)
        if secret:
            monkeypatch.setenv("CMF_AUTH_JWT_SECRET", secret)
            monkeypatch.setenv("CMF_AUTH_TRUSTED_GATEWAY", "true")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(m, "_harness", None)
        c = TestClient(m.app)
        clients.append(c)
        return c
    yield build, m, db
    if m._store is not None:
        m._store.close()


def kenji_text(db) -> list:
    """Every piece of Kenji's stored memory text: none of it may appear in a withheld response."""
    con = sqlite3.connect(db)
    rows = con.execute("select content from memory_records where client_id=?", (KENJI,)).fetchall()
    ex = con.execute("select excerpt from memory_evidence where excerpt is not null").fetchall()
    con.close()
    return [r[0] for r in rows if r[0] and len(r[0]) > 20] + [r[0] for r in ex if r[0] and len(r[0]) > 20]


def kenji_ids(db) -> list:
    con = sqlite3.connect(db)
    ids = [r[0] for r in con.execute("select memory_id from memory_records where client_id=?", (KENJI,))]
    con.close()
    return ids


# ================================================================================================================== P0-1 identity
def test_forged_admin_headers_are_rejected(world):
    """BEFORE: with CMF_AUTH_TRUSTED_GATEWAY=true any caller claiming role=admin read every client (200)."""
    build, m, _ = world
    c = build("production")
    for path in ("/api/clients", f"/api/clients/{REBECCA}/brain"):
        r = c.get(path, headers=FORGED)
        assert r.status_code == 401, (path, r.status_code, r.text[:80])
    assert c.post("/api/ask", headers=FORGED, json=ASK_SENSITIVE).status_code == 401


def test_forged_supervisor_headers_cannot_approve(world):
    """BEFORE: a forged supervisor header could approve a withheld answer."""
    from harness.approval import ApprovalProposal, RiskCategory
    build, m, _ = world
    c = build("production")
    rec = m.harness().approvals.submit(ApprovalProposal(RiskCategory.SUITABILITY_CONCLUSION, "release_report", "ADVISOR-001", "test", {"x": 1}, "dv", (KENJI,)))
    r = c.post(f"/api/approvals/{rec.approval_id}/decision", headers=FORGED_SUP, json={"approve": True, "reason": "forged approval"})
    assert r.status_code == 401
    assert m.harness().approvals.store.get(rec.approval_id).status == "pending"


def test_identity_headers_are_never_authentication_in_any_profile(world):
    """Headers without a token are refused, in production AND in development (they used to be accepted as 'asserted' identities)."""
    build, m, _ = world
    for profile in ("production", "development"):
        c = build(profile)
        assert c.get("/api/clients", headers=FORGED).status_code == 401, profile
    c = build("production")
    r = c.get(f"/api/clients/{REBECCA}/brain", headers={**bearer("ADVISOR-001", "advisor"), **FORGED})     # a valid token wins; headers are ignored, not merged
    assert r.status_code == 404   # out-of-scope client: indistinguishable from a missing one (P1.4.2)


def test_invalid_signature_is_rejected(world):
    build, m, _ = world
    c = build("production")
    bad = {"Authorization": "Bearer " + raw_token(claims(), secret=OTHER_SECRET)}
    assert c.get("/api/clients", headers=bad).status_code == 401
    tampered = raw_token(claims(role="advisor")).split(".")                                           # re-use a valid signature with a promoted role
    tampered[1] = b64(json.dumps(claims(role="admin")).encode())
    assert c.get("/api/clients", headers={"Authorization": "Bearer " + ".".join(tampered)}).status_code == 401


def test_unsigned_and_wrong_algorithm_tokens_are_rejected(world):
    build, m, _ = world
    c = build("production")
    for tok in (raw_token(claims(role="admin"), alg="none", sign=False), raw_token(claims(role="admin"), alg="none"), raw_token(claims(role="admin"), alg="HS512"),
                "not.a.jwt", "garbage", "", "a.b"):
        assert c.get("/api/clients", headers={"Authorization": "Bearer " + tok}).status_code == 401, tok[:30]
    assert c.get("/api/clients", headers={"Authorization": "Basic abc"}).status_code == 401


def test_expired_and_not_yet_valid_tokens_are_rejected(world):
    build, m, _ = world
    c = build("production")
    assert c.get("/api/clients", headers=bearer(ttl=-3600)).status_code == 401
    future = {"Authorization": "Bearer " + raw_token(claims(nbf=int(time.time()) + 3600))}
    assert c.get("/api/clients", headers=future).status_code == 401


@pytest.mark.parametrize("drop", ["sub", "role", "exp"])
def test_tokens_missing_required_claims_are_rejected(world, drop):
    build, m, _ = world
    c = build("production")
    cl = claims()
    del cl[drop]
    assert c.get("/api/clients", headers={"Authorization": "Bearer " + raw_token(cl)}).status_code == 401
    for bad in ({"sub": ""}, {"sub": 123}, {"role": ""}, {"exp": "tomorrow"}, {"exp": True}):
        assert c.get("/api/clients", headers={"Authorization": "Bearer " + raw_token({**claims(), **bad})}).status_code == 401, bad


def test_issuer_and_audience_are_enforced_when_configured(world):
    build, m, _ = world
    c = build("production", CMF_AUTH_ISSUER="cmf-gateway", CMF_AUTH_AUDIENCE="cmf-api")
    assert c.get("/api/clients", headers=bearer()).status_code == 401                                           # no iss/aud in the token
    ok = {"Authorization": "Bearer " + raw_token(claims(iss="cmf-gateway", aud="cmf-api"))}
    assert c.get("/api/clients", headers=ok).status_code == 200
    wrong = {"Authorization": "Bearer " + raw_token(claims(iss="someone-else", aud="cmf-api"))}
    assert c.get("/api/clients", headers=wrong).status_code == 401


def test_valid_signed_advisor_identity_works_and_is_scoped(world):
    build, m, _ = world
    c = build("production")
    r = c.get("/api/clients", headers=bearer("ADVISOR-001", "advisor"))
    assert r.status_code == 200 and {x["client_id"] for x in r.json()} == {"CLIENT-001", "CLIENT-004"}
    assert c.get(f"/api/clients/{KENJI}/brain", headers=bearer("ADVISOR-001", "advisor")).status_code == 200


def test_cross_client_access_remains_denied_with_a_valid_token(world):
    build, m, _ = world
    c = build("production")
    assert c.get(f"/api/clients/{REBECCA}/brain", headers=bearer("ADVISOR-001", "advisor")).status_code == 404       # P1.4.2: an out-of-scope client looks exactly like a missing one
    r = c.post("/api/ask", headers=bearer("ADVISOR-001", "advisor"), json={"question": "How concentrated is Rebecca's portfolio?"})
    assert r.status_code == 403 and "client_not_in_scope" in r.text
    assert c.get(f"/api/clients/{REBECCA}/brain", headers=bearer("ADVISOR-001", "root")).status_code == 403    # an unknown role is denied by the gate


def test_valid_signed_supervisor_identity_can_decide_approvals(world):
    from harness.approval import ApprovalProposal, RiskCategory
    build, m, _ = world
    c = build("production")
    rec = m.harness().approvals.submit(ApprovalProposal(RiskCategory.SUITABILITY_CONCLUSION, "release_report", "ADVISOR-001", "test", {"x": 2}, "dv", (KENJI,)))
    url = f"/api/approvals/{rec.approval_id}/decision"
    assert c.post(url, headers=bearer("ADVISOR-001", "advisor"), json={"approve": True, "reason": "advisor may not approve"}).status_code == 403
    ok = c.post(url, headers=bearer("SUP-1", "supervisor"), json={"approve": True, "reason": "reviewed the sources"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.json()["decided_by"] == "SUP-1"


def test_trusted_gateway_mode_without_a_secret_fails_startup():
    """BEFORE: the flag alone switched on header trust."""
    with pytest.raises(ConfigError, match="CMF_AUTH_JWT_SECRET"):
        HarnessConfig.from_env({"CMF_AUTH_TRUSTED_GATEWAY": "true"})
    with pytest.raises(ConfigError, match="32"):
        HarnessConfig.from_env({"CMF_AUTH_TRUSTED_GATEWAY": "true", "CMF_AUTH_JWT_SECRET": "short"})
    with pytest.raises(ConfigError):
        HarnessConfig.from_env({"HARNESS_PROFILE": "development", "CMF_AUTH_TRUSTED_GATEWAY": "true"})      # also in development
    cfg = HarnessConfig.from_env({"CMF_AUTH_TRUSTED_GATEWAY": "true", "CMF_AUTH_JWT_SECRET": SECRET})
    assert SECRET not in repr(cfg) and SECRET not in json.dumps(cfg.__dict__, default=str)                    # the secret never lives in the config object
    assert cfg.allow_asserted_identity is False                                                               # the flag no longer enables asserted identities


def test_app_startup_fails_when_gateway_mode_has_no_secret(world, monkeypatch):
    build, m, _ = world
    monkeypatch.setenv("HARNESS_PROFILE", "production")
    monkeypatch.setenv("CMF_AUTH_TRUSTED_GATEWAY", "true")
    monkeypatch.delenv("CMF_AUTH_JWT_SECRET", raising=False)
    monkeypatch.setattr(m, "_harness", None)
    with pytest.raises(ConfigError):
        with TestClient(m.app):
            pass


def test_auth_token_module_round_trip_and_checks():
    from harness import auth_tokens as at
    tok = at.issue_token(SECRET, "SUP-9", "supervisor", ttl_seconds=60)
    c = at.verify_token(tok, SECRET)
    assert c["sub"] == "SUP-9" and c["role"] == "supervisor" and c["exp"] > time.time()
    with pytest.raises(at.AuthError):
        at.verify_token(tok, OTHER_SECRET)
    with pytest.raises(at.AuthError):
        at.verify_token(at.issue_token(SECRET, "x", "advisor", ttl_seconds=-5), SECRET)
    with pytest.raises(at.AuthError):
        at.verify_token(tok, "short")                                                                         # a weak verification secret is refused
    with pytest.raises(at.AuthError):
        at.verify_token("a" * 20000, SECRET)                                                                  # size cap
    assert at.verify_token(at.issue_token(SECRET, "a", "advisor", ttl_seconds=-5), SECRET, now=time.time() - 3600)["sub"] == "a"   # clock is injectable


# ================================================================================================================== P0-2 withheld response
def ask_withheld(c, db, advisor="ADVISOR-001"):
    r = c.post("/api/ask", headers=bearer(advisor, "advisor"), json=ASK_SENSITIVE)
    assert r.status_code == 200, r.text
    return r.json()


def assert_no_content(payload, db):
    blob = json.dumps(payload, ensure_ascii=False)
    for text in kenji_text(db):
        assert text[:60] not in blob, f"stored memory text leaked: {text[:60]!r}"
    for mid in kenji_ids(db):
        assert mid not in blob, f"memory id leaked: {mid}"
    for needle in ("Current knowledge", "Superseded earlier knowledge", "replaced by", "retirement_age", "valid from", "epistemic"):
        assert needle not in blob, f"leaked fragment: {needle!r}"


def test_pending_approval_response_contains_no_report_content(world):
    """BEFORE: released=false still returned 11 evidence descriptions, memory labels, ids and conflict text."""
    build, m, db = world
    c = build("production")
    body = ask_withheld(c, db)
    assert body["released"] is False and body["status"] == "pending_approval"
    assert body["approval"]["approval_id"].startswith("APR-") and body["approval"]["status"] == "pending"
    rep = body["report"]
    assert rep["key_findings"] == [] and rep["historical_context"] == [] and rep["financial_analysis"] == []
    assert rep["evidence"] == [] and rep["assumptions"] == [] and rep["limitations"] == [] and rep["uncertainties"] == []
    assert body["retrieved_memory_ids"] == [] and body["agent_run"] is None and body["harness"] is None
    assert_no_content(body, db)


def test_withheld_response_has_only_safe_keys(world):
    build, m, db = world
    body = ask_withheld(build("production"), db)
    assert set(body) <= {"request_id", "client_ids", "report", "retrieved_memory_ids", "agent_run", "harness", "released", "status", "approval", "message"}
    assert set(body["approval"]) <= {"approval_id", "status", "category", "created_at"}
    assert "conflict" not in json.dumps(body).lower() and "tool" not in json.dumps(body["approval"]).lower()


def test_approval_status_endpoint_never_exposes_the_draft(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    r = c.get(f"/api/approvals/{aid}", headers=bearer("ADVISOR-001", "advisor"))
    assert r.status_code == 200 and "draft" not in r.text
    assert_no_content(r.json(), db)


def test_rejected_approval_response_contains_no_report_content(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    d = c.post(f"/api/approvals/{aid}/decision", headers=bearer("SUP-1", "supervisor"), json={"approve": False, "reason": "not appropriate for this client"})
    assert d.status_code == 200 and d.json()["status"] == "rejected"
    rel = c.post(f"/api/approvals/{aid}/release", headers=bearer("ADVISOR-001", "advisor"))
    assert rel.status_code == 200
    body = rel.json()
    assert body["released"] is False and body["status"] == "rejected" and body["report"]["evidence"] == []
    assert_no_content(body, db)
    assert_no_content(c.get(f"/api/approvals/{aid}", headers=bearer("ADVISOR-001", "advisor")).json(), db)


def test_release_while_pending_is_still_withheld(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    body = c.post(f"/api/approvals/{aid}/release", headers=bearer("ADVISOR-001", "advisor")).json()
    assert body["released"] is False and body["status"] == "pending_approval"
    assert_no_content(body, db)


def test_only_an_authorised_actor_can_release(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    c.post(f"/api/approvals/{aid}/decision", headers=bearer("SUP-1", "supervisor"), json={"approve": True, "reason": "reviewed the sources"})
    # P1.2 CONTRACT CHANGE (deliberate): another advisor's book used to answer 403, which revealed that the approval exists.  Unknown and not-yours are now both 404.
    assert c.post(f"/api/approvals/{aid}/release", headers=bearer("ADVISOR-002", "advisor")).status_code == 404     # another advisor's book
    assert c.post(f"/api/approvals/{aid}/release").status_code == 401
    assert c.post("/api/approvals/APR-NOPE/release", headers=bearer("ADVISOR-001", "advisor")).status_code == 404


def test_approved_release_returns_the_authorised_report(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    ok = c.post(f"/api/approvals/{aid}/decision", headers=bearer("SUP-1", "supervisor"), json={"approve": True, "reason": "reviewed the sources"})
    assert ok.status_code == 200
    body = c.post(f"/api/approvals/{aid}/release", headers=bearer("ADVISOR-001", "advisor")).json()
    assert body["released"] is True and body["status"] == "released"
    assert body["report"]["evidence"] and "withheld" not in body["report"]["summary"].lower()
    assert any(kenji in json.dumps(body) for kenji in ("MEM-",))                                           # the authorised report carries its evidence


def test_release_is_refused_after_the_data_version_changes(world):
    build, m, db = world
    c = build("production")
    aid = ask_withheld(c, db)["approval"]["approval_id"]
    c.post(f"/api/approvals/{aid}/decision", headers=bearer("SUP-1", "supervisor"), json={"approve": True, "reason": "reviewed the sources"})
    m.store().execute("UPDATE {S}memory_records SET updated_at = '2099-01-01T00:00:00' WHERE client_id = 'CLIENT-001' AND memory_type = 'semantic'")
    body = c.post(f"/api/approvals/{aid}/release", headers=bearer("ADVISOR-001", "advisor"))
    assert body.status_code == 200 and body.json()["released"] is False
    assert_no_content(body.json(), db)


def test_pipeline_level_withholding_scrubs_the_report_itself(world):
    """Every consumer of PipelineResult (CLI, API, library) gets a scrubbed report; the draft lives only in the approval record."""
    build, m, db = world
    build("production")
    from agents.memory.agent import MemoryOrchestrator
    from harness import AuthContext
    from llm.client import MockLLM
    from orchestration.pipeline import run_pipeline
    from test_harness_pipeline import planner_out, report_out
    mem = MemoryOrchestrator(MockLLM(), store=m.store())
    res = run_pipeline("Should Kenji retire at 60?", MockLLM(handlers={"planner": [planner_out(task="forecast_feasibility")], "report": [report_out()]}), mem, persist_trace=False,
                       harness=m.harness(), auth=AuthContext.for_test("ADVISOR-001", "advisor"))
    assert res.released is False
    assert res.report.evidence == [] and res.report.key_findings == [] and res.report.uncertainties == [] and res.report.limitations == []
    assert_no_content(json.loads(res.report.model_dump_json()), db)
    rec = m.harness().approvals.store.get(res.harness["approval"]["approval_id"])
    assert rec.draft.evidence                                                                              # the real draft is kept server-side for release


# ================================================================================================================== P0-3 profiles and exposure
def test_shipped_default_configuration_is_production_safe():
    prod = dict(l.split("=", 1) for l in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if "=" in l and not l.lstrip().startswith("#"))
    assert prod["HARNESS_PROFILE"].strip() == "production"
    assert prod.get("CMF_API_HOST", "127.0.0.1").strip() in ("127.0.0.1", "localhost", "::1")
    assert prod.get("CMF_AUTH_JWT_SECRET", "").strip() == ""                                                # no secret ships in the example
    dev_file = ROOT / ".env.development.example"
    assert dev_file.is_file()
    dev = dict(l.split("=", 1) for l in dev_file.read_text(encoding="utf-8").splitlines() if "=" in l and not l.lstrip().startswith("#"))
    assert dev["HARNESS_PROFILE"].strip() == "development" and dev["CMF_API_HOST"].strip() == "127.0.0.1"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert ".env.development.example" in readme and "production" in readme.lower()


def test_loopback_binding_rules():
    from harness.config import check_bind, is_loopback_host
    dev = HarnessConfig.development()
    for h in ("127.0.0.1", "localhost", "::1", "127.0.0.5"):
        assert is_loopback_host(h)
        check_bind(dev, h)
    for h in ("0.0.0.0", "", "::", "192.168.1.20", "10.0.0.4", "example.com"):
        assert not is_loopback_host(h)
        with pytest.raises(ConfigError, match="loopback"):
            check_bind(dev, h)
    check_bind(HarnessConfig(), "0.0.0.0")                                                                   # production may bind anywhere: it requires signed identities


def test_development_profile_on_localhost_starts_and_warns(world, caplog):
    build, m, _ = world
    c = build("development", secret=None, CMF_API_HOST="127.0.0.1")
    with caplog.at_level(logging.WARNING, logger="cmf.api"):
        with c:
            assert c.get("/api/clients").status_code == 200                                                   # local workflow stays usable
    assert any("DEVELOPMENT" in r.getMessage() and "unauthenticated" in r.getMessage().lower() for r in caplog.records)


def test_development_profile_on_a_non_loopback_host_refuses_to_start(world):
    build, m, _ = world
    c = build("development", secret=None, CMF_API_HOST="0.0.0.0")
    with pytest.raises(ConfigError, match="loopback"):
        with c:
            pass


def test_development_anonymous_access_is_refused_to_remote_peers(world):
    """Defence in depth: even if uvicorn is started by hand with --host 0.0.0.0, dev-anonymous never serves a non-loopback peer."""
    build, m, _ = world
    build("development", secret=None)
    remote = TestClient(m.app, client=("203.0.113.9", 4444))
    assert remote.get("/api/clients").status_code == 401
    assert TestClient(m.app, client=("127.0.0.1", 4444)).get("/api/clients").status_code == 200


def test_production_profile_rejects_missing_identity_and_cannot_be_weakened(world):
    build, m, _ = world
    c = build("production", HARNESS_ALLOW_ANONYMOUS_DEV="true", HARNESS_ALLOW_ASSERTED_IDENTITY="true", HARNESS_PERMISSION_GATE="false", HARNESS_EVIDENCE_VERIFICATION="false",
              HARNESS_ENABLED="false")
    assert c.get("/api/clients").status_code == 401
    assert c.get("/api/clients", headers=FORGED).status_code == 401
    cfg = m.harness().config
    assert cfg.profile == "production" and cfg.permission_gate and cfg.evidence_verification and cfg.enabled
    assert not cfg.allow_anonymous_dev and not cfg.allow_asserted_identity and len(cfg.warnings) >= 3
    assert c.get("/api/clients", headers=bearer()).status_code == 200                                       # signed identity still works


def test_production_without_any_auth_configuration_fails_closed(world):
    build, m, _ = world
    c = build("production", secret=None)
    assert c.get("/api/clients", headers=bearer()).status_code == 401                                        # cannot verify anything -> nothing is accepted
    assert c.get("/api/clients").status_code == 401


def test_disabling_the_harness_in_development_does_not_open_the_api_to_remote_peers(world):
    """Found in self-review: HARNESS_ENABLED=false (development only) made identity() return None, which skipped the loopback-peer rule as well."""
    build, m, _ = world
    build("development", secret=None, HARNESS_ENABLED="false")
    assert TestClient(m.app, client=("203.0.113.9", 4444)).get("/api/clients").status_code == 401
    assert TestClient(m.app, client=("127.0.0.1", 4444)).get("/api/clients").status_code == 200
    assert TestClient(m.app, client=("203.0.113.9", 4444)).get("/api/health").status_code == 200       # health stays public


# ================================================================================================================== P0-4 lifecycle, health, routes
def first_episode(db) -> str:
    con = sqlite3.connect(db)
    mid = con.execute("select memory_id from memory_records where client_id=? and memory_type='episodic' and status='active' limit 1", (KENJI,)).fetchone()[0]
    con.close()
    return mid


def test_spoofed_lifecycle_actor_is_ignored_and_the_verified_actor_is_recorded(world):
    """BEFORE: the audit event stored the request body's `actor` ('somebody-else')."""
    build, m, db = world
    c = build("production")
    mid = first_episode(db)
    r = c.post(f"/api/memories/{mid}/lifecycle", headers=bearer("SUP-REAL", "supervisor"), json={"new_status": "disputed", "reason": "reviewed by supervisor", "actor": "somebody-else"})
    assert r.status_code == 200, r.text
    con = sqlite3.connect(db)
    actor, to = con.execute("select actor, to_status from memory_lifecycle_events where memory_id=? order by created_at desc, rowid desc limit 1", (mid,)).fetchone()
    con.close()
    assert (actor, to) == ("SUP-REAL", "disputed")
    assert c.post(f"/api/memories/{mid}/lifecycle", headers=bearer("SUP-REAL", "supervisor"), json={"new_status": "expired", "reason": "no actor field needed"}).status_code == 200


def test_lifecycle_requires_a_verified_privileged_identity(world):
    build, m, db = world
    c = build("production")
    mid = first_episode(db)
    body = {"new_status": "disputed", "reason": "reason given", "actor": "SUP-1"}
    assert c.post(f"/api/memories/{mid}/lifecycle", json=body).status_code == 401
    assert c.post(f"/api/memories/{mid}/lifecycle", headers=FORGED_SUP, json=body).status_code == 401
    assert c.post(f"/api/memories/{mid}/lifecycle", headers=bearer("ADVISOR-001", "advisor"), json=body).status_code == 403


def test_health_never_exposes_the_database_url(world, monkeypatch):
    """BEFORE: /api/health returned the raw CMF_MEMORY_DATABASE_URL (a PostgreSQL password would leak)."""
    build, m, db = world
    c = build("production")
    assert c.get("/api/clients", headers=bearer()).status_code == 200                       # the store is initialised on the real (SQLite) database first
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", "postgresql://cmf_user:SuperSecretPw@db.internal:5432/cmf")
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "SuperSecretPw" not in r.text and "postgresql" not in r.text and "sqlite" not in r.text and str(db) not in r.text


def test_global_memory_routes_require_identity(world):
    """BEFORE: GET /api/memories/<GLOBAL procedure> answered 200 with no identity."""
    build, m, db = world
    c = build("production")
    con = sqlite3.connect(db)
    gid = con.execute("select memory_id from memory_records where client_id='GLOBAL' limit 1").fetchone()[0]
    con.close()
    assert c.get(f"/api/memories/{gid}").status_code == 401
    assert c.get(f"/api/memories/{gid}", headers=FORGED).status_code == 401
    assert c.get(f"/api/memories/{gid}", headers=bearer("ADVISOR-001", "advisor")).status_code == 200


def test_unknown_ids_do_not_reveal_existence_to_unauthenticated_callers(world):
    build, m, db = world
    c = build("production")
    assert c.get("/api/memories/MEM-NOPE").status_code == 401
    assert c.get("/api/approvals/APR-NOPE").status_code == 401
    assert c.get("/api/clients/NOPE/brain").status_code == 401
    assert c.post("/api/memories/MEM-NOPE/lifecycle", json={"new_status": "expired", "reason": "reason", "actor": "x"}).status_code == 401
    assert c.get("/api/memories/MEM-NOPE", headers=bearer("ADVISOR-001", "advisor")).status_code == 404       # authenticated callers still get a useful 404


def test_docs_and_openapi_are_inaccessible_in_production_by_default(world):
    """BEFORE: /docs and /openapi.json answered 200 to anyone."""
    build, m, _ = world
    c = build("production")
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert c.get(path).status_code in (401, 404), path
        assert c.get(path, headers=bearer("SUP-1", "supervisor")).status_code in (401, 404), path             # off even for valid identities unless configured


def test_docs_in_production_need_explicit_opt_in_and_a_verified_identity(world):
    build, m, _ = world
    c = build("production", CMF_API_DOCS_ENABLED="true")
    assert c.get("/openapi.json").status_code == 401
    assert c.get("/openapi.json", headers=FORGED).status_code == 401
    r = c.get("/openapi.json", headers=bearer("ADVISOR-001", "advisor"))
    assert r.status_code == 200 and "/api/ask" in r.json()["paths"]


def test_docs_remain_available_for_local_development(world):
    build, m, _ = world
    c = build("development", secret=None)
    assert c.get("/openapi.json").status_code == 200 and c.get("/docs").status_code == 200


def test_every_api_route_except_health_requires_identity_in_production(world):
    """Sweep: a future route that forgets authorization fails this test."""
    build, m, _ = world
    c = build("production")
    seen = 0
    for route in m.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path == "/api/health":
            continue
        url = path.replace("{client_id}", "CLIENT-001").replace("{memory_id}", "MEM-X").replace("{approval_id}", "APR-X").replace("{conflict_id}", "CF-X")
        for method in sorted(m for m in route.methods if m not in ("HEAD", "OPTIONS")):
            r = c.request(method, url, json={"question": "hello there", "new_status": "expired", "reason": "reason", "actor": "x", "approve": True, "decision": "keep"})
            assert r.status_code == 401, (method, path, r.status_code)
            seen += 1
    assert seen >= 8
    assert c.get("/api/health").status_code == 200


def test_unauthorised_responses_advertise_bearer_authentication(world):
    build, m, _ = world
    r = build("production").get("/api/clients")
    assert r.status_code == 401 and "bearer" in r.headers.get("www-authenticate", "").lower()
