# Purge manuelle confirmée — prototype 2C3B

La purge retire des sources explicitement choisies de la base synthétique courante.
Elle est disponible comme API backend, sans interface Tauri. Aucun document réel,
fichier utilisateur, sauvegarde ou clé système n'est supprimé par cette API.

## Prévisualiser puis confirmer

```python
from q25.persistence.purge import ManualPurgeService

service = ManualPurgeService(database)
candidates = (document_id,)
preview = service.prepare(candidates)
# Le contrôleur présente la sélection, les dépendances et les limites à l'utilisateur.
# Seulement après confirmation explicite de cette prévisualisation :
result = service.execute(
    preview.preview_id, candidates,
    confirmed=True, acknowledge_copies_remain=True,
)
```

Les candidats sont un tuple non vide, sans doublon, au plus 256 identifiants
techniques. La sélection est triée ; une sélection différente est refusée.
La préparation vérifie la base et restitue des comptages sans écrire de données.
Le service accepte au plus 32 préparations en attente ; `close()` les invalide.
Une confirmation n'est ni persistée ni transférable entre services ou connexions.
Chaque tentative avec un identifiant connu le consomme, y compris un refus ou
une erreur. Il faut alors préparer une nouvelle prévisualisation.

`PurgePreview` et `PurgeResult` sont des objets immuables produits par le service.
Construire manuellement un objet de transfert ne crée pas de préparation valide.
Le rapport ne contient ni texte, ni nom, chemin ou SHA256 de source. Le service
conserve en mémoire une signature de l'état technique, absente des représentations.
La confirmation API est destinée à un contrôleur local de confiance ; elle ne
prouve pas l'identité d'une personne et ne protège pas d'un programme du même compte.

## Blocages et transaction

Toute admission ou citation encore active bloque la sélection entière. Le retrait
explicite des admissions passe par [CorpusRepository](PERSISTENT_CORPORA.md) ;
la purge ne retire jamais automatiquement une source des autres corpus.

L'exécution ouvre `BEGIN IMMEDIATE`, valide la base puis recontrôle révisions et
dépendances. Un changement pertinent depuis la prévisualisation invalide celle-ci.
Tous les candidats sont contrôlés avant la première mutation. Les jobs concernés
doivent être completed avec une source encore attachée.

La transaction détache les jobs, ajoute leurs événements DOCUMENT_PURGED, supprime
les documents/éléments, écrit le bilan puis revalide la base avant commit. Un
échec annule l'ensemble, y compris les événements, indicateurs et bilan. Une
confirmation ne permet pas une seconde purge. Aucun réseau, processus ou journal
de valeurs sources n'est déclenché.

Les erreurs fermées incluent PURGE_CONFIRMATION, PURGE_BLOCKED et PURGE_CHANGED,
ainsi que les refus existants de candidats, stockage, transaction ou intégrité.

## Schéma 3 et audit

Le script 003 est ajouté sans modifier 001/002. Il reconstruit jobs et audit_events
dans une transaction avec clés étrangères actives : valeurs historiques,
séquences et dates sont copiées à l'identique, puis les triggers sont rétablis.
Un rollback pendant cette reconstruction conserve intégralement l'ancien schéma.

Un job reste completed après retrait, avec document_id NULL et source_purged=1.
Les autres états gardent source_purged=0. `JobResult` conserve ses quatre arguments
historiques et ajoute `source_purged=False`. Les événements historiques restent
intacts ; un événement DOCUMENT_PURGED est ajouté par job détaché. Les triggers
interdisent toujours l'altération de l'audit et les retours d'état des jobs.

La table purge_operations conserve seulement identifiant d'opération, date UTC
et nombres de sources/éléments/jobs. Elle ne recopie ni identifiant de document,
ni empreinte ou texte. Des triggers la protègent contre UPDATE/DELETE. Le bilan
couvre aussi une source fictive sans job. Les validateurs vérifient la cohérence
entre jobs marqués, événements de purge et total de jobs des bilans.

Les copies/restaurations reconnaissent les versions 1/2/3 et incluent toutes les
tables. Une copie ancienne est restaurée vers un nouveau fichier puis migrée à
l'ouverture. Son fichier source reste intact. L'inventaire en lecture seule
accepte désormais 2/3 et expose la version réellement observée ; ses blocages jobs/
audit restent descriptifs et ne deviennent pas une autorisation de suppression.

## Limites

La suppression est **logique dans la base courante**. Le texte peut rester dans
les pages libres, WAL, objets mémoire, originaux ou anciennes sauvegardes. Restaurer
une sauvegarde antérieure peut réintroduire la source ; les tests le vérifient.
Aucune sauvegarde pré-purge n'est créée automatiquement.

Il n'y a ni VACUUM/checkpoint de maintenance, ni destruction de clé, effacement
SSD, nettoyage de copies externes, délai de conservation ou expiration automatique.
La conservation de l'audit technique est un choix de prototype, pas une politique
juridique de conservation validée. L'usage réel et la purge complète restent à prouver.

## Tests

```bash
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src .venv/bin/python -m unittest tests.integration.test_manual_purge -v
```

21 nouveaux tests : dix scénarios sur chaque moteur et un test de contrat job.
Ils couvrent confirmation/session/sélection, source partagée, état changé, erreurs
à chacune des quatre phases d'écriture, migration 1/2→3 et échec pendant la
reconstruction, historique conservé, sauvegarde avant/après et protections SQL.
Les essais du coffre OS restent désactivés.
