"""Contrats de clés : références techniques et aucune valeur secrète dans les repr."""

from dataclasses import dataclass
import re
from typing import Protocol

from q25.core.documents import BackendError


@dataclass(frozen=True)
class KeyReference:
    identifier: str

    def __post_init__(self):
        if type(self.identifier) is not str or re.fullmatch(r"[0-9a-f]{32}", self.identifier) is None:
            raise BackendError("KEY_REFERENCE_INVALID")


class KeyProvider(Protocol):
    def create(self) -> KeyReference:
        """Créer une clé aléatoire de test, sans retourner sa valeur."""
        ...

    def load(self, reference: KeyReference) -> bytes:
        """Charger uniquement une clé autorisée, en mémoire pour le moteur."""
        ...

    def delete(self, reference: KeyReference) -> None:
        """Supprimer et vérifier l'absence de la clé de test possédée."""
        ...
