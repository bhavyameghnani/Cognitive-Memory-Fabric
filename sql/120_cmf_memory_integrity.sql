-- CMF memory layer, migration 120 (P1.4): memory integrity boundary.  ADDITIVE and idempotent.  SQLite only (the PostgreSQL path of MemoryStore has not been run and is refused
-- for this migration).  This file holds the new TABLES and INDEXES.  The new COLUMNS on memory_records / memory_evidence, the data back-fill and the guard TRIGGERS are applied
-- by MemoryStore._migrate_120() (ALTER TABLE ... ADD COLUMN has no IF NOT EXISTS, and trigger bodies contain ';').  The whole migration runs in ONE transaction and
-- schema_migrations('120') is written last, so an interrupted run leaves the database exactly as it was.
-- Existing rows are never deleted or rewritten except for the new columns, and no existing row is marked 'verified' by the migration.

CREATE TABLE IF NOT EXISTS memory_write_log (
    log_id          text PRIMARY KEY,
    operation_id    text NOT NULL,
    op              text NOT NULL CHECK (op IN ('create', 'transition', 'correct', 'supersede', 'link', 'support', 'approval')),
    client_id       text NOT NULL,                      -- the client the write TARGETED (claimed for a denied attempt, never the content of another client)
    actor_id        text NOT NULL,                      -- '' when the caller supplied no identity
    actor_kind      text NOT NULL,
    request_id      text NOT NULL DEFAULT '',
    idempotency_key text NOT NULL DEFAULT '',           -- '' = no key reserved
    payload_hash    text NOT NULL,
    outcome         text NOT NULL CHECK (outcome IN ('new', 'duplicate', 'updated', 'corrected', 'superseded', 'rejected')),
    reason_codes    text NOT NULL DEFAULT '[]',         -- JSON array of stable reason codes (and conflict kinds)
    memory_id       text,                               -- NULL for attempts that never resolved a memory in the caller's scope
    created_at      text NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_write_log_key ON memory_write_log (client_id, op, idempotency_key) WHERE idempotency_key <> '';
CREATE INDEX IF NOT EXISTS idx_write_log_memory ON memory_write_log (memory_id);
CREATE INDEX IF NOT EXISTS idx_write_log_actor ON memory_write_log (actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mem_slot ON memory_records (client_id, topic, subject, event_date);
