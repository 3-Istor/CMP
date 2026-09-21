# GitHub Bootstrap Terraform Module

This Terraform module handles Day-0 provisioning for Kubernetes-based applications in the CNP platform.

## What It Does

1. **GitHub Repository Creation**: Creates a private repository with initial template code
2. **Kubernetes Namespace**: Provisions an isolated namespace with proper labels
3. **Vault Secrets**: Creates KV paths and Kubernetes auth roles for secret injection
4. **ArgoCD Registration**: Configures ArgoCD to sync the application from the GitHub repo

## Usage

This module is invoked automatically by the CMP Backend's `saga_orchestrator.py` when a developer requests a Kubernetes deployment.

### Dynamic State Management

Each application gets its own isolated Terraform state file in S3:

```
s3://3-istor-tf-infra-aws/cmp/projects/<project-name>/<app-name>.tfstate
```

This prevents state locking conflicts and allows parallel deployments.

### Required Variables

- `project_name`: The CNP project (used for namespace prefix and RBAC)
- `app_name`: The application name
- `github_installation_id`: From the user's Keycloak profile
- `github_app_private_key`: The CNP GitHub App private key (from Vault)

### Optional Variables

- `replica_count`: Initial pod count (default: 2)
- `sso_protected`: Enable Keycloak SSO via Envoy Gateway (default: false)

## Outputs

- `github_repo_url`: The HTTPS URL of the created repository
- `k8s_namespace`: The Kubernetes namespace name
- `argocd_app_name`: The ArgoCD Application CRD name
- `vault_path`: The Vault KV path for secrets

## Template Files

- `templates/values.yaml.tftpl`: The GitOps configuration file
- `templates/Dockerfile`: A placeholder multi-stage Dockerfile
- `templates/ci.yml.tftpl`: GitHub Actions CI/CD pipeline

## Security Notes

- The GitHub App token is short-lived (1 hour) and never stored
- Vault secrets are auto-generated with strong randomness
- Kubernetes auth is bound to specific ServiceAccounts and namespaces
- ArgoCD uses GitHub App credentials (not PATs) for repository access
