"""Ouverture normale refusée ; SQLite en clair seulement via capacité synthétique."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from q25.core.documents import BackendError, StorageCapabilities
from q25.persistence.migrations import migrate
from q25.persistence.workspace import SyntheticWorkspace

BUSY_TIMEOUT_MS = 200


def configure(connection: sqlite3.Connection):
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if hasattr(connection, "enable_load_extension"):
        connection.enable_load_extension(False)
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise BackendError("STORAGE_FAILURE")


class Database:
    error_types = (sqlite3.Error,)
    capabilities = StorageCapabilities(encrypted_at_rest=False, synthetic_only=True)

    def __init__(self, connection: sqlite3.Connection, workspace: SyntheticWorkspace, path: Path | None):
        self.connection = connection
        self.workspace = workspace
        self.path = path
        self.closed = False

    def __enter__(self):
        self.ensure_open()
        return self

    def __exit__(self, *_):
        self.close()

    def ensure_open(self):
        self.workspace.root
        if self.closed:
            raise BackendError("STORAGE_CLOSED")

    def close(self):
        if not self.closed:
            self.connection.close()
            self.closed = True

    @contextmanager
    def transaction(self):
        self.ensure_open()
        if self.connection.in_transaction:
            raise BackendError("TRANSACTION_ACTIVE")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except BaseException as error:
            if self.connection.in_transaction:
                self.connection.rollback()
            if isinstance(error, self.error_types):
                raise BackendError("STORAGE_FAILURE") from None
            raise


def open_database(path: Path | None = None, *, synthetic_workspace: SyntheticWorkspace | None = None) -> Database:
    # Le futur adaptateur chiffré ne doit pas être simulé par un simple booléen.
    if synthetic_workspace is None:
        raise BackendError("ENCRYPTION_REQUIRED")
    synthetic_workspace.root
    connection = None
    created = False
    if path is not None:
        path = synthetic_workspace.check_path(path, must_exist=False)
        if not path.exists():
            synthetic_workspace.claim_file(path)
            created = True
    try:
        target = ":memory:" if path is None else path.as_uri() + "?mode=rw"
        connection = sqlite3.connect(target, uri=path is not None, isolation_level=None,
                                     timeout=BUSY_TIMEOUT_MS / 1000)
        configure(connection)
        migrate(connection)
        if path is not None:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if mode != "wal":
                raise BackendError("STORAGE_FAILURE")
        database = Database(connection, synthetic_workspace, path)
        synthetic_workspace.register(database.close)
        return database
    except BaseException as error:
        if connection is not None:
            connection.close()
        if created:
            path.unlink(missing_ok=True)
        if isinstance(error, sqlite3.Error):
            raise BackendError("STORAGE_FAILURE") from None
        raise
