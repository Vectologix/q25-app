# Corpus persistés et références de citations — 2C2

Prototype synthétique uniquement. Le schéma courant est 2 ; le script historique
001 est conservé sans modification. La migration 002 ajoute `corpora`,
`corpus_documents`, `citations` et l'index unique `documents(id, sha256)`.
Les citations stockent uniquement les références et positions, jamais l'extrait.
Le texte est reconstruit depuis les éléments sources, puis vérifié par
[le résolveur de provenance](PROVENANCE.md).

## API

`q25.persistence.corpus.CorpusRepository(database)` expose :

| Méthode | Effet |
| --- | --- |
| `create(corpus)` | Créer un corpus technique explicite. |
| `admit(corpus, revision)` | Admettre une source existante avec son empreinte exacte. |
| `snapshot(corpus)` | Relire les documents actuellement admis. |
| `resolve(corpus, span, expected_text=None)` | Vérifier la plage contre les admissions courantes. |
| `save_citation(corpus, citation_id, span, expected_text=None)` | Vérifier puis enregistrer les positions uniquement. |
| `get_citation(corpus, citation_id)` | Relire et revérifier une référence stockée. |
| `remove_document(corpus, document_id)` | Retirer cette admission et ses citations. |
| `delete(corpus)` | Retirer le corpus, ses admissions et citations. |

`expected_text` est un argument nommé. Les identifiants techniques suivent les
contrats 2C1. Chaque opération utilise une transaction `BEGIN IMMEDIATE` : les
lectures de sources et d'admissions sont cohérentes et un conflit de transaction
est refusé. Les requêtes sont paramétrées et limitées au corpus demandé.
L'ingestion et DocumentRepository restent des APIs internes sans autorisation
multiutilisateur ; les nouvelles analyses devront passer par le repository de corpus.

## Migration et copies

Les ouvertures synthétiques habituelles appliquent les migrations jusqu'à 2.
Les tests construisent explicitement des fixtures 1 et en prennent une copie
vérifiée avant évolution. La migration est atomique, sans downgrade ; un échec
restaure le schéma et l'historique antérieurs. Une ancienne application limitée
à 1 refuse la version 2.

Les validateurs et copies reconnaissent exactement les versions 1 et 2 : tables,
triggers, historique et checksums, intégrité, clés étrangères, sources et, pour 2,
admissions et positions des citations. Les comparaisons de copies incluent les
trois nouvelles tables. Une citation aux positions falsifiées bloque la sauvegarde.

La restauration conserve le schéma de la sauvegarde dans un nouveau fichier.
**L'ouverture de ce fichier restauré applique la migration avant usage courant.**
La sauvegarde source n'est jamais migrée. SQLCipher utilise les mêmes contrôles,
avec clés de test explicites ; aucune copie chiffrée ne repasse en clair.

## Retrait et limites

Une source peut appartenir à plusieurs corpus. Son retrait d'un corpus supprime
uniquement les citations de cette admission ; les autres corpus et les documents
sources restent intacts. Les clés étrangères composites empêchent les références
à une admission ou empreinte inexistante. Les positions sont en plus vérifiées
par le code à l'écriture, à la lecture et à la validation des copies.

`resolve` et `get_citation` relisent la base courante. Un ancien snapshot ou résultat
déjà retourné reste lisible en mémoire ; il n'est pas révoqué. Une modification
après la transaction reste possible : un futur export devra revalider ses
admissions et références au moment approprié. Aucun contrôle d'accès contre un
programme hostile du même compte n'est fourni.

Le retrait logique ne purge ni les sources partagées, ni les jobs/audits,
WAL/SHM, sauvegardes ou objets mémoire. La politique de rétention et la purge
globale exigent une étape distincte. Il n'y a pas encore d'identité logique
regroupant les versions d'un fichier, d'analyse LLM ou d'anonymisation livrée.
Les erreurs du repository sont fermées ; aucun journal, réseau ou processus
n'est déclenché par les documents.

## Vérification

```bash
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src .venv/bin/python -m unittest tests.integration.test_corpus_pipeline -v
```

14 nouveaux tests : mêmes sept scénarios sur SQLite et SQLCipher, dont migration
1→2, rollback provoqué, restauration ancienne, réouverture, source partagée,
références falsifiées et absence de réseau/processus/journaux. Sans SQLCipher,
les sept scénarios du moteur facultatif sont explicitement ignorés.
