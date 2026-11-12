-- CMF human-approval workflow (harness P1.2).  SQLite ONLY: it relies on partial unique indexes and BEFORE UPDATE/DELETE triggers that make the audit trail
-- append-only at the database level.  Applied by harness/approval_store.py (version recorded in approval_schema_migrations); idempotent.
-- Timestamps are fixed-width UTC text (YYYY-MM-DDTHH:MM:SS.ffffffZ) so lexicographic comparison is chronological.
--
-- approval_requests : one row per approval.  The BOUND columns (requester, client, payload hash, data version, fingerprint, created/expires) can never change.
--                     status moves only pending -> approved|rejected|expired and approved -> consumed|expired.  report_payload (the report awaiting release, the only
--                     sensitive column) is cleared in the same statement that rejects, consumes or expires the request.
-- approval_events   : append-only audit trail (no UPDATE, no DELETE).  A request/decision/release/expiry can be recorded at most once per approval; refusals are
--                     recorded as *_denied events carrying a reason code, never as successes.

CREATE TABLE IF NOT EXISTS approval_schema_migrations (
    version     TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    approval_id       TEXT PRIMARY KEY CHECK (length(approval_id) >= 20),
    request_id        TEXT NOT NULL DEFAULT '',
    requester_subject TEXT NOT NULL CHECK (length(trim(requester_subject)) > 0),
    requester_role    TEXT NOT NULL DEFAULT '',
    client_id         TEXT NOT NULL DEFAULT '',          -- the clients of the report, sorted and comma-joined (a report may cover several)
    category          TEXT NOT NULL CHECK (length(category) > 0),
    operation         TEXT NOT NULL CHECK (length(operation) > 0),
    reason            TEXT NOT NULL DEFAULT '',
    report_payload    TEXT,                              -- JSON of the exact draft awaiting release; NULL once rejected / consumed / expired (and for non-report approvals)
    payload_hash      TEXT NOT NULL CHECK (length(payload_hash) = 64),
    data_version      TEXT NOT NULL DEFAULT '',
    fingerprint       TEXT NOT NULL CHECK (length(fingerprint) = 64),
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'consumed', 'expired')),
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    decided_by        TEXT,
    decided_role      TEXT,
    decided_at        TEXT,
    decision_reason   TEXT,
    consumed_at       TEXT,
    consumed_by       TEXT,
    CHECK (expires_at > created_at),
    -- nobody approves or rejects their own request (compared on the stable subject, case- and whitespace-insensitively)
    CHECK (decided_by IS NULL OR lower(trim(decided_by)) <> lower(trim(requester_subject))),
    CHECK (status NOT IN ('approved', 'rejected', 'consumed') OR (decided_by IS NOT NULL AND decided_at IS NOT NULL AND length(trim(coalesce(decision_reason, ''))) > 0)),
    CHECK (status <> 'approved' OR decided_role IN ('supervisor', 'admin')),
    CHECK ((status = 'consumed') = (consumed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS approval_requests_fingerprint ON approval_requests (fingerprint, status);
CREATE INDEX IF NOT EXISTS approval_requests_status_expires ON approval_requests (status, expires_at);
CREATE INDEX IF NOT EXISTS approval_requests_requester ON approval_requests (requester_subject, created_at);

CREATE TABLE IF NOT EXISTS approval_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id   TEXT NOT NULL REFERENCES approval_requests (approval_id) ON DELETE RESTRICT,
    actor_subject TEXT NOT NULL CHECK (length(trim(actor_subject)) > 0),
    actor_role    TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL CHECK (event_type IN ('requested', 'approved', 'rejected', 'released', 'expired', 'release_denied', 'decision_denied')),
    reason        TEXT NOT NULL DEFAULT '',
    fingerprint   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS approval_events_by_approval ON approval_events (approval_id, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS approval_events_once ON approval_events (approval_id, event_type)
    WHERE event_type IN ('requested', 'approved', 'rejected', 'released', 'expired');

CREATE TRIGGER IF NOT EXISTS approval_events_no_update BEFORE UPDATE ON approval_events
BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approval_events_no_delete BEFORE DELETE ON approval_events
BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approval_requests_no_delete BEFORE DELETE ON approval_requests
BEGIN SELECT RAISE(ABORT, 'approval_requests cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS approval_requests_bound_fields BEFORE UPDATE ON approval_requests
WHEN NEW.approval_id IS NOT OLD.approval_id OR NEW.request_id IS NOT OLD.request_id OR NEW.requester_subject IS NOT OLD.requester_subject
  OR NEW.requester_role IS NOT OLD.requester_role OR NEW.client_id IS NOT OLD.client_id OR NEW.category IS NOT OLD.category OR NEW.operation IS NOT OLD.operation
  OR NEW.reason IS NOT OLD.reason OR NEW.payload_hash IS NOT OLD.payload_hash OR NEW.data_version IS NOT OLD.data_version OR NEW.fingerprint IS NOT OLD.fingerprint
  OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at
  OR (NEW.report_payload IS NOT OLD.report_payload AND NOT (NEW.report_payload IS NULL AND NEW.status IN ('rejected', 'consumed', 'expired')))
  OR (OLD.decided_by IS NOT NULL AND (NEW.decided_by IS NOT OLD.decided_by OR NEW.decided_role IS NOT OLD.decided_role OR NEW.decided_at IS NOT OLD.decided_at
                                      OR NEW.decision_reason IS NOT OLD.decision_reason))
  OR (OLD.consumed_at IS NOT NULL AND (NEW.consumed_at IS NOT OLD.consumed_at OR NEW.consumed_by IS NOT OLD.consumed_by))
BEGIN SELECT RAISE(ABORT, 'approval_requests: bound fields are immutable'); END;

CREATE TRIGGER IF NOT EXISTS approval_requests_transition BEFORE UPDATE OF status ON approval_requests
WHEN NEW.status <> OLD.status AND NOT ((OLD.status = 'pending' AND NEW.status IN ('approved', 'rejected', 'expired'))
                                    OR (OLD.status = 'approved' AND NEW.status IN ('consumed', 'expired')))
BEGIN SELECT RAISE(ABORT, 'approval_requests: illegal status transition'); END;

-- defence in depth: even a buggy writer cannot decide or consume after the approval expired
CREATE TRIGGER IF NOT EXISTS approval_requests_not_after_expiry BEFORE UPDATE OF status ON approval_requests
WHEN NEW.status <> OLD.status AND ((NEW.status IN ('approved', 'rejected') AND NEW.decided_at > OLD.expires_at) OR (NEW.status = 'consumed' AND NEW.consumed_at > OLD.expires_at))
BEGIN SELECT RAISE(ABORT, 'approval_requests: the approval has expired'); END;
