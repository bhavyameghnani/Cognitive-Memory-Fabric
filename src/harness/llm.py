"""HarnessLLM: the shared choke point.  Implements the existing LLMClient interface (model, complete) around any real/mock client.

Per call, in order:
    1. context fit      per-agent token ceiling; deterministic compression when the prompt approaches it; ContextOverflow when protected content does not fit
    2. cache lookup     application-level response cache (safe stages only); a hit consumes NO model call and NO budget
    3. budget charge    shared RequestBudget (LLM calls, retries, tokens, deadline)  -- through the Strands BeforeModelCallEvent hook when the Strands
                        transport is on, directly otherwise.  The hook is MANDATORY: if it did not demonstrably run, the provider is not contacted (P1.3).
    4. model call       real client, in a lifecycle-tracked worker.  The provider gets a native timeout DERIVED from the remaining request budget; a call that still
                        overruns is ABANDONED (a Python thread cannot be killed - this module never claims it was), and if it later finishes its result is DISCARDED
                        and recorded as a ghost call.  Usage is read from the provider's response when it reports it, otherwise estimated (and flagged as such).
    5. recovery         a prompt the PROVIDER rejects as too long gets a bounded, budget-aware compress-and-retry; a truncated reply ('length') is never an answer.
    6. telemetry/trace  one record per call; prompts are hashed, never stored unless trace_store_prompts is on; reliability incidents are structured events.
Agents and generate_structured() are unchanged; they only see a client with .model and .complete().  accept_last()/reject_last() are called by
generate_structured after validation so that only validated output is cached.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Optional

from harness.budget import BudgetExceeded, RequestBudget
from harness.cache import ResponseCache, make_key
from harness.context_manager import ContextBudgetController, ContextOverflow
from harness.reliability import InstrumentationError, safe_error
from harness.strands_adapter import CallState, ModelCallGuard, StrandsExecutor, invoke_inner
from harness.telemetry import LLMCallRecord, Telemetry
from harness.token_counter import TokenCounter
from harness.workers import CANCELS, REGISTRY, CapacityExhausted
from llm.client import ATTEMPT, TRUNCATED_FINISH, ContextLengthError, LLMTimeoutError, LLMUsage, PartialResponseError


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class _Life:
    """Lifecycle of ONE worker: running -> done (the caller consumes the result) | abandoned (the caller gave up; a late result is discarded)."""

    def __init__(self) -> None:
        self.lock, self.event = threading.Lock(), threading.Event()
        self.done = self.abandoned = False
        self.text: Optional[str] = None
        self.err: Optional[BaseException] = None


class HarnessLLM:
    def __init__(self, inner: Any, *, config, budget: RequestBudget, telemetry: Telemetry, counter: TokenCounter, context: ContextBudgetController,
                 cache: ResponseCache, emit: Callable[..., None], scope: Callable[[], tuple], data_version: Callable[[], str], request_id: str = "") -> None:
        self.inner, self.cfg, self.budget, self.telemetry = inner, config, budget, telemetry
        self.counter, self.context, self.cache, self.emit = counter, context, cache, emit
        self._scope, self._data_version = scope, data_version      # (actor_id, client_ids) / data version string, evaluated per call
        self.request_id = request_id
        self.guard = ModelCallGuard(budget)
        self.strands = StrandsExecutor(inner, self.guard, budget, counter) if config.strands_execution else None
        self._tl = threading.local()
        self._seq, self._seq_lock = 0, threading.Lock()

    # ------------------------------------------------------------------ LLMClient interface
    @property
    def model(self) -> str:
        return getattr(self.inner, "model", "unknown")

    def __getattr__(self, name: str):          # .calls, .handlers, ... of the wrapped client stay reachable (MockLLM compatibility)
        if name in ("inner", "__setstate__") or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    def complete(self, agent: str, system: str, user: str, schema: Optional[dict] = None) -> str:
        attempt = ATTEMPT.get()
        self._tl.pending = None
        phash = _h(system + "\x00" + user)
        t0 = time.perf_counter()
        compressed = False
        try:
            self.budget.check_deadline()          # an expired request is refused before anything else (and before a 1 ms model timeout could mask it)
            fit = self.context.fit(agent, system, user, schema)
            user, compressed = fit.user, fit.compressed
            est_in = fit.tokens
        except BudgetExceeded as e:
            if isinstance(e, ContextOverflow):    # the local pre-flight could not fit the protected content
                self.budget.note_exhausted("context_overflow", e.detail)
                self._incident("context_overflow", agent, attempt, "-", source="preflight")
            self._record(agent, attempt, 0, 0, 0, False, t0, "miss", "denied", phash, False, "-", safe_error(e))
            raise

        if self.cfg.trace_store_prompts:     # off by default: prompts are otherwise only hashed
            self.emit("harness.llm", "prompt", agent=agent, attempt=attempt, system=system, user=user)
        actor, clients = self._scope()
        cacheable = self._cacheable(agent, clients)
        key = self._key(agent, system, schema, user, actor, clients) if cacheable else None
        if key is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self._record(agent, attempt, 0, 0, 0, False, t0, "hit", "ok", phash, compressed, "cache", None)
                return hit
        cache_state = "miss" if key is not None else "bypass"
        transport = "strands" if self.strands is not None else "direct"

        recoveries = 0
        while True:
            call_id = self._next_call_id()
            state = CallState(agent=agent, system=system, user=user, schema=schema, attempt=attempt + recoveries, est_input=est_in, call_id=call_id)
            try:
                text = self._run(state)
            except ContextLengthError as e:       # the PROVIDER refused the prompt: not a generic failure, and a smaller prompt may succeed
                self._record(agent, state.attempt, est_in, 0, 0, False, t0, cache_state, "context_overflow", phash, compressed, transport, safe_error(e), call_id)
                self._incident("context_overflow", agent, state.attempt, call_id, source="provider", prompt_tokens_estimated=est_in)
                user, est_in = self._recover(agent, system, user, schema, est_in, recoveries, state.attempt, call_id)   # raises ContextOverflow when it cannot
                compressed, recoveries = True, recoveries + 1
                if cacheable:
                    key = self._key(agent, system, schema, user, actor, clients)      # the response belongs to the prompt that was actually sent
                continue
            except BudgetExceeded as e:
                timed_out = e.reason == "model_call_timeout"
                self._record(agent, state.attempt, est_in if timed_out else 0, 0, 0, False, t0, cache_state, "timeout" if timed_out else "denied", phash, compressed, transport,
                             safe_error(e), call_id)
                raise
            except InstrumentationError as e:     # a mandatory control did not run: structured failure, never a normal success
                tin = tout = 0
                exact = False
                if state.text is not None:        # the provider DID answer (only the completion hook is missing/failed): the work was performed, so it is charged - once
                    u = state.usage or LLMUsage(est_in, self.counter.n(state.text), 0, False)
                    if self.guard.settle(state, u.input_tokens, u.output_tokens):
                        tin, tout, exact = u.input_tokens, u.output_tokens, u.exact
                self._record(agent, state.attempt, tin, tout, 0, exact, t0, cache_state, "error", phash, compressed, transport, safe_error(e), call_id)   # the TEXT is discarded
                self._incident(e.kind, agent, state.attempt, call_id, detail=e.detail[:120], charged_input_tokens=tin, charged_output_tokens=tout, charged_estimated=not exact)
                raise
            except Exception as e:
                self._record(agent, state.attempt, est_in, 0, 0, False, t0, cache_state, "error", phash, compressed, transport, safe_error(e), call_id)
                raise
            break

        usage = state.usage
        if usage is None or (not usage.exact and not usage.input_tokens and not usage.output_tokens):    # nothing reported: estimate (and say so)
            usage = LLMUsage(est_in, self.counter.n(text), 0, False, getattr(usage, "finish_reason", None))
        self.guard.after(state, usage.input_tokens, usage.output_tokens)         # tokens were spent whatever we do with the text
        if usage.finish_reason in TRUNCATED_FINISH:
            self._record(agent, state.attempt, usage.input_tokens, usage.output_tokens, usage.cached_tokens, usage.exact, t0, cache_state, "partial", phash, compressed, transport,
                         f"provider stopped on its output limit ({usage.finish_reason})", call_id)
            self._incident("partial_response", agent, state.attempt, call_id, finish_reason=usage.finish_reason, output_tokens=usage.output_tokens)
            raise PartialResponseError(f"{agent}: the provider stopped on its output limit ({usage.finish_reason}); the incomplete text was discarded")
        self._record(agent, state.attempt, usage.input_tokens, usage.output_tokens, usage.cached_tokens, usage.exact, t0, cache_state, "ok", phash, compressed, transport, None, call_id)
        if key is not None:
            self._tl.pending = (key, text)
        return text

    # ------------------------------------------------------------------ validated-output hooks (called by generate_structured)
    def accept_last(self) -> None:
        pending = getattr(self._tl, "pending", None)
        if pending:
            self.cache.put(*pending)
        self._tl.pending = None

    def reject_last(self) -> None:
        self._tl.pending = None

    # ------------------------------------------------------------------ internals
    def _key(self, agent, system, schema, user, actor, clients) -> str:
        return make_key(model=self.model, agent=agent, system=system, schema=schema, user=user, actor=actor, clients=clients, data_version=self._data_version())

    def _cacheable(self, agent: str, clients: tuple) -> bool:
        if not (self.cfg.cache_enabled and self.cache.enabled) or agent not in self.cfg.cache_agents:
            return False
        if getattr(self.inner, "temperature", 0) not in (0, 0.0, None):     # non-deterministic sampling: never cache
            return False
        return bool(self._scope()[0])    # an unscoped request is never cached (actor is mandatory)

    def _next_call_id(self) -> str:
        with self._seq_lock:
            self._seq += 1
            return f"{self.request_id or 'req'}-c{self._seq:03d}"

    def _incident(self, kind: str, agent: str, attempt: int, call_id: str, **fields) -> None:
        """One structured reliability incident: kept in telemetry (and so in the harness summary) and emitted to the trace.  Fixed vocabulary, counts and ids only."""
        self.telemetry.incident(kind, agent=agent, attempt=attempt, call_id=call_id, **fields)
        self.emit("harness.reliability", kind, agent=agent, attempt=attempt, call_id=call_id, **fields)

    def _recover(self, agent: str, system: str, user: str, schema: Optional[dict], est_in: int, recoveries: int, attempt: int, call_id: str) -> tuple:
        """Bounded, budget-aware recovery from a provider context-length rejection.  Returns (smaller user prompt, its estimated size) or raises ContextOverflow."""
        def fail(why: str):
            self.budget.note_exhausted("context_overflow", why)
            raise ContextOverflow(why)
        if recoveries >= self.cfg.max_context_recoveries:
            fail(f"{agent}: the provider rejected the prompt as too long and the recovery limit ({self.cfg.max_context_recoveries}) is used up")
        self.budget.check_deadline()                                           # recovery spends the same request deadline as everything else
        target = int(est_in * self.cfg.context_recovery_shrink)
        try:
            fit = self.context.fit_to(agent, system, user, schema, target)
        except ContextOverflow as e:
            fail(e.detail)
        if fit.tokens >= est_in:
            fail(f"{agent}: compression cannot make the prompt smaller than {est_in} tokens")
        self._incident("context_retry", agent, attempt, call_id, recovery=recoveries + 1, from_tokens=est_in, to_tokens=fit.tokens)
        return fit.user, fit.tokens

    # ---- the model call: native timeout, lifecycle, abandonment ----------------------------------------------------------------------------
    def _run(self, state: CallState) -> str:
        """Run the model call.  The provider is given a native timeout slightly SHORTER than the harness deadline so that, when it honours it, it fails first and nothing is
        left running.  When it does not (or cannot), the worker is abandoned at the deadline: its late result is discarded and recorded, never applied.

        P1.3.1: every call holds a process-wide worker SLOT until its provider work has really exited (admission fails closed at the configured caps); the abandonment transition,
        the ghost record and the slot's 'abandoned' mark happen in ONE critical section, so an abandoned call always has exactly one ghost record and one terminal outcome."""
        timeout = self.budget.model_timeout()
        state.timeout = max(0.001, timeout - min(0.25, 0.1 * timeout))
        try:
            slot = REGISTRY.admit(self.cfg.max_provider_workers, self.cfg.max_abandoned_workers)
        except CapacityExhausted as e:       # fail closed BEFORE the budget is charged or the provider is contacted
            self._incident("worker_capacity_exhausted", state.agent, state.attempt, state.call_id, limit=e.reason, workers=REGISTRY.snapshot())
            self.budget.deny(e.reason, e.detail)
        state.slot = slot
        life = _Life()

        def work() -> None:
            try:
                text, err = None, None
                try:
                    text = self._call(state)
                except BaseException as e:          # noqa: BLE001 - carried to whoever is waiting, or recorded as a late error
                    err = e
                with life.lock:
                    late = life.abandoned
                    if not late:
                        life.done, life.text, life.err = True, text, err
                if late:
                    self._late(state, text, err)
                else:
                    life.event.set()
            finally:
                slot.drop()                         # capacity is returned only when this worker (and any provider thread it started) has really exited
        # a daemon thread: unlike a ThreadPoolExecutor worker it is not joined at interpreter exit, so an abandoned call cannot hold the process hostage
        threading.Thread(target=work, name=f"cmf-llm-{state.call_id}", daemon=True).start()
        if not life.event.wait(timeout):
            with life.lock:
                if not life.done:
                    with state.lock:                # atomic with ModelCallGuard.before / invoke_inner: from here on this call can neither be charged nor reach the provider
                        state.abandoned = True
                    life.abandoned = True
                    self.telemetry.ghost_started(state.call_id, state.agent, state.attempt)    # BEFORE the worker can observe `abandoned` (it needs life.lock)
                    REGISTRY.mark_abandoned(slot)
            if life.abandoned:
                self._timed_out(state, timeout, native=False)      # raises BudgetExceeded
        if life.err is not None:
            if isinstance(life.err, LLMTimeoutError):              # the provider enforced the timeout itself: nothing is left running
                self._timed_out(state, timeout, native=True)       # raises BudgetExceeded
            raise life.err
        return life.text

    def _call(self, state: CallState) -> str:
        if self.strands is not None:
            return self.strands.run(state)
        self.guard.before(state)
        text, usage = invoke_inner(self.inner, state)
        state.text, state.usage = text, usage
        return text

    def _timed_out(self, state: CallState, timeout: float, native: bool) -> None:
        self.guard.settle(state, state.est_input, 0, abandoned=True)   # the prompt WAS sent: abandoning never refunds it (the call stays counted); charged at most once
        supports = bool(getattr(self.inner, "native_timeout", False))
        cancel = "not_needed" if native else self._try_cancel(state)
        self._incident("provider_timeout", state.agent, state.attempt, state.call_id, timeout_s=round(timeout, 3), provider_timeout_s=round(state.timeout or 0, 3),
                       enforcement="provider_native" if native else "worker_abandoned", provider_native_timeout=supports, cancel=cancel,
                       charged_input_tokens=state.est_input, charged_estimated=True)
        self.budget.deny("model_call_timeout", f"{state.agent} exceeded {timeout:g}s" + ("" if native else " (worker abandoned; the provider may still be running)"))

    def _try_cancel(self, state: CallState) -> str:
        """Best-effort cancellation, NEVER on the request thread: the provider's cancel hook runs in a bounded pool of daemon threads and the request does not wait for it.  Every
        dispatch reports exactly one terminal status (ok | failed | timeout).  `ok` means the provider acknowledged the request - not that its work stopped."""
        cancel = getattr(self.inner, "cancel_call", None)
        if not callable(cancel):
            self._incident("provider_cancel_unsupported", state.agent, state.attempt, state.call_id)
            return "unsupported"
        kinds = {"ok": "provider_cancel_ok", "failed": "provider_cancel_failed", "timeout": "provider_cancel_timeout"}

        def report(status: str, error: Optional[str]) -> None:
            self._incident(kinds[status], state.agent, state.attempt, state.call_id, provider_work_stopped="unknown", **({"error": error} if error else {}))

        def started() -> None:
            self._incident("provider_cancel_started", state.agent, state.attempt, state.call_id)

        if not CANCELS.dispatch(lambda: cancel(state.call_id), self.cfg.max_cancel_workers, self.cfg.cancel_timeout_seconds, report, on_start=started):
            self._incident("provider_cancel_rejected", state.agent, state.attempt, state.call_id, cancel_pool=CANCELS.snapshot())
            return "rejected"
        return "dispatched"

    def _late(self, state: CallState, text: Optional[str], err: Optional[BaseException]) -> None:
        """An ABANDONED call finished.  Runs in the ghost thread.  It records what happened and changes nothing else: no budget counter of the request, no cache, no
        response - the text is dropped here."""
        try:
            usage = state.usage
            tin = usage.input_tokens if usage else state.est_input
            tout = usage.output_tokens if usage else (self.counter.n(text) if text else 0)
            self.budget.record_ghost(tin, tout)
            self.telemetry.ghost_finished(state.call_id, "late_result" if err is None else "late_error", input_tokens=tin, output_tokens=tout,
                                          error=safe_error(err) if err is not None else None)
            self._incident("ghost_call_late_result", state.agent, state.attempt, state.call_id, completed=err is None, discarded=True, input_tokens=tin, output_tokens=tout)
        except Exception:                                        # noqa: BLE001 - a ghost thread must never raise into anything
            pass

    def _record(self, agent, attempt, tin, tout, cached, exact, t0, cache, outcome, phash, compressed, transport, error, call_id: str = "") -> None:
        rec = LLMCallRecord(agent=agent, model=self.model, attempt=attempt, input_tokens=tin, output_tokens=tout, cached_tokens=cached, exact=exact,
                            latency_ms=int((time.perf_counter() - t0) * 1000), cache=cache, outcome=outcome, prompt_hash=phash, compressed=compressed,
                            transport=transport, error=error, call_id=call_id)
        self.telemetry.add(rec)
        self.emit("harness.llm", "call", agent=agent, model=self.model, attempt=attempt, input_tokens=tin, output_tokens=tout, cached_tokens=cached,
                  usage_exact=exact, token_method=self.counter.method, latency_ms=rec.latency_ms, cache=cache, outcome=outcome, prompt_hash=phash,
                  compressed=compressed, transport=transport, error=error, call_id=call_id)
