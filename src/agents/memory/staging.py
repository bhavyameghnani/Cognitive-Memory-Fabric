"""Staged memory formation (Milestone M2): extraction PROPOSES, validation CHECKS, a person APPROVES, a separate identity PROMOTES.

    LLM / rules -> fact checks + evidence policy (M1) -> PROPOSAL (extracted) -> validate -> validated | rejected | unresolved
                -> submit -> pending_approval -> human approve -> approved -> promote (identity `promotion`, through the integrity boundary) -> promoted -> production memory

Four distinct operations, four distinct identities:
  * propose / validate / submit   the `ingestion` service.  In staged mode the integrity boundary REFUSES it a direct memory write, and a database trigger refuses any
                                   conversation-derived memory row that lacks an APPROVED proposal for exactly that memory.
  * approve / reject / expire     an authenticated PERSON (Principal.user, authenticated=True, not a reserved name, in the client's scope).  A model, a script or any service
                                   identity is refused.  An optional `authorize` callback lets the caller apply its role check (the harness PermissionGate `approve`).  The decision is bound to the
                                   exact content hash that was reviewed, and the approver cannot raise a certainty: the ceiling was fixed from the evidence at extraction.
  * promote                       the `promotion` service, only for an approved proposal, re-validating the source first, inside ONE transaction (the memory, the state change and the audit
                                   event commit together or not at all).  Idempotent.
Semantic memory is never proposed: it is still written only by consolidation, from promoted (client-attributable) episodes.

What this does NOT do: it does not judge whether a claim is TRUE.  `validated` means the structural evidence checks passed; `approved` means a person decided; neither is a statement of
semantic accuracy.  A model's confidence is stored for audit and is never an input to validation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from agents.memory import attribution, ingest as ing, rules, support
from agents.memory.errors import EvidenceError, MemoryWriteRejected, ScopeDenied
from agents.memory.integrity import Principal, value_in
from agents.memory.proposal_states import STATES, TERMINAL, TRANSITIONS
from agents.memory.store import MemoryStore

_WS = re.compile(r"\s+")


# ------------------------------------------------------------------------------------------------------------------------------------ exact offsets
@dataclass(frozen=True)
class Match:
    method: str                      # exact | whitespace_normalised | unresolved
    start: Optional[int] = None      # offsets into the ORIGINAL turn text, None (never invented) when unresolved
    end: Optional[int] = None
    reason: str = ""


def locate_excerpt(text: str, excerpt: str) -> Match:
    """Where does `excerpt` sit in the ORIGINAL text?  exact: it occurs exactly once.  whitespace_normalised: it occurs exactly once after collapsing whitespace runs on both sides, and
    the offsets are mapped back to the original characters and verified.  unresolved: not found, more than one place, empty, only a letter-case match, or a mapping that does not
    verify.  No offset is ever guessed."""
    if not isinstance(text, str) or not isinstance(excerpt, str) or not excerpt.strip():
        return Match("unresolved", reason="empty_or_not_text")

    def all_at(hay: str, needle: str) -> list:
        out, i = [], hay.find(needle)
        while i != -1:
            out.append(i)
            i = hay.find(needle, i + 1)
        return out
    hits = all_at(text, excerpt)
    if len(hits) == 1:
        return Match("exact", hits[0], hits[0] + len(excerpt)) if text[hits[0]:hits[0] + len(excerpt)] == excerpt else Match("unresolved", reason="verification_failed")
    if len(hits) > 1:
        return Match("unresolved", reason="ambiguous_multiple_exact_matches")
    norm, idx, prev = [], [], False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev:
                norm.append(" ")
                idx.append(i)
            prev = True
        else:
            norm.append(ch)
            idx.append(i)
            prev = False
    n, target = "".join(norm), _WS.sub(" ", excerpt).strip()
    hits = all_at(n, target)
    if len(hits) == 1:
        s, e = idx[hits[0]], idx[hits[0] + len(target) - 1] + 1
        if _WS.sub(" ", text[s:e]).strip() == target and not text[s].isspace() and not text[e - 1].isspace():
            return Match("whitespace_normalised", s, e, "matched after collapsing whitespace")
        return Match("unresolved", reason="whitespace_mapping_failed_verification")
    if len(hits) > 1:
        return Match("unresolved", reason="ambiguous_multiple_whitespace_normalised_matches")
    if n.lower().find(target.lower()) != -1 and len(n.lower()) == len(n):
        return Match("unresolved", reason="case_only_difference")
    return Match("unresolved", reason="not_found")


@dataclass
class Promotion:
    proposal_id: str
    memory_id: str
    created: bool                    # False when the memory already existed (duplicate promotion) or the proposal was already promoted
    idempotent: bool = False


# ------------------------------------------------------------------------------------------------------------------------------------ the service
class Proposals:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    # ---- formation mode (the one-way latch) -----------------------------------------------------------------------------------------------------
    def formation_mode(self) -> str:
        return self.store.boundary._formation_mode()

    def set_staged(self, actor: str) -> None:
        """direct -> staged (safe direction, idempotent).  staged -> direct is refused by the database (offline maintenance only)."""
        if self.formation_mode() != "staged":
            with self.store.transaction():
                self.store.execute("UPDATE memory_formation_mode SET mode='staged', changed_at=?, changed_by=? WHERE singleton=1", (self.store.now(), actor or "operator"))

    # ---- plumbing ---------------------------------------------------------------------------------------------------------------------------------
    def _row(self, pid: str) -> Optional[dict]:
        r = self.store.query("SELECT * FROM memory_proposals WHERE proposal_id=?", (pid,))
        return r[0] if r else None

    def _visible(self, principal: Any, pid: str, op: str) -> tuple:
        """(Principal, row) - the proposal only if it is inside the caller's client scope.  'Not yours' and 'does not exist' are the same error."""
        who = self.store.boundary.authenticate(principal, op, None)              # identity first; nothing about any proposal is read before we know who asks
        row = self._row(pid)
        if row is None or not who.covers(row["client_id"]):
            raise ScopeDenied("proposal_not_found", "no such proposal in the actor's client scope")       # 'not yours' and 'does not exist' are the same answer
        return who, row

    def _event(self, row: dict, to: str, kind: str, actor: str, reason: str, detail: Optional[dict] = None, **updates) -> None:
        if to not in TRANSITIONS.get(row["state"], ()):
            raise MemoryWriteRejected("invalid_transition", f"{row['state']} -> {to} is not a legal proposal transition")
        now = self.store.now()
        self.store.execute("INSERT INTO memory_proposal_events (proposal_id, from_state, to_state, actor_kind, actor_id, reason, detail_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (row["proposal_id"], row["state"], to, kind, actor, reason, json.dumps(detail or {}, default=str), now))
        sets, vals = ["state=?", "state_changed_at=?"], [to, now]
        for k, v in updates.items():
            sets.append(f"{k}=?")
            vals.append(v)
        n = self.store.execute(f"UPDATE memory_proposals SET {', '.join(sets)} WHERE proposal_id=? AND state=?", (*vals, row["proposal_id"], row["state"]))
        if n != 1:
            raise MemoryWriteRejected("proposal_changed", "the proposal changed under this operation")

    # ---- 1. propose -------------------------------------------------------------------------------------------------------------------------------
    def propose(self, ctx: Any, d: Any, cls: Any, *, model: str, selection_note: str = "", request_id: str = "", extraction: Optional[dict] = None) -> list[str]:
        """Store the rows a direct write WOULD have stored, as proposals in state `extracted`.  Nothing here touches memory_records.  Idempotent (same content -> same proposal)."""
        principal = Principal.service("ingestion", [ctx.client_id], request_id=request_id)
        self.store.boundary.authenticate(principal, "propose", ctx.client_id)
        before = d.final.certainty if d.final is not None else None
        ing.enforce_policy(d, ctx)
        if d.final is not None and d.final.certainty != before:
            cls = ing.classify_memory(d)
        ids = []
        with self.store.transaction():
            for mtype, rec, det, ev in ing.memory_rows(ctx, d, cls, model, selection_note):
                ids.append(self._create(ctx, d, mtype, rec, det, ev, extraction or {}))
        return ids

    def _create(self, ctx: Any, d: Any, mtype: str, rec: dict, det: dict, ev: list, extraction: dict) -> str:
        pid = "PRP-" + rec["content_hash"][:20]
        if self._row(pid) is not None:
            return pid
        rows = []
        for e in ev:                                                          # every cited row keeps ITS OWN offsets, computed against the original turn text
            t = ctx.turn(int(e["turn_index"])) or {}
            m = locate_excerpt(t.get("utterance", ""), e["excerpt"])
            rows.append(dict(e, excerpt_start=m.start, excerpt_end=m.end, match_method=m.method, match_reason=m.reason))
        p = rows[0]
        c = d.final
        ext = dict(extraction, model=model_of(rec), origin=d.origin, decision=d.decision, reason=d.reason, notes=list(d.notes), certainty_in=d.certainty_in, certainty_out=d.certainty_out,
                   needs_review=bool(d.needs_review), calibration_confidence=rec.get("confidence"), formed_for=ctx.interaction_id)
        now = self.store.now()
        self.store.execute(
            "INSERT INTO memory_proposals (proposal_id, client_id, memory_type, topic, subject, statement, value_text, corrects_value, certainty, source_role, attribution, event_type, origin, "
            "interaction_id, turn_index, excerpt, excerpt_start, excerpt_end, match_method, evidence_json, record_json, details_json, extraction_json, content_hash, target_memory_id, "
            "state, created_by, formed_at, state_changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'extracted','ingestion',?,?)",
            (pid, rec["client_id"], mtype, rec["topic"], rec.get("subject") or "", rec["content"], rec.get("value_text"), rec.get("corrects_value"), rec["certainty"], rec["source_role"],
             d.attribution or attribution.classify_attribution(c.speaker_role, c.excerpt, origin=d.origin), det.get("event_type") or "procedure", rec["origin"], p["interaction_id"],
             int(p["turn_index"]), p["excerpt"], p["excerpt_start"], p["excerpt_end"], p["match_method"], json.dumps(rows), json.dumps(rec, default=str), json.dumps(det, default=str),
             json.dumps(ext, default=str), rec["content_hash"], rec["memory_id"], now, now))
        self.store.execute("INSERT INTO memory_proposal_events (proposal_id, from_state, to_state, actor_kind, actor_id, reason, detail_json, created_at) VALUES (?,NULL,'extracted','service',?,?,?,?)",
                           (pid, "ingestion", "proposed by extraction", json.dumps({"interaction": ctx.interaction_id}), now))
        return pid

    # ---- 2. validate ------------------------------------------------------------------------------------------------------------------------------
    def validate(self, proposal_id: str, principal: Any) -> dict:
        """Run the structural checks and the semantic-support gate.  fail -> rejected, offsets unresolved -> unresolved, else validated.  NEVER approves or promotes."""
        who, row = self._visible(principal, proposal_id, "validate")
        if row["state"] != "extracted":
            raise MemoryWriteRejected("invalid_state", f"only an extracted proposal can be validated (this one is {row['state']})")
        results = self.checks(row)
        fails = [r for r in results if r["result"] == "fail"]
        unres = [r for r in results if r["result"] == "unresolved"]
        warns = [r for r in results if r["result"] == "warn"]
        to = "rejected" if fails else "unresolved" if unres else "validated"
        why = ("validation failed: " + "; ".join(f"{r['check']}: {r['detail']}" for r in fails)) if fails else \
              ("unresolved: " + "; ".join(f"{r['check']}: {r['detail']}" for r in unres)) if unres else "all required checks passed (structural evidence only; not an approval)"
        with self.store.transaction():
            self.store.execute("UPDATE memory_proposals SET validation_json=?, unresolved_json=? WHERE proposal_id=?",
                               (json.dumps(results), json.dumps([r for r in results if r["result"] in ("unresolved", "warn")]), proposal_id))
            self._event(row, to, "service", who.actor_id, why, {"warnings": len(warns)})
        return dict(proposal_id=proposal_id, state=to, results=results)

    def checks(self, row: dict) -> list[dict]:
        """The validation checks, each {check, kind (structural|semantic), result (pass|fail|warn|unresolved), detail}.  Deterministic; no model is consulted."""
        st, conv = self.store, self.store.conv
        out: list[dict] = []

        def add(check, kind, result, detail=""):
            out.append(dict(check=check, kind=kind, result=result, detail=detail))
        ev = json.loads(row["evidence_json"])
        rec, det = json.loads(row["record_json"]), json.loads(row["details_json"])
        client = row["client_id"]
        turns = [(e, conv.turn(e["interaction_id"], int(e["turn_index"]))) for e in ev]
        # 1 client ownership, 2 source existence
        missing = [e for e, t in turns if t is None]
        add("source_existence", "structural", "fail" if missing else "pass", f"{len(missing)} cited turn(s) do not exist" if missing else "every cited turn exists")
        foreign = [e for e, t in turns if t is not None and t["client_id"] != client]
        add("client_ownership", "structural", "fail" if foreign else "pass", "a cited turn belongs to another client" if foreign else "every cited turn belongs to the proposal's client")
        if missing or foreign:
            return out + [dict(check="remaining_checks", kind="structural", result="fail", detail="not run: the evidence does not resolve inside this client")]
        # 3 evidence traceability + exact offsets
        bad = []
        for e, t in turns:
            try:
                st.verify_evidence(client, dict(e))
            except EvidenceError as x:
                bad.append(str(x))
            else:
                m = locate_excerpt(t["utterance"], e["excerpt"])
                if (m.method, m.start, m.end) != (e["match_method"], e["excerpt_start"], e["excerpt_end"]):
                    bad.append("the source text no longer matches the recorded offsets")
        add("evidence_traceability", "structural", "fail" if bad else "pass", "; ".join(bad) if bad else "every excerpt verifies in its cited turn")
        p = ev[0]
        if row["match_method"] == "unresolved":
            add("exact_offsets", "structural", "unresolved", f"the excerpt could not be located exactly once in the original text ({p.get('match_reason')}); no offsets were invented")
        else:
            add("exact_offsets", "structural", "pass", f"{row['match_method']} offsets {row['excerpt_start']}..{row['excerpt_end']} computed against the original text")
        # 4 speaker attribution
        speaker, text = turns[0][1]["speaker"], turns[0][1]["utterance"]
        cls_ok = attribution.classify_attribution(speaker, row["excerpt"], origin=row["origin"]) == row["attribution"]
        add("speaker_attribution", "structural", "pass" if speaker == row["source_role"] and cls_ok else "fail",
            f"source speaker {speaker!r}, attribution {row['attribution']}" if speaker == row["source_role"] and cls_ok else f"the turn is spoken by {speaker!r}; the proposal says {row['source_role']!r} / {row['attribution']}")
        # 5 certainty ceiling (M1 policy recomputed from the data)
        v = attribution.assess(row["certainty"], speaker, row["excerpt"], text, [dict(speaker=t["speaker"], excerpt=e["excerpt"]) for e, t in turns[1:]], row["origin"], row["value_text"])
        add("certainty_ceiling", "structural", "fail" if v.certainty != row["certainty"] else "pass",
            "certainty exceeds the evidence ceiling: " + "; ".join(v.notes) if v.certainty != row["certainty"] else f"{row['certainty']} is within the ceiling of this evidence")
        # 6 unsupported inference (statement and value must be supported by the cited sentence)
        excerpts = [e["excerpt"] for e in ev]
        open_item = row["certainty"] in ("exploratory", "hypothetical", "recommendation", "unconfirmed")
        ctxs = f"{row['topic']} {row['subject']}".replace("_", " ")
        if row["value_text"] and not any(value_in(row["value_text"], x) for x in excerpts):
            add("unsupported_inference", "semantic", "fail", "the stated value is not supported by the cited text")
        else:
            ok, why = support.claim_supported(row["statement"], row["value_text"], excerpts, verbatim=(row["origin"] == "conversation_rule"), allow_noncurrent=open_item, context=ctxs)
            add("unsupported_inference", "semantic", "pass" if ok else "fail", "the statement is lexically supported by the cited text (the paraphrase is not verified)" if ok else why)
        # 7 contradiction with existing memories (advisory: a person decides)
        clash = [m for m in st.records(client, topics=[row["topic"]]) if m["memory_type"] in ("episodic", "semantic") and (m["subject"] or m["topic"]) == (row["subject"] or row["topic"])
                 and m["status"] in ("active", "unconfirmed") and m["certainty"] in ("confirmed", "correction") and row["value_text"] and m["value_text"] and m["value_text"] != row["value_text"]]
        add("contradiction_with_existing", "semantic", "warn" if clash else "pass",
            ("differs from " + ", ".join(f"{m['memory_id']} ({m['value_text']})" for m in clash[:3]) + ": review before approving") if clash else "no existing current value of this slot differs")
        # 8 temporal consistency
        ts = turns[0][1]["interaction_datetime"]
        okd = rec.get("event_date") == ts[:10] and (row["memory_type"] != "episodic" or det.get("event_ts") == ts) and ts[:10] <= st.now()[:10]
        add("temporal_consistency", "structural", "pass" if okd else "fail", "the event date is the cited turn's own date" if okd else "the event date/time does not match the cited turn (or lies in the future)")
        # 9 memory type requirements
        probs = []
        if row["memory_type"] == "episodic":
            if (rec["status"] == "unconfirmed") != (row["certainty"] == "unconfirmed"):
                probs.append("status does not follow certainty")
            if det.get("interaction_id") != p["interaction_id"] or int(det.get("turn_index", -1)) != int(p["turn_index"]):
                probs.append("episodic details do not point at the cited turn")
        else:
            if det.get("approval_status") != "proposed" or rec["status"] != "candidate":
                probs.append("a procedure enters as a proposal")
        add("memory_type_requirements", "structural", "fail" if probs else "pass", "; ".join(probs) or f"{row['memory_type']} requirements met")
        # 10 duplicates
        dup_mem = st.query("SELECT memory_id FROM memory_records WHERE client_id=? AND memory_type=? AND content_hash=?", (client, row["memory_type"], row["content_hash"]))
        dup_p = st.query("SELECT proposal_id FROM memory_proposals WHERE client_id=? AND proposal_id<>? AND interaction_id=? AND turn_index=? AND topic=? AND subject=? AND coalesce(value_text,'')=coalesce(?,'') "
                         "AND state NOT IN ('rejected','expired')", (client, row["proposal_id"], row["interaction_id"], row["turn_index"], row["topic"], row["subject"], row["value_text"]))
        add("duplicate_candidates", "structural", "fail" if dup_mem else "warn" if dup_p else "pass",
            f"already stored as {dup_mem[0]['memory_id']}" if dup_mem else f"another open proposal covers this claim: {dup_p[0]['proposal_id']}" if dup_p else "no duplicate")
        # 11 supersession safety: staged episodes never supersede anything (consolidation owns versions)
        add("supersession_safety", "structural", "fail" if rec.get("supersedes_id") or rec.get("valid_to") else "pass",
            "a proposal may not carry supersession or validity windows" if rec.get("supersedes_id") or rec.get("valid_to") else "no supersession is requested")
        return out

    # ---- 3. submit / approve / reject / expire ---------------------------------------------------------------------------------------------------------
    def submit(self, proposal_id: str, principal: Any) -> None:
        who, row = self._visible(principal, proposal_id, "submit")
        if row["state"] != "validated":
            raise MemoryWriteRejected("invalid_state", f"only a validated proposal can be submitted for approval (this one is {row['state']})")
        with self.store.transaction():
            self._event(row, "pending_approval", "service", who.actor_id, "submitted for human approval")

    def advance(self, proposal_id: str, principal: Any) -> str:
        """validate, and submit when it validated.  Never approves.  Idempotent: a proposal that is already past `extracted` is left as it is."""
        _, row = self._visible(principal, proposal_id, "validate")
        if row["state"] != "extracted":
            return row["state"]
        res = self.validate(proposal_id, principal)
        if res["state"] == "validated":
            self.submit(proposal_id, principal)
            return "pending_approval"
        return res["state"]

    def _human(self, principal: Any, proposal_id: str, authorize: Optional[Callable]) -> tuple:
        who, row = self._visible(principal, proposal_id, "proposal_decision")
        if who.kind != "user" or not who.authenticated:
            raise ScopeDenied("human_required", "only an authenticated person can decide a proposal")
        if authorize is not None and not authorize(who, row):
            raise ScopeDenied("approval_not_authorised", "the caller's role may not decide this proposal")
        return who, row

    def _decide(self, row: dict, who: Principal, decision: str, reason: str) -> None:
        if not (reason or "").strip():
            raise MemoryWriteRejected("reason_required", "a decision needs a reason")
        try:
            self.store.execute("INSERT INTO memory_proposal_decisions (proposal_id, decision, reviewer_kind, reviewer_id, auth_ref, reason, proposal_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
                               (row["proposal_id"], decision, "human", who.actor_id, f"{who.kind}:{who.actor_id}:{who.request_id or 'session'}", reason, row["content_hash"], self.store.now()))
        except Exception as e:
            raise MemoryWriteRejected("decision_refused", str(e)[:160]) from None

    def approve(self, proposal_id: str, principal: Any, reason: str, *, authorize: Optional[Callable] = None) -> None:
        who, row = self._human(principal, proposal_id, authorize)
        if row["state"] != "pending_approval":
            raise MemoryWriteRejected("invalid_state", f"only a pending proposal can be approved (this one is {row['state']})")
        with self.store.transaction():
            self._decide(row, who, "approve", reason)
            self._event(row, "approved", "human", who.actor_id, reason)

    def reject(self, proposal_id: str, principal: Any, reason: str, *, authorize: Optional[Callable] = None) -> None:
        who, row = self._human(principal, proposal_id, authorize)
        with self.store.transaction():
            self._decide(row, who, "reject", reason)
            self._event(row, "rejected", "human", who.actor_id, reason)

    def expire(self, proposal_id: str, principal: Any, reason: str, *, authorize: Optional[Callable] = None) -> None:
        who, row = self._human(principal, proposal_id, authorize)
        with self.store.transaction():
            self._event(row, "expired", "human", who.actor_id, reason)

    # ---- 4. promote -------------------------------------------------------------------------------------------------------------------------------
    def promote(self, proposal_id: str, principal: Any) -> Promotion:
        """Create the production memory for an APPROVED proposal, through the integrity boundary, atomically.  Refuses anything else; repeating it returns the same memory."""
        who, row = self._visible(principal, proposal_id, "promote")
        if who.actor_id != "promotion":
            raise ScopeDenied("operation_not_permitted", "only the promotion identity may promote")
        if row["state"] == "promoted":
            return Promotion(proposal_id, row["promoted_memory_id"], False, True)
        if row["state"] != "approved":
            raise MemoryWriteRejected("approval_required", f"a proposal is promoted only when approved by a person (this one is {row['state']})")
        last = self.store.query("SELECT * FROM memory_proposal_decisions WHERE proposal_id=? ORDER BY decision_id DESC LIMIT 1", (proposal_id,))
        if not last or last[0]["decision"] != "approve" or last[0]["reviewer_kind"] != "human" or last[0]["proposal_hash"] != row["content_hash"]:
            raise MemoryWriteRejected("approval_required", "no current human approval of this exact content")
        hard = [r for r in self.checks(row) if r["result"] == "fail"]
        if hard:
            raise MemoryWriteRejected("promotion_revalidation_failed", "; ".join(f"{r['check']}: {r['detail']}" for r in hard))
        rec, det = json.loads(row["record_json"]), json.loads(row["details_json"])
        ev = [{k: v for k, v in e.items() if k not in ("excerpt_start", "excerpt_end", "match_method", "match_reason")} for e in json.loads(row["evidence_json"])]
        with self.store.transaction():
            mid, created = self.store.insert_memory(rec, det, ev, reason=f"promoted from proposal {proposal_id} approved by {last[0]['reviewer_id']}", principal=who)
            self._event(row, "promoted", "service", who.actor_id, f"promoted to {mid}", {"created": created, "approved_by": last[0]["reviewer_id"]}, promoted_memory_id=mid)
        return Promotion(proposal_id, mid, created)

    # ---- reads ------------------------------------------------------------------------------------------------------------------------------------
    def get(self, proposal_id: str, principal: Any) -> dict:
        _, row = self._visible(principal, proposal_id, "proposal_read" if getattr(principal, "kind", "") == "user" else "validate")
        return row

    def list(self, client_id: str, principal: Any, states: Optional[tuple] = None) -> list[dict]:
        self.store.boundary.authenticate(principal, "proposal_read" if getattr(principal, "kind", "") == "user" else "validate", client_id)
        rows = self.store.query("SELECT * FROM memory_proposals WHERE client_id=? ORDER BY formed_at, proposal_id", (client_id,))
        return [r for r in rows if not states or r["state"] in states]

    def events(self, proposal_id: str, principal: Any) -> list[dict]:
        self.get(proposal_id, principal)
        return self.store.query("SELECT * FROM memory_proposal_events WHERE proposal_id=? ORDER BY event_id", (proposal_id,))

    def decisions(self, proposal_id: str, principal: Any) -> list[dict]:
        self.get(proposal_id, principal)
        return self.store.query("SELECT * FROM memory_proposal_decisions WHERE proposal_id=? ORDER BY decision_id", (proposal_id,))


def model_of(rec: dict) -> str:
    return rec.get("model") or "unknown"
