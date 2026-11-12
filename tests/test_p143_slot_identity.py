"""P1.4.3 F4 - canonical semantic slot identity: spelling / case / whitespace variants of a topic or subject are ONE slot, topics come from an approved vocabulary, blank identities are
refused, the database itself carries a canonical unique index, and legacy duplicates are reported (never merged, deleted or rewritten) while new competing writes are refused.
All databases are temporary files or :memory:."""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory.agent import MemoryOrchestrator  # noqa: E402
from agents.memory.errors import InvalidRecord, MemoryWriteRejected  # noqa: E402
from agents.memory.integrity import TOPIC_VOCABULARY, Principal, canon_slot  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from agents.planner.topics import TOPICS  # noqa: E402
from harness.evaluation import p15  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from services.conversations import shared  # noqa: E402

K = "CLIENT-001"
CONV = shared()
CP = Principal.service("consolidation", [K])
SD = dict(slot_key="x", basis="b", supporting_episodes=1)
EV = dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt="We've decided: 63.")


def mk(mid, topic="retirement_age", subject="retirement_age", **kw):
    r = dict(memory_id=mid, client_id=K, memory_type="semantic", topic=topic, subject=subject, title="t", content="retirement age = 63", value_text="63", certainty="confirmed",
             status="active", origin="consolidation", content_hash=mid, event_date="2026-05-01", valid_from="2026-05-01", version_no=50)
    r.update(kw)
    return r


def active(st):
    return [(r["memory_id"], r["topic"], r["subject"]) for r in st.records(K, "semantic", statuses=["active"])]


# ------------------------------------------------------------------------------------------------------------------------- canonical form
def test_canonical_form():
    assert {canon_slot(v) for v in ("retirement_age", "retirement_age ", " Retirement_Age", "RETIREMENT AGE", "retirement-age", "Retirement  Age")} == {"retirement_age"}
    assert canon_slot("") == canon_slot("   ") == canon_slot(None) == ""


def test_the_vocabulary_is_the_planners_own_and_covers_every_topic_in_the_seed_database():
    assert TOPIC_VOCABULARY == frozenset(TOPICS)
    con = sqlite3.connect("file:" + str(ROOT / "data" / "seed" / "cmf_memory_demo.db") + "?mode=ro", uri=True)       # read-only
    used = {r[0] for r in con.execute("SELECT DISTINCT topic FROM memory_records")}
    con.close()
    assert used <= TOPIC_VOCABULARY, used - TOPIC_VOCABULARY


# ------------------------------------------------------------------------------------------------------------------------- variants cannot create a second active version
@pytest.mark.parametrize("kw", [dict(subject="retirement_age "), dict(subject=" retirement_age"), dict(subject="Retirement_Age"), dict(subject="RETIREMENT AGE"), dict(subject="retirement-age"),
                                dict(topic="Retirement_Age"), dict(topic="retirement_age "), dict(topic=" RETIREMENT-AGE")])
def test_a_variant_spelling_cannot_open_a_competing_active_version(kw):
    w = p15.build_world()
    st = w.store
    n = len(active(st))
    assert any(t == "retirement_age" for _, t, _ in active(st))
    with pytest.raises(MemoryWriteRejected) as e:
        st.insert_memory(mk("S-variant", **kw), dict(SD), [EV], principal=CP)
    assert e.value.code in ("active_version_exists", "human_correction_protected")
    assert len(active(st)) == n


@pytest.mark.parametrize("kw", [dict(subject="   "), dict(subject="\t"), dict(topic="   "), dict(topic=""), dict(topic="zzz_not_a_topic"), dict(topic="Retirement Plans")])
def test_blank_or_unapproved_identities_are_refused(kw):
    w = p15.build_world()
    with pytest.raises(MemoryWriteRejected):
        w.store.insert_memory(mk("S-bad", **kw), dict(SD), [EV], principal=CP)
    assert w.store.get("S-bad") is None


def test_an_equivalent_topic_spelling_is_stored_canonically_when_the_slot_is_free():
    w = p15.build_world()
    st = w.store
    st.insert_memory(mk("S-free", topic="Liquidity Reserve", subject="Cash-Buffer", value_text="6", content="cash buffer = 6"), dict(SD), [EV], principal=CP)
    row = st.get("S-free")
    assert (row["topic"], row["subject"]) == ("liquidity_reserve", "cash_buffer")


def test_a_semantic_memory_without_a_subject_is_stored_with_its_topic_as_the_subject():
    w = p15.build_world()
    st = w.store
    st.insert_memory(mk("S-nosub", topic="liquidity_reserve", subject="", value_text="6", content="reserve = 6"), dict(SD), [EV], principal=CP)
    assert st.get("S-nosub")["subject"] == "liquidity_reserve"
    with pytest.raises(MemoryWriteRejected):
        st.insert_memory(mk("S-nosub2", topic="Liquidity-Reserve", subject="", value_text="7", content="reserve = 7"), dict(SD), [EV], principal=CP)


def test_automatic_consolidation_after_normalization_keeps_one_active_version_per_slot():
    w = p15.build_world()
    for _ in range(3):
        w.consolidator.consolidate_client(K)
    seen = {}
    for _, t, s in active(w.store):
        seen[(t, s)] = seen.get((t, s), 0) + 1
    assert seen and all(n == 1 for n in seen.values()) and w.store.slot_collisions() == []


def test_a_human_corrected_slot_still_cannot_be_taken_over_by_a_variant_spelling():
    w, cor = p15.corrected_world(with_conflict=False)
    st = w.store
    with pytest.raises(MemoryWriteRejected) as e:
        st.insert_memory(mk("S-takeover", subject="Retirement_Age "), dict(SD), [EV], principal=CP)
    assert e.value.code in ("human_correction_protected", "active_version_exists")
    assert st.get(cor)["status"] == "active"


# ------------------------------------------------------------------------------------------------------------------------- database-level constraint
def test_the_database_itself_rejects_a_variant_duplicate_even_if_the_boundary_is_bypassed():
    w = p15.build_world()
    st = w.store
    assert st.query("SELECT 1 AS x FROM sqlite_master WHERE name='uq_active_semantic_slot_canonical'")
    assert not st.query("SELECT 1 AS x FROM sqlite_master WHERE name='uq_active_semantic_slot'")
    with st._gate():                                                         # the strongest bypass: guard triggers open
        with pytest.raises(sqlite3.IntegrityError):
            st.execute("INSERT INTO memory_records (memory_id, client_id, memory_type, topic, subject, content, certainty, status, origin, content_hash, created_at, updated_at, version_no) "
                       "VALUES ('RAW-DUP', ?, 'semantic', ' Retirement_Age', 'RETIREMENT_AGE ', 'x', 'confirmed', 'active', 'consolidation', 'rawdup', 't', 't', 9)", (K,))


# ------------------------------------------------------------------------------------------------------------------------- legacy data
def _legacy(tmp_path, names=("LA", "LB"), subjects=("retirement_age", "Retirement_Age ")):
    path = tmp_path / "legacy.db"
    url = "sqlite:///" + str(path).replace("\\", "/")
    s = MemoryStore(url=url, conv=CONV)
    MemoryOrchestrator(MockLLM(handlers={}), store=s, conv=CONV).init()
    s.close()
    con = sqlite3.connect(str(path))
    con.execute("DROP INDEX IF EXISTS uq_active_semantic_slot_canonical")
    con.execute("DROP TRIGGER guard_memory_records_insert")
    con.execute("DROP TRIGGER guard_semantic_details_insert")
    for mid, subj in zip(names, subjects):
        con.execute("INSERT INTO memory_records (memory_id, client_id, memory_type, topic, subject, content, certainty, status, origin, content_hash, created_at, updated_at, event_date, valid_from, "
                    "version_no, verification) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, K, "semantic", "retirement_age", subj, "legacy " + mid, "confirmed", "active", "consolidation", "h" + mid, "t", "t", "2026-01-01", "2026-01-01", 1, "unverified"))
        con.execute("INSERT INTO semantic_details (memory_id, slot_key, basis, supporting_episodes) VALUES (?,?,?,?)", (mid, "retirement_age|" + subj, "b", 1))
    con.commit()
    con.close()
    return url, path


def test_legacy_duplicates_are_reported_kept_and_block_new_competing_writes(tmp_path):
    url, path = _legacy(tmp_path)
    s = MemoryStore(url=url, conv=CONV)
    s.init()                                                                  # heals the triggers I dropped; must NOT merge, delete or rewrite anything
    assert not s.query("SELECT 1 AS x FROM sqlite_master WHERE name='uq_active_semantic_slot_canonical'")          # not creatable: the data collides
    coll = s.slot_collisions()
    assert coll == [dict(client_id=K, topic="retirement_age", subject="retirement_age", active_versions=2)]
    assert {r["memory_id"]: (r["subject"], r["content"], r["status"]) for r in s.records(K, "semantic", statuses=["active"]) if r["memory_id"] in ("LA", "LB")} == \
        {"LA": ("retirement_age", "legacy LA", "active"), "LB": ("Retirement_Age ", "legacy LB", "active")}      # user-visible data untouched, original spelling kept
    with pytest.raises(MemoryWriteRejected) as e:
        s.insert_memory(mk("S-new", subject="RETIREMENT-AGE"), dict(SD), [EV], principal=CP)
    assert e.value.code == "active_version_exists"
    assert s.integrity_ready()
    s.close()


def test_an_existing_p142_database_is_migrated_to_the_canonical_index(tmp_path):
    url, path = _legacy(tmp_path, names=("LA",), subjects=("retirement_age",))
    con = sqlite3.connect(str(path))
    con.execute("CREATE UNIQUE INDEX uq_active_semantic_slot ON memory_records (client_id, topic, COALESCE(NULLIF(subject, ''), topic)) WHERE memory_type='semantic' AND status='active'")
    con.execute("PRAGMA user_version = 122")
    con.commit()
    con.close()
    s = MemoryStore(url=url, conv=CONV)
    assert s.integrity_ready() is False                                       # a 122 database is not ready until it is migrated
    s.init()
    assert s.integrity_ready()
    assert s.query("SELECT 1 AS x FROM sqlite_master WHERE name='uq_active_semantic_slot_canonical'") and not s.query("SELECT 1 AS x FROM sqlite_master WHERE name='uq_active_semantic_slot'")
    assert int(s.query("PRAGMA user_version")[0]["user_version"]) == 123
    s.close()


# ------------------------------------------------------------------------------------------------------------------------- concurrency
@pytest.mark.parametrize("round_", range(3))
def test_concurrent_creates_with_variant_spellings_yield_exactly_one_active_version(tmp_path, round_):
    path = tmp_path / "race.db"
    url = "sqlite:///" + str(path).replace("\\", "/")
    boot = MemoryStore(url=url, conv=CONV)
    MemoryOrchestrator(MockLLM(handlers={}), store=boot, conv=CONV).init()
    boot.close()
    variants = ["liquidity_reserve", "Liquidity_Reserve", " liquidity_reserve", "LIQUIDITY RESERVE", "liquidity-reserve", "liquidity_reserve "]
    results, barrier = [], threading.Barrier(len(variants))

    def worker(i, v):
        st = MemoryStore(url=url, conv=CONV)
        try:
            barrier.wait()
            st.insert_memory(mk(f"RACE-{i}", topic="liquidity_reserve", subject=v, value_text="6", content="reserve = 6"), dict(SD), [EV], principal=CP)
            results.append("ok")
        except (MemoryWriteRejected, sqlite3.Error) as e:
            results.append(type(e).__name__ + ":" + str(getattr(e, "code", "")))
        finally:
            st.close()
    ts = [threading.Thread(target=worker, args=(i, v)) for i, v in enumerate(variants)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    chk = MemoryStore(url=url, conv=CONV)
    live = [r for r in chk.records(K, "semantic", statuses=["active"], topics=["liquidity_reserve"])]
    chk.close()
    assert len(results) == len(variants) and results.count("ok") == 1 and len(live) == 1, (results, [r["memory_id"] for r in live])
