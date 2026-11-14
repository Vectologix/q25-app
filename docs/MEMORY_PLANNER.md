# Planification mémoire et contexte — lot 1B

Le module [hardware/memory.py](../backend/src/q25/hardware/memory.py) ajoute
`TokenBudget`, `TaskRequest`, `MemoryPlan` et `MemoryPlanner`. Il réutilise
`recommend` et `BudgetPolicy`, sans charger de modèle ni consulter le matériel
lui-même. Le catalogue et son schéma 1 restent inchangés.

## Entrées et limites

`TokenBudget` compte système, question, historique, sources et génération réservée.
La réserve de génération est obligatoire et positive. Chaque quantité et leur
somme sont des entiers de 0 à 2³¹−1 ; cette borne est un format de représentation,
pas une fenêtre de contexte supportée. Une requête accepte au plus 256 profils.

Les comptes sont fournis par l'appelant. Cette tranche n'installe pas de tokenizer
et ne calcule pas des tokens depuis un texte ou une taille de fichier. Le futur
runtime devra compter avec le tokenizer et le format de prompt exacts, y compris
les tokens spéciaux, de raisonnement et de sortie applicables.

| Tâche | Préférence d'essai en tokens |
| --- | ---: |
| `extraction` | 4 096 |
| `qa` | 8 192 |
| `summary` | 16 384 |
| `comparison` | 32 768 |
| `redaction_context` | 8 192 |

La préférence peut être fournie explicitement ; `max_context_tokens` fixe le
plafond global (32K par défaut). La limite de sélection est le minimum des deux.
Une préférence de 8K ne déclenche donc pas une allocation de 32K, même si un tel
profil est disponible. Un ancien profil à contexte inférieur reste utilisable.

## Décision et plans de chargement

Pour chaque profil, reprendre les preuves et budgets RAM/VRAM/disque existants,
puis vérifier que tous les tokens, génération comprise, tiennent dans son
contexte exact et sous la limite demandée. Aucune interpolation de cache KV ni
conversion automatique Q4/Q8 en consommation de mémoire n’est effectuée.
Les types KV et le placement hybride ne sont pas configurables dans ce schéma ;
les mesures de cache restent propres à chaque profil et à son backend.

Parmi les profils génératifs admissibles, retenir la fenêtre validée la plus
proche par le bas de la préférence plafonnée. `selected_profile_ids` donne les
**alternatives** à cette fenêtre, sans classement de qualité ou de vitesse.
`plans` contient les possibilités de chargement d'un générateur seul ou avec
un encodeur compagnon ; il ne demande pas de charger tous les générateurs ensemble.
Les contraintes de chargement simultané/séquentiel et disque combiné sont conservées.

Les profils embeddings sont contrôlés pour la mémoire de leur rôle ; la limite
future de l'encodeur doit être vérifiée séparément sur ses propres entrées.
Le budget d'un prompt génératif ne valide pas la longueur d'un passage à encoder.

Si aucune configuration complète ne tient :

- `segmentation_required` seulement si un profil admissible sous la limite laisse
  de la place aux sources après système/question/historique/sortie réservée.
- `sans_llm` sinon, avec motifs de refus. Cela ne prouve pas qu'un moteur de
  traitement sans LLM est déjà disponible dans l'application.

Aucun passage n'est tronqué ou segmenté par le planificateur. Aucun plan
d'exécution complet n'est retourné tant que la requête entière ne tient pas.

## Exemple d'appel

En supposant un `snapshot` déjà obtenu par la sonde, les nombres suivants sont
uniquement des comptes fictifs pour illustrer le contrat :

```python
from q25.hardware.catalog import load_catalog
from q25.hardware.memory import MemoryPlanner, TaskRequest, TokenBudget

request = TaskRequest(
    tokens=TokenBudget(
        output_tokens=512,
        system_tokens=256,
        question_tokens=128,
        history_tokens=128,
        source_tokens=2048,
    ),
    task="qa",
    preferred_context_tokens=8192,
    max_context_tokens=32768,
)
plan = MemoryPlanner().plan(snapshot, load_catalog(), request)
```

Le catalogue réel reste non vérifié : cet appel conserve le repli `sans_llm`.
Les fixtures synthétiques sont exclues par défaut ; `allow_synthetic=True` est
réservé au harnais de tests, sans transformer un candidat réel en profil validé.

## Preuves et limites

30 nouveaux tests : contrats, réserves de sortie, limites 4K/8K/16K/32K, baisse de
mémoire disponible, GPU individuels, mémoire unifiée, preuves absentes, limites
manuelles, séquencement et intégration avec sondes simulées sans réseau/processus.
Le résultat garde l'horodatage du snapshot et exige un recontrôle avant chargement.
Il ne réserve aucune mémoire, ne gère pas un OOM de runtime et ne prouve aucune
performance ou compatibilité d'un modèle réel. Les chiffres de calculateurs tiers
ne servent pas de seuil d'admissibilité.
