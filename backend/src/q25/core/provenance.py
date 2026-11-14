"""Ancrages TXT en mémoire : corpus explicite, révision et positions exactes.

Aucune entrée/sortie ou gestion persistante de versions. Un snapshot représente
les seules sources admises par son appelant ; il ne constitue pas une ACL.
"""

from dataclasses import dataclass, field
import re

from q25.core.documents import BackendError, ExtractedDocument, technical_id


@dataclass(frozen=True)
class CorpusReference:
    corpus_id: str

    def __post_init__(self):
        if not technical_id(self.corpus_id):
            raise BackendError("PROVENANCE_INVALID")


@dataclass(frozen=True)
class RevisionReference:
    document_id: str
    sha256: str

    def __post_init__(self):
        if (not technical_id(self.document_id) or type(self.sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None):
            raise BackendError("PROVENANCE_INVALID")

    @classmethod
    def from_document(cls, document: ExtractedDocument) -> "RevisionReference":
        if type(document) is not ExtractedDocument:
            raise BackendError("PROVENANCE_INVALID")
        return cls(document.document_id, document.sha256)


def _char_range(start: int, end: int):
    if (type(start) is not int or type(end) is not int
            or start < 0 or end <= start):
        raise BackendError("SOURCE_SPAN_INVALID")


@dataclass(frozen=True)
class SourceSpan:
    corpus: CorpusReference
    revision: RevisionReference
    start: int
    end: int
    byte_start: int
    byte_end: int

    def __post_init__(self):
        if type(self.corpus) is not CorpusReference or type(self.revision) is not RevisionReference:
            raise BackendError("PROVENANCE_INVALID")
        _char_range(self.start, self.end)
        _char_range(self.byte_start, self.byte_end)


@dataclass(frozen=True)
class ResolvedCitation:
    span: SourceSpan
    text: str = field(repr=False)

    def __post_init__(self):
        # Contrôle structurel seulement. La preuve source vient de resolve().
        if type(self.span) is not SourceSpan or type(self.text) is not str:
            raise BackendError("PROVENANCE_INVALID")
        try:
            size = len(self.text.encode("utf-8"))
        except UnicodeError:
            raise BackendError("CITATION_MISMATCH") from None
        if (len(self.text) != self.span.end - self.span.start
                or size != self.span.byte_end - self.span.byte_start):
            raise BackendError("CITATION_MISMATCH")


@dataclass(frozen=True)
class CorpusSnapshot:
    corpus: CorpusReference
    documents: tuple[ExtractedDocument, ...] = field(repr=False)

    def __post_init__(self):
        if (type(self.corpus) is not CorpusReference or type(self.documents) is not tuple
                or any(type(doc) is not ExtractedDocument for doc in self.documents)):
            raise BackendError("PROVENANCE_INVALID")
        # Une seule révision admise par identifiant d'import dans ce snapshot.
        ids = tuple(doc.document_id for doc in self.documents)
        if len(set(ids)) != len(ids):
            raise BackendError("PROVENANCE_INVALID")

    def _document(self, revision: RevisionReference) -> ExtractedDocument:
        if type(revision) is not RevisionReference:
            raise BackendError("PROVENANCE_INVALID")
        for document in self.documents:
            if document.document_id == revision.document_id:
                if document.sha256 != revision.sha256:
                    raise BackendError("REVISION_MISMATCH")
                return document
        raise BackendError("CORPUS_DOCUMENT_MISSING")

    @staticmethod
    def _source(document: ExtractedDocument, start: int, end: int) -> tuple[str, int, int]:
        _char_range(start, end)
        text = document.text
        if end > len(text):
            raise BackendError("SOURCE_SPAN_INVALID")
        excerpt = text[start:end]
        byte_start = (3 if document.has_bom else 0) + len(text[:start].encode("utf-8"))
        return excerpt, byte_start, byte_start + len(excerpt.encode("utf-8"))

    def anchor(self, revision: RevisionReference, start: int, end: int) -> SourceSpan:
        """Construire les positions octets à partir d'une plage de caractères."""
        document = self._document(revision)
        _, byte_start, byte_end = self._source(document, start, end)
        return SourceSpan(self.corpus, revision, start, end, byte_start, byte_end)

    def resolve(self, span: SourceSpan, *, expected_text: str | None = None) -> ResolvedCitation:
        """Relire exclusivement les sources admises ; vérifier l'extrait proposé.

        Un appel ultérieur doit utiliser le snapshot courant après retrait ou
        remplacement d'une source. Les anciens snapshots restent immuables.
        """
        if type(span) is not SourceSpan:
            raise BackendError("PROVENANCE_INVALID")
        # Vérifier la portée avant de rechercher la moindre identité de document.
        if span.corpus != self.corpus:
            raise BackendError("CORPUS_MISMATCH")
        document = self._document(span.revision)
        text, byte_start, byte_end = self._source(document, span.start, span.end)
        if (span.byte_start, span.byte_end) != (byte_start, byte_end):
            raise BackendError("SOURCE_SPAN_INVALID")
        if expected_text is not None and (type(expected_text) is not str or expected_text != text):
            raise BackendError("CITATION_MISMATCH")
        return ResolvedCitation(span, text)
