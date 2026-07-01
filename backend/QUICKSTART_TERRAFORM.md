# Terraform Integration - Quick Start Guide

**Time to complete**: 5 minutes
**Prerequisite**: Terraform installed

---

## Step 1: Copy Environment Template

```bash
cd backend
cp .env.example .env
```

---

## Step 2: Fill Required Secrets

Edit `.env` and set these **required** variables:

```bash
# GitHub App (3836905 - CNP-Portal)
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"

# Vault
VAULT_URL="https://vault.3istor.com"
VAULT_TOKEN="hvs.xxxxxxxxxxxxxxxxxxxx"

# Keycloak
KEYCLOAK_ADMIN_USERNAME="admin"
KEYCLOAK_ADMIN_PASSWORD="your_admin_password"

# Cloudflare
CLOUDFLARE_API_TOKEN="your_cloudflare_token"
CLOUDFLARE_ZONE_ID="your_zone_id"

# S3 Backend
TF_BACKEND_S3_ENABLED=true
TF_BACKEND_S3_BUCKET="3-istor-tf-infra-aws"
TF_BACKEND_AWS_ACCESS_KEY_ID="AKIA..."
TF_BACKEND_AWS_SECRET_ACCESS_KEY="..."
```

---

## Step 3: Verify Configuration

```bash
python verify_terraform_integration.py
```

**Expected output**:

```
✅ All checks passed! Terraform integration is properly configured.
```

---

## Step 4: Test Terraform

```bash
cd app/terraform/github_bootstrap
terraform init
terraform version
```

---

## ✅ Done!

You're ready to deploy applications with Terraform.

### Next Steps

- **Deploy an app**: See `TERRAFORM_INTEGRATION.md`
- **Troubleshooting**: See `TERRAFORM_SECRETS_SUMMARY.md`
- **API usage**: See `.kiro/steering/docs/05-backend-api/`

---

## Quick Reference

### All Required Environment Variables

```bash
GITHUB_APP_PRIVATE_KEY=""          # GitHub App private key (PEM)
VAULT_URL=""                       # Vault URL
VAULT_TOKEN=""                     # Vault token
KEYCLOAK_URL=""                    # Keycloak URL (default: https://auth.3istor.com)
KEYCLOAK_ADMIN_USERNAME=""         # Keycloak admin username
KEYCLOAK_ADMIN_PASSWORD=""         # Keycloak admin password
CLOUDFLARE_API_TOKEN=""            # Cloudflare API token
CLOUDFLARE_ZONE_ID=""              # Cloudflare zone ID
TF_BACKEND_S3_ENABLED=true         # Enable S3 backend
TF_BACKEND_S3_BUCKET=""            # S3 bucket name
TF_BACKEND_AWS_ACCESS_KEY_ID=""    # AWS access key
TF_BACKEND_AWS_SECRET_ACCESS_KEY="" # AWS secret key
TF_BACKEND_AWS_REGION="eu-west-3"  # AWS region
```

### Optional Variables

```bash
GITHUB_REGISTRY_TOKEN=""           # GitHub PAT for private images
CLOUDFLARE_ACCOUNT_ID=""           # Cloudflare account ID
TF_BACKEND_S3_DYNAMODB_TABLE=""    # DynamoDB table for state locking
```

---

## Common Issues

### Issue: "No module named 'pydantic_settings'"

**Solution**:

```bash
poetry install
poetry shell
```

### Issue: "VAULT_TOKEN: NOT SET"

**Solution**: Check your `.env` file exists and contains the variable.

### Issue: "Terraform module not found"

**Solution**: Ensure modules exist:

```bash
ls app/terraform/github_bootstrap/
ls app/terraform/k3s-project-bootstrap/
```

---

## Help

- **Full documentation**: `TERRAFORM_INTEGRATION.md`
- **Changes summary**: `TERRAFORM_SECRETS_SUMMARY.md`
- **Verification**: `python verify_terraform_integration.py`
