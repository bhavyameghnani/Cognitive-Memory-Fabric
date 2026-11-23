"""Demo simulation (services/simulation.py, /api/simulation): pre-generated interactions become part of the source only when ingested, strictly in date order,
through the real ingestion path; the state is the memory database's own ingestion log.  Scripted models only; temporary databases and CSVs."""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agents.memory.store import MemoryStore  # noqa: E402
from services import simulation as sim  # noqa: E402
from services.conversations import Conversations  # noqa: E402

COLS = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index", "speaker", "utterance", "meeting_context", "source_type", "consent_status"]
CLIENT_TURN = "We have talked it over and we will keep the retirement date at 63."


def write_sim(path: Path) -> Path:
    rows = []
    for iid, day in (("SIM-001", "2026-07-10"), ("SIM-002", "2026-07-20")):
        for i, (sp, text) in enumerate((("Advisor", "Good morning, Kenji. Shall we start with the retirement date?"), ("Client", CLIENT_TURN),
                                         ("Advisor", "Understood. I will keep the plan on that basis.")), 1):
            rows.append(dict(interaction_id=iid, client_id="CLIENT-001", advisor_id="ADVISOR-001", interaction_datetime=f"{day}T09:00:00", channel="video", turn_index=i,
                             speaker=sp, utterance=text, meeting_context="Retirement date check-in", source_type="synthetic_simulation", consent_status="synthetic"))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture()
def world(tmp_path):
    conv = Conversations()
    st = MemoryStore(url=f"sqlite:///{(tmp_path / 'm.db').as_posix()}", conv=conv)
    st.init()
    yield conv, st, write_sim(tmp_path / "sim.csv")
    st.close()


def test_nothing_is_visible_before_ingestion_and_order_is_enforced(world):
    conv, st, p = world
    q = sim.queue(conv, st, "CLIENT-001", p)
    assert [x["interaction_id"] for x in q] == ["SIM-001", "SIM-002"] and not any(x["ingested"] for x in q)
    assert "SIM-001" not in conv.meetings                                            # the system has not "heard" it yet
    with pytest.raises(ValueError, match="not the next"):
        sim.activate(conv, st, "SIM-002", p)                                         # a later interaction cannot jump the queue
    sim.activate(conv, st, "SIM-001", p)
    assert conv.verify_excerpt("CLIENT-001", "SIM-001", 2, CLIENT_TURN)            # evidence checks can now resolve it


def test_a_source_row_is_never_replaced(world):
    conv, _, _ = world
    with pytest.raises(ValueError, match="already present"):
        conv.add_rows([dict(conv.meeting("INT-001")[0])])


def test_restore_re_adds_only_what_this_database_ingested(world):
    conv, st, p = world
    sim.activate(conv, st, "SIM-001", p)
    st.start_ingestion("SIM-001", "CLIENT-001", "mock", conv.transcript_hash("SIM-001"))
    st.finish_ingestion("SIM-001", "completed", 3, 0, 0, 0, "")
    fresh = Conversations()                                                          # a restart: a new source object, the same database
    assert sim.restore(st, fresh, p) == ["SIM-001"] and "SIM-002" not in fresh.meetings
    assert sim.next_for(fresh, st, "CLIENT-001", p)["interaction_id"] == "SIM-002"


def test_disabled_in_production(world):
    assert not sim.enabled("production")


def test_api_ingests_the_next_interaction_through_the_real_pipeline(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from llm.client import MockLLM
    from services import conversations as conversations_mod
    import api.main as m

    p = write_sim(tmp_path / "sim.csv")
    monkeypatch.setattr(sim, "SIM_CSV", p)
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{(tmp_path / 'mem.db').as_posix()}")
    monkeypatch.setenv("HARNESS_PROFILE", "development")
    monkeypatch.setenv("CMF_INGEST_MODE", "llm_first")
    monkeypatch.setattr(m, "_store", None)
    monkeypatch.setattr(m, "_harness", None)

    def extract(system, user, schema):
        return {"candidates": [dict(topic="retirement_age", subject="", statement="Kenji will keep the retirement date at 63.", value="63", certainty="confirmed",
                                    speaker_role="Client", turn_index=2, excerpt=CLIENT_TURN, corrects_value=None, reusable_procedure=False)]}

    def verify(system, user, schema):
        if "Topic:" in user and "client_figure" in system:
            return {"client_figure": True, "quantity": "age"}
        import re
        return {"items": [dict(id=i, certainty="confirmed", supported=True, worth_remembering=True) for i in range(len(re.findall(r"^Item \d+", user, re.M)))]}

    mock = MockLLM(handlers={"memory": extract, "memory_verifier": verify})
    monkeypatch.setattr("llm.client.make_llm", lambda: mock)
    conversations_mod.shared.cache_clear()                                           # a fresh shared source: this test adds rows to it
    try:
        with TestClient(m.app) as client:
            q = client.get("/api/simulation", params={"client_id": "CLIENT-001"}).json()
            assert q["next"] == {"CLIENT-001": "SIM-001"}
            assert client.post("/api/simulation/SIM-002/ingest").status_code == 409            # out of order
            r = client.post("/api/simulation/SIM-001/ingest")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["stored"] >= 1 and any(x["value_text"] == "63" and x["memory_type"] == "episodic" for x in body["new_memories"])
            assert client.post("/api/simulation/SIM-001/ingest").status_code == 409            # once only
            assert client.get("/api/simulation", params={"client_id": "CLIENT-001"}).json()["next"] == {"CLIENT-001": "SIM-002"}
            assert "SIM-001" in [i["interaction_id"] for i in client.get("/api/clients/CLIENT-001/brain").json()["interactions"]]
        if m._store is not None:
            m._store.close()
    finally:
        conversations_mod.shared.cache_clear()
