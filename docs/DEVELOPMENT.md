# Développement du backend

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

Résultat vérifié : 105 tests réussis avec le moteur ; sans le moteur, 83 réussis
et 22 ignorés. Le schéma initial demeure identique. Aucun modèle téléchargé,
aucun serveur démarré, aucune donnée réelle traitée.
