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


SEARCH_REFS_SHOWN = 8


def _search_refs(search: dict) -> dict:
    """A search result as exact, copyable references ("INT-001#3"), so the next call can read them with the right interaction_id and turn_index instead of guessing."""
    matches = [m for m in (search.get("matches") or []) if isinstance(m, dict) and isinstance(m.get("interaction_id"), str) and isinstance(m.get("turn_index"), int)]
    refs = [f"{m['interaction_id']}#{m['turn_index']}" + (" correction" if m.get("cue") == "correction" else "") for m in matches[:SEARCH_REFS_SHOWN]]
    if len(matches) > SEARCH_REFS_SHOWN:
        refs.append(f"... +{len(matches) - SEARCH_REFS_SHOWN} more")
    return {"query": search.get("query"), "total": search.get("total"), "truncated": search.get("truncated"), "turn_refs": refs}


def digest(envelope: dict, limit: int = 600, max_items: int = 4) -> str:
    raw = envelope.get("data", {})
    if isinstance(raw, dict) and isinstance(raw.get("search"), dict):
        raw = {"search": _search_refs(raw["search"])}
    data = _prune(raw, max(max_items, SEARCH_REFS_SHOWN + 1) if "search" in raw else max_items)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    lim = (envelope.get("limitations") or [""])[0]
    suffix = f" | limit: {lim[:90]}" if lim else ""
    keep = max(limit - len(suffix), 80)
    return (text if len(text) <= keep else text[:keep - 3] + "...") + suffix
