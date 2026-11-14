from dataclasses import asdict
import logging
import socket
import sqlite3
import time
import unittest
from unittest.mock import patch

from q25.app.ingestion import IngestionService
from q25.core.documents import BackendError
from q25.persistence.backup import _copy, backup_database, restore_database, validate_database
from q25.persistence.database import open_database
from q25.persistence.repository import DocumentRepository
from q25.persistence.workspace import SyntheticWorkspace


class DocumentPipelineTests(unittest.TestCase):
    def test_synthetic_utf8_roundtrip_reopen_and_no_network_or_logs(self):
        raw = b"\xef\xbb\xbf" + "PERSONNE_FICTIVE_A\r\nQuébec e\u0301\nEnglish 🦊".encode()
        with SyntheticWorkspace() as workspace:
            source = workspace.write_fixture("source.txt", raw)
            target = workspace.path("state.db")
            with self.assertNoLogs(level=logging.DEBUG), \
                 patch.object(socket, "socket", side_effect=AssertionError("réseau interdit")), \
                 open_database(target, synthetic_workspace=workspace) as db:
                result = IngestionService(db).ingest(source)
                self.assertEqual(result.state, "completed")
                document = DocumentRepository(db).get_document(result.document_id)
                self.assertEqual(document.text.encode(), raw[3:])
                self.assertTrue(document.has_bom)
                validate_database(db.connection)
                audit = db.connection.execute("SELECT * FROM audit_events").fetchall()
                self.assertEqual(len(audit), 3)
                self.assertNotIn("PERSONNE_FICTIVE_A", repr(audit))
                self.assertNotIn(str(source), repr(audit))
                self.assertNotIn("PERSONNE_FICTIVE_A", repr(document))
            with open_database(target, synthetic_workspace=workspace) as reopened:
                self.assertEqual(DocumentRepository(reopened).get_document(result.document_id), document)
            self.assertEqual(source.read_bytes(), raw)

    def test_text_is_a_parameter_not_sql(self):
        raw = b"'); DROP TABLE documents; --\nPERSONNE_FICTIVE_A"
        with SyntheticWorkspace() as workspace, open_database(synthetic_workspace=workspace) as db:
            source = workspace.write_fixture("source.txt", raw)
            result = IngestionService(db).ingest(source)
            self.assertEqual(result.state, "completed")
            self.assertEqual(DocumentRepository(db).get_document(result.document_id).text, raw.decode())
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)

    def test_invalid_document_records_neutral_failure_without_partial_data(self):
        with SyntheticWorkspace() as workspace, open_database(synthetic_workspace=workspace) as db:
            source = workspace.write_fixture("PRIVATE.txt", b"PRIVATE\xff")
            result = IngestionService(db).ingest(source)
            self.assertEqual(result.state, "failed")
            self.assertEqual(result.error_code, "TXT_ENCODING")
            self.assertNotIn("PRIVATE", repr(asdict(result)))
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM document_elements").fetchone()[0], 0)

    def test_cancellation_before_and_during_transaction(self):
        for cancel_at in (1, 3, 4):
            with self.subTest(cancel_at=cancel_at), SyntheticWorkspace() as workspace, \
                 open_database(synthetic_workspace=workspace) as db:
                source = workspace.write_fixture("source.txt", b"fictif A\nfictif B\n")
                calls = 0
                def cancelled():
                    nonlocal calls
                    calls += 1
                    return calls >= cancel_at
                result = IngestionService(db).ingest(source, cancelled=cancelled)
                self.assertEqual(result.state, "cancelled")
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM document_elements").fetchone()[0], 0)
                self.assertNotIn("JOB_COMPLETED", repr(db.connection.execute("SELECT code FROM audit_events").fetchall()))

    def test_unexpected_failure_after_insert_rolls_back_and_hides_message(self):
        with SyntheticWorkspace() as workspace, open_database(synthetic_workspace=workspace) as db:
            source = workspace.write_fixture("source.txt", b"PRIVATE")
            service = IngestionService(db)
            original = service.repository.put_document
            def fail(document, cancelled):
                original(document, cancelled)
                raise RuntimeError("PRIVATE PATH AND CONTENT")
            with patch.object(service.repository, "put_document", side_effect=fail):
                result = service.ingest(source)
            self.assertEqual(result.error_code, "JOB_FAILED")
            self.assertNotIn("PRIVATE", repr(result))
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM document_elements").fetchone()[0], 0)

    def test_terminal_transition_and_audit_mutation_are_rejected(self):
        with SyntheticWorkspace() as workspace, open_database(synthetic_workspace=workspace) as db:
            source = workspace.write_fixture("source.txt", b"fictif")
            result = IngestionService(db).ingest(source)
            for sql, parameters in (("UPDATE jobs SET state='running' WHERE id=?", (result.job_id,)),
                                    ("UPDATE audit_events SET code='JOB_FAILED'", ()),
                                    ("DELETE FROM audit_events", ())):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.connection.execute(sql, parameters)


class BackupTests(unittest.TestCase):
    def populated(self, workspace):
        db = open_database(workspace.path("state.db"), synthetic_workspace=workspace)
        source = workspace.write_fixture("source.txt", "PERSONNE_FICTIVE_A\r\nQuébec".encode())
        result = IngestionService(db).ingest(source)
        self.assertEqual(result.state, "completed")
        return db, result

    def test_live_wal_backup_and_restore_preserve_contents(self):
        with SyntheticWorkspace() as workspace:
            db, result = self.populated(workspace)
            self.assertTrue(workspace.path("state.db-wal").exists())
            backup = backup_database(db, workspace.path("backup.db"))
            restored = restore_database(backup, workspace.path("restored.db"), workspace=workspace)
            with open_database(restored, synthetic_workspace=workspace) as reopened:
                self.assertEqual(DocumentRepository(db).get_document(result.document_id),
                                 DocumentRepository(reopened).get_document(result.document_id))
                self.assertEqual(db.connection.execute("SELECT * FROM audit_events").fetchall(),
                                 reopened.connection.execute("SELECT * FROM audit_events").fetchall())
                validate_database(reopened.connection)

    def test_existing_destination_never_overwritten(self):
        with SyntheticWorkspace() as workspace:
            db, _ = self.populated(workspace)
            occupied = workspace.write_fixture("occupied.db", b"KEEP")
            with self.assertRaises(BackendError):
                backup_database(db, occupied)
            source = backup_database(db, workspace.path("backup.db"))
            with self.assertRaises(BackendError):
                restore_database(source, occupied, workspace=workspace)
            self.assertEqual(occupied.read_bytes(), b"KEEP")

    def test_corrupt_backup_is_refused_before_creating_destination(self):
        with SyntheticWorkspace() as workspace:
            source = workspace.write_fixture("corrupt.db", b"PRIVATE NOT SQLITE")
            destination = workspace.path("restored.db")
            with self.assertRaises(BackendError) as error:
                restore_database(source, destination, workspace=workspace)
            self.assertFalse(destination.exists())
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_semantic_tampering_and_future_version_are_refused(self):
        for mutation in ("UPDATE documents SET sha256='" + "a" * 64 + "'", "PRAGMA user_version=999"):
            with self.subTest(mutation=mutation), SyntheticWorkspace() as workspace:
                db, _ = self.populated(workspace)
                backup = backup_database(db, workspace.path("backup.db"))
                connection = sqlite3.connect(backup)
                connection.execute(mutation)
                connection.commit()
                connection.close()
                before = backup.read_bytes()
                with self.assertRaises(BackendError):
                    restore_database(backup, workspace.path("restored.db"), workspace=workspace)
                self.assertEqual(backup.read_bytes(), before)
                self.assertFalse(workspace.path("restored.db").exists())

    def test_backup_refuses_active_transaction(self):
        with SyntheticWorkspace() as workspace:
            db, _ = self.populated(workspace)
            with db.transaction():
                with self.assertRaises(BackendError) as error:
                    backup_database(db, workspace.path("backup.db"))
                self.assertEqual(error.exception.code, "TRANSACTION_ACTIVE")
                self.assertFalse(workspace.path("backup.db").exists())

    def test_failed_backup_cleans_only_its_own_destination(self):
        with SyntheticWorkspace() as workspace:
            db, _ = self.populated(workspace)
            keeper = workspace.write_fixture("keeper.txt", b"KEEP")
            target = workspace.path("backup.db")
            with patch("q25.persistence.backup._copy", side_effect=BackendError("BACKUP_TIMEOUT")):
                with self.assertRaises(BackendError):
                    backup_database(db, target)
            self.assertFalse(target.exists())
            self.assertEqual(keeper.read_bytes(), b"KEEP")

    def test_real_copy_timeout_on_locked_destination(self):
        with SyntheticWorkspace() as workspace:
            source, _ = self.populated(workspace)
            path = workspace.claim_file(workspace.path("locked.db"))
            locker = sqlite3.connect(path, isolation_level=None)
            target = sqlite3.connect(path, isolation_level=None, timeout=0.01)
            try:
                locker.execute("CREATE TABLE placeholder(x)")
                locker.execute("BEGIN EXCLUSIVE")
                started = time.monotonic()
                with self.assertRaises(BackendError) as error:
                    _copy(source.connection, target, timeout=0.05)
                self.assertEqual(error.exception.code, "BACKUP_TIMEOUT")
                self.assertLess(time.monotonic() - started, 2)
            finally:
                locker.rollback()
                locker.close()
                target.close()

    def test_invalid_copy_timeouts_are_refused(self):
        with SyntheticWorkspace() as workspace:
            db, _ = self.populated(workspace)
            for timeout in (0, -1, float('nan'), float('inf'), True):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    backup_database(db, workspace.path("new.db"), timeout=timeout)
            self.assertFalse(workspace.path("new.db").exists())
