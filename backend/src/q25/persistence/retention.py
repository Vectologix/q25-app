"""Inventaire synthétique en lecture seule ; aucune autorisation de suppression."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from q25.core.documents import BackendError, technical_id
from q25.persistence.migrations import default_migrations, verify_history

MAX_CANDIDATES = 256
UNASSESSED = ("backups", "originals", "exports", "caches", "wal_shm",
              "memory_objects", "swap", "external_copies")
UNIMPLEMENTED = ("search_indexes", "analysis_results")


@dataclass(frozen=True)
class DocumentDependencies:
    document_id: str = field(repr=False)
    elements: int
    admissions: int
    citations: int
    jobs: int
    audit_events: int

    def __post_init__(self):
        counts = (self.elements, self.admissions, self.citations, self.jobs, self.audit_events)
        if not technical_id(self.document_id) or any(type(n) is not int or n < 0 for n in counts):
            raise BackendError("RETENTION_INVALID")

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(code for count, code in (
            (self.admissions, "ACTIVE_ADMISSIONS"), (self.citations, "CITATION_REFERENCES"),
            (self.jobs, "JOB_REFERENCES"), (self.audit_events, "AUDIT_REFERENCES")) if count)

    @property
    def no_sql_dependencies(self) -> bool:
        # Les éléments sont le contenu détenu par la source, pas un tiers référent.
        return not self.blockers


@dataclass(frozen=True)
class RetentionInventory:
    observed_at: datetime
    documents: tuple[DocumentDependencies, ...] = field(repr=False)

    def __post_init__(self):
        if (type(self.observed_at) is not datetime or self.observed_at.tzinfo is not timezone.utc
                or type(self.documents) is not tuple or len(self.documents) > MAX_CANDIDATES
                or any(type(item) is not DocumentDependencies for item in self.documents)):
            raise BackendError("RETENTION_INVALID")
        ids = tuple(item.document_id for item in self.documents)
        if len(set(ids)) != len(ids):
            raise BackendError("RETENTION_INVALID")

    @property
    def schema_version(self) -> int:
        return 2

    @property
    def unassessed(self) -> tuple[str, ...]:
        return UNASSESSED

    @property
    def unimplemented(self) -> tuple[str, ...]:
        return UNIMPLEMENTED

    @property
    def execution_authorized(self) -> bool:
        return False


def inventory_retention(database, candidates: tuple[str, ...]) -> RetentionInventory:
    """Compter uniquement les candidats explicites dans un même snapshot SQL.

    Ne réserve rien et ne migre pas la base. Le résultat reste descriptif après
    la transaction ; un futur exécuteur devra tout revalider indépendamment.
    """
    if (type(candidates) is not tuple or len(candidates) > MAX_CANDIDATES
            or any(not technical_id(value) for value in candidates)
            or len(set(candidates)) != len(candidates)):
        raise BackendError("RETENTION_INVALID")
    database.ensure_open()
    connection = database.connection
    if connection.in_transaction:
        raise BackendError("TRANSACTION_ACTIVE")
    started = False
    try:
        connection.execute("BEGIN")
        started = True
        version = verify_history(connection, default_migrations(), current=True, errors=database.error_types)
        if version != 2:
            raise BackendError("MIGRATION_VERSION")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if (tables != {"schema_migrations", "documents", "document_elements", "jobs", "audit_events",
                       "corpora", "corpus_documents", "citations"}
                or triggers != {"audit_no_update", "audit_no_delete", "job_transition"}
                or connection.execute("PRAGMA foreign_key_check").fetchall()):
            raise BackendError("RETENTION_INVALID")
        result = []
        for document_id in sorted(candidates):
            if connection.execute("SELECT id FROM documents WHERE id=?", (document_id,)).fetchone() is None:
                raise BackendError("RETENTION_DOCUMENT_MISSING")
            # Requêtes fixes : jamais SELECT texte, chemin ou empreinte source.
            counts = tuple(connection.execute(sql, (document_id,)).fetchone()[0] for sql in (
                "SELECT count(*) FROM document_elements WHERE document_id=?",
                "SELECT count(*) FROM corpus_documents WHERE document_id=?",
                "SELECT count(*) FROM citations WHERE document_id=?",
                "SELECT count(*) FROM jobs WHERE document_id=?",
                "SELECT count(*) FROM audit_events WHERE job_id IN (SELECT id FROM jobs WHERE document_id=?)"))
            result.append(DocumentDependencies(document_id, *counts))
        return RetentionInventory(datetime.now(timezone.utc), tuple(result))
    except database.error_types:
        raise BackendError("RETENTION_INVALID") from None
    finally:
        if started and connection.in_transaction:
            connection.rollback()
