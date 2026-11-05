"""Step 1: build the numbered evidence list (E1..) from memory contexts and tool results. Pure code, data in -> data out."""
from __future__ import annotations

import json
from typing import Optional

from schemas.memory import MemoryContext, MemoryItem
from schemas.plan import ExecutionPlan
from schemas.report import EvidenceItem
from schemas.tools import ToolSelectorState

STORE_CHARS = 600   # stored description
PROMPT_CHARS = 300  # per item shown to the model
PROMPT_ITEMS = 16
EPISODES_PER_CLIENT = 12


def trunc(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _mem_desc(kind: str, m: MemoryItem) -> str:
    ev = m.evidence[0] if m.evidence else None
    bits = [kind, m.topic]
    if m.value_text:
        bits.append(f"value {m.value_text}")
    bits.append(f"{m.status}/{m.certainty}")
    bits.append(f"valid {m.valid_from or '?'} to {m.valid_to or 'open'}")
    if m.event_date:
        bits.append(f"on {m.event_date}")
    head = "; ".join(bits)
    tail = f" (said by {ev.speaker} in {ev.interaction_id} turn {ev.turn_index})" if ev else ""
    return trunc(f"{head}: {m.content}{tail}", STORE_CHARS)


def _mem_item(kind: str, m: MemoryItem, cls: str) -> EvidenceItem:
    ev = m.evidence[0] if m.evidence else None
    return EvidenceItem(ref="", source_class=cls, description=_mem_desc(kind, m), interaction_id=ev.interaction_id if ev else None,
                        turn_index=ev.turn_index if ev else None, memory_id=m.memory_id, excerpt=trunc(ev.excerpt, 300) if ev else None,
                        synthetic=False, topic=m.topic, value=m.value_text, certainty=m.certainty, status=m.status, valid_from=m.valid_from,
                        valid_to=m.valid_to, client_id=m.client_id, memory_type=m.memory_type, event_date=m.event_date)


def build_evidence(plan: ExecutionPlan, memory: dict[str, MemoryContext], tools: ToolSelectorState) -> tuple[list[EvidenceItem], dict]:
    """Order: current memory -> tool results -> superseded history -> recent events. Returns (items with handles, removal record)."""
    allowed = set(plan.client_ids) or set(memory)
    removed: dict = {"ownership_removed": []}
    cur: list[EvidenceItem] = []
    hist: list[EvidenceItem] = []
    recent: list[EvidenceItem] = []
    tool_items: list[EvidenceItem] = []
    seen: set[str] = set()

    def owned(cid: str, m: MemoryItem) -> bool:
        if m.client_id != cid:
            removed["ownership_removed"].append(f"{m.memory_id} (client {m.client_id} in context of {cid})")
            return False
        return True

    for cid, ctx in memory.items():
        if cid not in allowed or ctx.client_id not in allowed or ctx.client_id != cid:
            removed["ownership_removed"].append(f"memory context for {cid}")
            continue
        for m in ctx.current:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                cur.append(_mem_item("Current knowledge", m, "memory"))
        for m in ctx.history:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                hist.append(_mem_item("Superseded earlier knowledge", m, "memory"))
        for m in ctx.unresolved:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                recent.append(_mem_item("Unresolved item", m, "memory"))
        for m in ctx.episodes[:EPISODES_PER_CLIENT]:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                recent.append(_mem_item("Episode", m, "conversation_evidence" if m.evidence else "memory"))

    for ex in tools.executions:
        if ex.status != "success" or ex.result is None:
            continue
        env = ex.result
        if env.client_id and env.client_id not in allowed:
            removed["ownership_removed"].append(f"{ex.id} (client {env.client_id})")
            continue
        calc = env.tool.startswith(("calculate_", "compare_"))
        data = trunc(json.dumps(env.data, default=str, ensure_ascii=False), 380)
        args = trunc(json.dumps(ex.arguments, default=str, ensure_ascii=False), 120)
        desc = trunc(f"{env.tool}({args}) as of {env.as_of or 'unspecified date'}: {data}", STORE_CHARS)
        tool_items.append(EvidenceItem(ref="", source_class="tool_calculation" if calc else "operational_data", description=desc,
                                       tool_execution_id=ex.id, synthetic=True, client_id=env.client_id or (plan.client_ids[0] if len(plan.client_ids) == 1 else None)))

    items = cur + tool_items + hist + recent
    for i, it in enumerate(items, 1):
        it.ref = f"E{i}"
    return items, removed


def prompt_lines(evidence: list[EvidenceItem]) -> list[str]:
    out = []
    for e in evidence[:PROMPT_ITEMS]:
        tag = e.source_class + (", synthetic" if e.synthetic else "")
        out.append(f"[{e.ref} {tag}] {trunc(e.description, PROMPT_CHARS)}")
    return out


def find(evidence: list[EvidenceItem], ref: str) -> Optional[EvidenceItem]:
    return next((e for e in evidence if e.ref == ref), None)
