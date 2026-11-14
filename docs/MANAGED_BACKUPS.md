# Copies gérées et restauration après purge — 2C3C

`q25.persistence.managed_backups.ManagedBackupService` est réservé à une base
synthétique ouverte de schéma 3. Il possède un registre **en mémoire** des seules
copies qu'il crée. Il n'adopte aucun fichier ou dossier préexistant.

## API

```python
from q25.persistence.managed_backups import ManagedBackupService

service = ManagedBackupService(database)
receipt = service.create()  # Adaptateur SQLite de tests uniquement.
restored_path = service.restore(receipt.backup_id)
# Après confirmation explicite de la suppression de cette copie :
service.delete(receipt.backup_id, confirmed=True)
```

Avec SQLCipher, `create(key=...)` exige la clé de copie explicite ;
`restore(id, key=..., destination_key=...)` exige les deux clés. Elles ne sont
pas conservées dans le service ou son registre. Les clés sont celles du harnais
fictif, pas un nouveau mécanisme de stockage de clés produit. La copie chiffrée
et sa restauration ne passent jamais en clair.

`BackupReceipt` expose uniquement identifiant aléatoire, date UTC, nombre
d'opérations de purge et état chiffré. Le chemin, l'empreinte du fichier et les
identifiants d'opérations restent dans le registre privé. La Path de destination
est retournée uniquement au contrôleur interne. Aucun texte source ou secret
ne figure dans les représentations ou erreurs. Aucun rapport n'est écrit.

## Contrôle de restauration

Chaque copie passe par les [copies cohérentes existantes](DATABASE.md), puis
une inspection en lecture seule de la copie réelle : historique, intégrité,
références, ancrages et opérations de purge. Le service conserve son identité de
fichier et son empreinte. Fichier altéré/remplacé, lien symbolique, lien physique
supplémentaire ou voisin WAL/SHM/journal : restauration et suppression refusées.

La restauration vérifie les opérations de purge de la copie et de la base
courante sous `BEGIN IMMEDIATE` sur cette dernière. Les historiques doivent être
identiques. Une copie précédant une nouvelle purge est donc refusée **avant de
créer une destination**. La restauration crée un nouveau fichier dans le même
workspace ; elle ne remplace pas la base ouverte. Une copie post-purge permet
une restauration sans réintroduire la source retirée.

Le verrou protège cette comparaison pendant la copie, pas une future ouverture
après de nouvelles purges. Le fichier restauré doit être traité comme une nouvelle
copie sensible ; le service ne le réinscrit pas automatiquement dans son registre.

## Suppression et session

`delete(id, confirmed=True)` supprime seulement le fichier de copie sélectionné,
après vérification de son identité et empreinte. Un refus de confirmation ou une
erreur de suppression conserve le registre ; une nouvelle tentative est possible.
Aucune clé, base courante, source ou autre copie n'est supprimée. La suppression
ne peut pas être utilisée pour nettoyer un fichier altéré à la place de la copie.

Le service accepte au plus 32 copies en mémoire. `close()` invalide le registre
sans effacer les copies comme politique de rétention. La fin du SyntheticWorkspace
nettoie toujours son espace possédé. Une interruption ou un échec de nettoyage
peut laisser un artefact dans cet espace ; aucune reprise persistante n'est livrée.

Les erreurs fermées sont MANAGED_BACKUP_INVALID, MANAGED_BACKUP_CONFIRMATION et
MANAGED_BACKUP_STALE, ainsi que les refus existants de clés, intégrité ou session.
Il n'y a aucun réseau, processus externe, expiration automatique ou journal de
valeurs sensibles.

## Limites

Ce contrôle s'applique seulement aux copies de cette instance et à l'état de sa
base d'origine. Les APIs bas niveau historiques restent capables de restaurer
une copie ancienne ; les futurs parcours produit devront utiliser le service
approprié. Les copies externes ou d'une session précédente ne sont pas révoquées,
et un programme hostile du même compte n'est pas isolé par ce mécanisme.

La suppression du fichier ne démontre pas l'effacement des octets sur SSD,
dans les sauvegardes externes, pages libres, WAL/SHM, swap ou mémoire. Aucune
maintenance VACUUM/checkpoint ou destruction de clé n'est effectuée. La fin de
session de tests n'est pas une politique produit. Aucune purge globale ou
conformité juridique n'est démontrée.

## Vérification

```bash
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src .venv/bin/python -m unittest tests.integration.test_managed_backups -v
```

20 nouveaux tests, dix scénarios sur chaque moteur : copies fraîches et après
purge, refus d'une copie pré-purge sans destination, clés, altération et symlinks,
voisins, sessions étrangères/fermées, suppression ciblée avec reprise après échec,
absence de réseau/processus/journaux et de secret dans le reçu.
