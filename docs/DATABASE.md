# Persistance des prototypes synthétiques 2A, 2B1 et 2B2

État au 14 novembre 2026 : deux adaptateurs réservés aux espaces synthétiques
éphémères de tests : SQLite standard en clair (2A) et SQLCipher facultatif (2B1).
Le prototype chiffré est vérifié sous WSL/Linux x86_64 avec Python 3.12 seulement.
Aucun import réel ni stockage de production autorisé.

## Ouverture et cycle de vie

`open_database()` sans capacité synthétique échoue avec `ENCRYPTION_REQUIRED`
avant de créer un fichier, même si l'appelant fournit un chemin. Une capacité
provient uniquement d'un nouveau répertoire temporaire, jamais d'un dossier
existant à adopter. Elle impose des fichiers plats dans sa racine, refuse les
liens symboliques et les sorties de périmètre, et ferme les connexions avant
nettoyage. Les fixtures et nouveaux fichiers sont créés exclusivement, sans
écrasement, avec permissions POSIX demandées `0600` (répertoire temporaire `0700`).

Les tests utilisent `:memory:` ou un fichier temporaire WAL. Chaque connexion
active les clés étrangères, un délai de verrou de 200 ms, les temporaires en
mémoire, `trusted_schema=OFF` et désactive le chargement d'extensions si disponible.
Le choix WAL intervient après vérification/application des migrations ; une base
de version future ou sans historique reconnu est refusée avant cette mutation.

## Schéma version 1

| Table | Contenu |
| --- | --- |
| `schema_migrations` | Version et SHA256 du script appliqué. |
| `documents` | Identifiant technique, SHA256 source, taille en octets, présence du BOM, format et date. |
| `document_elements` | Texte par segment, ordre, offsets caractères et octets, référence au document. |
| `jobs` | Identifiant de tâche, état technique, document éventuel, code d'erreur neutre et dates. |
| `audit_events` | Séquence, tâche, type d'événement fermé et date ; aucun contenu ni chemin. |

Le texte n'est pas dupliqué dans `documents`, les tâches ou l'audit. Aucun nom de
fichier, chemin absolu ou original brut n'est conservé dans ces tables. Les
requêtes de données sont paramétrées. Les clés étrangères et contraintes SQL
contrôlent références, offsets, tailles et combinaisons d'états de tâche.

Les états techniques sont `waiting`, `running`, `completed`, `failed` et
`cancelled`. Ils ne signifient ni document anonymisé ni autorisation d'envoi.
Une tâche et son démarrage sont journalisés avant extraction ; document,
segments, succès et événement final sont ensuite enregistrés atomiquement.
Une erreur ou annulation annule cette transaction et inscrit un état terminal
neutre dans une transaction distincte. Si celle-ci est elle-même bloquée, l'erreur
est remontée ; aucun état terminal persistant fictif n'est annoncé.

L'audit interdit les mises à jour/suppressions via déclencheurs. Cette protection
est logique : elle ne protège pas contre un acteur modifiant directement le
fichier, les schémas ou l'ensemble des données et de leurs empreintes.

## Migrations et intégrité

Le [script initial](../backend/src/q25/persistence/migrations/001_initial.sql)
est un fichier local contrôlé par le projet. Les migrations sont contiguës,
bornées à 256 Kio par script et appliquées dans une transaction explicite.
Une migration historique modifiée ou une version future est refusée ; une
initialisation ou montée de version en échec revient intégralement en arrière.

Le moteur n'utilise pas `executescript`, qui pourrait changer les frontières de
transaction. Les instructions complètes sont exécutées individuellement ; un
authorizer interdit contrôle de transaction, ATTACH/DETACH, PRAGMA et chargement
d'extensions dans le script. Le contenu d'un TXT n'est jamais du SQL à exécuter.

## Provenance TXT

- Lecture binaire bornée à 5 Mio par défaut, UTF-8 strict, BOM UTF-8 facultatif.
- Accents, caractères combinés, LF/CRLF et contenu conservés sans normalisation.
- Segments d'au plus 65 536 points de code, pour borner le nombre d'objets même
  avec beaucoup de lignes vides. Ils peuvent couper une ligne ou un groupe de
  caractères visuels ; leur texte et leurs positions restent exacts.
- Offsets zéro-indexés, début inclus et fin exclue ; `start/end` en points de
  code Python, `byte_start/byte_end` en octets UTF-8 de l'original.
- Le BOM n'appartient pas au texte extrait : le premier offset octet vaut 3
  s'il est présent. Un fichier vide ou contenant uniquement le BOM n'a aucun segment.
- Les contrats et relectures reconstruisent octets/BOM, taille et SHA256 pour
  vérifier la continuité des segments et la cohérence avec la source.

L'extracteur rejette UTF-8 invalide, NUL et signatures manifestes d'autres formats.
Ce profil strict ne constitue pas une détection universelle de format/MIME.

## Sauvegarde et restauration

L'API SQLite `Connection.backup` copie l'instantané cohérent d'une transaction
de lecture, y compris les données validées du WAL, vers un nouveau fichier du
même espace synthétique. Aucun fichier préexistant n'est écrasé ; une transaction
déjà active sur la connexion source interdit la sauvegarde.

Source et copie sont vérifiées : historique/version, tables/déclencheurs attendus,
`integrity_check`, clés étrangères, documents/provenance et états de tâche.
Les lignes copiées sont comparées à l'instantané source. Cela détecte des
incohérences ; cela n'authentifie pas une sauvegarde contre une falsification complète.

La restauration ouvre la source en lecture seule et valide avant de créer la
destination. Un échec retire uniquement le nouveau fichier créé par la copie.
Le délai de copie par défaut est 2 s, avec contrôle de progression et attente
bornée sur verrou ; il ne constitue pas une limite globale des vérifications
sémantiques ni une garantie de performance sur de grandes bases.

## Adaptateur SQLCipher facultatif — 2B1

`q25.persistence.cipher` expose `open_encrypted_database`,
`backup_encrypted_database` et `restore_encrypted_database`. Ces API exigent
un espace synthétique et des clés brutes de 32 octets fournies en mémoire par
le harnais. Elles réutilisent le schéma et l'ingestion TXT précédents.
La sauvegarde classique refuse une base chiffrée ; chaque copie chiffrée exige
une clé explicite. Aucun moteur en clair ne remplace un SQLCipher indisponible.

Les tests couvrent réouverture, mauvaises clés, corruption, migrations, WAL et
copie/restauration avec clés distinctes. Ils ne valident pas un stockage produit.
Voir [l'environnement de développement](DEVELOPMENT.md) et
[les dépendances](DEPENDENCIES.md). La gestion de clés de production reste à réaliser.

## Références de clés — preuve 2B2

`q25.core.keys` définit le contrat `KeyProvider` et les références techniques.
`q25.persistence.keyed` relie ces références aux ouvertures et copies SQLCipher.
Une clé manquante ou un fournisseur défaillant fait échouer l'opération ; aucune
clé n'est régénérée automatiquement pour ouvrir une base existante.

Les références étrangères à la session sont refusées par le prototype de coffre
Windows. Les opérations de la session ne peuvent ni énumérer les autres entrées
ni adopter un secret préexistant. Le helper gère un nettoyage borné de ses entrées
et signale un nettoyage non confirmé. L'essai réel WSL vers Windows a réussi :
création/relecture d'une clé fictive, trajet SQLCipher et suppression confirmée
par l'API Windows. L'exception de lancement a été autorisée pour ce processus,
sans modification permanente de la politique Windows.
Voir [les conditions de l'essai](DEVELOPMENT.md#fournisseur-de-clés-de-test--2b2).

## Limites

Pas de gestion de clés de production, coffre Linux/macOS, FTS5/RAG, rétention de
production, reprise automatique après crash ou orchestration asynchrone. L'annulation est coopérative aux
frontières d'opérations ; elle n'interrompt pas une lecture ou instruction SQL
déjà en cours. Les tests locaux ne valident pas le packaging Windows/macOS natif.

Le prototype reste réservé aux données fictives ; le lot 2B complet reste partiel.
Voir la [politique de sécurité](../SECURITY.md) pour les signalements.


## Corpus et citations en mémoire — 2C1

`q25.core.provenance` accepte les `ExtractedDocument` déjà extraits ou relus par
`DocumentRepository`. Il vérifie la portée du corpus, la révision et les plages
TXT exactes ; aucun champ ni table n'est ajouté au schéma 1. Les tests conservent
les mêmes références après réouverture d'une base synthétique existante.
Les corpus et citations ne sont pas encore persistés. Voir [les contrats et
limites](PROVENANCE.md), notamment l'usage obligatoire du snapshot courant après
un retrait. Migrations et rétention durable restent une tranche distincte.
