"""Temporal Conflict Detector.

Reads a client's retrieved MemoryContext and reports, WITHOUT changing or overwriting any memory:
  replacement          newer confirmed version supersedes an older one (expected lifecycle; information only, history is preserved)
  contradiction        two confirmed, currently-valid statements about the same slot with different values (needs a human)
  unconsolidated_update  a confirmed episode newer than the current version disagrees with it: the current value may be out of date, but a newer
                       statement is NOT assumed correct (it may itself be a mistake), so both are surfaced
  changed_over_time    differing values stated at different dates by non-confirmed statements (preference drift): both kept, none promoted
  open_exploration     an exploratory / hypothetical / unconfirmed / recommended item dated AFTER the current version: it does not replace it
  stale                the current version is older than HarnessConfig.stale_after_days relative to the reference date
  disputed             a memory carries lifecycle status 'disputed'
Each Conflict carries the memory ids involved and the reason it was raised.  Unresolved kinds are exposed to downstream agents through
MemoryContext.contradictions, which the Report Agent already turns into 'Contradiction (<client>)' uncertainties.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from schemas.memory import MemoryContext, MemoryItem

CONFIRMED = ("confirmed", "correction")
OPEN = ("exploratory", "hypothetical", "unconfirmed", "recommendation")
UNRESOLVED_KINDS = ("contradiction", "unconsolidated_update", "open_exploration", "disputed", "stale")


@dataclass
class Conflict:
    kind: str
    client_id: str
    topic: str
    subject: str
    memory_ids: list
    reason: str
    severity: str = "info"      # info | warning | high

    @property
    def unresolved(self) -> bool:
        return self.kind in UNRESOLVED_KINDS

    def describe(self) -> str:
        return f"[{self.kind}] {self.topic}/{self.subject or self.topic}: {self.reason} (memories: {', '.join(self.memory_ids)})"

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(v: Optional[str]) -> str:
    return re.sub(r"[\s$,]", "", (v or "").lower())


def _d(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _overlap(a: MemoryItem, b: MemoryItem) -> bool:
    a0, a1 = a.valid_from or "0000-00-00", a.valid_to or "9999-12-31"
    b0, b1 = b.valid_from or "0000-00-00", b.valid_to or "9999-12-31"
    return a0 < b1 and b0 < a1


def detect(ctx: MemoryContext, stale_after_days: int = 180, reference_date: Optional[str] = None) -> list[Conflict]:
    out: list[Conflict] = []
    cid = ctx.client_id
    items = [*ctx.current, *ctx.history, *ctx.episodes, *ctx.unresolved]
    dates = [x for x in (_d(m.event_date) or _d(m.valid_from) for m in items) if x]
    ref = _d(reference_date) or _d(ctx.as_of) or (max(dates) if dates else None)

    def slot(m: MemoryItem) -> tuple:
        return (m.topic, m.subject or m.topic)

    sem = [m for m in [*ctx.current, *ctx.history] if m.memory_type == "semantic"]
    by_slot: dict[tuple, list] = {}
    for m in sem:
        by_slot.setdefault(slot(m), []).append(m)
    episodes_by_slot: dict[tuple, list] = {}
    for m in [*ctx.episodes, *ctx.unresolved]:
        if m.memory_type == "episodic":
            episodes_by_slot.setdefault(slot(m), []).append(m)

    for sl, versions in by_slot.items():
        versions.sort(key=lambda m: m.version_no or 0)
        # replacement chain: information only
        for a, b in zip(versions, versions[1:]):
            if a.status == "superseded" and b.supersedes_id == a.memory_id:
                out.append(Conflict("replacement", cid, *sl, [a.memory_id, b.memory_id],
                                    f"value {a.value_text} (valid {a.valid_from} to {a.valid_to}) was replaced by {b.value_text} from {b.valid_from}; history kept", "info"))
        live = [m for m in versions if m.status in ("active", "disputed") and not m.valid_to]
        # genuine contradiction: more than one live confirmed value in the same slot with overlapping validity
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if _norm(a.value_text) != _norm(b.value_text) and a.certainty in CONFIRMED and b.certainty in CONFIRMED and _overlap(a, b):
                    out.append(Conflict("contradiction", cid, *sl, [a.memory_id, b.memory_id],
                                        f"two valid confirmed values at the same time: {a.value_text} vs {b.value_text}", "high"))
        for m in live:
            if m.status == "disputed":
                out.append(Conflict("disputed", cid, *sl, [m.memory_id], f"value {m.value_text} is marked disputed", "high"))
            vf = _d(m.valid_from)
            if ref and vf and (ref - vf).days > stale_after_days:
                out.append(Conflict("stale", cid, *sl, [m.memory_id], f"current value {m.value_text} has been valid since {m.valid_from} ({(ref - vf).days} days before {ref})", "warning"))
        cur = live[-1] if live else None
        if cur is not None:
            for e in episodes_by_slot.get(sl, []):
                ed = _d(e.event_date)
                if not ed or not _d(cur.valid_from) or ed <= _d(cur.valid_from):
                    continue
                if e.certainty in CONFIRMED and e.status == "active" and e.value_text and _norm(e.value_text) != _norm(cur.value_text):
                    out.append(Conflict("unconsolidated_update", cid, *sl, [cur.memory_id, e.memory_id],
                                        f"a confirmed statement on {e.event_date} says {e.value_text} but the current value is {cur.value_text}; "
                                        f"the newer statement is not assumed correct", "high"))
                elif e.certainty in OPEN:
                    out.append(Conflict("open_exploration", cid, *sl, [cur.memory_id, e.memory_id],
                                        f"{e.certainty} statement on {e.event_date} does not replace the current value {cur.value_text}", "info"))

    # preference drift among non-confirmed statements (no semantic version to compare with)
    for sl, eps in episodes_by_slot.items():
        if sl in by_slot:
            continue
        valued = sorted((e for e in eps if e.value_text and e.event_date), key=lambda e: e.event_date)
        for a, b in zip(valued, valued[1:]):
            if _norm(a.value_text) != _norm(b.value_text) and b.event_date > a.event_date:
                out.append(Conflict("changed_over_time", cid, *sl, [a.memory_id, b.memory_id],
                                    f"{a.certainty} value {a.value_text} on {a.event_date} was followed by {b.certainty} value {b.value_text} on {b.event_date}; "
                                    f"neither is promoted to a verified constraint", "info"))
    return out


def publish(ctx: MemoryContext, conflicts: list[Conflict]) -> int:
    """Expose unresolved conflicts to downstream agents (idempotent).  Returns how many were added."""
    existing = set(ctx.contradictions)
    n = 0
    for c in conflicts:
        if c.unresolved:
            text = c.describe()
            if text not in existing:
                ctx.contradictions.append(text)
                existing.add(text)
                n += 1
    return n
