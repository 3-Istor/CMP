# Terraform Quick Start Guide

**Quick reference for working with Terraform templates in the CMP backend**

---

## ✅ Status

All Terraform variables are configured and ready to use.

**Verification**:

```bash
cd backend
poetry run python validate_env.py
```

Expected: All ✅ (see output below)

---

## 🚀 Quick Commands

### Validate Environment

```bash
cd backend
poetry run python validate_env.py
```

### Manual Template Testing

#### k3s-project-bootstrap

```bash
cd backend/data/templates/templates/k3s-project-bootstrap

# Initialize
terraform init -input=false -reconfigure

# Plan
terraform plan -input=false \
  -var="project_name=test-project"

# Apply (creates Keycloak groups and Vault paths)
terraform apply -input=false -auto-approve \
  -var="project_name=test-project"

# Destroy
terraform destroy -input=false -auto-approve \
  -var="project_name=test-project"
```

#### k3s-gitops-app

```bash
cd backend/data/templates/templates/k3s-gitops-app

# Initialize
terraform init -input=false -reconfigure

# Plan (requires GitHub token)
terraform plan -input=false \
  -var="github_token=$GITHUB_TOKEN" \
  -var="github_owner=3-Istor" \
  -var="template_repo_name=template-html-css" \
  -var="app_name=test-app" \
  -var="project_name=test-project"

# Apply (creates GitHub repo, ArgoCD app, Vault secrets, DNS)
terraform apply -input=false -auto-approve \
  -var="github_token=$GITHUB_TOKEN" \
  -var="github_owner=3-Istor" \
  -var="template_repo_name=template-html-css" \
  -var="app_name=test-app" \
  -var="project_name=test-project"

# Destroy
terraform destroy -input=false -auto-approve \
  -var="github_token=$GITHUB_TOKEN" \
  -var="github_owner=3-Istor" \
  -var="template_repo_name=template-html-css" \
  -var="app_name=test-app" \
  -var="project_name=test-project"
```

---

## 📋 Required Variables Summary

### k3s-project-bootstrap (6 variables)

| Variable                  | Type    | Source                    |
| ------------------------- | ------- | ------------------------- |
| `project_name`            | Dynamic | User input                |
| `keycloak_url`            | Env     | `KEYCLOAK_URL`            |
| `keycloak_admin_username` | Env     | `KEYCLOAK_ADMIN_USERNAME` |
| `keycloak_admin_password` | Env     | `KEYCLOAK_ADMIN_PASSWORD` |
| `vault_url`               | Env     | `VAULT_URL`               |
| `vault_token`             | Env     | `VAULT_TOKEN`             |

### k3s-gitops-app (16 variables)

| Variable                   | Type      | Source                         |
| -------------------------- | --------- | ------------------------------ |
| `github_token`             | Dynamic   | Generated via GitHub App       |
| `github_owner`             | Dynamic   | User input                     |
| `template_repo_name`       | Dynamic   | User input                     |
| `app_name`                 | Dynamic   | User input                     |
| `project_name`             | Dynamic   | User input                     |
| `app_type`                 | Dynamic   | User input (default: "static") |
| `vault_token`              | Env       | `VAULT_TOKEN`                  |
| `vault_url`                | Env       | `VAULT_URL`                    |
| `github_registry_token`    | Env       | `GITHUB_REGISTRY_TOKEN`        |
| `github_registry_username` | Hardcoded | "3-Istor"                      |
| `keycloak_url`             | Env       | `KEYCLOAK_URL`                 |
| `keycloak_admin_username`  | Env       | `KEYCLOAK_ADMIN_USERNAME`      |
| `keycloak_admin_password`  | Env       | `KEYCLOAK_ADMIN_PASSWORD`      |
| `cloudflare_api_token`     | Env       | `CLOUDFLARE_API_TOKEN`         |
| `cloudflare_account_id`    | Env       | `CLOUDFLARE_ACCOUNT_ID`        |
| `cloudflare_zone_id`       | Env       | `CLOUDFLARE_ZONE_ID`           |

---

## 🔍 Debugging

### Check if Terraform is blocking

```bash
# Set a timeout to catch blocks early
timeout 30 terraform plan -input=false -var="..." 2>&1

# If it times out, you're missing a required variable
```

### See real-time output

The CMP backend now streams Terraform output in real-time. Watch logs:

```bash
cd backend
tail -f logs/cmp.log | grep "\[TF\]"
```

You'll see:

```
[TF] Initializing provider plugins...
[TF] Creating github_repository.app...
[TF] Creating cloudflare_record.app_dns...
```

### Check variable flow

```bash
# 1. Check .env
grep "CLOUDFLARE" backend/.env

# 2. Check if loaded by Python
cd backend
poetry run python -c "from app.core.config import settings; print(settings.CLOUDFLARE_API_TOKEN[:10])"

# 3. Check if passed to Terraform
export TF_LOG=DEBUG
poetry run python -m app.main
# Look for "TF_VAR_cloudflare_api_token" in logs
```

---

## ⚠️ Common Issues

### Issue: "No value for required variable"

**Symptom**: Terraform hangs or errors

**Solution**:

1. Check which variable is missing from the error message
2. Add to `.env`
3. Add to `terraform_executor.py` (lines 135-162)
4. Run `validate_env.py` to confirm

### Issue: "value for undeclared variable"

**Symptom**: Terraform errors immediately

**Solution**: The variable is being passed but not declared in `variables.tf`

### Issue: Terraform hangs with no output

**Symptom**: Process stuck for 60+ seconds

**Solution**:

1. Kill the process
2. Run `validate_env.py` to find missing variables
3. Add missing variables to `.env`
4. Restart

---

## 📚 Documentation

- **Full troubleshooting**: `TERRAFORM_BLOCKING_FIX.md`
- **Variable reference**: `TERRAFORM_VARIABLES_REFERENCE.md`
- **Template README**: Each template has its own README
- **CMP Backend**: `backend/README.md`

---

## 🎯 Current Status

**Environment Variables**: ✅ All 21 variables configured

**Validation Output**:

```
🔍 Validating Environment Variables for CMP Backend
================================================================================
📦 Core Infrastructure
--------------------------------------------------------------------------------
  ✅ AWS_ACCESS_KEY_ID                        = AKIA3B2Z6T...
  ✅ AWS_SECRET_ACCESS_KEY                    = wHfdwYahNG...
  ✅ AWS_DEFAULT_REGION                       = eu-west-3
  ✅ OS_AUTH_URL                              = http://192.168.1.210:5000
  ✅ OS_USERNAME                              = admin
  ✅ OS_PASSWORD                              = CZaexXDoZn...
  ✅ OS_PROJECT_NAME                          = 3-istor-cloud

🌐 Cloudflare DNS (k3s-gitops-app template)
--------------------------------------------------------------------------------
  ✅ CLOUDFLARE_API_TOKEN                     = cfut_vbnBh...
  ✅ CLOUDFLARE_ZONE_ID                       = 5984eff5179d6656a7c9e1b00c768d21
  ✅ CLOUDFLARE_ACCOUNT_ID                    = b522d6f1d77b7df478edbbf07ef7d5c4

🔑 Keycloak SSO (k3s-gitops-app template)
--------------------------------------------------------------------------------
  ✅ KEYCLOAK_URL                             = https://au...
  ✅ KEYCLOAK_CLIENT_ID                       = 3-istor-op...
  ✅ KEYCLOAK_CLIENT_SECRET                   = Y1aUBf71Bf...
  ✅ KEYCLOAK_ADMIN_USERNAME                  = [SET]
  ✅ KEYCLOAK_ADMIN_PASSWORD                  = m2wRadiKHT...

🔒 HashiCorp Vault (k3s-gitops-app template)
--------------------------------------------------------------------------------
  ✅ VAULT_URL                                = https://vault.3istor.com
  ✅ VAULT_TOKEN                              = hvs.tue4Sv...

🐙 GitHub Integration
--------------------------------------------------------------------------------
  ✅ GITHUB_APP_PRIVATE_KEY                   = [PEM KEY - 1679 chars]
  ✅ GITHUB_REGISTRY_TOKEN                    = ghp_BLlwK7...

📦 Terraform S3 Backend (optional)
--------------------------------------------------------------------------------
  ✅ TF_BACKEND_S3_ENABLED                    = True
  ✅ TF_BACKEND_S3_BUCKET                     = 3-istor-tf-infra-aws
  ✅ TF_BACKEND_AWS_REGION                    = eu-west-3

================================================================================
✅ All required environment variables are configured!
🚀 You can now run deployments safely.
```

**Templates**: ✅ Both templates tested and working

**Streaming**: ✅ Real-time output enabled

**Fail-fast**: ✅ `-input=false` on all commands

---

**Ready to deploy!** 🚀
