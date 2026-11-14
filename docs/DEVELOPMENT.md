# Développement du backend

## Espace de travail local

Les deux dépôts peuvent être regroupés dans un dossier commun sans dépôt Git parent :

```text
q25/
├── q25-app/
├── q25-web/
└── q25.code-workspace
```

Chaque projet conserve son historique et son dépôt distant indépendants.
Le workspace reste local. Depuis l'un des deux dépôts, ouvrir VS Code avec :

```bash
code ../q25.code-workspace
```

## Tests du backend

Prérequis : Python 3.12. Le backend standard n'exige aucune dépendance externe.
Les tests manipulent uniquement des fixtures inventées. Depuis la racine q25-app :

```bash
mkdir -p .private/tmp
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src python3 -m unittest discover -s tests -p 'test_*.py'
```

Sans SQLCipher, les tests du moteur facultatif sont explicitement ignorés ; les
tests de refus en son absence restent exécutés.

## Environnement chiffré facultatif

Profil vérifié : CPython 3.12, WSL Ubuntu 24.04/Linux x86_64. Les autres versions
Python et plateformes ne sont pas validées. Utiliser un environnement `.venv`
local Python 3.12 doté de pip ; aucune installation globale n'est nécessaire.
Si ce prérequis n'est pas disponible, préparer séparément son installation locale.

```bash
TMPDIR="$PWD/.private/tmp" .venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes -r requirements/sqlcipher-linux-py312.txt
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

L'installation est une action réseau volontaire, séparée des tests et traitements.
Le [verrou de dépendance](../requirements/sqlcipher-linux-py312.txt) ne permet
que la wheel Linux CPython 3.12 vérifiée. Le marqueur de l'extra
`cipher-prototype` dans pyproject.toml limite son profil d'installation ; utiliser
le verrou avec hashes pour reproduire cet essai.

Résultat du lot mémoire du 14 novembre 2026 : 156 tests découverts, 155 réussis
avec SQLCipher et l'essai OS explicitement ignoré ; sans SQLCipher, 131 réussis
et 25 ignorés. Les 30 nouveaux tests du MemoryPlanner ne nécessitent aucun paquet.
La preuve de coffre séparée du lot 2B2 reste acquise ; elle n'est pas réexécutée
pour un changement du planificateur pur.
Le schéma initial demeure identique. Aucun modèle téléchargé, aucun serveur
démarré, aucune donnée réelle traitée.

## Fournisseur de clés de test — 2B2

Les tests ordinaires n'utilisent jamais le coffre système. Un double uniquement
présent dans les tests vérifie les références, l'isolation, les erreurs et la
liaison réelle à SQLCipher. Aucun paquet supplémentaire n'est nécessaire.

Le fournisseur `WindowsInteropKeyProvider` est un prototype WSL vers Windows
Credential Manager, réservé à un `SyntheticWorkspace` et aux clés qu'il crée.
Il exige `allow_system_store=True`. L'essai natif dans
[tests/integration/test_os_keyring.py](../tests/integration/test_os_keyring.py)
est désactivé par défaut ; `Q25_RUN_OS_KEYRING_TESTS=1` active cet essai seulement
après accord explicite de création/relecture/suppression des clés fictives.

Preuve du 14 novembre 2026 : une clé fictive de 32 octets a été créée et relue
dans Windows Credential Manager depuis WSL, utilisée pour l'ouverture, la sauvegarde
et la restauration SQLCipher, puis supprimée. Le helper a confirmé l'absence de
cette entrée par l'API Windows après suppression. Aucun secret existant adopté.

L'option `allow_unsigned_helper=True` ajoute `-ExecutionPolicy Bypass` uniquement
au processus enfant, et reste désactivée par défaut. Son utilisation demande un
accord distinct. Cet accord a été reçu et l'option a été utilisée pour l'essai
séparé, car `RemoteSigned` refuse le script non signé via le chemin WSL. Elle
correspond à `Q25_ALLOW_UNSIGNED_TEST_HELPER=1`. La politique a été comparée avant
et après : `LocalMachine=RemoteSigned`, autres scopes `Undefined`, inchangés.

Le lot 2B2 est vérifié pour cette preuve synthétique WSL vers Windows. Le test
reste opt-in et son exception de processus reste désactivée par défaut. Cette
preuve ne valide pas un exécutable Windows distribué, les coffres Linux/macOS,
la récupération après crash, la rotation ou un stockage de production.

## Tests ciblés de mémoire et contexte

```bash
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src .venv/bin/python -m unittest tests.unit.test_memory tests.integration.test_memory_pipeline -v
```

Voir [les contrats et limites du MemoryPlanner](MEMORY_PLANNER.md). Les sondes de
ce test d'intégration sont simulées ; aucun modèle, benchmark ou GPU réel n'est
validé par cette commande.
