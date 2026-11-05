-- CMF memory layer (Memory Orchestrator).  Schema cmf_memory.  PostgreSQL 13+ syntax, written to also load into SQLite.
-- Only portable DDL is used.  Dates and timestamps are ISO-8601 text (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS), which sorts correctly
-- as text on both engines.  No foreign keys cross schemas: client_id and interaction_id are plain values verified in code against
-- data/conversations.csv.  cmf_ops and cmf_memory can be reset independently (see 199_drop_cmf_memory.sql).
-- NOTE: only the SQLite load path has been run.  The PostgreSQL path has NOT been run on a live server.
-- Append-only guarantee for memory_lifecycle_events: MemoryStore offers no update or delete; on SQLite it also installs triggers.

CREATE SCHEMA IF NOT EXISTS cmf_memory;

CREATE TABLE cmf_memory.schema_migrations (
    version     text PRIMARY KEY,
    description text NOT NULL,
    applied_at  text NOT NULL
);

CREATE TABLE cmf_memory.ingestion_log (
    interaction_id   text PRIMARY KEY,
    client_id        text NOT NULL,
    status           text NOT NULL CHECK (status IN ('running', 'completed', 'degraded', 'failed')),
    model            text,
    transcript_hash  text,
    turns_selected   integer NOT NULL DEFAULT 0,
    candidates_total integer NOT NULL DEFAULT 0,
    accepted         integer NOT NULL DEFAULT 0,
    rejected         integer NOT NULL DEFAULT 0,
    note             text,
    started_at       text NOT NULL,
    finished_at      text
);

CREATE TABLE cmf_memory.memory_records (
    memory_id     text PRIMARY KEY,
    client_id     text NOT NULL,                       -- a CLIENT-nnn id, or GLOBAL for curated procedures
    memory_type   text NOT NULL CHECK (memory_type IN ('episodic', 'semantic', 'procedural', 'working')),
    topic         text NOT NULL,
    subject       text NOT NULL DEFAULT '',
    title         text NOT NULL DEFAULT '',
    content       text NOT NULL,
    value_text    text,
    corrects_value text,
    certainty     text NOT NULL CHECK (certainty IN ('confirmed', 'unconfirmed', 'recommendation', 'exploratory', 'hypothetical', 'correction', 'n/a')),
    source_role   text CHECK (source_role IN ('Client', 'Advisor', 'System')),
    status        text NOT NULL CHECK (status IN ('candidate', 'active', 'superseded', 'disputed', 'expired', 'unconfirmed')),
    confidence    double precision NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
    event_date    text,
    valid_from    text,
    valid_to      text,
    version_no    integer,
    supersedes_id text REFERENCES cmf_memory.memory_records (memory_id),
    origin        text NOT NULL CHECK (origin IN ('conversation_llm', 'conversation_rule', 'consolidation', 'curated_file', 'tool_summary', 'orchestrator')),
    model         text,
    content_hash  text NOT NULL,
    created_at    text NOT NULL,
    updated_at    text NOT NULL,
    UNIQUE (client_id, memory_type, content_hash),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    CHECK (supersedes_id IS NULL OR supersedes_id <> memory_id)
);
CREATE INDEX idx_mem_client_type_status ON cmf_memory.memory_records (client_id, memory_type, status);
CREATE INDEX idx_mem_client_topic_date  ON cmf_memory.memory_records (client_id, topic, event_date);
CREATE INDEX idx_mem_supersedes         ON cmf_memory.memory_records (supersedes_id);

CREATE TABLE cmf_memory.episodic_details (
    memory_id      text PRIMARY KEY REFERENCES cmf_memory.memory_records (memory_id),
    interaction_id text NOT NULL,
    turn_index     integer NOT NULL,
    event_ts       text NOT NULL,                      -- interaction_datetime of the meeting (ordering key)
    speaker_role   text NOT NULL CHECK (speaker_role IN ('Client', 'Advisor')),
    event_type     text NOT NULL,                      -- decision | exploration | correction | recommendation | statement | hypothetical
    selection_note text,
    candidate_id   text
);

CREATE TABLE cmf_memory.semantic_details (
    memory_id           text PRIMARY KEY REFERENCES cmf_memory.memory_records (memory_id),
    slot_key            text NOT NULL,                 -- topic|subject
    basis               text NOT NULL,
    supporting_episodes integer NOT NULL DEFAULT 1 CHECK (supporting_episodes >= 1)
);
CREATE INDEX idx_sem_slot ON cmf_memory.semantic_details (slot_key);

CREATE TABLE cmf_memory.procedural_details (
    memory_id       text PRIMARY KEY REFERENCES cmf_memory.memory_records (memory_id),
    procedure_name  text NOT NULL,
    steps           text NOT NULL,                     -- JSON array of strings
    topics          text NOT NULL DEFAULT '',          -- comma separated
    keywords        text NOT NULL DEFAULT '',          -- comma separated
    approval_status text NOT NULL CHECK (approval_status IN ('proposed', 'approved', 'rejected')),
    approver        text,
    source_ref      text NOT NULL
);

CREATE TABLE cmf_memory.working_context (
    memory_id       text PRIMARY KEY REFERENCES cmf_memory.memory_records (memory_id),
    session_id      text NOT NULL,
    plan_id         text,
    derived_from_id text REFERENCES cmf_memory.memory_records (memory_id),
    expires_at      text NOT NULL
);
CREATE INDEX idx_work_session ON cmf_memory.working_context (session_id, expires_at);

CREATE TABLE cmf_memory.memory_evidence (
    evidence_id    text PRIMARY KEY,
    memory_id      text NOT NULL REFERENCES cmf_memory.memory_records (memory_id),
    source_type    text NOT NULL CHECK (source_type IN ('conversation', 'tool', 'curated')),
    interaction_id text,
    turn_index     integer,
    speaker        text,
    excerpt        text,
    role           text NOT NULL DEFAULT 'primary',
    ref            text,                               -- e.g. tool execution id
    CHECK (source_type <> 'conversation' OR (interaction_id IS NOT NULL AND turn_index IS NOT NULL AND speaker IS NOT NULL AND excerpt IS NOT NULL)),
    UNIQUE (memory_id, source_type, interaction_id, turn_index, role)
);
CREATE INDEX idx_evid_turn   ON cmf_memory.memory_evidence (interaction_id, turn_index);
CREATE INDEX idx_evid_memory ON cmf_memory.memory_evidence (memory_id);

CREATE TABLE cmf_memory.memory_relationships (
    rel_id     text PRIMARY KEY,
    source_id  text NOT NULL REFERENCES cmf_memory.memory_records (memory_id),
    target_id  text NOT NULL REFERENCES cmf_memory.memory_records (memory_id),
    rel_type   text NOT NULL CHECK (rel_type IN ('derived_from', 'supersedes', 'corrects', 'explores', 'supports', 'follows', 'related_to')),
    note       text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    UNIQUE (source_id, target_id, rel_type),
    CHECK (source_id <> target_id)
);
CREATE INDEX idx_rel_source ON cmf_memory.memory_relationships (source_id);
CREATE INDEX idx_rel_target ON cmf_memory.memory_relationships (target_id);

CREATE TABLE cmf_memory.memory_lifecycle_events (
    event_id          text PRIMARY KEY,
    memory_id         text NOT NULL REFERENCES cmf_memory.memory_records (memory_id),
    from_status       text,
    to_status         text NOT NULL CHECK (to_status IN ('candidate', 'active', 'superseded', 'disputed', 'expired', 'unconfirmed')),
    reason            text NOT NULL,
    actor             text NOT NULL,
    related_memory_id text,
    created_at        text NOT NULL
);
CREATE INDEX idx_life_memory ON cmf_memory.memory_lifecycle_events (memory_id);

CREATE TABLE cmf_memory.memory_candidates (
    candidate_id    text PRIMARY KEY,
    interaction_id  text NOT NULL,
    client_id       text NOT NULL,
    turn_index      integer,
    origin          text NOT NULL,
    topic           text,
    subject         text,
    statement       text,
    value_text      text,
    excerpt         text,
    certainty_in    text,
    certainty_out   text,
    decision        text NOT NULL CHECK (decision IN ('accepted', 'downgraded', 'merged', 'rejected')),
    reason          text NOT NULL,
    memory_id       text REFERENCES cmf_memory.memory_records (memory_id),
    created_at      text NOT NULL
);
CREATE INDEX idx_cand_interaction ON cmf_memory.memory_candidates (interaction_id);
