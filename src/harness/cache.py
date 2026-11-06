"""Application-level response cache (NOT provider prompt caching).

Provider-side prompt caching is automatic at OpenAI for repeated prompt prefixes; the harness cannot switch it on.  It only REPORTS provider-claimed
cached tokens (telemetry.cached_input_tokens, taken from the response usage block).  Everything in this file is the separate, local cache of raw model
output for safe, repeatable operations.

A key binds ALL of: model id, agent, system-prompt hash (= template version), output-schema hash, the full user-prompt hash, the actor and client scope,
and a data-version string (changes whenever memory or operational data changes).  Consequences:
  * client A can never be served client B's output (scope differs, and the client's own evidence is inside the prompt hash);
  * changed evidence / memory / prompt / schema / model gives a different key, so stale output is not served;
  * authorization decisions are never cached (the gate runs before every lookup, and its result is not stored here);
  * entries expire (TTL) and the cache is size-bounded (LRU).
Only output that PASSED structured validation is stored (accept_last), so a malformed first answer is never replayed.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Optional


def sha(text: str, n: int = 24) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def make_key(*, model: str, agent: str, system: str, schema: Optional[dict], user: str, actor: str, clients: tuple, data_version: str, salt: str = "") -> str:
    schema_hash = sha(json.dumps(schema, sort_keys=True, default=str)) if schema else "-"
    return "|".join([model, agent, sha(system), schema_hash, sha(user), actor or "-", (json.dumps(sorted({str(c) for c in clients})) if clients else "-"), data_version or "-", salt])


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0
    evictions: int = 0
    expired: int = 0
    bypassed: int = 0

    @property
    def hit_rate(self) -> float:
        n = self.hits + self.misses
        return round(self.hits / n, 3) if n else 0.0


@dataclass
class ResponseCache:
    max_entries: int = 256
    ttl_seconds: float = 900.0
    enabled: bool = True
    clock: Callable[[], float] = time.monotonic
    stats: CacheStats = field(default_factory=CacheStats)
    _data: "OrderedDict[str, tuple]" = field(default_factory=OrderedDict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get(self, key: str) -> Optional[str]:
        if not self.enabled:
            self.stats.bypassed += 1
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.stats.misses += 1
                return None
            stored_at, value = item
            if self.clock() - stored_at > self.ttl_seconds:
                del self._data[key]
                self.stats.expired += 1
                self.stats.misses += 1
                return None
            self._data.move_to_end(key)
            self.stats.hits += 1
            return value

    def put(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._data[key] = (self.clock(), value)
            self._data.move_to_end(key)
            self.stats.stores += 1
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.stats.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
