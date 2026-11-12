"""ONE request-level budget shared by all four agents.  Enforced (raises), not just logged.

BudgetExceeded subclasses LLMError on purpose: every CMF agent already catches LLMError and degrades gracefully (heuristic plan, tool fallback,
deterministic report), so exhaustion yields a controlled partial result and never a crash or a fabricated success.
Deadlines use time.monotonic().
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from harness.config import HarnessConfig
from llm.client import LLMError


class BudgetExceeded(LLMError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"budget exhausted: {reason}" + (f" ({detail})" if detail else ""))
        self.reason, self.detail = reason, detail


@dataclass
class RequestBudget:
    config: HarnessConfig
    clock: Callable[[], float] = time.monotonic
    started: float = field(default=0.0)
    llm_calls: int = 0
    retries: int = 0
    tool_calls: int = 0
    tool_calls_by_agent: dict = field(default_factory=dict)
    llm_calls_by_agent: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    exhausted: list = field(default_factory=list)   # [{"reason", "detail", "t_ms"}]
    ghost_calls: int = 0                    # abandoned model calls that finished LATER (their results were discarded)
    ghost_input_tokens: int = 0             # what those late calls reported; kept apart so a late result never changes the request's own counters
    ghost_output_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.started = self.clock()

    # ------------------------------------------------------------------ helpers
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def elapsed(self) -> float:
        return self.clock() - self.started

    def remaining_seconds(self) -> float:
        return self.config.max_runtime_seconds - self.elapsed()

    def _deny(self, reason: str, detail: str = "") -> None:
        rec = {"reason": reason, "detail": detail, "t_ms": int(self.elapsed() * 1000)}
        if not any(e["reason"] == reason for e in self.exhausted):
            self.exhausted.append(rec)
        raise BudgetExceeded(reason, detail)

    def deny(self, reason: str, detail: str = "") -> None:
        """Public: record an exhaustion reason and raise BudgetExceeded."""
        with self._lock:
            self._deny(reason, detail)

    def note_exhausted(self, reason: str, detail: str = "") -> None:
        """Record an exhaustion reason WITHOUT raising (the caller raises its own, more specific error).  Same record shape as deny()."""
        with self._lock:
            if not any(e["reason"] == reason for e in self.exhausted):
                self.exhausted.append({"reason": reason, "detail": detail, "t_ms": int(self.elapsed() * 1000)})

    def charge_abandoned(self, est_input_tokens: int) -> None:
        """A model call that timed out / was abandoned still SENT its prompt: charge the (estimated) input tokens so abandoning work never refunds it.
        The call itself stays counted (begin_llm_call incremented it and nothing decrements it)."""
        with self._lock:
            self.input_tokens += max(0, est_input_tokens)

    def record_ghost(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.ghost_calls += 1
            self.ghost_input_tokens += max(0, input_tokens)
            self.ghost_output_tokens += max(0, output_tokens)

    def check_deadline(self) -> None:
        with self._lock:
            if self.remaining_seconds() <= 0:
                self._deny("runtime_deadline", f"{self.config.max_runtime_seconds:g}s")

    # ------------------------------------------------------------------ LLM
    def begin_llm_call(self, agent: str, attempt: int, est_input_tokens: int) -> None:
        c = self.config
        with self._lock:
            if self.remaining_seconds() <= 0:
                self._deny("runtime_deadline", f"{c.max_runtime_seconds:g}s")
            if self.llm_calls >= c.max_llm_calls:
                self._deny("max_llm_calls", f"{self.llm_calls}/{c.max_llm_calls}")
            if attempt > 0:
                if attempt > c.max_retries_per_operation:
                    self._deny("max_retries_per_operation", f"attempt {attempt} > {c.max_retries_per_operation}")
                if self.retries >= c.max_retries:
                    self._deny("max_retries", f"{self.retries}/{c.max_retries}")
            if self.input_tokens + est_input_tokens > c.max_input_tokens:
                self._deny("max_input_tokens", f"{self.input_tokens}+{est_input_tokens} > {c.max_input_tokens}")
            if self.output_tokens >= c.max_output_tokens:
                self._deny("max_output_tokens", f"{self.output_tokens}/{c.max_output_tokens}")
            if self.total_tokens + est_input_tokens > c.max_total_tokens:
                self._deny("max_total_tokens", f"{self.total_tokens}+{est_input_tokens} > {c.max_total_tokens}")
            self.llm_calls += 1
            self.llm_calls_by_agent[agent] = self.llm_calls_by_agent.get(agent, 0) + 1
            if attempt > 0:
                self.retries += 1

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.input_tokens += max(0, input_tokens)
            self.output_tokens += max(0, output_tokens)

    # ------------------------------------------------------------------ tools
    def begin_tool_call(self, agent: str, tool: str) -> None:
        c = self.config
        with self._lock:
            if self.remaining_seconds() <= 0:
                self._deny("runtime_deadline", f"{c.max_runtime_seconds:g}s")
            if self.tool_calls >= c.max_tool_calls:
                self._deny("max_tool_calls", f"{self.tool_calls}/{c.max_tool_calls} (tried {tool})")
            if self.tool_calls_by_agent.get(agent, 0) >= c.max_tool_calls_per_agent:
                self._deny("max_tool_calls_per_agent", f"{agent}: {self.tool_calls_by_agent.get(agent, 0)}/{c.max_tool_calls_per_agent}")
            self.tool_calls += 1
            self.tool_calls_by_agent[agent] = self.tool_calls_by_agent.get(agent, 0) + 1

    def tool_timeout(self) -> float:
        """Per-call tool timeout, never longer than the time left on the request."""
        return max(0.001, min(self.config.max_tool_seconds, self.remaining_seconds()))

    def model_timeout(self) -> float:
        return max(0.001, min(self.config.max_model_call_seconds, self.remaining_seconds()))

    def snapshot(self) -> dict:
        c = self.config
        return dict(llm_calls=self.llm_calls, retries=self.retries, tool_calls=self.tool_calls, input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                    total_tokens=self.total_tokens, elapsed_ms=int(self.elapsed() * 1000), llm_calls_by_agent=dict(self.llm_calls_by_agent),
                    ghost_calls=self.ghost_calls, ghost_input_tokens=self.ghost_input_tokens, ghost_output_tokens=self.ghost_output_tokens,
                    tool_calls_by_agent=dict(self.tool_calls_by_agent), exhausted=list(self.exhausted),
                    limits=dict(max_llm_calls=c.max_llm_calls, max_retries=c.max_retries, max_tool_calls=c.max_tool_calls, max_input_tokens=c.max_input_tokens,
                                max_output_tokens=c.max_output_tokens, max_total_tokens=c.max_total_tokens, max_runtime_seconds=c.max_runtime_seconds))
