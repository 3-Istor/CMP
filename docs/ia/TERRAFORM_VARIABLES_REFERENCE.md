# Terraform Variables Reference

**Quick reference for all Terraform template variables**

---

## Template: k3s-project-bootstrap

**Purpose**: Create a new project with Keycloak groups and Vault paths

**Required Variables**:

| Variable                  | Type   | Source                    | Default                         | Sensitive |
| ------------------------- | ------ | ------------------------- | ------------------------------- | --------- |
| `project_name`            | string | Dynamic                   | -                               | ❌        |
| `keycloak_url`            | string | `KEYCLOAK_URL`            | `https://admin-auth.3istor.com` | ❌        |
| `keycloak_admin_username` | string | `KEYCLOAK_ADMIN_USERNAME` | `admin`                         | ❌        |
| `keycloak_admin_password` | string | `KEYCLOAK_ADMIN_PASSWORD` | -                               | ✅        |
| `vault_url`               | string | `VAULT_URL`               | `https://vault.3istor.com`      | ❌        |
| `vault_token`             | string | `VAULT_TOKEN`             | -                               | ✅        |

**Optional Variables**:

| Variable              | Type   | Default                     | Description                        |
| --------------------- | ------ | --------------------------- | ---------------------------------- |
| `project_description` | string | `"Managed by CNP Platform"` | Human-readable project description |
| `keycloak_realm`      | string | `"3istor"`                  | Keycloak realm name                |

**Manual Test**:

```bash
cd backend/data/templates/templates/k3s-project-bootstrap
terraform apply \
  -var="project_name=sandbox" \
  -var="keycloak_admin_password=$KEYCLOAK_ADMIN_PASSWORD" \
  -var="vault_token=$VAULT_TOKEN"
```

---

## Template: k3s-gitops-app

**Purpose**: Create a GitOps-managed application with GitHub repo, ArgoCD, Vault secrets, and Cloudflare DNS

**Required Variables**:

| Variable                  | Type   | Source                    | Default | Sensitive |
| ------------------------- | ------ | ------------------------- | ------- | --------- |
| `github_token`            | string | Generated via GitHub App  | -       | ✅        |
| `github_owner`            | string | Dynamic                   | -       | ❌        |
| `template_repo_name`      | string | Dynamic                   | -       | ❌        |
| `app_name`                | string | Dynamic                   | -       | ❌        |
| `project_name`            | string | Dynamic                   | -       | ❌        |
| `vault_token`             | string | `VAULT_TOKEN`             | -       | ✅        |
| `github_registry_token`   | string | `GITHUB_REGISTRY_TOKEN`   | -       | ✅        |
| `cloudflare_api_token`    | string | `CLOUDFLARE_API_TOKEN`    | -       | ✅        |
| `cloudflare_account_id`   | string | `CLOUDFLARE_ACCOUNT_ID`   | -       | ❌        |
| `cloudflare_zone_id`      | string | `CLOUDFLARE_ZONE_ID`      | -       | ❌        |
| `keycloak_admin_password` | string | `KEYCLOAK_ADMIN_PASSWORD` | -       | ✅        |

**Optional Variables (with defaults)**:

| Variable                   | Type   | Default                           | Description                                |
| -------------------------- | ------ | --------------------------------- | ------------------------------------------ |
| `app_type`                 | string | `"static"`                        | Application type (`static` or `fullstack`) |
| `github_registry_username` | string | `"3-Istor"`                       | GitHub username for image registry         |
| `keycloak_realm`           | string | `"3istor"`                        | Keycloak realm name                        |
| `keycloak_url`             | string | `"https://admin-auth.3istor.com"` | Keycloak server URL                        |
| `keycloak_admin_username`  | string | `"admin"`                         | Keycloak admin username                    |
| `vault_url`                | string | `"https://vault.3istor.com"`      | Vault server URL                           |

**Manual Test**:

```bash
cd backend/data/templates/templates/k3s-gitops-app
terraform apply \
  -var="github_token=$GITHUB_TOKEN" \
  -var="github_owner=3-Istor" \
  -var="template_repo_name=template-html-css" \
  -var="app_name=test-app" \
  -var="project_name=sandbox" \
  -var="vault_token=$VAULT_TOKEN" \
  -var="github_registry_token=$GITHUB_REGISTRY_TOKEN" \
  -var="cloudflare_api_token=$CLOUDFLARE_API_TOKEN" \
  -var="cloudflare_account_id=$CLOUDFLARE_ACCOUNT_ID" \
  -var="cloudflare_zone_id=$CLOUDFLARE_ZONE_ID" \
  -var="keycloak_admin_password=$KEYCLOAK_ADMIN_PASSWORD"
```

---

## Environment Variables Mapping

**How variables flow from `.env` to Terraform:**

```
.env file
  ↓
app/core/config.py (Settings class)
  ↓
app/services/terraform_executor.py (_run_command)
  ↓
TF_VAR_* environment variables
  ↓
Terraform (variables.tf)
```

**Example**:

```bash
# In .env
CLOUDFLARE_API_TOKEN="cfut_vbnBh..."

# In app/core/config.py
class Settings(BaseSettings):
    CLOUDFLARE_API_TOKEN: str = Field(default="")

# In app/services/terraform_executor.py
if settings.CLOUDFLARE_API_TOKEN:
    env["TF_VAR_cloudflare_api_token"] = settings.CLOUDFLARE_API_TOKEN

# In Terraform variables.tf
variable "cloudflare_api_token" {
  type = string
  sensitive = true
}
```

---

## Configuration Checklist

### Required .env Variables

```bash
# Core Infrastructure
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=wHf...
AWS_DEFAULT_REGION=eu-west-3
OS_AUTH_URL=http://192.168.1.210:5000
OS_USERNAME=admin
OS_PASSWORD=CZa...
OS_PROJECT_NAME=3-istor-cloud

# Cloudflare (for DNS)
CLOUDFLARE_API_TOKEN=cfut_vbnBh...
CLOUDFLARE_ZONE_ID=5984eff5179d6656a7c9e1b00c768d21
CLOUDFLARE_ACCOUNT_ID=b522d6f1d77b7df478edbbf07ef7d5c4

# Keycloak (for SSO)
KEYCLOAK_URL=https://auth.3istor.com
KEYCLOAK_CLIENT_ID=3-istor-openid
KEYCLOAK_CLIENT_SECRET=Y1aUBf71Bf...
KEYCLOAK_ADMIN_USERNAME=admin
KEYCLOAK_ADMIN_PASSWORD=m2wRadiKHT...

# Vault (for secrets)
VAULT_URL=https://vault.3istor.com
VAULT_TOKEN=hvs.tue4Sv...

# GitHub (for repos and image registry)
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----..."
GITHUB_REGISTRY_TOKEN=ghp_BLlwK7...

# Terraform Backend (optional but recommended)
TF_BACKEND_S3_ENABLED=true
TF_BACKEND_S3_BUCKET=3-istor-tf-infra-aws
TF_BACKEND_AWS_REGION=eu-west-3
```

### Verify Configuration

```bash
cd backend
poetry run python validate_env.py
```

Expected: All ✅ green checkmarks

---

## Adding a New Variable

### 1. Add to Template

```hcl
# In templates/your-template/variables.tf
variable "new_variable" {
  type        = string
  description = "Description of what this variable does"
  default     = "optional_default"  # Omit if required
  sensitive   = true  # If it contains secrets
}
```

### 2. Add to .env

```bash
# In backend/.env
NEW_VARIABLE="value"
```

### 3. Add to Settings

```python
# In app/core/config.py
class Settings(BaseSettings):
    NEW_VARIABLE: str = Field(default="")
```

### 4. Add to Executor

```python
# In app/services/terraform_executor.py (_run_command method)
if settings.NEW_VARIABLE:
    env["TF_VAR_new_variable"] = settings.NEW_VARIABLE
```

### 5. Add to Validation

```python
# In backend/validate_env.py
required = [
    # ... existing variables
    ("NEW_VARIABLE", settings.NEW_VARIABLE),
]
```

### 6. Test

```bash
poetry run python validate_env.py
```

### 7. Update Documentation

- Update this file
- Update `TERRAFORM_BLOCKING_FIX.md`
- Update template README

---

## Debugging Variable Issues

### Check if variable is set in .env

```bash
cd backend
grep "VARIABLE_NAME" .env
```

### Check if variable is loaded by Settings

```python
from app.core.config import settings
print(settings.VARIABLE_NAME)
```

### Check if variable is passed to Terraform

```bash
# Enable debug logging
export TF_LOG=DEBUG

# Run a deployment and check logs
poetry run python -m app.main
```

Look for lines like:

```
[TF] TF_VAR_variable_name=[REDACTED]
```

### Check Terraform variable definition

```bash
cd backend/data/templates/templates/your-template
grep -A5 "variable \"variable_name\"" variables.tf
```

### Manual test without CMP

```bash
cd backend/data/templates/templates/your-template

# Test with minimal variables
terraform init -input=false
terraform plan -input=false \
  -var="required_var=value" \
  -var="another_var=value"
```

If it hangs, you're missing a required variable.

---

## Common Issues

### "No value for required variable"

**Symptom**: Terraform waits for input

**Cause**: Variable is required but not provided

**Fix**: Add the variable to `.env` and `terraform_executor.py`

### "value for undeclared variable"

**Symptom**: Terraform errors immediately

**Cause**: Variable is passed but not declared in `variables.tf`

**Fix**: Add the variable declaration to the template

### "Invalid value for input variable"

**Symptom**: Terraform errors on type mismatch

**Cause**: Wrong variable type (e.g., passing string for number)

**Fix**: Check variable type in `variables.tf` and adjust value

### "variable has no value"

**Symptom**: Error during apply/plan

**Cause**: Variable is optional (has default) but default is invalid

**Fix**: Either provide a value or fix the default in `variables.tf`

---

## Variable Naming Conventions

### In .env

```bash
SCREAMING_SNAKE_CASE
```

### In Terraform

```hcl
lowercase_snake_case
```

### In Python

```python
# Settings class
SCREAMING_SNAKE_CASE

# Environment variables passed to Terraform
TF_VAR_lowercase_snake_case
```

**Example**:

- `.env`: `CLOUDFLARE_API_TOKEN`
- `config.py`: `CLOUDFLARE_API_TOKEN`
- `terraform_executor.py`: `TF_VAR_cloudflare_api_token`
- `variables.tf`: `variable "cloudflare_api_token"`

---

## Quick Reference Table

| .env Variable             | TF_VAR                            | Terraform Variable         | Template(s)    |
| ------------------------- | --------------------------------- | -------------------------- | -------------- |
| `CLOUDFLARE_API_TOKEN`    | `TF_VAR_cloudflare_api_token`     | `cloudflare_api_token`     | k3s-gitops-app |
| `CLOUDFLARE_ZONE_ID`      | `TF_VAR_cloudflare_zone_id`       | `cloudflare_zone_id`       | k3s-gitops-app |
| `CLOUDFLARE_ACCOUNT_ID`   | `TF_VAR_cloudflare_account_id`    | `cloudflare_account_id`    | k3s-gitops-app |
| `KEYCLOAK_URL`            | `TF_VAR_keycloak_url`             | `keycloak_url`             | Both           |
| `KEYCLOAK_ADMIN_USERNAME` | `TF_VAR_keycloak_admin_username`  | `keycloak_admin_username`  | Both           |
| `KEYCLOAK_ADMIN_PASSWORD` | `TF_VAR_keycloak_admin_password`  | `keycloak_admin_password`  | Both           |
| `VAULT_URL`               | `TF_VAR_vault_url`                | `vault_url`                | Both           |
| `VAULT_TOKEN`             | `TF_VAR_vault_token`              | `vault_token`              | Both           |
| `GITHUB_REGISTRY_TOKEN`   | `TF_VAR_github_registry_token`    | `github_registry_token`    | k3s-gitops-app |
| - (hardcoded)             | `TF_VAR_github_registry_username` | `github_registry_username` | k3s-gitops-app |

---

## See Also

- **Full troubleshooting guide**: `TERRAFORM_BLOCKING_FIX.md`
- **Validation script**: `backend/validate_env.py`
- **Settings definition**: `backend/app/core/config.py`
- **Executor implementation**: `backend/app/services/terraform_executor.py`
- **Template files**: `backend/data/templates/templates/*/variables.tf`
