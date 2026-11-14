# Références légales — anonymisation et usage d'IA en ligne

Revue initiale du **13 novembre 2026**, sources officielles uniquement. Ce document
sert au cadrage et à la traçabilité ; il ne certifie ni le logiciel ni une sortie.
Les implications produit ci-dessous sont des propositions à valider avant code.

## Finalité et champ d'application

Q25 vise à traiter localement des documents pour pouvoir ensuite utiliser
leurs sorties dans une IA sur Internet. Distinguer trois questions : qualité du
caviardage, qualification de l'anonymisation et autorisation de communication au
destinataire. Leur réponse dépend du contexte, pas seulement du fichier exporté.

La Loi 25 a modifié plusieurs lois. Il faut déterminer le régime de l'organisation
et des renseignements : secteur privé, organisme public, ordre professionnel ou
régime particulier. Les usages personnels ne se confondent pas avec l'exploitation
d'une entreprise. Notamment, l'article 3 de P-39.1 exclut certains renseignements
de santé et de services sociaux régis par R-22.1 : aucun usage sectoriel de ce type
n'est validé par cette revue. [Source : loi privée](https://www.legisquebec.gouv.qc.ca/fr/pdf/cs/P-39.1.pdf).

## Sources et limites de vérification

| Référence officielle | Consultation et portée |
| --- | --- |
| [P-39.1 — secteur privé](https://www.legisquebec.gouv.qc.ca/fr/pdf/cs/P-39.1.pdf) | PDF consulté, consolidation affichée au 15 octobre 2026 ; articles ciblés, notamment 2, 3, 3.1 à 3.3, 10, 12 à 14, 17 et 23. |
| [A-2.1 — secteur public](https://www.legisquebec.gouv.qc.ca/fr/pdf/cs/A-2.1.pdf) | Accès direct en erreur 502 lors de cette revue ; articles 63.5, 70.1 et 73 recoupés avec les sources CAI/Québec ci-dessous. Consolidation à recontrôler. |
| [Règlement A-2.1, r. 0.1](https://www.legisquebec.gouv.qc.ca/fr/pdf/rc/A-2.1%2C%20R.%200.1.pdf) | Accès direct en erreur 502 ; texte intégral de l'édiction lu dans la Gazette officielle ci-dessous. Modifications ultérieures à recontrôler sur la consolidation. |
| [Décret 783-2024 — Gazette officielle, 15 mai 2024, p. 2847–2849](https://www.publicationsduquebec.gouv.qc.ca/fileadmin/gazette/pdf_encrypte/lois_reglements/2024F/83286.pdf) | Source officielle de repli, articles 1 à 10 ; texte et rendu des pages réglementaires consultés. |
| [Québec — anonymisation, secteur public](https://www.quebec.ca/gouvernement/travailler-gouvernement/normes-gouvernance-pratiques-internes/protection-des-renseignements-personnels/anonymisation) | Explications de l'article 73, finalités et critères ; ne remplace pas la loi. |
| [CAI — conservation/destruction, privé](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-entreprises-privees/conservation-destruction-renseignements-personnels) | Distinction anonymisation/dépersonnalisation et position de prudence. |
| [CAI — conservation/destruction, public](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-ministeres-et-organismes-publics/conservation-destruction-renseignements-personnels) | Distinctions et obligations propres au secteur public. |
| [CAI — communication, privé](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-entreprises-privees/utilisation-communication-renseignements-personnels) | Nécessité, consentement/exceptions et communications hors Québec. |
| [CAI — communication, public](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-ministeres-et-organismes-publics/utilisation-communication-de-renseignements-personnels) | Règles de communication et EFVP du secteur public. |
| [CAI — guide EFVP, version 3.1, avril 2024](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_GU_EFVP.pdf) | Sections 1, 7.1 et 7.2 ; guide explicatif, pas un texte obligatoire à appliquer mot à mot. |
| [CAI — rapport quinquennal 2026, version abrégée](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_RQ_2026_Res.pdf?v=1781187811) | Section 2.1.6, recommandations 40 à 43 : enjeux et propositions de réforme, pas des modifications législatives établies par ce rapport. |
| [Québec — bonnes pratiques d'IA générative](https://www.quebec.ca/securite-situations-urgence/cybersecurite/conseils-cybersecurite/utilisation-securitaire-de-lintelligence-artificielle-generative/bonnes-pratiques-dutilisation-de-lintelligence-artificielle) | Conseil de ne pas divulguer d'informations personnelles ou confidentielles et de vérifier les règles internes. |

Cette revue ciblée ne démontre ni l'exhaustivité du droit applicable ni l'absence
de changement depuis les versions consultées. Revalider avant implémentation
juridiquement déterminante, avant livraison et lors d'un changement de finalité.

## Exigences du règlement : articles et preuves attendues

Synthèse du [décret officiel](https://www.publicationsduquebec.gouv.qc.ca/fileadmin/gazette/pdf_encrypte/lois_reglements/2024F/83286.pdf).
La colonne produit est une traduction proposée, pas une obligation de format logiciel.

| Article | Exigence | Preuve à prévoir dans le produit/processus |
| --- | --- | --- |
| 1–2 | Champ et trois critères de risque. | Régime identifié ; individualisation, corrélation, inférence évaluées séparément. |
| 3 | Finalités établies avant traitement ; nouvelle finalité à contrôler. | Finalité précise et historique des changements. |
| 4 | Supervision par une personne compétente. | Rôle, compétence et validation documentés. |
| 5 | Retrait des identifiants directs, puis analyse préliminaire. | Résultat du retrait ; risques indirects et sources raisonnablement disponibles. |
| 6 | Techniques reconnues et protections adaptées aux risques. | Techniques retenues, motifs, limites et mesures de sécurité. |
| 7 | Analyse après traitement ; risques résiduels très faibles, sans exigence de risque nul. | Dossier contextualisé, moyens de réidentification et validation compétente ; aucun score substitutif. |
| 8 | Réévaluation périodique tenant compte des avancées technologiques. | Échéance motivée par les risques ; perte du statut anonyme si les conditions échouent. |
| 9 | Registre : description, finalités, techniques/protections, dates des analyses et mises à jour. | Champs obligatoires, traçabilité et export local du registre. |

L'article 10 de l'édiction prévoit l'entrée en vigueur générale le quinzième jour
suivant la publication et celle du registre au 1er janvier 2025. Ne pas traiter
le registre comme une exigence future non encore applicable.

La fréquence de réévaluation est liée au risque ; ce texte ne fixe pas une
fréquence annuelle universelle. Il ne fournit pas non plus un pourcentage
universel d'acceptation, une valeur de k-anonymat ou une liste de modèles approuvés.

## Qualification, finalités et responsabilité

- Dans le privé, l'article 23 prévoit, après accomplissement des finalités,
  destruction ou anonymisation pour des fins sérieuses et légitimes, sous réserve
  des délais légaux. La définition vise l'identification directe et indirecte,
  de façon irréversible et selon les circonstances.
  [Source : P-39.1, article 23](https://www.legisquebec.gouv.qc.ca/fr/pdf/cs/P-39.1.pdf).
- Dans le public, l'article 73 vise des fins d'intérêt public, avec réserves
  relatives aux archives et au Code des professions. Le simple désir d'utiliser
  une IA ne démontre pas cette finalité.
  [Source : Québec](https://www.quebec.ca/gouvernement/travailler-gouvernement/normes-gouvernance-pratiques-internes/protection-des-renseignements-personnels/anonymisation).
- La CAI indique qu'une donnée dépersonnalisée demeure personnelle et souligne
  la difficulté de garantir l'irréversibilité. Le retrait des noms ou la substitution
  par un identifiant ne vaut donc pas validation d'anonymisation.
  [Source : CAI, privé](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-entreprises-privees/conservation-destruction-renseignements-personnels).
- Point juridique ouvert essentiel : le cas d'usage vise parfois une réutilisation
  avant la fin des finalités initiales. Le rapport CAI 2026 relève l'encadrement
  non explicite de l'anonymisation à d'autres moments du cycle de vie et propose
  de le clarifier. Ces recommandations ne sont pas une autorisation ni une réforme
  déjà en vigueur. Faire valider cette articulation pour nos usages.
  [Source : CAI 2026, section 2.1.6](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_RQ_2026_Res.pdf?v=1781187811).

La gouvernance, la sécurité et l'EFVP de l'organisation restent à examiner :
responsable désigné, accès, conservation, destruction, incidents et droits des
personnes selon le régime. Le traitement local n'exonère pas de ces obligations.
Notamment, l'acquisition ou le développement d'un système impliquant des
renseignements personnels peut déclencher une EFVP dès le début du projet.
[Source : CAI, guide EFVP, sections 1 et 7.2](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_GU_EFVP.pdf).

## Transmission à une IA externe

Si la sortie reste personnelle, examiner nécessité, information, consentement
valide ou exception applicable. Si un destinataire ou traitement est hors Québec,
les articles 17 du privé et 70.1 du public prévoient notamment une EFVP préalable,
une protection adéquate et une entente écrite, dans leur champ et sous réserve
des exceptions. Désactiver l'entraînement d'un service ne démontre pas à lui seul
la légalité de la communication.
[Sources : CAI privé](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-entreprises-privees/utilisation-communication-renseignements-personnels),
[CAI public](https://www.cai.gouv.qc.ca/protection-renseignements-personnels/information-ministeres-et-organismes-publics/utilisation-communication-de-renseignements-personnels).

Même une anonymisation établie ne lève pas automatiquement les autres contraintes
de confidentialité, de contrat, de secret professionnel ou de politique interne.
Les modalités du service et le contexte doivent être examinés séparément ; aucun
service ou abonnement ChatGPT n'est évalué ou déclaré approprié dans cette revue.
[Source : Québec, bonnes pratiques](https://www.quebec.ca/securite-situations-urgence/cybersecurite/conseils-cybersecurite/utilisation-securitaire-de-lintelligence-artificielle-generative/bonnes-pratiques-dutilisation-de-lintelligence-artificielle).

## Conséquences proposées pour la conception, à approuver avant code

1. Conserver l'export local et la transmission externe comme deux décisions
   distinctes ; aucune connexion à une IA ni autorisation d'envoi automatique.
2. Prévoir la finalité, le contexte/destinataire et la supervision avant de
   qualifier un processus d'anonymisation ; ne pas attendre l'étape 7 pour ces
   critères de conception. Cette étape développera le parcours complet.
3. Évaluer un document dans son corpus et son contexte : événements rares,
   dates, lieux, professions, liens familiaux et formulations peuvent réidentifier.
4. Tester la suppression réelle dans le fichier rouvert, les métadonnées,
   annotations, pièces jointes et couches cachées selon le format ; refuser
   la qualification finale si un contrôle requis échoue ou reste indéterminé.
5. Distinguer retrait vérifié, revue humaine et analyse de risque ; aucun bouton
   « conforme à 100 % », aucune équivalence entre F1 élevée et anonymisation.
6. Prévoir registre, dates d'analyses, responsabilité des réévaluations et limites
   connues après une transmission dont les copies ne peuvent plus être maîtrisées.

## Points à soumettre à validation spécialisée

- Régime applicable, finalités autorisées et articulation avec la réutilisation IA.
- Consolidations du règlement et de la loi publique ; éventuelles réformes récentes.
- Méthode et preuves suffisantes de risque très faible pour du texte libre et
  des corpus ; critères de refus et compétences de la personne qui supervise.
- Conditions d'usage des sorties, communication hors Québec et régimes sectoriels.
- Obligations organisationnelles hors périmètre du seul logiciel ; wording public
  et limites contractuelles avant tout usage réel.
