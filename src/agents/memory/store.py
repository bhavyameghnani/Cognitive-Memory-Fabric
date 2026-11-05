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
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from agents.memory.config import GLOBAL_CLIENT, database_url
from schemas.memory import EvidenceRef, MemoryItem, Relationship
from services.conversations import Conversations, shared

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "sql"
SCHEMA_FILE, DROP_FILE, SCHEMA_VERSION = "100_cmf_memory_schema.sql", "199_drop_cmf_memory.sql", "100"
TABLES = ["memory_candidates", "memory_lifecycle_events", "memory_relationships", "memory_evidence", "working_context", "procedural_details",
          "semantic_details", "episodic_details", "memory_records", "ingestion_log", "schema_migrations"]


class EvidenceError(ValueError):
    """Conversation evidence that does not exist in the CSV (turn missing, other client, other speaker, excerpt absent)."""


def h(*parts: Any, n: int = 16) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:n]


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0, tzinfo=None).isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MemoryStore:
    def __init__(self, url: Optional[str] = None, conv: Optional[Conversations] = None, clock: Callable[[], datetime] = utcnow) -> None:
        self.url, self.conv, self.clock, self._depth = url or database_url(), conv or shared(), clock, 0
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
        elif self.url.startswith(("postgresql://", "postgres://")):
            self.kind, self._s = "postgresql", "cmf_memory."
            import psycopg  # only needed for this backend
            self._conn = psycopg.connect(self.url)
        else:
            raise ValueError("unsupported CMF_MEMORY_DATABASE_URL (use sqlite:///... or postgresql://...)")

    # ------------------------------------------------------------------ plumbing
    def now(self) -> str:
        return iso(self.clock())

    def _sql(self, sql: str) -> str:
        sql = sql.replace("{S}", self._s)
        return sql.replace("?", "%s") if self.kind == "postgresql" else sql

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cur = self._conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur.rowcount

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One transaction per outermost block; nested blocks join it.  Any exception rolls everything back."""
        if self._depth == 0 and self.kind == "sqlite":
            self._conn.execute("BEGIN")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self._conn.execute("ROLLBACK") if self.kind == "sqlite" else self._conn.rollback()
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self._conn.execute("COMMIT") if self.kind == "sqlite" else self._conn.commit()

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
        """Create the schema (idempotent).  reset=True drops and recreates ONLY the memory schema/tables."""
        exists = self.kind == "sqlite" and bool(self.query("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_records'"))
        if self.kind == "postgresql":
            exists = bool(self.query("SELECT 1 AS x FROM information_schema.tables WHERE table_schema='cmf_memory' AND table_name='memory_records'"))
        with self.transaction():
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
            if self.kind == "sqlite":  # append-only guarantee for lifecycle events
                for op in ("UPDATE", "DELETE"):
                    self.execute(f"CREATE TRIGGER IF NOT EXISTS lifecycle_no_{op.lower()} BEFORE {op} ON memory_lifecycle_events "
                                 f"BEGIN SELECT RAISE(ABORT, 'memory_lifecycle_events is append-only'); END")

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

    def add_evidence(self, memory_id: str, client_id: str, ev: dict) -> None:
        ev = dict(ev)
        ev.setdefault("source_type", "conversation")
        self.verify_evidence(client_id, ev)
        eid = h(memory_id, ev["source_type"], ev.get("interaction_id"), ev.get("turn_index"), ev.get("role", "primary"), ev.get("ref"))
        self.execute("INSERT INTO {S}memory_evidence (evidence_id, memory_id, source_type, interaction_id, turn_index, speaker, excerpt, role, ref) "
                     "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                     (eid, memory_id, ev["source_type"], ev.get("interaction_id"), ev.get("turn_index"), ev.get("speaker"), ev.get("excerpt"),
                      ev.get("role", "primary"), ev.get("ref")))

    # ------------------------------------------------------------------ writes
    def insert_memory(self, rec: dict, details: dict, evidence: Sequence[dict] = (), actor: str = "system", reason: str = "created") -> tuple[str, bool]:
        """Persist one memory (one transaction): record + detail row + verified evidence + creation lifecycle event.
        Returns (memory_id, created).  A duplicate (same client, type, content_hash) is not inserted and returns the existing id."""
        with self.transaction():
            dup = self.query("SELECT memory_id FROM {S}memory_records WHERE client_id=? AND memory_type=? AND content_hash=?",
                             (rec["client_id"], rec["memory_type"], rec["content_hash"]))
            if dup:
                return dup[0]["memory_id"], False
            for ev in evidence:
                self.verify_evidence(rec["client_id"], ev)
            ts = self.now()
            r = dict(subject="", title="", value_text=None, corrects_value=None, source_role=None, confidence=0.5, event_date=None, valid_from=None,
                     valid_to=None, version_no=None, supersedes_id=None, model=None, created_at=ts, updated_at=ts)
            r.update(rec)
            cols = ["memory_id", "client_id", "memory_type", "topic", "subject", "title", "content", "value_text", "corrects_value", "certainty", "source_role",
                    "status", "confidence", "event_date", "valid_from", "valid_to", "version_no", "supersedes_id", "origin", "model", "content_hash",
                    "created_at", "updated_at"]
            self.execute(f"INSERT INTO {{S}}memory_records ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])
            table = {"episodic": "episodic_details", "semantic": "semantic_details", "procedural": "procedural_details", "working": "working_context"}[r["memory_type"]]
            d = dict(details, memory_id=r["memory_id"])
            self.execute(f"INSERT INTO {{S}}{table} ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
            for ev in evidence:
                self.add_evidence(r["memory_id"], r["client_id"], ev)
            self._event(r["memory_id"], None, r["status"], reason, actor)
            return r["memory_id"], True

    def _event(self, memory_id: str, frm: Optional[str], to: str, reason: str, actor: str, related: Optional[str] = None) -> None:
        self.execute("INSERT INTO {S}memory_lifecycle_events (event_id, memory_id, from_status, to_status, reason, actor, related_memory_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (f"EV-{time.time_ns():020d}-{uuid.uuid4().hex[:6]}", memory_id, frm, to, reason, actor, related, self.now()))

    def set_status(self, memory_id: str, new_status: str, reason: str, actor: str, related: Optional[str] = None, valid_to: Optional[str] = None) -> bool:
        """Change status and append the lifecycle event in one transaction.  No-op (False) when already in that status."""
        with self.transaction():
            row = self.query("SELECT status FROM {S}memory_records WHERE memory_id=?", (memory_id,))
            if not row:
                raise KeyError(memory_id)
            if row[0]["status"] == new_status:
                return False
            self.execute("UPDATE {S}memory_records SET status=?, valid_to=COALESCE(?, valid_to), updated_at=? WHERE memory_id=?", (new_status, valid_to, self.now(), memory_id))
            self._event(memory_id, row[0]["status"], new_status, reason, actor, related)
            return True

    def set_approval(self, memory_id: str, approval: str, approver: str) -> None:
        self.execute("UPDATE {S}procedural_details SET approval_status=?, approver=? WHERE memory_id=?", (approval, approver, memory_id))

    def set_semantic_support(self, memory_id: str, count: int) -> None:
        self.execute("UPDATE {S}semantic_details SET supporting_episodes=? WHERE memory_id=?", (count, memory_id))
        self.execute("UPDATE {S}memory_records SET updated_at=? WHERE memory_id=?", (self.now(), memory_id))

    def add_relationship(self, source_id: str, target_id: str, rel_type: str, note: str = "") -> bool:
        """Insert once (UNIQUE source,target,type).  Both ends must belong to the same client: relationships never cross clients."""
        rows = self.query("SELECT memory_id, client_id FROM {S}memory_records WHERE memory_id IN (?, ?)", (source_id, target_id))
        if len(rows) != 2 and source_id != target_id:
            raise KeyError(f"unknown memory in relationship {source_id} -> {target_id}")
        if len({r["client_id"] for r in rows if r["client_id"] != GLOBAL_CLIENT}) > 1:
            raise ValueError("cross-client relationship refused")
        return self.execute("INSERT INTO {S}memory_relationships (rel_id, source_id, target_id, rel_type, note, created_at) VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                            ("REL-" + h(source_id, target_id, rel_type), source_id, target_id, rel_type, note, self.now())) > 0

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

    def expire_working(self, now_iso: Optional[str] = None) -> list[str]:
        """TTL: active working memories past expires_at become expired (event appended).  Rows and evidence are never deleted."""
        now_iso = now_iso or self.now()
        with self.transaction():
            rows = self.query("SELECT m.memory_id FROM {S}memory_records m JOIN {S}working_context w ON w.memory_id=m.memory_id "
                              "WHERE m.memory_type='working' AND m.status='active' AND w.expires_at <= ?", (now_iso,))
            for r in rows:
                self.set_status(r["memory_id"], "expired", "working memory TTL elapsed", "system")
            return [r["memory_id"] for r in rows]

    # ------------------------------------------------------------------ reads
    _SELECT = ("SELECT m.*, e.interaction_id, e.turn_index, e.event_ts, e.speaker_role, e.event_type, s.slot_key, s.basis, s.supporting_episodes, "
               "p.procedure_name, p.steps, p.topics AS proc_topics, p.keywords AS proc_keywords, p.approval_status, p.approver, p.source_ref, "
               "w.session_id, w.plan_id, w.derived_from_id, w.expires_at FROM {S}memory_records m "
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
        procedure_name=row.get("procedure_name"))
