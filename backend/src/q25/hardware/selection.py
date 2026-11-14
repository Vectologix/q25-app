"""Budgets explicites ; ne charge et ne télécharge aucun modèle."""

from dataclasses import dataclass, fields

from q25.core.hardware import HardwareSnapshot, nonnegative_integer
from q25.hardware.catalog import ModelProfile

GIB = 1024**3


@dataclass(frozen=True)
class BudgetPolicy:
    # Réserves additionnelles sur le disponible ; hypothèses prudentes, pas des benchmarks.
    system_headroom_bytes: int = 2 * GIB
    application_bytes: int = GIB
    margin_bytes: int = GIB
    gpu_headroom_bytes: int = GIB // 2
    disk_headroom_bytes: int = GIB

    def __post_init__(self):
        if any(not nonnegative_integer(getattr(self, f.name)) for f in fields(self)):
            raise ValueError("Réserves entières positives ou nulles requises.")


@dataclass(frozen=True)
class Budgets:
    ram_bytes: int | None
    disk_bytes: int | None
    gpu_bytes: tuple[tuple[int, int | None], ...]


@dataclass(frozen=True)
class CandidateDecision:
    profile_id: str
    eligible: bool
    reasons: tuple[str, ...]
    ram_required: int = 0
    vram_required: int = 0
    gpu_index: int | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    profile_ids: tuple[str, ...]
    strategy: str
    reason: str


@dataclass(frozen=True)
class Recommendation:
    budgets: Budgets
    decisions: tuple[CandidateDecision, ...]
    plans: tuple[ExecutionPlan, ...]
    notes: tuple[str, ...]
    fallback: str = "sans_llm"


def _remaining(available: int | None, reserve: int) -> int | None:
    return None if available is None else max(0, available - reserve)


def recommend(snapshot: HardwareSnapshot, profiles: tuple[ModelProfile, ...],
              policy: BudgetPolicy = BudgetPolicy(), *, allow_synthetic: bool = False) -> Recommendation:
    """Retourner les options admissibles, sans classer leur vitesse ou qualité.

    allow_synthetic est réservé aux tests ; le catalogue livré n'en contient pas.
    Les preuves du catalogue restent déclaratives et nécessitent une revue humaine.
    """
    if len({p.id for p in profiles}) != len(profiles):
        raise ValueError("Identifiant de profil dupliqué.")
    budgets = Budgets(
        _remaining(snapshot.ram.available.value,
                   policy.system_headroom_bytes + policy.application_bytes + policy.margin_bytes),
        _remaining(snapshot.disk.available.value, policy.disk_headroom_bytes),
        tuple((g.index, _remaining(g.memory.available.value, policy.gpu_headroom_bytes))
              for g in snapshot.gpus or ()),
    )
    gpu_budgets = dict(budgets.gpu_bytes)
    decisions = []
    for profile in profiles:
        reasons = []
        if profile.synthetic and not allow_synthetic:
            reasons.append("profil synthétique exclu hors tests")
        if profile.status != "verified":
            reasons.append("compatibilité non prouvée")
        missing = profile.missing_proofs()
        if missing:
            reasons.append("preuves ou mesures manquantes : " + ", ".join(missing))
        if snapshot.os_name not in profile.platforms:
            reasons.append("plateforme non prouvée")
        if snapshot.architecture not in profile.architectures:
            reasons.append("architecture machine non prouvée")
        if reasons:
            decisions.append(CandidateDecision(profile.id, False, tuple(reasons)))
            continue
        # Tous les champs numériques suivants ont été contrôlés par missing_proofs.
        ram = profile.ram_bytes
        vram = profile.vram_bytes
        cache = profile.kv_cache_bytes
        gpu_index = None
        if profile.disk_bytes < profile.artifact_bytes:
            reasons.append("empreinte disque inférieure à l'artefact")
        if profile.memory_mode in ("cpu", "unified"):
            if vram != 0 or ram < profile.weights_bytes:
                reasons.append("empreinte RAM/poids incohérente")
            ram += cache
            if profile.memory_mode == "unified" and snapshot.unified_memory.value is not True:
                reasons.append("mémoire unifiée non confirmée")
        else:
            if vram < profile.weights_bytes:
                reasons.append("empreinte VRAM/poids incohérente ; offloading non validé")
            vram += cache
            if snapshot.unified_memory.value is not False:
                reasons.append("mémoire dédiée non confirmée")
            fits = sorted(index for index, amount in gpu_budgets.items()
                          if amount is not None and vram <= amount)
            if fits:
                gpu_index = fits[0]
            else:
                reasons.append("VRAM libre insuffisante ou inconnue sur chaque GPU")
        if budgets.ram_bytes is None or ram > budgets.ram_bytes:
            reasons.append("RAM disponible insuffisante ou inconnue après réserves")
        if budgets.disk_bytes is None or profile.disk_bytes > budgets.disk_bytes:
            reasons.append("disque disponible insuffisant ou inconnu après réserve")
        decisions.append(CandidateDecision(
            profile.id, not reasons, tuple(reasons) or ("preuves déclarées et budgets satisfaits",),
            ram, vram, gpu_index,
        ))
    eligible = {d.profile_id: d for d in decisions if d.eligible}
    plans = [ExecutionPlan((identifier,), "single", "empreinte individuelle dans les budgets")
             for identifier in eligible]
    embeddings = [p for p in profiles if p.id in eligible and p.role == "embedding"]
    llms = [p for p in profiles if p.id in eligible and p.role == "llm"]
    notes = []
    for embedding in embeddings:
        for llm in llms:
            if embedding.disk_bytes + llm.disk_bytes > budgets.disk_bytes:
                notes.append(f"{embedding.id} + {llm.id} : disque combiné insuffisant")
                continue
            first, second = eligible[embedding.id], eligible[llm.id]
            simultaneous = first.ram_required + second.ram_required <= budgets.ram_bytes
            for index, amount in gpu_budgets.items():
                needed = sum(d.vram_required for d in (first, second) if d.gpu_index == index)
                if needed and (amount is None or needed > amount):
                    simultaneous = False
            plans.append(ExecutionPlan(
                (embedding.id, llm.id), "concurrent" if simultaneous else "sequential",
                "budgets simultanés satisfaits" if simultaneous
                else "décharger un modèle avant l'autre ; empreintes individuelles admissibles",
            ))
    return Recommendation(budgets, tuple(decisions), tuple(plans), tuple(notes))
