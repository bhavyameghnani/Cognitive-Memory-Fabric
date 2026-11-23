-- CMF memory layer, migration 130 (M2): staged memory formation.  ADDITIVE and idempotent; SQLite only.  Nothing here alters an existing table or row.
-- A PROPOSAL is what the extractor produces; it is NOT a memory.  Validation, human approval and promotion are separate, audited steps (agents/memory/staging.py).  The triggers that
-- enforce the lifecycle in the database are installed by MemoryStore._install_proposal_guards() (they contain semicolons, which this script splitter cannot carry).

-- one-way latch: once a store is `staged` the ingestion identity can never create a memory directly again (an offline maintenance decision is needed to leave staged mode)
CREATE TABLE IF NOT EXISTS memory_formation_mode (
    singleton   integer PRIMARY KEY CHECK (singleton = 1),
    mode        text NOT NULL CHECK (mode IN ('direct', 'staged')),
    changed_at  text NOT NULL,
    changed_by  text NOT NULL
);
INSERT OR IGNORE INTO memory_formation_mode (singleton, mode, changed_at, changed_by) VALUES (1, 'direct', '1970-01-01T00:00:00', 'migration 130');

CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id        text PRIMARY KEY,
    client_id          text NOT NULL CHECK (client_id GLOB 'CLIENT-[0-9][0-9][0-9]*'),
    memory_type        text NOT NULL CHECK (memory_type IN ('episodic', 'procedural')),          -- semantic memory is written ONLY by consolidation, never proposed
    topic              text NOT NULL,
    subject            text NOT NULL DEFAULT '',
    statement          text NOT NULL CHECK (length(statement) BETWEEN 3 AND 600),
    value_text         text,
    corrects_value     text,
    certainty          text NOT NULL CHECK (certainty IN ('confirmed', 'unconfirmed', 'recommendation', 'exploratory', 'hypothetical', 'correction')),   -- a CEILING set from the evidence
    source_role        text NOT NULL CHECK (source_role IN ('Client', 'Advisor')),
    attribution        text NOT NULL CHECK (attribution IN ('client_direct', 'advisor_reported_client', 'advisor_interpretation', 'system_inference', 'ambiguous')),
    event_type         text NOT NULL,
    origin             text NOT NULL CHECK (origin IN ('conversation_llm', 'conversation_rule')),
    interaction_id     text NOT NULL,
    turn_index         integer NOT NULL,
    excerpt            text NOT NULL CHECK (length(excerpt) > 0),
    excerpt_start      integer,                                                                 -- offsets into the ORIGINAL turn text, NULL (never invented) when unresolved
    excerpt_end        integer,
    match_method       text NOT NULL CHECK (match_method IN ('exact', 'whitespace_normalised', 'unresolved')),
    evidence_json      text NOT NULL CHECK (json_valid(evidence_json)),                        -- every cited row (primary + supporting) with its own offsets
    record_json        text NOT NULL CHECK (json_valid(record_json)),                          -- the exact production row a promotion would write
    details_json       text NOT NULL CHECK (json_valid(details_json)),
    extraction_json    text NOT NULL CHECK (json_valid(extraction_json)),                      -- model, ingest mode, verifier, notes, vote bucket: AUDIT values, never a validity input
    validation_json    text NOT NULL DEFAULT '[]' CHECK (json_valid(validation_json)),
    unresolved_json    text NOT NULL DEFAULT '[]' CHECK (json_valid(unresolved_json)),
    content_hash       text NOT NULL,
    target_memory_id   text NOT NULL,                                                           -- the memory id a promotion will create: the database gate matches on it
    state              text NOT NULL DEFAULT 'extracted' CHECK (state IN ('extracted', 'validated', 'rejected', 'unresolved', 'pending_approval', 'approved', 'promoted', 'superseded', 'expired')),
    promoted_memory_id text,
    created_by         text NOT NULL CHECK (length(trim(created_by)) > 0),
    formed_at          text NOT NULL,
    state_changed_at   text NOT NULL,
    UNIQUE (client_id, content_hash),
    CHECK ((excerpt_start IS NULL) = (excerpt_end IS NULL)),
    CHECK ((match_method = 'unresolved') = (excerpt_start IS NULL)),
    CHECK (excerpt_start IS NULL OR (excerpt_start >= 0 AND excerpt_end > excerpt_start)),
    CHECK (state <> 'promoted' OR promoted_memory_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_proposal_client_state ON memory_proposals (client_id, state);
CREATE INDEX IF NOT EXISTS idx_proposal_source ON memory_proposals (client_id, interaction_id, turn_index);

CREATE TABLE IF NOT EXISTS memory_proposal_events (
    event_id    integer PRIMARY KEY AUTOINCREMENT,
    proposal_id text NOT NULL REFERENCES memory_proposals (proposal_id),
    from_state  text,
    to_state    text NOT NULL CHECK (to_state IN ('extracted', 'validated', 'rejected', 'unresolved', 'pending_approval', 'approved', 'promoted', 'superseded', 'expired')),
    actor_kind  text NOT NULL CHECK (actor_kind IN ('human', 'service')),
    actor_id    text NOT NULL CHECK (length(trim(actor_id)) > 0),
    reason      text NOT NULL CHECK (length(trim(reason)) > 0),
    detail_json text NOT NULL DEFAULT '{}' CHECK (json_valid(detail_json)),
    created_at  text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposal_events ON memory_proposal_events (proposal_id, event_id);

CREATE TABLE IF NOT EXISTS memory_proposal_decisions (
    decision_id   integer PRIMARY KEY AUTOINCREMENT,
    proposal_id   text NOT NULL REFERENCES memory_proposals (proposal_id),
    decision      text NOT NULL CHECK (decision IN ('approve', 'reject', 'request_evidence')),
    reviewer_kind text NOT NULL CHECK (reviewer_kind = 'human'),                               -- a model, a script or a service identity can never be recorded as a reviewer
    reviewer_id   text NOT NULL CHECK (length(trim(reviewer_id)) > 0
        AND lower(trim(reviewer_id)) NOT IN ('', 'admin', 'anonymous', 'assistant', 'consolidation', 'curated_loader', 'dev-anonymous', 'ingestion', 'llm', 'model', 'none', 'null',
                                            'orchestrator', 'promotion', 'root', 'system', 'ttl_expiry', 'unknown')
        AND lower(reviewer_id) NOT LIKE 'service:%' AND lower(reviewer_id) NOT LIKE 'system:%' AND lower(reviewer_id) NOT LIKE 'model:%' AND lower(reviewer_id) NOT LIKE 'llm:%'),
    auth_ref      text NOT NULL CHECK (length(trim(auth_ref)) > 0),                            -- what the caller's verified identity said (the database records it and cannot verify it)
    reason        text NOT NULL CHECK (length(trim(reason)) > 0),
    proposal_hash text NOT NULL,                                                                -- binds the decision to the EXACT content that was reviewed
    created_at    text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposal_decisions ON memory_proposal_decisions (proposal_id, decision_id)
