from dataclasses import asdict
import importlib.util
import logging
import socket
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from tests.integration.test_corpus_pipeline import CorpusCases
from q25.core.documents import BackendError
from q25.core.provenance import CorpusReference
from q25.persistence.backup import _snapshot_rows
from q25.persistence.repository import DocumentRepository
from q25.persistence.retention import inventory_retention
from q25.persistence.workspace import SyntheticWorkspace
from tests.unit.test_provenance import document


class RetentionCases:
    encrypted = False
    make = CorpusCases.make
    populate = CorpusCases.populate

    def test_shared_source_partial_total_removal_and_audit_blockers(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            repo, corpus, span = self.populate(w, db)
            other = CorpusReference("c" * 32)
            repo.create(other)
            repo.admit(other, span.revision)
            repo.save_citation(other, "d" * 32, repo.snapshot(other).anchor(span.revision, 0, 1))
            candidates = (span.revision.document_id,)
            first = inventory_retention(db, candidates).documents[0]
            self.assertEqual((first.elements, first.admissions, first.citations, first.jobs, first.audit_events), (1, 2, 2, 1, 3))
            repo.remove_document(corpus, span.revision.document_id)
            partial = inventory_retention(db, candidates).documents[0]
            self.assertEqual((partial.admissions, partial.citations), (1, 1))
            repo.delete(other)
            total = inventory_retention(db, candidates).documents[0]
            self.assertEqual(total.blockers, ("JOB_REFERENCES", "AUDIT_REFERENCES"))
            self.assertEqual(first.admissions, 2)

    def test_no_dependencies_and_empty_candidates_are_only_descriptive(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            doc = document()
            with db.transaction():
                DocumentRepository(db).put_document(doc, lambda: False)
            report = inventory_retention(db, (doc.document_id,))
            self.assertTrue(report.documents[0].no_sql_dependencies)
            self.assertFalse(report.execution_authorized)
            self.assertEqual(inventory_retention(db, ()).documents, ())
            self.assertEqual(db.connection.execute("SELECT count(*) FROM documents").fetchone()[0], 1)

    def test_read_only_sql_no_sensitive_fields_network_process_or_logs(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            repo, corpus, span = self.populate(w, db)
            before = _snapshot_rows(db.connection)
            schema = db.connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            changes = db.connection.total_changes
            reads = []
            def authorize(action, table, column, *_):
                if action == sqlite3.SQLITE_READ:
                    reads.append((table, column))
                    if (table == "document_elements" and column == "text") or (table == "documents" and column == "sha256"):
                        return sqlite3.SQLITE_DENY
                if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                              sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.connection.set_authorizer(authorize)
            try:
                with self.assertNoLogs(level=logging.DEBUG), \
                     patch.object(socket, "socket", side_effect=AssertionError("network")), \
                     patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
                    report = inventory_retention(db, (span.revision.document_id,))
            finally:
                db.connection.set_authorizer(None)
            self.assertTrue(reads)
            self.assertEqual(db.connection.total_changes, changes)
            self.assertEqual(_snapshot_rows(db.connection), before)
            self.assertEqual(db.connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall(), schema)
            self.assertFalse(db.connection.in_transaction)
            for marker in ("PERSONNE_FICTIVE_A", span.revision.sha256, str(w.root)):
                self.assertNotIn(marker, repr(report))
                self.assertNotIn(marker, repr(asdict(report)))

    def test_absent_candidate_produces_no_partial_report_and_closes_transaction(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            _, _, span = self.populate(w, db)
            with self.assertRaises(BackendError) as error:
                inventory_retention(db, (span.revision.document_id, "f" * 32))
            self.assertEqual(error.exception.code, "RETENTION_DOCUMENT_MISSING")
            self.assertFalse(db.connection.in_transaction)

    def test_active_transaction_and_closed_database_are_refused(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            with db.transaction():
                with self.assertRaises(BackendError) as error:
                    inventory_retention(db, ())
                self.assertEqual(error.exception.code, "TRANSACTION_ACTIVE")
                self.assertTrue(db.connection.in_transaction)
            db.close()
            with self.assertRaises(BackendError) as error:
                inventory_retention(db, ())
            self.assertEqual(error.exception.code, "STORAGE_CLOSED")

    def test_old_future_and_corrupt_history_refused_without_migration(self):
        with SyntheticWorkspace() as w:
            db = self.make(w, version=1)
            before = _snapshot_rows(db.connection)
            with self.assertRaises(BackendError):
                inventory_retention(db, ())
            self.assertEqual(_snapshot_rows(db.connection), before)
            db = self.make(w, name="other.db")
            db.connection.execute("PRAGMA user_version=999")
            with self.assertRaises(BackendError):
                inventory_retention(db, ())
            self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 999)
            db.connection.execute("PRAGMA user_version=2")
            db.connection.execute("UPDATE schema_migrations SET checksum='modified'")
            with self.assertRaises(BackendError):
                inventory_retention(db, ())

    def test_sql_error_is_neutral_and_transaction_is_released(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            db.connection.execute("ALTER TABLE citations RENAME TO absent")
            with self.assertRaises(BackendError) as error:
                inventory_retention(db, ())
            self.assertEqual(error.exception.code, "RETENTION_INVALID")
            self.assertFalse(db.connection.in_transaction)


class SQLiteRetentionTests(RetentionCases, unittest.TestCase):
    pass


@unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher facultatif absent")
class CipherRetentionTests(RetentionCases, unittest.TestCase):
    encrypted = True
