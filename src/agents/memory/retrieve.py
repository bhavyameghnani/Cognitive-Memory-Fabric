"""Retrieval pipeline: client -> memory-type -> topic -> status -> temporal -> relevance ranking -> related memories -> MemoryContext.

Every store query is scoped to the requested client, so another client's memories are never returned.  Episodes are capped (default 8)
and the number omitted is reported; a client's full history is never returned.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from agents.memory import rules
from agents.memory.embeddings import EmbeddingError, cosine, default_embedder
from agents.memory.config import CERTAINTY_WEIGHT, RetrievalConfig
from agents.memory.store import MemoryStore, to_item
from agents.planner.topics import TOPICS, infer_topics
from schemas.memory import MemoryContext, MemoryItem
from schemas.plan import ExecutionPlan

OPEN_CERTAINTY = ("exploratory", "hypothetical", "unconfirmed", "recommendation")
STOP = {"what", "is", "his", "her", "their", "the", "a", "an", "and", "how", "did", "does", "do", "of", "to", "in", "on", "for", "it", "about", "was", "are", "current", "with"}


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", rules.norm(text)) if w not in STOP}


def relevance(row: dict, kw: set[str], sem: float = 0.0, semantic_weight: float = 0.0) -> float:
    """Ranking score = certainty weight + keyword overlap with content/subject + semantic_weight * embedding cosine (sem, clamped at 0).

    With the defaults (sem=0, semantic_weight=0) this is the original keyword-only ranking."""
    overlap = len(kw & keywords(f"{row['content']} {row['subject']} {row['topic'].replace('_', ' ')}"))
    return CERTAINTY_WEIGHT.get(row["certainty"], 0.2) + 0.25 * min(overlap, 4) + semantic_weight * max(sem, 0.0)


def doc_text(row: dict) -> str:
    return f"{row['topic'].replace('_', ' ')} {row['subject']}: {row['content']}"


def semantic_scores(query: str, rows: list[dict], cfg: RetrievalConfig) -> tuple[dict[str, float], Optional[str]]:
    """memory_id -> cosine(query, memory).  Returns ({}, note) when embeddings are off or the backend fails (ranking then falls back to keywords)."""
    if cfg.semantic_weight <= 0 or not rows:
        return {}, None
    try:
        emb = cfg.embedder or default_embedder()
        if emb is None:
            return {}, None
        vecs = emb.embed([query] + [doc_text(r) for r in rows])
    except EmbeddingError as e:
        return {}, f"embedding retrieval unavailable ({e}); ranked by keywords only"
    q = vecs[0]
    return {r["memory_id"]: cosine(q, v) for r, v in zip(rows, vecs[1:])}, None


def parse_as_of(time_period: Optional[str]) -> Optional[str]:
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", time_period or "")
    return m.group(1) if m else None


def _ord(r: dict) -> tuple:
    return (r.get("event_ts") or r.get("event_date") or "", r.get("turn_index") or 0)


def unresolved_items(episodes: list[dict]) -> list[dict]:
    """Open items: exploratory/hypothetical/unconfirmed/recommended episodes with no LATER confirmed or correction episode in the same slot."""
    out = []
    for e in episodes:
        if e["certainty"] not in OPEN_CERTAINTY or e["status"] == "superseded":
            continue
        slot = (e["topic"], e["subject"])
        if not any((x["topic"], x["subject"]) == slot and x["certainty"] in ("confirmed", "correction") and _ord(x) > _ord(e) for x in episodes):
            out.append(e)
    return out


def retrieve_context(store: MemoryStore, plan: ExecutionPlan, client_id: str, request_id: str, cfg: Optional[RetrievalConfig] = None,
                     as_of: Optional[str] = None, session_id: Optional[str] = None) -> MemoryContext:
    cfg = cfg or RetrievalConfig()
    as_of = as_of or parse_as_of(plan.time_period)
    cats = set(plan.required_memory_categories or ["semantic", "episodic"])
    topics = [t for t in plan.memory_topics if t in TOPICS and t != "other"] or infer_topics(plan.request_text)
    kw = keywords(plan.request_text)
    followup = plan.task_type == "followup_review"
    history_wanted = plan.needs_history or plan.task_type == "history"
    ctx = MemoryContext(client_id=client_id, request_id=request_id, topics=topics, as_of=as_of)
    scan_all = not topics
    if scan_all:
        ctx.notes.append("no topic identified: " + ("scanning all topics for open items only" if followup else "returning current values only; ask the advisor to narrow the topic"))
    tf = None if scan_all else topics

    all_eps = store.records(client_id, "episodic", topics=tf)
    if as_of:
        all_eps = [e for e in all_eps if e["event_date"] <= as_of]
    all_eps.sort(key=_ord)

    # semantic: current (+ history)
    sem = store.records(client_id, "semantic", topics=tf)
    sem.sort(key=lambda r: (r["topic"], r["version_no"] or 0))
    if as_of:
        valid = [s for s in sem if s["valid_from"] <= as_of and (s["valid_to"] is None or as_of < s["valid_to"])]
        hist = [s for s in sem if s["valid_from"] <= as_of and s not in valid]
    else:
        valid = [s for s in sem if s["status"] in ("active", "disputed")]
        hist = [s for s in sem if s["status"] == "superseded"]
    show_sem = "semantic" in cats
    cur_rows = valid if show_sem else []
    hist_rows = hist[-cfg.history_cap * 2:] if (show_sem and (history_wanted or "episodic" in cats)) else []

    # episodic: status filter, rank, cap, chronological
    ep_rows: list[dict] = []
    omitted = 0
    if "episodic" in cats and not (scan_all):
        pool = [e for e in all_eps if e["status"] in ("active", "unconfirmed") or (history_wanted and e["status"] == "superseded")]
        sims, emb_note = semantic_scores(plan.request_text, pool, cfg)
        if emb_note:
            ctx.notes.append(emb_note)
        ranked = sorted(pool, key=lambda r: -relevance(r, kw, sims.get(r["memory_id"], 0.0), cfg.semantic_weight))[: cfg.episode_cap]
        ep_rows = sorted(ranked, key=_ord)
        omitted = len(pool) - len(ep_rows)
    unres = unresolved_items([e for e in all_eps if e["status"] != "superseded"])[: cfg.unresolved_cap]

    # procedural: approved only, matching topics or request keywords
    proc_rows: list[dict] = []
    if "procedural" in cats:
        for p in store.records(client_id, "procedural", statuses=["active"], include_global=True):
            if p["approval_status"] != "approved":
                continue
            ptopics = set(filter(None, (p["proc_topics"] or "").split(",")))
            pkw = {k.strip().lower() for k in (p["proc_keywords"] or "").split(",") if k.strip()}
            if (ptopics & set(topics)) or any(k in rules.norm(plan.request_text) for k in pkw):
                proc_rows.append(p)

    # working: same session only, not expired
    work_rows: list[dict] = []
    if "working" in cats:
        sid = session_id or request_id
        store.expire_working()
        work_rows = [w for w in store.records(client_id, "working", statuses=["active"], session_id=sid) if w["expires_at"] > store.now()]

    rows_all = cur_rows + hist_rows + ep_rows + unres + proc_rows + work_rows
    ids = list({r["memory_id"] for r in rows_all})
    ev = store.evidence_map(ids)

    def items(rs: list[dict]) -> list[MemoryItem]:
        return [to_item(r, ev.get(r["memory_id"], [])) for r in rs]

    ctx.current, ctx.history, ctx.episodes = items(cur_rows), items(hist_rows), items(ep_rows)
    ctx.episodes_omitted = omitted
    ctx.unresolved, ctx.procedures, ctx.working = items(unres), items(proc_rows), items(work_rows)
    ctx.relations = store.relationships_among(ids)
    for r in ctx.relations:
        if r.rel_type == "related_to" and r.note.startswith("conflict:"):
            ctx.contradictions.append(r.note[len("conflict:"):].strip())
    ctx.contradictions += [f"{s['topic']} {s['value_text']} is marked disputed" for s in cur_rows if s["status"] == "disputed"]
    if omitted:
        ctx.notes.append(f"{omitted} further episode(s) omitted (cap {cfg.episode_cap}); narrow the topic or period to see them")
    for r in rows_all:
        if r["memory_type"] in ("episodic", "semantic") and rules.CONDITIONAL_ON_CLOSING.search(r["content"] + " " + " ".join(e.excerpt for e in ev.get(r["memory_id"], []))):
            ctx.notes.append(f"{r['memory_id']}: stated as conditional on the sale closing; it is not a current asset")
    ctx.notes = list(dict.fromkeys(ctx.notes))
    return ctx
