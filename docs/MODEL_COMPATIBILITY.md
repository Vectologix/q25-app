# Catalogue et compatibilité — étape 1

État au 13 novembre 2026. Aucune recherche réseau, installation ou acquisition de
poids dans ce lot. Les noms ci-dessous proviennent du cahier ; leur présence
ne prouve ni leur disponibilité, ni leur licence, ni leur prise en charge.

| Référence du cahier | Rôle prévu | Version/runtime/quantification/licence | État |
| --- | --- | --- | --- |
| EmbeddingGemma 2 | Embeddings | Non vérifiés. | `unverified`, exclu de l'automatisme. |
| Gemma 4 E4B | LLM | Non vérifiés. | `unverified`, exclu de l'automatisme. |
| Gemma 4 12B | LLM | Non vérifiés. | `unverified`, exclu de l'automatisme. |
| Gemma 4 26B A4B | LLM | Non vérifiés. | `unverified`, exclu de l'automatisme. |

Catalogue livré : [model_catalog.toml](../backend/src/q25/hardware/model_catalog.toml).
Le profil `sans_llm` est un repli de planification : aucun moteur de traitement
documentaire ou NER n'est encore livré.

## Règles du schéma 1

- Un profil est lié à son rôle, modèle/version/architecture, quantification,
  backend/version, OS, architecture machine et taille de contexte.
- L'admissibilité automatique exige le statut `verified` et les champs de preuve :
  licence, source HTTPS sans identifiants, SHA256 de forme valide, taille de
  l'artefact, références de preuve et empreintes mémoire/disque.
- `verified` reste une déclaration du catalogue à faire examiner. Le validateur
  ne démontre pas la licence, l'existence du modèle ou l'authenticité des preuves ;
  aucun poids n'est ouvert ou vérifié par son hash à cette étape.
- Les empreintes sont propres au profil et au contexte. `weights_bytes` couvre
  tous les poids, y compris pour un MoE. `ram_bytes`/`vram_bytes` incluent les
  poids dans leur pool et le fonctionnement hors cache KV ; `kv_cache_bytes`
  est ajouté dans le pool d'inférence. `disk_bytes` inclut l'artefact local.
- CPU/unifiée : poids et cache en RAM, VRAM déclarée à zéro. GPU dédié : poids
  et cache en VRAM, overhead en RAM. Aucun offloading hybride validé.
- Les mémoires de plusieurs GPU ne sont pas additionnées. Un profil GPU doit
  tenir sur un GPU individuel et avoir une topologie dédiée confirmée.
- Les deux profils d'une paire embeddings/LLM doivent tenir individuellement et
  simultanément sur disque. Le sélecteur propose concurrence si les budgets
  mémoire combinés le permettent, sinon séquence avec déchargement obligatoire.
- Les profils synthétiques sont exclus par défaut. L'option `allow_synthetic`
  sert aux tests, jamais à prouver la compatibilité d'un modèle réel.

## Prise en charge des sondes

| Cible | Réalisation | Limite |
| --- | --- | --- |
| Linux/WSL | Mémoire `/proc/meminfo`, CPU/affinité/topologie, limites cgroup v2 et disque. | Cgroup inaccessible/v1/hybride : RAM disponible inconnue. Aucune extrapolation Windows hôte. |
| NVIDIA sous POSIX/Linux | Requête mémoire seule via `nvidia-smi` déjà présent, 2 s et 64 Kio maximum. | Aucun pilote installé ; outil absent ou sortie invalide : GPU inconnu. |
| Windows/macOS | OS, architecture, CPU logique standard et disque ; adaptateurs partiels. | RAM/VRAM/topologie natives non validées, valeurs inconnues. Tests simulés seulement. |
| AMD/Apple/mémoire unifiée | Contrats et sélecteur testés sur données synthétiques. | Pas de sonde native validée. La topologie réelle reste inconnue, y compris après une requête NVIDIA. |

Le choix d'un profil ne garantit ni le chargement, ni une vitesse, ni une qualité
de détection. Il faudra recontrôler les ressources avant tout futur chargement.
Les mesures sont ponctuelles, sans réservation de mémoire ni gestionnaire OOM
de runtime à cette étape. Aucune sélection ne certifie une anonymisation.

## Planification par tâche — lot 1B

Le [MemoryPlanner](MEMORY_PLANNER.md) applique le budget de tokens et la préférence
par tâche aux profils de contexte exacts du schéma 1. Il réutilise les budgets,
inconnues et preuves de cette étape ; aucun profil ci-dessus n'est devenu vérifié.
Plusieurs contextes d'un même artefact nécessitent leurs profils/mesures propres ;
aucune mémoire d'un autre contexte ou KV quantifié n'est inventée par interpolation.
Les alternatives à une même fenêtre ne sont pas classées en qualité ou vitesse.
Le runtime, le tokenizer, le recontrôle matériel avant chargement et les OOM réels
restent à valider dans les lots suivants.
