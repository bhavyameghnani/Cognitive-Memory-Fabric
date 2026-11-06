"""Context Budget Controller.

Two jobs, both deterministic:
  fit()            keep ONE prompt inside its agent's token limit (and the model window minus reserved output), compressing only when the prompt
                   approaches the limit and failing safely (ContextOverflow) when protected content does not fit.
  select_memory()  decide which retrieved memories travel downstream: drop anything owned by another client or not authorised, never let a
                   superseded / unconfirmed / exploratory item sit in the "current verified" group, rank the rest by relevance, recency, certainty,
                   source quality and lifecycle status, and keep the whole set inside memory_context_tokens.  Every inclusion/exclusion is recorded.

The per-request total is enforced by RequestBudget (shared); the per-agent limits here make sure no single agent is handed the whole window.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, Optional

from agents.memory.config import CERTAINTY_WEIGHT
from harness.budget import BudgetExceeded
from harness.compression import CompressionResult, compress
from harness.config import HarnessConfig
from harness.token_counter import TokenCounter
from schemas.memory import MemoryContext, MemoryItem

VERIFIED_CERTAINTY = ("confirmed", "correction", "n/a")
GROUPS = ("current", "history", "episodes", "procedures", "working", "unresolved")
STATUS_WEIGHT = {"active": 1.0, "disputed": 0.8, "unconfirmed": 0.4, "candidate": 0.3, "superseded": 0.2, "expired": 0.0}
SOURCE_WEIGHT = {"consolidation": 1.0, "curated_file": 1.0, "conversation_rule": 0.8, "conversation_llm": 0.7, "tool_summary": 0.5, "orchestrator": 0.4}
_WORD = re.compile(r"[a-z]{3,}")


class ContextOverflow(BudgetExceeded):
    def __init__(self, detail: str) -> None:
        super().__init__("context_overflow", detail)


@dataclass
class FitResult:
    user: str
    tokens: int                      # total prompt tokens after fitting (system + schema + user)
    limit: int
    compression: Optional[CompressionResult] = None

    @property
    def compressed(self) -> bool:
        return self.compression is not None and self.compression.changed


@dataclass
class MemorySelection:
    included: dict = field(default_factory=dict)        # client_id -> {group: [memory_id]}
    excluded: list = field(default_factory=list)        # [{"memory_id", "client_id", "reason"}]
    demoted: list = field(default_factory=list)         # [{"memory_id", "from", "to", "reason"}]
    tokens_before: int = 0
    tokens_after: int = 0

    def report(self) -> dict:
        return dict(included=self.included, excluded=self.excluded, demoted=self.demoted, tokens_before=self.tokens_before, tokens_after=self.tokens_after)


class ContextBudgetController:
    def __init__(self, config: HarnessConfig, counter: TokenCounter, emit: Callable[..., None] = lambda *a, **k: None) -> None:
        self.cfg, self.counter, self.emit = config, counter, emit

    # ------------------------------------------------------------------ one prompt
    def fit(self, agent: str, system: str, user: str, schema: Optional[dict], overhead: int = 24) -> FitResult:
        limit = self.cfg.agent_limit(agent)
        fixed = self.counter.n(system) + (self.counter.n(json.dumps(schema)) if schema else 0) + overhead
        body = self.counter.n(user)
        total = fixed + body
        if total <= limit * self.cfg.compression_threshold:
            return FitResult(user, total, limit)
        if not self.cfg.compression_enabled:
            if total <= limit:
                return FitResult(user, total, limit)
            raise ContextOverflow(f"{agent} prompt is {total} tokens, limit {limit}, compression disabled")
        res = compress(user, max(1, limit - fixed), self.counter, reason=f"{agent} prompt {total} tokens approaches limit {limit}")
        self.emit("harness.context", "compression", agent=agent, **res.report())
        if not res.fits:
            raise ContextOverflow(f"{agent} prompt needs {fixed + res.compressed_tokens} tokens after compression, limit {limit}; "
                                  f"protected content does not fit, refusing to cut it")
        return FitResult(res.text, fixed + res.compressed_tokens, limit, res)

    def fit_to(self, agent: str, system: str, user: str, schema: Optional[dict], target_tokens: int, overhead: int = 24) -> FitResult:
        """Recovery after the PROVIDER refused a prompt as too long (our estimate said it fitted): shrink the user part so the whole prompt is at most `target_tokens`.
        Same deterministic, deletion-only compression as fit(); protected content is never cut, so this raises ContextOverflow when it cannot get there."""
        fixed = self.counter.n(system) + (self.counter.n(json.dumps(schema)) if schema else 0) + overhead
        res = compress(user, max(1, target_tokens - fixed), self.counter, reason=f"{agent} prompt rejected by the provider as too long; retrying at <= {target_tokens} tokens")
        self.emit("harness.context", "recovery_compression", agent=agent, **res.report())
        if not res.fits:
            raise ContextOverflow(f"{agent} prompt needs {fixed + res.compressed_tokens} tokens after recovery compression, target {target_tokens}; protected content does not fit")
        return FitResult(res.text, fixed + res.compressed_tokens, target_tokens, res)

    # ------------------------------------------------------------------ memories
    def _tokens(self, m: MemoryItem) -> int:
        return self.counter.n(f"{m.topic} {m.value_text or ''} {m.content}") + 8

    def _priority(self, m: MemoryItem, kw: set, ref: Optional[date]) -> float:
        cert = CERTAINTY_WEIGHT.get(m.certainty, 0.2)
        status = STATUS_WEIGHT.get(m.status, 0.3)
        rec = 0.5
        if ref and m.event_date:
            try:
                age = (ref - date.fromisoformat(m.event_date[:10])).days
                rec = max(0.0, 1.0 - max(age, 0) / 365.0)
            except ValueError:
                pass
        overlap = min(len(kw & set(_WORD.findall(f"{m.content} {m.topic}".lower()))), 4) / 4
        return 0.35 * cert + 0.2 * status + 0.2 * rec + 0.15 * overlap + 0.1 * SOURCE_WEIGHT.get(m.origin, 0.5)

    def select_memory(self, plan, memory: dict, allowed_clients: Optional[Iterable[str]]) -> tuple[dict, MemorySelection]:
        sel = MemorySelection()
        allowed = None if allowed_clients is None else set(allowed_clients)
        kw = set(_WORD.findall((plan.request_text or "").lower()))
        out: dict = {}
        for cid, ctx in memory.items():
            if (allowed is not None and cid not in allowed) or (plan.client_ids and cid not in plan.client_ids):
                for g in GROUPS:
                    for m in getattr(ctx, g):
                        sel.excluded.append(dict(memory_id=m.memory_id, client_id=m.client_id, reason="client_not_authorized_or_not_in_plan"))
                continue
            c = ctx.model_copy(deep=True)
            dates = [m.event_date for g in GROUPS for m in getattr(c, g) if m.event_date]
            ref = date.fromisoformat(max(dates)[:10]) if dates else None
            # 1. ownership
            for g in GROUPS:
                keep = []
                for m in getattr(c, g):
                    if m.client_id != cid and m.client_id != "GLOBAL":
                        sel.excluded.append(dict(memory_id=m.memory_id, client_id=m.client_id, reason="foreign_client"))
                    else:
                        keep.append(m)
                setattr(c, g, keep)
            # 2. nothing unverified / superseded may sit in the "current" (verified) group
            stay = []
            for m in c.current:
                if m.status not in ("active", "disputed") or m.valid_to:
                    c.history.append(m)
                    sel.demoted.append(dict(memory_id=m.memory_id, **{"from": "current", "to": "history"}, reason=f"status {m.status} / valid_to {m.valid_to}"))
                elif m.certainty not in VERIFIED_CERTAINTY or m.memory_type != "semantic":
                    c.episodes.append(m)
                    sel.demoted.append(dict(memory_id=m.memory_id, **{"from": "current", "to": "episodes"}, reason=f"certainty {m.certainty} / type {m.memory_type} is not a verified fact"))
                else:
                    stay.append(m)
            c.current = stay
            # 3. token budget: current + unresolved are protected; drop the lowest-priority remainder
            groups_all = [(g, m) for g in GROUPS for m in getattr(c, g)]
            before = sum(self._tokens(m) for _, m in groups_all)
            sel.tokens_before += before
            total = before
            droppable = sorted([(self._priority(m, kw, ref), g, m) for g, m in groups_all if g not in ("current", "unresolved")], key=lambda t: t[0])
            for _, g, m in droppable:
                if total <= self.cfg.memory_context_tokens:
                    break
                setattr(c, g, [x for x in getattr(c, g) if x.memory_id != m.memory_id])
                total -= self._tokens(m)
                sel.excluded.append(dict(memory_id=m.memory_id, client_id=m.client_id, reason="token_budget"))
                if g == "episodes":
                    c.episodes_omitted += 1
            sel.tokens_after += total
            sel.included[cid] = {g: [m.memory_id for m in getattr(c, g)] for g in GROUPS}
            out[cid] = c
        self.emit("harness.context", "memory_selection", **sel.report())
        return out, sel
