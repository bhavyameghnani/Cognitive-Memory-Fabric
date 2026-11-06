"""Strands Agents SDK integration boundary for CMF model calls.

CMF's four agents call LLMClient.complete(); Strands hooks do not see that path.  This module creates the boundary deliberately:

  CmfStrandsModel     a strands.models.Model whose stream() delegates to the CMF LLMClient (so Ollama/OpenAI/Mock keep working unchanged).
  CmfBudgetPlugin     (hooks.py) the request budget, enforced through BeforeModelCallEvent.cancel / AfterModelCallEvent.
  StrandsExecutor     builds a single-turn strands.Agent per model call (no tools, no retry strategy, no callbacks), attaches the plugin and applies
                      Strands' invocation `limits` (turns=1 plus the remaining output/total token budget), then returns the raw text to the caller.

Not migrated: the agents' domain logic, prompts, validators and the pipeline stay plain CMF code.  Not used: Strands tool execution (the Tool Selector
talks to MCP through its own Toolbox, so tool budget/permission enforcement is done by HarnessToolbox at the CMF boundary) and Strands middleware
(in 1.57.1 the middleware registry is only reachable through the private `agent._middleware_registry`, so it is deliberately not relied on).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from strands import Agent
from strands.models import Model

from harness.budget import BudgetExceeded, RequestBudget
from harness.hooks import CmfBudgetPlugin
from harness.reliability import InstrumentationError
from llm.client import CALL_ID, CALL_TIMEOUT, LLMError, LLMUsage

logging.getLogger("strands").setLevel(logging.WARNING)   # Strands logs INFO on every Agent creation; keep the console quiet unless something is wrong


@dataclass
class CallState:
    """Everything about ONE model call, shared between the Strands hooks, the model adapter and the caller."""
    agent: str
    system: str
    user: str
    schema: Optional[dict]
    attempt: int
    est_input: int
    started: float = 0.0
    text: Optional[str] = None
    usage: Optional[LLMUsage] = None
    denied: Optional[BudgetExceeded] = None
    error: Optional[BaseException] = None
    stop_reason: Optional[str] = None
    strands_after_called: bool = False
    call_id: str = ""                    # correlation id of this provider call (also the cancellation handle)
    timeout: Optional[float] = None       # seconds the provider may take, derived from the remaining request budget
    before_called: bool = False           # the mandatory BeforeModelCall hook ran ...
    before_ok: bool = False               # ... and charged the budget
    hook_error: Optional[BaseException] = None   # a mandatory hook raised
    abandoned: bool = False               # the caller gave up on this call (timeout): it must not charge the budget or contact the provider if it has not yet
    # P1.3.1: `lock` makes 'not abandoned -> charge the budget' and 'not abandoned -> contact the provider' ATOMIC with the abandonment transition (F5); `settled` makes the
    # token charge happen exactly once whatever path the call ends on (F2); `slot` is the process-wide worker slot (F8).
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    charged: bool = False                 # begin_llm_call admitted and counted this call
    provider_started: bool = False        # the provider was (or is being) contacted
    settled: bool = False                 # tokens for this call have been charged to the request budget
    slot: Optional[Any] = field(default=None, repr=False, compare=False)


class ModelCallGuard:
    """The single place a model call is charged to the request budget (used by the direct path and by the Strands hook)."""

    def __init__(self, budget: RequestBudget) -> None:
        self.budget = budget

    def before(self, state: CallState) -> None:
        with state.lock:      # the abandonment transition takes the same lock: a call is either charged BEFORE it was abandoned or refused, never charged afterwards
            if state.abandoned:
                raise LLMError("model call abandoned before it started")
            self.budget.begin_llm_call(state.agent, state.attempt, state.est_input)
            state.charged = True
            state.started = time.perf_counter()

    def settle(self, state: CallState, input_tokens: int, output_tokens: int, abandoned: bool = False) -> bool:
        """Charge the tokens of ONE call to the request budget - exactly once, whatever path ends the call.  Returns False if it was already settled."""
        with state.lock:
            if state.settled:
                return False
            state.settled = True
        if abandoned:
            self.budget.charge_abandoned(input_tokens)
        else:
            self.budget.record_tokens(input_tokens, output_tokens)
        return True

    def after(self, state: CallState, input_tokens: int, output_tokens: int) -> None:
        self.settle(state, input_tokens, output_tokens)


def invoke_inner(inner: Any, state: CallState) -> tuple[str, Optional[LLMUsage]]:
    """Call the real client and read its per-thread usage in the SAME thread."""
    with state.lock:                 # atomic with the abandonment transition: a call that lost the race never contacts the provider
        if state.abandoned:
            raise LLMError("model call abandoned before the provider was contacted")
        state.provider_started = True
    t1, t2 = CALL_TIMEOUT.set(state.timeout), CALL_ID.set(state.call_id or None)       # providers read these in THIS thread (see llm.client.call_timeout)
    try:
        text = inner.complete(state.agent, state.system, state.user, state.schema)
        pop = getattr(inner, "pop_usage", None)
        return text, (pop() if callable(pop) else None)
    finally:
        CALL_TIMEOUT.reset(t1)
        CALL_ID.reset(t2)


async def run_in_daemon_thread(fn, slot=None):
    """Run a blocking call in a DAEMON thread and await it.  `asyncio.to_thread` is deliberately NOT used: it runs on the loop's default ThreadPoolExecutor, whose threads are
    joined by `asyncio.run` (shutdown_default_executor) and again at interpreter exit, so a provider call that never returns would hang the process (P1.3 audit F1).
    The slot (if any) stays held until the thread has really exited, so an abandoned call keeps occupying process-wide capacity."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def deliver(result=None, exc=None) -> None:
        def apply() -> None:
            if not fut.done():
                fut.set_exception(exc) if exc is not None else fut.set_result(result)
        try:
            loop.call_soon_threadsafe(apply)
        except RuntimeError:               # the loop is gone (the caller was abandoned and its event loop closed): the late result has nowhere to go - drop it
            pass

    def run() -> None:
        try:
            try:
                res = fn()
            except BaseException as e:     # noqa: BLE001 - handed to the awaiting coroutine
                deliver(exc=e)
            else:
                deliver(res)
        finally:
            if slot is not None:
                slot.drop()

    if slot is not None:
        slot.hold()
    threading.Thread(target=run, name="cmf-provider", daemon=True).start()
    return await fut


class CmfStrandsModel(Model):
    """strands.models.Model backed by a CMF LLMClient."""

    def __init__(self, inner: Any, state: CallState, counter) -> None:
        self.inner, self.state, self.counter = inner, state, counter
        self._config: dict = {"model_id": getattr(inner, "model", "cmf")}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return dict(self._config)

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):   # CMF validates structure itself (generate_structured)
        raise NotImplementedError("CmfStrandsModel does not implement Strands structured_output; CMF validates model output in generate_structured")
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncGenerator[dict, None]:
        st = self.state
        try:
            if not st.before_ok:       # the budget hook did not run (or failed): the provider must NOT be contacted, whatever Strands does next
                raise InstrumentationError("hook_failed" if st.hook_error is not None else "hook_missing", "BeforeModelCall budget hook did not run before the model call")
            text, usage = await run_in_daemon_thread(lambda: invoke_inner(self.inner, st), st.slot)
        except BaseException as e:   # keep the original exception so the executor can re-raise it unchanged
            st.error = e
            raise
        st.text = text
        st.usage = usage or LLMUsage(st.est_input, self.counter.n(text), 0, False)
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": st.usage.input_tokens, "outputTokens": st.usage.output_tokens,
                                      "totalTokens": st.usage.input_tokens + st.usage.output_tokens},
                            "metrics": {"latencyMs": int((time.perf_counter() - (st.started or time.perf_counter())) * 1000)}}}


class StrandsExecutor:
    def __init__(self, inner: Any, guard: ModelCallGuard, budget: RequestBudget, counter) -> None:
        self.inner, self.guard, self.budget, self.counter = inner, guard, budget, counter

    def run(self, state: CallState) -> str:
        model = CmfStrandsModel(self.inner, state, self.counter)
        plugin = CmfBudgetPlugin(self.guard, state)
        agent = Agent(model=model, system_prompt=state.system, tools=[], callback_handler=None, plugins=[plugin], retry_strategy=None,
                      agent_id=f"cmf-{state.agent}", name=f"cmf-{state.agent}", load_tools_from_directory=False)
        c = self.budget.config
        limits = {"turns": 1,
                  "output_tokens": max(1, c.max_output_tokens - self.budget.output_tokens),
                  "total_tokens": max(1, c.max_total_tokens - self.budget.total_tokens)}
        try:
            self._invoke(agent, state.user, limits)
        except BaseException:
            if state.denied is not None:
                raise state.denied from None
            if state.error is not None:
                raise state.error from None
            if state.hook_error is not None:
                raise InstrumentationError("hook_failed", type(state.hook_error).__name__) from None
            raise
        if state.denied is not None:      # Strands returned the cancel message as if it were an answer: refuse to use it
            raise state.denied
        self.verify_instrumentation(state)
        if state.text is None:
            raise LLMError("Strands finished without model output")
        return state.text

    @staticmethod
    def _invoke(agent, prompt: str, limits: dict) -> Any:
        """Drive the Strands agent WITHOUT `Agent.__call__`.  `__call__` -> strands._async.run_async runs the event loop inside a `ThreadPoolExecutor` thread, and
        concurrent.futures joins those threads at interpreter exit: a provider call that never returns would then hang the whole process even though HarnessLLM already
        abandoned it (P1.3 audit F1).  `invoke_async` is the same public API; we run it with asyncio.run in the CALLER's thread - HarnessLLM's daemon worker - and the
        provider call itself runs in another daemon thread (run_in_daemon_thread), so nothing that can block forever is registered with an at-exit join.
        Verified by tests/test_p131_hardening.py::test_f1_* (a subprocess that must exit)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(agent.invoke_async(prompt, limits=limits))
        return agent(prompt, limits=limits)       # called from inside a running loop (never by HarnessLLM): asyncio.run would fail, so use the SDK bridge

    @staticmethod
    def verify_instrumentation(state: CallState) -> None:
        """A call is a normal success only if EVERY mandatory hook demonstrably ran.  Otherwise its output is discarded and a structured InstrumentationError is raised."""
        if state.hook_error is not None:
            raise InstrumentationError("hook_failed", type(state.hook_error).__name__)
        if not (state.before_called and state.before_ok):
            raise InstrumentationError("hook_missing", "BeforeModelCall budget hook did not run")
        if not state.strands_after_called:
            raise InstrumentationError("hook_missing", "AfterModelCall completion hook did not run")
