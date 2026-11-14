"""Preuves SQLCipher réelles sur fixtures inventées ; aucun secret utilisateur."""

from dataclasses import replace
import importlib.util
import logging
from secrets import token_bytes
import socket
import sqlite3
import time
import unittest
from unittest.mock import patch

from q25.app.ingestion import IngestionService
from q25.core.documents import BackendError
from q25.persistence.backup import _copy, _snapshot_rows, backup_database, validate_database
from q25.persistence.cipher import (
    backup_encrypted_database, open_encrypted_database, restore_encrypted_database,
)
from q25.persistence.database import open_database
from q25.persistence.migrations import Migration, default_migrations, migrate
from q25.persistence.repository import DocumentRepository
from q25.persistence.workspace import SyntheticWorkspace

MARKER = "PERSONNE_ENTIEREMENT_FICTIVE_2B1_Québec_🦊"
RAW = b"\xef\xbb\xbf" + (MARKER + "\r\nEnglish e\u0301\n").encode()


class CipherPolicyTests(unittest.TestCase):
    def test_no_workspace_refuses_normal_usage(self):
        with SyntheticWorkspace() as workspace:
            target = workspace.path("state.db")
            with self.assertRaises(BackendError) as error:
                open_encrypted_database(target, key=token_bytes(32))
            self.assertEqual(error.exception.code, "ENCRYPTION_REQUIRED")
            self.assertFalse(target.exists())

    def test_absent_or_invalid_key_refused_before_loading_driver(self):
        for key in (None, b"", b"x" * 31, b"x" * 33, "x" * 32, bytearray(32), True):
            with self.subTest(kind=type(key).__name__), SyntheticWorkspace() as workspace:
                with patch("q25.persistence.cipher._load_driver") as loader:
                    with self.assertRaises(BackendError) as error:
                        open_encrypted_database(workspace.path("state.db"), key=key,
                                                synthetic_workspace=workspace)
                    self.assertEqual(error.exception.code,
                                     "CIPHER_KEY_REQUIRED" if key is None else "CIPHER_KEY_INVALID")
                    loader.assert_not_called()
                self.assertEqual(list(workspace.root.iterdir()), [])

    def test_missing_driver_has_neutral_error_and_no_file(self):
        with SyntheticWorkspace() as workspace:
            with patch("q25.persistence.cipher.importlib.import_module",
                       side_effect=ImportError("PRIVATE PATH")):
                with self.assertRaises(BackendError) as error:
                    open_encrypted_database(workspace.path("state.db"), key=token_bytes(32),
                                            synthetic_workspace=workspace)
            self.assertEqual(error.exception.code, "CIPHER_UNAVAILABLE")
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertEqual(list(workspace.root.iterdir()), [])

    def test_standard_sqlite_cannot_impersonate_cipher(self):
        with SyntheticWorkspace() as workspace:
            with patch("q25.persistence.cipher.importlib.import_module", return_value=sqlite3):
                with self.assertRaises(BackendError) as error:
                    open_encrypted_database(workspace.path("state.db"), key=token_bytes(32),
                                            synthetic_workspace=workspace)
            self.assertEqual(error.exception.code, "CIPHER_UNAVAILABLE")
            self.assertEqual(list(workspace.root.iterdir()), [])


@unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "binding SQLCipher facultatif absent")
class CipherPipelineTests(unittest.TestCase):
    def populated(self, workspace, *, name="state.db"):
        key = token_bytes(32)
        database = open_encrypted_database(workspace.path(name), key=key,
                                           synthetic_workspace=workspace)
        source = workspace.write_fixture(name + ".txt", RAW)
        result = IngestionService(database).ingest(source)
        self.assertEqual(result.state, "completed")
        return database, key, result

    def assert_ciphertext(self, path):
        raw = path.read_bytes()
        self.assertGreater(len(raw), 0)
        self.assertFalse(raw.startswith(b"SQLite format 3"))
        self.assertNotIn(MARKER.encode(), raw)
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("SELECT * FROM sqlite_master").fetchall()
        finally:
            connection.close()

    def test_unicode_roundtrip_no_network_logs_or_plaintext_in_db_and_wal(self):
        with SyntheticWorkspace() as workspace:
            with self.assertNoLogs(level=logging.DEBUG), \
                 patch.object(socket, "socket", side_effect=AssertionError("réseau interdit")):
                db, key, result = self.populated(workspace)
                document = DocumentRepository(db).get_document(result.document_id)
                self.assertEqual(document.text.encode(), RAW[3:])
                self.assertTrue(document.has_bom)
                self.assertTrue(db.capabilities.encrypted_at_rest)
                self.assertTrue(db.capabilities.synthetic_only)
                self.assertEqual(db.connection.execute("PRAGMA temp_store").fetchone(), (2,))
                self.assertEqual(db.connection.execute("PRAGMA cipher_memory_security").fetchone(), ("1",))
                audit = db.connection.execute("SELECT * FROM audit_events").fetchall()
                self.assertNotIn(MARKER, repr(audit))
                self.assertNotIn(key.hex(), repr(result))
                for suffix in ("", "-wal", "-shm"):
                    path = workspace.path("state.db" + suffix)
                    self.assertTrue(path.exists())
                    self.assertNotIn(MARKER.encode(), path.read_bytes())
                db.close()
                self.assert_ciphertext(workspace.path("state.db"))
                with open_encrypted_database(workspace.path("state.db"), key=key,
                                             synthetic_workspace=workspace) as reopened:
                    self.assertEqual(DocumentRepository(reopened).get_document(result.document_id), document)
                self.assertEqual(workspace.path("state.db.txt").read_bytes(), RAW)

    def test_wrong_key_preserves_existing_file_and_hides_details(self):
        with SyntheticWorkspace() as workspace:
            db, key, result = self.populated(workspace)
            db.close()
            path = workspace.path("state.db")
            before = path.read_bytes()
            wrong = token_bytes(32)
            with self.assertRaises(BackendError) as error:
                open_encrypted_database(path, key=wrong, synthetic_workspace=workspace)
            self.assertEqual(error.exception.code, "CIPHER_INVALID")
            for secret in (str(path), key.hex(), wrong.hex(), MARKER):
                self.assertNotIn(secret, str(error.exception))
            self.assertEqual(path.read_bytes(), before)
            with open_encrypted_database(path, key=key, synthetic_workspace=workspace) as good:
                self.assertEqual(DocumentRepository(good).get_document(result.document_id).text.encode(), RAW[3:])

    def test_ciphertext_page_corruption_refused_without_repair(self):
        with SyntheticWorkspace() as workspace:
            db, key, _ = self.populated(workspace)
            db.close()
            path = workspace.path("state.db")
            original = path.read_bytes()
            for offset in (128, 4096 + 128):
                with self.subTest(offset=offset):
                    damaged = bytearray(original)
                    damaged[offset] ^= 0x80
                    path.write_bytes(damaged)
                    with self.assertRaises(BackendError):
                        open_encrypted_database(path, key=key, synthetic_workspace=workspace)
                    self.assertEqual(path.read_bytes(), damaged)

    def test_plain_database_is_not_silently_converted_or_overwritten(self):
        with SyntheticWorkspace() as workspace:
            path = workspace.path("state.db")
            with open_database(path, synthetic_workspace=workspace):
                pass
            before = path.read_bytes()
            with self.assertRaises(BackendError):
                open_encrypted_database(path, key=token_bytes(32), synthetic_workspace=workspace)
            self.assertEqual(path.read_bytes(), before)

    def test_failed_migration_rolls_back_vendor_exceptions(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            with self.assertRaises(BackendError) as error:
                migrate(db.connection, (*default_migrations(),
                        Migration(4, "CREATE TABLE probe(x); INSERT INTO absent VALUES(1);")),
                        errors=db.error_types)
            self.assertEqual(error.exception.code, "MIGRATION_INVALID")
            self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone(), (3,))
            self.assertIsNone(db.connection.execute("SELECT name FROM sqlite_master WHERE name='probe'").fetchone())
            validate_database(db.connection, errors=db.error_types)

    def test_changed_migration_and_transaction_sql_are_refused(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            first = default_migrations()[0]
            for migrations in ((replace(first, sql=first.sql + "\n-- modified"), *default_migrations()[1:]),
                               (*default_migrations(), Migration(4, "CREATE TABLE probe(x); COMMIT;"))):
                with self.subTest(), self.assertRaises(BackendError):
                    migrate(db.connection, migrations, errors=db.error_types)
            self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone(), (3,))
            validate_database(db.connection, errors=db.error_types)

    def test_future_version_is_refused_and_preserved(self):
        with SyntheticWorkspace() as workspace:
            db, key, _ = self.populated(workspace)
            db.connection.execute("PRAGMA user_version = 999")
            db.close()
            path = workspace.path("state.db")
            before = path.read_bytes()
            with self.assertRaises(BackendError) as error:
                open_encrypted_database(path, key=key, synthetic_workspace=workspace)
            self.assertEqual(error.exception.code, "MIGRATION_VERSION")
            self.assertEqual(path.read_bytes(), before)

    def test_foreign_keys_and_lock_failure_are_enforced(self):
        with SyntheticWorkspace() as workspace:
            first, key, _ = self.populated(workspace)
            with self.assertRaises(BackendError) as error, first.transaction():
                first.connection.execute("INSERT INTO document_elements VALUES ('absent',0,0,1,0,1,'x')")
            self.assertEqual(error.exception.code, "STORAGE_FAILURE")
            with open_encrypted_database(workspace.path("state.db"), key=key,
                                         synthetic_workspace=workspace) as second:
                with first.transaction():
                    started = time.monotonic()
                    with self.assertRaises(BackendError) as error:
                        with second.transaction():
                            pass
                    self.assertEqual(error.exception.code, "STORAGE_FAILURE")
                    self.assertLess(time.monotonic() - started, 2)
            document_id = first.connection.execute("SELECT id FROM documents").fetchone()[0]
            first.connection.execute("DROP TABLE document_elements")
            with self.assertRaises(BackendError) as error:
                DocumentRepository(first).get_document(document_id)
            self.assertEqual(error.exception.code, "STORAGE_FAILURE")
            self.assertNotIn("document_elements", str(error.exception))

    def test_cancellation_rolls_back_document_inserts(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            source = workspace.write_fixture("cancel.txt", RAW)
            calls = 0
            def cancelled():
                nonlocal calls
                calls += 1
                return calls >= 4
            result = IngestionService(db).ingest(source, cancelled=cancelled)
            self.assertEqual(result.state, "cancelled")
            self.assertEqual(db.connection.execute("SELECT count(*) FROM documents").fetchone(), (1,))

    def test_backup_restore_with_distinct_keys_and_identical_snapshot(self):
        with SyntheticWorkspace() as workspace:
            db, original_key, _ = self.populated(workspace)
            expected = _snapshot_rows(db.connection)
            backup_key, restored_key = token_bytes(32), token_bytes(32)
            backup = backup_encrypted_database(db, workspace.path("backup.db"), key=backup_key)
            self.assert_ciphertext(backup)
            with self.assertRaises(BackendError):
                open_encrypted_database(backup, key=original_key, synthetic_workspace=workspace)
            restored = restore_encrypted_database(backup, workspace.path("restored.db"),
                                                  workspace=workspace, source_key=backup_key,
                                                  destination_key=restored_key)
            self.assert_ciphertext(restored)
            with open_encrypted_database(restored, key=restored_key, synthetic_workspace=workspace) as reopened:
                self.assertEqual(_snapshot_rows(reopened.connection), expected)
            with self.assertRaises(BackendError):
                open_encrypted_database(restored, key=backup_key, synthetic_workspace=workspace)

    def test_backup_sees_one_snapshot_during_concurrent_wal_commit(self):
        with SyntheticWorkspace() as workspace:
            db, key, _ = self.populated(workspace)
            expected = _snapshot_rows(db.connection)
            second = open_encrypted_database(workspace.path("state.db"), key=key, synthetic_workspace=workspace)
            source = workspace.write_fixture("concurrent.txt", b"AUTRE_PERSONNE_FICTIVE")
            def copy_after_write(*args, **kwargs):
                self.assertEqual(IngestionService(second).ingest(source).state, "completed")
                return _copy(*args, **kwargs)
            backup_key = token_bytes(32)
            with patch("q25.persistence.cipher._copy", side_effect=copy_after_write):
                backup = backup_encrypted_database(db, workspace.path("backup.db"), key=backup_key)
            with open_encrypted_database(backup, key=backup_key, synthetic_workspace=workspace) as reopened:
                self.assertEqual(_snapshot_rows(reopened.connection), expected)
            self.assertEqual(db.connection.execute("SELECT count(*) FROM documents").fetchone(), (2,))

    def test_plain_backup_api_refuses_encrypted_database_before_creating_file(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            target = workspace.path("plain.db")
            with self.assertRaises(BackendError) as error:
                backup_database(db, target)
            self.assertEqual(error.exception.code, "ENCRYPTED_COPY_REQUIRED")
            self.assertFalse(target.exists())

    def test_encrypted_backup_api_refuses_plain_database(self):
        with SyntheticWorkspace() as workspace, open_database(synthetic_workspace=workspace) as db:
            with self.assertRaises(BackendError):
                backup_encrypted_database(db, workspace.path("target.db"), key=token_bytes(32))
            self.assertFalse(workspace.path("target.db").exists())

    def test_backup_and_restore_never_overwrite_existing_destination(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            keeper = workspace.write_fixture("keeper.db", b"KEEP")
            backup_key = token_bytes(32)
            with self.assertRaises(BackendError):
                backup_encrypted_database(db, keeper, key=backup_key)
            backup = backup_encrypted_database(db, workspace.path("backup.db"), key=backup_key)
            with self.assertRaises(BackendError):
                restore_encrypted_database(backup, keeper, workspace=workspace,
                                           source_key=backup_key, destination_key=token_bytes(32))
            self.assertEqual(keeper.read_bytes(), b"KEEP")

    def test_wrong_backup_key_refused_before_destination_creation(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            backup = backup_encrypted_database(db, workspace.path("backup.db"), key=token_bytes(32))
            before = backup.read_bytes()
            with self.assertRaises(BackendError):
                restore_encrypted_database(backup, workspace.path("restored.db"), workspace=workspace,
                                           source_key=token_bytes(32), destination_key=token_bytes(32))
            self.assertFalse(workspace.path("restored.db").exists())
            self.assertEqual(backup.read_bytes(), before)

    def test_failed_copy_cleans_only_new_files(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            keeper = workspace.write_fixture("keeper.txt", b"KEEP")
            target = workspace.path("backup.db")
            with patch("q25.persistence.cipher._copy", side_effect=BackendError("BACKUP_TIMEOUT")):
                with self.assertRaises(BackendError) as error:
                    backup_encrypted_database(db, target, key=token_bytes(32))
            self.assertEqual(error.exception.code, "BACKUP_TIMEOUT")
            self.assertFalse(target.exists())
            self.assertEqual(keeper.read_bytes(), b"KEEP")
            self.assertEqual(db.connection.execute("SELECT count(*) FROM documents").fetchone(), (1,))

    def test_restore_rejects_semantic_tampering_before_creating_destination(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            backup_key = token_bytes(32)
            backup = backup_encrypted_database(db, workspace.path("backup.db"), key=backup_key)
            with open_encrypted_database(backup, key=backup_key, synthetic_workspace=workspace) as edited:
                edited.connection.execute("UPDATE documents SET sha256='" + "a" * 64 + "'")
            before = backup.read_bytes()
            with self.assertRaises(BackendError):
                restore_encrypted_database(backup, workspace.path("restored.db"), workspace=workspace,
                                           source_key=backup_key, destination_key=token_bytes(32))
            self.assertFalse(workspace.path("restored.db").exists())
            self.assertEqual(backup.read_bytes(), before)

    def test_invalid_backup_keys_timeouts_and_active_transactions(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            target = workspace.path("backup.db")
            for timeout in (0, -1, float("nan"), float("inf"), True):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    backup_encrypted_database(db, target, key=token_bytes(32), timeout=timeout)
            for key in (None, b"bad"):
                with self.subTest(), self.assertRaises(BackendError):
                    backup_encrypted_database(db, target, key=key)
            with db.transaction(), self.assertRaises(BackendError) as error:
                backup_encrypted_database(db, target, key=token_bytes(32))
            self.assertEqual(error.exception.code, "TRANSACTION_ACTIVE")
            self.assertFalse(target.exists())

    def test_external_paths_symlinks_and_unowned_sidecars_refused(self):
        with SyntheticWorkspace() as workspace:
            db, key, _ = self.populated(workspace)
            link = workspace.path("link.db")
            link.symlink_to(workspace.path("state.db"))
            for path in (link, workspace.root.parent / "escape.db"):
                with self.subTest(), self.assertRaises(BackendError):
                    open_encrypted_database(path, key=key, synthetic_workspace=workspace)
            keeper = workspace.write_fixture("new.db-wal", b"KEEP")
            with self.assertRaises(BackendError):
                open_encrypted_database(workspace.path("new.db"), key=key, synthetic_workspace=workspace)
            self.assertFalse(workspace.path("new.db").exists())
            self.assertEqual(keeper.read_bytes(), b"KEEP")

    def test_failed_initial_migration_cleans_only_owned_files_and_hides_error(self):
        with SyntheticWorkspace() as workspace:
            keeper = workspace.write_fixture("keeper.txt", b"KEEP")
            from sqlcipher3 import dbapi2 as driver
            with patch("q25.persistence.cipher.migrate", side_effect=driver.DatabaseError("PRIVATE")):
                with self.assertRaises(BackendError) as error:
                    open_encrypted_database(workspace.path("new.db"), key=token_bytes(32),
                                            synthetic_workspace=workspace)
            self.assertEqual(error.exception.code, "CIPHER_INVALID")
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertEqual(list(workspace.root.iterdir()), [keeper])

    def test_real_backup_timeout_on_locked_cipher_destination(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            target_key = token_bytes(32)
            target_db = open_encrypted_database(workspace.path("locked.db"), key=target_key,
                                                synthetic_workspace=workspace)
            other = open_encrypted_database(workspace.path("locked.db"), key=target_key,
                                            synthetic_workspace=workspace)
            with target_db.transaction():
                started = time.monotonic()
                with self.assertRaises(BackendError) as error:
                    _copy(db.connection, other.connection, timeout=0.05)
                self.assertEqual(error.exception.code, "BACKUP_TIMEOUT")
                self.assertLess(time.monotonic() - started, 2)

    def test_workspace_closes_native_connection_and_rejects_reuse(self):
        with SyntheticWorkspace() as workspace:
            db, _, _ = self.populated(workspace)
            root = workspace.root
            native_error = db.driver.Error
        self.assertFalse(root.exists())
        with self.assertRaises(BackendError):
            db.ensure_open()
        with self.assertRaises(native_error):
            db.connection.execute("SELECT 1")
