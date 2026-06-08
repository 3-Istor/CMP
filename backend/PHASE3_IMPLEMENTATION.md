# Phase 3 Implementation Summary

## Overview

Phase 3 adds Kubernetes provider support to the CMP backend, enabling GitOps-based deployments alongside the existing legacy hybrid (OpenStack + AWS) infrastructure.

## Files Created

### Core Services

1. **`app/services/github_service.py`**
   - GitHub App JWT generation and token exchange
   - Repository creation via GitHub API
   - Uses GitHub App ID: 3836905

### Terraform Module

2. **`app/terraform/github_bootstrap/`**
   - `main.tf`: Core resources (GitHub repo, K8s namespace, Vault, ArgoCD)
   - `variables.tf`: Input variables with validation
   - `outputs.tf`: Deployment metadata outputs
   - `templates/values.yaml.tftpl`: GitOps configuration template
   - `templates/Dockerfile`: Placeholder multi-stage Dockerfile
   - `templates/ci.yml.tftpl`: GitHub Actions CI/CD pipeline
   - `README.md`: Module documentation

### Database Migration

3. **`alembic/versions/c4d8f2a91b3e_add_kubernetes_provider_support.py`**
   - Adds `provider_type` enum column
   - Adds `project_id`, `github_repo_url`, `argocd_app_name`, `k8s_namespace`
   - Backward compatible with existing deployments

## Files Modified

### Models

1. **`app/models/deployment.py`**
   - Added `ProviderType` enum (`LEGACY_HYBRID`, `KUBERNETES`)
   - Added Kubernetes-specific fields
   - Reorganized field comments for clarity

### Configuration

2. **`app/core/config.py`**
   - Added `GITHUB_APP_PRIVATE_KEY` setting

3. **`.env.example`**
   - Added GitHub App configuration section with usage instructions

### Orchestration

4. **`app/services/saga_orchestrator.py`**
   - Refactored to support multi-provider routing
   - Added `_run_kubernetes_deployment()` for GitOps flow
   - Added `_execute_terraform_kubernetes()` for Terraform execution
   - Added `_run_terraform_command()` helper
   - Preserved legacy flow in `_run_legacy_hybrid_deployment()`
   - Updated deletion to support both provider types

## Architecture Decisions

### 1. Micro-State Pattern

Each Kubernetes deployment gets its own isolated Terraform state:

```
s3://3-istor-tf-infra-aws/cmp/projects/<project>/<app>.tfstate
```

**Benefits:**

- No state locking conflicts between deployments
- Parallel deployments possible
- Easier rollback and debugging
- Clear ownership boundaries

### 2. Strategy Pattern for Orchestration

The saga orchestrator routes based on `provider_type`:

```python
if deployment.provider_type == ProviderType.KUBERNETES:
    _run_kubernetes_deployment(deployment, db)
else:
    _run_legacy_hybrid_deployment(deployment, db)
```

**Benefits:**

- Clean separation of concerns
- No breaking changes to existing code
- Easy to add new providers in the future

### 3. GitHub App Authentication

Uses short-lived installation tokens instead of PATs:

```python
jwt_token = generate_jwt()  # Valid 10 minutes
installation_token = get_installation_token(installation_id)  # Valid 1 hour
```

**Benefits:**

- Granular permissions (read/write only to specific repos)
- Automatic token rotation
- Native ArgoCD support
- Better security posture

### 4. Terraform as a Library

Terraform is invoked as a subprocess with dynamic configuration:

```python
terraform init -backend-config=key=<dynamic-key>
terraform apply -var-file=<generated-tfvars>
terraform output -json
```

**Benefits:**

- No need for Terraform Cloud/Enterprise
- Full control over execution environment
- Easy to capture outputs and errors
- Works with existing S3 backend

## Data Flow

### Kubernetes Deployment Flow

```
1. User clicks "Deploy" in CMP UI
   ↓
2. FastAPI creates Deployment record (status: PENDING)
   ↓
3. Background task starts saga_orchestrator.run_deployment()
   ↓
4. Orchestrator detects provider_type == KUBERNETES
   ↓
5. Fetch GitHub installation_id from app_config
   ↓
6. Exchange for installation token via github_service
   ↓
7. Generate Terraform tfvars with dynamic values
   ↓
8. Execute terraform init with dynamic S3 state key
   ↓
9. Execute terraform apply (creates GitHub repo, K8s namespace, Vault, ArgoCD)
   ↓
10. Parse terraform outputs (github_repo_url, argocd_app_name, k8s_namespace)
   ↓
11. Update Deployment record with outputs (status: RUNNING)
   ↓
12. ArgoCD detects new Application CRD and syncs from GitHub
```

### Deletion Flow

```
1. User clicks "Delete" in CMP UI
   ↓
2. Background task starts saga_orchestrator.run_deletion()
   ↓
3. Orchestrator detects provider_type == KUBERNETES
   ↓
4. Execute terraform init with same S3 state key
   ↓
5. Execute terraform destroy
   ↓
6. Update Deployment record (status: DELETED)
```

## Security Considerations

1. **GitHub App Private Key**: Stored in environment variable, never in database
2. **Installation Tokens**: Generated on-demand, never persisted
3. **Vault Secrets**: Auto-generated with strong randomness (32+ chars)
4. **Kubernetes RBAC**: Bound to specific ServiceAccounts and namespaces
5. **Terraform State**: Encrypted at rest in S3, locked via DynamoDB

## Testing Checklist

- [ ] Database migration runs successfully
- [ ] GitHub JWT generation works with test key
- [ ] Installation token exchange succeeds
- [ ] Terraform module validates (terraform validate)
- [ ] Kubernetes deployment creates all resources
- [ ] ArgoCD Application CRD is created correctly
- [ ] Vault secrets are accessible to pods
- [ ] Deletion removes all resources cleanly
- [ ] Legacy deployments still work (backward compatibility)

## Known Limitations

1. **GitHub App Installation**: Must be done manually by users (Phase 4 will add UI)
2. **ArgoCD Health Monitoring**: Not yet integrated (Phase 4)
3. **Template Repository**: Uses inline templates, not actual Git template repos
4. **Rollback**: Terraform destroy only, no partial rollback yet

## Next Phase (Phase 4)

Frontend refactoring to support:

- Dual catalog view (IaaS vs PaaS)
- GitHub account linking UI
- Dynamic deployment cards based on provider_type
- ArgoCD health status integration
- Day-2 operations (scaling, SSO toggle)
