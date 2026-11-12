"""Harness at its integration boundaries: HTTP API (identity, 401/403, approvals), CLI, memory selection, trace hygiene.  Scripted/mock models only."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from test_memory_support import KENJI, REBECCA  # noqa: E402

from harness import HarnessConfig  # noqa: E402
from harness.context_manager import ContextBudgetController  # noqa: E402
from harness.token_counter import TokenCounter  # noqa: E402
from harness.trace_recorder import prune_traces  # noqa: E402
from orchestration.tracing import Tracer, redact  # noqa: E402
from schemas.memory import MemoryContext, MemoryItem  # noqa: E402
from schemas.plan import ExecutionPlan  # noqa: E402


# ------------------------------------------------------------------------------------------------------------------ API
@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{tmp_path / 'mem.db'}")
    monkeypatch.setenv("CMF_LLM_BACKEND", "mock")
    monkeypatch.setenv("HARNESS_TRACE_ENABLED", "false")
    import api.main as m
    monkeypatch.setattr(m, "_store", None)
    monkeypatch.setattr(m, "_harness", None)

    def build(profile="production", gateway=False):
        monkeypatch.setenv("HARNESS_PROFILE", profile)
        monkeypatch.setenv("CMF_AUTH_TRUSTED_GATEWAY", "true" if gateway else "false")
        if gateway:                                   # trusted-gateway mode now REQUIRES a verification secret (identity = signed token, never headers)
            monkeypatch.setenv("CMF_AUTH_JWT_SECRET", TEST_SECRET)
        monkeypatch.setattr(m, "_harness", None)
        return TestClient(m.app)
    yield build, m
    if m._store is not None:
        m._store.close()


TEST_SECRET = "integration-test-signing-secret-0123456789"


def who(actor, role="advisor"):
    """Authenticated identity = a correctly SIGNED token.  (These tests used to send bare X-CMF-Actor-* headers, which is exactly the vulnerability P0-1 closed.)"""
    from harness.auth_tokens import issue_token
    return {"Authorization": "Bearer " + issue_token(TEST_SECRET, actor, role, 600)}


def test_production_api_refuses_missing_and_merely_asserted_identity(api):
    build, m = api
    c = build("production")
    assert c.get("/api/clients").status_code == 401                                   # no identity
    assert c.get("/api/clients", headers=who("ADVISOR-001")).status_code == 401       # role missing
    assert c.get("/api/clients", headers=who("ADVISOR-001")).status_code == 401
    r = c.get("/api/clients", headers=who("ADVISOR-001"))
    assert r.status_code == 401
    assert c.get(f"/api/clients/{KENJI}/brain", headers=who("ADVISOR-001", "advisor")).status_code == 401   # asserted, not authenticated
    assert c.post("/api/ask", json={"question": "What did Kenji decide?"}).status_code == 401


def test_gateway_authenticated_advisor_sees_only_their_clients(api):
    build, m = api
    c = build("production", gateway=True)
    ids = {x["client_id"] for x in c.get("/api/clients", headers=who("ADVISOR-001")).json()}
    assert ids == {"CLIENT-001", "CLIENT-004"}
    assert c.get(f"/api/clients/{KENJI}/brain", headers=who("ADVISOR-001")).status_code == 200
    assert c.get(f"/api/clients/{REBECCA}/brain", headers=who("ADVISOR-001")).status_code == 404    # another advisor's client: P1.4.2 answers exactly like a client that does not exist (no existence oracle)
    assert c.get(f"/api/clients/{REBECCA}/brain", headers=who("ADVISOR-002")).status_code == 200
    assert c.get(f"/api/clients/{REBECCA}/brain", headers=who("ADVISOR-001", "root")).status_code == 403   # invalid role
    body = {"new_status": "expired", "reason": "reason here", "actor": "ADVISOR-001"}
    # P0-4: authorization now precedes the lookup, so an advisor (no `lifecycle` permission) cannot learn whether an id exists; a permitted caller still gets the useful 404
    assert c.post("/api/memories/MEM-NOPE/lifecycle", headers=who("ADVISOR-001"), json=body).status_code == 403
    assert c.post("/api/memories/MEM-NOPE/lifecycle", headers=who("SUP-1", "supervisor"), json=body).status_code == 404


def test_development_profile_allows_anonymous_but_labels_it(api):
    build, m = api
    c = build("development")
    assert c.get("/api/clients").status_code == 200
    assert len(c.get("/api/clients").json()) == 6


def test_real_ask_enforces_client_scope_and_returns_the_harness_summary(api):
    build, m = api
    c = build("production", gateway=True)
    ok = c.post("/api/ask", headers=who("ADVISOR-002"), json={"question": "How concentrated is Rebecca's portfolio?"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["released"] is True and body["client_ids"] == [REBECCA]
    assert body["harness"]["budget"]["llm_calls"] >= 1 and body["harness"]["budget"]["tool_calls"] >= 0
    assert "harness" in body["agent_run"] and body["report"]["validation"]
    denied = c.post("/api/ask", headers=who("ADVISOR-001"), json={"question": "How concentrated is Rebecca's portfolio?"})
    assert denied.status_code == 403 and "client_not_in_scope" in denied.text


def test_withheld_answer_can_only_be_released_by_a_supervisor(api):
    build, m = api
    c = build("production", gateway=True)
    r = c.post("/api/ask", headers=who("ADVISOR-002"), json={"question": "Can Rebecca afford to retire early?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] is False and "withheld" in body["report"]["summary"]
    assert body["harness"] is None                                                           # nothing about the withheld run leaves the process
    aid = body["approval"]["approval_id"]
    assert c.get(f"/api/approvals/{aid}", headers=who("ADVISOR-002")).json()["status"] == "pending"
    assert c.post(f"/api/approvals/{aid}/decision", headers=who("ADVISOR-002"), json={"approve": True, "reason": "approving myself"}).status_code == 403   # advisor role
    assert c.post(f"/api/approvals/{aid}/decision", headers=who("SUP-1", "supervisor"), json={"approve": True, "reason": "reviewed sources"}).status_code == 200
    assert c.get(f"/api/approvals/{aid}", headers=who("ADVISOR-002")).json()["status"] == "approved"
    assert c.post(f"/api/approvals/{aid}/decision", headers=who("SUP-2", "supervisor"), json={"approve": False, "reason": "too late"}).status_code == 403   # already decided
    assert c.get("/api/approvals/APR-NOPE", headers=who("SUP-1", "supervisor")).status_code == 404


# ------------------------------------------------------------------------------------------------------------------ CLI
def test_cli_ask_uses_the_same_harness_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("CMF_LLM_BACKEND", "mock")
    monkeypatch.setenv("HARNESS_PROFILE", "production")
    monkeypatch.setenv("HARNESS_TRACE_ENABLED", "false")
    from orchestration.cli import main
    assert main(["ask", "How concentrated is Rebecca's portfolio?"]) == 0
    out = capsys.readouterr().out
    assert "(harness:" in out and "model calls" in out
    assert main(["ask", "x", "--no-harness"]) == 2                                      # the harness cannot be switched off in production
    monkeypatch.setenv("HARNESS_MAX_LLM_CALLS", "0")
    assert main(["ask", "x"]) == 2                                                      # invalid configuration is reported, not ignored


# ------------------------------------------------------------------------------------------------------------------ memory selection
def mi(mid, client=KENJI, **kw):
    base = dict(memory_id=mid, client_id=client, memory_type="episodic", topic="retirement_age", subject="retirement_age", content="Kenji talked about retirement age", certainty="confirmed",
                status="active", event_date="2026-05-01", origin="conversation_llm", value_text="63")
    base.update(kw)
    return MemoryItem(**base)


def plan_for(clients, text="Why did Kenji's retirement age change?"):
    return ExecutionPlan(request_id="R", plan_id="P", request_text=text, client_ids=list(clients), user_intent=text, task_type="history", needs_history=True, needs_calculation=False,
                         subtasks=[], required_memory_categories=["semantic", "episodic"], memory_topics=["retirement_age"], required_data=[], required_tools=[], dependencies={},
                         execution_order=[], parallelizable_tasks=[], missing_information=[], success_criteria=["x"])


def controller(**kw):
    events = []
    return ContextBudgetController(HarnessConfig.for_test(**kw), TokenCounter("qwen"), emit=lambda c, n, **p: events.append((c, n, p))), events


def test_selection_drops_foreign_client_memories_and_unauthorised_contexts():
    ctl, events = controller()
    ctx = MemoryContext(client_id=KENJI, current=[mi("S1", memory_type="semantic", certainty="confirmed")], episodes=[mi("E1"), mi("LEAK", client=REBECCA)])
    other = MemoryContext(client_id=REBECCA, current=[mi("R1", client=REBECCA, memory_type="semantic")])
    out, sel = ctl.select_memory(plan_for([KENJI]), {KENJI: ctx, REBECCA: other}, {KENJI})
    assert set(out) == {KENJI} and [m.memory_id for m in out[KENJI].episodes] == ["E1"]
    reasons = {x["memory_id"]: x["reason"] for x in sel.excluded}
    assert reasons["LEAK"] == "foreign_client" and reasons["R1"] == "client_not_authorized_or_not_in_plan"
    assert events[-1][1] == "memory_selection"


def test_nothing_unverified_or_superseded_stays_in_the_current_group():
    ctl, _ = controller()
    ctx = MemoryContext(client_id=KENJI, current=[mi("OK", memory_type="semantic", certainty="confirmed"),
                                                  mi("OLD", memory_type="semantic", status="superseded", valid_to="2026-05-01"),
                                                  mi("MAYBE", memory_type="semantic", certainty="unconfirmed"),
                                                  mi("EXPL", memory_type="episodic", certainty="exploratory")])
    out, sel = ctl.select_memory(plan_for([KENJI]), {KENJI: ctx}, {KENJI})
    assert [m.memory_id for m in out[KENJI].current] == ["OK"]
    assert {d["memory_id"]: d["to"] for d in sel.demoted} == {"OLD": "history", "MAYBE": "episodes", "EXPL": "episodes"}
    assert all(m.certainty == "unconfirmed" for m in out[KENJI].episodes if m.memory_id == "MAYBE")      # demoted, never relabelled as verified


def test_token_budget_drops_the_least_valuable_memories_first_and_records_why():
    ctl, _ = controller(memory_context_tokens=260)
    eps = [mi(f"E{i}", content=("retirement age discussion " * 6) + str(i), certainty="confirmed" if i == 0 else "hypothetical", event_date=f"2026-0{1 + i % 5}-1{i % 9}") for i in range(12)]
    ctx = MemoryContext(client_id=KENJI, current=[mi("CUR", memory_type="semantic")], episodes=eps, unresolved=[mi("UNR", certainty="exploratory")])
    out, sel = ctl.select_memory(plan_for([KENJI]), {KENJI: ctx}, {KENJI})
    kept = {m.memory_id for m in out[KENJI].episodes}
    assert sel.tokens_after < sel.tokens_before and len(kept) < 12 and "E0" in kept                       # best (confirmed) episode survives
    assert [m.memory_id for m in out[KENJI].current] == ["CUR"] and [m.memory_id for m in out[KENJI].unresolved] == ["UNR"]   # protected groups
    assert {x["reason"] for x in sel.excluded} == {"token_budget"} and out[KENJI].episodes_omitted == 12 - len(kept)


# ------------------------------------------------------------------------------------------------------------------ trace hygiene
def test_token_counts_are_not_redacted_but_secrets_are():
    r = redact({"input_tokens": 12, "cached_tokens": 3, "api_key": "sk-abc", "token": "t", "note": "password=hunter2", "nested": {"secret": "s", "output_tokens": 5}})
    assert r["input_tokens"] == 12 and r["cached_tokens"] == 3 and r["nested"]["output_tokens"] == 5
    assert r["api_key"] == "***" and r["token"] == "***" and r["nested"]["secret"] == "***" and "hunter2" not in r["note"]


def test_prompts_are_hashed_not_stored_unless_enabled():
    from harness import AuthContext, Harness
    from llm.client import MockLLM
    for store_prompts in (False, True):
        t = Tracer("REQ-P", persist=False)
        rt = Harness(HarnessConfig.for_test(trace_store_prompts=store_prompts)).start("REQ-P", t, AuthContext.for_test("a", "admin"), None)
        rt.wrap_llm(MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}})).complete("planner", "SYSTEM-PROMPT-TEXT", "SECRET-CLIENT-QUESTION")
        blob = json.dumps(t.events)
        assert ("SECRET-CLIENT-QUESTION" in blob) is store_prompts
        assert any(e["component"] == "harness.llm" and e["event"] == "call" and e["payload"]["prompt_hash"] for e in t.events)


def test_trace_retention_by_age_and_count(tmp_path):
    for i in range(6):
        p = tmp_path / f"REQ-{i}.jsonl"
        p.write_text("{}", encoding="utf-8")
        age = (40 if i < 2 else 1) * 86400 + (6 - i)
        os.utime(p, (time.time() - age, time.time() - age))
    assert prune_traces(tmp_path, retention_days=30, max_files=3) == 3                      # two expired + one over the count limit
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["REQ-3.jsonl", "REQ-4.jsonl", "REQ-5.jsonl"]
    assert prune_traces(tmp_path / "missing", 30, 3) == 0
