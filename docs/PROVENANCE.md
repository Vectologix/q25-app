# Corpus et citations TXT en mémoire

Le lot 2C1 résout une citation contre des documents synthétiques explicitement
admis. Il réutilise `ExtractedDocument` et son SHA256 vérifié, sans fichier,
réseau, modèle, index ou persistance supplémentaire. Le schéma SQL reste en version 1.

## Contrats

API : `q25.core.provenance`. Tous ces objets sont des dataclasses immuables.

| Contrat | Contenu et règle |
| --- | --- |
| `CorpusReference` | Identifiant technique, 32 caractères hexadécimaux minuscules. |
| `RevisionReference` | Identifiant d'import et SHA256 des octets originaux, BOM compris. `from_document(document)` réutilise les métadonnées de l'extraction. |
| `SourceSpan` | Corpus, révision, début inclus et fin exclue, en caractères et en octets. Plage non vide. |
| `CorpusSnapshot` | Corpus et tuple explicite de documents admis ; un seul document par identifiant d'import. Tuple vide autorisé. |
| `ResolvedCitation` | Plage et texte restitué par le résolveur. Texte absent du `repr`. |

Les caractères sont les points de code Python, sans normalisation Unicode : un
accent combiné peut être sélectionné séparément. Les octets sont les positions
UTF-8 dans l'original ; le premier caractère commence à l'octet 3 avec BOM, à 0
sans BOM. CRLF est préservé. Une plage peut traverser des éléments d'extraction.
Un TXT vide ou contenant seulement un BOM n'a pas de citation non vide.

## Résolution

L'appelant fournit un document extrait **non vide** et un identifiant de corpus :

```python
from uuid import uuid4
from q25.core.provenance import CorpusReference, CorpusSnapshot, RevisionReference

snapshot = CorpusSnapshot(CorpusReference(uuid4().hex), (document,))
revision = RevisionReference.from_document(document)
span = snapshot.anchor(revision, 0, len(document.text))
citation = snapshot.resolve(span, expected_text=document.text)
```

`anchor` calcule les positions octets depuis les caractères de la source admise.
`resolve` peut également recevoir un `SourceSpan` proposé par un autre composant.
Il contrôle dans cet ordre la portée, la présence du document, son empreinte,
les limites caractères et la correspondance exacte des deux positions octets.
Si `expected_text` est fourni, il doit être une chaîne exactement identique à
l'extrait, sans changement de casse, d'espace ou de normalisation. Sans cette
option, le texte est simplement découpé de façon déterministe depuis la source.

Un `ResolvedCitation` construit manuellement ne prouve rien sur la source :
son constructeur contrôle seulement les types et longueurs. Pour vérifier un
résultat reçu, utiliser `snapshot.resolve(result.span, expected_text=result.text)`.
Le résolveur ne recherche ni citation voisine ni autre corpus en cas d'échec.

Les erreurs `BackendError` ont des codes et messages fermés, sans source ni
chemin : `PROVENANCE_INVALID`, `CORPUS_MISMATCH`, `CORPUS_DOCUMENT_MISSING`,
`REVISION_MISMATCH`, `SOURCE_SPAN_INVALID` et `CITATION_MISMATCH`.

## Révisions et retrait

Le snapshot courant doit être reconstruit après retrait ou remplacement d'une
source. Une citation visant un document absent ou une empreinte différente est
alors refusée. Une nouvelle extraction du même fichier produit un nouvel
identifiant d'import, même si le SHA256 est identique. Il n'y a pas encore
d'identité logique regroupant les versions d'un fichier.

Un ancien snapshot reste lisible en mémoire. L'application doit remplacer ses
références et revalider les résultats contre le snapshot courant avant usage.
Il n'y a ni révocation globale automatique, ni effacement sécurisé de la mémoire,
ni historique durable de révisions dans ce lot.

## Confidentialité et limites

La portée est une sélection logique effectuée par l'appelant de confiance. Ce
module ne découvre aucun fichier et ne fournit pas d'authentification multiutilisateur
ou de protection contre un programme hostile du même compte. Il ne peut pas
empêcher un appelant de placer explicitement un document dans plusieurs corpus.
Les instructions des documents restent du texte et ne déclenchent aucune action.

Documents et citations contiennent du texte en mémoire. Leur `repr` l'omet ;
accéder à `.text`, sérialiser avec `dataclasses.asdict` ou capturer des variables
dans une trace peut l'exposer. Ne pas journaliser ni exporter ces valeurs sans
le parcours de protection prévu. Aucun nouveau journal ou rapport n'est écrit.

La validité d'un ancrage ne démontre ni la vérité d'une réponse, ni sa couverture,
ni une anonymisation. La détection exhaustive, la revue et la vérification
indépendante restent à livrer. Données synthétiques seulement, comme le
[prototype documentaire](DATABASE.md). Persistance des corpus, migrations et
rétention doivent être approuvées dans une tranche distincte.

## Vérification

```bash
TMPDIR="$PWD/.private/tmp" PYTHONPATH=backend/src python3 -m unittest tests.unit.test_provenance tests.integration.test_provenance_pipeline -v
```

24 nouveaux tests : Unicode/BOM/CRLF, bornes et octets, empreintes et réimport,
retrait et corpus étrangers, citations inventées, représentations et erreurs.
Le trajet TXT vers citation refuse réseau/processus et, après extraction, accès
fichier. Un autre trajet vérifie la compatibilité avec le dépôt SQLite existant
après réouverture, sans table ajoutée. Voir [les tests complets](DEVELOPMENT.md).
