"""Purge manuelle logique, limitée à une session synthétique et à sa base."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from uuid import uuid4

from q25.core.documents import BackendError, technical_id
from q25.persistence.backup import validate_database
from q25.persistence.retention import MAX_CANDIDATES, DocumentDependencies


def _candidates(values):
    if (type(values) is not tuple or not values or len(values) > MAX_CANDIDATES
            or any(not technical_id(value) for value in values) or len(set(values)) != len(values)):
        raise BackendError("RETENTION_INVALID")
    return tuple(sorted(values))


@dataclass(frozen=True)
class PurgePreview:
    preview_id: str = field(repr=False)
    observed_at: datetime
    documents: tuple[DocumentDependencies, ...] = field(repr=False)


@dataclass(frozen=True)
class PurgeResult:
    operation_id: str
    documents: int
    elements: int
    jobs: int


def _state(connection, candidates):
    dependencies, signature, job_ids = [], [], []
    for identity in candidates:
        document = connection.execute("SELECT id,sha256,byte_size,has_bom FROM documents WHERE id=?", (identity,)).fetchone()
        if document is None:
            raise BackendError("RETENTION_DOCUMENT_MISSING")
        counts = tuple(connection.execute(query, (identity,)).fetchone()[0] for query in (
            "SELECT count(*) FROM document_elements WHERE document_id=?",
            "SELECT count(*) FROM corpus_documents WHERE document_id=?",
            "SELECT count(*) FROM citations WHERE document_id=?"))
        jobs = connection.execute("SELECT * FROM jobs WHERE document_id=? ORDER BY id", (identity,)).fetchall()
        events = connection.execute("SELECT * FROM audit_events WHERE job_id IN (SELECT id FROM jobs WHERE document_id=?) ORDER BY sequence", (identity,)).fetchall()
        admissions = connection.execute("SELECT * FROM corpus_documents WHERE document_id=? ORDER BY corpus_id", (identity,)).fetchall()
        citations = connection.execute("SELECT * FROM citations WHERE document_id=? ORDER BY id", (identity,)).fetchall()
        dependencies.append(DocumentDependencies(identity, *counts, len(jobs), len(events)))
        signature.append((document, counts, jobs, events, admissions, citations))
        job_ids.extend(row[0] for row in jobs)
    return tuple(dependencies), hashlib.sha256(repr(signature).encode()).digest(), tuple(job_ids)


class ManualPurgeService:
    """La confirmation API n'est pas une authentification de l'utilisateur final."""

    def __init__(self, database):
        database.ensure_open()
        self.database = database
        self._connection = database.connection
        self._pending = {}
        self._closed = False
        database.workspace.register(self.close)

    def close(self):
        self._pending.clear()
        self._closed = True

    def _check(self):
        self.database.ensure_open()
        if self._closed or self.database.connection is not self._connection:
            raise BackendError("PURGE_CONFIRMATION")
        if self._connection.execute("PRAGMA user_version").fetchone()[0] != 3:
            raise BackendError("MIGRATION_VERSION")

    def prepare(self, candidates):
        candidates = _candidates(candidates)
        self._check()
        if len(self._pending) >= 32:
            raise BackendError("PURGE_CONFIRMATION")
        with self.database.transaction() as connection:
            validate_database(connection, errors=self.database.error_types)
            documents, signature, _ = _state(connection, candidates)
        preview_id = uuid4().hex
        self._pending[preview_id] = (candidates, signature)
        return PurgePreview(preview_id, datetime.now(timezone.utc), documents)

    def execute(self, preview_id, candidates, *, confirmed=False, acknowledge_copies_remain=False):
        if not technical_id(preview_id):
            raise BackendError("PURGE_CONFIRMATION")
        # Toute tentative consomme la préparation, y compris une confirmation refusée.
        saved = self._pending.pop(preview_id, None)
        self._check()
        if saved is None or confirmed is not True or acknowledge_copies_remain is not True:
            raise BackendError("PURGE_CONFIRMATION")
        candidates = _candidates(candidates)
        if candidates != saved[0]:
            raise BackendError("PURGE_CONFIRMATION")
        with self.database.transaction() as connection:
            validate_database(connection, errors=self.database.error_types)
            documents, signature, job_ids = _state(connection, candidates)
            if signature != saved[1]:
                raise BackendError("PURGE_CHANGED")
            if any(item.admissions or item.citations for item in documents):
                raise BackendError("PURGE_BLOCKED")
            for job_id in job_ids:
                row = connection.execute("SELECT state,source_purged FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row != ("completed", 0):
                    raise BackendError("PURGE_BLOCKED")
            for job_id in job_ids:
                connection.execute("UPDATE jobs SET document_id=NULL,source_purged=1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (job_id,))
                connection.execute("INSERT INTO audit_events(job_id,code) VALUES (?,'DOCUMENT_PURGED')", (job_id,))
            for identity in candidates:
                connection.execute("DELETE FROM documents WHERE id=?", (identity,))
            result = PurgeResult(uuid4().hex, len(documents), sum(item.elements for item in documents), len(job_ids))
            connection.execute("INSERT INTO purge_operations(id,documents,elements,jobs) VALUES (?,?,?,?)",
                               (result.operation_id, result.documents, result.elements, result.jobs))
            validate_database(connection, errors=self.database.error_types)
        return result
