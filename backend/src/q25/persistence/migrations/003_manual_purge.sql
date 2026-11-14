CREATE TABLE jobs_new (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    state TEXT NOT NULL CHECK(state IN ('waiting', 'running', 'completed', 'failed', 'cancelled')),
    document_id TEXT REFERENCES documents(id),
    error_code TEXT,
    source_purged INTEGER NOT NULL DEFAULT 0 CHECK(source_purged IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK((state = 'completed' AND error_code IS NULL AND ((document_id IS NOT NULL AND source_purged=0) OR (document_id IS NULL AND source_purged=1)))
       OR (source_purged=0 AND state IN ('failed', 'cancelled') AND document_id IS NULL AND error_code IS NOT NULL)
       OR (source_purged=0 AND state IN ('waiting', 'running') AND document_id IS NULL AND error_code IS NULL))
);

CREATE TABLE audit_events_new (
    sequence INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs_new(id),
    code TEXT NOT NULL CHECK(code IN ('JOB_CREATED', 'JOB_STARTED', 'JOB_COMPLETED', 'JOB_FAILED', 'JOB_CANCELLED', 'DOCUMENT_PURGED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO jobs_new(id,state,document_id,error_code,created_at,updated_at)
SELECT id,state,document_id,error_code,created_at,updated_at FROM jobs;
INSERT INTO audit_events_new SELECT * FROM audit_events;
DROP TABLE audit_events;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
ALTER TABLE audit_events_new RENAME TO audit_events;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit append-only');
END;

CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit append-only');
END;

CREATE TRIGGER job_transition BEFORE UPDATE OF state ON jobs
WHEN NOT ((OLD.state = 'waiting' AND NEW.state IN ('running', 'cancelled', 'failed'))
       OR (OLD.state = 'running' AND NEW.state IN ('completed', 'cancelled', 'failed')))
BEGIN
    SELECT RAISE(ABORT, 'invalid job transition');
END;

CREATE TRIGGER job_purge_guard BEFORE UPDATE OF source_purged ON jobs
WHEN NOT (OLD.source_purged=0 AND NEW.source_purged=1 AND OLD.state='completed'
          AND NEW.state='completed' AND OLD.document_id IS NOT NULL AND NEW.document_id IS NULL)
BEGIN SELECT RAISE(ABORT, 'invalid purge transition'); END;
CREATE TABLE purge_operations (
    id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    documents INTEGER NOT NULL CHECK(typeof(documents)='integer' AND documents>0 AND documents<=256),
    elements INTEGER NOT NULL CHECK(typeof(elements)='integer' AND elements>=0),
    jobs INTEGER NOT NULL CHECK(typeof(jobs)='integer' AND jobs>=0)
);
CREATE TRIGGER purge_no_update BEFORE UPDATE ON purge_operations
BEGIN SELECT RAISE(ABORT,'purge append-only'); END;
CREATE TRIGGER purge_no_delete BEFORE DELETE ON purge_operations
BEGIN SELECT RAISE(ABORT,'purge append-only'); END;
