"""Trajet synthétique TXT atomique et annulable aux frontières d'opérations."""

from pathlib import Path
from typing import Callable

from q25.core.documents import BackendError, DocumentExtractor
from q25.extraction.txt import TxtExtractor
from q25.persistence.database import Database
from q25.persistence.repository import DocumentRepository, JobResult


class IngestionService:
    def __init__(self, database: Database, *, extractor: DocumentExtractor | None = None):
        self.database = database
        self.extractor = extractor or TxtExtractor(database.workspace)
        self.repository = DocumentRepository(database)

    def ingest(self, source: Path, *, cancelled: Callable[[], bool] = lambda: False) -> JobResult:
        # Si la création échoue (p. ex. verrou), ne pas inventer une tâche persistée.
        self.database.workspace.check_path(source)
        job_id = self.repository.create_job()
        try:
            if cancelled():
                raise BackendError("JOB_CANCELLED")
            with self.database.transaction():
                self.repository.transition(job_id, "running")
            document = self.extractor.extract(source)
            if cancelled():
                raise BackendError("JOB_CANCELLED")
            with self.database.transaction():
                self.repository.put_document(document, cancelled)
                if cancelled():
                    raise BackendError("JOB_CANCELLED")
                self.repository.transition(job_id, "completed", document_id=document.document_id)
        except Exception as error:
            code = error.code if isinstance(error, BackendError) else "JOB_FAILED"
            state = "cancelled" if code == "JOB_CANCELLED" else "failed"
            with self.database.transaction():
                self.repository.transition(job_id, state, error_code=code)
        return self.repository.get_job(job_id)
