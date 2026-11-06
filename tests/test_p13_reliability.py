"""P1.3 - runtime reliability and execution safety.  Deterministic fake providers only: no network, no live LLM, no sleeping longer than a second.

Covers: Strands hook instrumentation (missing / raising / incomplete), provider timeouts derived from the request budget, abandoned calls and late (ghost) results,
cancellation protocol, provider context-overflow recovery (bounded, budget-aware), partial replies, structured telemetry, request outcomes and report finalization.
NOTE: no `from __future__ import annotations` - Strands' @hook infers the event type from the annotation.
"""
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import BaseModel
from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent
from strands.plugins import Plugin, hook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import harness.strands_adapter as sa  # noqa: E402
from harness import AuthContext, BudgetExceeded, ConfigError, ContextOverflow, Harness, HarnessConfig  # noqa: E402
from harness.budget import RequestBudget  # noqa: E402
from harness.hooks import CmfBudgetPlugin  # noqa: E402
from harness.reliability import INCIDENT_KINDS, InstrumentationError, safe_error  # noqa: E402
from harness.strands_adapter import CallState, ModelCallGuard, StrandsExecutor  # noqa: E402
from harness.token_counter import TokenCounter  # noqa: E402
from llm.client import (CALL_ID, CALL_TIMEOUT, ContextLengthError, LLMError, LLMTimeoutError, LLMUsage, MockLLM, OllamaLLM, OpenAILLM,  # noqa: E402
                        PartialResponseError, call_timeout, generate_structured)
from orchestration.pipeline import run_pipeline  # noqa: E402
from orchestration.tracing import Tracer  # noqa: E402
from test_harness_core import _prompt  # noqa: E402
from test_harness_pipeline import ADMIN, KENJI_ADVISOR, harness, kenji_memory, planner_out, report_out  # noqa: E402


class Out(BaseModel):
    ok: bool


class Fake:
    """A scripted provider.  behave(provider, agent, user, n_call) -> text.  It records what the harness handed it (prompt, native timeout, call id)."""
    model = "fake-model"
    native_timeout = False

    def __init__(self, behave):
        self.behave, self.calls, self.timeouts, self.ids, self._u = behave, [], [], [], None

    def complete(self, agent, system, user, schema=None):
        self.calls.append(user)
        self.timeouts.append(CALL_TIMEOUT.get())
        self.ids.append(CALL_ID.get())
        return self.behave(self, agent, user, len(self.calls))

    def pop_usage(self):
        u, self._u = self._u, None
        return u


def ok_text(p, agent, user, n):
    p._u = LLMUsage(30, 6, 0, True)
    return '{"ok": true}'


def rig(provider, **cfg):
    h = Harness(HarnessConfig.for_test(**cfg))
    tr = Tracer("REQ-P13", persist=False)
    rt = h.start("REQ-P13", tr, AuthContext.for_test("advisor-1", "admin"), None)
    rt.client_ids = ("CLIENT-001",)
    return h, rt, rt.wrap_llm(provider), tr


def wait_for(pred, secs=6.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def events(tr, component=None, name=None):
    return [e for e in tr.events if (component is None or e["component"] == component) and (name is None or e["event"] == name)]


def kinds(rt):
    return [i["kind"] for i in rt.telemetry.incidents]


BOTH = pytest.mark.parametrize("strands", [True, False], ids=["strands", "direct"])


# ================================================================================================================== scope 1: hooks and instrumentation
def _executor(provider=None):
    inner = provider or MockLLM(handlers={"report": lambda s, u, sc: '{"ok": true}'})
    b = RequestBudget(HarnessConfig.for_test())
    return inner, b, StrandsExecutor(inner, ModelCallGuard(b), b, TokenCounter("mock"))


def _state():
    return CallState("report", "sys", "usr", None, 0, 10)


def test_a_missing_budget_hook_refuses_the_provider_call(monkeypatch):
    """BEFORE: with no hooks registered the call ran UNCHARGED and returned text (llm_calls == 0)."""
    class NoHooks(Plugin):
        name = "no-hooks"
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: NoHooks())
    inner, b, ex = _executor()
    st = _state()
    with pytest.raises(InstrumentationError) as e:
        ex.run(st)
    assert e.value.kind == "hook_missing" and inner.calls == [] and st.text is None and b.llm_calls == 0


def test_a_missing_completion_hook_is_not_a_normal_success(monkeypatch):
    class OnlyBefore(Plugin):
        name = "only-before"

        def __init__(self, guard, state):
            super().__init__()
            self.guard, self.state = guard, state

        @hook
        def enforce(self, event: BeforeModelCallEvent):
            self.state.before_called = True
            self.guard.before(self.state)
            self.state.before_ok = True
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: OnlyBefore(guard, state))
    inner, b, ex = _executor()
    with pytest.raises(InstrumentationError) as e:
        ex.run(_state())
    assert e.value.kind == "hook_missing" and "AfterModelCall" in e.value.detail
    assert len(inner.calls) == 1 and b.llm_calls == 1                                   # the work happened and stays charged; only the TEXT is refused


def test_a_raising_budget_hook_fails_closed_with_a_structured_error():
    inner, b, ex = _executor()
    ex.guard.before = lambda state: (_ for _ in ()).throw(RuntimeError("boom inside the hook"))
    with pytest.raises(InstrumentationError) as e:
        ex.run(_state())
    assert e.value.kind == "hook_failed" and "RuntimeError" in e.value.detail
    assert inner.calls == [] and b.llm_calls == 0                                       # not contacted, not charged


def test_a_raising_completion_hook_discards_the_text(monkeypatch):
    class Boom:
        @property
        def stop_response(self):
            raise RuntimeError("bad event")

    class AfterExplodes(CmfBudgetPlugin):
        @hook
        def record_completion(self, event: AfterModelCallEvent):
            CmfBudgetPlugin.record_completion(self, Boom())
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: AfterExplodes(guard, state))
    inner, b, ex = _executor()
    with pytest.raises(InstrumentationError) as e:
        ex.run(_state())
    assert e.value.kind == "hook_failed"


def test_instrumentation_failure_is_recorded_correlated_and_degrades_the_request(monkeypatch):
    class NoHooks(Plugin):
        name = "no-hooks"
    monkeypatch.setattr(sa, "CmfBudgetPlugin", lambda guard, state: NoHooks())
    prov = Fake(ok_text)
    h, rt, llm, tr = rig(prov, strands_execution=True)
    with pytest.raises(InstrumentationError):
        llm.complete("report", "sys", "user", None)
    rec = rt.telemetry.records[-1]
    assert prov.calls == [] and rt.budget.llm_calls == 0                                # nothing ran, nothing charged
    assert rec.outcome == "error" and "hook_missing" in rec.error and rec.call_id.startswith("REQ-P13-c")
    ev = events(tr, "harness.reliability", "hook_missing")
    assert len(ev) == 1 and ev[0]["payload"]["call_id"] == rec.call_id and ev[0]["payload"]["agent"] == "report" and ev[0]["request_id"] == "REQ-P13"
    assert rt._runtime_state()["degraded_reasons"] == ["hook_missing"] and rt._runtime_state()["complete"] is False


def test_normal_instrumented_call_is_unchanged():
    for strands in (True, False):
        prov = Fake(ok_text)
        h, rt, llm, tr = rig(prov, strands_execution=strands)
        assert llm.complete("report", "sys", "user", None) == '{"ok": true}'
        rec = rt.telemetry.records[-1]
        assert rec.outcome == "ok" and rec.exact and rt.budget.llm_calls == 1 and rt.budget.input_tokens == 30 and rt.budget.output_tokens == 6
        assert rt.telemetry.incidents == [] and rt._runtime_state() == dict(complete=True, outcome="completed", degraded_reasons=[])


# ================================================================================================================== scope 2: provider timeouts
@BOTH
def test_the_provider_receives_a_timeout_derived_from_the_remaining_request_budget(strands):
    prov = Fake(ok_text)
    h, rt, llm, tr = rig(prov, strands_execution=strands, max_runtime_seconds=5.0, max_model_call_seconds=180.0)
    llm.complete("report", "sys", "user", None)
    assert 0 < prov.timeouts[0] <= 5.0 and prov.ids[0] == rt.telemetry.records[-1].call_id          # not the provider's own 900 s, and the call id reached it
    assert call_timeout(900.0) == 900.0                                                           # outside the harness nothing changes
    tok = CALL_TIMEOUT.set(2.5)
    try:
        assert call_timeout(900.0) == 2.5 and call_timeout(1.0) == 1.0
    finally:
        CALL_TIMEOUT.reset(tok)


def test_a_provider_that_enforces_its_timeout_fails_first_and_nothing_keeps_running():
    def behave(p, agent, user, n):
        time.sleep(call_timeout(900.0))                       # a native socket timeout: it fires at the derived timeout
        raise LLMTimeoutError("socket timed out")
    prov = Fake(behave)
    prov.native_timeout = True
    h, rt, llm, tr = rig(prov, strands_execution=False, max_model_call_seconds=1.0)
    with pytest.raises(BudgetExceeded) as e:
        llm.complete("report", "sys", "user", None)
    assert e.value.reason == "model_call_timeout"
    inc = next(i for i in rt.telemetry.incidents if i["kind"] == "provider_timeout")
    assert inc["enforcement"] == "provider_native" and inc["provider_native_timeout"] is True and inc["provider_timeout_s"] < inc["timeout_s"]
    assert rt.telemetry.ghosts == [] and rt.telemetry.records[-1].outcome == "timeout"
    assert wait_for(lambda: not [t for t in threading.enumerate() if t.name.startswith("cmf-llm-") and t.is_alive()])
    assert rt.budget.llm_calls == 1 and rt.budget.input_tokens > 0                              # the call stays counted and its prompt stays charged: no refund


@BOTH
def test_an_unresponsive_provider_is_abandoned_and_its_late_result_cannot_change_the_request(strands):
    release = threading.Event()

    def behave(p, agent, user, n):
        release.wait(10)
        p._u = LLMUsage(40, 9, 0, True)
        return '{"ok": true}'
    prov = Fake(behave)
    h, rt, llm, tr = rig(prov, strands_execution=strands, max_model_call_seconds=0.6)
    with pytest.raises(BudgetExceeded) as e:
        llm.complete("report", "sys", "user", {"type": "object"})
    assert e.value.reason == "model_call_timeout"
    rec = rt.telemetry.records[-1]
    assert rec.outcome == "timeout" and rec.input_tokens > 0                                       # recorded as a TIMEOUT, not as 'denied', with the estimated input charged
    snap, n_rec, n_cache = rt.budget.snapshot(), len(rt.telemetry.records), len(h.cache)
    assert snap["llm_calls"] == 1 and snap["input_tokens"] > 0 and snap["output_tokens"] == 0
    assert rt.telemetry.summary()["ghost_pending"] == 1
    workers = [t for t in threading.enumerate() if t.name.startswith("cmf-llm-")]
    assert workers and all(t.daemon for t in workers)                                              # an abandoned call can never hold the interpreter at exit
    release.set()                                                                                  # the provider finally answers - far too late
    assert wait_for(lambda: rt.telemetry.summary()["ghost_pending"] == 0)
    after = rt.budget.snapshot()
    for k in ("llm_calls", "retries", "input_tokens", "output_tokens", "total_tokens", "exhausted"):
        assert after[k] == snap[k], k                                                              # the late completion changed NOTHING of the request's own accounting
    assert (after["ghost_calls"], after["ghost_output_tokens"]) == (1, 9)                          # ...and was recorded separately
    assert len(rt.telemetry.records) == n_rec and len(h.cache) == n_cache == 0 and rt.telemetry.records[-1].outcome == "timeout"
    ks = kinds(rt)
    assert {"provider_timeout", "provider_cancel_unsupported", "ghost_call_late_result"} <= set(ks)
    late = next(i for i in rt.telemetry.incidents if i["kind"] == "ghost_call_late_result")
    assert late["discarded"] is True and late["completed"] is True and late["call_id"] == rec.call_id
    to = next(i for i in rt.telemetry.incidents if i["kind"] == "provider_timeout")
    assert to["enforcement"] == "worker_abandoned" and to["cancel"] == "unsupported" and to["provider_native_timeout"] is False
    assert not [e for e in events(tr, "harness.llm", "call") if e["payload"]["outcome"] == "ok"]  # no 'ok' call event was ever produced for it
    assert rt._runtime_state()["complete"] is False and "provider_timeout" in rt._runtime_state()["degraded_reasons"]


@pytest.mark.parametrize("mode,expect", [("ok", "provider_cancel_ok"), ("declined", "provider_cancel_failed"), ("raises", "provider_cancel_failed")])
def test_cancellation_protocol_reports_success_and_failure_honestly(mode, expect):
    release = threading.Event()

    class Cancelable(Fake):
        def cancel_call(self, call_id):
            self.cancelled = call_id
            if mode == "raises":
                raise OSError("socket already closed")
            if mode == "ok":
                release.set()
            return mode == "ok"
    prov = Cancelable(lambda p, a, u, n: (release.wait(10), '{"ok": true}')[1])
    h, rt, llm, tr = rig(prov, strands_execution=False, max_model_call_seconds=0.4)
    with pytest.raises(BudgetExceeded):
        llm.complete("report", "sys", "user", None)
    release.set()
    assert wait_for(lambda: expect in kinds(rt))                    # P1.3.1: cancellation is asynchronous; the terminal status arrives from the cancel thread
    assert "provider_cancel_unsupported" not in kinds(rt) and "provider_cancel_started" in kinds(rt)
    assert prov.cancelled == prov.ids[0] == rt.telemetry.records[-1].call_id                       # the provider was handed the same correlation id
    to = next(i for i in rt.telemetry.incidents if i["kind"] == "provider_timeout")
    assert to["cancel"] == "dispatched"                              # the request only knows it was DISPATCHED, never that the provider work stopped
    assert wait_for(lambda: rt.telemetry.summary()["ghost_pending"] == 0)


def test_an_abandoned_call_that_only_now_starts_cannot_charge_the_budget():
    b = RequestBudget(HarnessConfig.for_test())
    st = _state()
    st.abandoned = True
    with pytest.raises(LLMError):
        ModelCallGuard(b).before(st)
    assert b.llm_calls == 0
    inner, _, ex = _executor()
    ex.budget = b
    with pytest.raises(LLMError):
        sa.invoke_inner(inner, st)
    assert inner.calls == []


def test_a_timeout_in_the_report_agent_yields_a_labelled_degraded_report():
    release = threading.Event()

    class Routed(MockLLM):
        def complete(self, agent, system, user, schema=None):
            if agent == "report":
                release.wait(10)
                return json.dumps(report_out())
            return super().complete(agent, system, user, schema)
    llm = Routed(handlers={"planner": [planner_out()]})
    h = harness(max_model_call_seconds=0.5)
    res = run_pipeline("Why did Kenji's retirement age change?", llm, kenji_memory(), persist_trace=False, harness=h, auth=ADMIN)
    release.set()
    rep = res.report
    assert rep.validation["mode"] == "deterministic_model_failure"
    rs = rep.validation["harness_runtime"]
    assert rs["complete"] is False and {"provider_timeout", "model_call_timeout", "report_agent_fallback"} <= set(rs["degraded_reasons"])
    assert any("degraded mode" in x for x in rep.limitations) and any("budget exhausted" in x.lower() for x in rep.limitations)
    assert res.harness["outcome"] == "degraded" and res.harness["telemetry"]["timeouts"] == 1
    fin = [e for e in res.trace if e["component"] == "harness" and e["event"] == "request_finished"][0]["payload"]
    assert fin["outcome"] == "degraded"                                                            # the trace says degraded, never 'completed normally'
    assert list(res.harness["telemetry"]["by_agent"]) == ["planner", "report"]                      # agent order and responsibilities unchanged


# ================================================================================================================== scope 3: context overflow and partial results
def _overflowing(limit_chars):
    def behave(p, agent, user, n):
        if len(user) > limit_chars:
            raise ContextLengthError("maximum context length exceeded")
        p._u = LLMUsage(100, 8, 0, True)
        return '{"ok": true}'
    return behave


@BOTH
def test_overflow_on_the_first_attempt_is_recovered_by_bounded_compression(strands):
    text, head, tail = _prompt(30)
    prov = Fake(_overflowing(int(len(text) * 0.8)))
    h, rt, llm, tr = rig(prov, strands_execution=strands)
    assert llm.complete("report", "sys", text, None) == '{"ok": true}'
    assert len(prov.calls) == 2 and len(prov.calls[1]) < len(prov.calls[0])
    for ln in head + tail:
        assert ln in prov.calls[1].split("\n")                                                      # protected lines survive the recovery compression
    assert [r.outcome for r in rt.telemetry.records] == ["context_overflow", "ok"] and rt.telemetry.records[-1].compressed
    assert kinds(rt) == ["context_overflow", "context_retry"]
    retry = rt.telemetry.incidents[1]
    assert retry["from_tokens"] > retry["to_tokens"] and retry["recovery"] == 1
    assert rt.budget.llm_calls == 2 and rt.budget.retries == 1                                      # the retry is charged like any other retry
    assert rt._runtime_state()["complete"] is True and rt.budget.exhausted == []


def test_the_recovered_response_is_cached_for_the_prompt_that_was_actually_sent():
    text, _, _ = _prompt(30)
    prov = Fake(_overflowing(int(len(text) * 0.8)))
    h, rt, llm, tr = rig(prov, strands_execution=False)
    out = generate_structured(llm, "report", "sys", text, Out, retries=0)
    assert out.ok and len(h.cache) == 1
    again = generate_structured(llm, "report", "sys", text, Out, retries=0)                        # the original (long) prompt: still overflows, so it must NOT be a cache hit
    assert again.ok and len(prov.calls) == 4 and len(h.cache) == 1                                  # 2 calls each time (overflow, then the compressed prompt); one cache entry


@BOTH
def test_overflow_after_the_retry_is_a_clear_bounded_failure(strands):
    text, _, _ = _prompt(30)
    prov = Fake(lambda p, a, u, n: (_ for _ in ()).throw(ContextLengthError("context_length_exceeded")))
    h, rt, llm, tr = rig(prov, strands_execution=strands)
    with pytest.raises(ContextOverflow) as e:
        llm.complete("report", "sys", text, None)
    assert e.value.reason == "context_overflow"
    assert len(prov.calls) == 1 + h.config.max_context_recoveries == 2                              # bounded: first attempt + exactly one recovery
    assert rt.budget.exhausted[0]["reason"] == "context_overflow"                                   # visible in the budget summary, so the report is flagged partial
    assert [r.outcome for r in rt.telemetry.records] == ["context_overflow", "context_overflow"]
    assert rt._runtime_state() == dict(complete=False, outcome="degraded", degraded_reasons=["context_overflow"])


def test_the_recovery_limit_is_configurable_and_never_unbounded():
    text, _, _ = _prompt(30)
    # recoveries are retries: they also spend max_retries_per_operation (2 by default), so even a configured limit of 3 stops after 3 provider calls
    for limit, max_calls in ((0, 1), (3, 3)):
        prov = Fake(lambda p, a, u, n: (_ for _ in ()).throw(ContextLengthError("context_length_exceeded")))
        h, rt, llm, tr = rig(prov, strands_execution=False, max_context_recoveries=limit)
        with pytest.raises(BudgetExceeded):                    # ContextOverflow (recovery limit) or the per-operation retry budget, whichever bites first
            llm.complete("report", "sys", text, None)
        assert 1 <= len(prov.calls) <= max_calls and rt.budget.retries <= h.config.max_retries_per_operation
    with pytest.raises(ConfigError):
        HarnessConfig.from_env({"HARNESS_MAX_CONTEXT_RECOVERIES": "4"})
    assert HarnessConfig.from_env({"HARNESS_MAX_CONTEXT_RECOVERIES": "0"}).max_context_recoveries == 0


def test_content_that_cannot_be_shrunk_is_not_retried():
    prov = Fake(lambda p, a, u, n: (_ for _ in ()).throw(ContextLengthError("context_length_exceeded")))
    h, rt, llm, tr = rig(prov, strands_execution=False)
    with pytest.raises(ContextOverflow):
        llm.complete("report", "a fairly long system prompt " * 20, "REQUEST: protected one-liner", None)
    assert len(prov.calls) == 1 and "context_retry" not in kinds(rt)


def test_budget_exhaustion_during_recovery_stops_it():
    text, _, _ = _prompt(30)
    for cfg, reason in ((dict(max_llm_calls=1), "max_llm_calls"), (dict(max_retries=0), "max_retries")):
        prov = Fake(_overflowing(int(len(text) * 0.8)))
        h, rt, llm, tr = rig(prov, strands_execution=False, **cfg)
        with pytest.raises(BudgetExceeded) as e:
            llm.complete("report", "sys", text, None)
        assert e.value.reason == reason and len(prov.calls) == 1                                    # the retry was refused BEFORE reaching the provider
        assert rt.telemetry.records[-1].outcome == "denied" and rt._runtime_state()["complete"] is False


def test_deadline_expiring_during_recovery_stops_it():
    text, _, _ = _prompt(30)
    holder = {}

    def behave(p, agent, user, n):
        holder["rt"].budget.started -= 10_000                                                       # the request deadline passes while the provider is rejecting the prompt
        raise ContextLengthError("context_length_exceeded")
    prov = Fake(behave)
    h, rt, llm, tr = rig(prov, strands_execution=False)
    holder["rt"] = rt
    with pytest.raises(BudgetExceeded) as e:
        llm.complete("report", "sys", text, None)
    assert e.value.reason == "runtime_deadline" and len(prov.calls) == 1


@BOTH
def test_a_truncated_reply_is_never_an_answer_even_when_it_parses(strands):
    def behave(p, agent, user, n):
        p._u = LLMUsage(10, 5, 0, True, "length")
        return '{"ok": true}'                                  # syntactically valid, but the provider says it stopped on its output limit
    prov = Fake(behave)
    h, rt, llm, tr = rig(prov, strands_execution=strands)
    with pytest.raises(PartialResponseError):
        generate_structured(llm, "report", "sys", "usr", Out, retries=2)
    assert len(prov.calls) == 1 and len(h.cache) == 0                                               # not retried blindly, never cached
    rec = rt.telemetry.records[-1]
    assert rec.outcome == "partial" and rt.budget.output_tokens == 5                                # the tokens were spent; they are not refunded
    assert "partial_response" in kinds(rt) and rt._runtime_state()["degraded_reasons"] == ["partial_response"]


def test_partial_output_followed_by_failure_returns_nothing_and_caches_nothing():
    def behave(p, agent, user, n):
        p.partial = '{"ok": tr'
        raise LLMError("connection reset after 9 bytes")
    prov = Fake(behave)
    h, rt, llm, tr = rig(prov, strands_execution=False)
    with pytest.raises(LLMError):
        llm.complete("report", "sys", "usr", None)
    assert prov.partial and len(h.cache) == 0 and rt.telemetry.records[-1].outcome == "error"
    assert getattr(llm._tl, "pending", None) is None


def test_incomplete_report_runs_are_labelled_and_the_label_travels_into_the_approval_draft():
    """A model failure in the report stage must not look like a normal successful report - including the draft a supervisor will be asked to approve."""
    class Routed(MockLLM):
        def complete(self, agent, system, user, schema=None):
            if agent == "report":
                raise ContextLengthError("maximum context length exceeded")
            return super().complete(agent, system, user, schema)
    llm = Routed(handlers={"planner": [planner_out(task="forecast_feasibility", topics=("retirement_age",))]})
    h = harness()
    res = run_pipeline("Should Kenji retire at 60?", llm, kenji_memory(), persist_trace=False, harness=h, auth=KENJI_ADVISOR)
    assert res.released is False and res.harness["outcome"] == "degraded"                           # still goes through approval, unchanged
    assert "context_overflow" in res.harness["degraded_reasons"] and "report_agent_fallback" in res.harness["degraded_reasons"]
    draft = h.approvals.store.get(res.harness["approval"]["approval_id"]).draft
    assert draft.validation["harness_runtime"]["complete"] is False and draft.validation["mode"] == "deterministic_model_failure"
    assert any("degraded mode" in x for x in draft.limitations)


# ================================================================================================================== scope 4: telemetry
def test_telemetry_is_correlated_and_free_of_secrets():
    secret = "HTTP 500 Bearer abc.DEF-123 api_key=SUPERSECRET sk-ABCDEFGH12345678 eyJabcdefgh12.payload123.sig456"
    prov = Fake(lambda p, a, u, n: (_ for _ in ()).throw(LLMError(secret)))
    h, rt, llm, tr = rig(prov, strands_execution=False)
    with pytest.raises(LLMError):
        llm.complete("planner", "sys", "usr", None)
    rec = rt.telemetry.records[-1]
    blob = json.dumps(tr.events, default=str) + json.dumps(rt.telemetry.as_dicts(), default=str)
    for needle in ("abc.DEF-123", "SUPERSECRET", "sk-ABCDEFGH", "eyJabcdefgh12"):
        assert needle not in blob, needle
    ev = events(tr, "harness.llm", "call")[-1]
    assert ev["payload"]["call_id"] == rec.call_id == prov.ids[0] and ev["request_id"] == "REQ-P13"
    assert "SUPERSECRET" not in safe_error(RuntimeError(secret)) and len(safe_error(RuntimeError("x" * 5000))) < 220


def test_incident_vocabulary_is_closed_and_every_kind_is_classified():
    seen = set()
    for src in (ROOT / "src" / "harness" / "llm.py", ROOT / "src" / "harness" / "runtime.py"):
        import re
        seen |= set(re.findall(r'_incident\("([a-z_]+)"', src.read_text(encoding="utf-8"))) | set(re.findall(r'incident\("([a-z_]+)"', src.read_text(encoding="utf-8")))
    assert seen - {"hook_missing", "hook_failed"} <= set(INCIDENT_KINDS), seen - set(INCIDENT_KINDS)
    assert {"provider_timeout", "provider_cancel_ok", "provider_cancel_failed", "provider_cancel_unsupported", "ghost_call_late_result", "context_overflow", "context_retry",
            "partial_response", "hook_missing", "hook_failed", "budget_exhausted",
            "provider_cancel_started", "provider_cancel_timeout", "provider_cancel_rejected", "worker_capacity_exhausted"} == set(INCIDENT_KINDS)    # + P1.3.1


def test_request_outcomes_completed_degraded_abandoned_and_idempotent_finish():
    h, rt, llm, tr = rig(Fake(ok_text), strands_execution=False)
    llm.complete("report", "s", "u", None)
    s = rt.finish()
    assert s["outcome"] == "completed" and s["degraded_reasons"] == [] and rt.finish() is s          # idempotent
    h, rt, llm, tr = rig(Fake(ok_text), strands_execution=False, max_llm_calls=1)
    llm.complete("report", "s", "u1", None)
    with pytest.raises(BudgetExceeded):
        llm.complete("report", "s", "u2", None)
    s = rt.finish()
    assert s["outcome"] == "degraded" and s["degraded_reasons"] == ["max_llm_calls"] and "budget_exhausted" in [i["kind"] for i in s["telemetry"]["incidents"]]
    h, rt, llm, tr = rig(Fake(ok_text), strands_execution=False)
    s = rt.finish(error=RuntimeError("it broke; Bearer abcdef123456"))
    assert s["outcome"] == "abandoned" and "abcdef123456" not in json.dumps(s, default=str)
    ev = events(tr, "harness.reliability", "request_abandoned")
    assert len(ev) == 1 and events(tr, "harness", "request_finished")[0]["payload"]["outcome"] == "abandoned"


def test_an_exception_that_ends_the_pipeline_records_the_request_as_abandoned(monkeypatch):
    import orchestration.pipeline as pl
    made = []

    class Capturing(Tracer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)
    monkeypatch.setattr(pl, "Tracer", Capturing)

    class Exploding(MockLLM):
        def complete(self, agent, system, user, schema=None):
            raise KeyError("not an LLMError: nobody degrades gracefully from this")
    with pytest.raises(KeyError):
        run_pipeline("Why did Kenji's retirement age change?", Exploding(), kenji_memory(), persist_trace=False, harness=harness(), auth=ADMIN)
    fin = events(made[0], "harness", "request_finished")
    assert len(fin) == 1 and fin[0]["payload"]["outcome"] == "abandoned" and events(made[0], "harness.reliability", "request_abandoned")


# ================================================================================================================== real provider clients (urllib stubbed)
class _Resp:
    def __init__(self, doc):
        self.b = json.dumps(doc).encode()

    def read(self, *a):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(monkeypatch, result):
    seen = {}

    def fake(req, timeout=None, **kw):
        seen["timeout"] = timeout
        if isinstance(result, BaseException):
            raise result
        return _Resp(result)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def _http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def test_ollama_uses_the_budget_derived_timeout_and_classifies_failures(monkeypatch):
    doc = {"message": {"content": "hi"}, "prompt_eval_count": 7, "eval_count": 3, "done_reason": "length"}
    seen = _urlopen(monkeypatch, doc)
    o = OllamaLLM(timeout=900.0)
    assert o.native_timeout is True
    o.complete("report", "s", "u")
    assert seen["timeout"] == 900.0
    u = o.pop_usage()
    assert (u.input_tokens, u.output_tokens, u.exact, u.finish_reason) == (7, 3, True, "length")
    tok = CALL_TIMEOUT.set(2.5)
    try:
        o.complete("report", "s", "u")
        assert seen["timeout"] == 2.5                                                                # min(own 900, derived 2.5)
        CALL_TIMEOUT.set(5000.0)
        o.complete("report", "s", "u")
        assert seen["timeout"] == 900.0
    finally:
        CALL_TIMEOUT.reset(tok)
    import socket
    for exc in (urllib.error.URLError(socket.timeout("timed out")), TimeoutError("read timed out")):
        _urlopen(monkeypatch, exc)
        with pytest.raises(LLMTimeoutError):
            o.complete("report", "s", "u")
    _urlopen(monkeypatch, _http_error(400, '{"error":"input length exceeds the context length"}'))
    with pytest.raises(ContextLengthError):
        o.complete("report", "s", "u")
    _urlopen(monkeypatch, _http_error(500, "boom"))
    with pytest.raises(LLMError) as e:
        o.complete("report", "s", "u")
    assert type(e.value) is LLMError                                                                  # an ordinary failure stays ordinary
    _urlopen(monkeypatch, urllib.error.URLError(ConnectionRefusedError("refused")))
    with pytest.raises(LLMError) as e:
        o.complete("report", "s", "u")
    assert type(e.value) is LLMError


def test_openai_uses_the_budget_derived_timeout_and_classifies_failures(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real-0000")
    doc = {"choices": [{"message": {"content": "hi"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 11, "completion_tokens": 4}}
    seen = _urlopen(monkeypatch, doc)
    o = OpenAILLM(timeout=120.0)
    tok = CALL_TIMEOUT.set(3.0)
    try:
        o.complete("report", "s", "u")
        assert seen["timeout"] == 3.0
    finally:
        CALL_TIMEOUT.reset(tok)
    u = o.pop_usage()
    assert (u.input_tokens, u.output_tokens, u.finish_reason) == (11, 4, "length")
    _urlopen(monkeypatch, _http_error(400, '{"error":{"code":"context_length_exceeded"}}'))
    with pytest.raises(ContextLengthError):
        o.complete("report", "s", "u")
    _urlopen(monkeypatch, TimeoutError("timed out"))
    with pytest.raises(LLMTimeoutError):
        o.complete("report", "s", "u")
    _urlopen(monkeypatch, _http_error(401, "bad key"))
    with pytest.raises(LLMError) as e:
        o.complete("report", "s", "u")
    assert type(e.value) is LLMError
