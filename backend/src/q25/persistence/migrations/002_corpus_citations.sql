CREATE UNIQUE INDEX document_revision ON documents(id, sha256);
CREATE TABLE corpora (
    id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*')
);
CREATE TABLE corpus_documents (
    corpus_id TEXT NOT NULL REFERENCES corpora(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(corpus_id, document_id, sha256),
    UNIQUE(corpus_id, document_id),
    FOREIGN KEY(document_id, sha256) REFERENCES documents(id, sha256)
);
CREATE TABLE citations (
    id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
    corpus_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    start INTEGER NOT NULL CHECK(typeof(start)='integer' AND start>=0),
    end INTEGER NOT NULL CHECK(typeof(end)='integer' AND end>start),
    byte_start INTEGER NOT NULL CHECK(typeof(byte_start)='integer' AND byte_start>=0),
    byte_end INTEGER NOT NULL CHECK(typeof(byte_end)='integer' AND byte_end>byte_start),
    FOREIGN KEY(corpus_id, document_id, sha256)
        REFERENCES corpus_documents(corpus_id, document_id, sha256) ON DELETE CASCADE
);
