"""Catalogue déclaratif borné ; aucune URL n'est visitée."""

from dataclasses import dataclass, fields
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

MAX_CATALOG_BYTES = 1024 * 1024


class CatalogError(ValueError):
    """Structure de catalogue invalide, sans recopier le contenu fourni."""


@dataclass(frozen=True)
class ModelProfile:
    id: str
    model: str
    role: str
    status: str = "unverified"
    synthetic: bool = False
    model_version: str | None = None
    model_architecture: str | None = None
    quantization: str | None = None
    backend: str | None = None
    backend_version: str | None = None
    platforms: tuple[str, ...] = ()
    architectures: tuple[str, ...] = ()
    context_tokens: int | None = None
    memory_mode: str = "cpu"
    weights_bytes: int | None = None
    ram_bytes: int | None = None
    vram_bytes: int | None = None
    kv_cache_bytes: int | None = None
    disk_bytes: int | None = None
    artifact_bytes: int | None = None
    license: str | None = None
    source_url: str | None = None
    sha256: str | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("id", "model", "role", "status", "memory_mode"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise CatalogError("Champ textuel requis invalide.")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", self.id):
            raise CatalogError("Identifiant de profil invalide.")
        if self.role not in ("llm", "embedding"):
            raise CatalogError("Rôle non pris en charge.")
        if self.status not in ("unverified", "experimental", "verified"):
            raise CatalogError("Statut non pris en charge.")
        if self.memory_mode not in ("cpu", "dedicated", "unified"):
            raise CatalogError("Mode mémoire non pris en charge.")
        if type(self.synthetic) is not bool:
            raise CatalogError("Indicateur synthétique invalide.")
        for name in ("model_version", "model_architecture", "quantization", "backend", "backend_version",
                     "license", "source_url", "sha256"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value.strip()):
                raise CatalogError("Champ textuel optionnel invalide.")
        for name in ("context_tokens", "weights_bytes", "ram_bytes", "vram_bytes",
                     "kv_cache_bytes", "disk_bytes", "artifact_bytes"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise CatalogError("Quantité entière positive ou nulle requise.")
        for name in ("context_tokens", "weights_bytes", "disk_bytes", "artifact_bytes"):
            if getattr(self, name) == 0:
                raise CatalogError("Quantité strictement positive requise.")
        for sequence in (self.platforms, self.architectures, self.evidence):
            if type(sequence) is not tuple or any(type(x) is not str or not x.strip() for x in sequence):
                raise CatalogError("Liste de textes invalide.")
        if self.source_url is not None:
            try:
                url = urlsplit(self.source_url)
                valid = url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
            except ValueError:
                valid = False
            if not valid:
                raise CatalogError("Source HTTPS sans identifiants requise.")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256):
            raise CatalogError("SHA256 invalide.")

    def missing_proofs(self) -> tuple[str, ...]:
        required = ("model_version", "model_architecture", "quantization", "backend", "backend_version",
                    "platforms", "architectures", "context_tokens", "weights_bytes", "ram_bytes",
                    "vram_bytes", "kv_cache_bytes", "disk_bytes", "artifact_bytes",
                    "license", "source_url", "sha256", "evidence")
        return tuple(name for name in required
                     if getattr(self, name) is None or getattr(self, name) == ())


def parse_catalog(raw: str) -> tuple[ModelProfile, ...]:
    if type(raw) is not str or len(raw.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise CatalogError("Catalogue trop volumineux ou non textuel.")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        raise CatalogError("Syntaxe TOML invalide.") from None
    if set(data) - {"schema_version", "profiles"}:
        raise CatalogError("Champ racine inconnu.")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise CatalogError("Version de schéma non prise en charge.")
    entries = data.get("profiles", [])
    if type(entries) is not list:
        raise CatalogError("Liste de profils requise.")
    allowed = {f.name for f in fields(ModelProfile)}
    profiles = []
    ids = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) - allowed:
            raise CatalogError("Champ de profil inconnu.")
        for name in ("platforms", "architectures", "evidence"):
            if name in entry:
                if type(entry[name]) is not list:
                    raise CatalogError("Liste de textes requise.")
                entry[name] = tuple(entry[name])
        try:
            profile = ModelProfile(**entry)
        except TypeError:
            raise CatalogError("Champs requis absents.") from None
        if profile.id in ids:
            raise CatalogError("Identifiant dupliqué.")
        ids.add(profile.id)
        profiles.append(profile)
    return tuple(profiles)


def load_catalog(path: Path | None = None) -> tuple[ModelProfile, ...]:
    source = path if path is not None else Path(__file__).with_name("model_catalog.toml")
    try:
        with source.open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise CatalogError("Catalogue trop volumineux.")
        return parse_catalog(raw.decode("utf-8"))
    except (OSError, UnicodeError):
        raise CatalogError("Catalogue local inaccessible ou encodage invalide.") from None
