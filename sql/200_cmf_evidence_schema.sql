-- CMF structured evidence layer.  Separate SQLite database (build/cmf_evidence.db); see docs/evidence_layer_design.md.
-- Source-preserving parsed communications.  NO memories live here.  Timestamps are ISO-8601 text.  No foreign key crosses into
-- the memory, ops or approvals databases.  Governance (access) fixtures are SYNTHETIC and are read from datasets/governance, not stored here.

CREATE TABLE schema_migrations (
    version text PRIMARY KEY, description text NOT NULL, applied_at text NOT NULL
);

CREATE TABLE ingest_runs (
    run_id       integer PRIMARY KEY AUTOINCREMENT,
    started_at   text NOT NULL,
    finished_at  text,
    parser_version text NOT NULL,
    status       text NOT NULL CHECK (status IN ('running', 'completed', 'completed_with_errors')),
    ingested integer NOT NULL DEFAULT 0, unchanged integer NOT NULL DEFAULT 0, reprocessed integer NOT NULL DEFAULT 0, failed integer NOT NULL DEFAULT 0,
    note text
);

-- one party per registry entry; attribution status and confidence are copied, never upgraded
CREATE TABLE parties (
    party_id text PRIMARY KEY, display_name text NOT NULL, role text, person_category text,
    id_status text, name_status text, role_status text, attribution_confidence text, attribution_evidence text
);

CREATE TABLE source_artifacts (
    artifact_id    text PRIMARY KEY,                      -- = container id (dataset ID)
    layer          text NOT NULL CHECK (layer IN ('original', 'derived')),
    channel        text NOT NULL CHECK (channel IN ('Meetings', 'Calls', 'Email', 'Chat')),
    client_id      text NOT NULL,
    source_file    text NOT NULL,                         -- repo-relative file the container was read from
    source_ref     text NOT NULL,                         -- native key in that file (thread_id, meeting_id, interaction_id, ...)
    source_ts      text,                                  -- source interaction/creation timestamp
    ingested_at    text NOT NULL,
    content_hash   text NOT NULL,
    parser_version text NOT NULL,
    revision       integer NOT NULL DEFAULT 1,
    status         text NOT NULL CHECK (status IN ('parsed', 'failed')),
    error          text,
    provenance     text NOT NULL                          -- json: files, key columns, classification basis
);
CREATE INDEX idx_art_client ON source_artifacts (client_id, channel, source_ts);

CREATE TABLE containers (
    container_id   text PRIMARY KEY REFERENCES source_artifacts (artifact_id),
    channel        text NOT NULL,
    container_kind text NOT NULL CHECK (container_kind IN ('email_thread', 'meeting', 'call', 'chat_conversation')),
    client_id      text NOT NULL,
    advisor_id     text,
    subject        text,
    started_at     text,
    ended_at       text,
    turn_count     integer NOT NULL,
    participant_ids text NOT NULL DEFAULT '',
    source_platform text,
    classification_basis text,                            -- original interactions only: why Call vs Meeting
    metadata       text NOT NULL DEFAULT '{}'            -- json: channel-specific native metadata, verbatim
);
CREATE INDEX idx_cont_client ON containers (client_id, channel, started_at);

CREATE TABLE turns (
    turn_id      text PRIMARY KEY,
    container_id text NOT NULL REFERENCES containers (container_id),
    client_id    text NOT NULL,
    channel      text NOT NULL,
    seq          integer NOT NULL,
    ts           text,                                    -- NULL when the source carries no per-turn time (calls, original meetings)
    ts_basis     text NOT NULL CHECK (ts_basis IN ('message', 'turn', 'container_start_only')),
    speaker_id   text,
    speaker_name text,
    speaker_role text,
    attribution_status text NOT NULL CHECK (attribution_status IN ('source_stated', 'derived_from_role_label')),
    attribution_confidence text,                          -- copied from parties; NULL if the speaker is not in the registry
    original_text text NOT NULL,
    normalized_text text NOT NULL,
    source_ref   text NOT NULL,
    UNIQUE (container_id, seq)
);
CREATE INDEX idx_turn_client ON turns (client_id, channel, ts);
CREATE INDEX idx_turn_speaker ON turns (client_id, speaker_id);

CREATE TABLE email_thread_details (
    container_id text PRIMARY KEY REFERENCES containers (container_id),
    includes_third_party text, references_source_ids text
);

CREATE TABLE email_message_details (
    turn_id text PRIMARY KEY REFERENCES turns (turn_id),
    message_id text NOT NULL,
    subject text,
    from_id text, from_email text,
    to_ids text NOT NULL DEFAULT '', to_emails text NOT NULL DEFAULT '',
    cc_ids text NOT NULL DEFAULT '', cc_emails text NOT NULL DEFAULT '',
    in_reply_to text,                                     -- verbatim Message-ID header
    in_reply_to_turn_id text,                             -- resolved within the thread, else NULL (never guessed)
    reference_ids text NOT NULL DEFAULT '',               -- verbatim, space separated
    attachment_refs text NOT NULL DEFAULT '',             -- file names only; contents are not in the dataset
    authored_text text NOT NULL,                          -- body minus identified quoted blocks
    quoted_text text NOT NULL DEFAULT '',                 -- identified quoted blocks (empty when none are marked)
    quote_basis text NOT NULL                             -- 'no_quote_markers' | name of the marker that matched
);
CREATE UNIQUE INDEX idx_eml_msgid ON email_message_details (message_id);

CREATE TABLE chat_message_details (
    turn_id text PRIMARY KEY REFERENCES turns (turn_id),
    reply_to_turn_id text,                                -- only where the source states reply_to_message_id
    recipient_id text
);

CREATE TABLE segments (
    segment_id   text PRIMARY KEY,
    container_id text NOT NULL REFERENCES containers (container_id),
    client_id    text NOT NULL,
    seg_index    integer NOT NULL,
    topic_label  text NOT NULL,
    summary      text,                                    -- NULL: no summaries are generated in this phase
    start_seq    integer NOT NULL,
    end_seq      integer NOT NULL,
    method       text NOT NULL,
    confidence   real NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    review_status text NOT NULL DEFAULT 'unreviewed',
    UNIQUE (container_id, seg_index)
);
CREATE INDEX idx_seg_client_topic ON segments (client_id, topic_label);

CREATE TABLE segment_turns (
    segment_id text NOT NULL REFERENCES segments (segment_id),
    turn_id    text NOT NULL REFERENCES turns (turn_id),
    position   integer NOT NULL,
    PRIMARY KEY (segment_id, turn_id)
);
CREATE INDEX idx_segturn_turn ON segment_turns (turn_id);

CREATE TABLE evidence_spans (
    evidence_id  text PRIMARY KEY,
    turn_id      text NOT NULL REFERENCES turns (turn_id),
    container_id text NOT NULL REFERENCES containers (container_id),
    client_id    text NOT NULL,
    span_kind    text NOT NULL CHECK (span_kind IN ('full_turn', 'authored_part')),
    exact_text   text NOT NULL,
    start_offset integer,                                 -- offsets into turns.original_text; reliable by construction (whole turn or the authored prefix)
    end_offset   integer,
    source_ref   text NOT NULL,
    UNIQUE (turn_id, span_kind)
);
CREATE INDEX idx_span_client ON evidence_spans (client_id);

CREATE TABLE annotations (
    annotation_id integer PRIMARY KEY AUTOINCREMENT,
    target_type  text NOT NULL CHECK (target_type IN ('turn', 'segment', 'container')),
    target_id    text NOT NULL,
    container_id text NOT NULL REFERENCES containers (container_id),
    client_id    text NOT NULL,
    kind         text NOT NULL,                           -- dialogue_act | topic | question | correction_cue | commitment_cue | uncertainty_cue | temporal_ref | entity_ref
    value        text NOT NULL,
    provenance   text NOT NULL CHECK (provenance IN ('observed', 'rule_generated', 'model_generated')),
    method       text NOT NULL,
    confidence   real,
    UNIQUE (target_type, target_id, kind, value, method)
);
CREATE INDEX idx_ann_target ON annotations (target_type, target_id);
CREATE INDEX idx_ann_kind ON annotations (client_id, kind, value);

CREATE TABLE turn_relations (
    container_id text NOT NULL REFERENCES containers (container_id),
    from_turn_id text NOT NULL REFERENCES turns (turn_id),
    to_turn_id   text NOT NULL REFERENCES turns (turn_id),
    rel_type     text NOT NULL CHECK (rel_type IN ('replies_to', 'answers', 'corrects')),
    basis        text NOT NULL,                           -- 'in_reply_to_header' | 'reply_to_message_id' | 'rule_v1:adjacent_question' | 'rule_v1:correction_cue'
    provenance   text NOT NULL CHECK (provenance IN ('observed', 'rule_generated')),
    PRIMARY KEY (from_turn_id, to_turn_id, rel_type)
);

-- derived CRM pathway: never mixed into turns / segments / spans
CREATE TABLE derived_artifacts (
    crm_record_id text PRIMARY KEY REFERENCES source_artifacts (artifact_id),
    crm_file      text NOT NULL,
    artifact_type text NOT NULL,                          -- Activity | Note | Summary
    channel       text NOT NULL,
    client_id     text NOT NULL,
    advisor_id    text,
    author_id     text,
    created_at    text NOT NULL,
    subject       text,
    original_text text NOT NULL,
    transfer_method text,
    derived_status text NOT NULL CHECK (derived_status = 'derived'),
    is_original_source_evidence integer NOT NULL CHECK (is_original_source_evidence = 0)
);
CREATE INDEX idx_der_client ON derived_artifacts (client_id, created_at);

CREATE TABLE derived_sources (
    crm_record_id text NOT NULL REFERENCES derived_artifacts (crm_record_id),
    source_ref    text NOT NULL,
    resolved_container_id text,                           -- NULL if the ref does not resolve to an ingested container
    resolution    text NOT NULL CHECK (resolution IN ('container', 'unresolved')),
    PRIMARY KEY (crm_record_id, source_ref)
);
CREATE INDEX idx_dsrc_container ON derived_sources (resolved_container_id);
