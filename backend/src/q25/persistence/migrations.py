"""Migrations locales atomiques ; jamais executescript ni SQL provenant d'un document."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3

from q25.core.documents import BackendError

MAX_MIGRATION_BYTES = 256 * 1024


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str

    def __post_init__(self):
        if (type(self.version) is not int or self.version <= 0 or type(self.sql) is not str
                or not self.sql.strip() or len(self.sql.encode()) > MAX_MIGRATION_BYTES):
            raise BackendError("MIGRATION_INVALID")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def default_migrations() -> tuple[Migration, ...]:
    path = Path(__file__).with_name("migrations") / "001_initial.sql"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MIGRATION_BYTES + 1)
        return (Migration(1, raw.decode("utf-8")),)
    except (OSError, UnicodeError):
        raise BackendError("MIGRATION_INVALID") from None


def _validate_list(migrations):
    if (type(migrations) is not tuple or not migrations
            or any(not isinstance(m, Migration) for m in migrations)
            or tuple(m.version for m in migrations) != tuple(range(1, len(migrations) + 1))):
        raise BackendError("MIGRATION_INVALID")


def verify_history(connection: sqlite3.Connection, migrations: tuple[Migration, ...], *,
                   current=False, errors=(sqlite3.Error,)) -> int:
    _validate_list(migrations)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > len(migrations) or version < 0 or (current and version != len(migrations)):
            raise BackendError("MIGRATION_VERSION")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "schema_migrations" not in tables:
            if version != 0 or tables:
                raise BackendError("MIGRATION_HISTORY")
            return 0
        history = connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
        expected = [(m.version, m.checksum) for m in migrations[:version]]
        if history != expected:
            raise BackendError("MIGRATION_HISTORY")
        return version
    except errors:
        raise BackendError("MIGRATION_INVALID") from None


def _statements(script: str):
    buffer = []
    for char in script:
        buffer.append(char)
        if char == ";" and sqlite3.complete_statement("".join(buffer)):
            yield "".join(buffer)
            buffer.clear()
    tail = "".join(buffer)
    if any(line.strip() and not line.lstrip().startswith("--") for line in tail.splitlines()):
        raise BackendError("MIGRATION_INVALID")


def _migration_authorizer(action, first, second, *_):
    denied = (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
              sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA)
    if action in denied or (action == sqlite3.SQLITE_FUNCTION and second == "load_extension"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def migrate(connection: sqlite3.Connection, migrations: tuple[Migration, ...] | None = None,
            *, errors=(sqlite3.Error,)):
    migrations = default_migrations() if migrations is None else migrations
    if connection.in_transaction:
        raise BackendError("TRANSACTION_ACTIVE")
    version = verify_history(connection, migrations, errors=errors)
    if version == len(migrations):
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)")
        for migration in migrations[version:]:
            connection.set_authorizer(_migration_authorizer)
            try:
                for statement in _statements(migration.sql):
                    connection.execute(statement)
            finally:
                connection.set_authorizer(None)
            connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (migration.version, migration.checksum))
            connection.execute(f"PRAGMA user_version = {migration.version}")
        connection.commit()
    except BaseException as error:
        connection.set_authorizer(None)
        if connection.in_transaction:
            connection.rollback()
        if isinstance(error, errors):
            raise BackendError("MIGRATION_INVALID") from None
        raise
