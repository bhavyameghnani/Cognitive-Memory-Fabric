"""Memory Orchestrator Agent: builds and maintains each client's evidence-linked, versioned memory and reconstructs the right slice for a task.

Public API
    MemoryOrchestrator(llm, store=None, conv=None, tracer=None, config=None)
    .init(reset=False)                                   create/reset schema cmf_memory and load the curated approved procedures
    .ingest_meeting(interaction_id, force=False)         stages 1-8 for one meeting (idempotent; one ingestion_log row per meeting)
    .ingest_client(client_id, interaction_ids=None)      all (or the given) meetings of one client, chronologically
    .retrieve(plan, request_id=None, as_of=None)         -> dict[client_id, MemoryContext]
    .store_working(session_id, plan, tool_summaries)     working memory for one request (TTL, same session only)
    .update_memory_lifecycle(memory_id, new_status, reason, actor, approval_status=None)   explicit human lifecycle action
The model proposes structured candidates only; it never sees SQL or credentials, and semantic memory is written only by consolidation.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from agents.memory import ingest as ing
from agents.memory import retrieve as ret
from agents.memory.config import GLOBAL_CLIENT, OrchestratorConfig
from agents.memory.consolidate import Consolidator
from agents.memory.integrity import Principal
from agents.memory.store import MemoryStore, h, iso
from llm.client import LLMClient
from orchestration.tracing import NullTracer, Tracer
from schemas.memory import MemoryContext
from schemas.plan import ExecutionPlan
from services.conversations import Conversations, shared

PROCEDURES_FILE = Path(__file__).with_name("procedures.json")
HUMAN_REQUIRED = {"", "llm", "model", "system", "ingestion", "consolidation", "assistant"}


class MemoryOrchestrator:
    def __init__(self, llm: LLMClient, store: Optional[MemoryStore] = None, conv: Optional[Conversations] = None, tracer: Optional[Tracer] = None,
                 config: Optional[OrchestratorConfig] = None, write_validator=None) -> None:
        self.llm, self.conv = llm, conv or shared()
        self.store = store or MemoryStore(conv=self.conv)
        self.tracer, self.cfg = tracer or NullTracer(), config or OrchestratorConfig()
        self.write_validator = write_validator          # optional harness MemoryWriteValidator (independent pre-persistence check)
        self.consolidator = Consolidator(self.store, validator=write_validator)
        # durable-write audit events go to this orchestrator's CURRENT tracer (the pipeline swaps it per request, so it is read lazily)
        self.store.boundary.emit_fn = lambda component, name, **payload: self.tracer.event(component, name, **payload)

    # ------------------------------------------------------------------ setup
    def init(self, reset: bool = False) -> dict:
        self.store.init(reset=reset)
        return {"procedures_loaded": self.load_curated_procedures()}

    def load_curated_procedures(self) -> int:
        """Curated, versioned file -> approved GLOBAL procedures (labelled curated_file; never derived from conversations)."""
        data = json.loads(PROCEDURES_FILE.read_text(encoding="utf-8"))
        n = 0
        for p in data["procedures"]:
            digest = h("curated", data["version"], p["key"], json.dumps(p["steps"]), n=40)
            rec = dict(memory_id="MEM-PRO-" + digest[:12], client_id=GLOBAL_CLIENT, memory_type="procedural", topic=(p["topics"] or ["other"])[0], subject=p["key"],
                       title="[curated] " + p["name"], content=f"Curated approved procedure: {p['name']}", certainty="n/a", source_role="System", status="active",
                       confidence=1.0, valid_from="2026-01-01", origin="curated_file", content_hash=digest)
            det = dict(procedure_name=p["name"], steps=json.dumps(p["steps"]), topics=",".join(p["topics"]), keywords=",".join(p["keywords"]), approval_status="approved",
                       approver=data["approver"], source_ref=f"{PROCEDURES_FILE.name} v{data['version']} (curated)")
            _, created = self.store.insert_memory(rec, det, [dict(source_type="curated", ref=f"{PROCEDURES_FILE.name}#{p['key']}", role="primary")],
                                                  reason="loaded from curated procedures file", principal=Principal.service("curated_loader", [GLOBAL_CLIENT]))
            n += int(created)
        return n

    # ------------------------------------------------------------------ ingestion
    def ingest_meeting(self, interaction_id: str, force: bool = False) -> dict:
        if interaction_id not in self.conv.meetings:
            raise KeyError(f"unknown interaction {interaction_id}")
        st = self.store
        if st.ingestion_status(interaction_id) == "completed" and not force:
            self.tracer.event("memory", "ingest_skipped", interaction=interaction_id)
            return dict(interaction_id=interaction_id, skipped=True)
        ctx = ing.MeetingContext.load(self.conv, interaction_id)
        st.start_ingestion(interaction_id, ctx.client_id, getattr(self.llm, "model", "unknown"), self.conv.transcript_hash(interaction_id))
        try:
            selected = ing.select_salient_turns(ctx.rows, self.cfg.filter)
            self.tracer.event("memory", "turns_selected", interaction=interaction_id, turns=[(t.turn_index, t.score, t.reasons) for t in selected])
            label = (self.conv.directory().get(ctx.client_id) or {}).get("first_name") or ctx.client_id
            res, err = ing.extract_memory_candidates(self.llm, ctx, selected, label, self.tracer)
            cands = [(c, "conversation_llm") for c in res.candidates] + [(c, "conversation_rule") for c in ing.rule_candidates(ctx)]
            decisions = ing.filter_memory_candidates(cands, ctx, self.conv, {t.turn_index for t in selected}, self.cfg.filter)
            why = {t.turn_index: ",".join(t.reasons) for t in selected}
            kept = 0
            for d in decisions:
                if d.kept and self.write_validator is not None:   # independent re-check; may reject or downgrade, never upgrade
                    self.write_validator.apply(d, self.write_validator.review(d, ctx))
                mem_id, decision, reason = None, d.decision, d.reason
                if d.kept:
                    cls = ing.classify_memory(d)
                    done = ing.persist_decision(st, ctx, d, cls, getattr(self.llm, "model", "unknown"), "selected: " + why.get(d.final.turn_index, "rule"),
                                                request_id=getattr(self.tracer, "request_id", "") or "")
                    mem_id = done[0][0]
                    if not any(c for _, c in done):
                        decision, reason = "merged", "already stored (same content hash)"
                    else:
                        kept += 1
                c = d.final or d.candidate
                st.add_candidate_audit(dict(interaction_id=interaction_id, client_id=ctx.client_id, turn_index=c.turn_index, origin=d.origin, topic=c.topic, subject=c.subject,
                                            statement=c.statement, value_text=c.value, excerpt=c.excerpt, certainty_in=d.certainty_in, certainty_out=d.certainty_out,
                                            decision=decision, reason="; ".join([reason] + d.notes), memory_id=mem_id))
            summary = self.consolidator.consolidate_client(ctx.client_id)
            st.finish_ingestion(interaction_id, "completed" if err is None else "degraded", len(selected), len(decisions), kept, len(decisions) - kept, err or "")
            self.tracer.event("memory", "ingested", interaction=interaction_id, candidates=len(decisions), stored=kept, consolidation=summary)
            return dict(interaction_id=interaction_id, client_id=ctx.client_id, skipped=False, turns_selected=len(selected), candidates=len(decisions), stored=kept,
                        decisions=[(x.origin, x.decision, x.certainty_in, x.certainty_out) for x in decisions], consolidation=summary, llm_error=err)
        except Exception as e:
            st.finish_ingestion(interaction_id, "failed", 0, 0, 0, 0, repr(e))
            raise

    def ingest_client(self, client_id: str, interaction_ids: Optional[list[str]] = None) -> list[dict]:
        if client_id not in self.conv.directory():
            raise ValueError(f"unknown client {client_id}")
        ids = interaction_ids or self.conv.interactions_for(client_id)
        foreign = [i for i in ids if self.conv.meeting(i)[0]["client_id"] != client_id]
        if foreign:
            raise ValueError(f"interactions {foreign} do not belong to {client_id}")
        return [self.ingest_meeting(i) for i in ids]

    # ------------------------------------------------------------------ retrieval
    def retrieve(self, plan: ExecutionPlan, request_id: Optional[str] = None, as_of: Optional[str] = None) -> dict[str, MemoryContext]:
        rid = request_id or plan.request_id
        out: dict[str, MemoryContext] = {}
        for cid in plan.client_ids:
            if cid not in self.conv.directory():
                out[cid] = MemoryContext(client_id=cid, request_id=rid, notes=[f"unknown client {cid}"])
                continue
            ctx = ret.retrieve_context(self.store, plan, cid, rid, self.cfg.retrieval, as_of=as_of, session_id=rid)
            if "working" in plan.required_memory_categories and ctx.current:
                made = False
                for m in ctx.current:
                    made |= self._working(rid, plan, cid, f"Analysis uses {m.topic.replace('_', ' ')} {m.value_text} (valid from {m.valid_from}), from {m.memory_id}",
                                          derived_from=m.memory_id, origin="orchestrator", ttl=self.cfg.retrieval.working_ttl_hours)[1]
                if made:
                    ctx = ret.retrieve_context(self.store, plan, cid, rid, self.cfg.retrieval, as_of=as_of, session_id=rid)
            out[cid] = ctx
            self.tracer.event("memory", "retrieved", client=cid, topics=ctx.topics, current=len(ctx.current), history=len(ctx.history), episodes=len(ctx.episodes),
                              omitted=ctx.episodes_omitted, unresolved=len(ctx.unresolved), procedures=len(ctx.procedures), working=len(ctx.working))
        return out

    # ------------------------------------------------------------------ working memory
    def _working(self, session_id: str, plan: ExecutionPlan, client_id: str, content: str, derived_from: Optional[str], origin: str, ttl: Optional[float],
                 ref: Optional[str] = None, topic: str = "other", known_refs: tuple = ()) -> tuple[str, bool]:
        ttl = self.cfg.retrieval.working_ttl_hours if ttl is None else ttl
        digest = h("working", session_id, client_id, content, n=40)
        rec = dict(memory_id="MEM-WRK-" + digest[:12], client_id=client_id, memory_type="working", topic=topic, title="working context", content=content[:600],
                   certainty="n/a", source_role="System", status="active", confidence=0.5, origin=origin, content_hash=digest, event_date=iso(self.store.clock())[:10])
        det = dict(session_id=session_id, plan_id=plan.plan_id, derived_from_id=derived_from, expires_at=iso(self.store.clock() + timedelta(hours=ttl)))
        ev = [dict(source_type="tool", ref=ref, role="primary")] if ref else []
        who = Principal.service("orchestrator", [client_id], request_id=session_id, evidence_refs=known_refs)     # tool refs resolve only against THIS request's executions
        return self.store.insert_memory(rec, det, ev, reason=f"working memory for session {session_id}", principal=who)

    @staticmethod
    def _summary_fields(s: Any) -> tuple[Optional[str], str, Optional[str], str]:
        """tool summary (str | dict | ToolExecution-like) -> (client_id, text, execution ref, topic)"""
        if isinstance(s, str):
            return None, s, None, "other"
        d = s.model_dump() if hasattr(s, "model_dump") else dict(s)
        res = d.get("result") or {}
        cid = d.get("client_id") or res.get("client_id") or (d.get("arguments") or {}).get("client_id")
        text = d.get("summary") or d.get("text") or d.get("content")
        if not text:
            text = f"Tool {d.get('tool', '?')} {d.get('status', '')}".strip()
            if res.get("data"):
                text += ": " + json.dumps(res["data"], default=str)[:300]
        return cid, f"[tool result, synthetic operational data] {text}", d.get("id") or d.get("execution_id") or d.get("ref"), d.get("topic", "other")

    def store_working(self, session_id: str, plan: ExecutionPlan, tool_summaries: list[Any], ttl_hours: Optional[float] = None) -> list[str]:
        """Task context and Tool Selector summaries become working memory for ONE session (default TTL 4 h).  Never promoted to semantic memory,
        never stored as a client statement.  A summary that names no client is attached only when the plan has exactly one client."""
        ids: list[str] = []
        known = tuple(r for r in (self._summary_fields(s)[2] for s in tool_summaries) if r)
        for cid in plan.client_ids:
            if cid in self.conv.directory():
                ids.append(self._working(session_id, plan, cid, f"Task context: {plan.task_type}; topics: {', '.join(plan.memory_topics) or 'none'}; intent: {plan.user_intent}",
                                         None, "orchestrator", ttl_hours)[0])
        for s in tool_summaries:
            cid, text, ref, topic = self._summary_fields(s)
            cid = cid or (plan.client_ids[0] if len(plan.client_ids) == 1 else None)
            if cid not in plan.client_ids:
                self.tracer.event("memory", "tool_summary_skipped", reason="no client in plan scope", client=cid)
                continue
            ids.append(self._working(session_id, plan, cid, text, None, "tool_summary", ttl_hours, ref=ref, topic=topic if topic in ret.TOPICS else "other", known_refs=known)[0])
        return ids

    def expire_working(self) -> list[str]:
        return self.store.expire_working(Principal.service("ttl_expiry", any_client=True))      # the background job: every client, genuinely expired rows only

    # ------------------------------------------------------------------ lifecycle (explicit, human)
    def update_memory_lifecycle(self, memory_id: str, new_status: str, reason: str, actor: str, approval_status: Optional[str] = None, *, principal: Optional[Principal] = None,
                                expected_version: Optional[int] = None) -> dict:
        """Explicit lifecycle actions only: dispute, expire, procedure approval/rejection.  Everything appends a lifecycle event; nothing is deleted.
        `principal` (an authenticated person) is REQUIRED; `actor` is kept for compatibility but the recorded actor is always principal.actor_id."""
        # the Principal is validated FIRST (nothing about the target is read before we know who is asking); a memory outside the caller's scope is reported exactly like a missing one
        row = self.store.boundary.lookup(memory_id, principal)
        if row is None:
            raise KeyError(memory_id)
        if principal.actor_id.strip().lower() in HUMAN_REQUIRED or not reason.strip():
            raise PermissionError("an explicit human actor and a reason are required")
        frm, mt = row["status"], row["memory_type"]
        with self.store.transaction():
            if mt == "procedural" and approval_status:
                if approval_status not in ("approved", "rejected") or frm != "candidate":
                    raise ValueError("only a candidate procedure can be approved or rejected")
                self.store.set_approval(memory_id, approval_status, principal.actor_id, principal=principal)
                self.store.set_status(memory_id, "active" if approval_status == "approved" else "expired", f"procedure {approval_status}: {reason}", principal.actor_id, principal=principal,
                                      expected_version=expected_version)
            elif new_status == "disputed" and mt in ("episodic", "semantic") and frm in ("active", "unconfirmed"):
                self.store.set_status(memory_id, "disputed", reason, principal.actor_id, principal=principal, expected_version=expected_version)
            elif new_status == "expired" and frm in ("active", "candidate", "disputed", "unconfirmed"):
                self.store.set_status(memory_id, "expired", reason, principal.actor_id, principal=principal, expected_version=expected_version)
            else:
                raise ValueError(f"transition {mt}:{frm} -> {new_status} is not an explicit lifecycle action")
        return dict(memory_id=memory_id, from_status=frm, to_status=self.store.get(memory_id)["status"])
