-- CMF memory layer, migration 121 (P1.4.1): durable, discoverable memory conflicts.  ADDITIVE and idempotent; SQLite only.  A conflict row never changes either memory's status:
-- it records that two memories (or a memory and newer evidence) disagree and that a person has to look.  The guard triggers are installed by MemoryStore._install_guards().
CREATE TABLE IF NOT EXISTS memory_conflicts (
    conflict_id     text PRIMARY KEY,
    client_id       text NOT NULL,
    memory_id       text NOT NULL REFERENCES memory_records(memory_id),
    other_memory_id text NOT NULL REFERENCES memory_records(memory_id),
    kind            text NOT NULL,
    severity        text NOT NULL DEFAULT 'info',
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    detail          text NOT NULL DEFAULT '',
    operation_id    text NOT NULL DEFAULT '',
    detected_at     text NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_conflict ON memory_conflicts (memory_id, other_memory_id, kind);
CREATE INDEX IF NOT EXISTS idx_memory_conflict_client ON memory_conflicts (client_id, status);
