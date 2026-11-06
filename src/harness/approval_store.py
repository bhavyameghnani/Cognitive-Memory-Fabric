"""Durable SQLite approval store (harness P1.2).

An approval authorizes ONE exact proposal (category, operation, clients, payload, data version AND requester) for a limited time, and is consumed by the first
successful release.  Every state change is a single SQLite transaction (BEGIN IMMEDIATE) made of a conditional UPDATE plus its audit event, so a crash, an error
or a concurrent request can never leave a half-applied decision, and two releases can never both succeed.  The database enforces the same rules with CHECK
constraints and triggers (sql/110_cmf_approvals_schema.sql), so even a buggy caller cannot rewrite history or approve its own request.

What the store knows: stable authenticated subject ids (the JWT `sub`), roles, hashes and timestamps.  It never reads a token, a secret or a request body.
Errors raised to callers carry no driver text, file paths, payloads or evidence.

Not guaranteed (see docs/harness.md): the file is not encrypted and not tamper-proof against someone who can write it directly (they can drop the triggers); the
payload_hash only DETECTS a changed payload at release.  One human holding two different subject ids is not detectable here.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from harness.permission_gate import PermissionDenied

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE, SCHEMA_VERSION = "110_cmf_approvals_schema.sql", "110"
DEFAULT_URL = "sqlite:///build/cmf_approvals.db"
APPROVER_ROLES = ("supervisor", "admin")
ACTIVE = ("pending", "approved")
GENERIC = "approval store unavailable"


class ApprovalStoreError(RuntimeError):
    """The approval database is missing, corrupt, locked or otherwise unusable.  The message is generic on purpose; the original error is only the __cause__."""

    def __init__(self, message: str = GENERIC) -> None:
        super().__init__(message)


def norm_subject(s: Optional[str]) -> str:
    """Stable-subject comparison key: whitespace- and case-insensitive.  (Display names never reach this module.)"""
    return (s or "").strip().casefold()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------------------------------------------------------------------------
@dataclass
class ApprovalRecord:
    approval_id: str
    fingerprint: str
    category: str
    operation: str
    requested_by: str
    reason: str
    client_ids: tuple
    created_at: str
    status: str = "pending"                  # pending | approved | rejected | consumed | expired   ('expired' is also reported when the clock passed expires_at)
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_reason: Optional[str] = None
    draft: Any = None                        # the exact object awaiting release; present only while pending / approved; never shown to an API caller before release
    requester_role: str = ""
    request_id: str = ""
    data_version: str = ""
    payload_hash: str = ""
    expires_at: str = ""
    consumed_at: Optional[str] = None
    consumed_by: Optional[str] = None
    decided_role: Optional[str] = None

    def public(self) -> dict:
        """Metadata only: never the draft, the payload hash, the fingerprint or the data version."""
        return dict(approval_id=self.approval_id, category=self.category, operation=self.operation, requested_by=self.requested_by, reason=self.reason,
                    client_ids=list(self.client_ids), status=self.status, created_at=self.created_at, expires_at=self.expires_at, decided_by=self.decided_by,
                    decided_at=self.decided_at, decision_reason=self.decision_reason, consumed_at=self.consumed_at)


class NotApproved(PermissionDenied):
    """Raised by consume() for a request that is still pending or was rejected; the gate turns it into ApprovalRequired."""

    def __init__(self, record: ApprovalRecord) -> None:
        super().__init__("not_approved", f"{record.approval_id} is {record.status}")
        self.record = record


# ---------------------------------------------------------------------------------------------------------------------------------------------
def encode_draft(draft: Any) -> tuple:
    """-> (payload text or None, sha256 of that text).  Pydantic models keep their class so the exact object can be rebuilt on release."""
    if draft is None:
        return None, _sha("null")
    if hasattr(draft, "model_dump_json"):
        cls = f"{type(draft).__module__}.{type(draft).__qualname__}"
        payload = json.dumps({"kind": "model", "cls": cls, "data": json.loads(draft.model_dump_json())}, sort_keys=True)
    else:
        payload = json.dumps({"kind": "json", "data": draft}, sort_keys=True, default=str)
    return payload, _sha(payload)


class SqliteApprovalStore:
    durable = True

    def __init__(self, url: str = ":memory:", *, ttl_seconds: int = 14400, clock: Optional[Callable[[], datetime]] = None, draft_types: Optional[dict] = None) -> None:
        self.ttl_seconds, self.clock = int(ttl_seconds), clock or _now
        self._draft_types = dict(draft_types or {})
        self._lock = threading.RLock()
        self._live: "weakref.WeakValueDictionary[str, ApprovalRecord]" = weakref.WeakValueDictionary()
        raw = url.strip()
        if raw.startswith("sqlite:///"):
            raw = raw[len("sqlite:///"):]
        elif "://" in raw:
            raise ApprovalStoreError("unsupported approval database url (SQLite only)")
        self._mem = raw in (":memory:", "")
        self.durable = not self._mem
        if self._mem:
            self.path = ":memory:"
            self._shared = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
            self._shared.execute("PRAGMA foreign_keys = ON")
        else:
            p = Path(raw)
            p = p if p.is_absolute() else ROOT / p
            self.path = str(p)
            self._shared = None
        self._migrate()

    # ------------------------------------------------------------------ plumbing
    def _open(self) -> sqlite3.Connection:
        if self._mem:
            return self._shared
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            return con
        except (sqlite3.Error, OSError) as e:
            raise ApprovalStoreError() from e

    @contextmanager
    def _tx(self):
        """One write transaction.  Raising inside the block rolls EVERYTHING back (decision/consumption and its event together)."""
        with self._lock:
            con = self._open()
            if self._mem:
                con.row_factory = sqlite3.Row
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.execute("COMMIT")
            except ApprovalStoreError:
                self._rollback(con)
                raise
            except sqlite3.Error as e:
                self._rollback(con)
                raise ApprovalStoreError() from e
            except BaseException:
                self._rollback(con)
                raise
            finally:
                if not self._mem:
                    con.close()

    @staticmethod
    def _rollback(con: sqlite3.Connection) -> None:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @contextmanager
    def _read(self):
        with self._lock:
            con = self._open()
            if self._mem:
                con.row_factory = sqlite3.Row
            try:
                yield con
            except sqlite3.Error as e:
                raise ApprovalStoreError() from e
            finally:
                if not self._mem:
                    con.close()

    def _migrate(self) -> None:
        try:
            with self._tx() as con:
                have = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='approval_schema_migrations'").fetchone()
                done = have is not None and con.execute("SELECT 1 FROM approval_schema_migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone() is not None
                if done:
                    return
                script = (ROOT / "sql" / SCHEMA_FILE).read_text(encoding="utf-8")
                for stmt in _statements(script):
                    con.execute(stmt)
                con.execute("INSERT OR IGNORE INTO approval_schema_migrations (version, description, applied_at) VALUES (?,?,?)",
                            (SCHEMA_VERSION, "approval requests + append-only approval events", _ts(self.clock())))
            if not self._mem:
                try:
                    Path(self.path).chmod(0o600)                       # best effort (no-op semantics on Windows): the file holds pending reports
                except OSError:
                    pass
        except OSError as e:
            raise ApprovalStoreError() from e

    # ------------------------------------------------------------------ rows <-> records
    def _decode(self, payload: Optional[str]) -> Any:
        if payload is None:
            return None
        try:
            doc = json.loads(payload)
            if doc["kind"] == "json":
                return doc["data"]
            cls = self._draft_class(doc["cls"])
            return cls.model_validate(doc["data"])
        except ApprovalStoreError:
            raise
        except Exception as e:                                          # noqa: BLE001 - a payload that cannot be rebuilt must never be released
            raise ApprovalStoreError() from e

    def _draft_class(self, name: str):
        if name not in self._draft_types:
            from schemas.report import AdvisorReport
            self._draft_types.setdefault("schemas.report.AdvisorReport", AdvisorReport)
        if name not in self._draft_types:
            raise ApprovalStoreError()                                  # only known classes are ever rebuilt from stored JSON
        return self._draft_types[name]

    def _record(self, row: sqlite3.Row, now: str) -> ApprovalRecord:
        status = row["status"]
        expired = status in ACTIVE and row["expires_at"] <= now
        status = "expired" if expired else status
        draft = None if (expired or status not in ACTIVE) else self._decode(row["report_payload"])
        fields = dict(approval_id=row["approval_id"], fingerprint=row["fingerprint"], category=row["category"], operation=row["operation"], requested_by=row["requester_subject"],
                      reason=row["reason"], client_ids=tuple(c for c in row["client_id"].split(",") if c), created_at=row["created_at"], status=status, decided_by=row["decided_by"],
                      decided_at=row["decided_at"], decision_reason=row["decision_reason"], draft=draft, requester_role=row["requester_role"], request_id=row["request_id"],
                      data_version=row["data_version"], payload_hash=row["payload_hash"], expires_at=row["expires_at"], consumed_at=row["consumed_at"],
                      consumed_by=row["consumed_by"], decided_role=row["decided_role"])
        live = self._live.get(row["approval_id"])
        if live is None:
            live = ApprovalRecord(**fields)
            self._live[row["approval_id"]] = live
        else:                                                           # a record object a caller still holds reflects the database, never the other way round
            for k, v in fields.items():
                setattr(live, k, v)
        return live

    def _ts_now(self) -> str:
        return _ts(self.clock())

    def _insert_event(self, con: sqlite3.Connection, approval_id: str, event_type: str, actor_subject: str, actor_role: str, reason: str, fingerprint: str) -> None:
        con.execute("INSERT INTO approval_events (approval_id, actor_subject, actor_role, event_type, reason, fingerprint, created_at) VALUES (?,?,?,?,?,?,?)",
                    (approval_id, actor_subject, actor_role or "", event_type, reason or "", fingerprint or "", self._ts_now()))

    def _sweep(self, con: sqlite3.Connection, now: str) -> None:
        """Move requests whose time ran out to 'expired', scrub their payload and audit it - inside the caller's transaction."""
        for r in con.execute("SELECT approval_id, fingerprint FROM approval_requests WHERE status IN ('pending','approved') AND expires_at <= ?", (now,)).fetchall():
            n = con.execute("UPDATE approval_requests SET status='expired', report_payload=NULL WHERE approval_id=? AND status IN ('pending','approved')", (r["approval_id"],)).rowcount
            if n == 1:
                self._insert_event(con, r["approval_id"], "expired", "system", "system", "validity period elapsed", r["fingerprint"])

    # ------------------------------------------------------------------ creating
    def create(self, *, fingerprint: str, category: str, operation: str, requested_by: str, requester_role: str, reason: str, client_ids: tuple, request_id: str,
               data_version: str, draft: Any = None) -> tuple:
        """-> (record, created).  An identical request (same fingerprint, so same requester/payload/data version) that is still pending or approved is returned as is."""
        if not (requested_by or "").strip():
            raise PermissionDenied("missing_identity", "an approval needs the stable subject id of the requester")
        payload, phash = encode_draft(draft)
        with self._tx() as con:
            now_dt = self.clock()
            now = _ts(now_dt)
            self._sweep(con, now)
            row = con.execute("SELECT * FROM approval_requests WHERE fingerprint=? AND status IN ('pending','approved') AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
                              (fingerprint, now)).fetchone()
            if row is not None:
                return self._record(row, now), False
            aid = "APR-" + secrets.token_hex(16)
            con.execute("INSERT INTO approval_requests (approval_id, request_id, requester_subject, requester_role, client_id, category, operation, reason, report_payload, "
                        "payload_hash, data_version, fingerprint, status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                        (aid, request_id or "", requested_by.strip(), requester_role or "", ",".join(sorted(client_ids)), category, operation, (reason or "")[:300], payload, phash,
                         data_version or "", fingerprint, now, _ts(now_dt + timedelta(seconds=self.ttl_seconds))))
            self._insert_event(con, aid, "requested", requested_by.strip(), requester_role, "", fingerprint)
            row = con.execute("SELECT * FROM approval_requests WHERE approval_id=?", (aid,)).fetchone()
            return self._record(row, now), True

    # ------------------------------------------------------------------ reading
    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        with self._read() as con:
            row = con.execute("SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone()
            return self._record(row, self._ts_now()) if row is not None else None

    def find(self, fingerprint: str) -> list:
        with self._read() as con:
            now = self._ts_now()
            return [self._record(r, now) for r in con.execute("SELECT * FROM approval_requests WHERE fingerprint=? ORDER BY created_at", (fingerprint,)).fetchall()]

    def all(self) -> list:
        with self._read() as con:
            now = self._ts_now()
            return [self._record(r, now) for r in con.execute("SELECT * FROM approval_requests ORDER BY created_at, approval_id").fetchall()]

    def list_pending(self, limit: int = 200) -> list:
        self.purge_expired()
        with self._read() as con:
            now = self._ts_now()
            rows = con.execute("SELECT * FROM approval_requests WHERE status='pending' AND expires_at > ? ORDER BY created_at DESC, approval_id LIMIT ?", (now, int(limit))).fetchall()
            return [self._record(r, now) for r in rows]

    def events(self, approval_id: str) -> list:
        with self._read() as con:
            return [dict(r) for r in con.execute("SELECT * FROM approval_events WHERE approval_id=? ORDER BY event_id", (approval_id,)).fetchall()]

    def purge_expired(self) -> None:
        """Expire (and scrub) everything whose validity period elapsed.  Safe to call at any time."""
        with self._tx() as con:
            self._sweep(con, self._ts_now())

    # ------------------------------------------------------------------ deciding
    def decide(self, approval_id: str, approver_subject: str, approver_role: str, approve: bool, reason: str) -> ApprovalRecord:
        """Approve or reject, atomically with its audit event.  Raises KeyError (unknown) or PermissionDenied(code).  Refusals are audited as decision_denied."""
        subject, role, why = (approver_subject or "").strip(), (approver_role or "").strip().lower(), (reason or "").strip()
        err: Optional[PermissionDenied] = None
        rec: Optional[ApprovalRecord] = None
        with self._tx() as con:
            now = self._ts_now()
            self._sweep(con, now)
            row = con.execute("SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if not subject:
                err = PermissionDenied("missing_identity", "the approver has no stable subject id")
            elif role not in APPROVER_ROLES:
                err = PermissionDenied("role_not_permitted", f"role {role or '?'} may not approve")
            elif row["status"] == "expired":
                err = PermissionDenied("approval_expired", f"{approval_id} expired before a decision")
            elif row["status"] != "pending":
                err = PermissionDenied("already_decided", f"{approval_id} is already {row['status']}")
            elif norm_subject(subject) == norm_subject(row["requester_subject"]):
                err = PermissionDenied("self_approval", "the requester cannot approve their own request")
            elif not why:
                err = PermissionDenied("reason_required", "an approval decision needs a reason")
            else:
                new = "approved" if approve else "rejected"
                n = con.execute("UPDATE approval_requests SET status=?, decided_by=?, decided_role=?, decided_at=?, decision_reason=?, "
                                "report_payload=CASE WHEN ?='rejected' THEN NULL ELSE report_payload END "
                                "WHERE approval_id=? AND status='pending' AND expires_at > ?", (new, subject, role, now, why, new, approval_id, now)).rowcount
                if n != 1:
                    err = PermissionDenied("already_decided", f"{approval_id} was decided concurrently")
                else:
                    self._insert_event(con, approval_id, new, subject, role, why, row["fingerprint"])
                    rec = self._record(con.execute("SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone(), now)
        if err is not None:
            self.record_denial(approval_id, subject, role, "decision_denied", err.code)
            raise err
        return rec

    # ------------------------------------------------------------------ releasing
    def consume(self, approval_id: str, fingerprint: str, actor_subject: str, actor_role: str) -> Any:
        """Release = consume.  In ONE transaction: re-check everything, flip approved -> consumed with a conditional UPDATE, write the release event.  Returns the draft
        only after the commit.  Raises KeyError, NotApproved (pending / rejected) or PermissionDenied(approval_invalidated | already_consumed | approval_expired |
        not_requester | missing_identity)."""
        subject, role = (actor_subject or "").strip(), (actor_role or "").strip().lower()
        err: Optional[PermissionDenied] = None
        payload: Optional[str] = None
        with self._tx() as con:
            now = self._ts_now()
            self._sweep(con, now)
            row = con.execute("SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if not subject:
                err = PermissionDenied("missing_identity", "no stable subject id")
            elif row["fingerprint"] != fingerprint:
                err = PermissionDenied("approval_invalidated", "the proposal, requester, client or data version changed since approval; a fresh approval is required")
            elif row["status"] == "consumed":
                err = PermissionDenied("already_consumed", "this approval has already been used")
            elif row["status"] == "expired":
                err = PermissionDenied("approval_expired", "this approval expired")
            elif row["status"] != "approved":
                err = NotApproved(self._record(row, now))
            elif norm_subject(subject) != norm_subject(row["requester_subject"]):
                err = PermissionDenied("not_requester", "only the requester of this answer may release it")
            elif (not row["decided_by"] or norm_subject(row["decided_by"]) == norm_subject(row["requester_subject"]) or (row["decided_role"] or "") not in APPROVER_ROLES):
                err = PermissionDenied("approval_invalidated", "the recorded decision is not a valid supervisor decision")
            elif _sha(row["report_payload"]) != row["payload_hash"] if row["report_payload"] is not None else row["payload_hash"] != _sha("null"):
                err = PermissionDenied("approval_invalidated", "the stored payload no longer matches its hash")
            else:
                n = con.execute("UPDATE approval_requests SET status='consumed', consumed_at=?, consumed_by=?, report_payload=NULL "
                                "WHERE approval_id=? AND status='approved' AND consumed_at IS NULL AND fingerprint=? AND expires_at > ?",
                                (now, subject, approval_id, fingerprint, now)).rowcount
                if n != 1:
                    err = PermissionDenied("already_consumed", "this approval was used concurrently")
                else:
                    self._insert_event(con, approval_id, "released", subject, role, "released to the requester", fingerprint)
                    payload = row["report_payload"]
        if err is not None:
            if not isinstance(err, NotApproved) or err.record.status != "pending":
                self.record_denial(approval_id, subject, role, "release_denied", err.code if not isinstance(err, NotApproved) else err.record.status)
            raise err
        return self._decode(payload)

    # ------------------------------------------------------------------ auditing refusals
    def record_denial(self, approval_id: str, actor_subject: str, actor_role: str, event_type: str, code: str) -> None:
        """Best-effort audit of a REFUSED attempt, in its own transaction (the refused operation was rolled back).  Never raises: auditing cannot turn a refusal into a success."""
        try:
            with self._tx() as con:
                if con.execute("SELECT 1 FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone() is None:
                    return
                self._insert_event(con, approval_id, event_type, (actor_subject or "").strip() or "unknown", actor_role, str(code)[:80], "")
        except Exception:                                                # noqa: BLE001
            pass


def _statements(script: str):
    """Split a SQL script into statements (triggers contain ';' inside BEGIN ... END)."""
    buf: list = []
    for line in script.splitlines():
        if line.strip().startswith("--") or not line.strip():
            continue
        buf.append(line)
        joined = "\n".join(buf)
        if sqlite3.complete_statement(joined):
            yield joined
            buf = []
    if buf:
        yield "\n".join(buf)


class ApprovalStore(SqliteApprovalStore):
    """Volatile store (SQLite ':memory:').  For unit tests and development tooling ONLY: the production profile refuses it (see open_approval_store)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(":memory:", **kw)


def open_approval_store(config, clock: Optional[Callable[[], datetime]] = None) -> SqliteApprovalStore:
    """The store a Harness uses: CMF_APPROVAL_DATABASE_URL (or config.approval_database_url, or build/cmf_approvals.db).  Production never gets a volatile store."""
    import os
    from harness.config import ConfigError
    url = (getattr(config, "approval_database_url", "") or os.environ.get("CMF_APPROVAL_DATABASE_URL", "") or DEFAULT_URL).strip()
    if url in (":memory:", "sqlite:///:memory:", "sqlite://") and config.profile == "production":
        raise ConfigError("the production profile requires a durable approval database (CMF_APPROVAL_DATABASE_URL=sqlite:///path), not an in-memory store")
    return SqliteApprovalStore(url, ttl_seconds=getattr(config, "approval_ttl_seconds", 14400), clock=clock)
