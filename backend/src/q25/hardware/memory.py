"""Planification pure par tâche sur profils mesurés, sans runtime ni tokenizer.

Aucune interpolation du KV : chaque profil conserve son contexte exact et les
mesures/preuves exigées par le sélecteur existant. Aucun texte de document en entrée.
"""

from dataclasses import dataclass, fields, replace
from datetime import datetime
from types import MappingProxyType

from q25.core.hardware import HardwareSnapshot
from q25.hardware.catalog import ModelProfile
from q25.hardware.selection import (
    BudgetPolicy, Budgets, CandidateDecision, ExecutionPlan, recommend,
)

# Limites de représentation/ressources du prototype, pas capacités des modèles.
MAX_TOKEN_COUNT = 2**31 - 1
MAX_PROFILES = 256
TASK_CONTEXTS = MappingProxyType({
    "extraction": 4096,
    "qa": 8192,
    "summary": 16384,
    "comparison": 32768,
    "redaction_context": 8192,
})


def _token_count(value, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= MAX_TOKEN_COUNT):
        raise ValueError("Quantité de tokens entière et bornée requise.")


@dataclass(frozen=True)
class TokenBudget:
    output_tokens: int
    system_tokens: int = 0
    question_tokens: int = 0
    history_tokens: int = 0
    source_tokens: int = 0

    def __post_init__(self):
        for item in fields(self):
            _token_count(getattr(self, item.name), positive=item.name == "output_tokens")
        _token_count(self.total_tokens, positive=True)

    @property
    def total_tokens(self) -> int:
        return (self.output_tokens + self.system_tokens + self.question_tokens
                + self.history_tokens + self.source_tokens)

    @property
    def non_source_tokens(self) -> int:
        return self.total_tokens - self.source_tokens


@dataclass(frozen=True)
class TaskRequest:
    tokens: TokenBudget
    task: str = "qa"
    preferred_context_tokens: int | None = None
    max_context_tokens: int = 32768

    def __post_init__(self):
        if not isinstance(self.tokens, TokenBudget):
            raise ValueError("Budget de tokens validé requis.")
        if type(self.task) is not str or self.task not in TASK_CONTEXTS:
            raise ValueError("Type de tâche non pris en charge.")
        if self.preferred_context_tokens is not None:
            _token_count(self.preferred_context_tokens, positive=True)
        _token_count(self.max_context_tokens, positive=True)

    @property
    def preferred_context(self) -> int:
        return (TASK_CONTEXTS[self.task] if self.preferred_context_tokens is None
                else self.preferred_context_tokens)

    @property
    def context_limit(self) -> int:
        return min(self.preferred_context, self.max_context_tokens)


@dataclass(frozen=True)
class MemoryPlan:
    task: str
    tokens: TokenBudget
    context_limit: int
    selected_context_tokens: int | None
    selected_profile_ids: tuple[str, ...]
    budgets: Budgets
    decisions: tuple[CandidateDecision, ...]
    plans: tuple[ExecutionPlan, ...]
    fallback: str | None
    notes: tuple[str, ...]
    hardware_measured_at: datetime
    requires_resource_recheck: bool = True


@dataclass(frozen=True)
class MemoryPlanner:
    policy: BudgetPolicy = BudgetPolicy()

    def __post_init__(self):
        if not isinstance(self.policy, BudgetPolicy):
            raise ValueError("Politique matérielle validée requise.")

    def plan(self, snapshot: HardwareSnapshot, profiles: tuple[ModelProfile, ...],
             request: TaskRequest, *, allow_synthetic: bool = False) -> MemoryPlan:
        if not isinstance(snapshot, HardwareSnapshot) or not isinstance(request, TaskRequest):
            raise ValueError("Mesures et requête validées requises.")
        if (type(profiles) is not tuple or len(profiles) > MAX_PROFILES
                or any(not isinstance(profile, ModelProfile) for profile in profiles)):
            raise ValueError("Catalogue de profils borné et immuable requis.")
        if type(allow_synthetic) is not bool:
            raise ValueError("Option de fixtures booléenne requise.")
        # Ordre technique stable, jamais un classement de qualité des modèles.
        profiles = tuple(sorted(profiles, key=lambda profile: profile.id))
        hardware = recommend(snapshot, profiles, self.policy, allow_synthetic=allow_synthetic)
        limit = request.context_limit
        decisions = []
        fitting = []
        segmentable = []
        for profile, decision in zip(profiles, hardware.decisions):
            reasons = list(decision.reasons) if not decision.eligible else []
            context = profile.context_tokens
            if profile.role == "llm":
                if context is None:
                    reasons.append("contexte du profil inconnu")
                else:
                    if context > limit:
                        reasons.append("contexte du profil au-dessus de la préférence ou du plafond")
                    if request.tokens.total_tokens > context:
                        reasons.append("budget total avec génération supérieur au contexte du profil")
                    if (decision.eligible and context <= limit
                            and request.tokens.source_tokens > 0
                            and request.tokens.non_source_tokens < context
                            and request.tokens.total_tokens > context):
                        segmentable.append(profile)
                if not reasons:
                    fitting.append(profile)
            eligible = not reasons
            if eligible:
                reasons.append("matériel et budget de génération admissibles" if profile.role == "llm"
                               else "matériel admissible pour un encodeur compagnon ; budget encodeur séparé")
            decisions.append(replace(decision, eligible=eligible, reasons=tuple(reasons)))

        # Ne pas réserver un contexte supérieur à la demande, même si la machine
        # peut le contenir ; proposer la fenêtre validée la plus proche par le bas.
        context = max((profile.context_tokens for profile in fitting), default=None)
        selected = tuple(profile.id for profile in fitting if profile.context_tokens == context)
        companions = {profile.id for profile, decision in zip(profiles, decisions)
                      if profile.role == "embedding" and decision.eligible}
        allowed = companions | set(selected)
        plans = tuple(sorted((plan for plan in hardware.plans
                              if set(plan.profile_ids) <= allowed and set(plan.profile_ids) & set(selected)),
                             key=lambda plan: (plan.profile_ids, plan.strategy)))
        notes = list(hardware.notes)
        notes.append("recontrôler le matériel avant chargement ; aucune mémoire réservée ici")
        if request.preferred_context > request.max_context_tokens:
            notes.append("préférence limitée par le plafond global")
        if selected:
            fallback = None
            if context < limit:
                notes.append("contexte réduit vers un profil validé ; tous les tokens demandés sont conservés")
            notes.append("modèles et quantifications non classés en qualité ou vitesse")
        elif segmentable:
            fallback = "segmentation_required"
            notes.append("sources à segmenter par le futur moteur ; aucun passage tronqué par le planificateur")
        else:
            fallback = "sans_llm"
            notes.append("aucun profil génératif admissible pour cette requête ; aucune preuve de traitement sans LLM")
        return MemoryPlan(request.task, request.tokens, limit, context, selected, hardware.budgets,
                          tuple(decisions), plans, fallback, tuple(notes), snapshot.measured_at)
