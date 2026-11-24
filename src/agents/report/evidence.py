"""Step 1: build the numbered evidence list (E1..) from memory contexts and tool results. Pure code, data in -> data out."""
from __future__ import annotations

import json
from typing import Optional

from schemas.memory import MemoryContext, MemoryItem
from schemas.plan import ExecutionPlan
from schemas.report import EvidenceItem, is_source_turn
from schemas.tools import ToolSelectorState

STORE_CHARS = 600   # stored description
PROMPT_CHARS = 300  # per item shown to the model
# B2A: how much evidence the MODEL is shown.  Measured, not guessed: the fixed part of the Report prompt (system prompt, output schema, request frame) is about 1,700 tokens; the harness's report
# ceiling is 5,000 tokens (harness.config.DEFAULT_AGENT_CONTEXT, inside a 6,144-token context with 1,000 reserved for the answer); one 300-character evidence line costs about 100 tokens (the
# conservative utf8/3 estimate).  That leaves about 3,300 tokens; 8,400 characters (about 2,800 tokens) keeps a margin of roughly 15%.  The old fixed 16 items were about 4,800 characters.
# The harness fitter (harness/compression.py) still runs on top and can only delete whole low-priority lines; without a harness this budget is the only guard.
PROMPT_ITEMS = 32                   # at most this many evidence lines ...
PROMPT_EVIDENCE_CHARS = 8400        # ... and at most this many characters of them
TURN_SHARE = 0.4                    # source turns are guaranteed up to this share of the budget, so a long retrieval of turns cannot push every memory out (nor the reverse)
EPISODES_PER_CLIENT = 16            # episodes kept per client in the evidence list (was 12); see pick_episodes for WHICH ones


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
                        valid_to=m.valid_to, client_id=m.client_id, memory_type=m.memory_type, event_date=m.event_date,
                        source_role=m.source_role, subject=m.subject or None, corrects_value=m.corrects_value)


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


DECISION_CERTAINTY = ("confirmed", "correction")        # stated as final (M1 has already capped anything the evidence does not support: selection never changes a certainty)


def is_decision(m: MemoryItem) -> bool:
    return m.certainty in DECISION_CERTAINTY


def is_change_record(m: MemoryItem) -> bool:
    """An episode that records that something CHANGED or was withdrawn: a correction, one naming the value it corrects, or one already superseded or disputed."""
    return m.certainty == "correction" or bool(m.corrects_value) or m.status in ("superseded", "disputed")


def is_chronology_critical(m: MemoryItem) -> bool:
    """What a reader cannot do without when told how a value developed: a decision or a change record.  (Exploratory discussion is context, never critical by itself.)"""
    return is_decision(m) or is_change_record(m)


def _val(m: MemoryItem) -> str:
    return " ".join((m.value_text or "").lower().split())


def _slot(m: MemoryItem) -> tuple:
    return (m.topic, m.subject or m.topic)


def episode_groups(items: list[MemoryItem], topics=None) -> list[int]:
    """The selection group of every episode (0 = keep first).  `items` are in chronological order.  An episode takes the LOWEST group it qualifies for.
      0  the latest decision of each slot (topic, subject): the current state;
      1  change records: corrections, episodes that name the value they correct, superseded / disputed ones;
      2  the episode a correction replaces (a correction that names the value it corrects: the latest earlier episode of the slot with that value; one that does not: the latest earlier
         DECISION with a different value);
      3  verified episodes (the integrity boundary's verdict; selection never creates one);
      4  decisions that a later decision REPLACED with a different value (the chain that explains a change);
      5  every other decision;
      6  one representative per (slot, value, certainty) of the tentative remainder (exploratory, hypothetical, unconfirmed, recommendation), latest first: each value that was only
         considered is represented before any value is represented twice;
      7  everything else on the plan's topics;
      8  episodes about topics the plan did not ask for.
    A confirmed decision therefore outranks an exploratory remark, a correction outranks an ordinary decision of its slot, and the decision a correction replaces is kept with the correction."""
    n = len(items)
    topics = topics if topics and any(m.topic in topics for m in items) else None            # a plan whose topics match no episode says nothing: everything counts as on-topic
    on = [(not topics) or m.topic in topics for m in items]
    g = [7] * n
    by_slot: dict[tuple, list[int]] = {}
    for i, m in enumerate(items):
        by_slot.setdefault(_slot(m), []).append(i)

    def put(i: int, k: int) -> None:
        g[i] = min(g[i], k)
    for idx in by_slot.values():
        decisions = [i for i in idx if is_decision(items[i])]
        if decisions:
            put(max(decisions, key=lambda i: (items[i].event_date or "", i)), 0)
        for a, b in zip(decisions, decisions[1:]):
            if _val(items[a]) != _val(items[b]):
                put(a, 4)
        for i in decisions:
            put(i, 5)
        for i in idx:
            m = items[i]
            if is_change_record(m):
                put(i, 1)
                before = [j for j in idx if j < i]
                if m.corrects_value:
                    hit = [j for j in before if _val(items[j]) == " ".join(m.corrects_value.lower().split())]
                else:
                    hit = [j for j in before if is_decision(items[j]) and _val(items[j]) != _val(m)]
                if hit:
                    put(max(hit), 2)
            if m.verification == "verified":
                put(i, 3)
        rep: dict[tuple, int] = {}
        for i in idx:
            if not is_decision(items[i]):
                rep[(_val(items[i]), items[i].certainty)] = i                       # the latest one wins (items are chronological)
        for i in rep.values():
            put(i, 6)
    return [g[i] if on[i] else 8 for i in range(n)]


def pick_episodes(episodes: list[MemoryItem], cap: int, topics=None) -> tuple[list[MemoryItem], list[MemoryItem]]:
    """(shown, omitted): at most `cap` of a client's episodes, `shown` in their ORIGINAL (chronological) order.

    Retrieval can return many more episodes than the report can show (a history question widens the cap to the whole timeline).  Taking the first `cap` keeps the OLDEST meetings and
    drops the latest decision; ranking tentative remarks above decisions (the Phase 0 rule) crowded the decision out the other way.  Episodes are ranked by `episode_groups` (current
    decision, change records, what a correction replaced, verified, the decision chain, other decisions, one representative per tentative value, the rest, off-topic) and, inside a
    group, by recency.  Ties are resolved by position and memory id, so the same input always gives the same output.  Selection only CHOOSES: no field of any episode is touched, so a
    tentative or unverified episode stays tentative and unverified.  Nothing is dropped silently: the caller receives `omitted`."""
    items = list(episodes)
    if cap <= 0:
        return [], items
    if len(items) <= cap:
        return items, []
    groups = episode_groups(items, set(topics) if topics else None)
    ranked = sorted(range(len(items)), key=lambda i: (groups[i], -i, items[i].memory_id))
    keep = set(ranked[:cap])
    return [m for i, m in enumerate(items) if i in keep], [m for i, m in enumerate(items) if i not in keep]


def build_evidence(plan: ExecutionPlan, memory: dict[str, MemoryContext], tools: ToolSelectorState) -> tuple[list[EvidenceItem], dict]:
    """Order: current memory -> tool results -> superseded history -> recent events. Returns (items with handles, removal record)."""
    allowed = set(plan.client_ids) or set(memory)
    removed: dict = {"ownership_removed": [], "episodes_omitted": [], "duplicate_source_turns": []}
    cur: list[EvidenceItem] = []
    hist: list[EvidenceItem] = []
    recent: list[EvidenceItem] = []
    tool_items: list[EvidenceItem] = []
    seen: set[str] = set()
    seen_turns: set[tuple] = set()

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
        own_eps = [m for m in ctx.episodes if owned(cid, m)]                   # ownership BEFORE selection: another client's episode must never take a slot (or be counted) at all
        shown, omitted = pick_episodes(own_eps, EPISODES_PER_CLIENT, plan.memory_topics)
        if omitted:                                           # said in the report (agent.py adds it to the uncertainties): the reader is told what the evidence list does not contain
            removed["episodes_omitted"].append(dict(client_id=cid, shown=len(shown), total=len(own_eps), critical=sum(is_chronology_critical(m) for m in omitted),
                                                    decisions=sum(is_decision(m) for m in omitted), ids=[m.memory_id for m in omitted]))
        for m in shown:
            if m.memory_id not in seen:
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
            for t in turns:
                key = (env.client_id, str(t["interaction_id"]), t["turn_index"])
                if key in seen_turns:                          # the same source turn read by two calls is ONE piece of evidence (the first, with its execution id, is kept)
                    removed["duplicate_source_turns"].append(f"{t['interaction_id']}#{t['turn_index']} (also in {ex.id})")
                    continue
                seen_turns.add(key)
                tool_items.append(_turn_item(ex, env, t))
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


def _line(e: EvidenceItem) -> str:
    tag = e.source_class + (", synthetic" if e.synthetic else "") + (f", {e.evidence_class}" if e.evidence_class and e.evidence_class != DEFINITIVE else "")
    return f"[{e.ref} {tag}] {trunc(e.description, PROMPT_CHARS)}"


def prompt_tier(e: EvidenceItem) -> int:
    """How much the model needs to SEE an evidence item (0 = first).  0: tool results and the semantic memories (current knowledge and its superseded history); 1: episodes that are
    decisions, change records or verified; 2: retrieved source turns (chosen with a spread, see select_for_prompt); 3: every other episode and unresolved item."""
    if e.source_class in ("operational_data", "tool_calculation") or e.memory_type == "semantic":
        return 0
    if is_source_turn(e):
        return 2
    if e.memory_type == "episodic" and (e.evidence_class == DEFINITIVE or e.certainty in DECISION_CERTAINTY or e.status in ("superseded", "disputed")):
        return 1
    return 3


def _turn_spread(turns: list[EvidenceItem]) -> list[EvidenceItem]:
    """Source turns in the order they should be shown when the budget cannot hold them all: round-robin over the meetings (oldest meeting first), the client's turns before the advisor's
    within a meeting, so every meeting is represented before any meeting is represented twice.  Deterministic."""
    by: dict[str, list[EvidenceItem]] = {}
    for e in sorted(turns, key=lambda e: (e.event_date or "", e.interaction_id or "", e.turn_index or 0)):
        by.setdefault(e.interaction_id or "", []).append(e)
    for v in by.values():
        v.sort(key=lambda e: (e.speaker != "Client", e.turn_index or 0))
    out, rounds = [], max((len(v) for v in by.values()), default=0)
    for r in range(rounds):
        out.extend(v[r] for v in by.values() if len(v) > r)
    return out


def select_for_prompt(evidence: list[EvidenceItem], max_items: int = PROMPT_ITEMS, max_chars: int = PROMPT_EVIDENCE_CHARS) -> tuple[list[EvidenceItem], list[EvidenceItem]]:
    """(shown, hidden): which evidence items the MODEL is shown, within `max_items` lines and `max_chars` characters; `shown` keeps the evidence list's own order and handles (E-numbers do not
    change, so a gap in the numbering is simply an item the model was not shown).  Priority: prompt_tier 0, then 1, then source turns, then 3.  Source turns are guaranteed up to TURN_SHARE
    of the budget (and are what is left over when memory needs less), so neither a long retrieval of turns nor a long list of episodes can push the other out.  An item is never cut in the
    middle: it is shown whole (its description is already bounded by PROMPT_CHARS) or it is hidden, and the caller is told which.  Hidden evidence stays in the report's evidence list."""
    cost = {e.ref: len(_line(e)) + 1 for e in evidence}
    tiers: dict[int, list[EvidenceItem]] = {k: [] for k in range(4)}
    for e in evidence:
        tiers[prompt_tier(e)].append(e)
    turns = _turn_spread(tiers[2])
    chosen: dict[str, EvidenceItem] = {}
    used = 0

    def take(items: list[EvidenceItem], chars: int, count: int) -> None:
        nonlocal used
        for e in items:
            if e.ref not in chosen and len(chosen) < count and used + cost[e.ref] <= chars:
                chosen[e.ref] = e
                used += cost[e.ref]
    turn_chars = min(int(max_chars * TURN_SHARE), sum(cost[e.ref] for e in turns))
    turn_count = min(int(max_items * TURN_SHARE), len(turns))
    take(tiers[0] + tiers[1], max_chars - turn_chars, max_items - turn_count)             # memory first, leaving the turns' guaranteed share
    take(turns, max_chars, max_items)                                                      # the turns, spread over the meetings
    take(tiers[0] + tiers[1] + tiers[3], max_chars, max_items)                             # whatever room is left goes to memory that did not fit
    shown = [e for e in evidence if e.ref in chosen]
    return shown, [e for e in evidence if e.ref not in chosen]


def prompt_lines(evidence: list[EvidenceItem]) -> list[str]:
    return [_line(e) for e in select_for_prompt(evidence)[0]]


def find(evidence: list[EvidenceItem], ref: str) -> Optional[EvidenceItem]:
    return next((e for e in evidence if e.ref == ref), None)
