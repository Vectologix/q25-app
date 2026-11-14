"""Liaison explicite d'un fournisseur de clés au prototype SQLCipher synthétique."""

from pathlib import Path

from q25.core.documents import BackendError
from q25.core.keys import KeyProvider, KeyReference
from q25.persistence.cipher import (
    EncryptedDatabase, _validate_key, backup_encrypted_database,
    open_encrypted_database, restore_encrypted_database,
)
from q25.persistence.workspace import SyntheticWorkspace
from q25.persistence.backup import _validate_timeout


def _load_key(provider: KeyProvider, reference: KeyReference) -> bytes:
    if not isinstance(reference, KeyReference):
        raise BackendError("KEY_REFERENCE_INVALID")
    try:
        key = provider.load(reference)
    except BackendError:
        raise
    except Exception:
        raise BackendError("KEYRING_FAILURE") from None
    _validate_key(key)
    return key


def open_keyed_database(path: Path, *, provider: KeyProvider, reference: KeyReference,
                        synthetic_workspace: SyntheticWorkspace | None = None) -> EncryptedDatabase:
    if synthetic_workspace is None:
        raise BackendError("ENCRYPTION_REQUIRED")
    synthetic_workspace.check_path(path, must_exist=False)
    return open_encrypted_database(path, key=_load_key(provider, reference),
                                   synthetic_workspace=synthetic_workspace)


def backup_keyed_database(database: EncryptedDatabase, destination: Path, *,
                          provider: KeyProvider, reference: KeyReference,
                          timeout: float = 2.0) -> Path:
    if not isinstance(database, EncryptedDatabase):
        raise BackendError("ENCRYPTION_REQUIRED")
    database.ensure_open()
    _validate_timeout(timeout)
    if database.connection.in_transaction:
        raise BackendError("TRANSACTION_ACTIVE")
    database.workspace.check_path(destination, must_exist=False)
    return backup_encrypted_database(database, destination, key=_load_key(provider, reference),
                                     timeout=timeout)


def restore_keyed_database(source: Path, destination: Path, *, workspace: SyntheticWorkspace,
                           provider: KeyProvider, source_reference: KeyReference,
                           destination_reference: KeyReference, timeout: float = 2.0) -> Path:
    _validate_timeout(timeout)
    workspace.check_path(source)
    workspace.check_path(destination, must_exist=False)
    return restore_encrypted_database(source, destination, workspace=workspace,
                                      source_key=_load_key(provider, source_reference),
                                      destination_key=_load_key(provider, destination_reference),
                                      timeout=timeout)
