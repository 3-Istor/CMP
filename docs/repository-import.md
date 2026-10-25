# Tester l’import de repo depuis CMP local

La branche de travail est `import-fichier-joe-raph`, dans le worktree
`CMP-import-fichier-joe-raph`. Le parcours utilise un projet CMP **déjà actif**.
Le schéma, les fichiers par repo, les prérequis de recette et l’exemple démo sont
dans [le guide joetest](../../extern-project-analyzer/docs/import-recipe-joetest.md).

## Démarrage local

```sh
cd /home/joebuntu/Desktop/SIGL/CNP/CMP-import-fichier-joe-raph
make import-setup
make import-doctor
make import-local BACKEND_PORT=8001
```

L’interface est sur http://localhost:3000 ; l’API écoute sur 127.0.0.1:8001.
Le lanceur utilise une base SQLite dédiée et l’app-templates voisin. Les collecteurs
sont désactivés. Il respecte les paramètres S3 et IMPORT_ENABLED du `.env`
backend ; **l’import reste désactivé par défaut**, sans activation implicite.
Après une modification de `.env`, arrêter puis relancer le backend.

Le compte SSO et le secret du client Keycloak `3-istor-openid` sont nécessaires
à l’authentification locale. Si l’accès cluster est disponible :

```sh
make import-tunnel
# Dans un autre terminal :
make import-auth KUBE_CONTEXT=3istor
```

`import-auth` copie uniquement le secret client dans les fichiers locaux ignorés,
sans l’afficher. Le tunnel SSH utilise 127.0.0.1:6443 vers l’API K3s et conserve
le port 3000 libre. Le contexte Kubernetes est `3istor`, avec le plugin OIDC.
Le VPN concerne l’accès réseau au cluster ; le site public et le SSO peuvent
fonctionner sans ce VPN.

La commande standard `make env` de CMP synchronise aussi les credentials des
services CNP dans le `.env`. Elle a été exécutée manuellement par l’utilisateur.
Si Vault public est inaccessible, laisser dans un terminal dédié :

```sh
make import-vault-tunnel KUBE_CONTEXT=3istor
```

Configurer alors `VAULT_URL=http://127.0.0.1:18200` dans le `.env` backend ignoré.
Le port-forward expose Vault uniquement sur loopback. Ces accès utilisent les
services réels de CNP ; le fonctionnement local concerne le frontend/backend.

## Formulaire et vrai import

Ouvrir joetest → Applications → **Importer un repo**, avec le compte propriétaire
ou admin du projet. Le bouton n’est pas affiché aux invités/membres.
Le formulaire permet de choisir le repo, sa branche, le dossier, le port HTTP,
le chemin de santé et la configuration de l’application.

Pour Session Manager : branche `import-fichier-joe-raph`, dossier `.`, port `8000`,
santé `/api/health`, mode démo selon le guide joetest. Les variables publiques sont
proposées dans la PR ; les secrets éventuels restent dans Vault.

Un formulaire consultable ne signifie pas que l’import réel est activé. Celui-ci
exige aussi les deux liaisons GitHub, l’App installée sur les repos, le workflow
et le chart publiés à leurs SHA approuvés, le callback HTTPS, le registre et le
profil Argo compatibles, ainsi que les accès Terraform/GHCR/Vault.

Le 25 octobre, les contrôles en lecture ont confirmé le projet actif joetest et
les droits de son propriétaire. Le parcours de connexion GitHub a été corrigé
pour réutiliser l’installation existante ; la liaison d’identité et le rattachement
restent à terminer par l’utilisateur dans Compte. Aucun import connecté de Session
Manager n’a été lancé et aucun contrôle d’identité n’a été contourné. L’import
reste désactivé dans le `.env` local.

Après publication et configuration autorisées, vérifier sans muter les services :

```sh
make import-readiness
make import-readiness ARGS=--live
```

Le second contrôle lit GitHub et Argo : commit du dispatcher, callback, audience,
provenance du registre, pins source/chart/digest et protection ImageUpdater.
Il n’active pas l’import et ne clone, ne dispatch ni ne déploie rien.

Une fois les prérequis validés, l’utilisateur lance l’import et donne son
consentement dans le portail. Si des fichiers manquent, la CI propose une PR et
s’arrête. Il faut une approbation indépendante du dernier commit, la fusion sur
la branche source choisie puis **Reprendre** dans CMP. Le nouveau run rescanne
la version finale avant build et déploiement.

## Isolation et vérification

En recette avec `IMPORT_ALLOWED_PROJECT=joetest` et
`CNP_REGISTRY_BRANCH=import-fichier-joe-raph`, les imports des autres projets sont
refusés. La création/suppression de projet et les déploiements catalogue sont
aussi refusés sur cette instance, pour éviter d’écrire leur état dans le registre
de test. Le profil K3s ne change la branche de registre que pour joetest.

Le premier refus, timeout, scan bloquant ou contrôle manquant arrête le parcours.
Une PR en attente ne permet aucun build ni déploiement. Seuls le rapport, le statut
et le nettoyage temporaire peuvent continuer. Le déploiement réutilise le projet
et le repo existants ; les tfvars se trouvent dans un workdir propre à l’app,
jamais dans le template partagé. Les modifications directes du repo importé par
les écrans configuration/sécurité/FinOps/release/MCP sont bloquées.

Les tests backend utilisent des JWT signés de test et une base SQL temporaire,
avec les services externes simulés. Le frontend a passé lint, TypeScript et build.
Le script inter-repos `extern-project-analyzer/scripts/check-import-local.sh`
regroupe les tests analyzer, CMP, registre, K3s, chart et Terraform simulé.
Ces résultats ne remplacent pas la recette connectée avec SSO/GitHub/Argo réels.

## Correction de la connexion GitHub

Le bouton Compte réutilise maintenant l’installation CNP existante après vérification
de l’identité GitHub liée dans Keycloak et de l’appartenance à l’organisation.
La page d’installation réservée aux administrateurs ne fait plus partie de la
connexion courante. Voir [le parcours et les permissions](github-connection.md).
