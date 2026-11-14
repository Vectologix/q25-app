# Q25 — Architecture et état du prototype

Q25 prépare localement des documents avant leur utilisation éventuelle dans une
IA externe. La marque vise à soutenir les exigences québécoises applicables,
sans certification automatique d'anonymisation ou de conformité.

## Deux dépôts indépendants

| Dépôt | Rôle | Exécution |
| --- | --- | --- |
| `Vectologix/q25-app` | Application Tauri 2/React/TypeScript et backend Python. | Traitement documentaire, modèles et SQLite sur l'appareil. |
| `Vectologix/q25-web` | Site officiel q25.ai, Astro/TypeScript/Tailwind CSS. | Présentation, documentation et liens vers les Releases de q25-app. |

Le site ne reçoit aucun document et n'héberge pas le backend d'inférence. Ses
compilation/déploiement et les distributions desktop sont indépendants. Les
téléchargements devront pointer vers des versions réellement publiées, avec
empreintes et signatures disponibles ; aucune version desktop n'est livrée ici.

## Organisation de l'application

```text
apps/desktop/       interface Tauri cible, pas encore implémentée
backend/src/q25/    contrats, matériel, catalogue et prototype TXT/SQLite
packages/brand/    logos, icônes et tokens graphiques fournis
docs/              documentation technique publiable
tests/             tests automatisés sur données synthétiques
.github/workflows/ emplacement réservé à l'intégration continue future
```

`packages/brand` constitue la source des assets partagés, dont la copie du site
sera liée à une version et à des empreintes. Aucun lien de fichier entre les
dossiers des deux dépôts n'est nécessaire à leur construction.

## État réalisé

- Contrats matériels, sondes Linux/WSL et NVIDIA partielles, catalogue TOML et
  sélection prudente avec réserves et repli sans LLM.
- Extraction TXT UTF-8/BOM, hash et positions, migrations SQLite atomiques,
  tâches, audit minimal, sauvegarde/restauration vérifiées.
- MemoryPlanner par tâche : contextes et budgets de génération contrôlés sur
  profils synthétiques, sans téléchargement ou chargement de modèle.
- Corpus explicite et citations TXT en mémoire : révision et positions exactes,
  refus des extraits inventés ou hors corpus.
- Corpus et références persistés, schéma 2, copies et restauration 1/2 vérifiées.
- 193 tests ordinaires réussis avec SQLCipher ; essai OS ignoré par défaut.
  La preuve de coffre séparée du 14 novembre reste consignée dans le lot 2B2.
- Contrats de clés et liaison SQLCipher testés sur doubles ; preuve réelle du
  coffre Windows depuis WSL, clé fictive supprimée et politique Windows inchangée.

Le prototype documentaire est réservé aux données synthétiques : SQLite standard
en clair (2A) ou SQLCipher facultatif (2B1), dans des espaces temporaires de tests.
L’ouverture produit reste refusée ; gestion de clés de production et packaging
restent à prouver.
Ni interface, ni détection PII, ni anonymisation, ni chargement de modèle ne sont
encore livrés. Voir [DATABASE.md](DATABASE.md), [politique de sécurité](../SECURITY.md),
[MODEL_COMPATIBILITY.md](MODEL_COMPATIBILITY.md) et [BENCHMARKS.md](BENCHMARKS.md).

## Invariants

Pas de télémétrie, de ressource distante dans l'interface ou de téléchargement
automatique de modèle. Tests synthétiques exclusivement. Les modèles suggèrent
des actions structurées et ancrées ; un moteur déterministe devra appliquer les
retraits et un moteur indépendant vérifier les sorties après réouverture.

EmbeddingGemma 2/RAG SQLite, Gemma 4 12B et 26B A4B, quantifications et profils
RAM/VRAM restent des cibles à vérifier. Les poids et bases locales ne sont pas
versionnés. Les exigences de licence, chiffrement, validation humaine et risque
de réidentification restent applicables. Voir [LEGAL_REFERENCES.md](LEGAL_REFERENCES.md).

## Développement local

Python 3.12+, sans installation pour la suite actuelle :

```bash
PYTHONPATH=backend/src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

API Python : `q25.hardware.probe`, `q25.hardware.catalog`,
`q25.hardware.selection`, `q25.hardware.memory`, `q25.core.provenance`,
`q25.app.ingestion` et `q25.persistence`.
Les tests d'intégration montrent les usages synthétiques. Le packaging et les
applications web/desktop nécessitent encore des étapes distinctes.

Planification du contexte : voir [MEMORY_PLANNER.md](MEMORY_PLANNER.md). La sélection
pure ne réserve pas la mémoire et ne remplace pas un futur chargement mesuré.

Ancrages de sources : voir [PROVENANCE.md](PROVENANCE.md), y compris la portée
logique et la revalidation requise après remplacement du snapshot courant.

Persistance : [corpus et citations](PERSISTENT_CORPORA.md), retrait logique ciblé.
