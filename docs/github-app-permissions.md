# Permissions de l'app GitHub CNP-Portal

App id 3836905. Installation sur l'org 3-Istor : 135177507, `repository_selection: all`.

Inventaire fait le 2026-11-13 sur `origin/main` de CMP, app-templates, K3s, cnp-project-base et Cloud. Objectif : garder le minimum qui fait tourner la plateforme, puis ajouter ce dont l'import a besoin (CNP-158 / CNP-241).

## Qui utilise l'app

| Consommateur | Comment il obtient un token | Où |
|---|---|---|
| CMP (API GitHub directe) | `get_org_installation_token()`, installation de l'org | `backend/app/services/github_service.py`, `backend/app/services/agents/repo.py` |
| Terraform `k3s-gitops-app` (provider `integrations/github ~> 6.12`) | token d'installation de l'org passé en `github_token` | app-templates `templates/k3s-gitops-app/main.tf`, `providers.tf` |
| Argo CD (repo-creds `cnp-portal-github-creds`) | `githubAppID` + `githubAppInstallationID` + clé privée | K3s `k8s/config/argocd/secrets.yaml`, `terraform/secret_templates.tf` |
| Argo CD Image Updater (write-back git) | même secret `argocd/cnp-portal-github-creds` | K3s `k8s/app/*/*-updater.yaml`, `k8s/config/3istor-sessions/3istor-sessions-updater.yaml` |

Ne passent **pas** par l'app :

- suppression des paquets GHCR au destroy : PAT classique `github_registry_token` (app-templates `k3s-gitops-app/main.tf`, `null_resource.delete_ghcr_packages`) ;
- lecture des manifestes GHCR et pull des images : PAT `GITHUB_REGISTRY_TOKEN` (CMP `services/environments/registry.py`, `terraform_executor.py`) ;
- `project-bootstrap` : reçoit `TF_VAR_github_token` mais le module n'a plus de ressource `github_*` ;
- runner `Cloud/terraform/openstack/projects/epimac/runner.tf` : repo personnel, autre token.

## Permissions à garder

Toutes au niveau dépôt. Aucune permission d'organisation n'est utilisée.

| Permission | Niveau | Pourquoi (référence) |
|---|---|---|
| Metadata | read | Obligatoire. `GET /repos/{repo}` : `get_default_branch` (`github_service.py`), `agents/repo.py` |
| Administration | write | Création du repo depuis un template et suppression au destroy : `github_repository.app` (app-templates `k3s-gitops-app/main.tf`). Le niveau read est aussi exigé à chaque refresh Terraform : en lisant `github_repository`, le provider `integrations/github` appelle `GET /repos/{owner}/{repo}/vulnerability-alerts`, qui demande `Administration: read`. Ne pas descendre sous read même sans création de repo |
| Contents | write | Lecture/écriture de fichiers et commits : `get_file_content`, `put_file_content`, `delete_file`, `list_directory`, `commit_files`, `get_branch_head` (`github_service.py`) appelés par `routers/deployments.py`, `routers/security.py`, `routers/security_databases.py`, `routers/finops.py`, `routers/releases.py`, `services/project_registry.py` (repo `cnp-projects`), `services/environments/gitops.py` ; trees, refs, tags, commits (`agents/repo.py`, MCP `push_files`, `cut_release`) ; releases (`create_release`, `services/environments/promotion.py`) ; skills (`services/agents/skills.py`, repo `cnp-agent-kit`) ; `github_repository_file` x3 et `data.github_branch` (Terraform) ; clone Argo CD ; write-back Image Updater sur K3s |
| Workflows | write | Écriture sous `.github/workflows/` : `github_repository_file.security_workflow` (Terraform), MCP `push_files` et `commit_files` quand un chemin CI est modifié |
| Actions | write | `dispatch_workflow` (`routers/security.py` scan manuel, `services/environments/runs.py`) ; read pour `get_latest_artifact` (`services/security/collector.py`) |
| Secrets | write | `github_actions_secret.discord_webhook_url` (Terraform) |
| Pull requests | write | `commit_files(..., as_pull_request=True)` : `POST /repos/{repo}/pulls` (`agents/repo.py`, MCP `push_files`) |

## Ajouts pour l'import

| Élément | Valeur | Pourquoi |
|---|---|---|
| Contents | read sur les repos des utilisateurs | Lire le repo importé. Déjà couvert par `contents: write`, mais chaque token sur une installation utilisateur passe par `get_scoped_installation_token(installation_id, [repo], {"contents": "read", "metadata": "read"})` |
| Événement webhook | `push` | Déclencher la resynchronisation. URL du webhook et secret à configurer plus tard |
| Événements `installation`, `installation_repositories` | automatiques | Envoyés à toute app sans abonnement. Utiles pour révoquer un lien quand l'utilisateur désinstalle ou retire un repo |

## Permissions à retirer

Aucune référence trouvée dans le code.

Dépôt :

- `actions_variables`
- `agent_secrets`, `agent_tasks`, `agent_variables`, `copilot_agent_settings`
- `artifact_metadata`, `attestations`
- `checks`, `statuses`, `deployments`, `merge_queues`
- `code_quality`, `security_events`, `secret_scanning_alerts`, `vulnerability_alerts`, `repository_advisories`
- `secret_scanning_bypass_requests`, `secret_scanning_dismissal_requests`, `repo_secret_scanning_dismissal_requests`
- `codespaces`, `codespaces_lifecycle_admin`, `codespaces_metadata`, `codespaces_secrets`
- `dependabot_secrets`
- `discussions`, `issues`, `issue_fields`, `issue_types`
- `environments` : les environnements CNP (staging, prod) sont gérés par le CMP, pas par GitHub
- `packages` : la suppression GHCR utilise le PAT classique
- `pages`
- `repository_custom_properties`, `repository_hooks`, `repository_projects`

Organisation : **toutes**, dont `members`, `organization_administration`, `organization_hooks`, `organization_secrets`, `organization_self_hosted_runners`, `organization_personal_access_tokens`, `organization_personal_access_token_requests`, `organization_credentials`, `organization_custom_*`, `organization_projects`, `organization_copilot_*`, `organization_codespaces*`, `organization_dependabot_*`, `organization_actions_variables`, `organization_agent_*`, `organization_user_blocking`, `organization_private_registries`, `organization_runner_custom_images`, `organization_network_configurations`, `organization_announcement_banners`, `organization_campaigns`, `organization_events`, `organization_models`, `organization_api_insights`, `organization_plan`, `organization_code_scanning_dismissal_requests`, `organization_secret_scanning_bypass_requests`.

## Points d'attention

- Les permissions d'une app GitHub valent pour **toutes** ses installations. Un utilisateur qui installe CNP-Portal sur ses repos accorde aussi `administration`, `secrets`, `workflows` et `contents: write`. Le CMP ne s'en sert jamais : tout token sur une installation utilisateur devra passer par `get_scoped_installation_token`, qui n'a pas encore d'appelant (socle pour CNP-241). En attendant, le portail n'invite plus à installer CNP-Portal sur un compte personnel. L'écran d'installation affiche quand même la liste complète. Le réduire ici limite ce que l'utilisateur doit accepter.
- `Administration: write` est la permission la plus large restante. Elle est indispensable tant que Terraform crée les repos d'apps. Même ensuite, `Administration: read` reste nécessaire : le provider lit `GET /repos/{owner}/{repo}/vulnerability-alerts` à chaque refresh de `github_repository`, et le plan échoue sans elle. Retirer la permission `vulnerability_alerts` ne change rien à ce point.
- Provider Terraform GitHub : sa lecture de `github_repository` peut appeler des endpoints annexes (alertes de vulnérabilité, Pages). Après retrait, lancer un déploiement puis une suppression d'app de test sur staging avant de valider.
- L'app s'abonne à zéro événement aujourd'hui (`events: []`).
- Après modification des permissions, GitHub demande à l'owner de chaque installation d'accepter les nouvelles. Un retrait s'applique sans validation, un ajout (événement `push`) en demande une.
