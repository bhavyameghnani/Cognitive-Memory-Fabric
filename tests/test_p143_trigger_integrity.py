"""P1.4.3 F3 - integrity triggers are checked by DEFINITION, not by name, and a tampered or missing one is repaired transactionally or the store refuses writes.

THREAT BOUNDARY: whoever owns the database file can rewrite SQLite itself.  These tests prove reliable detection and fail-closed behaviour at startup / on every durable write,
not that SQLite can defend itself from its file owner.  All databases here are temporary files; nothing operational is opened."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory.agent import MemoryOrchestrator  # noqa: E402
from agents.memory.errors import MemoryIntegrityUnavailable  # noqa: E402
from agents.memory.integrity import Principal  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from harness.evaluation import p15  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from services.conversations import shared  # noqa: E402

K = "CLIENT-001"
CONV = shared()
ING = Principal.service("ingestion", [K])
EV = dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt="We've decided: 63.")
DET = dict(interaction_id="INT-090", turn_index=2, event_ts="2026-05-01T08:15:00", speaker_role="Client", event_type="decision")


def _open(path) -> MemoryStore:
    return MemoryStore(url="sqlite:///" + str(path).replace("\\", "/"), conv=CONV)


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    """A correctly initialised database file (copied per test, never modified in place) and its canonical trigger definitions."""
    path = tmp_path_factory.mktemp("p143") / "base.db"
    st = _open(path)
    MemoryOrchestrator(MockLLM(handlers={}), store=st, conv=CONV).init()
    defs = st._trigger_definitions()
    st.close()
    return path, defs


def _copy(pristine, tmp_path, name="t.db"):
    dst = tmp_path / name
    dst.write_bytes(Path(pristine[0]).read_bytes())
    return dst


def _raw(path):
    con = sqlite3.connect(str(path))
    return con


def _noop(sql: str) -> str:
    sql = re.sub(r"BEGIN.*END", "BEGIN SELECT 1; END", sql, flags=re.S | re.I)
    return re.sub(r"\s+WHEN\s+cmf_write_allowed\(\)\s*=\s*0", "", sql, flags=re.I)


def _tamper(path, name, how="noop"):
    con = _raw(path)
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()[0]
    con.execute(f"DROP TRIGGER {name}")
    if how == "noop":
        con.execute(_noop(sql))
    con.commit()
    con.close()
    return sql


def _rec(mid="MEM-T1"):
    return dict(memory_id=mid, client_id=K, memory_type="episodic", topic="retirement_age", subject="s" + mid, content="We've decided: 63.", value_text="63", certainty="confirmed",
                source_role="Client", status="active", origin="conversation_rule", event_date="2026-05-01", content_hash=mid)


def test_a_pristine_database_has_exactly_the_canonical_triggers(pristine):
    path, defs = pristine
    st = _open(path)
    assert len(defs) == 43 and st.integrity_ready() and st.trigger_mismatches() == {}
    have = {r["name"] for r in st.query("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert have == set(defs)                                                        # nothing missing, nothing unexpected
    st.close()


NAMES = sorted(MemoryStore(url="sqlite:///:memory:", conv=CONV)._trigger_definitions())


@pytest.mark.parametrize("name", NAMES)
def test_same_name_noop_trigger_is_detected_refused_and_repaired(pristine, tmp_path, name):
    path = _copy(pristine, tmp_path)
    original = _tamper(path, name)
    st = _open(path)
    n0 = st.count("memory_records")                                                             # the 3 curated procedures that init() seeds
    assert st.integrity_ready() is False and st.trigger_mismatches() == {name: "modified"}      # the old check (names only) said True here
    with pytest.raises(MemoryIntegrityUnavailable):
        st.insert_memory(_rec(), dict(DET), [dict(EV)], principal=ING)                         # fail closed: no durable write while a trigger is wrong
    assert st.count("memory_records") == n0
    st.init()                                                                                   # repair
    assert st.integrity_ready() and st.trigger_mismatches() == {} and st.repaired_triggers == {name: "modified"}
    now = st.query("SELECT sql FROM sqlite_master WHERE name=?", (name,))[0]["sql"]
    assert " ".join(now.split()) == " ".join(original.split()) == " ".join(st._trigger_definitions()[name].replace("IF NOT EXISTS ", "").split())
    st.close()
    again = _open(path)                                                                         # and the repair is durable across a reopen
    again.insert_memory(_rec(), dict(DET), [dict(EV)], principal=ING)
    assert again.integrity_ready() and again.get("MEM-T1") is not None
    again.close()


@pytest.mark.parametrize("name", NAMES)
def test_missing_trigger_is_detected_refused_and_restored(pristine, tmp_path, name):
    path = _copy(pristine, tmp_path)
    _tamper(path, name, how="drop")
    st = _open(path)
    assert st.integrity_ready() is False and st.trigger_mismatches() == {name: "missing"}
    with pytest.raises(MemoryIntegrityUnavailable):
        st.insert_memory(_rec(), dict(DET), [dict(EV)], principal=ING)
    st.init()
    assert st.integrity_ready() and st.repaired_triggers == {name: "missing"}
    st.close()


@pytest.mark.parametrize("variant", ["when_removed", "event_changed", "table_changed", "body_changed", "action_message_changed"])
def test_each_kind_of_definition_change_is_detected(pristine, tmp_path, variant):
    path = _copy(pristine, tmp_path)
    name = "guard_memory_records_delete"
    con = _raw(path)
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
    con.execute(f"DROP TRIGGER {name}")
    new = {"when_removed": re.sub(r"\s+WHEN\s+cmf_write_allowed\(\)\s*=\s*0", "", sql, flags=re.I),
           "event_changed": sql.replace("BEFORE DELETE", "BEFORE UPDATE"),
           "table_changed": sql.replace("ON memory_records", "ON episodic_details"),
           "body_changed": sql.replace("SELECT RAISE(ABORT, 'memory write outside the integrity boundary');", "SELECT 1;"),
           "action_message_changed": sql.replace("RAISE(ABORT,", "RAISE(IGNORE,").replace("'memory write outside the integrity boundary'", "")}[variant]
    assert new != sql
    try:
        con.execute(new)
    except sqlite3.Error:                                                                       # RAISE(IGNORE) needs no message; keep the case valid
        con.execute(sql.replace("RAISE(ABORT, 'memory write outside the integrity boundary')", "RAISE(IGNORE)"))
    con.commit()
    con.close()
    st = _open(path)
    assert st.integrity_ready() is False and name in st.trigger_mismatches()
    st.init()
    assert st.integrity_ready()
    st.close()


def test_several_tampered_triggers_are_all_repaired_in_one_init(pristine, tmp_path):
    path = _copy(pristine, tmp_path)
    for n in NAMES[:6]:
        _tamper(path, n)
    st = _open(path)
    assert len(st.trigger_mismatches()) == 6
    st.init()
    assert st.integrity_ready() and len(st.repaired_triggers) == 6
    st.close()


def test_partial_repair_failure_rolls_back_and_the_store_stays_not_ready(pristine, tmp_path, monkeypatch):
    path = _copy(pristine, tmp_path)
    targets = NAMES[:5]
    for n in targets:
        _tamper(path, n)
    before = {r[0]: r[1] for r in _raw(path).execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    st = _open(path)
    real = st.execute
    seen = {"creates": 0}

    def flaky(sql, params=()):
        if str(sql).startswith("CREATE TRIGGER"):
            seen["creates"] += 1
            if seen["creates"] == 3:
                raise sqlite3.OperationalError("disk I/O error (injected)")
        return real(sql, params)
    monkeypatch.setattr(st, "execute", flaky)
    with pytest.raises(sqlite3.OperationalError):
        st.init()
    monkeypatch.undo()
    assert seen["creates"] == 3
    assert st.integrity_ready() is False                                                         # NOT healthy after a partial repair
    after = {r[0]: r[1] for r in _raw(path).execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    assert after == before                                                                       # the whole repair was rolled back: no half-repaired database
    with pytest.raises(MemoryIntegrityUnavailable):
        st.insert_memory(_rec(), dict(DET), [dict(EV)], principal=ING)
    st.init()                                                                                    # a later, working init repairs everything
    assert st.integrity_ready()
    st.close()


def test_repair_that_cannot_reach_the_canonical_definition_is_refused(pristine, tmp_path, monkeypatch):
    path = _copy(pristine, tmp_path)
    _tamper(path, "write_log_no_delete")
    st = _open(path)
    monkeypatch.setattr(type(st), "_norm_sql", staticmethod(lambda sql: object()))                # verification can never succeed
    with pytest.raises(MemoryIntegrityUnavailable):
        st.init()
    monkeypatch.undo()
    assert st.integrity_ready() is False
    st.close()


# ------------------------------------------------------------------------------------------------------------------------- behaviour, not just text
def test_a_neutered_guard_lets_a_raw_write_through_until_repaired_and_then_blocks_it_again():
    w = p15.build_world()
    st = w.store
    mid = st.records(K, "semantic")[0]["memory_id"]
    with pytest.raises(sqlite3.Error):
        st.execute("UPDATE memory_records SET status='expired' WHERE memory_id=?", (mid,))
    with st._maintenance():                                                                      # the "attacker": same-name no-op replacement
        st.execute("DROP TRIGGER guard_memory_records_update")
        st.execute("CREATE TRIGGER guard_memory_records_update BEFORE UPDATE OF status ON memory_records BEGIN SELECT 1; END")
    assert st.integrity_ready() is False                                                         # detected immediately by the readiness check
    st.init()
    assert st.integrity_ready()
    with pytest.raises(sqlite3.Error):
        st.execute("UPDATE memory_records SET status='expired' WHERE memory_id=?", (mid,))
    assert st.get(mid)["status"] != "expired"


def test_every_append_only_and_guard_trigger_blocks_its_raw_operation_after_repair():
    """Every canonical trigger whose table holds a row in a world that has episodes, semantic versions, a conflict and its resolution: the raw statement it exists to block is blocked."""
    w, cor = p15.corrected_world()
    cf = [c for c in w.store.conflicts(K, "open") if c["kind"] == "human_correction_vs_evidence"][0]
    w.store.boundary.resolve_conflict(cf["conflict_id"], "keep", "ok", principal=Principal.user("audit-advisor", [K], authenticated=True))
    st = w.store
    st.init()
    blocked = 0
    for name, sql in st._trigger_definitions().items():
        m = re.search(r"BEFORE (INSERT|UPDATE|DELETE)(?: OF [^ ]+(?:, [^ ]+)*)? ON (\w+)", sql)
        op, table = m.group(1), m.group(2)
        if op == "INSERT":
            stmt = f"INSERT INTO {table} DEFAULT VALUES"
        elif op == "DELETE":
            if not st.count(table):
                continue
            stmt = f"DELETE FROM {table}"
        else:
            if not st.count(table):
                continue
            col = "status" if table == "memory_records" else st.query(f"SELECT name FROM pragma_table_info('{table}') WHERE pk=0 LIMIT 1")[0]["name"]
            if "UPDATE OF" in sql and col not in sql:
                col = re.search(r"UPDATE OF (\w+)", sql).group(1)
            stmt = f"UPDATE {table} SET {col}={col}"
        with pytest.raises(sqlite3.Error):
            st.execute(stmt)
        blocked += 1
    assert blocked >= 30
