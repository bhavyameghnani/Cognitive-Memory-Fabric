"""Per-request telemetry: one record per LLM call (and cache hit), aggregated for the trace and the API."""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class LLMCallRecord:
    agent: str
    model: str
    attempt: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int          # provider-reported cached prompt tokens (0 unless the provider said so)
    exact: bool                 # True only when the provider reported the counts
    latency_ms: int
    cache: str                  # "miss" | "hit" | "bypass"
    outcome: str                # "ok" | "denied" | "error" | "timeout" | "context_overflow" | "partial"
    prompt_hash: str
    compressed: bool = False
    transport: str = "direct"   # "direct" | "strands"
    error: Optional[str] = None
    call_id: str = ""           # correlates this record with harness.llm / harness.reliability events and any late (ghost) completion


@dataclass
class Telemetry:
    records: list = field(default_factory=list)
    incidents: list = field(default_factory=list)       # structured reliability incidents (reliability.INCIDENT_KINDS)
    ghosts: list = field(default_factory=list)          # abandoned calls: {call_id, agent, attempt, state: pending|late_result|late_error, ...}
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, rec: LLMCallRecord) -> None:
        with self._lock:
            self.records.append(rec)

    def incident(self, kind: str, **fields) -> dict:
        rec = dict(kind=kind, **fields)
        with self._lock:
            self.incidents.append(rec)
        return rec

    def ghost_started(self, call_id: str, agent: str, attempt: int) -> None:
        with self._lock:
            self.ghosts.append(dict(call_id=call_id, agent=agent, attempt=attempt, state="pending"))

    def ghost_finished(self, call_id: str, state: str, **fields) -> None:
        with self._lock:
            for g in self.ghosts:
                if g["call_id"] == call_id and g["state"] == "pending":      # exactly one terminal outcome per ghost
                    g.update(state=state, **fields)

    def degrading_kinds(self) -> list:
        from harness.reliability import INCIDENT_KINDS
        with self._lock:
            return sorted({i["kind"] for i in self.incidents if INCIDENT_KINDS.get(i["kind"])})

    def summary(self) -> dict:
        with self._lock:
            recs = list(self.records)
            incidents, ghosts = list(self.incidents), list(self.ghosts)
        by_agent: dict = {}
        for r in recs:
            a = by_agent.setdefault(r.agent, dict(calls=0, cache_hits=0, input_tokens=0, output_tokens=0, retries=0, latency_ms=0))
            a["calls"] += 1
            a["cache_hits"] += r.cache == "hit"
            a["input_tokens"] += r.input_tokens
            a["output_tokens"] += r.output_tokens
            a["retries"] += r.attempt > 0 and r.cache != "hit"
            a["latency_ms"] += r.latency_ms
        real = [r for r in recs if r.cache != "hit" and r.outcome != "denied"]
        return dict(calls=len(recs), model_calls=len(real), cache_hits=sum(r.cache == "hit" for r in recs), cache_misses=sum(r.cache == "miss" for r in recs),
                    input_tokens=sum(r.input_tokens for r in real), output_tokens=sum(r.output_tokens for r in real),
                    cached_input_tokens=sum(r.cached_tokens for r in real), exact_usage_calls=sum(r.exact for r in real),
                    estimated_usage_calls=sum(not r.exact for r in real), denied=sum(r.outcome == "denied" for r in recs),
                    compressed_calls=sum(r.compressed for r in recs), latency_ms=sum(r.latency_ms for r in recs),
                    provider_prompt_cache_reported=any(r.cached_tokens > 0 for r in real), by_agent=by_agent,
                    timeouts=sum(r.outcome == "timeout" for r in recs), context_overflows=sum(r.outcome == "context_overflow" for r in recs),
                    partial_responses=sum(r.outcome == "partial" for r in recs),
                    ghost_calls=len(ghosts), ghost_pending=sum(g["state"] == "pending" for g in ghosts),
                    ghost_late_results=sum(g["state"] in ("late_result", "late_error") for g in ghosts),
                    incidents=[dict(i) for i in incidents])

    def as_dicts(self) -> list[dict]:
        with self._lock:
            return [asdict(r) for r in self.records]
