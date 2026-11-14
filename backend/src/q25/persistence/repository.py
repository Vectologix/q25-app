"""Dépôts paramétrés ; les mutations de document participent à la transaction de tâche."""

from dataclasses import dataclass
import sqlite3
from typing import Callable
from uuid import uuid4

from q25.core.documents import (
    BackendError, DocumentElement, ExtractedDocument, technical_id,
)
from q25.persistence.database import Database

_EVENTS = {"running": "JOB_STARTED", "completed": "JOB_COMPLETED",
           "failed": "JOB_FAILED", "cancelled": "JOB_CANCELLED"}
_TRANSITIONS = {"waiting": {"running", "failed", "cancelled"},
                "running": {"completed", "failed", "cancelled"}}


@dataclass(frozen=True)
class JobResult:
    job_id: str
    state: str
    document_id: str | None
    error_code: str | None

    def __post_init__(self):
        if not technical_id(self.job_id) or self.state not in ("waiting", "running", *_EVENTS):
            raise BackendError("JOB_STATE")
        if self.state == "completed":
            if not technical_id(self.document_id) or self.error_code is not None:
                raise BackendError("JOB_STATE")
        elif self.document_id is not None:
            raise BackendError("JOB_STATE")
        if self.state in ("failed", "cancelled"):
            try:
                BackendError(self.error_code)
            except (ValueError, TypeError):
                raise BackendError("JOB_STATE") from None
            if (self.state == "cancelled") != (self.error_code == "JOB_CANCELLED"):
                raise BackendError("JOB_STATE")
        elif self.error_code is not None:
            raise BackendError("JOB_STATE")


def read_document(connection: sqlite3.Connection, document_id: str) -> ExtractedDocument:
    if not technical_id(document_id):
        raise BackendError("DOCUMENT_INVALID")
    row = connection.execute(
        "SELECT id, sha256, byte_size, has_bom FROM documents WHERE id=?", (document_id,),
    ).fetchone()
    if row is None or type(row[3]) is not int or row[3] not in (0, 1):
        raise BackendError("DOCUMENT_INVALID")
    elements = tuple(DocumentElement(*element) for element in connection.execute(
        "SELECT ordinal, start, end, byte_start, byte_end, text FROM document_elements "
        "WHERE document_id=? ORDER BY ordinal", (document_id,),
    ))
    return ExtractedDocument(row[0], row[1], row[2], bool(row[3]), elements)


class DocumentRepository:
    def __init__(self, database: Database):
        self.database = database

    def get_document(self, document_id: str) -> ExtractedDocument:
        self.database.ensure_open()
        try:
            return read_document(self.database.connection, document_id)
        except self.database.error_types:
            raise BackendError("STORAGE_FAILURE") from None

    def put_document(self, document: ExtractedDocument, cancelled: Callable[[], bool]):
        self.database.ensure_open()
        connection = self.database.connection
        if not connection.in_transaction:
            raise BackendError("TRANSACTION_ACTIVE")
        connection.execute("INSERT INTO documents(id,sha256,byte_size,has_bom) VALUES (?,?,?,?)",
                           (document.document_id, document.sha256, document.byte_size, int(document.has_bom)))
        for element in document.elements:
            if cancelled():
                raise BackendError("JOB_CANCELLED")
            connection.execute("INSERT INTO document_elements VALUES (?,?,?,?,?,?,?)",
                               (document.document_id, element.ordinal, element.start, element.end,
                                element.byte_start, element.byte_end, element.text))

    def create_job(self) -> str:
        job_id = uuid4().hex
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO jobs(id,state) VALUES (?, 'waiting')", (job_id,))
            connection.execute("INSERT INTO audit_events(job_id,code) VALUES (?, 'JOB_CREATED')", (job_id,))
        return job_id

    def transition(self, job_id: str, state: str, *, document_id=None, error_code=None):
        self.database.ensure_open()
        if not self.database.connection.in_transaction:
            raise BackendError("TRANSACTION_ACTIVE")
        JobResult(job_id, state, document_id, error_code)
        row = self.database.connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or state not in _TRANSITIONS.get(row[0], set()):
            raise BackendError("JOB_STATE")
        self.database.connection.execute(
            "UPDATE jobs SET state=?,document_id=?,error_code=?,"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (state, document_id, error_code, job_id),
        )
        self.database.connection.execute("INSERT INTO audit_events(job_id,code) VALUES (?,?)",
                                         (job_id, _EVENTS[state]))

    def get_job(self, job_id: str) -> JobResult:
        self.database.ensure_open()
        try:
            row = self.database.connection.execute(
                "SELECT id,state,document_id,error_code FROM jobs WHERE id=?", (job_id,),
            ).fetchone()
            if row is None:
                raise BackendError("JOB_STATE")
            return JobResult(*row)
        except self.database.error_types:
            raise BackendError("STORAGE_FAILURE") from None
