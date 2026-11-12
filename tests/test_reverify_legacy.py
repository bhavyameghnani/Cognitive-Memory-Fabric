"""Re-verification of legacy memories (migration 124): append-only, uses the boundary's own rules, never edits a memory row."""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.memory.errors import MemoryWriteRejected  # noqa: E402
from agents.memory.integrity import Principal  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "data" / "seed" / "cmf_memory_demo.db"
VERIFIABLE_EPISODE = "MEM-EPI-1301b1bc15da"     # rule-extracted sentence that IS the cited source sentence ("65", confirmed)
CURATED = "MEM-PRO-f526b52938dd"
TERSE_CURRENT = "MEM-SEM-33d022d24ad8"          # derived 63: its episodes cite "We've decided: 63." and the sentence-level checker rightly does not accept the paraphrase


def operator(*clients: str) -> Principal:
    return Principal.user("local-operator", list(clients) or ["CLIENT-001", "GLOBAL"], authenticated=True)


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "mem.db"
    shutil.copyfile(SEED, db)
    s = MemoryStore(url=f"sqlite:///{db}")
    s.init()
    yield s
    s.close()


def test_legacy_rows_start_unverified_and_the_shipped_seed_is_untouched(store):
    assert {r["verification"] for r in store.records("CLIENT-001")} == {"unverified"}
    raw = sqlite3.connect(f"file:{SEED}?mode=ro", uri=True)
    assert raw.execute("select count(*) from sqlite_master where name='memory_reverifications'").fetchone()[0] == 0   # the copy was upgraded, never the seed


def test_rows_that_pass_the_boundary_rules_become_verified(store):
    out = store.reverify_legacy(principal=operator())
    assert out["checked"] == out["verified"] + out["unverified"] and out["verified"] > 0
    assert store.get(VERIFIABLE_EPISODE)["verification"] == "verified"
    assert store.get(CURATED)["verification"] == "verified"
    assert "predates" not in store.get(VERIFIABLE_EPISODE)["verification_basis"]            # the reading shows WHY it is verified now


def test_paraphrases_the_checker_cannot_match_stay_unverified(store):
    out = store.reverify_legacy(principal=operator())
    assert store.get(TERSE_CURRENT)["verification"] == "unverified"
    assert out["unverified"] > 0 and out["reasons"]                                          # every refusal is explained


def test_the_stored_column_is_never_edited_and_the_run_is_idempotent(store):
    first = store.reverify_legacy(principal=operator())
    stored = store.query("select verification from memory_records where memory_id=?", (VERIFIABLE_EPISODE,))[0]["verification"]
    assert stored == "unverified"                                                            # immutable: the result lives in the append-only table
    again = store.reverify_legacy(principal=operator())
    assert again["verified"] == 0 and again["checked"] == first["unverified"]
    assert store.query("select count(*) c from memory_reverifications")[0]["c"] == first["verified"]


def test_the_table_is_append_only_and_only_the_boundary_can_write_it(store):
    store.reverify_legacy(principal=operator())
    for sql in ("update memory_reverifications set verification='verified'", "delete from memory_reverifications",
                "insert into memory_reverifications values ('x','CLIENT-001','verified','b',0,'op','a','t')"):
        with pytest.raises(Exception):
            store.execute(sql)
    assert store.integrity_ready() and store.trigger_mismatches() == {}


def test_scope_is_respected_and_an_identity_is_required(store):
    out = store.reverify_legacy(principal=operator("GLOBAL"))
    assert set(out["by_client"]) == {"GLOBAL"}                                               # CLIENT-001 rows are outside this operator's scope
    with pytest.raises(MemoryWriteRejected):
        store.reverify_legacy(principal=None)
    with pytest.raises(MemoryWriteRejected):
        store.reverify_legacy(principal=Principal.user("local-operator", ["CLIENT-001"], authenticated=False))


def test_a_client_filter_limits_the_run(store):
    out = store.reverify_legacy(principal=operator(), client_id="CLIENT-001")
    assert set(out["by_client"]) == {"CLIENT-001"}
