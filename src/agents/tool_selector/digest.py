"""Compact, truncated summaries of tool results for the next prompt (a 3B model with a 4-6k context window needs short digests)."""
from __future__ import annotations

import json
from typing import Any

_DROP = {"description", "liquidity_requirements", "occupation", "row_provenance", "note", "amount_provenance", "goal_key", "synthetic_fields", "employment_status"}


def _prune(v: Any, max_items: int) -> Any:
    if isinstance(v, dict):
        return {k: _prune(x, max_items) for k, x in v.items() if k not in _DROP}
    if isinstance(v, list):
        out = [_prune(x, max_items) for x in v[:max_items]]
        if len(v) > max_items:
            out.append(f"... +{len(v) - max_items} more")
        return out
    if isinstance(v, str) and len(v) > 60:
        return v[:57] + "..."
    return v


def digest(envelope: dict, limit: int = 600, max_items: int = 4) -> str:
    data = _prune(envelope.get("data", {}), max_items)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    lim = (envelope.get("limitations") or [""])[0]
    suffix = f" | limit: {lim[:90]}" if lim else ""
    keep = max(limit - len(suffix), 80)
    return (text if len(text) <= keep else text[:keep - 3] + "...") + suffix
