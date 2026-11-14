from dataclasses import asdict
import importlib.util
import logging
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

from tests.integration.test_corpus_pipeline import CorpusCases
from q25.core.documents import BackendError
from q25.persistence.backup import _snapshot_rows
from q25.persistence.managed_backups import ManagedBackupService
from q25.persistence.purge import ManualPurgeService
from q25.persistence.workspace import SyntheticWorkspace


class ManagedBackupCases:
    encrypted = False
    make = CorpusCases.make
    populate = CorpusCases.populate

    def setup_service(self, w):
        db = self.make(w)
        repo, corpus, span = self.populate(w, db)
        repo.remove_document(corpus, span.revision.document_id)
        return db, ManagedBackupService(db), span

    def create(self, service):
        return service.create(key=b"g" * 32) if self.encrypted else service.create()

    def restore(self, service, receipt):
        return service.restore(receipt.backup_id, key=b"g" * 32, destination_key=b"h" * 32) if self.encrypted else service.restore(receipt.backup_id)

    def open_restored(self, w, path):
        if self.encrypted:
            from q25.persistence.cipher import open_encrypted_database
            return open_encrypted_database(path, key=b"h" * 32, synthetic_workspace=w)
        from q25.persistence.database import open_database
        return open_database(path, synthetic_workspace=w)

    def backup_path(self, w, receipt):
        return w.path("managed-backup-" + receipt.backup_id + ".db")

    def test_fresh_copy_restore_keeps_all_rows_without_touching_origin(self):
        with SyntheticWorkspace() as w:
            db, service, _ = self.setup_service(w)
            before = _snapshot_rows(db.connection)
            receipt = self.create(service)
            path = self.restore(service, receipt)
            self.assertNotEqual(path, db.path)
            self.assertEqual(_snapshot_rows(db.connection), before)
            with self.open_restored(w, path) as restored:
                self.assertEqual(_snapshot_rows(restored.connection), before)
            self.assertEqual(receipt.encrypted, self.encrypted)
            self.assertEqual(receipt.purge_operation_count, 0)

    def test_pre_purge_copy_refused_and_post_purge_copy_restores_only_current_history(self):
        with SyntheticWorkspace() as w:
            db, service, span = self.setup_service(w)
            old = self.create(service)
            original = self.backup_path(w, old).read_bytes()
            purge = ManualPurgeService(db)
            candidates = (span.revision.document_id,)
            preview = purge.prepare(candidates)
            purge.execute(preview.preview_id, candidates, confirmed=True, acknowledge_copies_remain=True)
            paths = set(w.root.iterdir())
            with self.assertRaises(BackendError) as error:
                self.restore(service, old)
            self.assertEqual(error.exception.code, "MANAGED_BACKUP_STALE")
            self.assertEqual(set(w.root.iterdir()), paths)
            self.assertEqual(self.backup_path(w, old).read_bytes(), original)
            new = self.create(service)
            self.assertEqual(new.purge_operation_count, 1)
            target = self.restore(service, new)
            with self.open_restored(w, target) as restored:
                self.assertEqual(restored.connection.execute("SELECT count(*) FROM documents").fetchone()[0], 0)
                self.assertEqual(_snapshot_rows(restored.connection), _snapshot_rows(db.connection))

    def test_delete_requires_explicit_confirmation_and_only_selected_copy_disappears(self):
        with SyntheticWorkspace() as w:
            db, service, _ = self.setup_service(w)
            first, second = self.create(service), self.create(service)
            other = self.backup_path(w, second).read_bytes()
            before = _snapshot_rows(db.connection)
            for flag in (False, None, 1, "yes"):
                with self.subTest(), self.assertRaises(BackendError):
                    service.delete(first.backup_id, confirmed=flag)
            service.delete(first.backup_id, confirmed=True)
            self.assertFalse(self.backup_path(w, first).exists())
            self.assertEqual(self.backup_path(w, second).read_bytes(), other)
            self.assertEqual(_snapshot_rows(db.connection), before)
            with self.assertRaises(BackendError):
                self.restore(service, first)

    def test_tampered_copy_refuses_restore_and_delete(self):
        with SyntheticWorkspace() as w:
            _, service, _ = self.setup_service(w)
            receipt = self.create(service)
            path = self.backup_path(w, receipt)
            raw = path.read_bytes()
            path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            for action in (lambda: self.restore(service, receipt), lambda: service.delete(receipt.backup_id, confirmed=True)):
                with self.assertRaises(BackendError) as error:
                    action()
                self.assertEqual(error.exception.code, "MANAGED_BACKUP_INVALID")
            self.assertTrue(path.exists())

    def test_symlink_and_neighbors_are_refused_without_touching_other_file(self):
        with SyntheticWorkspace() as w:
            _, service, _ = self.setup_service(w)
            receipt = self.create(service)
            path = self.backup_path(w, receipt)
            for suffix in ("-wal", "-shm", "-journal"):
                neighbor = w.write_fixture(path.name + suffix, b"FICTIF")
                with self.assertRaises(BackendError):
                    service.delete(receipt.backup_id, confirmed=True)
                neighbor.unlink()
            target = w.write_fixture("other.txt", b"FICTIF_INTACT")
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(BackendError):
                service.delete(receipt.backup_id, confirmed=True)
            with self.assertRaises(BackendError):
                self.restore(service, receipt)
            self.assertEqual(target.read_bytes(), b"FICTIF_INTACT")

    def test_foreign_and_closed_sessions_do_not_adopt_or_delete_copies(self):
        with SyntheticWorkspace() as w:
            db, service, _ = self.setup_service(w)
            receipt = self.create(service)
            other = ManagedBackupService(db)
            with self.assertRaises(BackendError):
                self.restore(other, receipt)
            service.close()
            with self.assertRaises(BackendError):
                service.delete(receipt.backup_id, confirmed=True)
            self.assertTrue(self.backup_path(w, receipt).exists())
            for identity in (None, "../PRIVATE", "f" * 32):
                with self.subTest(), self.assertRaises(BackendError) as error:
                    other.delete(identity, confirmed=True)
                self.assertNotIn("PRIVATE", str(error.exception))

    def test_unlink_failure_preserves_registry_and_allows_retry(self):
        with SyntheticWorkspace() as w:
            _, service, _ = self.setup_service(w)
            receipt = self.create(service)
            with patch.object(Path, "unlink", side_effect=PermissionError("PRIVATE_PATH")):
                with self.assertRaises(BackendError) as error:
                    service.delete(receipt.backup_id, confirmed=True)
                self.assertNotIn("PRIVATE_PATH", str(error.exception))
            self.assertTrue(self.backup_path(w, receipt).exists())
            service.delete(receipt.backup_id, confirmed=True)
            self.assertFalse(self.backup_path(w, receipt).exists())

    def test_no_network_process_logs_or_sensitive_receipt(self):
        with SyntheticWorkspace() as w:
            _, service, span = self.setup_service(w)
            with self.assertNoLogs(level=logging.DEBUG), \
                 patch.object(socket, "socket", side_effect=AssertionError("network")), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
                receipt = self.create(service)
                self.restore(service, receipt)
                service.delete(receipt.backup_id, confirmed=True)
            self.assertEqual(set(asdict(receipt)), {"backup_id", "created_at", "purge_operation_count", "encrypted"})
            for marker in ("PERSONNE_FICTIVE_A", span.revision.sha256, str(w.root)):
                self.assertNotIn(marker, repr(receipt))

    def test_key_contract_and_wrong_key_do_not_create_destination(self):
        with SyntheticWorkspace() as w:
            _, service, _ = self.setup_service(w)
            before = set(w.root.iterdir())
            if self.encrypted:
                with self.assertRaises(BackendError):
                    service.create()
                self.assertEqual(set(w.root.iterdir()), before)
                receipt = self.create(service)
                before = set(w.root.iterdir())
                with self.assertRaises(BackendError):
                    service.restore(receipt.backup_id, key=b"x" * 32, destination_key=b"h" * 32)
                self.assertEqual(set(w.root.iterdir()), before)
                raw = self.backup_path(w, receipt).read_bytes()
                self.assertNotEqual(raw[:16], b"SQLite format 3\x00")
                self.assertNotIn(b"PERSONNE_FICTIVE_A", raw)
            else:
                with self.assertRaises(BackendError):
                    service.create(key=b"x" * 32)
                self.assertEqual(set(w.root.iterdir()), before)

    def test_active_transaction_closed_database_and_old_schema_refused(self):
        with SyntheticWorkspace() as w:
            db, service, _ = self.setup_service(w)
            with db.transaction(), self.assertRaises(BackendError) as error:
                self.create(service)
            self.assertEqual(error.exception.code, "TRANSACTION_ACTIVE")
            db.close()
            with self.assertRaises(BackendError):
                self.create(service)
            old = self.make(w, name="old.db", version=2)
            service = ManagedBackupService(old)
            with self.assertRaises(BackendError) as error:
                self.create(service)
            self.assertEqual(error.exception.code, "MIGRATION_VERSION")


class SQLiteManagedBackupTests(ManagedBackupCases, unittest.TestCase):
    pass


@unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher facultatif absent")
class CipherManagedBackupTests(ManagedBackupCases, unittest.TestCase):
    encrypted = True
