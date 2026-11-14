# Dépendances du prototype backend

État vérifié le 13 novembre 2026. Aucune licence du projet Q25 n'est choisie ici.
Le backend 2A dépend seulement de Python et SQLite standard.

## Prototype SQLCipher facultatif

| Composant | Version observée | Source primaire |
| --- | --- | --- |
| Binding Python sqlcipher3-binary | 0.6.0 | [PyPI](https://pypi.org/project/sqlcipher3-binary/0.6.0/), [sources](https://github.com/coleifer/sqlcipher3) |
| SQLCipher Community | 4.12.0 | [sources](https://github.com/sqlcipher/sqlcipher), [licence Community](https://www.zetetic.net/sqlcipher/community/) |
| SQLite embarqué | 3.51.1 | [SQLite](https://www.sqlite.org/) |
| Fournisseur cryptographique OpenSSL | 3.5.4 | [sources et licence](https://github.com/openssl/openssl/tree/openssl-3.5.4) |

Le [verrou Linux CPython 3.12](../requirements/sqlcipher-linux-py312.txt)
contient l'empreinte SHA256 de la wheel testée. Ces versions décrivent cet essai,
sans garantie qu'elles soient les dernières ni exemptes de vulnérabilités.

La métadonnée PyPI du binding déclare MIT ; le fichier LICENSE réellement inclus
dans cette wheel porte les conditions de Gerhard Häring de type zlib. Cet écart
et les notices des bibliothèques embarquées doivent être clarifiés avant de
redistribuer un binaire. SQLCipher Community exige de conserver ses mentions et
sa licence ; OpenSSL comporte ses propres obligations de redistribution.

Ce lot n'ajoute aucun binaire au dépôt et ne produit aucune distribution Q25.
La provenance, les licences complètes, la SBOM et l'audit de vulnérabilités des
composants natifs restent des prérequis du packaging. Le hash vérifie l'artefact
reçu ; il ne constitue pas un audit de son code ou de sa chaîne de compilation.
