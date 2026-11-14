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

Résultat du 14 novembre 2026 : avec le moteur, 125 tests réussis et un essai
OS explicitement ignoré ; sans le moteur, 101 réussis et 25 ignorés. Le schéma initial demeure identique. Aucun modèle téléchargé,
aucun serveur démarré, aucune donnée réelle traitée.

## Fournisseur de clés de test — 2B2 en cours

Les tests ordinaires n'utilisent jamais le coffre système. Un double uniquement
présent dans les tests vérifie les références, l'isolation, les erreurs et la
liaison réelle à SQLCipher. Aucun paquet supplémentaire n'est nécessaire.

Le fournisseur `WindowsInteropKeyProvider` est un prototype WSL vers Windows
Credential Manager, réservé à un `SyntheticWorkspace` et aux clés qu'il crée.
Il exige `allow_system_store=True`. L'essai natif dans
[tests/integration/test_os_keyring.py](../tests/integration/test_os_keyring.py)
est désactivé par défaut ; `Q25_RUN_OS_KEYRING_TESTS=1` active cet essai seulement
après accord explicite de création/relecture/suppression des clés fictives.

État constaté : Windows refuse le helper PowerShell non signé depuis le chemin
WSL avec `RemoteSigned`. Aucune clé OS créée ou suppression réelle validée.
Le parseur PowerShell et la compilation C# ont été vérifiés sans appel de coffre.

L'option `allow_unsigned_helper=True` ajoute `-ExecutionPolicy Bypass` uniquement
au processus enfant, et reste désactivée par défaut. Son utilisation demande un
accord distinct ; elle n'a pas été exécutée lors de cette livraison. Dans l'essai
natif, elle correspond à `Q25_ALLOW_UNSIGNED_TEST_HELPER=1`. Aucun réglage permanent
Windows ni paramètre de stratégie de groupe n'est modifié par cette option.

Le lot coffre reste incomplet tant que l'essai natif et son nettoyage ne sont pas
prouvés. Aucune prise en charge Windows native distribuée, Linux/macOS, récupération
après crash ou usage de documents réels ne découle des tests sur doubles.
