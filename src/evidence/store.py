"""EvidenceStore: the ONLY module of the evidence layer that imports a database driver.

CMF_EVIDENCE_DATABASE_URL selects the file (default sqlite:///build/cmf_evidence.db).  Writers (ingestion) use the write handle; every retrieval
path uses a separate READ-ONLY handle (`reader()`).  It is a separate database: the memory, operational and approval databases are never opened here.
Writes are an operator-side batch path; there is no agent or API route to them, and nothing here creates, updates or reads memories."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from . import PARSER_VERSION
from .model import ParsedContainer, ParsedCrm

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = ROOT / "sql" / "200_cmf_evidence_schema.sql"
SCHEMA_VERSION = "200"
DEFAULT_URL = "sqlite:///build/cmf_evidence.db"
ENV = "CMF_EVIDENCE_DATABASE_URL"


def database_url() -> str:
    return os.environ.get(ENV) or DEFAULT_URL


def _path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError(f"unsupported evidence database url: {url!r} (SQLite only in this phase)")
    p = Path(url[len("sqlite:///"):])
    return p if p.is_absolute() else ROOT / p


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


class EvidenceStore:
    def __init__(self, url: Optional[str] = None, clock=utcnow) -> None:
        self.url, self.clock = url or database_url(), clock
        self.path = _path(self.url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.has_fts = False
        self._init_schema()

    # ---- schema --------------------------------------------------------------------------------------------------------------------------
    def _init_schema(self) -> None:
        have = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        if not have:
            self._conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            self._conn.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (SCHEMA_VERSION, "structured evidence layer", self.clock()))
        try:
            self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(turn_id UNINDEXED, text)")
            self.has_fts = True
        except sqlite3.OperationalError:
            self.has_fts = False

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def reader(self) -> sqlite3.Connection:
        """A fresh read-only connection.  Retrieval code uses only this."""
        c = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only = ON")
        return c

    # ---- runs ----------------------------------------------------------------------------------------------------------------------------
    def begin_run(self) -> int:
        with self._tx() as c:
            return c.execute("INSERT INTO ingest_runs (started_at, parser_version, status) VALUES (?,?,'running')", (self.clock(), PARSER_VERSION)).lastrowid

    def finish_run(self, run_id: int, counts: dict, note: str = "") -> None:
        with self._tx() as c:
            status = "completed_with_errors" if counts.get("failed") else "completed"
            c.execute("UPDATE ingest_runs SET finished_at=?, status=?, ingested=?, unchanged=?, reprocessed=?, failed=?, note=? WHERE run_id=?",
                      (self.clock(), status, counts.get("ingested", 0), counts.get("unchanged", 0), counts.get("reprocessed", 0), counts.get("failed", 0), note, run_id))

    # ---- parties -------------------------------------------------------------------------------------------------------------------------
    def upsert_parties(self, registry: dict) -> None:
        with self._tx() as c:
            for k, r in registry.items():
                c.execute("INSERT OR REPLACE INTO parties VALUES (?,?,?,?,?,?,?,?,?)",
                          (k, r["display_name"], r["role"], r.get("person_category"), r.get("id_status"), r.get("name_status"), r.get("role_status"),
                           r.get("attribution_confidence"), r.get("attribution_evidence")))

    # ---- containers ----------------------------------------------------------------------------------------------------------------------
    def ingest_container(self, k: ParsedContainer, parser_version: str = PARSER_VERSION) -> str:
        """Insert, skip or reprocess one container atomically.  Returns 'ingested' | 'unchanged' | 'reprocessed'."""
        h = k.content_hash(parser_version)
        with self._tx() as c:
            old = c.execute("SELECT content_hash, parser_version, revision, status FROM source_artifacts WHERE artifact_id=?", (k.container_id,)).fetchone()
            if old and old["content_hash"] == h and old["status"] == "parsed":
                return "unchanged"
            rev = 1
            if old:
                rev = old["revision"] + 1
                self._delete_children(c, k.container_id)
                c.execute("DELETE FROM containers WHERE container_id=?", (k.container_id,))
                c.execute("DELETE FROM source_artifacts WHERE artifact_id=?", (k.container_id,))
            self._insert(c, k, h, rev, parser_version)
            # CRM links that were stored before this container existed (or while it was failed) resolve now
            c.execute("UPDATE derived_sources SET resolved_container_id=?, resolution='container' WHERE source_ref=?", (k.container_id, k.container_id))
            return "reprocessed" if old else "ingested"

    def _delete_children(self, c, cid: str) -> None:
        if self.has_fts:
            c.execute("DELETE FROM turns_fts WHERE turn_id IN (SELECT turn_id FROM turns WHERE container_id=?)", (cid,))
        for sql in ("DELETE FROM turn_relations WHERE container_id=?",
                    "DELETE FROM annotations WHERE container_id=?",
                    "DELETE FROM segment_turns WHERE segment_id IN (SELECT segment_id FROM segments WHERE container_id=?)",
                    "DELETE FROM segments WHERE container_id=?",
                    "DELETE FROM evidence_spans WHERE container_id=?",
                    "DELETE FROM email_message_details WHERE turn_id IN (SELECT turn_id FROM turns WHERE container_id=?)",
                    "DELETE FROM chat_message_details WHERE turn_id IN (SELECT turn_id FROM turns WHERE container_id=?)",
                    "DELETE FROM turns WHERE container_id=?",
                    "DELETE FROM email_thread_details WHERE container_id=?"):
            c.execute(sql, (cid,))

    def _insert(self, c, k: ParsedContainer, h: str, rev: int, pv: str) -> None:
        parties = {r["party_id"]: r["attribution_confidence"] for r in c.execute("SELECT party_id, attribution_confidence FROM parties")}
        c.execute("INSERT INTO source_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (k.container_id, k.layer, k.channel, k.client_id, k.source_file, k.source_ref, k.started_at, self.clock(), h, pv, rev, "parsed", None,
                   json.dumps(k.provenance, sort_keys=True)))
        c.execute("INSERT INTO containers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (k.container_id, k.channel, k.kind, k.client_id, k.advisor_id, k.subject, k.started_at, k.ended_at, len(k.turns), k.participant_ids,
                   k.source_platform, k.classification_basis, json.dumps(k.metadata, sort_keys=True, ensure_ascii=False)))
        if k.email_thread:
            c.execute("INSERT INTO email_thread_details VALUES (?,?,?)", (k.container_id, k.email_thread["includes_third_party"], k.email_thread["references_source_ids"]))
        for t in k.turns:
            norm = " ".join((t.email["authored_text"] if t.email else t.original_text).split())
            c.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (t.turn_id, k.container_id, k.client_id, k.channel, t.seq, t.ts, t.ts_basis, t.speaker_id, t.speaker_name, t.speaker_role,
                       t.attribution_status, parties.get(t.speaker_id), t.original_text, norm, t.source_ref))
            if self.has_fts:
                c.execute("INSERT INTO turns_fts (turn_id, text) VALUES (?,?)", (t.turn_id, t.original_text))
            c.execute("INSERT INTO evidence_spans VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"EV-{t.turn_id}", t.turn_id, k.container_id, k.client_id, "full_turn", t.original_text, 0, len(t.original_text), t.source_ref))
            if t.email:
                e = t.email
                c.execute("INSERT INTO email_message_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (t.turn_id, e["message_id"], e["subject"], e["from_id"], e["from_email"], e["to_ids"], e["to_emails"], e["cc_ids"], e["cc_emails"],
                           e["in_reply_to"], e["in_reply_to_turn_id"], e["reference_ids"], e["attachment_refs"], e["authored_text"], e["quoted_text"], e["quote_basis"]))
                if e["quoted_text"] and t.original_text.startswith(e["authored_text"]) and e["quote_basis"] != "angle_bracket_lines":
                    c.execute("INSERT INTO evidence_spans VALUES (?,?,?,?,?,?,?,?,?)",
                              (f"EV-{t.turn_id}-A", t.turn_id, k.container_id, k.client_id, "authored_part", e["authored_text"], 0, len(e["authored_text"]), t.source_ref))
            if t.chat:
                c.execute("INSERT INTO chat_message_details VALUES (?,?,?)", (t.turn_id, t.chat["reply_to_turn_id"], t.chat["recipient_id"]))
        for s in k.segments:
            sid = f"SEG-{k.container_id}-{s['seg_index']:02d}"
            c.execute("INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, k.container_id, k.client_id, s["seg_index"], s["topic"], None, s["start_seq"], s["end_seq"], "keyword_lexicon_v1", s["confidence"], "unreviewed"))
            for pos, tid in enumerate(s["turn_ids"], 1):
                c.execute("INSERT INTO segment_turns VALUES (?,?,?)", (sid, tid, pos))
        for a in k.annotations:
            c.execute("INSERT OR IGNORE INTO annotations (target_type, target_id, container_id, client_id, kind, value, provenance, method, confidence) VALUES (?,?,?,?,?,?,?,?,?)",
                      (a[0], a[1], k.container_id, k.client_id, a[2], a[3], a[4], a[5], a[6]))
        for r in k.relations:
            c.execute("INSERT INTO turn_relations VALUES (?,?,?,?,?,?)", (k.container_id, r[0], r[1], r[2], r[3], r[4]))

    def record_failure(self, artifact_id: str, channel: str, client_id: str, error: str, source_file: str = "") -> None:
        """A container that could not be parsed is recorded (never guessed) and does not block the rest of the run."""
        with self._tx() as c:
            old = c.execute("SELECT status FROM source_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if old and old["status"] == "parsed":
                return                       # an earlier good parse stays; the run log carries the failure count
            c.execute("DELETE FROM source_artifacts WHERE artifact_id=? AND status='failed'", (artifact_id,))
            c.execute("INSERT INTO source_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (artifact_id, "original", channel if channel in ("Meetings", "Calls", "Email", "Chat") else "Meetings", client_id or "UNKNOWN",
                       source_file, artifact_id, None, self.clock(), "", PARSER_VERSION, 1, "failed", error[:500], "{}"))

    # ---- derived CRM -----------------------------------------------------------------------------------------------------------------------
    def ingest_derived(self, d: ParsedCrm, parser_version: str = PARSER_VERSION) -> str:
        h = d.content_hash(parser_version)
        with self._tx() as c:
            old = c.execute("SELECT content_hash, revision FROM source_artifacts WHERE artifact_id=?", (d.crm_record_id,)).fetchone()
            if old and old["content_hash"] == h:
                return "unchanged"
            if old:
                c.execute("DELETE FROM derived_sources WHERE crm_record_id=?", (d.crm_record_id,))
                c.execute("DELETE FROM derived_artifacts WHERE crm_record_id=?", (d.crm_record_id,))
                c.execute("DELETE FROM source_artifacts WHERE artifact_id=?", (d.crm_record_id,))
            c.execute("INSERT INTO source_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (d.crm_record_id, "derived", d.channel, d.client_id, d.crm_file, d.crm_record_id, d.created_at, self.clock(), h, parser_version,
                       (old["revision"] + 1) if old else 1, "parsed", None, json.dumps({"files": [d.crm_file], "keys": ["crm_record_id"]})))
            c.execute("INSERT INTO derived_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (d.crm_record_id, d.crm_file, d.artifact_type, d.channel, d.client_id, d.advisor_id, d.author_id, d.created_at, d.subject, d.text,
                       d.transfer_method, "derived", 0))
            for ref in d.source_refs:
                row = c.execute("SELECT container_id FROM containers WHERE container_id=?", (ref,)).fetchone()
                c.execute("INSERT INTO derived_sources VALUES (?,?,?,?)", (d.crm_record_id, ref, row["container_id"] if row else None, "container" if row else "unresolved"))
            return "reprocessed" if old else "ingested"
