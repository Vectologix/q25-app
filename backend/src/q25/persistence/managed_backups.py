"""Copies possédées par une session synthétique ; aucun registre ou secret persistant."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4

from q25.core.documents import BackendError, technical_id
from q25.persistence.backup import backup_database, restore_database, validate_database
from q25.persistence.database import configure
from q25.persistence.cipher import (
    _load_driver, _connect, _check_cipher, _validate_key,
    backup_encrypted_database, restore_encrypted_database,
)


@dataclass(frozen=True)
class BackupReceipt:
    backup_id: str
    created_at: datetime
    purge_operation_count: int
    encrypted: bool


@dataclass(frozen=True, repr=False)
class _Record:
    receipt: BackupReceipt
    path: Path
    identity: tuple[int, int]
    digest: bytes
    operations: tuple[str, ...]


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while part := stream.read(65536):
            digest.update(part)
    return digest.digest()


def _operations(connection):
    return tuple(row[0] for row in connection.execute("SELECT id FROM purge_operations ORDER BY id"))


class ManagedBackupService:
    """Une instance et sa connexion d'origine, sans adoption ni contournement de restauration."""

    def __init__(self, database):
        database.ensure_open()
        self.database = database
        self._connection = database.connection
        self._encrypted = database.capabilities.encrypted_at_rest
        self._records = {}
        self._closed = False
        database.workspace.register(self.close)

    def close(self):
        self._records.clear()
        self._closed = True

    def _check(self):
        self.database.ensure_open()
        if self._closed or self.database.connection is not self._connection:
            raise BackendError("MANAGED_BACKUP_INVALID")
        if self._connection.in_transaction:
            raise BackendError("TRANSACTION_ACTIVE")
        if self._connection.execute("PRAGMA user_version").fetchone()[0] != 3:
            raise BackendError("MIGRATION_VERSION")

    def _key(self, key):
        if self._encrypted:
            _validate_key(key)
        elif key is not None:
            raise BackendError("CIPHER_KEY_INVALID")

    def _inspect(self, path, key):
        connection = None
        try:
            if self._encrypted:
                connection = _connect(_load_driver(), path, key, readonly=True)
                _check_cipher(connection)
            else:
                connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
                configure(connection)
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            validate_database(connection, errors=self.database.error_types)
            if connection.execute("PRAGMA user_version").fetchone()[0] != 3:
                raise BackendError("MIGRATION_VERSION")
            return _operations(connection)
        except self.database.error_types:
            raise BackendError("BACKUP_INVALID") from None
        finally:
            if connection is not None:
                connection.close()

    def _verify(self, backup_id):
        if not technical_id(backup_id) or backup_id not in self._records:
            raise BackendError("MANAGED_BACKUP_INVALID")
        record = self._records[backup_id]
        try:
            self.database.workspace.check_path(record.path)
            status = record.path.stat()
            if status.st_nlink != 1 or (status.st_dev, status.st_ino) != record.identity:
                raise BackendError("MANAGED_BACKUP_INVALID")
            for suffix in ("-wal", "-shm", "-journal"):
                neighbor = record.path.with_name(record.path.name + suffix)
                if neighbor.exists() or neighbor.is_symlink():
                    raise BackendError("MANAGED_BACKUP_INVALID")
            if _digest(record.path) != record.digest:
                raise BackendError("MANAGED_BACKUP_INVALID")
        except (OSError, BackendError):
            raise BackendError("MANAGED_BACKUP_INVALID") from None
        return record

    def create(self, *, key=None):
        self._check()
        self._key(key)
        if len(self._records) >= 32:
            raise BackendError("MANAGED_BACKUP_INVALID")
        backup_id = uuid4().hex
        path = self.database.workspace.path("managed-backup-" + backup_id + ".db")
        created = False
        try:
            if self._encrypted:
                backup_encrypted_database(self.database, path, key=key)
            else:
                backup_database(self.database, path)
            created = True
            operations = self._inspect(path, key)
            status = path.stat()
            receipt = BackupReceipt(backup_id, datetime.now(timezone.utc), len(operations), self._encrypted)
            self._records[backup_id] = _Record(receipt, path, (status.st_dev, status.st_ino), _digest(path), operations)
            self._verify(backup_id)
            return receipt
        except BaseException as error:
            self._records.pop(backup_id, None)
            if created:
                try:
                    path.unlink()
                except OSError:
                    pass  # Le workspace possédé reste responsable de sa fin de vie.
            if isinstance(error, OSError):
                raise BackendError("MANAGED_BACKUP_INVALID") from None
            raise

    def restore(self, backup_id, *, key=None, destination_key=None):
        self._check()
        self._key(key)
        self._key(destination_key)
        record = self._verify(backup_id)
        operations = self._inspect(record.path, key)
        if operations != record.operations:
            raise BackendError("MANAGED_BACKUP_INVALID")
        # Le verrou empêche une nouvelle purge de la base d'origine pendant la copie.
        with self.database.transaction() as connection:
            validate_database(connection, errors=self.database.error_types)
            if _operations(connection) != operations:
                raise BackendError("MANAGED_BACKUP_STALE")
            target = self.database.workspace.path("managed-restore-" + uuid4().hex + ".db")
            if self._encrypted:
                return restore_encrypted_database(record.path, target, workspace=self.database.workspace,
                                                  source_key=key, destination_key=destination_key)
            return restore_database(record.path, target, workspace=self.database.workspace)

    def delete(self, backup_id, *, confirmed=False):
        self._check()
        if confirmed is not True:
            raise BackendError("MANAGED_BACKUP_CONFIRMATION")
        record = self._verify(backup_id)
        try:
            record.path.unlink()
        except OSError:
            raise BackendError("MANAGED_BACKUP_INVALID") from None
        del self._records[backup_id]
