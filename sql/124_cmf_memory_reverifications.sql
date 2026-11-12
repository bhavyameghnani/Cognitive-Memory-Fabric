-- CMF memory layer, migration 124: append-only re-verification of LEGACY memories.  ADDITIVE and idempotent; SQLite only.
-- memory_records.verification is immutable (a memory is corrected by superseding it), and migration 120 deliberately marks every row that predates the integrity boundary
-- 'unverified'.  This table is how such a row can EARN 'verified' without being edited: an operator runs `orchestration.cli reverify`, the integrity boundary re-checks the row's
-- cited transcript turn(s) with the SAME rules a new write goes through, and records a positive result here.  Reads use the row's own value unless a result exists.
-- Only positive results are stored (a row that does not pass simply stays unverified), so the table can never downgrade a memory.  The guard triggers are installed by
-- MemoryStore._install_guards(): inserts only inside the integrity boundary's write gate, no update, no delete.
CREATE TABLE IF NOT EXISTS memory_reverifications (
    memory_id          text PRIMARY KEY REFERENCES memory_records(memory_id),
    client_id          text NOT NULL,
    verification       text NOT NULL CHECK (verification = 'verified'),
    verification_basis text NOT NULL,
    evidence_checked   integer NOT NULL DEFAULT 0,
    operation_id       text NOT NULL,
    actor_id           text NOT NULL,
    verified_at        text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reverif_client ON memory_reverifications (client_id);
