# Inventaire de rétention en lecture seule — 2C3A

`q25.persistence.retention.inventory_retention(database, candidates)` produit une
prévisualisation descriptive sur une base synthétique de schéma 2. Il ne migre
pas la base, n'efface rien et n'autorise jamais une suppression.

## Contrats et usage

`candidates` est un tuple explicite d'identifiants techniques de documents, sans
doublon, au plus 256. Le tuple vide produit un inventaire vide après contrôle du
schéma. Un candidat absent provoque un refus global, sans rapport partiel.
Le résultat est trié par identifiant, indépendant de l'ordre des candidats.

```python
from q25.persistence.retention import inventory_retention

inventory = inventory_retention(database, (document_id,))
for item in inventory.documents:
    counts = (item.elements, item.admissions, item.citations,
              item.jobs, item.audit_events)
    blockers = item.blockers
assert inventory.execution_authorized is False
```

Les dataclasses immuables `DocumentDependencies` et `RetentionInventory` exposent
les comptages, l'instant UTC observé, le schéma et les périmètres inconnus. Les
représentations omettent les identifiants de documents. Le rapport mémoire contient
ces identifiants techniques mais aucun texte, chemin, nom de fichier ou SHA256.
Les erreurs sont fermées : `RETENTION_INVALID`, `RETENTION_DOCUMENT_MISSING` ou
les erreurs existantes de version/historique, session fermée et transaction active.
Aucun rapport n'est écrit sur disque ; ne pas journaliser les identifiants inutilement.

## Dépendances et blocages

| Champ | Signification | Motif bloquant si non nul |
| --- | --- | --- |
| `elements` | Segments de texte possédés par la source. | Aucun à lui seul. |
| `admissions` | Admissions dans tous les corpus de cette base. | `ACTIVE_ADMISSIONS` |
| `citations` | Références persistées à cette source, tous corpus. | `CITATION_REFERENCES` |
| `jobs` | Tâches référençant cette source. | `JOB_REFERENCES` |
| `audit_events` | Événements liés à ces tâches. | `AUDIT_REFERENCES` |

Retirer une source d'un corpus laisse visibles les autres admissions. Après le
retrait de toutes les admissions, les jobs terminés et leur audit restent
bloquants : le schéma exige leur lien au document et interdit l'effacement des
événements. L'inventaire ne contourne ni les contraintes ni les triggers.

`no_sql_dependencies=True` signifie seulement qu'aucune admission, citation,
tâche ou événement lié n'a été constaté. Les éléments restent le contenu de la
source. **Ce constat n'est jamais une autorisation de purge.** Aucun exécuteur,
délai de conservation par défaut, expiration ou ordonnanceur n'est livré.

## Portée et cohérence

La lecture utilise `BEGIN` puis rollback de sa propre transaction, après contrôle
des candidats, de l'ouverture et de l'absence de transaction existante. Version,
historique, tables/triggers attendus et clés étrangères sont contrôlés dans le
même snapshot. Aucun texte source ni empreinte source n'est lu ; les checksums
des migrations sont uniquement utilisés pour vérifier l'historique du schéma.
Cette vérification ne remplace pas la validation complète des documents et
citations lors d'une [sauvegarde](PERSISTENT_CORPORA.md).

L'API est interne : les comptages globaux servent à protéger les sources partagées,
sans donner accès aux textes d'autres corpus. Elle ne fournit pas d'authentification
multiutilisateur. Il n'y a ni parcours de dossier ni découverte d'une autre base.
Le résultat ne reste pas courant après la transaction. Toute future exécution
devra revérifier dépendances, politique et périmètre dans sa propre transaction.

Les périmètres `unassessed` sont sauvegardes, originaux, exports, caches, WAL/SHM,
objets mémoire, swap et copies externes. Les index de recherche et résultats
d'analyse sont `unimplemented`, pas implicitement purgés. Aucune garantie
d'effacement physique ou de purge complète ne découle de cet inventaire.
Une lecture peut faire intervenir les mécanismes WAL/SHM du moteur ; elle ne
constitue pas une garantie d'absence d'activité sur ces fichiers.

## Vérification

```bash
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src .venv/bin/python -m unittest tests.unit.test_retention tests.integration.test_retention_pipeline -v
```

19 nouveaux tests : cinq de contrats et sept scénarios exécutés sur chacun des
deux moteurs. Les intégrations interdisent les mutations SQL et la lecture du
texte/empreinte sources, comparent lignes et schéma avant/après et vérifient
l'absence de réseau, processus et journaux. Elles couvrent notamment les sources
partagées, les retraits successifs, les jobs/audits bloquants et les versions
refusées sans migration. Les essais du coffre OS ne sont pas activés.

## Évolution avec la purge manuelle

L'inventaire accepte maintenant les schémas 2 et 3 et expose leur version observée.
Le contrôle d'historique reconnaît exactement ces deux versions, sans les migrer.
Les blocages jobs/audits restent affichés : seul le [service de purge confirmée](MANUAL_PURGE.md)
peut les détacher dans sa propre transaction de schéma 3 après validation.
L'inventaire seul reste sans autorisation d'effacement.
