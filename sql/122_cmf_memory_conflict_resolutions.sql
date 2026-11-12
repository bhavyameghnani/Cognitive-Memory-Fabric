-- CMF memory layer, migration 122 (P1.4.2): append-only resolution history for memory_conflicts.  ADDITIVE and idempotent; SQLite only.
-- A conflict is OPEN until a resolution row exists for it; the conflict row itself is never edited.  The resolution table is append-only (no UPDATE or DELETE, even inside the
-- write gate).  The partial unique index that keeps one ACTIVE semantic version per (client, topic, subject) slot is created by MemoryStore._ensure_slot_index() because it must be
-- skipped, not failed, on a legacy database that already holds duplicates (the integrity boundary enforces the same rule in either case).
CREATE TABLE IF NOT EXISTS memory_conflict_resolutions (
    resolution_id   text PRIMARY KEY,
    conflict_id     text NOT NULL REFERENCES memory_conflicts(conflict_id),
    client_id       text NOT NULL,
    decision        text NOT NULL CHECK (decision IN ('keep', 'dismiss', 'correct')),
    reason          text NOT NULL,
    resolved_by     text NOT NULL,
    new_memory_id   text REFERENCES memory_records(memory_id),
    operation_id    text NOT NULL DEFAULT '',
    resolved_at     text NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_conflict_resolution ON memory_conflict_resolutions (conflict_id);
CREATE INDEX IF NOT EXISTS idx_conflict_resolution_client ON memory_conflict_resolutions (client_id);
