"""Profil TXT UTF-8 strict du harnais synthétique."""

import hashlib
from pathlib import Path
from uuid import uuid4

from q25.core.documents import BackendError, DocumentElement, ExtractedDocument
from q25.persistence.workspace import SyntheticWorkspace

_BOM = b"\xef\xbb\xbf"
_FOREIGN_SIGNATURES = (b"%PDF-", b"PK\x03\x04", b"PK\x05\x06", b"\x89PNG", b"\xff\xd8\xff",
                       b"GIF87a", b"GIF89a", b"MZ", b"\x7fELF", b"\xd0\xcf\x11\xe0")


class TxtExtractor:
    def __init__(self, workspace: SyntheticWorkspace, *, max_bytes: int = 5 * 1024**2):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("Limite TXT strictement positive requise.")
        self.workspace = workspace
        self.max_bytes = max_bytes

    def extract(self, source: Path) -> ExtractedDocument:
        source = self.workspace.check_path(source)
        try:
            with source.open("rb") as stream:
                raw = stream.read(self.max_bytes + 1)
        except OSError:
            raise BackendError("TXT_READ") from None
        if len(raw) > self.max_bytes:
            raise BackendError("TXT_TOO_LARGE")
        has_bom = raw.startswith(_BOM)
        payload = raw[3:] if has_bom else raw
        if b"\x00" in payload or payload.startswith(_FOREIGN_SIGNATURES):
            raise BackendError("TXT_FORMAT")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeError:
            raise BackendError("TXT_ENCODING") from None
        elements = []
        char_pos, byte_pos = 0, 3 if has_bom else 0
        # Segments bornés en nombre : une multitude de lignes vides ne crée pas
        # des millions d'objets. Aucune normalisation ni perte de frontière source.
        for ordinal, start in enumerate(range(0, len(text), 65536)):
            part = text[start:start + 65536]
            byte_end = byte_pos + len(part.encode("utf-8"))
            elements.append(DocumentElement(ordinal, char_pos, char_pos + len(part), byte_pos, byte_end, part))
            char_pos += len(part)
            byte_pos = byte_end
        return ExtractedDocument(uuid4().hex, hashlib.sha256(raw).hexdigest(), len(raw), has_bom, tuple(elements))
