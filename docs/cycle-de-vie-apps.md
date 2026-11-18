# Ce que crée une app, et qui le supprime (CNP-306)

Inventaire lu dans le code le 2026-11-18 : CMP `main` (`1d2f284`), app-templates
`main` (`templates/k3s-gitops-app`), cnp-project-base `main`
(`templates/namespace.yaml`). Il sert de référence au contrôle d'orphelins
(`backend/app/services/orphans.py`) : une ligne ajoutée ici sans contrôle
correspondant est un orphelin que personne ne verra.

## App créée depuis un template (`k3s-gitops-app`)

Création : `terraform_orchestrator.run_deployment` applique
`templates/k3s-gitops-app`, puis la CMP inscrit l'app dans le registre.
Suppression : `DELETE /api/deployments/{id}` lance
`terraform_orchestrator.run_deletion` (staging d'abord, puis `terraform destroy`,
puis désinscription du registre, puis suppression des états Terraform).

| Ressource | Créée par | Supprimée par | Rejouable après un échec |
|---|---|---|---|
| Repo GitHub `3-Istor/<app>` (historique compris, accepté pour le MVP) | Terraform `github_repository.app` | `terraform destroy` | Oui |
| Fichiers `deploy/values*.yaml`, `.github/workflows/security.yml`, secret Actions `DISCORD_WEBHOOK_URL` | Terraform | Partent avec le repo | Oui |
| Client OIDC Keycloak `<app>` et ses scopes | Terraform `keycloak_openid_client.app_client` | `terraform destroy` | Oui |
| Secrets Vault `project-<p>/<app>` et `project-<p>/<app>/registry` | Terraform `vault_kv_secret_v2` (`delete_all_versions`) | `terraform destroy` | Oui |
| Politique Vault `<p>-<app>-policy` (CNP-297) et rôle `<p>-<app>-role` sur le montage du cloud | Terraform | `terraform destroy` | Oui |
| CNAME Cloudflare `<app>-<p>` | Terraform `cloudflare_dns_record.app_cname` | `terraform destroy` | Oui |
| Images GHCR `<app>` ou `<app>/frontend`, `<app>/backend` | CI du repo de l'app | Provisioner de destruction `null_resource.delete_ghcr_packages` | Oui, mais un refus de GitHub (401, 403) n'échoue pas : l'image reste sans bruit. Non contrôlé |
| Staging : client Keycloak, secrets `<app>/staging`, politique `project-<p>-<app>-staging-policy`, rôle `<p>-<app>-staging-role`, CNAME | Module `staging` (`environments/activation.py`) | `destroy_with_app`, avant le destroy de l'app | Oui |
| Entrée de l'app dans `cnp-projects/registry/projects/<p>.yaml` | `project_registry.register_app` | `project_registry.unregister_app` | **Corrigé ici** : un échec du registre marquait l'app supprimée et laissait l'entrée. L'app passe maintenant en échec, et une nouvelle suppression reprend à cette étape |
| Application Argo CD `<p>-<app>-app` | ApplicationSet, depuis le registre | ApplicationSet, quand l'entrée quitte le registre | Oui |
| Namespace `<p>-<app>` (et `-stg`), base CNPG et volumes dedans | cnp-project-base, depuis le registre | Élagage Argo CD de cnp-project-base | Oui |
| États Terraform `cnp/projects/<p>/<app>/terraform.tfstate` et `.../staging/` | Backend S3 | **Ajouté ici** : `aws_tfstate.delete_states`, seulement après un destroy réussi | Oui (une clé absente n'est pas une erreur) |
| Ligne `deployments` | API | Gardée, statut `deleted`, `github_repo_url` vidé | Oui |
| Journal d'audit, activité, notifications | API, collecteur | Gardés exprès (traçabilité, purge des notifications à 90 j) | Sans objet |
| Log local `logs/deployments/<id>.log` du pod CMP | Orchestrateur | Disparaît avec le pod | Sans objet |

Une suppression interrompue par un redémarrage de la CMP repasse en `failed`
au démarrage (`project_status.fail_interrupted_runs`) : la relancer est le
chemin normal, et chaque étape ci-dessus accepte de trouver sa ressource
déjà partie.

## App importée depuis un repo

Création : `services/imports/deploy.py` (après application du plan). Suppression :
`DELETE /api/projects/{p}/imports/{id}` appelle `deploy.remove`, qui retire les
apps du registre en un commit, supprime les CNAME et les rôles Vault, puis
attend qu'Argo CD retire chaque Application pour supprimer son namespace
(10 min au plus).

| Ressource | Créée par | Supprimée par | Rejouable après un échec |
|---|---|---|---|
| Entrées de type `service` et `backing` dans le registre | `generate.registry_entry` | `deploy.remove` | Voir plus bas |
| Namespace `<p>-<app>` avec ses volumes (Argo CD ne l'élague pas : `Prune=false`) | cnp-project-base | `_cleanup_namespaces`, après le retrait de l'Application | **Non** |
| Rôle Vault `<p>-<app>-role` (politique `project-<p>-read-policy`) | `_ensure_app_roles` | `deploy.remove`, une erreur est seulement journalisée | **Non** |
| Secrets Vault `project-<p>/<app>` (variables générées, mot de passe Neo4j) | `_ensure_credentials` | **Personne** | Sans objet |
| CNAME Cloudflare des services publics | `dns.py` | `deploy.remove`, une erreur est seulement journalisée | **Non** |
| Images GHCR construites par le workflow d'import | extern-project-analyzer `import.yml` | **Personne** | Sans objet |
| Lignes `deployments`, `import_sources`, services et runs | API | Lignes passées en `deleted` ; la source est supprimée quand tous les namespaces sont partis | Oui |
| Secret de pull `project-<p>/system/registry` | `_ensure_pull_secret` | Partagé par le projet, part avec lui | Sans objet |

Pourquoi ce chemin n'est pas rejouable : `deploy.remove` calcule les apps à
nettoyer depuis le registre (`owned_by_import`). Si le premier passage a déjà
retiré les entrées mais que le nettoyage des namespaces a expiré, le second
passage ne trouve plus rien dans le registre : il ne retente ni les
namespaces, ni les rôles Vault, ni les CNAME. Correctif proposé, à faire dans
`services/imports` (chantier import en cours, non modifié ici) : calculer les
apps depuis les services de la source (`source.services`) quand le registre
ne les porte plus, retenter rôles et CNAME en échec, et supprimer
`project-<p>/<app>` dans Vault. Le contrôle d'orphelins voit déjà ces restes
(namespace, rôle, secret, entrée de registre).

## Projet

Hors du périmètre de ce ticket : CNP-290 traite l'adoption des orphelins au
Retry du bootstrap. Le contrôle signale tout de même ce qu'un projet supprimé
laisse : enregistrement de registre, namespaces portant son label, rôles et
politiques `project-<p>-*`, états `cnp/projects/<p>/`.

## Le contrôle d'orphelins

- `GET /api/orphans` (administrateurs de la plateforme) et
  `python -m app.scripts.list_orphans` dans le pod CMP (code de sortie 1 si
  quelque chose est trouvé ou si une source n'a pas pu être lue).
- Il compare le registre, les namespaces portant `cnp.3istor.com/project` sur
  chaque cloud utilisé, les secrets de `project-<p>`, les rôles des montages
  `kubernetes*`, les politiques ACL et les clés `cnp/projects/` du bucket
  d'états avec les projets et les apps non supprimées de la CMP.
- Il ne supprime jamais rien. Une source illisible apparaît dans `errors` :
  un contrôle qui n'a pas tourné n'est pas un succès.
- Il ne voit pas les images GHCR, les clients Keycloak ni les CNAME : à
  ajouter si ces restes deviennent un problème.
- Droits nécessaires côté Vault pour la politique de la CMP : `list` sur
  `auth/kubernetes*/role` et `sys/policies/acl`. Sans eux, ces deux sources
  sortent en `errors`.
