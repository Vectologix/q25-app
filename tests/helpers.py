"""Profils et mesures entièrement synthétiques."""

from dataclasses import replace
from datetime import datetime, timezone

from q25.core.hardware import Capacity, GPU, HardwareSnapshot, Reading
from q25.hardware.catalog import ModelProfile

GIB = 1024**3


def reading(value, unit="bytes"):
    return Reading(value, "fixture synthétique", unit)


def capacity(total, available):
    return Capacity(reading(total), reading(available))


def snapshot(ram=16 * GIB, available=None, gpus=(), unified=False, **changes):
    base = HardwareSnapshot(
        os_name="Linux",
        architecture="x86_64",
        is_wsl=False,
        cpu_logical=reading(8, "count"),
        cpu_physical=reading(4, "count"),
        ram=capacity(ram, ram if available is None else available),
        swap=capacity(8 * GIB, 8 * GIB),
        disk=capacity(100 * GIB, 80 * GIB),
        gpus=gpus,
        gpu_reason=changes.get("gpu_reason"),
        unified_memory=reading(unified, "boolean"),
        measured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return replace(base, **changes)


def gpu(index=0, total=8 * GIB, available=6 * GIB):
    return GPU(index, "nvidia", capacity(total, available))


def profile(identifier="synthetic-llm", role="llm", ram=4 * GIB, **changes):
    base = ModelProfile(
        id=identifier,
        model="Modèle synthétique, ne pas télécharger",
        role=role,
        status="verified",
        synthetic=True,
        model_version="fixture-1",
        model_architecture="synthetic-dense",
        quantization="fixture-q4",
        backend="fixture-runtime",
        backend_version="fixture-1",
        platforms=("Linux",),
        architectures=("x86_64",),
        context_tokens=2048,
        memory_mode="cpu",
        weights_bytes=ram,
        ram_bytes=ram,
        vram_bytes=0,
        kv_cache_bytes=0,
        disk_bytes=ram,
        artifact_bytes=ram,
        license="licence-fictive-tests",
        source_url="https://example.invalid/synthetic",
        sha256="a" * 64,
        evidence=("fixture synthétique, aucune preuve sur un modèle réel",),
    )
    return replace(base, **changes)
