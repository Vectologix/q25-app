"""Sondes locales partielles ; aucun accès réseau ni sauvegarde de mesures."""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
from typing import Callable

from q25.core.hardware import Capacity, GPU, HardwareSnapshot, Reading
from q25.hardware.commands import CommandError, bounded_output

MAX_PROC_BYTES = 2 * 1024 * 1024


def _read_text(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_PROC_BYTES + 1)
    if len(data) > MAX_PROC_BYTES:
        raise ValueError("fichier de sonde trop volumineux")
    return data.decode("utf-8")


def _reading(value: int, source: str, unit: str = "bytes") -> Reading[int]:
    return Reading(value, source, unit)


@dataclass(frozen=True)
class LinuxMeasurements:
    ram: Capacity
    swap: Capacity
    cpu_logical: Reading[int]
    cpu_physical: Reading[int]
    is_wsl: bool | None


class LinuxProbe:
    def __init__(self, *, read_text: Callable[[Path], str] = _read_text,
                 cpu_count: Callable[[], int | None] = os.cpu_count,
                 affinity: Callable[[], set[int]] | None = None):
        self.read_text = read_text
        self.cpu_count = cpu_count
        self.affinity = affinity or (lambda: set(os.sched_getaffinity(0)))

    def _text(self, path: str) -> str | None:
        try:
            value = self.read_text(Path(path))
            if len(value.encode("utf-8")) > MAX_PROC_BYTES:
                return None
            return value
        except (OSError, ValueError, UnicodeError):
            return None

    def _meminfo(self) -> tuple[Capacity, Capacity]:
        raw = self._text("/proc/meminfo")
        source = "/proc/meminfo"
        if raw is None:
            return (Capacity.unknown("mémoire inaccessible", source),) * 2
        values = {}
        wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
        for line in raw.splitlines():
            name, separator, value = line.partition(":")
            if separator and name in wanted:
                match = re.fullmatch(r"\s*(\d+)\s+kB\s*", value)
                if not match or name in values:
                    return (Capacity.unknown("mémoire incohérente", source),) * 2
                values[name] = int(match.group(1)) * 1024
        def capacity(total_name, free_name):
            def item(name):
                return (_reading(values[name], source) if name in values else
                        Reading.unknown("champ mémoire absent", source))
            try:
                return Capacity(item(total_name), item(free_name))
            except ValueError:
                return Capacity.unknown("mémoire incohérente", source)
        return capacity("MemTotal", "MemAvailable"), capacity("SwapTotal", "SwapFree")

    def _cgroup(self, ram: Capacity, logical: int | None) -> tuple[Capacity, int | None]:
        source = "/proc/meminfo + cgroup v2"
        membership = self._text("/proc/self/cgroup")
        def unknown():
            return Capacity(ram.total, Reading.unknown("limites cgroup non vérifiables", source)), logical
        if membership is None:
            return unknown()
        entries = membership.strip().splitlines()
        if len(entries) != 1 or not entries[0].startswith("0::/"):
            return unknown()  # v1/hybride non validé ; ne pas exposer la RAM hôte.
        relative = PurePosixPath(entries[0][3:])
        if ".." in relative.parts or self._text("/sys/fs/cgroup/cgroup.controllers") is None:
            return unknown()
        root = Path("/sys/fs/cgroup")
        current = root.joinpath(*relative.parts[1:])
        limits = []
        headroom = []
        cpu_limits = []
        cpu_unknown = False
        while True:
            maximum = self._text(str(current / "memory.max"))
            if maximum is None or (maximum.strip() != "max" and not maximum.strip().isdigit()):
                return unknown()
            if maximum.strip() != "max":
                usage = self._text(str(current / "memory.current"))
                if usage is None or not usage.strip().isdigit():
                    return unknown()
                limit = int(maximum.strip())
                limits.append(limit)
                headroom.append(max(0, limit - int(usage.strip())))
            quota = self._text(str(current / "cpu.max"))
            if quota is None:
                cpu_unknown = True
            else:
                parts = quota.split()
                if (len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0
                        or (parts[0] != "max" and not parts[0].isdigit())):
                    cpu_unknown = True
                elif parts[0] != "max":
                    cpu_limits.append(max(1, int(parts[0]) // int(parts[1])))
            if current == root:
                break
            current = current.parent
        total, available = ram.total.value, ram.available.value
        if limits:
            total = min([total] + limits) if total is not None else min(limits)
        if available is not None:
            available = min([available] + headroom + ([total] if total is not None else []))
        result = Capacity(
            _reading(total, source) if total is not None else Reading.unknown("total absent", source),
            _reading(available, source) if available is not None else Reading.unknown("disponible absent", source),
        )
        if cpu_unknown:
            logical = None
        elif cpu_limits and logical is not None:
            logical = min([logical] + cpu_limits)
        return result, logical

    def _physical(self, allowed: set[int] | None, logical: int | None) -> Reading[int]:
        source = "/proc/cpuinfo + affinité"
        unknown = Reading.unknown("topologie CPU non vérifiable", source, "count")
        raw = self._text("/proc/cpuinfo")
        if raw is None or allowed is None or logical is None:
            return unknown
        cores = {}
        for block in re.split(r"\n\s*\n", raw):
            info = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
            info = {k.strip(): v.strip() for k, v in info.items()}
            if not all(info.get(key, "").isdigit() for key in ("processor", "physical id", "core id")):
                continue
            cores[int(info["processor"])] = (int(info["physical id"]), int(info["core id"]))
        if not allowed or not allowed.issubset(cores):
            return unknown
        return _reading(min(len({cores[i] for i in allowed}), logical), source, "count")

    def measure(self) -> LinuxMeasurements:
        ram, swap = self._meminfo()
        try:
            logical = self.cpu_count()
        except OSError:
            logical = None
        allowed = None
        try:
            allowed = self.affinity()
            if allowed:
                logical = min(logical, len(allowed)) if logical else len(allowed)
        except (OSError, AttributeError, NotImplementedError):
            pass
        if type(logical) is not int or logical <= 0:
            logical = None
        ram, logical = self._cgroup(ram, logical)
        release = self._text("/proc/sys/kernel/osrelease")
        is_wsl = None if release is None else any(s in release.lower() for s in ("microsoft", "wsl"))
        return LinuxMeasurements(
            ram, swap,
            _reading(logical, "CPU accessibles, affinité et cgroup", "count") if logical else
            Reading.unknown("quota ou nombre CPU inconnu", "sonde Linux", "count"),
            self._physical(allowed, logical), is_wsl,
        )


def _nvidia_command(args: tuple[str, ...]) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        raise CommandError("outil NVIDIA absent")
    return bounded_output((executable, *args[1:]))


class NvidiaProbe:
    def __init__(self, *, runner: Callable[[tuple[str, ...]], str] = _nvidia_command):
        self.runner = runner

    def measure(self) -> tuple[tuple[GPU, ...] | None, str | None]:
        args = ("nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits")
        try:
            raw = self.runner(args)
            if len(raw.encode("utf-8")) > 65536:
                raise ValueError
            rows = raw.strip().splitlines()
            if not rows or len(rows) > 64:
                raise ValueError
            gpus = []
            for index, row in enumerate(rows):
                parts = [part.strip() for part in row.split(",")]
                if len(parts) != 2 or not all(re.fullmatch(r"\d+", x) for x in parts):
                    raise ValueError
                total, available = (int(x) * 1024**2 for x in parts)
                gpus.append(GPU(index, "nvidia", Capacity(
                    _reading(total, "nvidia-smi"), _reading(available, "nvidia-smi"))))
            return tuple(gpus), None
        except (OSError, ValueError, CommandError, subprocess.TimeoutExpired):
            return None, "mémoire GPU non vérifiable ; outil absent, échec ou sortie invalide"


class LocalHardwareProbe:
    def __init__(self, *, system: Callable[[], str] = platform.system,
                 architecture: Callable[[], str] = platform.machine,
                 linux: LinuxProbe | None = None, nvidia: NvidiaProbe | None = None):
        self.system = system
        self.architecture = architecture
        self.linux = linux or LinuxProbe()
        self.nvidia = nvidia or NvidiaProbe()

    def probe(self, disk_path: Path) -> HardwareSnapshot:
        os_name = self.system()
        architecture = self.architecture() or "unknown"
        try:
            usage = shutil.disk_usage(disk_path)
            disk = Capacity(_reading(usage.total, "shutil.disk_usage"),
                            _reading(usage.free, "shutil.disk_usage"))
        except (OSError, ValueError):
            disk = Capacity.unknown("disque inaccessible ou incohérent", "shutil.disk_usage")
        if os_name == "Linux":
            measurements = self.linux.measure()
            ram, swap = measurements.ram, measurements.swap
            logical, physical, is_wsl = (measurements.cpu_logical,
                                         measurements.cpu_physical, measurements.is_wsl)
            gpus, reason = self.nvidia.measure()
        else:
            source = "adaptateur natif non validé"
            ram, swap = Capacity.unknown(source, source), Capacity.unknown(source, source)
            count = os.cpu_count()
            logical = (_reading(count, "os.cpu_count", "count") if count else
                       Reading.unknown(source, source, "count"))
            physical = Reading.unknown(source, source, "count")
            is_wsl = False if os_name in ("Windows", "Darwin") else None
            gpus, reason = None, "sonde GPU native non validée"
        return HardwareSnapshot(
            os_name, architecture, is_wsl, logical, physical, ram, swap, disk,
            gpus, reason, Reading.unknown("topologie mémoire non prouvée", "sonde locale", "boolean"),
            datetime.now(timezone.utc),
        )
