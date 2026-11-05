"""The shipped demo databases (data/seed/) install cleanly and serve the UI + tools without any LLM."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "utils"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import seed_demo_db  # noqa: E402

KENJI = "CLIENT-001"


def test_install_copies_and_refuses_overwrite(tmp_path):
    assert seed_demo_db.install(tmp_path, force=False) == 0
    assert (tmp_path / seed_demo_db.MEMORY_NAME).stat().st_size > 0
    assert seed_demo_db.install(tmp_path, force=False) == 1          # would overwrite: refused
    assert seed_demo_db.install(tmp_path, force=True) == 0


def test_seed_serves_kenjis_brain_through_the_api(tmp_path, monkeypatch):
    seed_demo_db.install(tmp_path, force=False)
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{tmp_path / seed_demo_db.MEMORY_NAME}")
    import api.main as m
    monkeypatch.setattr(m, "_store", None)
    try:
        b = TestClient(m.app).get(f"/api/clients/{KENJI}/brain").json()
        assert b["counts"]["by_type"] == {"episodic": 62, "semantic": 6, "procedural": 3}   # no working memories shipped
        retire = sorted((x for x in b["memories"] if x["topic"] == "retirement_age" and x["memory_type"] == "semantic"), key=lambda x: x["version_no"])
        assert [(x["value_text"], x["status"]) for x in retire] == [("65", "superseded"), ("63", "active")]
        assert len(b["relationships"]) == 73 and len(b["interactions"]) == 25
    finally:
        if m._store is not None:
            m._store.close()


def test_ops_seed_answers_a_tool_call(tmp_path, monkeypatch):
    seed_demo_db.install(tmp_path, force=False)
    monkeypatch.setenv("CMF_DATABASE_URL", f"sqlite:///{tmp_path / seed_demo_db.OPS_NAME}")
    from mcp_server.services import FinancialServices
    out = FinancialServices().get_client_profile(client_id=KENJI)
    assert out["tool"] == "get_client_profile" and out["client_id"] == KENJI
    assert out["data"]["age"] == 58 and out["data"]["retirement_target_age"] == 63
