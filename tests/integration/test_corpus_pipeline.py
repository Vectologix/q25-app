from contextlib import contextmanager
from dataclasses import replace
import importlib.util
import logging
import socket
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from q25.app.ingestion import IngestionService
from q25.core.documents import BackendError
from q25.core.provenance import CorpusReference, RevisionReference
from q25.persistence.backup import backup_database, restore_database, validate_database, _snapshot_rows
from q25.persistence.corpus import CorpusRepository
from q25.persistence.database import Database, configure, open_database
from q25.persistence.migrations import Migration, default_migrations, migrate, verify_history
from q25.persistence.repository import DocumentRepository
from q25.persistence.workspace import SyntheticWorkspace


class CorpusCases:
    encrypted = False

    def make(self, workspace, name="state.db", version=2):
        path = workspace.path(name)
        workspace.claim_file(path)
        if self.encrypted:
            from q25.persistence.cipher import _load_driver, _connect, EncryptedDatabase
            driver = _load_driver()
            connection = _connect(driver, path, b"f" * 32)
            db = EncryptedDatabase(connection, workspace, path, driver)
        else:
            connection = sqlite3.connect(path, isolation_level=None)
            configure(connection)
            db = Database(connection, workspace, path)
        workspace.register(db.close)
        migrate(connection, default_migrations()[:version], errors=db.error_types)
        return db

    def reopen(self, workspace, path):
        if self.encrypted:
            from q25.persistence.cipher import open_encrypted_database
            return open_encrypted_database(path, key=b"f" * 32, synthetic_workspace=workspace)
        return open_database(path, synthetic_workspace=workspace)

    def backup(self, db, path):
        if self.encrypted:
            from q25.persistence.cipher import backup_encrypted_database
            return backup_encrypted_database(db, path, key=b"f" * 32)
        return backup_database(db, path)

    def restore(self, workspace, source, target):
        if self.encrypted:
            from q25.persistence.cipher import restore_encrypted_database
            return restore_encrypted_database(source, target, workspace=workspace,
                                              source_key=b"f" * 32, destination_key=b"f" * 32)
        return restore_database(source, target, workspace=workspace)

    def populate(self, workspace, db):
        source = workspace.write_fixture("fiction.txt", b"\xef\xbb\xbf" + "PERSONNE_FICTIVE_A Ée\u0301🦊\r\n".encode())
        result = IngestionService(db).ingest(source)
        self.assertEqual(result.state, "completed")
        doc = DocumentRepository(db).get_document(result.document_id)
        repo = CorpusRepository(db)
        corpus = CorpusReference("a" * 32)
        repo.create(corpus)
        repo.admit(corpus, RevisionReference.from_document(doc))
        span = repo.snapshot(corpus).anchor(RevisionReference.from_document(doc), 17, len(doc.text))
        repo.save_citation(corpus, "b" * 32, span, expected_text=doc.text[17:])
        return repo, corpus, span

    def test_reopen_and_backup_restore_include_all_new_tables(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            repo, corpus, span = self.populate(w, db)
            rows = _snapshot_rows(db.connection)
            copy = self.backup(db, w.path("backup.db"))
            restored = self.restore(w, copy, w.path("restored.db"))
            db.close()
            for path in (w.path("state.db"), restored):
                with self.reopen(w, path) as reopened:
                    self.assertEqual(_snapshot_rows(reopened.connection), rows)
                    self.assertEqual(CorpusRepository(reopened).get_citation(corpus, "b" * 32).span, span)
                    validate_database(reopened.connection, errors=reopened.error_types)
            self.assertEqual(len(rows), 8)

    def test_legacy_backup_restores_without_changing_source_then_upgrades(self):
        with SyntheticWorkspace() as w:
            db = self.make(w, version=1)
            source = w.write_fixture("old.txt", "fixture É\r\n".encode())
            result = IngestionService(db).ingest(source)
            copy = self.backup(db, w.path("old-backup.db"))
            before = copy.read_bytes()
            restored = self.restore(w, copy, w.path("upgraded.db"))
            with self.reopen(w, restored) as new:
                self.assertEqual(new.connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(DocumentRepository(new).get_document(result.document_id).text, "fixture É\r\n")
                migrate(new.connection, errors=new.error_types)
                with self.assertRaises(BackendError):
                    verify_history(new.connection, default_migrations()[:1], errors=new.error_types)
            self.assertEqual(copy.read_bytes(), before)
            self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_upgrade_rollback_preserves_legacy_schema_and_rows(self):
        with SyntheticWorkspace() as w:
            db = self.make(w, version=1)
            before = _snapshot_rows(db.connection)
            second = default_migrations()[1]
            with self.assertRaises(BackendError):
                migrate(db.connection, (default_migrations()[0],
                        replace(second, sql=second.sql + "INSERT INTO absent VALUES(1);")), errors=db.error_types)
            self.assertEqual(_snapshot_rows(db.connection), before)
            self.assertIsNone(db.connection.execute("SELECT name FROM sqlite_master WHERE name='corpora'").fetchone())
            migrate(db.connection, errors=db.error_types)
            validate_database(db.connection, errors=db.error_types)

    def test_shared_source_removal_and_corpus_deletion_are_scoped(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            repo, corpus, span = self.populate(w, db)
            other = CorpusReference("c" * 32)
            repo.create(other)
            repo.admit(other, span.revision)
            other_span = replace(span, corpus=other)
            repo.save_citation(other, "d" * 32, other_span)
            stale = repo.snapshot(corpus)
            repo.remove_document(corpus, span.revision.document_id)
            for action in (lambda: repo.resolve(corpus, span), lambda: repo.get_citation(corpus, "b" * 32)):
                with self.assertRaises(BackendError):
                    action()
            self.assertEqual(stale.resolve(span).span, span)
            self.assertEqual(repo.get_citation(other, "d" * 32).span, other_span)
            repo.delete(other)
            self.assertEqual(db.connection.execute("SELECT count(*) FROM citations").fetchone()[0], 0)
            self.assertEqual(db.connection.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
            validate_database(db.connection, errors=db.error_types)

    def test_invalid_scope_revision_quote_and_bounds_leave_no_row(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            repo, corpus, span = self.populate(w, db)
            foreign = CorpusReference("c" * 32)
            for action in (lambda: repo.resolve(foreign, span),
                           lambda: repo.get_citation(foreign, "b" * 32),
                           lambda: repo.admit(corpus, replace(span.revision, sha256="0" * 64)),
                           lambda: repo.admit(corpus, replace(span.revision, document_id="0" * 32)),
                           lambda: repo.save_citation(corpus, "e" * 32, span, expected_text="PERSONNE_FICTIVE_A faux"),
                           lambda: repo.save_citation(corpus, "e" * 32, replace(span, byte_end=span.byte_end+1))):
                with self.subTest(), self.assertRaises(BackendError) as error:
                    action()
                self.assertNotIn("PERSONNE_FICTIVE_A", str(error.exception))
            self.assertEqual(db.connection.execute("SELECT count(*) FROM citations").fetchone()[0], 1)
            columns = {r[1] for r in db.connection.execute("PRAGMA table_info(citations)")}
            self.assertEqual(columns, {"id", "corpus_id", "document_id", "sha256", "start", "end", "byte_start", "byte_end"})

    def test_corrupt_citation_blocks_read_and_backup(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            repo, corpus, span = self.populate(w, db)
            db.connection.execute("UPDATE citations SET byte_end=byte_end+1")
            for action in (lambda: repo.get_citation(corpus, "b" * 32),
                           lambda: validate_database(db.connection, errors=db.error_types),
                           lambda: self.backup(db, w.path("bad-copy.db"))):
                with self.assertRaises(BackendError):
                    action()
            self.assertFalse(w.path("bad-copy.db").exists())

    def test_no_network_process_or_logs_and_transaction_conflict(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            with self.assertNoLogs(level=logging.DEBUG), \
                 patch.object(socket, "socket", side_effect=AssertionError("network")), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
                repo, corpus, span = self.populate(w, db)
                repo.resolve(corpus, span)
            with db.transaction(), self.assertRaises(BackendError) as error:
                repo.resolve(corpus, span)
            self.assertEqual(error.exception.code, "TRANSACTION_ACTIVE")


class SQLiteCorpusTests(CorpusCases, unittest.TestCase):
    pass


@unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher facultatif absent")
class CipherCorpusTests(CorpusCases, unittest.TestCase):
    encrypted = True
