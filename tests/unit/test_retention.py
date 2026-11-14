from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone
import unittest

from q25.core.documents import BackendError
from q25.persistence.retention import DocumentDependencies, RetentionInventory, inventory_retention


class RetentionContractTests(unittest.TestCase):
    def test_counts_and_identity_are_strict(self):
        valid = DocumentDependencies("a" * 32, 1, 0, 0, 0, 0)
        for value in (True, -1, 0.0, "PRIVATE", None):
            with self.subTest(), self.assertRaises(BackendError):
                replace(valid, jobs=value)
        for value in ("PRIVATE", None, "A" * 32):
            with self.subTest(), self.assertRaises(BackendError):
                replace(valid, document_id=value)

    def test_blockers_and_no_dependencies_do_not_authorize_execution(self):
        item = DocumentDependencies("a" * 32, 1, 2, 3, 1, 3)
        self.assertEqual(item.blockers, ("ACTIVE_ADMISSIONS", "CITATION_REFERENCES", "JOB_REFERENCES", "AUDIT_REFERENCES"))
        self.assertFalse(item.no_sql_dependencies)
        item = replace(item, admissions=0, citations=0, jobs=0, audit_events=0)
        report = RetentionInventory(datetime.now(timezone.utc), (item,))
        self.assertTrue(item.no_sql_dependencies)
        self.assertFalse(report.execution_authorized)
        self.assertIn("backups", report.unassessed)
        self.assertIn("analysis_results", report.unimplemented)
        self.assertEqual(report.schema_version, 2)

    def test_inventory_contract_rejects_invalid_containers_and_dates(self):
        now = datetime.now(timezone.utc)
        item = DocumentDependencies("a" * 32, 0, 0, 0, 0, 0)
        for date, items in ((datetime.now(), (item,)), ("PRIVATE", (item,)),
                            (now, [item]), (now, (item, item)), (now, ("PRIVATE",))):
            with self.subTest(), self.assertRaises(BackendError):
                RetentionInventory(date, items)

    def test_invalid_candidates_fail_without_database_access(self):
        for candidates in (None, [], ["a" * 32], ("PRIVATE",), (True,), ("a" * 32,) * 2,
                           tuple(f"{i:032x}" for i in range(257))):
            with self.subTest(), self.assertRaises(BackendError) as error:
                inventory_retention(object(), candidates)
            self.assertEqual(error.exception.code, "RETENTION_INVALID")
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_immutable_minimal_repr_and_serialization(self):
        item = DocumentDependencies("a" * 32, 1, 0, 0, 0, 0)
        report = RetentionInventory(datetime.now(timezone.utc), (item,))
        self.assertNotIn(item.document_id, repr(item))
        self.assertNotIn(item.document_id, repr(report))
        self.assertEqual(set(asdict(item)), {"document_id", "elements", "admissions", "citations", "jobs", "audit_events"})
        with self.assertRaises(FrozenInstanceError):
            report.documents = ()
        with self.assertRaises(FrozenInstanceError):
            item.jobs = 1
