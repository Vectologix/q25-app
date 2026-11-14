# Sécurité — périmètre du prototype 2A

Ce dépôt est en développement. **Aucun traitement de document réel n'est autorisé
dans le prototype 2A.** Il n'anonymise pas les données et ne fournit aucune
certification juridique. Voir les [références légales](LEGAL_REFERENCES.md).

## Stockage synthétique seulement

- L'ouverture normale exige un chiffrement indisponible et échoue avant écriture.
- L'exception explicite `SyntheticWorkspace` crée un espace neuf et éphémère
  pour les fixtures inventées par les tests ; aucun dossier existant n'est adopté.
- Cette capacité n'est pas un détecteur de données fictives. Un programmeur peut
  fournir des octets réels à une API Python : ce serait une utilisation interdite,
  pas une anonymisation ou un contrôle de conformité effectué par le logiciel.
- SQLite, WAL, SHM et sauvegardes de cette exception sont en clair. Les textes,
  leurs extraits et leurs empreintes devront être traités comme potentiellement
  sensibles dans le futur produit et protégés par le lot 2B.
- Les clés étrangères, transactions et contrôles d'intégrité ne sont pas du
  chiffrement, une signature ou une garantie contre la falsification des fichiers.

## Mesures réalisées et tests

- Fichiers temporaires contrôlés, création exclusive sans écrasement, refus des
  chemins externes/liens symboliques et connexions fermées avant nettoyage.
- Limite de taille TXT et segmentation bornée, décodage strict sans conversion
  externe ou exécution du contenu ; paramètres SQL pour toutes les valeurs.
- Aucun accès réseau dans les nouveaux composants. Tests d'intégration avec
  sockets Python bloquées ; ces tests ne prouvent pas un filtrage réseau OS.
- Erreurs à codes/messages fermés, contenu retiré des représentations de contrat,
  audit à types d'événements fermés sans texte, nom de fichier ni chemin.
- Rollback des tâches/migrations en échec ; sauvegarde SQLite cohérente, lecture
  de restauration seule et validation préalable de la source et de la copie.
- Tests d'annulation après insertion, copie réellement verrouillée, base corrompue,
  migrations modifiées et refus de destination occupée.

Les protections de chemins de cet espace privé ne constituent pas un bac à sable
contre un attaquant exécutant du code sous le même compte. Elles ne remplacent
pas l'isolation, les ACL et les contrôles de chemins nécessaires au produit final.

## Nettoyage et limites d'effacement

Le contexte de test ferme toutes ses connexions et retire uniquement son propre
répertoire temporaire. Une panne brutale peut laisser des fichiers ; la suppression
ne garantit pas l'effacement sur SSD, swap, dumps ou sauvegardes du système.
Ne pas employer ce nettoyage comme preuve de suppression irréversible.

## Prochaine barrière : lot 2B

Recherche et choix à faire approuver : moteur/binding chiffré, licences, versions,
gestion des clés OS/keyring, fichiers temporaires et WAL/SHM, backup/restauration
chiffrés, packaging et scénarios de mauvaise clé/clé absente/corruption.
Le chiffrement devra être effectivement testé, sans algorithme maison ni booléen
de configuration présenté comme preuve. La fin de 2A ne termine pas l'étape 2.

Ne placer aucun secret, document réel, base ou sauvegarde de production dans Git,
une issue publique ou le contexte de l'agent. La procédure de divulgation de
vulnérabilité et les canaux privés restent à définir avant ouverture aux usages réels.
