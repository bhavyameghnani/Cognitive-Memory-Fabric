"""P1.3.1 - runtime reliability hardening (audit findings F1-F8).  Deterministic fake providers; no network, no live LLM.
Races are driven with Events / Barriers / lock-ordering, not with sleeps (the few waits are bounded polls for a state that another thread must reach).
NOTE: no `from __future__ import annotations` - Strands' @hook infers the event type from the annotation.
"""
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent
from strands.plugins import Plugin, hook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import harness.strands_adapter as sa  # noqa: E402
from harness import AuthContext, BudgetExceeded, Harness, HarnessConfig  # noqa: E402
from harness.budget import RequestBudget  # noqa: E402
from harness.hooks import CmfBudgetPlugin  # noqa: E402
from harness.reliability import INCIDENT_KINDS, InstrumentationError  # noqa: E402
from harness.runtime import _report_digest, _report_proposal  # noqa: E402
from harness.strands_adapter import CallState, ModelCallGuard, invoke_inner  # noqa: E402
from harness.workers import CANCELS, REGISTRY, CancelPool, CapacityExhausted, WorkerRegistry  # noqa: E402
from llm.client import LLMError, LLMUsage  # noqa: E402
from schemas.report import AdvisorReport  # noqa: E402
from test_p13_reliability import Fake, events, kinds, ok_text, rig, wait_for  # noqa: E402

BOTH = pytest.mark.parametrize("strands", [True, False], ids=["strands", "direct"])


class OnlyBefore(Plugin):
    """Registers only the BeforeModelCall budget hook: the completion hook never runs."""
    name = "only-before"

    def __init__(self, guard, state):
        super().__init__()
        self.guard, self.state = guard, state

    @hook
    def enforce(self, event: BeforeModelCallEvent):
        self.state.before_called = True
        self.guard.before(self.state)
        self.state.before_ok = True


# ================================================================================================================== F1: interpreter exit
CHILD = textwrap.dedent('''
    import json, sys, threading, time
    sys.path.insert(0, {src!r}); sys.path.insert(0, {tests!r})
    from harness import AuthContext, BudgetExceeded, Harness, HarnessConfig
    from orchestration.tracing import Tracer
    from llm.client import LLMUsage

    forever = threading.Event()                         # never set: the provider call blocks indefinitely
    class Blocked:
        model, native_timeout = "fake", False
        def complete(self, agent, system, user, schema=None):
            forever.wait()
            return "{{}}"
        def pop_usage(self): return None

    h = Harness(HarnessConfig.for_test(strands_execution={strands}, max_model_call_seconds=0.4))
    rt = h.start("REQ-EXIT", Tracer("REQ-EXIT", persist=False), AuthContext.for_test("advisor-1", "admin"), None)
    llm = rt.wrap_llm(Blocked())
    t0 = time.time()
    try:
        llm.complete("report", "sys", "user", None)
        reason = "NO-TIMEOUT"
    except BudgetExceeded as e:
        reason = e.reason
    s = rt.finish()
    print(json.dumps(dict(reason=reason, waited=round(time.time() - t0, 2), llm_calls=s["budget"]["llm_calls"], input_tokens=s["budget"]["input_tokens"],
                          outcome=s["outcome"], ghost_pending=s["telemetry"]["ghost_pending"], timeouts=s["telemetry"]["timeouts"],
                          kinds=[i["kind"] for i in s["telemetry"]["incidents"]], abandoned_live=s["workers"]["provider"]["abandoned_live"])), flush=True)
''')


@BOTH
def test_f1_a_provider_call_that_never_returns_does_not_block_interpreter_exit(strands, tmp_path):
    """BEFORE: on the Strands transport asyncio.to_thread put the provider call on the default executor, which is joined at exit -> the child never exited."""
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(src=str(ROOT / "src"), tests=str(ROOT / "tests"), strands=strands), encoding="utf-8")
    t0 = time.time()
    try:
        done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=25, encoding="utf-8", cwd=str(tmp_path))
    except subprocess.TimeoutExpired as e:      # the regression: the process is still alive
        pytest.fail(f"the Python process did not exit although the request already timed out (transport strands={strands}): {e.stdout!r}")
    elapsed = time.time() - t0
    assert done.returncode == 0, done.stderr[-800:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["reason"] == "model_call_timeout" and out["waited"] < 5
    assert out["llm_calls"] == 1 and out["input_tokens"] > 0 and out["timeouts"] == 1             # the call stays counted and the prompt stays charged
    assert out["outcome"] == "degraded" and out["ghost_pending"] == 1 and out["abandoned_live"] == 1   # honestly reported: the provider work is still running
    assert {"provider_timeout", "provider_cancel_unsupported"} <= set(out["kinds"])
    assert elapsed < 20, elapsed                # strict bound: python start-up + 0.4 s timeout + exit


# ================================================================================================================== F2: usage on instrumentation failure
def _only_before(monkeypatch):
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: OnlyBefore(guard, state))


def test_f2_exact_provider_usage_is_charged_once_when_the_completion_hook_is_missing(monkeypatch):
    _only_before(monkeypatch)
    prov = Fake(ok_text)                         # reports exact usage 30 in / 6 out
    h, rt, llm, tr = rig(prov, strands_execution=True)
    with pytest.raises(InstrumentationError) as e:
        llm.complete("report", "sys", "user", None)
    assert e.value.kind == "hook_missing"
    b = rt.budget
    assert (b.llm_calls, b.input_tokens, b.output_tokens) == (1, 30, 6)                      # performed work stays charged, exactly once, call count preserved
    rec = rt.telemetry.records[-1]
    assert (rec.outcome, rec.input_tokens, rec.output_tokens, rec.exact) == ("error", 30, 6, True)
    inc = next(i for i in rt.telemetry.incidents if i["kind"] == "hook_missing")
    assert inc["charged_input_tokens"] == 30 and inc["charged_output_tokens"] == 6 and inc["charged_estimated"] is False
    llm.accept_last()                                                                          # nothing pending: the unverified text was neither cached nor exposed
    with pytest.raises(InstrumentationError):
        llm.complete("report", "sys", "user", None)
    assert len(prov.calls) == 2, "a second identical call must reach the provider again (nothing was cached)"
    assert (b.llm_calls, b.input_tokens, b.output_tokens) == (2, 60, 12)                      # charged once PER CALL, not twice per call


def test_f2_estimated_usage_is_charged_when_the_provider_reports_none(monkeypatch):
    _only_before(monkeypatch)
    prov = Fake(lambda p, a, u, n: '{"ok": true}')                                               # no usage reported
    h, rt, llm, tr = rig(prov, strands_execution=True)
    with pytest.raises(InstrumentationError):
        llm.complete("report", "sys", "user", None)
    rec = rt.telemetry.records[-1]
    assert rec.exact is False and rec.input_tokens > 0 and rec.output_tokens > 0
    assert (rt.budget.input_tokens, rt.budget.output_tokens, rt.budget.llm_calls) == (rec.input_tokens, rec.output_tokens, 1)


def test_f2_a_failing_completion_hook_charges_once(monkeypatch):
    class AfterExplodes(CmfBudgetPlugin):
        @hook
        def record_completion(self, event: AfterModelCallEvent):
            self.state.strands_after_called = True
            self.state.hook_error = RuntimeError("boom")
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: AfterExplodes(guard, state))
    h, rt, llm, tr = rig(Fake(ok_text), strands_execution=True)
    with pytest.raises(InstrumentationError) as e:
        llm.complete("report", "sys", "user", None)
    assert e.value.kind == "hook_failed"
    assert (rt.budget.llm_calls, rt.budget.input_tokens, rt.budget.output_tokens) == (1, 30, 6)


def test_f2_a_missing_budget_hook_charges_nothing_because_the_provider_was_never_contacted(monkeypatch):
    class NoHooks(Plugin):
        name = "no-hooks"
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: NoHooks())
    prov = Fake(ok_text)
    h, rt, llm, tr = rig(prov, strands_execution=True)
    with pytest.raises(InstrumentationError):
        llm.complete("report", "sys", "user", None)
    assert prov.calls == [] and (rt.budget.llm_calls, rt.budget.input_tokens, rt.budget.output_tokens) == (0, 0, 0)


def test_f2_settlement_is_idempotent_across_every_path():
    b = RequestBudget(HarnessConfig.for_test())
    g = ModelCallGuard(b)
    st = CallState("report", "s", "u", None, 0, 10)
    assert g.settle(st, 100, 20) is True
    assert g.settle(st, 100, 20) is False and g.settle(st, 5, 0, abandoned=True) is False and g.after(st, 7, 7) is None
    assert (b.input_tokens, b.output_tokens) == (100, 20)
    st2 = CallState("report", "s", "u", None, 0, 40)
    assert g.settle(st2, 40, 0, abandoned=True) and not g.settle(st2, 90, 9)                   # a timed-out call is not charged again by a later result
    assert (b.input_tokens, b.output_tokens) == (140, 20)


# ================================================================================================================== F3: bounded cancellation
class _Cancelable(Fake):
    def __init__(self, behave, cancel):
        super().__init__(behave)
        self.cancel = cancel

    def cancel_call(self, call_id):
        return self.cancel(call_id)


def test_f3_a_blocking_cancel_hook_never_blocks_the_request():
    """BEFORE: a cancel_call that blocked 3 s kept complete() waiting 3.3 s past a 0.3 s limit."""
    hang, gate = threading.Event(), threading.Event()
    prov = _Cancelable(lambda p, a, u, n: (gate.wait(10), ok_text(p, a, u, n))[1], lambda cid: (hang.wait(10), True)[1])
    h, rt, llm, tr = rig(prov, strands_execution=False, max_model_call_seconds=0.3, cancel_timeout_seconds=0.3)
    t0 = time.time()
    with pytest.raises(BudgetExceeded):
        llm.complete("report", "sys", "user", None)
    assert time.time() - t0 < 2.0
    try:
        assert wait_for(lambda: "provider_cancel_timeout" in kinds(rt))              # the cancel thread is bounded by its own timeout and reported as timed out
        ks = kinds(rt)
        assert "provider_cancel_started" in ks and ks.index("provider_cancel_started") < ks.index("provider_cancel_timeout")
        assert "provider_cancel_ok" not in ks and "provider_cancel_failed" not in ks
    finally:
        hang.set()
        gate.set()
    wait_for(lambda: rt.telemetry.summary()["ghost_pending"] == 0)
    assert kinds(rt).count("provider_cancel_timeout") == 1 and "provider_cancel_ok" not in kinds(rt), "a late cancel return must not produce a second terminal status"


def test_f3_cancel_dispatch_reports_exactly_one_terminal_status_and_is_bounded():
    pool = CancelPool()
    got, release = [], threading.Event()
    assert pool.dispatch(lambda: True, 2, 5, lambda s, e: got.append(s))
    assert wait_for(lambda: got == ["ok"])
    # the pool limit is enforced and a hung hook keeps its slot until it exits
    assert pool.dispatch(lambda: release.wait(10), 1, 0.05, lambda s, e: got.append(s))
    assert not pool.dispatch(lambda: True, 1, 5, lambda s, e: got.append("never")), "pool full -> rejected, nothing started"
    assert wait_for(lambda: got == ["ok", "timeout"])
    assert pool.snapshot()["active"] == 1 and pool.snapshot()["rejected"] == 1             # timed out but still running: still occupying its slot
    release.set()
    assert wait_for(lambda: pool.snapshot()["active"] == 0)
    assert got == ["ok", "timeout"]                                                          # still exactly one terminal status per dispatch


def test_f3_a_full_cancel_pool_is_reported_not_silently_skipped():
    assert wait_for(lambda: CANCELS.snapshot()["active"] == 0)
    hang, gate = threading.Event(), threading.Event()
    results = []
    prov = _Cancelable(lambda p, a, u, n: (gate.wait(10), ok_text(p, a, u, n))[1], lambda cid: (hang.wait(10), True)[1])
    try:
        for i in range(2):
            h, rt, llm, tr = rig(prov, strands_execution=False, max_model_call_seconds=0.2, max_cancel_workers=1, cancel_timeout_seconds=5.0)
            with pytest.raises(BudgetExceeded):
                llm.complete("report", "sys", f"user{i}", None)
            results.append(rt)
        assert "provider_cancel_started" in kinds(results[0])
        assert "provider_cancel_rejected" in kinds(results[1]) and "provider_cancel_started" not in kinds(results[1])
        to = next(i for i in results[1].telemetry.incidents if i["kind"] == "provider_timeout")
        assert to["cancel"] == "rejected"
    finally:
        hang.set()
        gate.set()
    assert wait_for(lambda: CANCELS.snapshot()["active"] == 0)


# ================================================================================================================== F4: atomic ghost lifecycle
def test_f4_a_ghost_that_finishes_inside_the_abandonment_window_still_gets_its_terminal_outcome():
    """BEFORE: ghost_started() ran after the abandoned flag was published; a ghost finishing in between stayed 'pending' forever."""
    gate, returned = threading.Event(), threading.Event()

    def slow(p, a, u, n):
        gate.wait(10)
        r = ok_text(p, a, u, n)
        returned.set()
        return r
    h, rt, llm, tr = rig(Fake(slow), strands_execution=False, max_model_call_seconds=0.3)
    orig = rt.telemetry.ghost_started

    def racing_ghost_started(*a, **k):
        gate.set()                                  # let the provider finish NOW, inside the abandonment critical section ...
        assert returned.wait(5)                     # ... and wait until it really has
        return orig(*a, **k)
    rt.telemetry.ghost_started = racing_ghost_started
    with pytest.raises(BudgetExceeded):
        llm.complete("report", "sys", "user", None)
    assert wait_for(lambda: rt.telemetry.summary()["ghost_pending"] == 0)
    s = rt.telemetry.summary()
    assert (s["ghost_calls"], s["ghost_pending"], s["ghost_late_results"]) == (1, 0, 1)
    assert rt.telemetry.ghosts[0]["state"] == "late_result" and rt.budget.ghost_calls == 1
    assert kinds(rt).count("ghost_call_late_result") == 1


def test_f4_a_ghost_has_exactly_one_terminal_outcome():
    from harness.telemetry import Telemetry
    t = Telemetry()
    t.ghost_started("c1", "report", 0)
    t.ghost_finished("c1", "late_result", input_tokens=1)
    t.ghost_finished("c1", "late_error", input_tokens=9)
    assert t.ghosts == [dict(call_id="c1", agent="report", attempt=0, state="late_result", input_tokens=1)]


def test_f4_every_abandoned_call_ends_with_one_ghost_record_under_a_concurrent_finish_storm():
    """Invariant stress: calls finish at, before and after their deadline.  Whatever the interleaving, every timed-out call has exactly one ghost, none stays pending."""
    gates = [threading.Event() for _ in range(24)]

    def behave(p, a, u, n):
        i = int(u[1:])
        gates[i].wait(0.25 + (i % 3) * 0.05)        # straddles the 0.3 s call deadline
        return ok_text(p, a, u, n)
    base = REGISTRY.snapshot()
    h, rt, llm, tr = rig(Fake(behave), strands_execution=False, max_model_call_seconds=0.3, max_llm_calls=100, max_abandoned_workers=base["abandoned_live"] + 100,
                         max_provider_workers=base["live"] + 100)
    timed_out = 0

    def one(i):
        nonlocal timed_out
        try:
            llm.complete("report", "sys", f"u{i}", None)
        except BudgetExceeded:
            timed_out += 1
        except Exception:
            pass
    ts = [threading.Thread(target=one, args=(i,)) for i in range(24)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wait_for(lambda: rt.telemetry.summary()["ghost_pending"] == 0 and REGISTRY.snapshot()["abandoned_live"] == base["abandoned_live"])
    s = rt.telemetry.summary()
    assert s["ghost_calls"] == rt.budget.ghost_calls == s["ghost_late_results"] == len(rt.telemetry.ghosts)
    assert len({g["call_id"] for g in rt.telemetry.ghosts}) == len(rt.telemetry.ghosts)
    assert s["ghost_calls"] == kinds(rt).count("ghost_call_late_result") == kinds(rt).count("provider_timeout")


# ================================================================================================================== F5: abandonment vs. budget charging
def test_f5_a_worker_that_loses_the_race_can_neither_charge_nor_contact_the_provider():
    b = RequestBudget(HarnessConfig.for_test())
    g = ModelCallGuard(b)
    st = CallState("report", "s", "u", None, 0, 10)
    with st.lock:
        st.abandoned = True
    with pytest.raises(LLMError):
        g.before(st)
    prov = Fake(ok_text)
    with pytest.raises(LLMError):
        invoke_inner(prov, st)
    assert prov.calls == [] and (b.llm_calls, b.llm_calls_by_agent, b.retries) == (0, {}, 0) and not st.charged and not st.provider_started


def test_f5_the_abandonment_transition_waits_for_an_in_flight_charge_so_the_summary_cannot_disagree_with_later_telemetry():
    """BEFORE: the worker could charge llm_calls AFTER the request had failed and its summary was taken."""
    prov = Fake(ok_text)
    h, rt, llm, tr = rig(prov, strands_execution=False, max_model_call_seconds=0.3)
    entered, release = threading.Event(), threading.Event()
    orig = rt.budget.begin_llm_call

    def slow_begin(*a, **k):                         # the worker is inside the charge when the deadline passes
        entered.set()
        release.wait(10)
        return orig(*a, **k)
    rt.budget.begin_llm_call = slow_begin
    box = {}

    def request():
        try:
            llm.complete("report", "sys", "user", None)
        except BudgetExceeded as e:
            box["reason"] = e.reason
    t = threading.Thread(target=request)
    t.start()
    assert entered.wait(5)
    time.sleep(0.6)                                 # the 0.3 s deadline has passed: the abandonment is now waiting for the in-flight charge
    assert t.is_alive(), "abandonment must not complete while a charge is in flight (it would let the charge land after the request failed)"
    release.set()
    t.join(10)
    assert box["reason"] == "model_call_timeout"
    summary = rt.finish()
    snap_at_finish = dict(llm_calls=rt.budget.llm_calls, by_agent=dict(rt.budget.llm_calls_by_agent), inp=rt.budget.input_tokens, out=rt.budget.output_tokens)
    assert snap_at_finish["llm_calls"] == 1 and summary["budget"]["llm_calls"] == 1
    assert wait_for(lambda: rt.telemetry.summary()["ghost_pending"] == 0)
    assert dict(llm_calls=rt.budget.llm_calls, by_agent=dict(rt.budget.llm_calls_by_agent), inp=rt.budget.input_tokens, out=rt.budget.output_tokens) == snap_at_finish
    assert summary["budget"]["llm_calls_by_agent"] == snap_at_finish["by_agent"]


def test_f5_charge_and_abandonment_are_atomic_under_a_barrier_race():
    """200 races between a worker's charge and the abandonment transition.  Invariant: what the abandoner saw (under the lock) is exactly what the budget ends with."""
    for _ in range(200):
        b = RequestBudget(HarnessConfig.for_test())
        g = ModelCallGuard(b)
        st = CallState("report", "s", "u", None, 0, 10)
        barrier = threading.Barrier(2)
        seen = {}

        def worker():
            barrier.wait()
            try:
                g.before(st)
            except LLMError:
                pass

        def abandoner():
            barrier.wait()
            with st.lock:
                st.abandoned = True
                seen["calls"] = b.llm_calls
        ts = [threading.Thread(target=worker), threading.Thread(target=abandoner)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert b.llm_calls == seen["calls"] == (1 if st.charged else 0)


# ================================================================================================================== F6: permission-denied outcome
def test_f6_a_permission_denied_request_is_not_recorded_as_completed(monkeypatch):
    import harness.runtime as rtm
    import orchestration.pipeline as pl
    from llm.client import MockLLM
    from orchestration.tracing import Tracer
    from test_harness_pipeline import harness, kenji_memory
    made, summaries = [], []

    class Spy(Tracer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)
    monkeypatch.setattr(pl, "Tracer", Spy)
    orig = rtm.RequestRuntime.finish
    monkeypatch.setattr(rtm.RequestRuntime, "finish", lambda self, error=None: summaries.append(orig(self, error)) or summaries[-1])
    llm = MockLLM(handlers={})
    from harness import PermissionDenied
    with pytest.raises(PermissionDenied):
        pl.run_pipeline("Is the plan suitable for Kenji?", llm, kenji_memory(), harness=harness(), auth=AuthContext.for_test("u-1", "viewer"), persist_trace=False)
    assert len(summaries) == 1 and summaries[0]["outcome"] == "denied" and "PermissionDenied" in summaries[0]["error"]
    fin = [e for e in made[0].events if e["component"] == "harness" and e["event"] == "request_finished"]
    assert len(fin) == 1 and fin[0]["payload"]["outcome"] == "denied"
    assert [e["event"] for e in made[0].events if e["component"] == "harness.reliability"] == ["request_denied"]
    assert llm.calls == [] or len(llm.calls) == 0                    # authorization behaviour is unchanged: no model call was spent


# ================================================================================================================== F7: runtime state in the report digest
def _report(**kw):
    return AdvisorReport(request_id="R", client_id="C", question="q", summary="same summary", **kw)


def test_f7_a_degraded_report_has_a_different_digest_and_fingerprint():
    complete = _report()
    complete.validation["harness_runtime"] = dict(complete=True, outcome="completed", degraded_reasons=[])
    degraded = _report(limitations=["This answer was produced in degraded mode (provider_timeout); treat it as incomplete."])
    degraded.validation["harness_runtime"] = dict(complete=False, outcome="degraded", degraded_reasons=["provider_timeout"])
    assert _report_digest(complete) != _report_digest(degraded)
    from harness.approval import RiskCategory
    fp = lambda r: _report_proposal(r, "advisor-1", RiskCategory.SUITABILITY_CONCLUSION, "why", "dv1", ("C",), "q").fingerprint()      # noqa: E731
    assert fp(complete) != fp(degraded) and fp(complete) == fp(complete)
    # each part is bound on its own
    only_state = _report(); only_state.validation["harness_runtime"] = degraded.validation["harness_runtime"]
    only_note = _report(limitations=degraded.limitations)
    assert len({_report_digest(complete), _report_digest(only_state), _report_digest(only_note)}) == 3
    assert _report_digest(_report(limitations=["a reviewer-irrelevant note"])) == _report_digest(_report())      # unrelated limitations are not (yet) bound


def test_f7_h1_omissions_that_this_change_does_not_cover_are_still_unbound():
    """TRACKING, not a feature: P1.2 finding H1 is NOT fixed.  These fields are still outside the digest; when H1 is fixed this test must be updated."""
    base = _report()
    for field in ("uncertainties", "assumptions", "suggested_followups"):
        assert _report_digest(_report(**{field: ["changed"]})) == _report_digest(base), field
    changed = _report(); changed.validation["claim_verification"] = {"unsupported": 3}
    assert _report_digest(changed) == _report_digest(base)


# ================================================================================================================== F8: process-wide worker cap
def test_f8_registry_capacity_is_held_until_the_last_thread_of_the_call_has_exited():
    r = WorkerRegistry()
    a = r.admit(2, 1)
    a.hold()                                                  # the provider thread
    r.mark_abandoned(a)
    a.drop()                                                   # the request worker returned
    assert r.snapshot()["live"] == 1 and r.snapshot()["abandoned_live"] == 1, "capacity must NOT be released while the provider thread still runs"
    with pytest.raises(CapacityExhausted) as e:
        r.admit(2, 1)
    assert e.value.reason == "abandoned_worker_capacity"
    a.drop()                                                   # the provider thread exited
    assert r.snapshot()["live"] == 0 and r.snapshot()["abandoned_live"] == 0 and r.snapshot()["exited_after_abandon"] == 1
    a.drop()                                                   # a stray extra drop can never push the counters negative
    assert r.snapshot()["live"] == 0
    b, c = r.admit(2, 5), r.admit(2, 5)
    with pytest.raises(CapacityExhausted) as e:
        r.admit(2, 5)
    assert e.value.reason == "provider_worker_capacity" and r.snapshot()["rejected_total"] == 2
    b.drop(); c.drop()


@pytest.mark.parametrize("transport", ["mixed", "strands", "direct"])
def test_f8_the_cap_is_process_wide_across_requests_and_transports(transport):
    assert wait_for(lambda: REGISTRY.snapshot()["live"] == 0, 8), "earlier tests left provider workers running: " + str(REGISTRY.snapshot())
    base = REGISTRY.snapshot()
    gate = threading.Event()
    prov = Fake(lambda p, a, u, n: (gate.wait(30), ok_text(p, a, u, n))[1])
    cap = 3
    rigs = []
    for i in range(8):                                                       # 8 DIFFERENT requests (own Harness, budget, telemetry) sharing one process
        st = {"mixed": i % 2 == 0, "strands": True, "direct": False}[transport]
        rigs.append(rig(prov, strands_execution=st, max_model_call_seconds=0.4, max_provider_workers=base["live"] + cap, max_abandoned_workers=cap + 10))
    barrier, outcomes = threading.Barrier(len(rigs)), {}

    def go(i):
        barrier.wait()
        try:
            rigs[i][2].complete("report", "sys", f"user{i}", None)
            outcomes[i] = "ok"
        except BudgetExceeded as e:
            outcomes[i] = e.reason
    try:
        ts = [threading.Thread(target=go, args=(i,)) for i in range(len(rigs))]
        [t.start() for t in ts]
        [t.join(15) for t in ts]
        reasons = sorted(outcomes.values())
        assert reasons.count("model_call_timeout") == cap and reasons.count("provider_worker_capacity") == len(rigs) - cap, reasons
        assert len(prov.calls) == cap, "refused requests must never contact the provider"
        snap = REGISTRY.snapshot()
        # the requests have all returned, but the provider work has not exited: capacity is STILL occupied
        assert snap["live"] == base["live"] + cap and snap["abandoned_live"] == base["abandoned_live"] + cap
        assert snap["rejected_total"] - base["rejected_total"] == len(rigs) - cap
        refused = [r for r in rigs if any(i["kind"] == "worker_capacity_exhausted" for i in r[1].telemetry.incidents)]
        assert len(refused) == len(rigs) - cap
        rt = refused[0][1]
        assert rt.budget.llm_calls == 0 and rt.budget.input_tokens == 0                      # refused before any charge
        inc = next(i for i in rt.telemetry.incidents if i["kind"] == "worker_capacity_exhausted")
        assert inc["limit"] == "provider_worker_capacity" and inc["workers"]["live"] == base["live"] + cap and inc["call_id"].startswith("REQ-P13-c")
        assert rt._runtime_state()["outcome"] == "degraded" and "worker_capacity_exhausted" in rt._runtime_state()["degraded_reasons"]
        assert rt.finish()["workers"]["provider"]["abandoned_live"] == base["abandoned_live"] + cap
        # a later request in the same process is refused by the ABANDONED cap even though total capacity is fine
        h, rt2, llm2, tr2 = rig(prov, strands_execution=False, max_model_call_seconds=0.4, max_provider_workers=base["live"] + 100, max_abandoned_workers=base["abandoned_live"] + cap)
        with pytest.raises(BudgetExceeded) as e:
            llm2.complete("report", "sys", "later", None)
        assert e.value.reason == "abandoned_worker_capacity" and len(prov.calls) == cap
    finally:
        gate.set()
    assert wait_for(lambda: REGISTRY.snapshot()["live"] == base["live"] and REGISTRY.snapshot()["abandoned_live"] == base["abandoned_live"], 10)   # released only when the work exited
    h, rt3, llm3, tr3 = rig(Fake(ok_text), strands_execution=False, max_provider_workers=base["live"] + cap, max_abandoned_workers=base["abandoned_live"] + cap)
    assert llm3.complete("report", "sys", "after", None) == '{"ok": true}'                   # capacity is available again


def test_f8_normal_calls_return_their_slot_and_cancel_workers_are_bounded_independently():
    assert wait_for(lambda: REGISTRY.snapshot()["live"] == 0, 8)
    h, rt, llm, tr = rig(Fake(ok_text), strands_execution=True)
    for i in range(5):
        llm.complete("report", "sys", f"u{i}", None)
    assert wait_for(lambda: REGISTRY.snapshot()["live"] == 0) and REGISTRY.snapshot()["abandoned_live"] == 0
    cfg = HarnessConfig.for_test(max_provider_workers=1, max_abandoned_workers=1, max_cancel_workers=1)
    assert (cfg.max_provider_workers, cfg.max_cancel_workers) == (1, 1)
    from harness.config import ConfigError
    for bad in (dict(max_provider_workers=0), dict(max_abandoned_workers=0), dict(max_cancel_workers=0), dict(cancel_timeout_seconds=0)):
        with pytest.raises(ConfigError):
            HarnessConfig.for_test(**bad).validate()
    env = HarnessConfig.from_env({"HARNESS_MAX_PROVIDER_WORKERS": "5", "HARNESS_MAX_ABANDONED_WORKERS": "2", "HARNESS_MAX_CANCEL_WORKERS": "3", "HARNESS_CANCEL_TIMEOUT_SECONDS": "1.5"})
    assert (env.max_provider_workers, env.max_abandoned_workers, env.max_cancel_workers, env.cancel_timeout_seconds) == (5, 2, 3, 1.5)


def test_f8_incident_vocabulary_for_p131_is_classified():
    assert INCIDENT_KINDS["worker_capacity_exhausted"] is True
    assert all(INCIDENT_KINDS[k] is False for k in ("provider_cancel_started", "provider_cancel_timeout", "provider_cancel_rejected"))
