"""P1.4.3 - the TTL equality boundary (`expires_at <= now`) that survived the P1.4.2 mutation audit.  The clock is the store's own constructor-injected clock; there is NO
caller-supplied timestamp parameter anywhere (that is itself asserted).  Temporary databases only."""
from __future__ import annotations

import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory.agent import MemoryOrchestrator  # noqa: E402
from agents.memory.errors import MemoryWriteRejected, ScopeDenied  # noqa: E402
from agents.memory.integrity import Principal  # noqa: E402
from agents.memory.store import MemoryStore, iso  # noqa: E402
from harness.evaluation import p15  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from services.conversations import shared  # noqa: E402

K, R = "CLIENT-001", "CLIENT-002"
T0 = datetime(2026, 11, 6, 10, 0, 0)
TTL = Principal.service("ttl_expiry", any_client=True)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def world(tmp_path=None, clock=None):
    """Two clients' working rows created at T0 with a 1 hour TTL (expires_at = 11:00:00 exactly)."""
    url = "sqlite:///" + (str(tmp_path / "ttl.db").replace("\\", "/") if tmp_path else ":memory:")
    clk = clock or Clock(T0)
    st = MemoryStore(url=url, conv=shared(), clock=clk)
    mem = MemoryOrchestrator(MockLLM(handlers={}), store=st, conv=shared())
    mem.init()
    a = mem.store_working("REQ-A", p15.plan_for(K, ("retirement_age",)), [], ttl_hours=1)
    b = mem.store_working("REQ-B", p15.plan_for(R, ("business_sale",)), [], ttl_hours=1)
    return mem, st, clk, a, b, url


def status(st, ids):
    return {st.get(i)["status"] for i in ids}


def test_premise_the_rows_expire_at_exactly_one_hour():
    _, st, _, a, _, _ = world()
    assert {r["expires_at"] for r in st.records(K, "working")} == {"2026-11-06T11:00:00"}


def test_no_caller_supplied_timestamp_exists_anywhere_on_the_expiry_path():
    assert list(inspect.signature(MemoryStore.expire_working).parameters) == ["self", "principal"]
    assert list(inspect.signature(MemoryOrchestrator.expire_working).parameters) == ["self"]
    with pytest.raises(TypeError):
        MemoryStore.expire_working(None, now_iso="2999-01-01")                                      # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Principal.service("ttl_expiry", any_client=True, as_of="2999")                              # type: ignore[call-arg]


@pytest.mark.parametrize("delta,expired", [(timedelta(seconds=-1), False), (timedelta(0), True), (timedelta(seconds=1), True), (timedelta(days=3), True), (timedelta(hours=-1), False)])
def test_boundary_before_equal_after(delta, expired):
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2026, 11, 6, 11, 0, 0) + delta
    done = mem.expire_working()
    assert (set(done) == set(a) | set(b)) is expired and (not done) is (not expired)
    assert status(st, a + b) == ({"expired"} if expired else {"active"})


def test_strictly_equal_is_expired_not_active():
    """The mutation that survived: `<=` -> `<`.  now == expires_at MUST expire."""
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2026, 11, 6, 11, 0, 0)
    assert st.now() == "2026-11-06T11:00:00" == st.records(K, "working")[0]["expires_at"]
    assert set(mem.expire_working()) == set(a) | set(b)


def test_timezone_aware_clock_is_converted_to_utc_before_comparing():
    ist = timezone(timedelta(hours=5, minutes=30))
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2026, 11, 6, 16, 29, 59, tzinfo=ist)              # 10:59:59 UTC: one second early
    assert mem.expire_working() == [] and status(st, a + b) == {"active"}
    clk.t = datetime(2026, 11, 6, 16, 30, 0, tzinfo=ist)               # 11:00:00 UTC: exactly the boundary
    assert set(mem.expire_working()) == set(a) | set(b)
    assert iso(datetime(2026, 11, 6, 16, 30, 0, tzinfo=ist)) == "2026-11-06T11:00:00" == iso(datetime(2026, 11, 6, 11, 0, 0, tzinfo=timezone.utc))


def test_a_reopened_database_expires_on_the_boundary(tmp_path):
    mem, st, clk, a, b, url = world(tmp_path)
    st.close()
    later = Clock(datetime(2026, 11, 6, 11, 0, 0))
    again = MemoryStore(url=url, conv=shared(), clock=later)
    assert status(again, a + b) == {"active"}
    assert set(again.expire_working(TTL)) == set(a) | set(b)
    assert status(again, a + b) == {"expired"}
    again.close()
    third = MemoryStore(url=url, conv=shared(), clock=later)
    assert third.expire_working(TTL) == [] and status(third, a + b) == {"expired"}
    third.close()


def test_a_client_scoped_ttl_principal_expires_only_that_clients_rows():
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2026, 11, 6, 11, 0, 0)
    assert set(st.expire_working(Principal.service("ttl_expiry", [K]))) == set(a)
    assert status(st, a) == {"expired"} and status(st, b) == {"active"}
    with pytest.raises(ScopeDenied):
        st.expire_working(Principal.service("ttl_expiry", []))                                       # an empty client scope is not a valid service identity
    assert set(st.expire_working(TTL)) == set(b)


def test_non_working_memory_never_expires_through_ttl():
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2999, 1, 1)
    before = {r["memory_id"]: r["status"] for r in st.query("SELECT memory_id, status FROM {S}memory_records WHERE memory_type<>'working'")}
    mem.expire_working()
    after = {r["memory_id"]: r["status"] for r in st.query("SELECT memory_id, status FROM {S}memory_records WHERE memory_type<>'working'")}
    assert before == after
    ep = next(r["memory_id"] for r in st.query("SELECT memory_id FROM {S}memory_records WHERE memory_type='procedural' LIMIT 1"))
    with pytest.raises(MemoryWriteRejected):
        st.set_status(ep, "expired", "r", "ttl_expiry", principal=TTL)


def test_repeated_expiry_is_idempotent_and_writes_one_event_per_row():
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2026, 11, 6, 11, 0, 0)
    first, second, third = mem.expire_working(), mem.expire_working(), mem.expire_working()
    assert set(first) == set(a) | set(b) and second == [] and third == []
    for i in a + b:
        assert [e["to_status"] for e in st.lifecycle_events(i)].count("expired") == 1


@pytest.mark.parametrize("who", [None, "user", "consolidation", "ingestion", "orchestrator", "forged"])
def test_no_expiry_through_an_unauthorised_principal(who):
    mem, st, clk, a, b, _ = world()
    clk.t = datetime(2026, 11, 6, 12, 0, 0)
    p = {None: None, "user": Principal.user("audit-advisor", [K], authenticated=True), "consolidation": Principal.service("consolidation", [K]), "ingestion": Principal.service("ingestion", [K]),
         "orchestrator": Principal.service("orchestrator", [K]), "forged": object()}[who]
    with pytest.raises((ScopeDenied, MemoryWriteRejected)):
        st.expire_working(p)
    assert status(st, a + b) == {"active"}
