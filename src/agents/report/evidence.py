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


DEFINITIVE = "verified"            # the ONLY evidence class a finding may rest on as an established fact
NON_DEFINITIVE = ("unverified", "contradicted", "ambiguous", "open_conflict")
LABELS = {"unverified": "UNVERIFIED - needs confirmation, not an established client fact",
          "contradicted": "CONTRADICTED - disputed, cannot support a finding",
          "ambiguous": "AMBIGUOUS - exploratory / unconfirmed, not a decision",
          "open_conflict": "OPEN CONFLICT - a person must review it before it is relied on"}
_AMBIGUOUS_CERTAINTY = ("exploratory", "hypothetical", "unconfirmed", "recommendation")


def evidence_class(m: MemoryItem, conflicted: Optional[set] = None) -> str:
    """What the report may do with a retrieved memory.  Presence in the retrieval context is NOT proof of truth: only a memory the integrity boundary VERIFIED, that is not disputed,
    not one side of an open conflict and not itself hypothetical, can support a definitive statement."""
    if m.status in ("disputed",):
        return "contradicted"
    if conflicted and m.memory_id in conflicted:
        return "open_conflict"
    if m.verification != "verified":
        return "unverified"
    if m.certainty in _AMBIGUOUS_CERTAINTY or m.status in ("unconfirmed", "candidate"):
        return "ambiguous"
    return "verified"


def _mem_desc(kind: str, m: MemoryItem, cls: str = "verified") -> str:
    ev = m.evidence[0] if m.evidence else None
    bits = [kind, m.topic]
    if m.value_text:
        bits.append(f"value {m.value_text}")
    bits.append(f"{m.status}/{m.certainty}")
    bits.append(f"valid {m.valid_from or '?'} to {m.valid_to or 'open'}")
    if m.event_date:
        bits.append(f"on {m.event_date}")
    if cls in LABELS:
        bits.append("[" + LABELS[cls] + "]")
    head = "; ".join(bits)
    tail = f" (said by {ev.speaker} in {ev.interaction_id} turn {ev.turn_index})" if ev else ""
    return trunc(f"{head}: {m.content}{tail}", STORE_CHARS)


def _mem_item(kind: str, m: MemoryItem, cls: str, conflicted: Optional[dict] = None) -> EvidenceItem:
    ev = m.evidence[0] if m.evidence else None
    conflicted = conflicted or {}
    ecls = evidence_class(m, set(conflicted))
    return EvidenceItem(ref="", source_class=cls, description=_mem_desc(kind, m, ecls),
                        verification=m.verification, verification_basis=trunc(m.verification_basis, 300), memory_source_class=m.source_class, temporal_status=m.temporal_status,
                        evidence_class=ecls, conflict_ids=sorted(conflicted.get(m.memory_id, [])), interaction_id=ev.interaction_id if ev else None,
                        turn_index=ev.turn_index if ev else None, memory_id=m.memory_id, excerpt=trunc(ev.excerpt, 300) if ev else None,
                        synthetic=False, topic=m.topic, value=m.value_text, certainty=m.certainty, status=m.status, valid_from=m.valid_from,
                        valid_to=m.valid_to, client_id=m.client_id, memory_type=m.memory_type, event_date=m.event_date)


def _source_turns(env) -> list[dict]:
    """The turns of a conversation-mode get_source_evidence result (one turn, or several turns of one meeting), each only when it carries everything needed to cite it (nothing is filled in)."""
    if env.tool != "get_source_evidence" or not isinstance(env.data, dict) or not env.client_id:
        return []
    raw = [env.data["turn"]] if isinstance(env.data.get("turn"), dict) else [t for t in (env.data.get("turns") or [])]
    return [t for t in raw if isinstance(t, dict) and t.get("interaction_id") and isinstance(t.get("turn_index"), int) and t.get("speaker") and t.get("text")]


def _turn_item(ex, env, t: dict) -> EvidenceItem:
    prov = f"{env.provenance.source}{', synthetic' if env.provenance.synthetic else ''}"
    head = (f"Source conversation turn {t['interaction_id']} turn {t['turn_index']}, recorded speaker {t['speaker']} ({prov} record; "
            f"shows what the turn contains, not that it is true): ")
    return EvidenceItem(ref="", source_class="conversation_evidence", description=trunc(head + str(t["text"]), STORE_CHARS), interaction_id=str(t["interaction_id"]),
                        turn_index=t["turn_index"], excerpt=str(t["text"]), speaker=str(t["speaker"]), provenance=prov, event_date=env.as_of, tool_execution_id=ex.id,
                        synthetic=bool(env.provenance.synthetic), client_id=env.client_id)


def pick_episodes(episodes: list[MemoryItem], cap: int) -> list[MemoryItem]:
    """At most `cap` of a client's episodes, kept in their (chronological) order.  Retrieval can return many more than the report shows (a history question widens the cap to the
    whole timeline), so the choice matters: VERIFIED episodes first, then the most recent of the rest.  Taking the first `cap` would keep only the oldest meetings and drop the
    latest decision."""
    if len(episodes) <= cap:
        return list(episodes)
    order = {id(m): i for i, m in enumerate(episodes)}
    verified = [m for m in episodes if evidence_class(m) == DEFINITIVE]
    rest = [m for m in reversed(episodes) if evidence_class(m) != DEFINITIVE]
    chosen = (verified + rest)[:cap]
    return sorted(chosen, key=lambda m: order[id(m)])


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

    review: list[EvidenceItem] = []          # everything that is NOT verified, visible but last and labelled (P1.4.3)
    for cid, ctx in memory.items():
        if cid not in allowed or ctx.client_id not in allowed or ctx.client_id != cid:
            removed["ownership_removed"].append(f"memory context for {cid}")
            continue
        conflicted: dict[str, list[str]] = {}
        for c in ctx.conflicts:
            for k in ("memory_id", "other_memory_id"):
                conflicted.setdefault(c.get(k), []).append(c.get("conflict_id"))

        def put(kind: str, m: MemoryItem, cls: str, bucket: list) -> None:
            it = _mem_item(kind, m, cls, conflicted)
            (bucket if it.evidence_class == DEFINITIVE else review).append(it)
        for m in ctx.current:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                put("Current knowledge", m, "memory", cur)
        for m in ctx.history:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                put("Superseded earlier knowledge", m, "memory", hist)
        for m in ctx.unresolved:
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                recent.append(_mem_item("Unresolved item", m, "memory", conflicted))
        for m in pick_episodes(ctx.episodes, EPISODES_PER_CLIENT):
            if m.memory_id not in seen and owned(cid, m):
                seen.add(m.memory_id)
                recent.append(_mem_item("Episode", m, "conversation_evidence" if m.evidence else "memory", conflicted))

    for ex in tools.executions:
        if ex.status != "success" or ex.result is None:
            continue
        env = ex.result
        if env.client_id and env.client_id not in allowed:
            removed["ownership_removed"].append(f"{ex.id} (client {env.client_id})")
            continue
        if env.tool == "get_source_evidence" and isinstance(env.data, dict) and "search" in env.data:
            continue                                           # a search lists WHERE a phrase occurs; it is a discovery aid, not evidence of anything (the retrieved turns are the evidence)
        turns = _source_turns(env)
        if turns:                                              # conversation-mode get_source_evidence: source records, not operational values
            tool_items.extend(_turn_item(ex, env, t) for t in turns)
            continue
        calc = env.tool.startswith(("calculate_", "compare_"))
        data = trunc(json.dumps(env.data, default=str, ensure_ascii=False), 380)
        args = trunc(json.dumps(ex.arguments, default=str, ensure_ascii=False), 120)
        desc = trunc(f"{env.tool}({args}) as of {env.as_of or 'unspecified date'}: {data}", STORE_CHARS)
        tool_items.append(EvidenceItem(ref="", source_class="tool_calculation" if calc else "operational_data", description=desc,
                                       tool_execution_id=ex.id, synthetic=True, client_id=env.client_id or (plan.client_ids[0] if len(plan.client_ids) == 1 else None)))

    items = cur + tool_items + hist + recent + review
    for i, it in enumerate(items, 1):
        it.ref = f"E{i}"
    return items, removed


def prompt_lines(evidence: list[EvidenceItem]) -> list[str]:
    out = []
    for e in evidence[:PROMPT_ITEMS]:
        tag = e.source_class + (", synthetic" if e.synthetic else "") + (f", {e.evidence_class}" if e.evidence_class and e.evidence_class != DEFINITIVE else "")
        out.append(f"[{e.ref} {tag}] {trunc(e.description, PROMPT_CHARS)}")
    return out


def find(evidence: list[EvidenceItem], ref: str) -> Optional[EvidenceItem]:
    return next((e for e in evidence if e.ref == ref), None)
