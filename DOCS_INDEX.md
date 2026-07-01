# CMP Documentation Index

Quick navigation to all documentation files in the repository.

---

## 🚀 Quick Start

- **[QUICK_START.md](QUICK_START.md)** - Get started with the CMP platform
- **[README.md](README.md)** - Main project overview

---

## 🔧 Terraform Documentation

### Essential Reading

1. **[TERRAFORM_VARIABLES_FIX_SUMMARY.md](TERRAFORM_VARIABLES_FIX_SUMMARY.md)** ⭐ **LATEST FIX**
   - Complete summary of all variable fixes
   - Backend changes (destroy + deploy)
   - Frontend requirements
   - Current status

2. **[DEPLOYMENT_API_REQUIREMENTS.md](DEPLOYMENT_API_REQUIREMENTS.md)** ⭐ **FOR FRONTEND**
   - What data frontend must send
   - Complete API examples
   - Error messages explained

3. **[TERRAFORM_DESTROY_FIX.md](TERRAFORM_DESTROY_FIX.md)** 📖 **DESTROY FIX**
   - How destroy variables are regenerated
   - GitHub token handling
   - Edge cases

4. **[TERRAFORM_FIX_SUMMARY.md](TERRAFORM_FIX_SUMMARY.md)** ⭐ **ORIGINAL FIX**
   - Quick summary of the blocking issue fix
   - What changed and why
   - Verification steps

5. **[TERRAFORM_QUICKSTART.md](TERRAFORM_QUICKSTART.md)** ⭐ **QUICK REFERENCE**
   - Quick commands for testing templates
   - Current status overview
   - Common debugging commands

6. **[TERRAFORM_BLOCKING_FIX.md](TERRAFORM_BLOCKING_FIX.md)** 📖 **FULL GUIDE**
   - Complete troubleshooting guide
   - Root cause analysis
   - Step-by-step resolution
   - Variable reference table

7. **[TERRAFORM_VARIABLES_REFERENCE.md](TERRAFORM_VARIABLES_REFERENCE.md)** 📋 **REFERENCE**
   - Complete variable list for all templates
   - Mapping between .env, Python, and Terraform
   - How to add new variables
   - Debugging variable issues

### Other Terraform Docs

- **[TERRAFORM_SECRETS_TLDR.md](TERRAFORM_SECRETS_TLDR.md)** - Quick reference for secrets management
- **[TERRAFORM_SECRETS_HANDOFF.md](TERRAFORM_SECRETS_HANDOFF.md)** - Team handoff for secrets
- **[TERRAFORM_STUCK_GUIDE.md](TERRAFORM_STUCK_GUIDE.md)** - What to do when Terraform appears stuck
- **[TERRAFORM_INTEGRATION.md](backend/TERRAFORM_INTEGRATION.md)** - Terraform integration details

---

## 🐛 Troubleshooting & Debugging

- **[DEBUG_DEPLOYMENTS.md](DEBUG_DEPLOYMENTS.md)** - Debugging deployment issues
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - General troubleshooting guide
- **[LOGS_GUIDE.txt](LOGS_GUIDE.txt)** - How to read and analyze logs

---

## ✅ Verification & Testing

- **[backend/validate_env.py](backend/validate_env.py)** - Environment validation script
  ```bash
  cd backend
  poetry run python validate_env.py
  ```

---

## 📝 Change Documentation

- **[CHANGES.txt](CHANGES.txt)** - Recent changes
- **[FIXES_SUMMARY.md](FIXES_SUMMARY.md)** - Summary of fixes applied
- **[README_FIXES.md](README_FIXES.md)** - README fixes
- **[CORRECTIFS_APPLIQUES.md](CORRECTIFS_APPLIQUES.md)** - Applied corrections (French)
- **[GIT_COMMIT_FIXES.txt](GIT_COMMIT_FIXES.txt)** - Git commit fixes

---

## 🏗️ Architecture & Design

### Phase 3 Documentation

- **[.kiro/steering/PHASE3_SUMMARY.md](.kiro/steering/PHASE3_SUMMARY.md)** - Phase 3 quick summary
- **[.kiro/steering/PHASE3_TEAM_HANDOFF.md](.kiro/steering/PHASE3_TEAM_HANDOFF.md)** - Phase 3 team handoff
- **[backend/PHASE3_COMPLETE.md](backend/PHASE3_COMPLETE.md)** - Phase 3 completion guide

### Architecture Docs

- **[.kiro/steering/docs/README.md](.kiro/steering/docs/README.md)** - Documentation overview
- **[.kiro/steering/docs/README_ROADMAP.md](.kiro/steering/docs/README_ROADMAP.md)** - Implementation roadmap
- **[.kiro/steering/CHANGELOG.md](.kiro/steering/CHANGELOG.md)** - Complete changelog

### Core Components

- **[.kiro/steering/docs/02-core-components/01-cmp-dashboard.md](.kiro/steering/docs/02-core-components/01-cmp-dashboard.md)** - CMP Dashboard
- **[.kiro/steering/docs/02-core-components/02-identity-keycloak.md](.kiro/steering/docs/02-core-components/02-identity-keycloak.md)** - Keycloak integration
- **[.kiro/steering/docs/02-core-components/03-secrets-vault.md](.kiro/steering/docs/02-core-components/03-secrets-vault.md)** - Vault secrets
- **[.kiro/steering/docs/02-core-components/04-gitops-argocd.md](.kiro/steering/docs/02-core-components/04-gitops-argocd.md)** - ArgoCD GitOps
- **[.kiro/steering/docs/02-core-components/05-github-integration.md](.kiro/steering/docs/02-core-components/05-github-integration.md)** - GitHub App

### API Documentation

- **[.kiro/steering/docs/05-cmp-backend-api/01-cmp-deployment-api.md](.kiro/steering/docs/05-cmp-backend-api/01-cmp-deployment-api.md)** - Deployment API spec
- **[.kiro/steering/docs/05-cmp-backend-api/02-cmp-phase3-changes.md](.kiro/steering/docs/05-cmp-backend-api/02-cmp-phase3-changes.md)** - Phase 3 changes
- **[.kiro/steering/docs/05-cmp-backend-api/03-cmp-frontend-integration.md](.kiro/steering/docs/05-cmp-backend-api/03-cmp-frontend-integration.md)** - Frontend integration

---

## 📦 Backend Documentation

- **[backend/README.md](backend/README.md)** - Backend overview
- **[backend/QUICKSTART_TERRAFORM.md](backend/QUICKSTART_TERRAFORM.md)** - Terraform quick start
- **[backend/README_TERRAFORM_SECRETS.md](backend/README_TERRAFORM_SECRETS.md)** - Terraform secrets
- **[backend/REGRESSION_FIXES.md](backend/REGRESSION_FIXES.md)** - Regression fixes
- **[backend/CHANGELOG_TERRAFORM_SECRETS.md](backend/CHANGELOG_TERRAFORM_SECRETS.md)** - Secrets changelog

---

## 🎯 GitHub Integration

- **[GITHUB_CALLBACK_IMPLEMENTATION.md](GITHUB_CALLBACK_IMPLEMENTATION.md)** - GitHub callback implementation

---

## 📋 Project Management

- **[.kiro/steering/structure.md](.kiro/steering/structure.md)** - Project structure
- **[.kiro/steering/product.md](.kiro/steering/product.md)** - Product overview
- **[.kiro/steering/frontend-ux.md](.kiro/steering/frontend-ux.md)** - Frontend UX specs

---

## 🔍 Quick Reference Cards

### For Developers

**Starting a new feature?**

1. Read **[QUICK_START.md](QUICK_START.md)**
2. Check **[backend/README.md](backend/README.md)**
3. Review **[.kiro/steering/docs/README_ROADMAP.md](.kiro/steering/docs/README_ROADMAP.md)**

**Deployment issues?**

1. Check **[DEBUG_DEPLOYMENTS.md](DEBUG_DEPLOYMENTS.md)**
2. Review **[TERRAFORM_FIX_SUMMARY.md](TERRAFORM_FIX_SUMMARY.md)**
3. Run **[backend/validate_env.py](backend/validate_env.py)**

**Terraform blocking?**

1. Read **[TERRAFORM_FIX_SUMMARY.md](TERRAFORM_FIX_SUMMARY.md)** first
2. Check **[TERRAFORM_QUICKSTART.md](TERRAFORM_QUICKSTART.md)** for commands
3. Refer to **[TERRAFORM_BLOCKING_FIX.md](TERRAFORM_BLOCKING_FIX.md)** for details

### For DevOps

**Infrastructure setup?**

1. **[QUICK_START.md](QUICK_START.md)** - Initial setup
2. **[backend/QUICKSTART_TERRAFORM.md](backend/QUICKSTART_TERRAFORM.md)** - Terraform setup
3. **[TERRAFORM_VARIABLES_REFERENCE.md](TERRAFORM_VARIABLES_REFERENCE.md)** - Variable config

**Secrets management?**

1. **[TERRAFORM_SECRETS_TLDR.md](TERRAFORM_SECRETS_TLDR.md)** - Quick reference
2. **[backend/README_TERRAFORM_SECRETS.md](backend/README_TERRAFORM_SECRETS.md)** - Full guide
3. **[.kiro/steering/docs/02-core-components/03-secrets-vault.md](.kiro/steering/docs/02-core-components/03-secrets-vault.md)** - Vault integration

### For QA/Testing

**Testing deployments?**

1. **[TERRAFORM_QUICKSTART.md](TERRAFORM_QUICKSTART.md)** - Test commands
2. **[backend/validate_env.py](backend/validate_env.py)** - Environment check
3. **[LOGS_GUIDE.txt](LOGS_GUIDE.txt)** - Log analysis

---

## 📚 By Topic

### Terraform

- [TERRAFORM_FIX_SUMMARY.md](TERRAFORM_FIX_SUMMARY.md) ⭐ Summary
- [TERRAFORM_QUICKSTART.md](TERRAFORM_QUICKSTART.md) ⭐ Quick ref
- [TERRAFORM_BLOCKING_FIX.md](TERRAFORM_BLOCKING_FIX.md) 📖 Full guide
- [TERRAFORM_VARIABLES_REFERENCE.md](TERRAFORM_VARIABLES_REFERENCE.md) 📋 Variables
- [TERRAFORM_SECRETS_TLDR.md](TERRAFORM_SECRETS_TLDR.md)
- [TERRAFORM_STUCK_GUIDE.md](TERRAFORM_STUCK_GUIDE.md)
- [backend/TERRAFORM_INTEGRATION.md](backend/TERRAFORM_INTEGRATION.md)

### Deployments

- [DEBUG_DEPLOYMENTS.md](DEBUG_DEPLOYMENTS.md)
- [.kiro/steering/docs/05-cmp-backend-api/01-cmp-deployment-api.md](.kiro/steering/docs/05-cmp-backend-api/01-cmp-deployment-api.md)

### Phase 3

- [.kiro/steering/PHASE3_SUMMARY.md](.kiro/steering/PHASE3_SUMMARY.md)
- [.kiro/steering/PHASE3_TEAM_HANDOFF.md](.kiro/steering/PHASE3_TEAM_HANDOFF.md)
- [backend/PHASE3_COMPLETE.md](backend/PHASE3_COMPLETE.md)
- [.kiro/steering/docs/05-cmp-backend-api/02-cmp-phase3-changes.md](.kiro/steering/docs/05-cmp-backend-api/02-cmp-phase3-changes.md)

### Architecture

- [.kiro/steering/docs/01-architecture/01-system-overview.md](.kiro/steering/docs/01-architecture/01-system-overview.md)
- [.kiro/steering/docs/01-architecture/02-tenancy-and-isolation.md](.kiro/steering/docs/01-architecture/02-tenancy-and-isolation.md)

### GitHub

- [GITHUB_CALLBACK_IMPLEMENTATION.md](GITHUB_CALLBACK_IMPLEMENTATION.md)
- [.kiro/steering/docs/02-core-components/05-github-integration.md](.kiro/steering/docs/02-core-components/05-github-integration.md)

### Troubleshooting

- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [DEBUG_DEPLOYMENTS.md](DEBUG_DEPLOYMENTS.md)
- [TERRAFORM_STUCK_GUIDE.md](TERRAFORM_STUCK_GUIDE.md)
- [LOGS_GUIDE.txt](LOGS_GUIDE.txt)

---

## 🔗 External Links

- **Templates**: `backend/data/templates/templates/`
  - `k3s-project-bootstrap/` - Project provisioning
  - `k3s-gitops-app/` - GitOps app provisioning
  - Each has its own `README.md` and `variables.tf`

---

## 📊 Document Status

| Document                         | Status      | Last Updated |
| -------------------------------- | ----------- | ------------ |
| TERRAFORM_FIX_SUMMARY.md         | ✅ Complete | 2026-06-11   |
| TERRAFORM_QUICKSTART.md          | ✅ Complete | 2026-06-11   |
| TERRAFORM_BLOCKING_FIX.md        | ✅ Complete | 2026-06-11   |
| TERRAFORM_VARIABLES_REFERENCE.md | ✅ Complete | 2026-06-11   |
| validate_env.py                  | ✅ Working  | 2026-06-11   |
| PHASE3_SUMMARY.md                | ✅ Complete | 2026-05-24   |
| PHASE3_TEAM_HANDOFF.md           | ✅ Complete | 2026-05-24   |

---

## 💡 Tips

- **Always start with summaries** (⭐ marked docs)
- **Run validation first** when troubleshooting
- **Check logs** for real-time debugging
- **Read phase docs** before making changes

---

**Need help?** Start with the ⭐ marked documents or run:

```bash
cd backend
poetry run python validate_env.py
```
