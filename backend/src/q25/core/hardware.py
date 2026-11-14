"""Mesures partielles, typées et sans identifiant de machine."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generic, Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Reading(Generic[T]):
    value: T | None
    source: str
    unit: str
    reason: str | None = None

    def __post_init__(self):
        if any(type(x) is not str or not x.strip() for x in (self.source, self.unit)):
            raise ValueError("Une mesure exige source et unité.")
        if self.value is None:
            if type(self.reason) is not str or not self.reason.strip():
                raise ValueError("Une inconnue exige une raison textuelle.")
        elif self.reason is not None:
            raise ValueError("Une inconnue exige une raison ; une valeur connue n'en a pas.")

    @classmethod
    def unknown(cls, reason: str, source: str, unit: str = "bytes"):
        return cls(None, source, unit, reason)


def nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class Capacity:
    total: Reading[int]
    available: Reading[int]

    def __post_init__(self):
        for measurement in (self.total, self.available):
            if measurement.unit != "bytes":
                raise ValueError("Capacité exprimée en octets uniquement.")
            if measurement.value is not None and not nonnegative_integer(measurement.value):
                raise ValueError("Capacité entière positive ou nulle requise.")
        if self.total.value is not None and self.available.value is not None:
            if self.available.value > self.total.value:
                raise ValueError("Disponibilité supérieure au total.")

    @classmethod
    def unknown(cls, reason: str, source: str):
        return cls(Reading.unknown(reason, source), Reading.unknown(reason, source))


@dataclass(frozen=True)
class GPU:
    index: int
    vendor: str
    memory: Capacity

    def __post_init__(self):
        if (not nonnegative_integer(self.index) or type(self.vendor) is not str
                or not self.vendor.strip()):
            raise ValueError("GPU invalide.")


@dataclass(frozen=True)
class HardwareSnapshot:
    os_name: str
    architecture: str
    is_wsl: bool | None
    cpu_logical: Reading[int]
    cpu_physical: Reading[int]
    ram: Capacity
    swap: Capacity
    disk: Capacity
    gpus: tuple[GPU, ...] | None
    gpu_reason: str | None
    unified_memory: Reading[bool]
    measured_at: datetime

    def __post_init__(self):
        if (not isinstance(self.measured_at, datetime) or self.measured_at.tzinfo is None
                or self.measured_at.utcoffset() is None):
            raise ValueError("Horodatage avec fuseau requis.")
        if any(type(x) is not str or not x.strip() for x in (self.os_name, self.architecture)):
            raise ValueError("OS et architecture requis, ou libellé d'inconnu.")
        if self.is_wsl is not None and type(self.is_wsl) is not bool:
            raise ValueError("Indicateur WSL invalide.")
        for measurement in (self.cpu_logical, self.cpu_physical):
            if measurement.unit != "count":
                raise ValueError("Unité CPU invalide.")
            if measurement.value is not None:
                if not nonnegative_integer(measurement.value) or measurement.value == 0:
                    raise ValueError("Nombre de CPU strictement positif requis.")
        if self.cpu_physical.value is not None and self.cpu_logical.value is not None:
            if self.cpu_physical.value > self.cpu_logical.value:
                raise ValueError("CPU physiques supérieurs aux CPU logiques accessibles.")
        if (self.gpus is None) != bool(self.gpu_reason):
            raise ValueError("Une détection GPU inconnue exige une raison.")
        if self.gpus is not None and type(self.gpus) is not tuple:
            raise ValueError("Liste GPU immuable requise.")
        if self.gpus is not None and len({g.index for g in self.gpus}) != len(self.gpus):
            raise ValueError("Index GPU dupliqué.")
        if self.unified_memory.unit != "boolean":
            raise ValueError("Unité de topologie mémoire invalide.")
        if self.unified_memory.value is not None and type(self.unified_memory.value) is not bool:
            raise ValueError("Indicateur de mémoire unifiée invalide.")


class HardwareProbe(Protocol):
    def probe(self, disk_path: Path) -> HardwareSnapshot:
        """Retourner des mesures locales, sans persistance ni réseau."""
        ...
