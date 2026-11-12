"""Memory integrity boundary (P1.4 / P1.4.1): the ONE service every durable memory mutation passes through.

    caller -> MemoryStore.<mutator>() -> MemoryIntegrityBoundary -> Principal validation -> client authorization -> provenance validation -> record integrity -> temporal conflict
              detection -> idempotency / duplicate check -> transactional persistence (guard gate open) -> durable memory_write_log row -> audit event AFTER commit

Defence in depth: the store's SQLite guard triggers abort any INSERT / UPDATE / DELETE on memory content tables unless the boundary has opened its gate on THIS connection
(`cmf_write_allowed()`), so raw SQL cannot bypass the checks; a connection that does not register the function (any other process or tool) fails closed because the triggers
cannot be evaluated; the store's SQLite authorizer refuses DDL (DROP TRIGGER ...) outside trusted maintenance.  Someone with direct file access can still drop the triggers -
that is outside what an application-level boundary can promise (the next durable write then refuses until MemoryStore.init() restores them).

IDENTITY.  There is no implicit identity.  Every mutation needs a Principal: a person (built from a verified AuthContext by trusted server code) or one of a few in-process service
identities, each limited to named operations, origins, memory types and targets.  A Principal object is sealed with a per-process key, so a copy that was edited afterwards
(dataclasses.replace, __setattr__ tricks) is rejected - but code that can call Principal.user(...) is trusted server code by definition: this module cannot authenticate a Python object.

The boundary never trusts the model: client ids come from the verified source data or the Principal's scopes, never from generated text; protected provenance columns cannot be
supplied by the caller; the source class is derived from WHO writes (service identity) and what the evidence resolves to, not from a caller-chosen label; verification and
observation time are computed here.  It adds no agent and makes no model call.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from agents.memory import rules, support
from agents.memory.config import GLOBAL_CLIENT
from agents.planner.topics import TOPICS
from agents.memory.errors import DatabaseIntegrityError, EvidenceError, EvidenceRejected, InvalidRecord, MemoryWriteRejected, ScopeDenied

PROCEDURES_FILE = Path(__file__).with_name("procedures.json")
COMPONENT = "memory.integrity"

# ---- structural vocabulary (mirrors the CHECK constraints of sql/100) ---------------------------------------------------------------------------------------------------
MEMORY_TYPES = ("episodic", "semantic", "procedural", "working")
STATUSES = ("candidate", "active", "superseded", "disputed", "expired", "unconfirmed")
CERTAINTIES = ("confirmed", "unconfirmed", "recommendation", "exploratory", "hypothetical", "correction", "n/a")
ORIGINS = ("conversation_llm", "conversation_rule", "consolidation", "curated_file", "tool_summary", "orchestrator")
SOURCE_CLASS = {"conversation_llm": "llm_extraction", "conversation_rule": "observed_transcript", "consolidation": "derived", "curated_file": "curated",
                "tool_summary": "tool_summary", "orchestrator": "system"}
TOPIC_VOCABULARY = frozenset(TOPICS)          # the approved topic vocabulary: the planner's own list (every topic in the seed database is in it), including "other"


def canon_slot(value: Any) -> str:
    """The ONE canonical spelling of a slot component (topic / subject): surrounding whitespace removed, lower-cased, runs of whitespace / hyphens -> '_'.
    'Retirement_Age ', ' retirement age' and 'retirement-age' are the same slot."""
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


# the same canonical form as a SQL expression (used by the slot queries here and by the unique index in the store, so a write that somehow skipped Python still collides)
def canon_sql(col: str) -> str:
    return f"replace(replace(lower(trim({col})), ' ', '_'), '-', '_')"


PROTECTED = ("verification", "verification_basis", "created_by", "request_id", "operation_id", "row_version", "source_class", "temporal_status", "observed_at")
CLIENT_ID = re.compile(r"\bCLIENT-\d{3,}\b")
TERMINAL = ("superseded", "expired")
DETAIL_COLUMNS = {"episodic": {"interaction_id", "turn_index", "event_ts", "speaker_role", "event_type", "selection_note", "candidate_id"},
                  "semantic": {"slot_key", "basis", "supporting_episodes"},
                  "procedural": {"procedure_name", "steps", "topics", "keywords", "approval_status", "approver", "source_ref"},
                  "working": {"session_id", "plan_id", "derived_from_id", "expires_at"}}
CONFLICT_KINDS = {"contradiction", "unconsolidated_update", "open_exploration", "changed_over_time", "disputed", "contradiction_same_period", "late_arriving_historical",
                  "unverified_vs_verified", "human_correction_vs_evidence"}
HC = "human_correction"


# ---- identities ---------------------------------------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ServiceSpec:
    """What ONE service identity may do.  Operation names alone are not enough: origins, memory types and the client scope are constrained too (targets: see _service_target)."""
    ops: frozenset
    origins: frozenset = frozenset()
    types: frozenset = frozenset()
    global_only: bool = False        # may act ONLY on the GLOBAL (curated) client
    any_client: bool = False         # MAY be issued for every client (only the TTL job - client-scoped or any_client - and only on expired working rows)


SERVICES = {
    "ingestion": ServiceSpec(frozenset({"create"}), frozenset({"conversation_llm", "conversation_rule"}), frozenset({"episodic", "procedural"})),
    "consolidation": ServiceSpec(frozenset({"create", "transition", "link", "support"}), frozenset({"consolidation"}), frozenset({"semantic"})),
    "orchestrator": ServiceSpec(frozenset({"create"}), frozenset({"orchestrator", "tool_summary"}), frozenset({"working"})),
    "curated_loader": ServiceSpec(frozenset({"create"}), frozenset({"curated_file"}), frozenset({"procedural"}), global_only=True),
    "ttl_expiry": ServiceSpec(frozenset({"transition"}), any_client=True),
}
USER_OPS = frozenset({"transition", "approval", "correct", "supersede", "resolve"})        # people can change status, approve procedures, correct and supersede - never create arbitrary rows
RESERVED_ACTORS = frozenset(SERVICES) | {"", "llm", "model", "system", "admin", "root", "assistant", "dev-anonymous", "anonymous", "unknown", "none", "null"}
_SEAL_KEY = os.urandom(32)


def _seal(p: "Principal") -> str:
    body = json.dumps([p.actor_id, p.kind, sorted(p.scopes), p.any_client, p.request_id, p.authenticated, sorted(p.evidence_refs), p.label])
    return hmac.new(_SEAL_KEY, body.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Principal:
    """WHO is writing and for WHICH clients.  Built from a verified AuthContext (users) or an explicit service identity - never from model output or a request body.
    Construct it ONLY through Principal.user / Principal.service: a hand-built or edited instance carries no valid seal and is refused."""
    actor_id: str
    kind: str = "user"                       # 'user' | 'service'
    scopes: frozenset = frozenset()
    any_client: bool = False                 # only the ttl_expiry service
    request_id: str = ""
    authenticated: bool = False
    evidence_refs: frozenset = frozenset()   # tool execution ids that belong to THIS request (lets tool evidence resolve)
    label: str = ""                          # name shown in lifecycle events when it differs from the identity (the TTL job is recorded as 'system', as before P1.4)
    seal: str = field(default="", repr=False, compare=False)

    @property
    def audit_name(self) -> str:
        return self.label or self.actor_id

    @staticmethod
    def _sealed(p: "Principal") -> "Principal":
        object.__setattr__(p, "seal", _seal(p))
        return p

    @staticmethod
    def user(actor_id: str, clients: Sequence[str], request_id: str = "", *, authenticated: bool) -> "Principal":
        """A person.  `authenticated` has NO default: pass what the verified AuthContext says (False when the identity was merely asserted)."""
        return Principal._sealed(Principal((actor_id or "").strip(), "user", frozenset(clients), False, request_id, bool(authenticated)))

    @staticmethod
    def service(name: str, clients: Sequence[str] = (), request_id: str = "", any_client: bool = False, evidence_refs: Sequence[str] = ()) -> "Principal":
        if name not in SERVICES:
            raise ValueError(f"unknown service identity {name!r}")
        return Principal._sealed(Principal(name, "service", frozenset(clients), bool(any_client), request_id, True, frozenset(evidence_refs), "system" if name == "ttl_expiry" else ""))

    def covers(self, client_id: str) -> bool:
        return bool(client_id) and (self.any_client or client_id in self.scopes)


@dataclass
class WriteResult:
    outcome: str                              # new | duplicate | updated | corrected | superseded | rejected
    operation_id: str
    memory_id: Optional[str] = None
    changed: bool = False
    replayed: bool = False
    reasons: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    verification: Optional[str] = None


def _h(*parts: Any, n: int = 16) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:n]


def payload_hash(op: str, client_id: str, *payload: Any) -> str:
    return hashlib.sha256(json.dumps([op, client_id, *payload], sort_keys=True, default=str).encode("utf-8")).hexdigest()


FUTURE = re.compile(r"\b(plan(?:s|ning|ned)? to|intend\w* to|will|going to|want(?:s)? to|hope(?:s)? to|would like to|aim(?:s)? to|expects? to|thinking (?:of|about)|considering)\b", re.I)
COMPLETED = re.compile(r"\b(retired|sold|completed|paid off|transferred|inherited|received|moved|signed|purchased|bought)\b", re.I)
DECISION = re.compile(r"\b(decided|decision|settled on|agreed|going with)\b", re.I)
NEGATION = re.compile(r"\b(?:not|never|no longer|yet to|hasn'?t|haven'?t|isn'?t|wasn'?t|didn'?t|won'?t)\b", re.I)


def classify_temporal(rec: dict, text: str) -> str:
    """current | historical | planned | superseded | disputed | unknown.  Conservative: a future intention is never read as having happened, a negated event ('has not retired
    yet') is never read as completed, and a completed event is never read as still current without evidence; anything ambiguous is 'unknown'."""
    st = rec.get("status")
    if st in ("superseded", "disputed"):
        return st
    if rec.get("valid_to"):
        return "historical"
    if rec.get("certainty") in ("exploratory", "hypothetical", "recommendation"):
        return "planned"
    t = text or ""
    done, future, decided = bool(COMPLETED.search(t)) and not NEGATION.search(t), bool(FUTURE.search(t)), bool(DECISION.search(t))
    if done and not future:
        return "historical"
    if future and not decided:
        return "planned"
    if rec.get("certainty") in ("confirmed", "correction") and rec.get("event_date"):
        return "current"
    return "unknown"


# ---- value / content comparison (no model: deterministic, conservative) ---------------------------------------------------------------------------------------------
_NUMBERISH = set(rules._UNITS) | set(rules._TENS) | {"hundred", "thousand", "million", "and", "k"} | set(rules._STOP)


def _norm(v: Optional[str]) -> str:
    return re.sub(r"[\s$,]", "", (v or "").lower())


def _numeric(v: Optional[str]) -> bool:
    """A value that is only a number (digits or spelled out) plus units: '63', '63.0', 'sixty-three', '35%', '$220,000'."""
    if not v or not rules.extract_numbers(v):
        return False
    return all(w in _NUMBERISH for w in re.findall(r"[a-z]+", v.lower().replace("-", " ")))


def _canon(v: Optional[str]) -> str:
    """Equivalent numeric spellings share one canonical form ('63' == '63.0' == 'sixty-three'); other values compare as normalised text."""
    return "#" + ",".join(rules.extract_numbers(v or "")) if _numeric(v) else _norm(v)


def value_in(value: Optional[str], excerpt: str) -> bool:
    """Is the claimed value supported by the cited text?  Numeric values compare as numbers (units and spelling ignored); other values need most of their words in the text."""
    if not value or not value.strip():
        return False
    if _numeric(value):
        return set(rules.extract_numbers(value)) <= set(rules.extract_numbers(excerpt))
    return rules.value_supported(value, excerpt)


def _words(text: str) -> set:
    return {w[:4] for w in re.findall(r"[a-z]+", rules.norm(text).replace("-", " ")) if len(w) >= 4 and w not in rules._STOP}


def _cmp_time(a: str, b: str) -> int:
    """-1 / 0 / 1 for a before / same as / after b.  Dates decide; clock times only when BOTH sides carry one."""
    da, db = a[:10], b[:10]
    if da != db:
        return -1 if da < db else 1
    if len(a) > 10 and len(b) > 10 and a[11:19] != b[11:19]:
        return -1 if a[11:19] < b[11:19] else 1
    return 0


class MemoryIntegrityBoundary:
    # ---- chronology (P1.4.2): the single rule for "may B replace A?"
    def _evidence_key(self, interaction_id: Any, turn_index: Any) -> Optional[tuple]:
        try:
            turn = self.store.conv.turn(interaction_id, int(turn_index))
        except (KeyError, ValueError, TypeError):
            turn = None
        ts = str((turn or {}).get("interaction_datetime") or "")
        return (ts, str(interaction_id), int(turn_index)) if ts else None

    def _row_key(self, row: dict) -> Optional[tuple]:
        """(observation time, interaction, turn) of the memory's PRIMARY source turn, from the validated transcript; falls back to the stored observation time (or date) for
        legacy rows.  None = unknown."""
        ev = self.store.query("SELECT interaction_id, turn_index FROM {S}memory_evidence WHERE memory_id=? AND source_type='conversation' "
                              "ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, evidence_id LIMIT 1", (row["memory_id"],))
        if ev:
            k = self._evidence_key(ev[0]["interaction_id"], ev[0]["turn_index"])
            if k:
                return k
        t = row.get("observed_at") or row.get("event_date") or ""
        return (t, "", -1) if t else None

    def _check_later(self, new_key: Optional[tuple], old_key: Optional[tuple], oid: str) -> None:
        """The replacement must be observed STRICTLY after what it replaces.  Same meeting (equal time): the later TURN wins; the same turn, or equal times in different
        meetings, are ambiguous and refused.  An unknown time on either side is refused: it can never override known evidence."""
        if not new_key or not old_key:
            raise MemoryWriteRejected("supersession_time_unknown", "both memories need a known observation time", operation_id=oid)
        c = _cmp_time(new_key[0], old_key[0])
        if c > 0 or (c == 0 and new_key[1] == old_key[1] and new_key[1] and new_key[2] > old_key[2]):
            return
        raise MemoryWriteRejected("supersession_chronology", "the replacing memory was not observed after the one it replaces", operation_id=oid)

    def __init__(self, store, emit: Optional[Callable[..., None]] = None) -> None:
        self.store = store
        self.emit_fn = emit

    # ------------------------------------------------------------------ audit plumbing
    def emit(self, name: str, ctx: dict, after_commit: bool = False, **extra) -> None:
        """Audit events never affect a write: an observer that raises is recorded on the store (observer_failures) and ignored."""
        payload = {k: v for k, v in dict(ctx, **extra).items() if v not in (None, "")}      # ids, codes and counts only: never memory content
        if self.emit_fn is None:
            return

        def fire(event: str = name, data: dict = payload) -> None:
            try:
                self.emit_fn(COMPONENT, event, **data)
            except Exception as exc:                  # noqa: BLE001
                self.store.observer_failed(event, exc)

        if after_commit:
            self.store.on_commit(fire, lambda: fire("memory_write_rolled_back", {**payload, "was": name}))
        else:
            fire()

    def _ctx(self, op: str, p: Any, client_id: str, memory_id: Optional[str] = None) -> dict:
        ok = isinstance(p, Principal)
        return dict(op=op, operation_id="OP-" + uuid.uuid4().hex[:12], actor=(p.actor_id if ok else ""), actor_kind=(p.kind if ok else ""), client_id=client_id or "",
                    request_id=(p.request_id if ok else ""), memory_id=memory_id)

    def _deny(self, ctx: dict, code: str, message: str, exc=ScopeDenied):
        raise exc(code, message, operation_id=ctx.get("operation_id", ""))

    def _principal(self, p: Any, op: str, ctx: dict) -> Principal:
        """Validate WHO is asking before anything about the target is looked at.  No Principal, a malformed / edited / unauthenticated one, a person whose name is a reserved or
        service name, or a service asked for an operation outside its allow-list: all refused.  Nothing here reads a record, so nothing is inferred from one."""
        if p is None:
            self._deny(ctx, "missing_principal", "no Principal was supplied: every durable memory mutation needs an explicit identity")
        if not isinstance(p, Principal) or not hmac.compare_digest(p.seal or "", _seal(p)):
            self._deny(ctx, "malformed_principal", "the Principal is not one issued by Principal.user / Principal.service")
        ctx.update(actor=p.actor_id, actor_kind=p.kind, request_id=ctx.get("request_id") or p.request_id)
        if not (p.actor_id or "").strip():
            self._deny(ctx, "missing_actor", "no actor identity was supplied")
        if not p.authenticated:
            self._deny(ctx, "unauthenticated_actor", "the identity is not authenticated")
        if p.kind == "service":
            spec = SERVICES.get(p.actor_id)
            if spec is None:
                self._deny(ctx, "malformed_principal", "unknown service identity")
            if op not in spec.ops:
                self._deny(ctx, "operation_not_permitted", f"service identity {p.actor_id!r} may not perform {op}")
            if (p.any_client and not spec.any_client) or (p.any_client and p.scopes) or (spec.any_client and not p.any_client and not p.scopes):
                self._deny(ctx, "malformed_principal", "the service's client scope does not match its definition")
            if spec.global_only and p.scopes != frozenset({GLOBAL_CLIENT}):
                self._deny(ctx, "malformed_principal", "this service acts on the GLOBAL scope only")
            if not spec.global_only and GLOBAL_CLIENT in p.scopes:
                self._deny(ctx, "malformed_principal", "this service may not act on the GLOBAL scope")
        elif p.kind == "user":
            if p.actor_id.lower() in RESERVED_ACTORS or p.any_client:
                self._deny(ctx, "reserved_actor", "that name is reserved and is not proof of identity")
            if op not in USER_OPS:
                self._deny(ctx, "operation_not_permitted", f"a person may not perform {op} directly")
        else:
            self._deny(ctx, "malformed_principal", "unknown principal kind")
        return p

    def _scope(self, p: Principal, client_id: str, ctx: dict) -> None:
        if not client_id:
            self._deny(ctx, "missing_client_scope", "no client scope was supplied")
        if not p.covers(client_id):
            self._deny(ctx, "client_scope_denied", "the actor is not authorized for this client")

    def _target(self, p: Principal, memory_id: str, ctx: dict) -> dict:
        """The row a mutation targets - found ONLY inside the caller's scope.  'Not yours' and 'does not exist' are the same error."""
        row = self.store.get(memory_id) if memory_id else None
        if row is None or not p.covers(row["client_id"]):
            ctx["memory_id"] = None
            self._deny(ctx, "memory_not_found", "no such memory in the actor's client scope")
        ctx.update(client_id=row["client_id"], memory_id=memory_id)
        return row

    def _visible(self, p: Principal, memory_id: Optional[str]) -> Optional[dict]:
        """A row, but only when it is inside the caller's scope (used for secondary references: related / target / superseding ids)."""
        row = self.store.get(memory_id) if memory_id else None
        return row if row is not None and p.covers(row["client_id"]) else None

    def lookup(self, memory_id: str, principal: Any, op: str = "transition") -> Optional[dict]:
        """Public, scope-checked read for callers that must decide before they mutate: validates the Principal FIRST, then returns the row only if it is in scope."""
        ctx = self._ctx(op, principal, "", memory_id)
        p = self._principal(principal, op, ctx)
        return self._visible(p, memory_id)

    @contextmanager
    def _persist(self, ctx: dict):
        """Open the write gate; a raw driver error (duplicate key, foreign key, trigger) becomes ONE safe domain error - never the database's own message."""
        try:
            with self.store._gate():
                yield
        except MemoryWriteRejected:
            raise
        except DatabaseIntegrityError:
            raise MemoryWriteRejected("write_conflict", "the write conflicts with data already stored", operation_id=ctx.get("operation_id", "")) from None

    # ------------------------------------------------------------------ durable audit log
    def _log(self, ctx: dict, p: Optional[Principal], outcome: str, reasons: Sequence[str], pay_hash: str, key: str = "", memory_id: Optional[str] = None) -> None:
        st = self.store
        with st._gate():
            st._p_log(dict(log_id="WL-" + f"{st.mono():020d}-" + uuid.uuid4().hex[:6], operation_id=ctx["operation_id"], op=ctx["op"], client_id=ctx.get("client_id") or "-",
                           actor_id=(p.actor_id if p else ctx.get("actor", "")), actor_kind=(p.kind if p else ctx.get("actor_kind", "")) or "unknown", request_id=ctx.get("request_id", ""),
                           idempotency_key=key, payload_hash=pay_hash, outcome=outcome, reason_codes=json.dumps(list(reasons)), memory_id=memory_id, created_at=st.now()))

    # a rejection that depends on the state of the data (not on the request itself) must not reserve the idempotency key: the same request can succeed after a re-read
    _TRANSIENT = ("stale_version", "version_required", "write_conflict")

    def _reject(self, ctx: dict, p: Optional[Principal], e: MemoryWriteRejected, pay_hash: str, key: str = "") -> None:
        """Record a refused attempt (durable row + event).  Identity / scope refusals never reserve an idempotency key (a stranger must not be able to squat on a key)."""
        reserve = bool(key) and not isinstance(e, ScopeDenied) and e.code not in self._TRANSIENT
        try:
            with self.store.transaction():
                self._log(ctx, p, "rejected", e.reasons, pay_hash, key if reserve else "", None)
        except Exception:                                                  # a key collision (concurrent identical rejection) or a locked database: the event still records it
            pass
        e.operation_id = e.operation_id or ctx["operation_id"]
        name = "memory_scope_denied" if isinstance(e, ScopeDenied) else "memory_write_rejected"
        self.emit(name, ctx, reason=e.code, reasons=e.reasons)
        if e.code == "provenance_missing":
            self.emit("memory_provenance_missing", ctx, reason=e.code)

    # ================================================================== CREATE
    def create(self, rec: dict, det: dict, evidence: Sequence[dict] = (), *, principal: Any = None, reason: str = "created", idempotency_key: Optional[str] = None) -> WriteResult:
        client = rec.get("client_id") or ""
        ctx = self._ctx("create", principal, client, rec.get("memory_id"))
        explicit = bool(idempotency_key)
        key = idempotency_key or str(rec.get("content_hash") or "")
        ph = payload_hash("create", client, rec, det, list(evidence))
        self.emit("memory_write_requested", ctx, memory_type=rec.get("memory_type"), origin=rec.get("origin"), idempotency_key_supplied=explicit)
        p = None
        try:
            p = self._principal(principal, "create", ctx)
            self._scope(p, client, ctx)
            with self.store.transaction():
                replay = self._replay(ctx, p, client, key, explicit, ph)
                if replay is not None:
                    return replay
                dup = self.store.query("SELECT memory_id, verification FROM {S}memory_records WHERE client_id=? AND memory_type=? AND content_hash=?",
                                       (client, rec.get("memory_type"), rec.get("content_hash")))
                if dup:
                    self._log(ctx, p, "duplicate", [], ph, "", dup[0]["memory_id"])
                    self.emit("memory_write_duplicate", ctx, memory_id=dup[0]["memory_id"], after_commit=True)
                    return WriteResult("duplicate", ctx["operation_id"], dup[0]["memory_id"], False, reasons=["duplicate_content_hash"], verification=dup[0]["verification"])
                row, det, ev_rows, conflicts, sup = self._validate_create(p, rec, det, evidence, ctx, None)
                self.emit("memory_write_validated", ctx, verification=row["verification"], temporal_status=row["temporal_status"], conflicts=[c["kind"] for c in conflicts])
                with self._persist(ctx):
                    if sup is not None:                         # the previous live version is superseded FIRST (the one-active-version index), its event written once the new row exists
                        self.store._p_status(sup["memory_id"], "superseded", max(row["valid_from"] or "", sup.get("valid_from") or "") or None)
                    self._insert(ctx, row, det, ev_rows, conflicts, reason, p)
                    if sup is not None:
                        self.store._p_event(sup["memory_id"], sup["status"], "superseded", f"superseded by {row['memory_id']}", p.audit_name, row["memory_id"])
                    self._log(ctx, p, "new", [c["kind"] for c in conflicts], ph, key, row["memory_id"])
                self._announce(ctx, row, conflicts, "new")
                return WriteResult("new", ctx["operation_id"], row["memory_id"], True, conflicts=conflicts, verification=row["verification"])
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph, key if explicit else "")
            raise

    def _insert(self, ctx: dict, row: dict, det: dict, ev_rows: list, conflicts: list, reason: str, p: Principal) -> None:
        st = self.store
        st._p_insert(row, det)
        for ev, resolution in ev_rows:
            st._p_evidence(row["memory_id"], row["client_id"], ev, resolution)
        st._p_event(row["memory_id"], None, row["status"], reason, p.audit_name, row.get("supersedes_id") if row["source_class"] == HC else None)
        for c in conflicts:
            st._p_conflict(dict(conflict_id="CF-" + _h(row["memory_id"], c["memory_id"], c["kind"], n=20), client_id=row["client_id"], memory_id=row["memory_id"],
                                other_memory_id=c["memory_id"], kind=c["kind"], severity=c["severity"], status="open", detail=c.get("detail", ""), operation_id=ctx["operation_id"],
                                detected_at=st.now()))

    def _announce(self, ctx: dict, row: dict, conflicts: list, outcome: str) -> None:
        for c in conflicts:
            self.emit("memory_conflict_detected", ctx, memory_id=row["memory_id"], conflict=c["kind"], severity=c["severity"], other_memory_id=c.get("memory_id"), after_commit=True)
        self.emit("memory_write_committed", ctx, memory_id=row["memory_id"], outcome=outcome, verification=row["verification"], after_commit=True)

    def _replay(self, ctx: dict, p: Principal, client: str, key: str, explicit: bool, ph: str) -> Optional[WriteResult]:
        if not key:
            return None
        rows = self.store.query("SELECT * FROM {S}memory_write_log WHERE client_id=? AND op=? AND idempotency_key=?", (client, ctx["op"], key))
        if not rows:
            return None
        old = rows[0]
        if old["payload_hash"] != ph:
            if explicit:
                raise MemoryWriteRejected("idempotency_key_conflict", "the key was already used with a different payload", operation_id=ctx["operation_id"])
            return None                                              # an implicit (content-hash) key falls through to the ordinary duplicate logic
        codes = json.loads(old["reason_codes"])
        self.emit("memory_ingestion_replayed", ctx, memory_id=old["memory_id"], original_operation_id=old["operation_id"], original_outcome=old["outcome"])
        if old["outcome"] == "rejected":                              # the ORIGINAL validation outcome is preserved
            raise MemoryWriteRejected(codes[0] if codes else "rejected", "replay of a rejected write", codes, old["operation_id"], replayed=True)
        if old["actor_id"] != p.actor_id:                             # a replay by ANOTHER actor of the same client is its own audit fact
            self._log(ctx, p, "duplicate", ["replay_by_other_actor"], ph, "", old["memory_id"])
        return WriteResult(old["outcome"], old["operation_id"], old["memory_id"], False, True, codes)

    # ------------------------------------------------------------------ validation
    def _validate_create(self, p: Principal, rec: dict, det: dict, evidence: Sequence[dict], ctx: dict, human_old: Optional[dict]):
        """Everything a new memory must satisfy.  `human_old` is set ONLY by correct(): a person replacing `human_old` (the one path that mints source_class human_correction)."""
        st = self.store
        oid = ctx["operation_id"]
        client = rec["client_id"]
        bad = [k for k in PROTECTED if k in rec]
        if bad:
            raise MemoryWriteRejected("protected_field_supplied", f"fields {bad} are set by the integrity boundary", operation_id=oid)
        self._structure(rec, ctx)
        rec, det = dict(rec), dict(det)
        topic = canon_slot(rec.get("topic"))
        if topic not in TOPIC_VOCABULARY:
            raise InvalidRecord("invalid_record", "topic is outside the approved topic vocabulary", operation_id=oid)
        rec["topic"] = topic
        raw_subject = rec.get("subject")
        if raw_subject is not None and str(raw_subject) != "" and not str(raw_subject).strip():
            raise InvalidRecord("invalid_record", "a blank subject is not a slot identity", operation_id=oid)
        rec["subject"] = canon_slot(raw_subject)
        if rec["memory_type"] == "semantic" and not rec["subject"]:
            rec["subject"] = topic                                               # a semantic memory with no subject IS its topic's own slot: stored explicitly, never blank
        if rec.get("valid_to"):
            raise MemoryWriteRejected("valid_time_not_permitted", "valid_to is set only by a trusted supersession, never by the writer", operation_id=oid)
        if set(det) - DETAIL_COLUMNS[rec["memory_type"]]:
            raise InvalidRecord("invalid_record", "unknown detail field", operation_id=oid)
        # identities embedded in generated text are never trusted: another client's id inside a memory is refused
        text = " ".join(str(rec.get(k) or "") for k in ("content", "title", "subject", "topic", "value_text", "corrects_value"))
        if {c for c in CLIENT_ID.findall(text)} - {client}:
            raise MemoryWriteRejected("forged_identity", "memory text names a different client", operation_id=oid)
        origin, mtype = rec["origin"], rec["memory_type"]
        sclass = self._source_class(p, origin, mtype, client, human_old, ctx)
        # references to other memories: inside THIS client or refused - the same code whether the id is foreign or does not exist
        if st.get(rec["memory_id"]) is not None:
            raise MemoryWriteRejected("memory_id_unavailable", "that memory id cannot be used", operation_id=oid)
        sup_target = None
        for ref in (rec.get("supersedes_id"), det.get("derived_from_id")):
            if ref:
                target = st.get(ref)
                if target is None or target["client_id"] != client:
                    raise MemoryWriteRejected("reference_not_found", "a referenced memory is not in this client's scope", operation_id=oid)
                if ref == rec.get("supersedes_id"):
                    sup_target = target
        sup_target = self._slot_rules(rec, human_old, sup_target, ctx) if mtype == "semantic" else None      # only semantic versions are replaced automatically
        # evidence: every handle must RESOLVE inside this client's scope; well-formed is not enough
        ev_rows: list = []
        conv: list = []
        for ev in evidence:
            ev = dict(ev)
            ev.setdefault("source_type", "conversation")
            if ev["source_type"] == "conversation":
                try:
                    st.verify_evidence(client, ev)
                except (EvidenceError, KeyError, ValueError, TypeError):
                    raise EvidenceRejected("evidence_unresolved", "a cited source does not resolve for this client", operation_id=oid) from None
                conv.append(ev)
                ev_rows.append((ev, "resolved"))
            elif ev["source_type"] == "curated":
                if sclass != "curated" or not self._curated_entry(ev.get("ref"))[1]:
                    raise EvidenceRejected("evidence_unresolved", "a cited curated source does not resolve", operation_id=oid)
                ev_rows.append((ev, "resolved"))
            else:   # tool evidence: resolvable only against this request's own execution ids; otherwise the limitation is recorded, not hidden
                ev_rows.append((ev, "resolved" if (ev.get("ref") and ev["ref"] in p.evidence_refs) else "unresolvable"))
        excerpts = [e.get("excerpt") or "" for e in conv]
        if sclass in ("llm_extraction", "observed_transcript", "derived", HC) and mtype in ("episodic", "semantic") and not conv:
            raise MemoryWriteRejected("provenance_missing", "a conversation-derived memory needs at least one resolvable source", operation_id=oid)
        if rec.get("certainty") in ("confirmed", "correction") and mtype in ("episodic", "semantic") and not conv:
            raise MemoryWriteRejected("certainty_requires_evidence", "a confirmed memory needs resolvable source evidence", operation_id=oid)
        observed = self._bind_time(rec, det, conv, mtype, oid)
        if observed and mtype in ("semantic", "procedural"):      # valid_from is DERIVED from the validated source (or, for a correction, from the trusted correction context)
            expected = max(observed[:10], human_old.get("valid_from") or "") if human_old is not None else observed[:10]
            if rec.get("valid_from") not in (None, "", expected):
                raise MemoryWriteRejected("valid_time_mismatch", "valid_from does not match the validated source chronology", operation_id=oid)
            rec["valid_from"] = expected
        self._check_details(p, rec, det, conv, sclass, oid)
        verification, basis = self._verification(client, sclass, rec, det, conv, excerpts, ev_rows)
        if sclass == HC and verification != "verified":
            raise MemoryWriteRejected("correction_value_unsupported", "the corrected value is not supported by the cited source", operation_id=oid)
        if human_old is not None and human_old.get("verification") == "verified" and verification != "verified":
            raise MemoryWriteRejected("unverified_cannot_override_verified", "an unverified memory cannot replace a verified one", operation_id=oid)
        if sup_target is not None and human_old is None:       # an automatic version that replaces a live one: verified precedence + chronology, like every other replacement
            if sup_target.get("verification") == "verified" and verification != "verified":
                raise MemoryWriteRejected("unverified_cannot_override_verified", "an unverified memory cannot replace a verified one", operation_id=oid)
            self._check_later(self._evidence_key(conv[0]["interaction_id"], conv[0]["turn_index"]) if conv else None, self._row_key(sup_target), oid)
        ts = st.now()
        row = dict(subject="", title="", value_text=None, corrects_value=None, source_role=None, confidence=0.5, event_date=None, valid_from=None, valid_to=None, version_no=None,
                   supersedes_id=None, model=None, created_at=ts, updated_at=ts)
        row.update(rec)
        row.update(verification=verification, verification_basis=basis, observed_at=observed, source_class=sclass, created_by=p.actor_id, request_id=ctx.get("request_id") or "",
                   operation_id=oid, row_version=1, temporal_status=classify_temporal(row, " ".join(excerpts) or str(rec.get("content") or "")))
        conflicts, _ = self._conflicts(row, mtype)
        if any(c["kind"] == "late_arriving_historical" for c in conflicts):
            row["temporal_status"] = "historical"
        return row, det, ev_rows, conflicts, sup_target

    def _slot_rules(self, rec: dict, human_old: Optional[dict], sup_target: Optional[dict], ctx: dict) -> Optional[dict]:
        """Semantic slot = (client, topic, subject or topic).  Correction precedence and ONE active version per slot are enforced HERE (not only in the consolidator):
        an automatic write may neither supersede nor compete with a live human correction, and may not create a second active version.  A legitimate `supersedes_id` that names the
        live previous version is returned so the caller supersedes it atomically, in the same transaction, before the new row is inserted."""
        oid = ctx["operation_id"]
        slot = canon_slot(rec.get("subject")) or canon_slot(rec["topic"])
        # compared in CANONICAL form on both sides, so legacy rows stored with another spelling still count as the same slot
        subj_expr = canon_sql("COALESCE(NULLIF(trim(subject), ''), topic)")
        rows = self.store.query("SELECT memory_id, status, source_class FROM {S}memory_records WHERE client_id=? AND memory_type='semantic' AND " + canon_sql("topic") + "=? "
                                "AND " + subj_expr + "=? AND memory_id<>?", (rec["client_id"], canon_slot(rec["topic"]), slot, rec["memory_id"]))
        live = ("active", "disputed", "unconfirmed")
        if human_old is None:
            if (sup_target is not None and sup_target.get("source_class") == HC) or any(r["source_class"] == HC and r["status"] in live for r in rows):
                raise MemoryWriteRejected("human_correction_protected", "a human-corrected version is replaced only by a person", operation_id=oid)
            auto = sup_target if sup_target is not None and sup_target["status"] in live and rec["status"] == "active" else None
            if rec["status"] == "active" and any(r["status"] == "active" and r["memory_id"] != (auto or {}).get("memory_id") for r in rows):
                raise MemoryWriteRejected("active_version_exists", "this slot already has an active version; supersede it explicitly", operation_id=oid)
            return auto
        if any(r["status"] == "active" and r["memory_id"] != human_old["memory_id"] for r in rows):
            raise MemoryWriteRejected("active_version_exists", "this slot already has another active version", operation_id=oid)
        return None

    def _source_class(self, p: Principal, origin: str, mtype: str, client: str, human_old: Optional[dict], ctx: dict) -> str:
        """Provenance class from WHO writes and what they may write - never from a label the caller chose.  A person's correction is the only way to human_correction;
        'curated' belongs to the curated loader on the GLOBAL scope alone; 'observed_transcript' / 'llm_extraction' belong to the ingestion service."""
        oid = ctx["operation_id"]
        if human_old is not None:
            if p.kind != "user" or origin != "orchestrator" or mtype not in ("episodic", "semantic"):
                raise MemoryWriteRejected("origin_not_permitted", "this identity may not write that kind of memory", operation_id=oid)
            return HC
        spec = SERVICES.get(p.actor_id) if p.kind == "service" else None
        if spec is None or origin not in spec.origins or mtype not in spec.types or origin not in SOURCE_CLASS:
            raise MemoryWriteRejected("origin_not_permitted", "this identity may not write that kind of memory", operation_id=oid)
        if (client == GLOBAL_CLIENT) != spec.global_only:
            raise MemoryWriteRejected("origin_not_permitted", "this identity may not write to that scope", operation_id=oid)
        return SOURCE_CLASS[origin]

    def _structure(self, rec: dict, ctx: dict) -> None:
        def bad(msg: str):
            raise InvalidRecord("invalid_record", msg, operation_id=ctx["operation_id"])
        for k in ("memory_id", "client_id", "memory_type", "topic", "content", "certainty", "status", "origin", "content_hash"):
            if not rec.get(k):
                bad(f"missing {k}")
        if rec["memory_type"] not in MEMORY_TYPES or rec["status"] not in STATUSES or rec["certainty"] not in CERTAINTIES or rec["origin"] not in ORIGINS:
            bad("a field holds a value outside its allowed vocabulary")
        c = rec.get("confidence", 0.5)
        if not isinstance(c, (int, float)) or not 0 <= c <= 1:
            bad("confidence outside 0..1")
        if rec.get("valid_to") and rec.get("valid_from") and rec["valid_to"] < rec["valid_from"]:
            bad("valid_to before valid_from")
        if rec.get("supersedes_id") and rec["supersedes_id"] == rec["memory_id"]:
            bad("a memory cannot supersede itself")

    def _bind_time(self, rec: dict, det: dict, conv: list, mtype: str, oid: str) -> Optional[str]:
        """Observation time comes from the CSV turn the memory cites, never from the caller.  A caller-supplied event_ts / event_date that disagrees with that turn is refused
        (a forged 1999 or 2099 date); a memory with no transcript source has NO observation time (None) - its creation time stays in created_at."""
        if not conv:
            return None
        turn = self.store.conv.turn(conv[0]["interaction_id"], int(conv[0]["turn_index"]))
        ts = str((turn or {}).get("interaction_datetime") or "")
        if not ts:
            return None
        if mtype == "episodic" and str(det.get("event_ts") or "") != ts:
            raise MemoryWriteRejected("event_time_mismatch", "the event time does not match the cited source turn", operation_id=oid)
        if mtype in ("episodic", "semantic") and rec.get("event_date") and rec["event_date"] != ts[:10]:
            raise MemoryWriteRejected("event_date_mismatch", "the event date does not match the cited source turn", operation_id=oid)
        return ts

    def _check_details(self, p: Principal, rec: dict, det: dict, conv: list, sclass: str, oid: str) -> None:
        """Detail-table references are validated like any other reference: an episodic row may only point at a transcript turn that its own evidence resolved in this client."""
        mtype = rec["memory_type"]
        if mtype == "episodic":
            handles = {(e["interaction_id"], int(e["turn_index"])) for e in conv}
            try:
                mine = (det.get("interaction_id"), int(det.get("turn_index")))
            except (TypeError, ValueError):
                raise InvalidRecord("invalid_record", "episodic details need an interaction and a turn", operation_id=oid) from None
            if mine not in handles:
                raise MemoryWriteRejected("detail_reference_invalid", "the interaction / turn is not one of the cited, resolved sources", operation_id=oid)
        if mtype == "procedural" and sclass != "curated" and (det.get("approval_status") != "proposed" or det.get("approver")):
            raise MemoryWriteRejected("approval_not_permitted", "a procedure enters as a proposal; only a person approves it", operation_id=oid)

    def _curated_entry(self, ref: Optional[str]) -> tuple:
        """(file data, entry) for 'procedures.json#key', or (None, None)."""
        try:
            name, key = (ref or "").split("#", 1)
            data = json.loads(PROCEDURES_FILE.read_text(encoding="utf-8"))
            if name != PROCEDURES_FILE.name:
                return None, None
            return data, next((x for x in data["procedures"] if x["key"] == key), None)
        except (ValueError, OSError, KeyError):
            return None, None

    def _verification(self, client: str, sclass: str, rec: dict, det: dict, conv: list, excerpts: list, ev_rows: list) -> tuple:
        """'verified' means: the cited source resolved inside the client's scope AND this memory's own claim (statement and value) is lexically / numerically supported by it,
        AND this class of memory is allowed to claim it.  It does NOT mean the claim is true or that the paraphrase is entailed.  Anything that cannot be checked is 'unverified'.
        A summary never inherits the status of its source: tool summaries and orchestrator context are always 'unverified'."""
        if sclass in ("tool_summary", "system"):
            unres = any(r == "unresolvable" for _, r in ev_rows)
            return "unverified", "summarised/derived context, never verified" + ("; tool source cannot be independently retrieved by the memory layer" if unres else "")
        if sclass == "curated":
            return self._curated_verification(client, rec, det, ev_rows)
        if not conv:
            return "unverified", "no source evidence supplied"
        v = rec.get("value_text")
        topic_ctx = f"{rec.get('topic') or ''} {rec.get('subject') or ''}".replace("_", " ")
        if sclass == HC:
            return ("verified", "authorized human correction: the new value is stated in a cited source sentence as a current value of the same unit, next to the corrected subject")                 if support.value_current_in(v, excerpts, topic_ctx) else ("unverified", "the corrected value is not stated in the cited source as a current value")
        if not v:
            return "unverified", "no extracted value to check against the cited source"
        if not any(value_in(v, x) for x in excerpts):
            return "unverified", "the stated value is not supported by the cited source"
        open_item = rec.get("certainty") in ("exploratory", "hypothetical", "recommendation", "unconfirmed")      # a hedged item may carry the hedged number as its value
        if sclass in ("observed_transcript", "llm_extraction"):
            ok, why = support.claim_supported(rec["content"], v, excerpts, verbatim=(sclass == "observed_transcript"), allow_noncurrent=open_item, context=topic_ctx)
            if sclass == "observed_transcript":
                return ("verified", "rule-extracted sentence IS a cited source sentence and its value is stated there as a current value of the same unit") if ok else ("unverified", why)
            return ("verified", "every sentence of the statement is matched by a cited source sentence (negation, hedging, numbers, units and roles agree); the paraphrase itself is not verified")                 if ok else ("unverified", why)
        if sclass == "derived":   # deterministic consolidation: a verified creating episode of the SAME turn must carry the SAME value
            for ev in conv:
                for r in self.store.query(
                        "SELECT m.value_text AS v FROM {S}memory_evidence e JOIN {S}memory_records m ON m.memory_id=e.memory_id WHERE m.client_id=? AND m.memory_type='episodic' "
                        "AND m.verification='verified' AND e.interaction_id=? AND e.turn_index=?", (client, ev.get("interaction_id"), ev.get("turn_index"))):
                    if _canon(r["v"]) == _canon(v):
                        return "verified", "derived deterministically from a verified episode of the same turn that states the same value"
            return "unverified", "no verified episode of the cited turn states this value"
        return "unverified", "unclassified source"

    def _curated_verification(self, client: str, rec: dict, det: dict, ev_rows: list) -> tuple:
        """Curated = this row is exactly the entry of the shipped procedures file that its evidence names (key, content hash, steps, name, approver) - else it is refused."""
        refs = [ev.get("ref") for ev, _ in ev_rows if ev.get("source_type") == "curated"]
        data, entry = self._curated_entry(refs[0]) if refs else (None, None)
        want = _h("curated", data["version"], entry["key"], json.dumps(entry["steps"]), n=40) if entry else None
        if (entry is None or client != GLOBAL_CLIENT or rec.get("subject") != entry["key"] or rec.get("content_hash") != want or det.get("steps") != json.dumps(entry["steps"])
                or det.get("procedure_name") != entry["name"] or det.get("approval_status") != "approved" or det.get("approver") != data["approver"]):
            raise EvidenceRejected("curated_mismatch", "the procedure does not match the curated file entry it cites", operation_id="")
        return "verified", "identical to the entry of the curated procedures file named by its evidence"

    # ------------------------------------------------------------------ temporal conflicts (uses the existing TemporalConflictDetector)
    def _conflicts(self, row: dict, mtype: str) -> tuple:
        if mtype not in ("episodic", "semantic") or not row.get("value_text") or row["status"] in ("expired",):
            return [], []
        from harness.temporal_conflict import detect          # lazy: harness imports this package
        from agents.memory.store import to_item
        from schemas.memory import MemoryContext
        st = self.store
        slot_sub = row.get("subject") or row["topic"]
        others = [r for r in st.records(row["client_id"], statuses=None, topics=[row["topic"]]) if r["memory_type"] in ("episodic", "semantic") and (r["subject"] or r["topic"]) == slot_sub]
        if not others:
            return [], []
        items = {r["memory_id"]: to_item(r) for r in others}
        cand = to_item(dict(row, steps=None))
        conflicts: list = []
        ctx = MemoryContext(client_id=row["client_id"], request_id="write", as_of=(row.get("event_date") or None))
        ctx.current = [m for m in items.values() if m.memory_type == "semantic" and m.status in ("active", "disputed")]
        ctx.history = [m for m in items.values() if m.memory_type == "semantic" and m.status == "superseded"]
        ctx.episodes = [m for m in items.values() if m.memory_type == "episodic" and m.certainty in ("confirmed", "correction")]
        ctx.unresolved = [m for m in items.values() if m.memory_type == "episodic" and m.certainty not in ("confirmed", "correction")]
        target = {"semantic": ctx.current, "episodic": ctx.episodes if row["certainty"] in ("confirmed", "correction") else ctx.unresolved}[mtype]
        target.append(cand)
        for c in detect(ctx, stale_after_days=10 ** 6):
            if row["memory_id"] in c.memory_ids and c.kind in ("contradiction", "unconsolidated_update", "open_exploration", "changed_over_time", "disputed"):
                for mid in c.memory_ids:
                    if mid != row["memory_id"]:
                        conflicts.append(dict(kind=c.kind, severity=c.severity, memory_id=mid))
        # rules the read-time detector does not cover (it never sees an episode pair on the same date, nor a verified-vs-unverified pair)
        for o in others:
            if o["memory_id"] == row["memory_id"] or o["status"] in ("expired",) or _canon(o["value_text"]) == _canon(row["value_text"]) or not o["value_text"]:
                continue
            both_conf = o["certainty"] in ("confirmed", "correction") and row["certainty"] in ("confirmed", "correction")
            od, nd = o.get("event_date") or "", row.get("event_date") or ""
            if both_conf and od and od == nd and o["memory_type"] == row["memory_type"]:
                conflicts.append(dict(kind="contradiction_same_period", severity="high", memory_id=o["memory_id"]))
            if o["verification"] == "verified" and row["verification"] != "verified" and o["status"] in ("active", "disputed") and o["certainty"] in ("confirmed", "correction"):
                conflicts.append(dict(kind="unverified_vs_verified", severity="high", memory_id=o["memory_id"]))
            if both_conf and od and nd and nd < od and o["status"] in ("active", "superseded"):
                conflicts.append(dict(kind="late_arriving_historical", severity="info", memory_id=o["memory_id"]))
        seen, uniq = set(), []
        for c in conflicts:
            if (c["kind"], c["memory_id"]) not in seen:
                seen.add((c["kind"], c["memory_id"]))
                uniq.append(c)
        return uniq, [c for c in uniq if c["memory_id"] in items]

    # ================================================================== TRANSITION (status change: dispute, expire, supersede, approval of a procedure, TTL)
    def _service_target(self, p: Principal, op: str, row: dict, ctx: dict, new_status: Optional[str] = None) -> None:
        """A service's allow-list constrains its TARGETS, not just its operation names."""
        if p.kind != "service":
            return
        if p.actor_id == "consolidation":
            if row.get("source_class") == HC and op in ("transition", "support"):
                self._deny(ctx, "human_correction_protected", "a human-corrected memory is changed only by a person")
            if op == "transition" and (new_status != "superseded" or row["memory_type"] not in ("semantic", "episodic")):
                self._deny(ctx, "target_not_permitted_for_service", "consolidation may only supersede semantic or episodic rows")
            if op == "support" and row["memory_type"] != "semantic":
                self._deny(ctx, "target_not_permitted_for_service", "consolidation may only reinforce semantic rows")
        elif p.actor_id == "ttl_expiry":
            now = self.store.now()          # the store's own clock - no Principal and no caller can choose the instant
            if not (row["memory_type"] == "working" and row["status"] == "active" and new_status == "expired" and row.get("expires_at") and row["expires_at"] <= now):
                self._deny(ctx, "target_not_permitted_for_service", "the TTL job may only expire active working memories that are past their expiry time")

    def transition(self, memory_id: str, new_status: str, reason: str, *, principal: Any = None, related: Optional[str] = None, valid_to: Optional[str] = None,
                   expected_version: Optional[int] = None) -> WriteResult:
        ctx = self._ctx("transition", principal, "", memory_id)
        ph = payload_hash("transition", "-", memory_id, new_status, related, valid_to, expected_version)
        self.emit("memory_write_requested", ctx, to_status=new_status)
        p = None
        try:
            p = self._principal(principal, "transition", ctx)
            with self.store.transaction():
                row = self._target(p, memory_id, ctx)
                self._service_target(p, "transition", row, ctx, new_status)
                if new_status not in STATUSES:
                    raise InvalidRecord("invalid_record", "unknown status", operation_id=ctx["operation_id"])
                if expected_version is not None and row["row_version"] != expected_version:
                    raise MemoryWriteRejected("stale_version", "the memory changed since it was read", operation_id=ctx["operation_id"])
                if row["status"] == new_status:
                    self.emit("memory_write_duplicate", ctx, status=new_status, after_commit=True)
                    return WriteResult("duplicate", ctx["operation_id"], memory_id, False, reasons=["already_in_status"])
                if row["status"] in TERMINAL:
                    raise MemoryWriteRejected("invalid_transition", f"a {row['status']} memory cannot change status", operation_id=ctx["operation_id"])
                if not (reason or "").strip():
                    raise MemoryWriteRejected("reason_required", "a reason is required", operation_id=ctx["operation_id"])
                if related:
                    rel = self._visible(p, related)
                    if rel is None or (rel["client_id"] != row["client_id"] and GLOBAL_CLIENT not in (rel["client_id"], row["client_id"])):
                        raise MemoryWriteRejected("cross_client_relationship", "the related memory is not in the same client scope", operation_id=ctx["operation_id"])
                valid_to = None                                       # a caller-supplied valid_to is NEVER used: the end of a window is computed here
                if new_status == "superseded":
                    if related:
                        if row["memory_type"] in ("episodic", "semantic"):
                            self._check_later(self._row_key(rel), self._row_key(row), ctx["operation_id"])
                        valid_to = (rel.get("valid_from") or rel.get("event_date")) if row.get("valid_from") else None
                    elif row.get("valid_from"):
                        valid_to = row["valid_from"]
                    if valid_to and row.get("valid_from") and valid_to < row["valid_from"]:
                        valid_to = row["valid_from"]                  # a window never ends before it starts
                self.emit("memory_write_validated", ctx, from_status=row["status"], to_status=new_status)
                outcome = "superseded" if new_status == "superseded" else "updated"
                with self._persist(ctx):
                    self.store._p_status(memory_id, new_status, valid_to)
                    self.store._p_event(memory_id, row["status"], new_status, reason, p.audit_name, related)
                    self._log(ctx, p, outcome, [], ph, "", memory_id)
                self.emit("memory_write_superseded" if new_status == "superseded" else "memory_write_committed", ctx, memory_id=memory_id, from_status=row["status"],
                          to_status=new_status, after_commit=True)
                return WriteResult(outcome, ctx["operation_id"], memory_id, True)
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph)
            raise

    # ================================================================== LINK / SUPPORT / APPROVAL / CONFLICT (relationships, supporting evidence, support counts, procedure approval, conflict records)
    def link(self, source_id: str, target_id: str, rel_type: str, note: str = "", *, principal: Any = None) -> bool:
        ctx = self._ctx("link", principal, "", source_id)
        ph = payload_hash("link", "-", source_id, target_id, rel_type)
        p = None
        try:
            p = self._principal(principal, "link", ctx)
            with self.store.transaction():
                src = self._target(p, source_id, ctx)
                tgt = self._visible(p, target_id)
                if tgt is None:
                    raise MemoryWriteRejected("memory_not_found", "no such memory in the actor's client scope", operation_id=ctx["operation_id"])
                if len({x for x in (src["client_id"], tgt["client_id"]) if x != GLOBAL_CLIENT}) > 1:
                    raise MemoryWriteRejected("cross_client_relationship", "relationships never cross clients", operation_id=ctx["operation_id"])
                if source_id == target_id:
                    raise InvalidRecord("invalid_record", "a memory cannot relate to itself", operation_id=ctx["operation_id"])
                self._service_target(p, "link", src, ctx)
                with self._persist(ctx):
                    made = self.store._p_link(source_id, target_id, rel_type, note)
                    if made:
                        self._log(ctx, p, "updated", [rel_type], ph, "", source_id)
                self.emit("memory_write_committed" if made else "memory_write_duplicate", ctx, memory_id=source_id, relationship=rel_type, after_commit=True)
                return made
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph)
            raise

    def record_conflict(self, memory_id: str, other_id: str, kind: str, severity: str = "high", detail: str = "", *, principal: Any = None) -> bool:
        """Make a disagreement between two memories of ONE client durable and discoverable (memory_conflicts).  It changes neither memory and resolves nothing."""
        ctx = self._ctx("link", principal, "", memory_id)
        ph = payload_hash("conflict", "-", memory_id, other_id, kind)
        p = None
        try:
            p = self._principal(principal, "link", ctx)
            with self.store.transaction():
                a = self._target(p, memory_id, ctx)
                b = self._visible(p, other_id)
                if b is None or b["client_id"] != a["client_id"]:
                    raise MemoryWriteRejected("memory_not_found", "no such memory in the actor's client scope", operation_id=ctx["operation_id"])
                if kind not in CONFLICT_KINDS:
                    raise InvalidRecord("invalid_record", "unknown conflict kind", operation_id=ctx["operation_id"])
                with self._persist(ctx):
                    made = self.store._p_conflict(dict(conflict_id="CF-" + _h(memory_id, other_id, kind, n=20), client_id=a["client_id"], memory_id=memory_id, other_memory_id=other_id,
                                                       kind=kind, severity=severity, status="open", detail=detail[:300], operation_id=ctx["operation_id"], detected_at=self.store.now()))
                    if made:
                        self._log(ctx, p, "updated", ["conflict:" + kind], ph, "", memory_id)
                if made:
                    self.emit("memory_conflict_detected", ctx, memory_id=memory_id, conflict=kind, severity=severity, other_memory_id=other_id, after_commit=True)
                return made
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph)
            raise

    def support(self, memory_id: str, *, client_id: Optional[str] = None, evidence: Optional[dict] = None, count: Optional[int] = None, approval: Optional[tuple] = None,
                principal: Any = None) -> None:
        op = "approval" if approval else "support"
        ctx = self._ctx(op, principal, "", memory_id)
        ph = payload_hash(op, "-", memory_id, evidence, count, approval)
        p = None
        try:
            p = self._principal(principal, op, ctx)
            with self.store.transaction():
                row = self._target(p, memory_id, ctx)
                if client_id is not None and client_id != row["client_id"]:
                    raise MemoryWriteRejected("memory_not_found", "no such memory in the actor's client scope", operation_id=ctx["operation_id"])
                self._service_target(p, "support", row, ctx)
                ev_row = None
                if evidence is not None:
                    ev = dict(evidence)
                    ev.setdefault("source_type", "conversation")
                    try:
                        self.store.verify_evidence(row["client_id"], ev)
                    except (EvidenceError, KeyError, ValueError, TypeError):
                        raise EvidenceRejected("evidence_unresolved", "a cited source does not resolve for this client", operation_id=ctx["operation_id"]) from None
                    ev_row = ev
                with self._persist(ctx):
                    if ev_row is not None:
                        self.store._p_evidence(memory_id, row["client_id"], ev_row, "resolved")
                    if count is not None:
                        self.store._p_support_count(memory_id, count)
                    if approval:
                        self.store._p_approval(memory_id, approval[0], p.actor_id)          # the approver is the verified identity, never a name the caller supplied
                    self._log(ctx, p, "updated", [], ph, "", memory_id)
                self.emit("memory_write_committed", ctx, memory_id=memory_id, after_commit=True)
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph)
            raise

    # ================================================================== CORRECTION / SUPERSESSION (history is preserved: the old row is never edited or deleted)
    def correct(self, memory_id: str, *, principal: Any, expected_version: Optional[int], new_value: str, evidence: Sequence[dict], reason: str,
                idempotency_key: Optional[str] = None) -> WriteResult:
        ctx = self._ctx("correct", principal, "", memory_id)
        key = idempotency_key or ""
        ph = payload_hash("correct", "-", memory_id, expected_version, new_value, list(evidence), reason)
        self.emit("memory_write_requested", ctx, expected_version=expected_version)
        p = None
        try:
            p = self._principal(principal, "correct", ctx)
            with self.store.transaction():
                row = self._target(p, memory_id, ctx)
                if key:
                    rep = self._replay(ctx, p, row["client_id"], key, True, ph)
                    if rep is not None:
                        return rep
                if expected_version is None:
                    raise MemoryWriteRejected("version_required", "a correction must state the version it was based on", operation_id=ctx["operation_id"])
                if row["row_version"] != expected_version:
                    raise MemoryWriteRejected("stale_version", "the memory changed since it was read", operation_id=ctx["operation_id"])
                if row["memory_type"] not in ("episodic", "semantic") or row["status"] not in ("active", "unconfirmed", "disputed"):
                    raise MemoryWriteRejected("target_not_correctable", f"a {row['status']} {row['memory_type']} memory cannot be corrected", operation_id=ctx["operation_id"])
                if not (reason or "").strip() or not (new_value or "").strip():
                    raise MemoryWriteRejected("reason_required", "a corrected value and a reason are required", operation_id=ctx["operation_id"])
                if not evidence:
                    raise MemoryWriteRejected("provenance_missing", "a correction needs a resolvable source", operation_id=ctx["operation_id"])
                e0 = dict(evidence[0])
                try:
                    turn = self.store.conv.turn(e0["interaction_id"], int(e0["turn_index"]))
                except (KeyError, ValueError, TypeError):
                    turn = None
                today = (turn or {}).get("interaction_datetime", "")[:10] or (row["event_date"] or "")
                eff = max(today, row["valid_from"] or "")            # the corrected version never starts before the one it replaces (windows must not overlap)
                new_id = "MEM-COR-" + _h(memory_id, row["row_version"], new_value, e0.get("interaction_id"), e0.get("turn_index"), n=12)
                sem = row["memory_type"] == "semantic"
                rec = dict(memory_id=new_id, client_id=row["client_id"], memory_type=row["memory_type"], topic=row["topic"], subject=row["subject"], title=f"{row['subject'] or row['topic']}: {new_value}",
                           content=f"{(row['subject'] or row['topic']).replace('_', ' ')} = {new_value} (correction of {memory_id}: {reason.strip()[:200]})", value_text=new_value,
                           corrects_value=row["value_text"], certainty="confirmed" if sem else "correction", source_role="Advisor", status="active", confidence=0.8, event_date=today,
                           valid_from=eff if sem else None, version_no=((row["version_no"] or 0) + 1) if sem else None, supersedes_id=memory_id, origin="orchestrator",
                           content_hash=_h(row["client_id"], row["memory_type"], "correction", memory_id, new_value, e0.get("interaction_id"), e0.get("turn_index"), n=40))
                if sem:
                    det = dict(slot_key=f"{row['topic']}|{row['subject'] or row['topic']}", basis=f"authorized correction by {p.actor_id}", supporting_episodes=1)
                else:
                    det = dict(interaction_id=e0.get("interaction_id"), turn_index=int(e0.get("turn_index", 0)), event_ts=(turn or {}).get("interaction_datetime", today),
                               speaker_role=e0.get("speaker") or "Advisor", event_type="correction", selection_note=f"authorized correction by {p.actor_id}", candidate_id=None)
                cctx = self._ctx("create", p, rec["client_id"], rec["memory_id"])
                cph = payload_hash("create", rec["client_id"], rec, det, list(evidence))
                row2, det2, ev_rows, conflicts, _ = self._validate_create(p, rec, det, evidence, cctx, row)       # the SAME validation as any new memory
                self._check_later(self._evidence_key(e0.get("interaction_id"), e0.get("turn_index")), self._row_key(row), ctx["operation_id"])
                self.emit("memory_write_validated", cctx, verification=row2["verification"], temporal_status=row2["temporal_status"], conflicts=[c["kind"] for c in conflicts])
                with self._persist(ctx):
                    self.store._p_status(memory_id, "superseded", eff or None)           # first: the slot must not hold two active versions, not even inside the transaction
                    self._insert(cctx, row2, det2, ev_rows, conflicts, f"correction of {memory_id}: {reason.strip()[:200]}", p)
                    self.store._p_event(memory_id, row["status"], "superseded", f"corrected by {new_id}: {reason.strip()[:200]}", p.audit_name, new_id)
                    self.store._p_link(new_id, memory_id, "corrects", f"corrects value {row['value_text']}")
                    self.store._p_link(new_id, memory_id, "supersedes", "correction replaces earlier value; earlier row preserved")
                    self._log(cctx, p, "new", [c["kind"] for c in conflicts], cph, "", new_id)
                    self._log(ctx, p, "corrected", [], ph, key, new_id)
                for c in conflicts:
                    self.emit("memory_conflict_detected", cctx, memory_id=new_id, conflict=c["kind"], severity=c["severity"], other_memory_id=c.get("memory_id"), after_commit=True)
                self.emit("memory_write_corrected", ctx, memory_id=new_id, corrected_memory_id=memory_id, after_commit=True)
                return WriteResult("corrected", ctx["operation_id"], new_id, True, conflicts=conflicts, verification=row2["verification"])
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph, key)
            raise

    def supersede(self, old_id: str, new_id: str, reason: str, *, principal: Any, expected_version: Optional[int]) -> WriteResult:
        """Explicit supersession of an old memory by an existing VERIFIED newer one of the same client and slot.  The old row is kept.  Chronology is enforced: the newer
        memory must have been observed STRICTLY after the older one; an equal, earlier or unknown observation time is refused (a late-arriving older fact never replaces a newer one)."""
        ctx = self._ctx("supersede", principal, "", old_id)
        ph = payload_hash("supersede", "-", old_id, new_id, expected_version)
        self.emit("memory_write_requested", ctx, new_memory_id=new_id)
        p = None
        try:
            p = self._principal(principal, "supersede", ctx)
            with self.store.transaction():
                old = self._target(p, old_id, ctx)
                new = self._visible(p, new_id)
                if new is None or new["client_id"] != old["client_id"]:
                    raise MemoryWriteRejected("memory_not_found", "no such memory in the actor's client scope", operation_id=ctx["operation_id"])
                if expected_version is None:
                    raise MemoryWriteRejected("version_required", "state the version the decision was based on", operation_id=ctx["operation_id"])
                if old["row_version"] != expected_version:
                    raise MemoryWriteRejected("stale_version", "the memory changed since it was read", operation_id=ctx["operation_id"])
                if old["status"] not in ("active", "unconfirmed", "disputed") or new["status"] not in ("active", "unconfirmed"):
                    raise MemoryWriteRejected("target_not_correctable", "only live memories can be superseded by live memories", operation_id=ctx["operation_id"])
                if old["verification"] == "verified" and new["verification"] != "verified":
                    raise MemoryWriteRejected("unverified_cannot_override_verified", "an unverified memory cannot replace a verified one", operation_id=ctx["operation_id"])
                if (new["topic"], new["subject"] or new["topic"]) != (old["topic"], old["subject"] or old["topic"]):
                    raise MemoryWriteRejected("slot_mismatch", "the memories describe different subjects", operation_id=ctx["operation_id"])
                if not (reason or "").strip():
                    raise MemoryWriteRejected("reason_required", "a reason is required", operation_id=ctx["operation_id"])
                self._check_later(self._row_key(new), self._row_key(old), ctx["operation_id"])
                valid_to = new["valid_from"] or new["event_date"]
                if valid_to and old.get("valid_from") and valid_to < old["valid_from"]:
                    raise MemoryWriteRejected("supersession_chronology", "the replaced window would end before it began", operation_id=ctx["operation_id"])
                self.emit("memory_write_validated", ctx, from_status=old["status"], to_status="superseded")
                with self._persist(ctx):
                    self.store._p_status(old_id, "superseded", valid_to)
                    self.store._p_event(old_id, old["status"], "superseded", reason.strip()[:300], p.audit_name, new_id)
                    self.store._p_link(new_id, old_id, "supersedes", "explicit supersession; earlier row preserved")
                    self._log(ctx, p, "superseded", [], ph, "", old_id)
                self.emit("memory_write_superseded", ctx, memory_id=old_id, superseded_by=new_id, after_commit=True)
                return WriteResult("superseded", ctx["operation_id"], old_id, True)
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph)
            raise

    # ================================================================== CONFLICT RESOLUTION (a person decides; the history is append-only)
    def resolve_conflict(self, conflict_id: str, decision: str, reason: str, *, principal: Any, new_value: Optional[str] = None, evidence: Sequence[dict] = (),
                         expected_version: Optional[int] = None) -> WriteResult:
        """Close ONE open conflict.  Only an authenticated person whose scope covers the conflict's client: services cannot (operation not in any service's allow-list).
        decision: 'keep' (the memory stays as it is), 'dismiss' (not material), 'correct' (the conflict is settled by a NEW value - which goes through the normal, fully validated
        correct() path; the conflicting memory is never edited).  The resolution is an immutable row (who, when, why, decision, resulting memory); nothing else changes."""
        ctx = self._ctx("link", principal, "", None)
        ph = payload_hash("resolve", "-", conflict_id, decision, new_value, list(evidence), expected_version)
        self.emit("memory_write_requested", ctx, conflict_id=conflict_id, decision=decision)
        p = None
        try:
            p = self._principal(principal, "resolve", ctx)
            with self.store.transaction():
                rows = self.store.query("SELECT * FROM {S}memory_conflicts WHERE conflict_id=?", (conflict_id,))
                c = rows[0] if rows and p.covers(rows[0]["client_id"]) else None
                if c is None:
                    raise MemoryWriteRejected("conflict_not_found", "no such conflict in the actor's client scope", operation_id=ctx["operation_id"])
                ctx.update(client_id=c["client_id"], memory_id=c["memory_id"])
                if self.store.query("SELECT 1 AS x FROM {S}memory_conflict_resolutions WHERE conflict_id=?", (conflict_id,)):
                    raise MemoryWriteRejected("conflict_already_resolved", "this conflict already has a resolution", operation_id=ctx["operation_id"])
                if decision not in ("keep", "dismiss", "correct"):
                    raise InvalidRecord("invalid_record", "unknown decision", operation_id=ctx["operation_id"])
                if not (reason or "").strip():
                    raise MemoryWriteRejected("reason_required", "a reason is required", operation_id=ctx["operation_id"])
                new_id = None
                if decision == "correct":
                    if not (new_value or "").strip() or not evidence:
                        raise MemoryWriteRejected("provenance_missing", "a corrected value needs a value and a resolvable source", operation_id=ctx["operation_id"])
                    new_id = self.correct(c["memory_id"], principal=p, expected_version=expected_version, new_value=new_value, evidence=evidence, reason=reason).memory_id
                with self._persist(ctx):
                    self.store._p_resolution(dict(resolution_id="CR-" + _h(conflict_id, n=20), conflict_id=conflict_id, client_id=c["client_id"], decision=decision, reason=reason.strip()[:500],
                                                  resolved_by=p.actor_id, new_memory_id=new_id, operation_id=ctx["operation_id"], resolved_at=self.store.now()))
                    self._log(ctx, p, "updated", [f"conflict_resolved:{decision}"], ph, "", c["memory_id"])
                self.emit("memory_conflict_resolved", ctx, conflict_id=conflict_id, decision=decision, after_commit=True)
                return WriteResult("updated", ctx["operation_id"], new_id or c["memory_id"], True, reasons=[f"conflict_resolved:{decision}"])
        except MemoryWriteRejected as e:
            self._reject(ctx, p, e, ph)
            raise
