# Mesures et protocole — étape 1

## Résultats locaux du 13 novembre 2026

- 45 tests `unittest` exécutés avec succès sur Python 3.12.3, dont deux tests
  d'intégration ; données et profils modèles entièrement synthétiques.
- Cas RAM 8/16/24/32/64 Gio, disponibilité différente à total constant, frontières
  de budget, cache KV, swap, GPU multiples, mémoire unifiée et séquencement.
- Robustesse : permissions refusées, champs manquants, cgroup v1/v2 et parents,
  outil absent, sortie invalide, timeout réel et dépassement de sortie bornée.
- Intégration réelle dans la session Linux/WSL : CPU logique et disque disponibles,
  RAM disponible inconnue car limites cgroup non vérifiables, GPU inconnu.
  Les quatre candidats sont refusés et le repli `sans_llm` est présent.
- Tests d'intégration avec sockets Python bloquées, aucun rapport créé dans le
  dossier de travail et aucune donnée utilisateur importée. Ces tests ne sont
  pas une preuve de filtrage réseau au niveau du système ou des processus tiers.
- Aucun benchmark d'inférence, chargement de modèle, débit ou pic RAM/VRAM mesuré.
  Aucun résultat Windows/macOS natif ne peut être déduit des mocks.

## Politique mémoire initiale

Réserves paramétrables dans `BudgetPolicy`, exprimées en octets. Hypothèses
prudentes de développement, pas exigences officielles ou seuils benchmarkés :

| Réserve | Valeur initiale |
| --- | ---: |
| Marge système additionnelle | 2 Gio |
| Application | 1 Gio |
| Marge RAM additionnelle | 1 Gio |
| Marge par GPU dédié | 512 Mio |
| Marge disque | 1 Gio |

Les 4 Gio de réserves RAM sont soustraits de la mémoire **disponible**, jamais
de la seule mémoire totale. La consommation OS déjà active est donc déjà
reflétée dans la mesure ; la réserve système est une marge additionnelle pour
les variations futures. Cette prudence devra être calibrée sur mesures réelles.
Une disponibilité inconnue produit un budget inconnu, pas une estimation.

Les limites mémoire des ancêtres cgroup v2 sont considérées ; l'espace libre
respecte leur consommation actuelle. Le quota CPU est ramené à un nombre entier
prudent de travailleurs (au minimum un lorsqu'un quota positif fractionnaire
est connu). Swap, mémoire GPU et mémoire unifiée ne sont pas cumulés en RAM.

## Protocole futur, soumis à un nouveau plan

Avant de valider un profil réel, vérifier modèle/version/licence, source/hash,
runtime/version, OS/architecture, quantification et contexte exact. Mesurer les
poids et le fonctionnement : pic RAM/VRAM, contexte/KV, embeddings seuls, LLM seul,
chargement simultané et séquentiel, variations de charge, OOM et annulation.
Consigner première réponse, débit et durée par document synthétique ; répéter
sur les plateformes cibles. Aucun modèle ne devient `verified` à partir d'un
tableau mémoire indicatif ou du seul succès des tests de sélection.
