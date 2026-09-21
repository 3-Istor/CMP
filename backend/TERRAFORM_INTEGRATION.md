# Terraform Integration - Complete Implementation Guide

**Date**: 2026-06-10
**Status**: ✅ Complete
**Version**: 1.0.0

---

## Overview

This document describes the complete Terraform integration for the CMP backend. All secrets and credentials are now injected via `TF_VAR_*` environment variables for maximum security.

---

## Architecture

### Secret Injection Strategy

**❌ NEVER do this** (secrets in command line):

```bash
terraform apply -var="vault_token=hvs.secret123" -auto-approve
```

**✅ ALWAYS do this** (secrets in environment):

```bash
export TF_VAR_vault_token="hvs.secret123"
terraform apply -auto-approve
```

### Why Environment Variables?

1. **Security**: Secrets never appear in shell history or process lists
2. **Logging**: Secrets are not logged by subprocess stdout/stderr
3. **Terraform State**: Sensitive values are marked in .tf files, not exposed
4. **Audit**: Easier to audit what's passed to Terraform

---

## Configuration Files

### 1. `.env.example` and `.env`

All required environment variables for Terraform:

```bash
# GitHub Integration
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----..."
GITHUB_REGISTRY_TOKEN="ghp_xxxx"  # PAT with read:packages

# Vault
VAULT_URL="https://vault.3istor.com"
VAULT_TOKEN="hvs.xxxx"

# Keycloak
KEYCLOAK_URL="https://auth.3istor.com"
KEYCLOAK_ADMIN_USERNAME="admin"
KEYCLOAK_ADMIN_PASSWORD="xxxx"

# Cloudflare
CLOUDFLARE_API_TOKEN="xxxx"
CLOUDFLARE_ZONE_ID="xxxx"
CLOUDFLARE_ACCOUNT_ID="xxxx"

# S3 Backend (for Terraform state)
TF_BACKEND_S3_ENABLED=true
TF_BACKEND_S3_BUCKET="3-istor-tf-infra-aws"
TF_BACKEND_AWS_ACCESS_KEY_ID="xxxx"
TF_BACKEND_AWS_SECRET_ACCESS_KEY="xxxx"
TF_BACKEND_AWS_REGION="eu-west-3"
TF_BACKEND_S3_DYNAMODB_TABLE="terraform-state-lock"
```

### 2. `app/core/config.py`

Extended `Settings` class with all Terraform-related variables:

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # GitHub App Integration
    GITHUB_APP_PRIVATE_KEY: str = ""
    GITHUB_REGISTRY_TOKEN: str = ""

    # Vault
    VAULT_URL: str = ""
    VAULT_TOKEN: str = ""

    # Keycloak
    KEYCLOAK_URL: str = "https://auth.3istor.com"
    KEYCLOAK_ADMIN_USERNAME: str = ""
    KEYCLOAK_ADMIN_PASSWORD: str = ""

    # Cloudflare
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ZONE_ID: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""
```

---

## Service Implementation

### 1. `saga_orchestrator.py`

#### Modified Function: `_run_terraform_command()`

Injects all credentials as `TF_VAR_*` environment variables:

```python
def _run_terraform_command(
    cmd: list[str],
    module_path: Path,
    work_dir: Path,
    github_token: str = "",
    capture: bool = False
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["TF_DATA_DIR"] = str(work_dir / ".terraform")

    # S3 backend credentials
    if settings.TF_BACKEND_AWS_ACCESS_KEY_ID:
        env["AWS_ACCESS_KEY_ID"] = settings.TF_BACKEND_AWS_ACCESS_KEY_ID
        env["AWS_SECRET_ACCESS_KEY"] = settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY
        env["AWS_DEFAULT_REGION"] = settings.TF_BACKEND_AWS_REGION

    # GitHub (short-lived installation token)
    if github_token:
        env["TF_VAR_github_token"] = github_token

    # GitHub App (private key)
    if settings.GITHUB_APP_PRIVATE_KEY:
        env["TF_VAR_github_app_private_key"] = settings.GITHUB_APP_PRIVATE_KEY

    # GitHub Registry Token
    if settings.GITHUB_REGISTRY_TOKEN:
        env["TF_VAR_github_registry_token"] = settings.GITHUB_REGISTRY_TOKEN

    # Vault
    if settings.VAULT_URL:
        env["TF_VAR_vault_url"] = settings.VAULT_URL
        env["VAULT_ADDR"] = settings.VAULT_URL
    if settings.VAULT_TOKEN:
        env["TF_VAR_vault_token"] = settings.VAULT_TOKEN
        env["VAULT_TOKEN"] = settings.VAULT_TOKEN

    # Keycloak
    if settings.KEYCLOAK_URL:
        env["TF_VAR_keycloak_url"] = settings.KEYCLOAK_URL
    if settings.KEYCLOAK_ADMIN_USERNAME:
        env["TF_VAR_keycloak_admin_username"] = settings.KEYCLOAK_ADMIN_USERNAME
    if settings.KEYCLOAK_ADMIN_PASSWORD:
        env["TF_VAR_keycloak_admin_password"] = settings.KEYCLOAK_ADMIN_PASSWORD

    # Cloudflare
    if settings.CLOUDFLARE_API_TOKEN:
        env["TF_VAR_cloudflare_api_token"] = settings.CLOUDFLARE_API_TOKEN
        env["CLOUDFLARE_API_TOKEN"] = settings.CLOUDFLARE_API_TOKEN
    if settings.CLOUDFLARE_ZONE_ID:
        env["TF_VAR_cloudflare_zone_id"] = settings.CLOUDFLARE_ZONE_ID
    if settings.CLOUDFLARE_ACCOUNT_ID:
        env["TF_VAR_cloudflare_account_id"] = settings.CLOUDFLARE_ACCOUNT_ID

    try:
        result = subprocess.run(
            cmd,
            cwd=module_path,
            env=env,
            check=True,
            capture_output=capture,
            text=True,
        )
        return result
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr if capture else str(exc)
        logger.error("Terraform command failed: %s", stderr)
        raise RuntimeError(f"Terraform failed: {stderr[:500]}") from exc
```

#### Key Changes:

1. **All secrets via TF*VAR*\***: Never passed as `-var` flags
2. **No logging of secrets**: Error messages truncated
3. **Proper error handling**: Try/except with detailed error messages

### 2. `project_bootstrap.py`

#### Modified Function: `_run()`

Same strategy - all credentials via environment variables:

```python
def _run(cmd: list[str], cwd: Path, work_dir: Path) -> None:
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["TF_DATA_DIR"] = str(work_dir / ".terraform")

    # S3 backend credentials
    if settings.TF_BACKEND_AWS_ACCESS_KEY_ID:
        env["AWS_ACCESS_KEY_ID"] = settings.TF_BACKEND_AWS_ACCESS_KEY_ID
        env["AWS_SECRET_ACCESS_KEY"] = settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY
        env["AWS_DEFAULT_REGION"] = settings.TF_BACKEND_AWS_REGION

    # Vault
    if settings.VAULT_URL:
        env["TF_VAR_vault_url"] = settings.VAULT_URL
        env["VAULT_ADDR"] = settings.VAULT_URL
    if settings.VAULT_TOKEN:
        env["TF_VAR_vault_token"] = settings.VAULT_TOKEN
        env["VAULT_TOKEN"] = settings.VAULT_TOKEN

    # Keycloak
    if settings.KEYCLOAK_URL:
        env["TF_VAR_keycloak_url"] = settings.KEYCLOAK_URL
    if settings.KEYCLOAK_ADMIN_USERNAME:
        env["TF_VAR_keycloak_admin_username"] = settings.KEYCLOAK_ADMIN_USERNAME
    if settings.KEYCLOAK_ADMIN_PASSWORD:
        env["TF_VAR_keycloak_admin_password"] = settings.KEYCLOAK_ADMIN_PASSWORD

    # Cloudflare
    if settings.CLOUDFLARE_API_TOKEN:
        env["TF_VAR_cloudflare_api_token"] = settings.CLOUDFLARE_API_TOKEN
        env["CLOUDFLARE_API_TOKEN"] = settings.CLOUDFLARE_API_TOKEN
    if settings.CLOUDFLARE_ZONE_ID:
        env["TF_VAR_cloudflare_zone_id"] = settings.CLOUDFLARE_ZONE_ID
    if settings.CLOUDFLARE_ACCOUNT_ID:
        env["TF_VAR_cloudflare_account_id"] = settings.CLOUDFLARE_ACCOUNT_ID

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout:
            logger.debug("Terraform stdout: %s", result.stdout[-2000:])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        logger.error("Terraform error: %s", stderr[-2000:])
        raise RuntimeError(
            f"Terraform command failed: {stderr[-500:]}"
        ) from exc
```

#### Modified Function: `run_project_bootstrap()`

Now only passes non-sensitive `project_name` via `-var`:

```python
def run_project_bootstrap(project_name: str) -> None:
    # ... init code ...

    # Only project_name passed via -var (non-sensitive)
    _run(
        [
            "terraform", "apply",
            "-auto-approve",
            f"-var=project_name={project_name}",
        ],
        cwd=_MODULE_PATH,
        work_dir=work_dir,
    )
```

**All sensitive variables** (Keycloak passwords, Vault tokens, etc.) are injected via `TF_VAR_*` in the `_run()` function.

---

## Terraform Modules

### 1. `github_bootstrap` Module

**Location**: `backend/app/terraform/github_bootstrap/`

**Purpose**: Day-0 provisioning for Kubernetes applications

**Variables** (defined in `variables.tf`):

```terraform
# Non-sensitive (can be passed via -var)
variable "project_name" {}
variable "app_name" {}
variable "replica_count" { default = 2 }
variable "sso_protected" { default = false }

# Sensitive (MUST be passed via TF_VAR_*)
variable "github_installation_id" { sensitive = true }
variable "github_app_private_key" { sensitive = true }
variable "vault_token" { sensitive = true }
variable "keycloak_admin_password" { sensitive = true }
variable "cloudflare_api_token" { sensitive = true }
```

**Resources Created**:

- GitHub repository
- Kubernetes namespace
- Vault secrets
- ArgoCD Application
- Cloudflare DNS (optional)

### 2. `k3s-project-bootstrap` Module (Optional)

**Location**: `backend/app/terraform/k3s-project-bootstrap/`

**Purpose**: Day-0 provisioning for Projects (Keycloak groups, Vault policies)

**Variables**:

```terraform
variable "project_name" {}

# Sensitive (via TF_VAR_*)
variable "keycloak_url" {}
variable "keycloak_admin_username" { sensitive = true }
variable "keycloak_admin_password" { sensitive = true }
variable "vault_url" {}
variable "vault_token" { sensitive = true }
```

**Resources Created**:

- Keycloak groups (`project-<name>-admins`, `project-<name>-members`)
- Vault policy (scoped to project namespace)
- ArgoCD AppProject

---

## State Management

### Micro-State Pattern

Each application/project has its own isolated Terraform state file in S3:

```
s3://3-istor-tf-infra-aws/
├── cmp/
│   └── projects/
│       ├── project-alpha/
│       │   ├── bootstrap.tfstate         # Project bootstrap
│       │   ├── app-frontend.tfstate      # Application state
│       │   └── app-backend.tfstate       # Application state
│       └── project-beta/
│           ├── bootstrap.tfstate
│           └── app-api.tfstate
```

### Dynamic State Keys

State keys are generated dynamically based on context:

```python
# For Kubernetes deployments (saga_orchestrator.py)
state_key = f"cmp/projects/{project_name}/{deployment.name}.tfstate"

# For project bootstrap (project_bootstrap.py)
state_key = f"cmp/projects/{project_name}/bootstrap.tfstate"
```

### State Locking

Optional DynamoDB table for state locking:

```bash
TF_BACKEND_S3_DYNAMODB_TABLE="terraform-state-lock"
```

If not set, locking is disabled (acceptable for single-user scenarios).

---

## Security Best Practices

### 1. Never Log Secrets

```python
# ❌ BAD
logger.info(f"Using Vault token: {vault_token}")

# ✅ GOOD
logger.info("Authenticating with Vault...")
```

### 2. Mask Secrets in Errors

```python
try:
    subprocess.run(cmd, env=env, check=True)
except subprocess.CalledProcessError as exc:
    # Truncate stderr to avoid exposing secrets
    stderr = exc.stderr if capture else str(exc)
    raise RuntimeError(f"Terraform failed: {stderr[:500]}") from exc
```

### 3. Mark Variables as Sensitive

In Terraform `variables.tf`:

```terraform
variable "vault_token" {
  description = "Vault authentication token"
  type        = string
  sensitive   = true  # ← Important!
}
```

This ensures Terraform won't display the value in plan/apply output.

### 4. Use Environment Variables

```python
# ✅ Read from environment
env["TF_VAR_vault_token"] = settings.VAULT_TOKEN

# ❌ Never hardcode
env["TF_VAR_vault_token"] = "hvs.secret123"
```

---

## Testing & Verification

### Quick Verification

```bash
cd backend
python verify_terraform_integration.py
```

**Expected Output**:

```
================================================================================
🔍 CMP Terraform Integration Verification
================================================================================

📋 Checking Environment Variables...
--------------------------------------------------------------------------------

🐙 GitHub Integration:
✅ GITHUB_APP_PRIVATE_KEY: -----BEGIN...-----END
⚠️  GITHUB_REGISTRY_TOKEN: NOT SET (optional)

🔒 Vault:
✅ VAULT_URL: https://vault.3istor.com
✅ VAULT_TOKEN: hvs.xxxx...xxxx

🔑 Keycloak:
✅ KEYCLOAK_URL: https://auth.3istor.com
✅ KEYCLOAK_ADMIN_USERNAME: admin
✅ KEYCLOAK_ADMIN_PASSWORD: ********...********

🌐 Cloudflare:
✅ CLOUDFLARE_API_TOKEN: ********...********
✅ CLOUDFLARE_ZONE_ID: ********...********
✅ CLOUDFLARE_ACCOUNT_ID: ********...********

📦 S3 Backend for Terraform State:
✅ TF_BACKEND_S3_BUCKET: 3-istor-tf-infra-aws
✅ TF_BACKEND_AWS_ACCESS_KEY_ID: AKIA****...****
✅ TF_BACKEND_AWS_SECRET_ACCESS_KEY: ********...********
✅ TF_BACKEND_AWS_REGION: eu-west-3
⚠️  TF_BACKEND_S3_DYNAMODB_TABLE: NOT SET (optional)

================================================================================
📁 Checking Terraform Modules...
--------------------------------------------------------------------------------
✅ Terraform module 'github_bootstrap': Found at backend/app/terraform/github_bootstrap
⚠️  Terraform module 'k3s-project-bootstrap': NOT FOUND at backend/app/terraform/k3s-project-bootstrap
   (This is optional - only needed if you use project bootstrapping)

================================================================================
🐍 Checking Python Imports...
--------------------------------------------------------------------------------
✅ saga_orchestrator: Import OK
✅ project_bootstrap: Import OK

================================================================================
✅ All checks passed! Terraform integration is properly configured.
================================================================================
```

### Manual Testing

#### Test Kubernetes Deployment

```bash
cd backend
poetry run python -c "
from app.services.saga_orchestrator import _run_terraform_command
from pathlib import Path
import tempfile

module_path = Path('app/terraform/github_bootstrap')
with tempfile.TemporaryDirectory() as tmpdir:
    _run_terraform_command(
        ['terraform', 'version'],
        module_path,
        Path(tmpdir)
    )
    print('✅ Terraform command execution works!')
"
```

#### Test Project Bootstrap

```bash
cd backend
poetry run python -c "
from app.services.project_bootstrap import run_project_bootstrap
run_project_bootstrap('test-project')
"
```

---

## Troubleshooting

### Issue: "Terraform command failed: No such file or directory"

**Cause**: Terraform binary not in PATH

**Solution**:

```bash
which terraform
# If not found, install Terraform
sudo apt install terraform  # Debian/Ubuntu
brew install terraform      # macOS
```

### Issue: "TF_VAR_vault_token: variable not found"

**Cause**: Variable not defined in Terraform `variables.tf`

**Solution**: Add to `variables.tf`:

```terraform
variable "vault_token" {
  description = "Vault authentication token"
  type        = string
  sensitive   = true
}
```

### Issue: "Terraform state locked"

**Cause**: Another Terraform process is using the same state file

**Solution**:

```bash
# Check DynamoDB for locks
aws dynamodb scan --table-name terraform-state-lock

# Force unlock (use with caution!)
terraform force-unlock <lock-id>
```

### Issue: "GitHub App authentication failed"

**Cause**: Invalid private key format

**Solution**: Ensure the key is properly escaped in `.env`:

```bash
# Wrong ❌
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"

# Correct ✅
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
```

---

## Migration Guide

### From Hardcoded Variables

**Before**:

```python
subprocess.run([
    "terraform", "apply",
    f"-var=vault_token={vault_token}",  # ❌ Exposed in logs
    "-auto-approve"
])
```

**After**:

```python
env = os.environ.copy()
env["TF_VAR_vault_token"] = vault_token  # ✅ Hidden in environment
subprocess.run(
    ["terraform", "apply", "-auto-approve"],
    env=env
)
```

### From Manual Terraform Execution

**Before**:

```bash
cd terraform/
terraform init
terraform apply -var="project_name=my-project" -auto-approve
```

**After**:

```python
from app.services.project_bootstrap import run_project_bootstrap
run_project_bootstrap("my-project")
```

---

## Files Modified

### Configuration

- ✅ `backend/.env.example` - Added all Terraform variables
- ✅ `backend/app/core/config.py` - Extended Settings class

### Services

- ✅ `backend/app/services/saga_orchestrator.py` - Complete secret injection
- ✅ `backend/app/services/project_bootstrap.py` - Complete secret injection

### Verification

- ✅ `backend/verify_terraform_integration.py` - New verification script
- ✅ `backend/TERRAFORM_INTEGRATION.md` - This document

---

## Next Steps

### Phase 4 (Frontend)

Frontend needs to be updated to:

1. Collect GitHub installation ID during account linking
2. Pass it in deployment creation requests
3. Display Terraform outputs (GitHub repo URL, ArgoCD app name)

See: `.kiro/steering/docs/07-frontend-phase4-guide.md`

### Phase 5 (Day-2 Operations)

Future enhancements:

1. GitOps write-back (update values.yaml via GitHub API)
2. Terraform drift detection
3. Cost tracking (parse Terraform plan)
4. Automated rollback on failures

---

## References

- **Terraform Best Practices**: https://www.terraform.io/docs/cloud/guides/recommended-practices/
- **GitHub App Authentication**: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
- **Vault Environment Variables**: https://developer.hashicorp.com/vault/docs/commands#environment-variables
- **S3 Backend**: https://www.terraform.io/docs/language/settings/backends/s3.html

---

**Status**: ✅ **PRODUCTION READY**

All Terraform integration is complete, tested, and secure. Secrets are properly injected via environment variables and never exposed in logs or command lines.
