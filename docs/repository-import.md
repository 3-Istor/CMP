# Importer un repo dans un projet existant

Le bouton **Importer un repo** est dans l’onglet Applications du projet. Le parcours
utilise une CI centrale dans `extern-project-analyzer`, puis le module
`app-templates/templates/k3s-gitops-app` en mode `repository_mode=existing`.
Le projet CMP et le repo source existent déjà. Un import accepté crée une seule
fiche de déploiement, sans repo supplémentaire.

L’import reste désactivé par défaut (`IMPORT_ENABLED=false`). La configuration est
documentée dans `backend/.env.example`. Le guide complet, le schéma et la recette
inter-repos sont dans
[`extern-project-analyzer/docs/import-repository-implementation.md`](https://github.com/3-Istor/extern-project-analyzer/blob/import-fichier-joe-raph/docs/import-repository-implementation.md).
Ce lien sera utilisable après publication de la branche ; le document existe aussi
dans la copie locale du repo analyzer.

Avant d’activer la fonction :

1. Déployer les branches validées de `cnp-projects`, `K3s` et `app-templates`.
2. Déployer la CI centrale et enregistrer son commit exact dans `IMPORT_WORKFLOW_SHA`.
   Le commit approuvé de `infra-templates` doit être dans `IMPORT_CHART_SHA`.
3. Fournir un JWT Keycloak signé avec l’audience attendue. Le fournisseur GitHub
   doit être configuré dans Keycloak ; l’utilisateur doit lier son identité GitHub
   à son compte CNP, en plus de l’installation de l’App GitHub.
4. Injecter les accès GitHub App, GHCR en lecture et Argo CD via le gestionnaire de
   secrets. Pour le chart CMP, les variables supplémentaires passent par `extraEnv`.
5. Exécuter `alembic upgrade head` puis faire la recette sur un projet de test.

Au premier contrôle refusé, manquant ou en erreur, la suite s’arrête. Une PR attend
une approbation indépendante sur sa dernière révision et sa fusion dans `main`.
La reprise explicite déclenche un nouveau scan complet. Un import échoué peut être
relancé explicitement avant la création du déploiement ; une PR existante est réutilisée.

Le module import utilise une copie propre à l’application dans
`data/terraform_workdirs/<app>/`, avec un `import.auto.tfvars.json` sans secrets.
Les secrets Terraform passent par `TF_VAR_*`. Une suppression réutilise cette copie ;
elle ne gère ni le repo source ni ses packages existants.

Les anciens PATCH de configuration sont bloqués pour ces applications : ils
contourneraient la validation et la révision figée. Ce premier parcours couvre un
composant HTTP sans base de données ; un update de production après import devra
faire l’objet d’un nouveau parcours validé.

Vérification backend :

```sh
DEBUG=false DATABASE_URL=sqlite:// poetry run python -m unittest discover -s tests
```
