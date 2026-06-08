# Phase 3 Quick Start Guide

Get the Kubernetes provider up and running in 5 minutes.

## Prerequisites

- Python 3.11+
- Terraform 1.5+
- Access to the CNP GitHub App (ID: 3836905)
- AWS credentials for S3 backend
- Running K3s cluster with ArgoCD

## Step 1: Apply Database Migration

```bash
cd backend
alembic upgrade head
```

**Expected output**:

```
INFO  [alembic.runtime.migration] Running upgrade b9751e077ee4 -> c4d8f2a91b3e, add_kubernetes_provider_support
```

## Step 2: Configure GitHub App

Get the private key from GitHub or Vault, then add to `.env`:

```bash
# Option A: Single line with \n
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"

# Option B: Multi-line (recommended)
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
...
-----END RSA PRIVATE KEY-----"
```

## Step 3: Install Dependencies

```bash
cd backend
poetry install
```

## Step 4: Test GitHub Integration

```bash
poetry run python test_github_service.py
```

**Expected output**:

```
🔐 Testing JWT generation...
✅ JWT generated successfully (length: 267)
```

## Step 5: Validate Terraform Module

```bash
cd app/terraform/github_bootstrap
terraform init
terraform validate
```

**Expected output**:

```
Success! The configuration is valid.
```

## Step 6: Create Test Deployment

### Option A: Via Python

```python
# Run with: poetry run python
from app.models.deployment import Deployment, ProviderType, DeploymentStatus
from app.core.database import SessionLocal

db = SessionLocal()

deployment = Deployment(
    name="test-app",
    template_id="kubernetes-fastapi",
    provider_type=ProviderType.KUBERNETES,
    status=DeploymentStatus.PENDING,
    project_id="test-project",
    app_config='{"project_name": "test-project", "github_installation_id": "12345678", "replica_count": 2}'
)

db.add(deployment)
db.commit()
print(f"Created deployment ID: {deployment.id}")
```

### Option B: Via API

```bash
curl -X POST http://localhost:8000/api/deployments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-app",
    "template_id": "kubernetes-fastapi",
    "provider_type": "kubernetes",
    "app_config": {
      "project_name": "test-project",
      "github_installation_id": "YOUR_INSTALLATION_ID",
      "replica_count": 2,
      "sso_protected": false
    }
  }'
```

## Step 7: Monitor Deployment

Watch the deployment progress:

```bash
# Check deployment status
curl http://localhost:8000/api/deployments/{deployment_id}

# Check logs
tail -f logs/cmp.log | grep "deployment_id"
```

**Expected flow**:

1. `PENDING` → Initial state
2. `DEPLOYING` → "🔐 Authenticating with GitHub App..."
3. `DEPLOYING` → "🛠️ Bootstrapping infrastructure with Terraform..."
4. `RUNNING` → "✅ Running - ArgoCD syncing from https://github.com/..."

## Troubleshooting

### "GITHUB_APP_PRIVATE_KEY not configured"

```bash
# Verify the key is loaded
python -c "from app.core.config import settings; print('Key loaded:', bool(settings.GITHUB_APP_PRIVATE_KEY))"
```

### "Terraform module not found"

```bash
# Verify module exists
ls -la app/terraform/github_bootstrap/
```

### "Failed to get installation token"

```bash
# Test JWT generation
python test_github_service.py
```

### "State locking failed"

```bash
# Verify DynamoDB table exists
aws dynamodb describe-table --table-name terraform-state-lock
```

## Verify Success

After deployment completes, verify:

1. **GitHub Repository Created**:

   ```bash
   # Check the github_repo_url in deployment record
   curl http://localhost:8000/api/deployments/{id} | jq .github_repo_url
   ```

2. **Kubernetes Namespace Created**:

   ```bash
   kubectl get namespace test-project-test-app
   ```

3. **ArgoCD Application Created**:

   ```bash
   kubectl get application -n argocd test-project-test-app
   ```

4. **Vault Secrets Created**:
   ```bash
   vault kv list kvv2/projects/test-project/test-app
   ```

## Next Steps

- Install the GitHub App on your organization
- Get the installation ID from the callback URL
- Create a real deployment with your installation ID
- Monitor ArgoCD sync status
- Proceed to Phase 4 (Frontend refactoring)

## Rollback

If you need to rollback:

```bash
# Rollback database
alembic downgrade -1

# Remove test deployment
curl -X DELETE http://localhost:8000/api/deployments/{id}
```

## Support

- **Implementation Details**: See `PHASE3_IMPLEMENTATION.md`
- **Migration Guide**: See `PHASE3_MIGRATION.md`
- **Full Summary**: See `PHASE3_SUMMARY.md`
- **Terraform Module**: See `app/terraform/github_bootstrap/README.md`
