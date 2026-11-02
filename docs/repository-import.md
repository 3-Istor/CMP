# Importer un repo depuis CMP

Modèle validé par le PO, 2 novembre 2026. Travail sur `import-fichier-joe-raph`.

## Lancement local

```sh
make import-setup
make import-doctor
IMPORT_ENABLED=false make import-local BACKEND_PORT=8001
```

Interface : `http://localhost:3000`, backend : `http://127.0.0.1:8001`.
Le lanceur utilise une base SQLite dédiée, le template voisin et désactive les
collecteurs. Il respecte les choix du `.env`, sans activer implicitement l’import.
Les secrets des services CNP viennent du mécanisme habituel `make env`, avec l’accès
Kubernetes autorisé. Après modification du `.env`, redémarrer le backend.

Le SSO CNP est conservé. La liaison GitHub est distincte et passe directement par
OAuth CMP, sans fournisseur GitHub dans Keycloak. Voir [la connexion](github-connection.md).

## Parcours utilisateur

Compte → connecter GitHub / installer CNP-Portal sur son compte ou organisation,
choisir les repos et sélectionner l’installation cible vérifiée. Puis ouvrir un
projet CMP déjà actif dont on est propriétaire ou administrateur → Applications
→ Importer un repo.

Le formulaire transmet repo, branche, dossier, nom d’app, port, chemin de santé,
configuration publique, noms de secrets Vault et consentement. Il reste consultable
si l’import est désactivé ; aucune identité Session Manager n’est codée dans ce flux.

Avant clone, CMP relit les droits projet, le compte CNP actif, l’identité OAuth
GitHub et les repos accessibles dans l’installation choisie. Il vérifie cluster,
versions approuvées, registre main et secrets requis. Les droits utilisateur et
ceux de l’App sont indépendants ; un installation_id seul n’autorise rien.

La CI centrale fige source/chart, scanne et prépare les fichiers manquants. S’il
faut une PR, le run s’arrête : approbation indépendante du dernier commit, fusion
vers la branche source, puis Reprendre dans CMP. Le nouveau run rescanne la version
finale avant build, contrôle HTTP, Trivy et publication.

Le déploiement reprend le module Terraform en mode existing : aucun second repo
ou projet créé. Il garde un état S3 explicite, des tfvars publics isolés et les
secrets hors fichiers versionnés. Une seule fiche Deployment peut être créée.

Pour Argo, les repos 3-Istor réutilisent les credentials par owner existants sans
déclaration. Les repos externes sont déclarés par repo et projet, avec la clé App
et l’installation du job ; le backend relit les droits avant l’appel et avant
l’écriture. Une entrée incompatible n’est jamais écrasée. Les connexions nouvelles
et existantes sont vérifiées sans cache avant Terraform.

Le record réel du projet reste sur `cnp-projects/main`, avec SHA source/chart et
digest. Aucun overlay de recette ni repointage de l’Application argocd. Le succès
exige les bonnes révisions et destination Argo, Healthy + Synced et l’URL disponible.

## Contrôles et recette

```sh
make import-readiness
make import-readiness ARGS=--github
make import-readiness ARGS=--live
```

Le contrôle GitHub vérifie la configuration OAuth/App, pas une fédération Keycloak.
Le live lit CI/chart/Argo/Vault, sans clone ni dispatch. S3 est réellement vérifié
à init/plan. Une réussite de préflight ne remplace pas OAuth dans le navigateur et
la recette connectée.

`IMPORT_ALLOWED_PROJECT=joetest` limite cette instance locale aux imports dans le
projet existant et bloque les mutations catalogue/projets depuis cette instance.
`CNP_REGISTRY_BRANCH` reste obligatoirement `main` pour l’import. Laisser la
restriction projet vide sur une instance destinée à tous les projets autorisés.

Chaque dispatch a un `attempt_id` neuf ; les anciens callbacks ne peuvent pas
faire avancer une nouvelle tentative. Le registre et le domaine sont figés dans
le job ; un changement de contexte exige une nouvelle demande explicite.

Tout refus, erreur, timeout ou contrôle obligatoire manquant bloque la suite.
Les modifications du source importé via configuration/sécurité/FinOps/release/MCP
doivent repasser par les scans. Les écritures déjà effectuées restent tracées ;
aucun nettoyage ne supprime automatiquement le repo source.

[Schéma et fichiers](../../extern-project-analyzer/docs/import-changes-overview.md) ·
[Recette joetest et fermeture du tunnel](../../extern-project-analyzer/docs/import-recipe-joetest.md) ·
[Accès Argo et RBAC](../../K3s/k8s/config/argocd/REPOSITORY_IMPORTS.md)
