from dataclasses import replace
import sqlite3
import time
import unittest

from q25.core.documents import BackendError
from q25.persistence.database import open_database
from q25.persistence.migrations import Migration, default_migrations, migrate
from q25.persistence.workspace import SyntheticWorkspace


class WorkspacePolicyTests(unittest.TestCase):
    def test_normal_open_refuses_missing_encryption_before_creating_file(self):
        with SyntheticWorkspace() as workspace:
            target = workspace.path("normal.db")
            with self.assertRaises(BackendError) as error:
                open_database(target)
            self.assertEqual(error.exception.code, "ENCRYPTION_REQUIRED")
            self.assertFalse(target.exists())

    def test_workspace_rejects_escape_symlink_and_existing_fixture(self):
        with SyntheticWorkspace() as workspace:
            for name in ("../escape", "/absolute", ".", "nested/file"):
                with self.subTest(name=name), self.assertRaises(BackendError):
                    workspace.path(name)
            original = workspace.write_fixture("source.txt", b"fictif")
            with self.assertRaises(BackendError):
                workspace.write_fixture("source.txt", b"different")
            self.assertEqual(original.read_bytes(), b"fictif")
            link = workspace.path("link.txt")
            link.symlink_to(original)
            with self.assertRaises(BackendError):
                workspace.check_path(link)

    def test_workspace_owns_cleanup_and_closes_connections(self):
        with SyntheticWorkspace() as workspace:
            root = workspace.root
            db = open_database(workspace.path("state.db"), synthetic_workspace=workspace)
            workspace.write_fixture("source.txt", b"fictif")
        self.assertFalse(root.exists())
        with self.assertRaises(sqlite3.ProgrammingError):
            db.connection.execute("SELECT 1")

    def test_cleanup_attempts_all_closers_even_after_failure(self):
        calls = []
        with self.assertRaises(BackendError):
            with SyntheticWorkspace() as workspace:
                root = workspace.root
                workspace.register(lambda: calls.append("closed"))
                def fail():
                    raise RuntimeError("PRIVATE")
                workspace.register(fail)
        self.assertEqual(calls, ["closed"])
        self.assertFalse(root.exists())


class MigrationTests(unittest.TestCase):
    def test_initialization_reopening_and_foreign_keys(self):
        with SyntheticWorkspace() as workspace:
            target = workspace.path("state.db")
            for _ in range(2):
                with open_database(target, synthetic_workspace=workspace) as db:
                    self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 3)
                    self.assertEqual(db.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                    self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 3)
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.connection.execute("INSERT INTO document_elements VALUES ('missing',0,0,1,0,1,'x')")

    def test_memory_and_file_journal_modes(self):
        with SyntheticWorkspace() as workspace:
            with open_database(synthetic_workspace=workspace) as memory:
                self.assertEqual(memory.connection.execute("PRAGMA journal_mode").fetchone()[0], "memory")
            with open_database(workspace.path("file.db"), synthetic_workspace=workspace) as disk:
                self.assertEqual(disk.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_future_version_is_refused_without_mutation(self):
        with SyntheticWorkspace() as workspace:
            path = workspace.path("future.db")
            with open_database(path, synthetic_workspace=workspace) as db:
                db.connection.execute("PRAGMA user_version = 999")
            before = path.read_bytes()
            with self.assertRaises(BackendError) as error:
                open_database(path, synthetic_workspace=workspace)
            self.assertEqual(error.exception.code, "MIGRATION_VERSION")
            self.assertEqual(path.read_bytes(), before)

    def test_changed_migration_is_refused(self):
        with SyntheticWorkspace() as workspace:
            with open_database(synthetic_workspace=workspace) as db:
                original = default_migrations()[0]
                with self.assertRaises(BackendError) as error:
                    migrate(db.connection, (replace(original, sql=original.sql + "\n-- changed"), *default_migrations()[1:]))
                self.assertEqual(error.exception.code, "MIGRATION_HISTORY")

    def test_failed_upgrade_rolls_back_all_changes(self):
        with SyntheticWorkspace() as workspace:
            with open_database(synthetic_workspace=workspace) as db:
                migrations = (*default_migrations(), Migration(4, "CREATE TABLE probe(value); INSERT INTO missing VALUES(1);"))
                with self.assertRaises(BackendError):
                    migrate(db.connection, migrations)
                self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertIsNone(db.connection.execute("SELECT name FROM sqlite_master WHERE name='probe'").fetchone())
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 3)

    def test_failed_initial_migration_leaves_no_schema(self):
        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            with self.assertRaises(BackendError):
                migrate(connection, (Migration(1, "CREATE TABLE probe(x); INSERT INTO absent VALUES(1);"),))
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
        finally:
            connection.close()

    def test_transaction_control_in_migration_is_denied(self):
        with SyntheticWorkspace() as workspace:
            with open_database(synthetic_workspace=workspace) as db:
                for sql in ("CREATE TABLE probe(value); COMMIT;", "ATTACH ':memory:' AS outside;", "PRAGMA user_version=9;"):
                    with self.subTest(sql=sql), self.assertRaises(BackendError):
                        migrate(db.connection, (*default_migrations(), Migration(4, sql)))
                    self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_corrupt_or_unrelated_database_is_refused(self):
        with SyntheticWorkspace() as workspace:
            corrupt = workspace.write_fixture("bad.db", b"PRIVATE NOT SQLITE")
            with self.assertRaises(BackendError) as error:
                open_database(corrupt, synthetic_workspace=workspace)
            self.assertNotIn("PRIVATE", str(error.exception))
            target = workspace.path("unrelated.db")
            connection = sqlite3.connect(target)
            connection.execute("CREATE TABLE unrelated(x)")
            connection.close()
            before = target.read_bytes()
            with self.assertRaises(BackendError):
                open_database(target, synthetic_workspace=workspace)
            self.assertEqual(target.read_bytes(), before)

    def test_lock_wait_is_bounded(self):
        with SyntheticWorkspace() as workspace:
            target = workspace.path("state.db")
            with open_database(target, synthetic_workspace=workspace) as first, \
                 open_database(target, synthetic_workspace=workspace) as second:
                with first.transaction():
                    started = time.monotonic()
                    with self.assertRaises(BackendError):
                        with second.transaction():
                            pass
                    self.assertLess(time.monotonic() - started, 2)
