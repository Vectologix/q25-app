"""Corpus synthétiques persistés ; chaque résolution relit les admissions courantes."""

from q25.core.documents import BackendError, technical_id
from q25.core.provenance import CorpusReference, CorpusSnapshot, RevisionReference, SourceSpan
from q25.persistence.repository import read_document


def _corpus(value):
    if type(value) is not CorpusReference:
        raise BackendError("PROVENANCE_INVALID")


def _id(value):
    if not technical_id(value):
        raise BackendError("PROVENANCE_INVALID")


def read_snapshot(connection, corpus):
    _corpus(corpus)
    if connection.execute("SELECT id FROM corpora WHERE id=?", (corpus.corpus_id,)).fetchone() is None:
        raise BackendError("CORPUS_DOCUMENT_MISSING")
    documents = []
    for document_id, sha in connection.execute(
            "SELECT document_id,sha256 FROM corpus_documents WHERE corpus_id=? ORDER BY document_id",
            (corpus.corpus_id,)):
        document = read_document(connection, document_id)
        if document.sha256 != sha:
            raise BackendError("REVISION_MISMATCH")
        documents.append(document)
    return CorpusSnapshot(corpus, tuple(documents))


def read_span(row):
    return SourceSpan(CorpusReference(row[0]), RevisionReference(row[1], row[2]), *row[3:])


def validate_corpora(connection):
    for (corpus_id,) in connection.execute("SELECT id FROM corpora"):
        snapshot = read_snapshot(connection, CorpusReference(corpus_id))
        for row in connection.execute(
                "SELECT id,corpus_id,document_id,sha256,start,end,byte_start,byte_end FROM citations WHERE corpus_id=?",
                (corpus_id,)):
            _id(row[0])
            snapshot.resolve(read_span(row[1:]))


class CorpusRepository:
    def __init__(self, database):
        self.database = database

    def create(self, corpus):
        _corpus(corpus)
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO corpora VALUES (?)", (corpus.corpus_id,))

    def admit(self, corpus, revision):
        _corpus(corpus)
        if type(revision) is not RevisionReference:
            raise BackendError("PROVENANCE_INVALID")
        with self.database.transaction() as connection:
            read_snapshot(connection, corpus)
            document = read_document(connection, revision.document_id)
            if document.sha256 != revision.sha256:
                raise BackendError("REVISION_MISMATCH")
            connection.execute("INSERT INTO corpus_documents VALUES (?,?,?)",
                               (corpus.corpus_id, revision.document_id, revision.sha256))

    def snapshot(self, corpus):
        with self.database.transaction() as connection:
            return read_snapshot(connection, corpus)

    def resolve(self, corpus, span, *, expected_text=None):
        _corpus(corpus)
        if type(span) is not SourceSpan:
            raise BackendError("PROVENANCE_INVALID")
        if span.corpus != corpus:
            raise BackendError("CORPUS_MISMATCH")
        with self.database.transaction() as connection:
            return read_snapshot(connection, corpus).resolve(span, expected_text=expected_text)

    def save_citation(self, corpus, citation_id, span, *, expected_text=None):
        _corpus(corpus)
        _id(citation_id)
        if type(span) is not SourceSpan:
            raise BackendError("PROVENANCE_INVALID")
        if span.corpus != corpus:
            raise BackendError("CORPUS_MISMATCH")
        with self.database.transaction() as connection:
            result = read_snapshot(connection, corpus).resolve(span, expected_text=expected_text)
            connection.execute("INSERT INTO citations VALUES (?,?,?,?,?,?,?,?)",
                               (citation_id, corpus.corpus_id, span.revision.document_id,
                                span.revision.sha256, span.start, span.end, span.byte_start, span.byte_end))
            return result

    def get_citation(self, corpus, citation_id):
        _corpus(corpus)
        _id(citation_id)
        with self.database.transaction() as connection:
            snapshot = read_snapshot(connection, corpus)
            row = connection.execute(
                "SELECT corpus_id,document_id,sha256,start,end,byte_start,byte_end FROM citations WHERE corpus_id=? AND id=?",
                (corpus.corpus_id, citation_id)).fetchone()
            if row is None:
                raise BackendError("CORPUS_DOCUMENT_MISSING")
            return snapshot.resolve(read_span(row))

    def remove_document(self, corpus, document_id):
        _corpus(corpus)
        _id(document_id)
        with self.database.transaction() as connection:
            read_snapshot(connection, corpus)
            connection.execute("DELETE FROM corpus_documents WHERE corpus_id=? AND document_id=?",
                               (corpus.corpus_id, document_id))

    def delete(self, corpus):
        _corpus(corpus)
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM corpora WHERE id=?", (corpus.corpus_id,))
