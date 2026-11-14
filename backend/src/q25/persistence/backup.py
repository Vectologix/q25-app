"""Copies SQLite cohérentes, bornées, et limitées à l'espace synthétique."""

from pathlib import Path
import math
import sqlite3
import time

from q25.core.documents import BackendError
from q25.persistence.database import Database, configure
from q25.persistence.migrations import default_migrations, verify_history
from q25.persistence.repository import JobResult, read_document
from q25.persistence.workspace import SyntheticWorkspace

_TABLES = {"schema_migrations", "documents", "document_elements", "jobs", "audit_events"}
_TRIGGERS = {"audit_no_update", "audit_no_delete", "job_transition"}


def _validate_timeout(timeout: float):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Délai de copie fini et positif requis.")


def validate_database(connection: sqlite3.Connection, *, errors=(sqlite3.Error,)):
    """Contrôles structurels et sémantiques ; aucune preuve cryptographique d'authenticité."""
    try:
        version = verify_history(connection, default_migrations(), errors=errors)
        if version not in (1, 2):
            raise BackendError("BACKUP_INVALID")
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        triggers = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        expected_tables = _TABLES | ({"corpora", "corpus_documents", "citations"} if version == 2 else set())
        if tables != expected_tables or triggers != _TRIGGERS:
            raise BackendError("BACKUP_INVALID")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackendError("BACKUP_INVALID")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise BackendError("BACKUP_INVALID")
        for (document_id,) in connection.execute("SELECT id FROM documents"):
            read_document(connection, document_id)
        if version == 2:
            from q25.persistence.corpus import validate_corpora
            validate_corpora(connection)
        for row in connection.execute("SELECT id,state,document_id,error_code FROM jobs"):
            JobResult(*row)
    except errors + (BackendError, ValueError, TypeError):
        raise BackendError("BACKUP_INVALID") from None


def _copy(source: sqlite3.Connection, destination: sqlite3.Connection, *, timeout: float):
    deadline = time.monotonic() + timeout
    def progress(*_):
        if time.monotonic() >= deadline:
            raise BackendError("BACKUP_TIMEOUT")
    source.backup(destination, pages=64, progress=progress, sleep=0.01)


def _snapshot_rows(connection: sqlite3.Connection) -> tuple:
    # Noms de tables fixes, jamais fournis par un document ou l'appelant.
    queries = (
        "SELECT * FROM schema_migrations ORDER BY version",
        "SELECT * FROM documents ORDER BY id",
        "SELECT * FROM document_elements ORDER BY document_id,ordinal",
        "SELECT * FROM jobs ORDER BY id",
        "SELECT * FROM audit_events ORDER BY sequence",
    )
    if connection.execute("PRAGMA user_version").fetchone()[0] == 2:
        queries += ("SELECT * FROM corpora ORDER BY id",
                    "SELECT * FROM corpus_documents ORDER BY corpus_id,document_id",
                    "SELECT * FROM citations ORDER BY id")
    return tuple(connection.execute(query).fetchall() for query in queries)


def _new_copy(source: sqlite3.Connection, destination: Path, workspace: SyntheticWorkspace,
              *, timeout: float) -> Path:
    workspace.claim_file(destination)
    target = None
    success = False
    try:
        target = sqlite3.connect(destination.as_uri() + "?mode=rw", uri=True,
                                 isolation_level=None, timeout=0.2)
        configure(target)
        _copy(source, target, timeout=timeout)
        validate_database(target)
        if _snapshot_rows(source) != _snapshot_rows(target):
            raise BackendError("BACKUP_INVALID")
        success = True
        return destination
    except sqlite3.Error:
        raise BackendError("BACKUP_INVALID") from None
    finally:
        if target is not None:
            target.close()
        if not success:
            # Ce fichier vient exclusivement de claim_file ; aucun fichier préexistant.
            destination.unlink(missing_ok=True)


def backup_database(database: Database, destination: Path, *, timeout: float = 2.0) -> Path:
    database.ensure_open()
    if database.capabilities.encrypted_at_rest:
        raise BackendError("ENCRYPTED_COPY_REQUIRED")
    _validate_timeout(timeout)
    if database.connection.in_transaction:
        raise BackendError("TRANSACTION_ACTIVE")
    destination = database.workspace.check_path(destination, must_exist=False)
    # La validation et la copie voient le même instantané WAL, même en cas d'autre écrivain.
    try:
        database.connection.execute("BEGIN")
        validate_database(database.connection)
        return _new_copy(database.connection, destination, database.workspace, timeout=timeout)
    except sqlite3.Error:
        raise BackendError("BACKUP_INVALID") from None
    finally:
        if database.connection.in_transaction:
            database.connection.rollback()


def restore_database(source: Path, destination: Path, *, workspace: SyntheticWorkspace,
                     timeout: float = 2.0) -> Path:
    _validate_timeout(timeout)
    source = workspace.check_path(source)
    destination = workspace.check_path(destination, must_exist=False)
    connection = None
    try:
        connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True,
                                     isolation_level=None, timeout=0.2)
        configure(connection)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        validate_database(connection)
        return _new_copy(connection, destination, workspace, timeout=timeout)
    except sqlite3.Error:
        raise BackendError("BACKUP_INVALID") from None
    finally:
        if connection is not None:
            connection.close()
