# Tester le bouton d’import sur la branche locale

Actualisation du 25 octobre 2026. Travailler dans **CMP-import-fichier-joe-raph**,
sur **import-fichier-joe-raph**. Les changements de main ont été récupérés dans les
cinq repos puis intégrés uniquement à nos branches. Aucun push ni déploiement.

## Lancer CMP

Les commandes dédiées évitent le tunnel OpenStack et les états Terraform distants.
Elles utilisent la branche locale d’app-templates, une base SQLite dédiée et
gardent l’import réel désactivé. Les collecteurs cloud/cluster ne démarrent pas.

    cd /home/joebuntu/Desktop/SIGL/CNP/CMP-import-fichier-joe-raph
    make import-setup

La préparation a déjà été exécutée pendant cette session. Elle installe les
dépendances des lockfiles, prépare les fichiers ignorés backend/.env et
frontend/.env.local sans écraser des valeurs existantes, génère un secret de
session local et applique les migrations à backend/data/import-local.db.
Node 22 est utilisé temporairement si le Node système est trop ancien.

La connexion CNP reste obligatoire. Renseigner le secret du client Keycloak
**3-istor-openid** dans les deux fichiers locaux. Si un accès kubectl CNP est
déjà disponible, la commande suivante copie uniquement ce secret, sans l’afficher :

    make import-auth

Elle utilise le contexte KUBE_CONTEXT du Makefile et le secret arcl-cmp-secret,
clé keycloak-client-secret, namespace arcl-cmp. Elle ne copie pas les autres
credentials de production et ne les écrit pas dans Git. Sans accès cluster,
faire renseigner la configuration locale par un administrateur CNP.

    make import-doctor
    make import-local

Le diagnostic s’arrête si le secret, le client ou le realm ne correspond pas.
L’interface est sur http://localhost:3000 et le backend sur http://127.0.0.1:8000.
Ouvrir un projet sur lequel le compte est admin/propriétaire, puis **Applications
→ Importer un repo**. Le bouton est absent pour les membres/invités.

Les deux serveurs peuvent aussi être lancés séparément :

    make import-backend
    make import-frontend

Dans ce lanceur, le formulaire est consultable, les champs et le consentement
fonctionnent, mais l’envoi réel est désactivé avec un message explicite. Les noms
d’application invalides sont refusés côté navigateur et API.
Aucune modification de repo ni opération de cloud ne part de ce mode local.
Si l’un des serveurs du lancement groupé s’arrête, l’autre est également arrêté.

## État des accès vérifié ici

L’endpoint public Keycloak répond et publie le realm 3istor. Aucun secret du client
n’a été trouvé dans la copie locale ou l’environnement de cette session. Les
exemples GitHub référencent le gestionnaire de secrets, pas une valeur à récupérer
dans le code.

kubectl n’est pas installé sur la machine. La configuration SSH contient
pae-node-1, mais la tentative en mode non interactif échoue sur 10.0.0.1 avec
« no route to host ». Il faut rétablir cet accès ou faire fournir la configuration
locale par le PO/admin pour tester la vraie connexion SSO. Le diagnostic actuel
indique exactement les deux champs KEYCLOAK_CLIENT_SECRET manquants.

## Ce qui a été testé

- Backend CMP : **526 tests réussis** ; vrais JWT de test signés, autorisations,
  absence de doublon, PR, refus release/staging/MCP et catalogue local sans clone/pull.
- Analyzer : 41 tests ; registre : 3 tests et 3 records valides ; K3s : 5 tests des
  expressions Go/Sprig et des ImageUpdaters ; Terraform : 3 tests à providers simulés.
- **578 tests automatisés** au total, plus **3 scénarios navigateur** sur le vrai
  frontend local : import désactivé, consentement puis refus d’accès sans
  continuation, et bouton absent pour un invité. Session et API simulées pour ces
  scénarios ; aucune connexion SSO réelle ni mutation distante.
- Lint/types/build Next 16.4.2 réussis. Black/isort passent et Pylint est à 9.92/10,
  au-dessus du seuil du repo. Les avertissements existants sont détaillés dans
  le compte rendu de l’analyzer.
- Migration de jonction f71a0c9e2b46 : une tête, upgrade/downgrade/upgrade vérifiés
  sur une base temporaire et upgrade sur la base dédiée au lancement local.
- Backend démarré sans clone du catalogue ni appels cloud ; health répond 200.
  Frontend démarré et page welcome répond 200. Serveurs de test arrêtés ensuite.

## Ce qui demande encore une recette connectée

La base locale ne reprend pas automatiquement les projets/déploiements de production.
Les groupes existants peuvent être listés après connexion ; un import réel exige
un projet actif correspondant dans la base de l’environnement de recette.

Pour lancer les vrais scans et déployer : workflow central publié/approuvé,
backend HTTPS joignable depuis GitHub, accès GitHub liés et App installée, chart
privé approuvé, registre, Argo et credentials cloud nécessaires. Le lanceur local
conserve IMPORT_ENABLED=false même si le fichier .env contient une autre valeur.
Ce comportement permet de tester le formulaire sans déclencher une ancienne CI.

Les modifications d’une app déjà importée via configuration, sécurité, FinOps,
release, staging et MCP sont refusées pour préserver la version scannée. Le
générateur Argo conserve le staging catalogue et limite les imports à prod ;
les ImageUpdaters ne sont pas créés pour les imports.

Le schéma et le guide inter-repos sont aussi dans
extern-project-analyzer/docs/import-repository-implementation.md et le compte rendu
du 25 octobre dans extern-project-analyzer/docs/import-oct25-analysis.md.
