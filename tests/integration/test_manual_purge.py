from dataclasses import asdict, replace
import importlib.util
import logging
import socket
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from tests.integration.test_corpus_pipeline import CorpusCases
from tests.unit.test_provenance import document
from q25.core.documents import BackendError
from q25.core.provenance import CorpusReference
from q25.persistence.backup import _snapshot_rows, validate_database
from q25.persistence.migrations import default_migrations, migrate
from q25.persistence.purge import ManualPurgeService
from q25.persistence.repository import DocumentRepository, JobResult
from q25.persistence.workspace import SyntheticWorkspace


class PurgeCases:
    encrypted = False
    make = CorpusCases.make
    populate = CorpusCases.populate
    backup = CorpusCases.backup
    restore = CorpusCases.restore
    reopen = CorpusCases.reopen

    def ready(self, w, db):
        repo, corpus, span = self.populate(w, db)
        repo.remove_document(corpus, span.revision.document_id)
        service = ManualPurgeService(db)
        return service, repo, corpus, span

    def execute(self, service, preview, candidates):
        return service.execute(preview.preview_id, candidates, confirmed=True, acknowledge_copies_remain=True)

    def test_purge_multiple_jobs_keeps_history_and_other_source_after_reopen(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, repo, corpus, span = self.ready(w, db)
            doc_id = span.revision.document_id
            jobs = DocumentRepository(db)
            second = jobs.create_job()
            with db.transaction():
                jobs.transition(second, "running")
                jobs.transition(second, "completed", document_id=doc_id)
            unaffected = document("AUTRE_SOURCE_FICTIVE", document_id="f" * 32)
            with db.transaction():
                jobs.put_document(unaffected, lambda: False)
            before = db.connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
            preview = service.prepare((doc_id,))
            self.assertEqual(preview.documents[0].jobs, 2)
            with self.assertNoLogs(level=logging.DEBUG), \
                 patch.object(socket, "socket", side_effect=AssertionError("network")), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
                result = self.execute(service, preview, (doc_id,))
            self.assertEqual((result.documents, result.elements, result.jobs), (1, 1, 2))
            self.assertEqual(db.connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()[:len(before)], before)
            for (job_id,) in db.connection.execute("SELECT id FROM jobs"):
                job = jobs.get_job(job_id)
                self.assertTrue(job.source_purged)
                self.assertIsNone(job.document_id)
                self.assertEqual(job.state, "completed")
            with self.assertRaises(BackendError):
                jobs.get_document(doc_id)
            with self.assertRaises(BackendError):
                repo.resolve(corpus, span)
            self.assertEqual(jobs.get_document(unaffected.document_id), unaffected)
            self.assertEqual(db.connection.execute("SELECT count(*) FROM document_elements WHERE document_id=?", (doc_id,)).fetchone()[0], 0)
            for value in (preview, result, asdict(result)):
                for marker in (span.revision.sha256, "PERSONNE_FICTIVE_A", str(w.root)):
                    self.assertNotIn(marker, repr(value))
            path = db.path
            db.close()
            with self.reopen(w, path) as reopened:
                self.assertEqual(reopened.connection.execute("SELECT count(*) FROM purge_operations").fetchone()[0], 1)
                validate_database(reopened.connection, errors=reopened.error_types)

    def test_source_without_jobs_and_invalid_candidates(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            doc = document("")
            with db.transaction():
                DocumentRepository(db).put_document(doc, lambda: False)
            service = ManualPurgeService(db)
            for values in ((), [], ("bad",), (doc.document_id,) * 2, tuple(f"{i:032x}" for i in range(257))):
                with self.subTest(), self.assertRaises(BackendError):
                    service.prepare(values)
            result = self.execute(service, service.prepare((doc.document_id,)), (doc.document_id,))
            self.assertEqual((result.jobs, result.elements), (0, 0))
            validate_database(db.connection, errors=db.error_types)

    def test_shared_and_batch_blocked_without_partial_deletion(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, repo, corpus, span = self.ready(w, db)
            other = CorpusReference("f" * 32)
            repo.create(other)
            repo.admit(other, span.revision)
            free = document("libre", document_id="e" * 32)
            with db.transaction():
                DocumentRepository(db).put_document(free, lambda: False)
            candidates = (span.revision.document_id, free.document_id)
            before = _snapshot_rows(db.connection)
            preview = service.prepare(candidates)
            with self.assertRaises(BackendError) as error:
                self.execute(service, preview, candidates)
            self.assertEqual(error.exception.code, "PURGE_BLOCKED")
            self.assertEqual(_snapshot_rows(db.connection), before)
            repo.remove_document(other, span.revision.document_id)
            self.assertEqual(self.execute(service, service.prepare(candidates), candidates).documents, 2)

    def test_confirmation_missing_wrong_selection_reused_or_foreign_service(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, _, _, span = self.ready(w, db)
            candidates = (span.revision.document_id,)
            before = _snapshot_rows(db.connection)
            preview = service.prepare(candidates)
            foreign = ManualPurgeService(db)
            with self.assertRaises(BackendError):
                self.execute(foreign, preview, candidates)
            with self.assertRaises(BackendError):
                service.execute(preview.preview_id, candidates, confirmed=True)
            with self.assertRaises(BackendError):
                self.execute(service, preview, candidates)
            preview = service.prepare(candidates)
            with self.assertRaises(BackendError):
                self.execute(service, preview, ("f" * 32,))
            self.assertEqual(_snapshot_rows(db.connection), before)
            preview = service.prepare(candidates)
            self.execute(service, preview, candidates)
            with self.assertRaises(BackendError):
                self.execute(service, preview, candidates)

    def test_changed_admission_and_new_audit_invalidate_preview(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, repo, corpus, span = self.ready(w, db)
            candidates = (span.revision.document_id,)
            preview = service.prepare(candidates)
            repo.admit(corpus, span.revision)
            with self.assertRaises(BackendError) as error:
                self.execute(service, preview, candidates)
            self.assertEqual(error.exception.code, "PURGE_CHANGED")
            repo.remove_document(corpus, span.revision.document_id)
            preview = service.prepare(candidates)
            job_id = db.connection.execute("SELECT id FROM jobs LIMIT 1").fetchone()[0]
            db.connection.execute("INSERT INTO audit_events(job_id,code) VALUES (?,'JOB_COMPLETED')", (job_id,))
            before = _snapshot_rows(db.connection)
            with self.assertRaises(BackendError) as error:
                self.execute(service, preview, candidates)
            self.assertEqual(error.exception.code, "PURGE_CHANGED")
            self.assertEqual(_snapshot_rows(db.connection), before)

    def test_each_mutation_failure_rolls_back_every_row_and_consumes_token(self):
        for action, table in ((sqlite3.SQLITE_UPDATE, "jobs"), (sqlite3.SQLITE_INSERT, "audit_events"),
                              (sqlite3.SQLITE_DELETE, "documents"), (sqlite3.SQLITE_INSERT, "purge_operations")):
            with self.subTest(table=table), SyntheticWorkspace() as w:
                db = self.make(w)
                service, _, _, span = self.ready(w, db)
                candidates = (span.revision.document_id,)
                preview = service.prepare(candidates)
                before = _snapshot_rows(db.connection)
                def deny(code, name, *_):
                    return sqlite3.SQLITE_DENY if code == action and name == table else sqlite3.SQLITE_OK
                db.connection.set_authorizer(deny)
                try:
                    with self.assertRaises(BackendError):
                        self.execute(service, preview, candidates)
                finally:
                    db.connection.set_authorizer(None)
                self.assertEqual(_snapshot_rows(db.connection), before)
                self.assertFalse(db.connection.in_transaction)
                with self.assertRaises(BackendError):
                    self.execute(service, preview, candidates)

    def test_migration_preserves_existing_audit_and_rolls_back_mid_rebuild(self):
        for version in (1, 2):
            with self.subTest(version=version), SyntheticWorkspace() as w:
                db = self.make(w, version=version)
                from q25.app.ingestion import IngestionService
                source = w.write_fixture("fiction.txt", "SOURCE_FICTIVE É\r\n".encode())
                result = IngestionService(db).ingest(source)
                self.assertEqual(result.state, "completed")
                audit = db.connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
                rows = _snapshot_rows(db.connection)
                sql = default_migrations()[2].sql.replace("ALTER TABLE jobs_new RENAME TO jobs;", "INSERT INTO absent VALUES(1);")
                with self.assertRaises(BackendError):
                    migrate(db.connection, (*default_migrations()[:2], replace(default_migrations()[2], sql=sql)), errors=db.error_types)
                self.assertEqual(_snapshot_rows(db.connection), rows)
                migrate(db.connection, errors=db.error_types)
                self.assertEqual(db.connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall(), audit)
                self.assertFalse(DocumentRepository(db).get_job(result.job_id).source_purged)
                validate_database(db.connection, errors=db.error_types)

    def test_backup_before_and_after_purge_and_append_only_guards(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, _, _, span = self.ready(w, db)
            candidates = (span.revision.document_id,)
            old = self.backup(db, w.path("before.db"))
            old_bytes = old.read_bytes()
            self.execute(service, service.prepare(candidates), candidates)
            post = self.backup(db, w.path("after.db"))
            for name, copy, expected in (("restore-old.db", old, 1), ("restore-new.db", post, 0)):
                target = self.restore(w, copy, w.path(name))
                with self.reopen(w, target) as restored:
                    self.assertEqual(restored.connection.execute("SELECT count(*) FROM documents").fetchone()[0], expected)
                    validate_database(restored.connection, errors=restored.error_types)
            self.assertEqual(old.read_bytes(), old_bytes)
            for sql in ("DELETE FROM audit_events", "UPDATE audit_events SET code='JOB_FAILED'",
                        "DELETE FROM purge_operations", "UPDATE purge_operations SET documents=9",
                        "UPDATE jobs SET source_purged=0", "UPDATE jobs SET state='running'"):
                with self.subTest(sql=sql), self.assertRaises(db.error_types):
                    db.connection.execute(sql)

    def test_preparation_has_no_writes_and_attempt_on_wrong_version_consumes_token(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, _, _, span = self.ready(w, db)
            candidates = (span.revision.document_id,)
            before = _snapshot_rows(db.connection)
            preview = service.prepare(candidates)
            self.assertEqual(_snapshot_rows(db.connection), before)
            db.connection.execute("PRAGMA user_version=999")
            with self.assertRaises(BackendError):
                self.execute(service, preview, candidates)
            db.connection.execute("PRAGMA user_version=3")
            with self.assertRaises(BackendError) as error:
                self.execute(service, preview, candidates)
            self.assertEqual(error.exception.code, "PURGE_CONFIRMATION")
            self.assertEqual(_snapshot_rows(db.connection), before)

    def test_close_invalidates_pending_confirmation(self):
        with SyntheticWorkspace() as w:
            db = self.make(w)
            service, _, _, span = self.ready(w, db)
            candidates = (span.revision.document_id,)
            preview = service.prepare(candidates)
            service.close()
            with self.assertRaises(BackendError):
                self.execute(service, preview, candidates)
            self.assertEqual(db.connection.execute("SELECT count(*) FROM documents").fetchone()[0], 1)


class SQLiteManualPurgeTests(PurgeCases, unittest.TestCase):
    pass


@unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher facultatif absent")
class CipherManualPurgeTests(PurgeCases, unittest.TestCase):
    encrypted = True


class PurgedJobContractTests(unittest.TestCase):
    def test_explicit_flag_required_for_detached_completed_job(self):
        self.assertTrue(JobResult("a" * 32, "completed", None, None, True).source_purged)
        for args in (("completed", None, None, False), ("completed", "b" * 32, None, True),
                     ("running", None, None, True), ("completed", None, None, 1)):
            with self.subTest(), self.assertRaises(BackendError):
                JobResult("a" * 32, *args)
