# Phase 3 Migration Guide: Kubernetes Provider Support

This document explains the changes made in Phase 3 and how to use the new Kubernetes provider.

## What Changed

### 1. Database Schema (`deployment.py`)

Added new fields to support multi-provider deployments:

- `provider_type`: Enum discriminator (`LEGACY_HYBRID` or `KUBERNETES`)
- `project_id`: Links to Keycloak project groups
- `github_repo_url`: The provisioned GitHub repository URL
- `argocd_app_name`: The ArgoCD Application CRD name
- `k8s_namespace`: The Kubernetes namespace

### 2. GitHub Service (`github_service.py`)

New service for GitHub App integration:

- `generate_jwt()`: Creates signed JWT for GitHub App authentication
- `get_installation_token()`: Exchanges JWT for installation access token
- `create_repository()`: Creates private repositories via GitHub API

### 3. Terraform Module (`terraform/github_bootstrap/`)

Day-0 provisioning module that creates:

- GitHub repository with template code
- Kubernetes namespace with proper labels
- Vault secrets and Kubernetes auth roles
- ArgoCD Application CRD

### 4. Saga Orchestrator Refactoring

The orchestrator now routes deployments based on `provider_type`:

- **LEGACY_HYBRID**: OpenStack VMs + AWS ASG (existing flow)
- **KUBERNETES**: GitHub + Terraform + ArgoCD (new flow)

## Running the Migration

### Step 1: Apply Database Migration

```bash
cd backend
alembic upgrade head
```

This adds the new columns to the `deployments` table. Existing deployments will default to `LEGACY_HYBRID`.

### Step 2: Configure GitHub App

1. Obtain the CNP GitHub App private key from Vault or GitHub settings
2. Add to your `.env` file:

```bash
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"
```

**Important**: The key must be on a single line with `\n` for newlines, or use multi-line format as shown above.

### Step 3: Configure S3 Backend (if not already done)

The Kubernetes provider uses dynamic S3 state keys:

```
s3://3-istor-tf-infra-aws/cmp/projects/<project>/<app>.tfstate
```

Ensure your `.env` has:

```bash
TF_BACKEND_S3_ENABLED=true
TF_BACKEND_AWS_ACCESS_KEY_ID=your_key
TF_BACKEND_AWS_SECRET_ACCESS_KEY=your_secret
TF_BACKEND_S3_BUCKET=3-istor-tf-infra-aws
TF_BACKEND_S3_DYNAMODB_TABLE=terraform-state-lock
```

### Step 4: Test Kubernetes Deployment

Create a test deployment via the API:

```bash
curl -X POST http://localhost:8000/api/deployments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-app",
    "template_id": "kubernetes-fastapi",
    "provider_type": "kubernetes",
    "app_config": {
      "project_name": "test-project",
      "github_installation_id": "12345678",
      "replica_count": 2,
      "sso_protected": false
    }
  }'
```

## Backward Compatibility

All existing deployments continue to work without changes:

- Legacy deployments use `provider_type: LEGACY_HYBRID` (default)
- The old SAGA flow (OpenStack → AWS) is preserved in `_run_legacy_hybrid_deployment()`
- No breaking changes to existing API endpoints

## Troubleshooting

### "GitHub App Private Key not configured"

Ensure `GITHUB_APP_PRIVATE_KEY` is set in `.env` and the FastAPI server is restarted.

### "Terraform module not found"

Verify the module exists at `backend/app/terraform/github_bootstrap/`.

### "Failed to get installation token"

Check that:

1. The GitHub App ID is correct (3836905)
2. The private key matches the GitHub App
3. The `github_installation_id` is valid

### "State locking failed"

Ensure the DynamoDB table `terraform-state-lock` exists in your AWS account.

## Next Steps (Phase 4)

- Update the frontend to support the dual catalog view
- Add GitHub account linking UI in the Account page
- Implement dynamic deployment cards based on `provider_type`
- Add ArgoCD health monitoring integration
