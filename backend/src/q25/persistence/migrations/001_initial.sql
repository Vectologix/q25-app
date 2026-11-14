CREATE TABLE documents (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    has_bom INTEGER NOT NULL CHECK(has_bom IN (0, 1)),
    format TEXT NOT NULL DEFAULT 'txt' CHECK(format = 'txt'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE document_elements (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    start INTEGER NOT NULL CHECK(start >= 0),
    end INTEGER NOT NULL CHECK(end >= start),
    byte_start INTEGER NOT NULL CHECK(byte_start >= 0),
    byte_end INTEGER NOT NULL CHECK(byte_end >= byte_start),
    text TEXT NOT NULL,
    PRIMARY KEY(document_id, ordinal),
    CHECK(length(text) = end - start),
    CHECK(length(CAST(text AS BLOB)) = byte_end - byte_start)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    state TEXT NOT NULL CHECK(state IN ('waiting', 'running', 'completed', 'failed', 'cancelled')),
    document_id TEXT REFERENCES documents(id),
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK((state = 'completed' AND document_id IS NOT NULL AND error_code IS NULL)
       OR (state IN ('failed', 'cancelled') AND document_id IS NULL AND error_code IS NOT NULL)
       OR (state IN ('waiting', 'running') AND document_id IS NULL AND error_code IS NULL))
);

CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    code TEXT NOT NULL CHECK(code IN ('JOB_CREATED', 'JOB_STARTED', 'JOB_COMPLETED', 'JOB_FAILED', 'JOB_CANCELLED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

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
