"""Contrats documentaires du prototype synthétique, sans chemin ni contenu dans les erreurs."""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Protocol

_MESSAGES = {
    "RETENTION_INVALID": "Inventaire de rétention invalide ou incomplet.",
    "RETENTION_DOCUMENT_MISSING": "Candidat absent de la base synthétique.",
    "KEY_REFERENCE_INVALID": "Référence de clé invalide.",
    "KEYRING_CONSENT_REQUIRED": "Essai du coffre système non autorisé explicitement.",
    "KEYRING_UNAVAILABLE": "Coffre système indisponible ou accès refusé.",
    "KEYRING_CLOSED": "Session de coffre fermée.",
    "KEYRING_FAILURE": "Opération de coffre impossible.",
    "KEYRING_CLEANUP_FAILED": "Suppression des clés de test non confirmée.",
    "KEY_NOT_FOUND": "Clé de test absente ou hors de cette session.",
    "KEY_EXISTS": "Identifiant de clé déjà occupé ; aucun écrasement.",
    "CIPHER_UNAVAILABLE": "Moteur SQLCipher validé indisponible.",
    "CIPHER_KEY_REQUIRED": "Clé de chiffrement requise.",
    "CIPHER_KEY_INVALID": "Format de clé de chiffrement refusé.",
    "CIPHER_INVALID": "Base chiffrée ou clé invalide.",
    "ENCRYPTED_COPY_REQUIRED": "Utiliser la sauvegarde chiffrée avec une clé explicite.",
    "ENCRYPTION_REQUIRED": "Ouverture normale refusée : chiffrement non disponible.",
    "WORKSPACE_CLOSED": "Espace synthétique fermé ou non ouvert.",
    "PATH_INVALID": "Chemin refusé hors du périmètre synthétique.",
    "DESTINATION_EXISTS": "Destination déjà occupée ; aucun écrasement.",
    "TXT_READ": "Lecture du TXT impossible.",
    "TXT_TOO_LARGE": "Limite de taille TXT dépassée.",
    "TXT_ENCODING": "Encodage TXT non pris en charge.",
    "TXT_FORMAT": "Contenu hors du profil TXT accepté.",
    "DOCUMENT_INVALID": "Document ou provenance incohérent.",
    "PROVENANCE_INVALID": "Contrat de provenance invalide.",
    "CORPUS_MISMATCH": "Référence hors du corpus fourni.",
    "CORPUS_DOCUMENT_MISSING": "Document absent du corpus fourni.",
    "REVISION_MISMATCH": "Révision différente de la source admise.",
    "SOURCE_SPAN_INVALID": "Plage de source invalide.",
    "CITATION_MISMATCH": "Extrait différent de la plage source.",
    "STORAGE_FAILURE": "Opération de stockage impossible.",
    "STORAGE_CLOSED": "Connexion de stockage fermée.",
    "TRANSACTION_ACTIVE": "Transaction incompatible avec cette opération.",
    "MIGRATION_VERSION": "Version de schéma non prise en charge.",
    "MIGRATION_HISTORY": "Historique des migrations incohérent.",
    "MIGRATION_INVALID": "Migration ou schéma invalide.",
    "BACKUP_INVALID": "Sauvegarde ou base restaurée invalide.",
    "BACKUP_TIMEOUT": "Délai de sauvegarde dépassé.",
    "JOB_CANCELLED": "Tâche annulée.",
    "JOB_FAILED": "Échec de la tâche.",
    "JOB_STATE": "Transition de tâche refusée.",
}


class BackendError(RuntimeError):
    def __init__(self, code: str):
        if code not in _MESSAGES:
            raise ValueError("Code d'erreur inconnu.")
        self.code = code
        super().__init__(f"{code} : {_MESSAGES[code]}")


def technical_id(value: str) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


@dataclass(frozen=True)
class DocumentElement:
    ordinal: int
    start: int
    end: int
    byte_start: int
    byte_end: int
    text: str = field(repr=False)

    def __post_init__(self):
        offsets = (self.ordinal, self.start, self.end, self.byte_start, self.byte_end)
        if any(type(value) is not int or value < 0 for value in offsets):
            raise BackendError("DOCUMENT_INVALID")
        if type(self.text) is not str or self.end - self.start != len(self.text):
            raise BackendError("DOCUMENT_INVALID")
        try:
            byte_length = len(self.text.encode("utf-8"))
        except UnicodeError:
            raise BackendError("DOCUMENT_INVALID") from None
        if self.byte_end - self.byte_start != byte_length:
            raise BackendError("DOCUMENT_INVALID")


@dataclass(frozen=True)
class ExtractedDocument:
    document_id: str
    sha256: str
    byte_size: int
    has_bom: bool
    elements: tuple[DocumentElement, ...] = field(repr=False)

    def __post_init__(self):
        if not technical_id(self.document_id):
            raise BackendError("DOCUMENT_INVALID")
        if type(self.sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise BackendError("DOCUMENT_INVALID")
        if type(self.byte_size) is not int or self.byte_size < 0 or type(self.has_bom) is not bool:
            raise BackendError("DOCUMENT_INVALID")
        if type(self.elements) is not tuple or not all(isinstance(e, DocumentElement) for e in self.elements):
            raise BackendError("DOCUMENT_INVALID")
        char_pos, byte_pos = 0, 3 if self.has_bom else 0
        for ordinal, element in enumerate(self.elements):
            if (element.ordinal, element.start, element.byte_start) != (ordinal, char_pos, byte_pos):
                raise BackendError("DOCUMENT_INVALID")
            char_pos, byte_pos = element.end, element.byte_end
        raw = (b"\xef\xbb\xbf" if self.has_bom else b"") + self.text.encode("utf-8")
        if byte_pos != self.byte_size or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise BackendError("DOCUMENT_INVALID")

    @property
    def text(self) -> str:
        return "".join(element.text for element in self.elements)


class DocumentExtractor(Protocol):
    def extract(self, source: Path) -> ExtractedDocument:
        """Extraire une source autorisée, sans la modifier."""
        ...


@dataclass(frozen=True)
class StorageCapabilities:
    encrypted_at_rest: bool
    synthetic_only: bool
