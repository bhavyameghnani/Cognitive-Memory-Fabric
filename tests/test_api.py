"""HTTP layer used by the brain UI: read endpoints over a throw-away memory DB, 404s, lifecycle guard rails, /api/ask with the pipeline stubbed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tests.test_memory_support import KENJI  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{tmp_path / 'mem.db'}")
    import api.main as m
    monkeypatch.setattr(m, "_store", None)
    yield TestClient(m.app), m
    if m._store is not None:
        m._store.close()


def test_clients_and_empty_brain(client):
    c, _ = client
    clients = c.get("/api/clients").json()
    assert any(x["client_id"] == KENJI and x["first_name"] == "Kenji" for x in clients)
    b = c.get(f"/api/clients/{KENJI}/brain").json()
    assert b["memories"] == [] and len(b["interactions"]) > 0


def test_unknown_ids_are_404(client):
    c, _ = client
    assert c.get("/api/clients/NOPE/brain").status_code == 404
    assert c.get("/api/memories/MEM-NOPE").status_code == 404
    r = c.post("/api/memories/MEM-NOPE/lifecycle", json={"new_status": "expired", "reason": "test reason", "actor": "advisor-1"})
    assert r.status_code == 404


def test_ask_returns_report_and_retrieved_ids(client, monkeypatch):
    c, m = client
    import orchestration.pipeline as pl
    from schemas.report import AdvisorReport
    from schemas.plan import ExecutionPlan  # noqa: F401

    class Plan:
        request_id, client_ids = "REQ-1", [KENJI]

    class Item:
        def __init__(self, i): self.memory_id = i

    class Ctx:
        current, history, episodes, procedures, working, unresolved = [Item("A")], [], [Item("B")], [], [], []

    class Res:
        plan, memory = Plan(), {KENJI: Ctx()}
        report = AdvisorReport(request_id="REQ-1", client_id=KENJI, question="q?", summary="s")

    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: Res())
    monkeypatch.setenv("CMF_LLM_BACKEND", "mock")
    r = c.post("/api/ask", json={"question": "How is Kenji doing?"})
    assert r.status_code == 200
    assert r.json()["retrieved_memory_ids"] == ["A", "B"] and r.json()["report"]["summary"] == "s"
    run = r.json()["agent_run"]  # stub has no tools/timings/trace: the summary must still build
    assert run["memory"]["total"] == 2 and run["memory"]["by_group"]["episodes"] == 1 and run["degraded"] == []
    assert c.post("/api/ask", json={"question": "x"}).status_code == 422
