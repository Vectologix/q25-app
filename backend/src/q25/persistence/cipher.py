"""Preuve SQLCipher facultative, limitée aux espaces synthétiques éphémères.

Clés brutes de 32 octets fournies en mémoire par le harnais ; aucun coffre OS,
aucune persistance de clé, aucun repli SQLite en clair ou adoption de dossier.
"""

import importlib
from pathlib import Path
import sqlite3

from q25.core.documents import BackendError, StorageCapabilities
from q25.persistence.backup import (
    _copy, _snapshot_rows, _validate_timeout, validate_database,
)
from q25.persistence.database import BUSY_TIMEOUT_MS, Database, configure
from q25.persistence.migrations import migrate
from q25.persistence.workspace import SyntheticWorkspace

VALIDATED_CIPHER_VERSION = "4.12.0 community"


def _validate_key(key):
    if key is None:
        raise BackendError("CIPHER_KEY_REQUIRED")
    if type(key) is not bytes or len(key) != 32:
        raise BackendError("CIPHER_KEY_INVALID")


def _load_driver():
    probe = None
    try:
        driver = importlib.import_module("sqlcipher3.dbapi2")
        probe = driver.connect(":memory:")
        # Éviter les diagnostics natifs contenant des détails de stockage.
        probe.execute("PRAGMA cipher_log_level = NONE")
        if probe.execute("PRAGMA cipher_version").fetchall() != [(VALIDATED_CIPHER_VERSION,)]:
            raise BackendError("CIPHER_UNAVAILABLE")
        return driver
    except Exception as error:
        # Aucun message natif ou chemin d'import ne sort de la frontière.
        if isinstance(error, BackendError):
            raise
        raise BackendError("CIPHER_UNAVAILABLE") from None
    finally:
        if probe is not None:
            probe.close()


def _claim(workspace, path):
    # Ne pas adopter des journaux déjà présents pour une nouvelle destination.
    for suffix in ("-wal", "-shm", "-journal"):
        if path.with_name(path.name + suffix).exists() or path.with_name(path.name + suffix).is_symlink():
            raise BackendError("DESTINATION_EXISTS")
    workspace.claim_file(path)


def _remove_created(path):
    # Appelé uniquement après création exclusive du fichier et contrôle des voisins.
    for suffix in ("", "-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _connect(driver, path, key, *, readonly=False):
    connection = None
    try:
        connection = driver.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                                    uri=True, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000,
                                    cached_statements=0)
        connection.execute("PRAGMA cipher_log_level = NONE")
        connection.execute("PRAGMA cipher_memory_security = ON")
        # Seul un hexadécimal dérivé de 32 octets validés est interpolé ; jamais
        # une phrase utilisateur. Ne pas installer de trace SQL sur cette connexion.
        connection.execute('PRAGMA key = "x\'' + key.hex() + '\'"')
        configure(connection)
        if (connection.execute("PRAGMA cipher_memory_security").fetchall() != [("1",)]
                or connection.execute("PRAGMA cipher_plaintext_header_size").fetchall() != [("0",)]
                or connection.execute("PRAGMA temp_store").fetchall() != [(2,)]
                or connection.execute("PRAGMA cipher_use_hmac").fetchall() != [("1",)]):
            raise BackendError("CIPHER_UNAVAILABLE")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        if readonly:
            connection.execute("PRAGMA query_only = ON")
        return connection
    except BaseException as error:
        if connection is not None:
            connection.close()
        if isinstance(error, driver.Error):
            raise BackendError("CIPHER_INVALID") from None
        raise


def _check_cipher(connection):
    if connection.execute("PRAGMA cipher_integrity_check").fetchall() != []:
        raise BackendError("CIPHER_INVALID")


class EncryptedDatabase(Database):
    capabilities = StorageCapabilities(encrypted_at_rest=True, synthetic_only=True)

    def __init__(self, connection, workspace, path, driver):
        super().__init__(connection, workspace, path)
        self.driver = driver
        self.error_types = (sqlite3.Error, driver.Error)


def open_encrypted_database(path: Path, *, key: bytes | None = None,
                            synthetic_workspace: SyntheticWorkspace | None = None) -> EncryptedDatabase:
    if synthetic_workspace is None:
        raise BackendError("ENCRYPTION_REQUIRED")
    synthetic_workspace.root
    _validate_key(key)
    driver = _load_driver()  # Vérifier le vrai moteur avant toute création de fichier.
    path = synthetic_workspace.check_path(path, must_exist=False)
    created = not path.exists()
    connection = None
    if created:
        _claim(synthetic_workspace, path)
    try:
        connection = _connect(driver, path, key)
        if not created:
            _check_cipher(connection)
        migrate(connection, errors=(sqlite3.Error, driver.Error))
        _check_cipher(connection)
        validate_database(connection, errors=(sqlite3.Error, driver.Error))
        if connection.execute("PRAGMA journal_mode = WAL").fetchone() != ("wal",):
            raise BackendError("STORAGE_FAILURE")
        database = EncryptedDatabase(connection, synthetic_workspace, path, driver)
        synthetic_workspace.register(database.close)
        return database
    except BaseException as error:
        if connection is not None:
            connection.close()
        if created:
            _remove_created(path)
        if isinstance(error, driver.Error):
            raise BackendError("CIPHER_INVALID") from None
        raise


def _new_encrypted_copy(source, destination, key, workspace, driver, *, timeout):
    _claim(workspace, destination)
    target = None
    success = False
    try:
        target = _connect(driver, destination, key)
        _copy(source, target, timeout=timeout)
        _check_cipher(target)
        validate_database(target, errors=(sqlite3.Error, driver.Error))
        if _snapshot_rows(source) != _snapshot_rows(target):
            raise BackendError("BACKUP_INVALID")
        success = True
        return destination
    except driver.Error:
        raise BackendError("BACKUP_INVALID") from None
    finally:
        if target is not None:
            target.close()
        if not success:
            _remove_created(destination)


def backup_encrypted_database(database: EncryptedDatabase, destination: Path, *,
                              key: bytes | None = None, timeout: float = 2.0) -> Path:
    if not isinstance(database, EncryptedDatabase):
        raise BackendError("ENCRYPTION_REQUIRED")
    database.ensure_open()
    _validate_key(key)
    _validate_timeout(timeout)
    if database.connection.in_transaction:
        raise BackendError("TRANSACTION_ACTIVE")
    destination = database.workspace.check_path(destination, must_exist=False)
    try:
        database.connection.execute("BEGIN")
        _check_cipher(database.connection)
        validate_database(database.connection, errors=database.error_types)
        return _new_encrypted_copy(database.connection, destination, key, database.workspace,
                                   database.driver, timeout=timeout)
    except database.error_types:
        raise BackendError("BACKUP_INVALID") from None
    finally:
        if database.connection.in_transaction:
            database.connection.rollback()


def restore_encrypted_database(source: Path, destination: Path, *,
                               workspace: SyntheticWorkspace, source_key: bytes | None = None,
                               destination_key: bytes | None = None, timeout: float = 2.0) -> Path:
    workspace.root
    _validate_key(source_key)
    _validate_key(destination_key)
    _validate_timeout(timeout)
    driver = _load_driver()
    source = workspace.check_path(source)
    destination = workspace.check_path(destination, must_exist=False)
    connection = None
    try:
        connection = _connect(driver, source, source_key, readonly=True)
        connection.execute("BEGIN")
        _check_cipher(connection)
        validate_database(connection, errors=(sqlite3.Error, driver.Error))
        return _new_encrypted_copy(connection, destination, destination_key, workspace, driver,
                                   timeout=timeout)
    except driver.Error:
        raise BackendError("BACKUP_INVALID") from None
    finally:
        if connection is not None:
            connection.close()
