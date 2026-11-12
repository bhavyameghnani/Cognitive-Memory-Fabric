"""MemoryStore: the ONLY module in the memory package that imports a database driver.

CMF_MEMORY_DATABASE_URL selects the backend:
    sqlite:///build/cmf_memory.db           (default; developed and tested)
    postgresql://user:pass@host:5432/db     (needs psycopg v3; schema cmf_memory)   -- NOT yet run against a live server
SQL below is portable: '?' placeholders and the token {S} for the schema prefix are translated per backend (same convention as cmf_data.backend).
The LLM never reaches this module.  Evidence is re-verified against data/conversations.csv before every insert.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from agents.memory.config import GLOBAL_CLIENT, database_url
from agents.memory.errors import EvidenceError, MemoryIntegrityUnavailable, MemoryResetRefused  # noqa: F401  (EvidenceError re-exported: existing importers use it from here)
from schemas.memory import EvidenceRef, MemoryItem, Relationship
from services.conversations import Conversations, shared

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "sql"
SCHEMA_FILE, DROP_FILE, SCHEMA_VERSION = "100_cmf_memory_schema.sql", "199_drop_cmf_memory.sql", "100"
TABLES = ["memory_reverifications", "memory_conflict_resolutions", "memory_conflicts", "memory_write_log", "memory_candidates", "memory_lifecycle_events", "memory_relationships", "memory_evidence", "working_context", "procedural_details",
          "semantic_details", "episodic_details", "memory_records", "ingestion_log", "schema_migrations"]


log = logging.getLogger("cmf.memory")
MAINTENANCE_ENV = "CMF_MEMORY_MAINTENANCE"        # an operator sets this in their own shell to allow an offline reset of a POPULATED store
_READ_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list"}      # PRAGMAs that only describe the schema
_SET_PRAGMAS_READ_ONLY = {"user_version", "foreign_keys", "busy_timeout"}                         # PRAGMAs an application may READ (no assignment)
_DENIED_ACTIONS = tuple(getattr(sqlite3, n) for n in (
    "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER",
    "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_ALTER_TABLE", "SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_REINDEX", "SQLITE_CREATE_VTABLE",
    "SQLITE_DROP_VTABLE") if hasattr(sqlite3, n))


def h(*parts: Any, n: int = 16) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:n]


def iso(dt: datetime) -> str:
    """Stored times are naive UTC.  A timezone-AWARE datetime is converted to UTC first (P1.4.3): dropping the offset without converting would shift the instant."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0, tzinfo=None).isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MemoryStore:
    def __init__(self, url: Optional[str] = None, conv: Optional[Conversations] = None, clock: Callable[[], datetime] = utcnow) -> None:
        self.url, self.conv, self.clock, self._depth = url or database_url(), conv or shared(), clock, 0
        self._lock = threading.RLock()                     # ONE connection is shared by API threads: serialise statements and transactions
        self._gate_owner: Optional[int] = None            # thread id currently inside the integrity boundary's write gate
        self._after: list = []                             # (fire, on_rollback) callbacks released only when the OUTERMOST transaction commits
        self._sp = 0
        self._ev_last = 0
        self._maint_owner: Optional[int] = None           # thread id inside a trusted schema-maintenance block (init / migration)
        self.observer_failures: deque = deque(maxlen=100)     # (callback name, error text): observers that failed AFTER a commit; never the write itself
        if self.url.startswith("sqlite:///"):
            self.kind, self._s = "sqlite", ""
            raw = self.url[len("sqlite:///"):]
            if raw == ":memory:":
                path = ":memory:"
            else:
                p = Path(raw)
                p = p if p.is_absolute() else ROOT / p
                p.parent.mkdir(parents=True, exist_ok=True)
                path = str(p)
            self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 10000")
            # the guard triggers call this function: only a connection that registered it (i.e. a MemoryStore) can write to the guarded tables, and only inside the boundary
            self._conn.create_function("cmf_write_allowed", 0, lambda: 1 if self._gate_owner == threading.get_ident() else 0)
            # schema changes (DROP TRIGGER, ALTER, ATTACH, writable PRAGMA ...) are refused through execute()/query() outside a trusted maintenance block
            self._conn.set_authorizer(self._authorize)
        elif self.url.startswith(("postgresql://", "postgres://")):
            self.kind, self._s = "postgresql", "cmf_memory."
            import psycopg  # only needed for this backend
            self._conn = psycopg.connect(self.url)
        else:
            raise ValueError("unsupported CMF_MEMORY_DATABASE_URL (use sqlite:///... or postgresql://...)")
        from agents.memory.integrity import MemoryIntegrityBoundary    # late: integrity imports rules/config only, but keeps the import graph acyclic
        self.boundary = MemoryIntegrityBoundary(self)

    # ------------------------------------------------------------------ plumbing
    def _authorize(self, action, arg1, arg2, _db, _source) -> int:
        if self._maint_owner == threading.get_ident():
            return sqlite3.SQLITE_OK
        if action in _DENIED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            name = (arg1 or "").lower()
            if name in _READ_PRAGMAS or (name in _SET_PRAGMAS_READ_ONLY and arg2 is None):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def _maintenance(self) -> Iterator[None]:
        """Trusted schema work (init, migrations, guard installation) for THIS thread only.  Leaving the block re-arms the authorizer, which also expires every statement that
        was prepared while it was open (a cached DDL statement can then not be replayed by an application call)."""
        with self._lock:
            prev, self._maint_owner = self._maint_owner, threading.get_ident()
            try:
                yield
            finally:
                self._maint_owner = prev
                if self.kind == "sqlite":
                    self._conn.set_authorizer(self._authorize)

    def observer_failed(self, name: str, exc: BaseException) -> None:
        """A tracer / audit-event observer failed.  The write it reported is unaffected; the failure is kept here and logged, never raised into the caller."""
        self.observer_failures.append((name, f"{type(exc).__name__}: {exc}"[:300]))
        try:
            log.error("memory observer %s failed after the write: %s", name, type(exc).__name__)
        except Exception:                                         # a broken logging setup must not turn a committed write into an error either
            pass

    def now(self) -> str:
        return iso(self.clock())

    def _sql(self, sql: str) -> str:
        sql = sql.replace("{S}", self._s)
        return sql.replace("?", "%s") if self.kind == "postgresql" else sql

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Raw SQL.  Writes to the guarded memory tables are refused by the database triggers unless they come from the integrity boundary."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            return cur.rowcount

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One transaction per outermost block (BEGIN IMMEDIATE: the write lock is taken up front, so concurrent writers are serialised and a check-then-write cannot race).
        A nested block is a SAVEPOINT: if it fails, ONLY its own changes are undone.  Callbacks registered with on_commit() run after the outermost COMMIT, never before."""
        with self._lock:
            outer, sp, mark = self._depth == 0, None, len(self._after)
            if self.kind == "sqlite":
                if outer:
                    self._conn.execute("BEGIN IMMEDIATE")
                else:
                    self._sp += 1
                    sp = f"sp{self._sp}"
                    self._conn.execute(f"SAVEPOINT {sp}")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                dropped = self._after[mark:]
                del self._after[mark:]
                if self.kind == "sqlite":
                    if outer:
                        self._conn.execute("ROLLBACK")
                    else:
                        self._conn.execute(f"ROLLBACK TO {sp}")
                        self._conn.execute(f"RELEASE {sp}")
                elif outer:
                    self._conn.rollback()
                for _, on_rollback in dropped:
                    if on_rollback:
                        try:
                            on_rollback()
                        except Exception as exc:              # noqa: BLE001  (an observer must not mask the error that caused the rollback)
                            self.observer_failed("on_rollback", exc)
                raise
            else:
                self._depth -= 1
                if self.kind == "sqlite":
                    if outer:
                        self._conn.execute("COMMIT")
                    else:
                        self._conn.execute(f"RELEASE {sp}")
                elif outer:
                    self._conn.commit()
                if outer:
                    fired, self._after = self._after, []
                    for fire, _ in fired:                         # the data is ALREADY committed: an observer failure must never make the write look failed
                        try:
                            fire()
                        except Exception as exc:                  # noqa: BLE001
                            self.observer_failed("after_commit", exc)

    def on_commit(self, fire, on_rollback=None) -> None:
        """Run `fire` only once the data is durable (after the outermost COMMIT).  Outside a transaction it runs immediately."""
        with self._lock:
            if self._depth == 0:
                fire()
            else:
                self._after.append((fire, on_rollback))

    @contextmanager
    def _gate(self) -> Iterator[None]:
        """Open the write gate for THIS thread.  Only the integrity boundary calls this; the guard triggers consult it."""
        with self._lock:
            prev, self._gate_owner = self._gate_owner, threading.get_ident()
            try:
                yield
            finally:
                self._gate_owner = prev

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ schema
    def _script(self, name: str) -> list[str]:
        text = "\n".join(ln for ln in (SQL_DIR / name).read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("--"))
        stmts = [s.strip() for s in text.split(";") if s.strip()]
        if self.kind == "sqlite":
            stmts = [s.replace("cmf_memory.", "") for s in stmts if not s.upper().startswith("CREATE SCHEMA")]
        return stmts

    def init(self, reset: bool = False) -> None:
        """Create / upgrade the schema (idempotent; also re-creates guard triggers an offline tool dropped).  reset=True drops and recreates ONLY the memory tables - and is refused
        on a store that already holds memories or audit history unless the OPERATOR has set CMF_MEMORY_MAINTENANCE=1 (an offline maintenance decision, not an application call)."""
        exists = self.kind == "sqlite" and bool(self.query("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_records'"))
        if self.kind == "postgresql":
            exists = bool(self.query("SELECT 1 AS x FROM information_schema.tables WHERE table_schema='cmf_memory' AND table_name='memory_records'"))
        if exists and reset and self.kind == "sqlite" and os.environ.get(MAINTENANCE_ENV) != "1":
            held = sum(self.count(t) for t in ("memory_records", "memory_write_log") if self.query("SELECT 1 AS x FROM sqlite_master WHERE type='table' AND name=?", (t,)))
            if held:
                raise MemoryResetRefused(f"refusing to erase a store holding {held} memory / audit rows: reset is an offline maintenance operation (set {MAINTENANCE_ENV}=1 to confirm)")
        with self._maintenance(), self.transaction():
            if exists and reset:
                if self.kind == "sqlite":
                    for t in TABLES:
                        self.execute(f"DROP TABLE IF EXISTS {t}")
                else:
                    for s in self._script(DROP_FILE):
                        self.execute(s)
                exists = False
            if not exists:
                for s in self._script(SCHEMA_FILE):
                    self.execute(s)
                self.execute("INSERT INTO {S}schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                             (SCHEMA_VERSION, "initial cmf_memory schema", self.now()))
            if self.kind == "sqlite" and not exists:
                self.execute("PRAGMA user_version = 0")                      # a fresh or reset schema has none of the 120+ additions yet
            if self.kind != "sqlite":
                raise MemoryIntegrityUnavailable("the P1.4 memory integrity migrations (120, 121) are SQLite-only; durable memory writes are refused on this backend")
            self._migrate()

    # ------------------------------------------------------------------ migrations 120 (P1.4) and 121 (P1.4.1): additive, one transaction, idempotent
    MIGRATION = "123"
    NEW_COLUMNS = {
        "memory_records": [
            ("verification", "TEXT NOT NULL DEFAULT 'unverified' CHECK (verification IN ('verified', 'unverified'))"),
            ("verification_basis", "TEXT NOT NULL DEFAULT ''"), ("observed_at", "TEXT"),
            ("temporal_status", "TEXT NOT NULL DEFAULT 'unknown' CHECK (temporal_status IN ('current', 'historical', 'planned', 'superseded', 'disputed', 'unknown'))"),
            ("source_class", "TEXT NOT NULL DEFAULT 'legacy' CHECK (source_class IN ('observed_transcript', 'llm_extraction', 'derived', 'curated', 'tool_summary', 'human_correction', 'system', 'legacy'))"),
            ("created_by", "TEXT NOT NULL DEFAULT ''"), ("request_id", "TEXT NOT NULL DEFAULT ''"), ("operation_id", "TEXT NOT NULL DEFAULT ''"),
            ("row_version", "INTEGER NOT NULL DEFAULT 1")],
        "memory_evidence": [("resolution", "TEXT NOT NULL DEFAULT 'unrecorded' CHECK (resolution IN ('resolved', 'unresolvable', 'unrecorded'))"), ("verified_at", "TEXT")],
    }
    GUARDED = ['memory_records', 'episodic_details', 'semantic_details', 'procedural_details', 'working_context', 'memory_evidence', 'memory_relationships', 'memory_lifecycle_events', 'memory_write_log', 'memory_conflicts', 'memory_conflict_resolutions', 'memory_reverifications']
    MR_GUARDED_COLUMNS = ['client_id', 'memory_type', 'topic', 'subject', 'title', 'content', 'value_text', 'corrects_value', 'certainty', 'source_role', 'status', 'confidence', 'event_date', 'valid_from', 'valid_to', 'version_no', 'supersedes_id', 'origin', 'model', 'content_hash', 'verification', 'verification_basis', 'observed_at', 'temporal_status', 'source_class', 'created_by', 'request_id', 'operation_id', 'row_version', 'memory_id', 'created_at']
    IMMUTABLE_COLUMNS = ['memory_id', 'client_id', 'memory_type', 'topic', 'subject', 'title', 'content', 'value_text', 'corrects_value', 'certainty', 'origin', 'content_hash', 'created_at', 'created_by', 'source_class', 'operation_id', 'event_date', 'supersedes_id', 'verification', 'verification_basis']
    UPDATE_GUARDED = ("episodic_details", "semantic_details", "procedural_details", "working_context", "memory_evidence", "memory_relationships", "memory_conflicts", "memory_conflict_resolutions", "memory_reverifications")

    repaired_triggers: dict = {}

    def _migrate(self) -> None:
        with self.transaction():
            version = int(self.query("PRAGMA user_version")[0]["user_version"])     # recorded in the DB header, written LAST inside this same transaction
            with self._gate(), self._maintenance():
                if version < 120:
                    self._migrate_120()
                if version < 121:
                    for s in self._script("121_cmf_memory_conflicts.sql"):
                        self.execute(s)
                if version < 122:
                    for s in self._script("122_cmf_memory_conflict_resolutions.sql"):
                        self.execute(s)
                for s in self._script("124_cmf_memory_reverifications.sql"):      # idempotent, runs on every init (no version bump: older databases pick the table up here)
                    self.execute(s)
                self._install_guards()
                self._ensure_slot_index()
                if version < int(self.MIGRATION):
                    self.execute(f"PRAGMA user_version = {int(self.MIGRATION)}")

    def _ensure_slot_index(self) -> bool:
        """One ACTIVE semantic version per (client, topic, subject) slot, also as a database constraint (the boundary enforces it first).  A legacy database that already holds
        duplicates keeps working: the index is skipped (False) rather than failing the migration, and the boundary still refuses to create further competing versions."""
        if self.query("SELECT 1 AS x FROM sqlite_master WHERE type='index' AND name='uq_active_semantic_slot_canonical'"):
            return True
        collisions = self.slot_collisions()
        if collisions:                                  # P1.4.3: nothing is merged, deleted or rewritten; new competing writes are refused by the boundary instead
            log.warning("legacy duplicate active semantic versions found (%d slot(s)): the canonical one-active-version index was not created: %s", len(collisions), collisions[:5])
            return False
        self.execute("DROP INDEX IF EXISTS uq_active_semantic_slot")                                  # the P1.4.2 index compared the RAW spelling
        self.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_active_semantic_slot_canonical ON memory_records (client_id, {self.CANON_TOPIC}, {self.CANON_SUBJECT}) "
                     "WHERE memory_type='semantic' AND status='active'")
        return True

    CANON_TOPIC = "replace(replace(lower(trim(topic)), ' ', '_'), '-', '_')"
    CANON_SUBJECT = "replace(replace(lower(trim(COALESCE(NULLIF(trim(subject), ''), topic))), ' ', '_'), '-', '_')"

    def slot_collisions(self) -> list:
        """Diagnostic: semantic slots (client, canonical topic, canonical subject) that hold MORE than one active version.  Empty on a healthy store."""
        return [dict(r) for r in self.query(f"SELECT client_id, {self.CANON_TOPIC} AS topic, {self.CANON_SUBJECT} AS subject, COUNT(*) AS active_versions FROM memory_records "
                                            f"WHERE memory_type='semantic' AND status='active' GROUP BY 1, 2, 3 HAVING COUNT(*) > 1")]

    def _migrate_120(self) -> None:
        for table, cols in self.NEW_COLUMNS.items():
            have = {r["name"] for r in self.query(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in have:
                    self.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        for s in self._script("120_cmf_memory_integrity.sql"):
            self.execute(s)
        # back-fill LEGACY rows only (operation_id = ''): provenance is described, never invented - nothing becomes 'verified' here
        self.execute("UPDATE memory_records SET source_class = CASE origin WHEN 'conversation_llm' THEN 'llm_extraction' WHEN 'conversation_rule' THEN 'observed_transcript' "
                     "WHEN 'consolidation' THEN 'derived' WHEN 'curated_file' THEN 'curated' WHEN 'tool_summary' THEN 'tool_summary' ELSE 'system' END, "
                     "created_by = COALESCE((SELECT e.actor FROM memory_lifecycle_events e WHERE e.memory_id = memory_records.memory_id ORDER BY e.created_at, e.event_id LIMIT 1), 'legacy'), "
                     "observed_at = (SELECT d.event_ts FROM episodic_details d WHERE d.memory_id = memory_records.memory_id), "
                     "temporal_status = CASE status WHEN 'superseded' THEN 'superseded' WHEN 'disputed' THEN 'disputed' ELSE 'unknown' END, "
                     "verification_basis = 'legacy row: predates the P1.4 integrity boundary; its evidence was checked at original ingestion but not re-verified' "
                     "WHERE operation_id = ''")

    # ------------------------------------------------------------------ trigger integrity (P1.4.3, audit finding F3)
    # THREAT BOUNDARY: whoever owns the database FILE can rewrite SQLite itself, and nothing inside SQLite can stop that.  What is guaranteed here is DETECTION and FAIL-CLOSED behaviour at
    # startup and on every durable write: the store is "ready" only if every integrity trigger has EXACTLY the canonical definition below (table, event, WHEN clause, body); a missing or
    # altered one is replaced in one transaction by init(), verified afterwards, and until then durable writes are refused.
    @staticmethod
    def _norm_sql(sql: Optional[str]) -> str:
        return " ".join((sql or "").replace("IF NOT EXISTS ", "").rstrip(" ;\n\t").split())

    def _trigger_definitions(self) -> dict:
        """name -> the canonical CREATE TRIGGER text.  The ONE source: installation, readiness and repair all use it."""
        msg = "memory write outside the integrity boundary"
        defs: dict[str, str] = {}

        def add(name: str, sql: str) -> None:
            defs[name] = sql
        for t in self.GUARDED:
            for op in ("INSERT", "DELETE"):
                add(f"guard_{t}_{op.lower()}", f"CREATE TRIGGER guard_{t}_{op.lower()} BEFORE {op} ON {t} WHEN cmf_write_allowed() = 0 BEGIN SELECT RAISE(ABORT, '{msg}'); END")
        for t in self.UPDATE_GUARDED:
            add(f"guard_{t}_update", f"CREATE TRIGGER guard_{t}_update BEFORE UPDATE ON {t} WHEN cmf_write_allowed() = 0 BEGIN SELECT RAISE(ABORT, '{msg}'); END")
        add("guard_memory_records_update", f"CREATE TRIGGER guard_memory_records_update BEFORE UPDATE OF {', '.join(self.MR_GUARDED_COLUMNS)} ON memory_records "
                                           f"WHEN cmf_write_allowed() = 0 BEGIN SELECT RAISE(ABORT, '{msg}'); END")
        add("immutable_memory_content", f"CREATE TRIGGER immutable_memory_content BEFORE UPDATE OF {', '.join(self.IMMUTABLE_COLUMNS)} ON memory_records "
                                        "BEGIN SELECT RAISE(ABORT, 'memory content is immutable: correct it by superseding, which preserves the original'); END")
        for op in ("UPDATE", "DELETE"):
            lo = op.lower()
            add(f"write_log_no_{lo}", f"CREATE TRIGGER write_log_no_{lo} BEFORE {op} ON memory_write_log BEGIN SELECT RAISE(ABORT, 'memory_write_log is append-only'); END")
            add(f"resolution_no_{lo}", f"CREATE TRIGGER resolution_no_{lo} BEFORE {op} ON memory_conflict_resolutions BEGIN SELECT RAISE(ABORT, 'conflict resolutions are append-only'); END")
            add(f"conflict_no_{lo}", f"CREATE TRIGGER conflict_no_{lo} BEFORE {op} ON memory_conflicts "
                                     "BEGIN SELECT RAISE(ABORT, 'memory_conflicts is append-only: it is resolved by a resolution row, never edited or deleted'); END")
            add(f"lifecycle_no_{lo}", f"CREATE TRIGGER lifecycle_no_{lo} BEFORE {op} ON memory_lifecycle_events BEGIN SELECT RAISE(ABORT, 'memory_lifecycle_events is append-only'); END")
        return defs

    def _expected_triggers(self) -> set:
        return set(self._trigger_definitions())

    def trigger_mismatches(self) -> dict:
        """{trigger name: 'missing' | 'modified'} for every integrity trigger whose installed definition is not EXACTLY the canonical one (whitespace-insensitive)."""
        have = {r["name"]: r["sql"] for r in self.query("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
        bad: dict[str, str] = {}
        for name, sql in self._trigger_definitions().items():
            if name not in have:
                bad[name] = "missing"
            elif self._norm_sql(have[name]) != self._norm_sql(sql):
                bad[name] = "modified"
        return bad

    def _install_guards(self) -> None:
        """Make every integrity trigger EXACTLY the canonical definition.  CREATE TRIGGER IF NOT EXISTS would silently keep a tampered same-name trigger, so a mismatching one is dropped
        and recreated.  Runs inside init()'s single transaction: any failure rolls ALL of it back (no half-repaired store); the result is verified before the transaction may commit."""
        bad = self.trigger_mismatches()
        for name, sql in self._trigger_definitions().items():
            if name in bad:
                if bad[name] == "modified":
                    self.execute(f"DROP TRIGGER {name}")
                self.execute(sql)
        left = self.trigger_mismatches()
        if left:
            raise MemoryIntegrityUnavailable(f"integrity trigger(s) could not be restored to the canonical definition: {sorted(left)}")
        if "modified" in bad.values():                                  # a same-name trigger with another body is tampering; a missing one on a fresh / older schema is just installation
            log.warning("memory integrity triggers repaired: %s", dict(sorted(bad.items())))
        elif bad:
            log.info("memory integrity triggers installed: %d", len(bad))
        self.repaired_triggers = dict(bad)

    def integrity_ready(self) -> bool:
        """The boundary needs the migrated schema AND every guard trigger with its exact canonical definition.  Checked on EVERY durable write (a few cheap catalogue reads, never
        cached): a store opened before another process migrated the database recovers by itself, and a database whose triggers were dropped OR REPLACED refuses writes until init()
        restores them.  Fails closed."""
        try:
            if int(self.query("PRAGMA user_version")[0]["user_version"]) < int(self.MIGRATION):
                return False
            return not self.trigger_mismatches()
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------ evidence
    def verify_evidence(self, client_id: str, ev: dict) -> None:
        if ev.get("source_type", "conversation") != "conversation":
            return
        if client_id == GLOBAL_CLIENT:
            raise EvidenceError("GLOBAL memories cannot cite client conversation evidence")
        row = self.conv.turn(ev["interaction_id"], int(ev["turn_index"]))
        if not row:
            raise EvidenceError(f"no such turn {ev['interaction_id']}.{ev['turn_index']}")
        if row["client_id"] != client_id:
            raise EvidenceError(f"turn {ev['interaction_id']}.{ev['turn_index']} belongs to {row['client_id']}, not {client_id}")
        if ev.get("speaker") and ev["speaker"] != row["speaker"]:
            raise EvidenceError(f"speaker mismatch at {ev['interaction_id']}.{ev['turn_index']}")
        if not self.conv.verify_excerpt(client_id, ev["interaction_id"], int(ev["turn_index"]), ev["excerpt"]):
            raise EvidenceError(f"excerpt not found in {ev['interaction_id']}.{ev['turn_index']}")

    # ------------------------------------------------------------------ writes: EVERY mutator below delegates to the integrity boundary (agents/memory/integrity.py)
    def _require_boundary(self) -> None:
        if not self.integrity_ready():
            raise MemoryIntegrityUnavailable("the memory database has not been migrated to the P1.4 integrity schema (run MemoryStore.init()); durable writes are refused")

    def add_evidence(self, memory_id: str, client_id: str, ev: dict, *, principal=None) -> None:
        """Add a supporting evidence row to an EXISTING memory (re-verified against the CSV inside the caller's client scope).  Needs an explicit Principal."""
        self._require_boundary()
        self.boundary.support(memory_id, client_id=client_id, evidence=dict(ev), principal=principal)

    def reverify_legacy(self, *, principal, client_id: Optional[str] = None) -> dict:
        """Re-check rows that predate the integrity boundary and record the ones that pass (append-only; see migration 124).  Needs an explicit operator Principal."""
        self._require_boundary()
        return self.boundary.reverify_legacy(principal=principal, client_id=client_id)

    def insert_memory(self, rec: dict, details: dict, evidence: Sequence[dict] = (), actor: str = "", reason: str = "created", *, principal=None,
                      idempotency_key: Optional[str] = None) -> tuple[str, bool]:
        """Persist one memory through the integrity boundary (authorization -> provenance -> integrity -> temporal conflicts -> idempotency -> ONE transaction).
        Returns (memory_id, created).  A duplicate (same client, type, content_hash) or a replayed idempotency key returns the existing id with created=False.
        Raises MemoryWriteRejected (a ValueError) with a stable `.code` when the write is refused; nothing is persisted in that case."""
        self._require_boundary()
        r = self.boundary.create(rec, details, evidence, principal=principal, reason=reason, idempotency_key=idempotency_key)      # `actor` is ignored: identity = the Principal
        return r.memory_id, (r.outcome == "new" and not r.replayed)

    # raw persistence primitives - callable only inside the boundary's write gate (the database triggers enforce it)
    def _p_insert(self, r: dict, details: dict) -> None:
        cols = ["memory_id", "client_id", "memory_type", "topic", "subject", "title", "content", "value_text", "corrects_value", "certainty", "source_role",
                "status", "confidence", "event_date", "valid_from", "valid_to", "version_no", "supersedes_id", "origin", "model", "content_hash",
                "created_at", "updated_at", "verification", "verification_basis", "observed_at", "temporal_status", "source_class", "created_by", "request_id", "operation_id", "row_version"]
        self.execute(f"INSERT INTO {{S}}memory_records ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])
        table = {"episodic": "episodic_details", "semantic": "semantic_details", "procedural": "procedural_details", "working": "working_context"}[r["memory_type"]]
        d = dict(details, memory_id=r["memory_id"])
        self.execute(f"INSERT INTO {{S}}{table} ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))

    def _p_evidence(self, memory_id: str, client_id: str, ev: dict, resolution: str) -> None:
        ev = dict(ev)
        ev.setdefault("source_type", "conversation")
        eid = h(memory_id, ev["source_type"], ev.get("interaction_id"), ev.get("turn_index"), ev.get("role", "primary"), ev.get("ref"))
        self.execute("INSERT INTO {S}memory_evidence (evidence_id, memory_id, source_type, interaction_id, turn_index, speaker, excerpt, role, ref, resolution, verified_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                     (eid, memory_id, ev["source_type"], ev.get("interaction_id"), ev.get("turn_index"), ev.get("speaker"), ev.get("excerpt"),
                      ev.get("role", "primary"), ev.get("ref"), resolution, self.now() if resolution == "resolved" else None))

    def mono(self) -> int:
        """Strictly increasing id source: the clock tick on some platforms (Windows) is coarser than two consecutive events, and ties used to be ordered by a random suffix."""
        with self._lock:
            self._ev_last = max(time.time_ns(), self._ev_last + 1)
            return self._ev_last

    def _p_event(self, memory_id: str, frm: Optional[str], to: str, reason: str, actor: str, related: Optional[str] = None) -> None:
        eid = f"EV-{self.mono():020d}-{uuid.uuid4().hex[:6]}"
        self.execute("INSERT INTO {S}memory_lifecycle_events (event_id, memory_id, from_status, to_status, reason, actor, related_memory_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (eid, memory_id, frm, to, reason, actor, related, self.now()))

    _event = _p_event

    def _p_status(self, memory_id: str, new_status: str, valid_to: Optional[str]) -> None:
        self.execute("UPDATE {S}memory_records SET status=?, valid_to=COALESCE(?, valid_to), updated_at=?, row_version=row_version+1 WHERE memory_id=?",
                     (new_status, valid_to, self.now(), memory_id))

    def _p_link(self, source_id: str, target_id: str, rel_type: str, note: str = "") -> bool:
        return self.execute("INSERT INTO {S}memory_relationships (rel_id, source_id, target_id, rel_type, note, created_at) VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                            ("REL-" + h(source_id, target_id, rel_type), source_id, target_id, rel_type, note, self.now())) > 0

    def _p_support_count(self, memory_id: str, count: int) -> None:
        self.execute("UPDATE {S}semantic_details SET supporting_episodes=? WHERE memory_id=?", (count, memory_id))
        self.execute("UPDATE {S}memory_records SET updated_at=? WHERE memory_id=?", (self.now(), memory_id))

    def _p_approval(self, memory_id: str, approval: str, approver: str) -> None:
        self.execute("UPDATE {S}procedural_details SET approval_status=?, approver=? WHERE memory_id=?", (approval, approver, memory_id))

    def _p_conflict(self, c: dict) -> bool:
        cols = list(c)
        return self.execute(f"INSERT INTO {{S}}memory_conflicts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT DO NOTHING", [c[k] for k in cols]) > 0

    def conflicts(self, client_id: str, status: Optional[str] = "open", memory_id: Optional[str] = None) -> list[dict]:
        """Durable conflicts of ONE client (survive restarts; a conflict never changes either memory's status).  `status` is DERIVED: a conflict is 'resolved' exactly when an
        append-only resolution row exists for it (the conflict row itself is never edited); the resolution (who, when, why, decision) is returned with it."""
        sql = ("SELECT c.conflict_id, c.client_id, c.memory_id, c.other_memory_id, c.kind, c.severity, c.detail, c.operation_id, c.detected_at, "
               "CASE WHEN r.resolution_id IS NULL THEN 'open' ELSE 'resolved' END AS status, r.resolution_id, r.decision, r.resolved_by, r.resolved_at, r.reason AS resolution_reason, "
               "r.new_memory_id FROM {S}memory_conflicts c LEFT JOIN {S}memory_conflict_resolutions r ON r.conflict_id=c.conflict_id WHERE c.client_id=?")
        ps: list = [client_id]
        if status:
            sql += " AND r.resolution_id IS NULL" if status == "open" else " AND r.resolution_id IS NOT NULL"
        if memory_id:
            sql, ps = sql + " AND (c.memory_id=? OR c.other_memory_id=?)", ps + [memory_id, memory_id]
        return self.query(sql + " ORDER BY c.detected_at, c.conflict_id", ps)

    def _p_resolution(self, r: dict) -> None:
        cols = list(r)
        self.execute(f"INSERT INTO {{S}}memory_conflict_resolutions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])

    def _p_reverification(self, r: dict) -> None:
        cols = list(r)
        self.execute(f"INSERT INTO {{S}}memory_reverifications ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT DO NOTHING", [r[c] for c in cols])

    def _p_log(self, r: dict) -> None:
        cols = list(r)
        self.execute(f"INSERT INTO {{S}}memory_write_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])

    # public facade (signatures unchanged; optional keyword-only `principal` / `expected_version` added)
    def set_status(self, memory_id: str, new_status: str, reason: str, actor: str, related: Optional[str] = None, valid_to: Optional[str] = None, *, principal=None,
                   expected_version: Optional[int] = None) -> bool:
        """Change status through the boundary (ownership re-checked inside the transaction, lifecycle event appended, row_version bumped).  False = already in that status."""
        self._require_boundary()
        return self.boundary.transition(memory_id, new_status, reason, principal=principal, related=related, valid_to=valid_to, expected_version=expected_version).changed

    def set_approval(self, memory_id: str, approval: str, approver: str, *, principal=None) -> None:
        self._require_boundary()
        self.boundary.support(memory_id, approval=(approval, approver), principal=principal)

    def set_semantic_support(self, memory_id: str, count: int, *, principal=None) -> None:
        self._require_boundary()
        self.boundary.support(memory_id, count=count, principal=principal)

    def add_relationship(self, source_id: str, target_id: str, rel_type: str, note: str = "", *, principal=None) -> bool:
        """Insert once (UNIQUE source,target,type).  Both ends must be in the caller's scope and belong to the same client: relationships never cross clients.  An end that
        is foreign or does not exist is the SAME error (memory_not_found)."""
        self._require_boundary()
        return self.boundary.link(source_id, target_id, rel_type, note, principal=principal)

    # ingestion log + candidate audit
    def ingestion_status(self, interaction_id: str) -> Optional[str]:
        r = self.query("SELECT status FROM {S}ingestion_log WHERE interaction_id=?", (interaction_id,))
        return r[0]["status"] if r else None

    def start_ingestion(self, interaction_id: str, client_id: str, model: str, transcript_hash: str) -> None:
        with self.transaction():
            if self.ingestion_status(interaction_id) is None:
                self.execute("INSERT INTO {S}ingestion_log (interaction_id, client_id, status, model, transcript_hash, started_at) VALUES (?,?,?,?,?,?)",
                             (interaction_id, client_id, "running", model, transcript_hash, self.now()))
            else:
                self.execute("UPDATE {S}ingestion_log SET status='running', model=?, started_at=?, finished_at=NULL WHERE interaction_id=?", (model, self.now(), interaction_id))

    def finish_ingestion(self, interaction_id: str, status: str, turns: int, total: int, accepted: int, rejected: int, note: str = "") -> None:
        self.execute("UPDATE {S}ingestion_log SET status=?, turns_selected=?, candidates_total=?, accepted=?, rejected=?, note=?, finished_at=? WHERE interaction_id=?",
                     (status, turns, total, accepted, rejected, note[:500], self.now(), interaction_id))

    def add_candidate_audit(self, a: dict) -> None:
        cid = h(a["interaction_id"], a.get("turn_index"), a["origin"], a.get("topic"), a.get("subject"), a.get("excerpt"), a["decision"])
        self.execute("INSERT INTO {S}memory_candidates (candidate_id, interaction_id, client_id, turn_index, origin, topic, subject, statement, value_text, excerpt, "
                     "certainty_in, certainty_out, decision, reason, memory_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                     ("CAND-" + cid, a["interaction_id"], a["client_id"], a.get("turn_index"), a["origin"], a.get("topic"), a.get("subject"), a.get("statement"),
                      a.get("value_text"), a.get("excerpt"), a.get("certainty_in"), a.get("certainty_out"), a["decision"], a["reason"][:500], a.get("memory_id"), self.now()))

    def expire_working(self, principal=None) -> list[str]:
        """TTL: ACTIVE working memories whose expires_at has passed (by THIS store's clock - there is no caller-supplied time) become expired, event appended; rows and evidence are
        never deleted.  Needs an explicit `ttl_expiry` service Principal: client-scoped (only those clients' rows) or any_client (the background job).  Through the boundary like every
        other mutation, so the Principal and the per-row target rules (working, active, past expiry, only -> expired) are enforced there.  An un-migrated database defers (returns [])."""
        ctx = self.boundary._ctx("transition", principal, "")
        p = self.boundary._principal(principal, "transition", ctx)
        if p.kind != "service" or p.actor_id != "ttl_expiry":
            self.boundary._deny(ctx, "operation_not_permitted", "only the TTL service may expire working memory in bulk")
        if not self.integrity_ready():       # a read path (retrieval) must not fail on an un-migrated database: expiry is simply deferred until init() has run
            return []
        now_iso = self.now()
        with self.transaction():
            sql = "SELECT m.memory_id FROM {S}memory_records m JOIN {S}working_context w ON w.memory_id=m.memory_id WHERE m.memory_type='working' AND m.status='active' AND w.expires_at <= ?"
            ps: list = [now_iso]
            if not p.any_client:
                if not p.scopes:
                    return []
                sql, ps = sql + f" AND m.client_id IN ({','.join('?' * len(p.scopes))})", ps + sorted(p.scopes)
            rows = self.query(sql, ps)
            for r in rows:
                self.set_status(r["memory_id"], "expired", "working memory TTL elapsed", "ttl_expiry", principal=p)
            return [r["memory_id"] for r in rows]

    # ------------------------------------------------------------------ reads
    # a legacy row that was re-verified by the integrity boundary (memory_reverifications) reads as 'verified'; the stored column itself is immutable and is never edited
    EFFECTIVE_VERIFICATION = "COALESCE(rv.verification, m.verification)"
    _SELECT = ("SELECT m.*, COALESCE(rv.verification, m.verification) AS verification, COALESCE(rv.verification_basis, m.verification_basis) AS verification_basis, "
               "e.interaction_id, e.turn_index, e.event_ts, e.speaker_role, e.event_type, s.slot_key, s.basis, s.supporting_episodes, "
               "p.procedure_name, p.steps, p.topics AS proc_topics, p.keywords AS proc_keywords, p.approval_status, p.approver, p.source_ref, "
               "w.session_id, w.plan_id, w.derived_from_id, w.expires_at FROM {S}memory_records m "
               "LEFT JOIN {S}memory_reverifications rv ON rv.memory_id=m.memory_id "
               "LEFT JOIN {S}episodic_details e ON e.memory_id=m.memory_id LEFT JOIN {S}semantic_details s ON s.memory_id=m.memory_id "
               "LEFT JOIN {S}procedural_details p ON p.memory_id=m.memory_id LEFT JOIN {S}working_context w ON w.memory_id=m.memory_id")

    def records(self, client_id: str, memory_type: Optional[str] = None, statuses: Optional[Sequence[str]] = None,
                topics: Optional[Sequence[str]] = None, include_global: bool = False, session_id: Optional[str] = None) -> list[dict]:
        """Rows for ONE client (plus GLOBAL only when include_global).  Every query is client-scoped: another client's rows can never be returned."""
        ids = [client_id] + ([GLOBAL_CLIENT] if include_global else [])
        sql, ps = self._SELECT + f" WHERE m.client_id IN ({','.join('?' * len(ids))})", list(ids)
        for col, vals in (("m.memory_type", [memory_type] if memory_type else None), ("m.status", statuses), ("m.topic", topics)):
            if vals:
                sql += f" AND {col} IN ({','.join('?' * len(vals))})"
                ps += list(vals)
        if session_id is not None:
            sql, ps = sql + " AND w.session_id = ?", ps + [session_id]
        return self.query(sql + " ORDER BY m.memory_id", ps)

    def get(self, memory_id: str) -> Optional[dict]:
        r = self.query(self._SELECT + " WHERE m.memory_id=?", (memory_id,))
        return r[0] if r else None

    def evidence_map(self, ids: Sequence[str]) -> dict[str, list[EvidenceRef]]:
        out: dict[str, list[EvidenceRef]] = {i: [] for i in ids}
        if not ids:
            return out
        rows = self.query(f"SELECT * FROM {{S}}memory_evidence WHERE source_type='conversation' AND memory_id IN ({','.join('?' * len(ids))}) "
                          "ORDER BY interaction_id, turn_index", list(ids))
        for r in rows:
            out[r["memory_id"]].append(EvidenceRef(interaction_id=r["interaction_id"], turn_index=r["turn_index"], speaker=r["speaker"], excerpt=r["excerpt"], role=r["role"]))
        return out

    def relationships_among(self, ids: Sequence[str]) -> list[Relationship]:
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        rows = self.query(f"SELECT * FROM {{S}}memory_relationships WHERE source_id IN ({ph}) AND target_id IN ({ph}) ORDER BY rel_type, source_id, target_id", list(ids) + list(ids))
        return [Relationship(source_id=r["source_id"], target_id=r["target_id"], rel_type=r["rel_type"], note=r["note"]) for r in rows]

    def lifecycle_events(self, memory_id: Optional[str] = None) -> list[dict]:
        if memory_id:
            return self.query("SELECT * FROM {S}memory_lifecycle_events WHERE memory_id=? ORDER BY created_at, event_id", (memory_id,))
        return self.query("SELECT * FROM {S}memory_lifecycle_events ORDER BY created_at, event_id")

    def count(self, table: str, where: str = "1=1", params: Sequence[Any] = ()) -> int:
        return int(self.query(f"SELECT COUNT(*) AS n FROM {{S}}{table} WHERE {where}", params)[0]["n"])


def to_item(row: dict, evidence: Optional[list[EvidenceRef]] = None) -> MemoryItem:
    """Row -> the shared MemoryItem contract."""
    steps = []
    if row.get("steps"):
        steps = json.loads(row["steps"])
    return MemoryItem(
        memory_id=row["memory_id"], client_id=row["client_id"], memory_type=row["memory_type"], topic=row["topic"], subject=row["subject"] or "", title=row["title"] or "",
        content=row["content"], value_text=row["value_text"], corrects_value=row["corrects_value"], certainty=row["certainty"], source_role=row["source_role"],
        status=row["status"], confidence=float(row["confidence"]), event_date=row["event_date"], valid_from=row["valid_from"], valid_to=row["valid_to"],
        version_no=row["version_no"], supersedes_id=row["supersedes_id"], origin=row["origin"], supporting_episodes=int(row.get("supporting_episodes") or 0),
        evidence=evidence or [], expires_at=row.get("expires_at"), session_id=row.get("session_id"), approval_status=row.get("approval_status"), steps=steps,
        procedure_name=row.get("procedure_name"), verification=row.get("verification") or "unverified", verification_basis=row.get("verification_basis") or "",
        source_class=row.get("source_class") or "", temporal_status=row.get("temporal_status") or "")
