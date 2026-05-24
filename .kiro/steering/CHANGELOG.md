# CNP Platform Changelog

This document tracks major changes and releases of the Cloud Native Platform.

---

## [Phase 3] - 2026-05-24

### ✅ Kubernetes Provider Support - COMPLETE

**Summary**: Added multi-provider architecture to support modern Kubernetes GitOps deployments alongside legacy hybrid infrastructure.

### Added

#### Backend

- **Multi-Provider Architecture**: Strategy pattern for routing deployments by `provider_type`
- **GitHub App Integration**: JWT generation and token exchange service (`github_service.py`)
- **Terraform Bootstrap Module**: Day-0 provisioning for Kubernetes apps (`terraform/github_bootstrap/`)
- **Database Schema**: 5 new columns for Kubernetes deployments
  - `provider_type` (enum: `legacy_hybrid` | `kubernetes`)
  - `project_id` (string, nullable)
  - `github_repo_url` (string, nullable)
  - `argocd_app_name` (string, nullable)
  - `k8s_namespace` (string, nullable)

#### API

- **Deployment API**: Extended with Kubernetes-specific fields
- **Backward Compatibility**: All existing endpoints work unchanged
- **New Query Parameters**: Filter deployments by `provider_type` and `project_id`

#### Infrastructure

- **Micro-State Pattern**: Isolated Terraform state per application in S3
- **GitHub App**: CNP-Portal (ID: 3836905) for repository management
- **ArgoCD Integration**: Automatic Application CRD creation

### Changed

#### Backend

- **Saga Orchestrator**: Refactored to support multiple providers
  - `_run_kubernetes_deployment()`: New GitOps flow
  - `_run_legacy_hybrid_deployment()`: Preserved existing flow
- **Configuration**: Added `GITHUB_APP_PRIVATE_KEY` and `AWS_INSTANCE_TYPE` settings

#### Database

- **Migration**: `c4d8f2a91b3e_add_kubernetes_provider_support`
- **Default Values**: Existing deployments default to `provider_type: legacy_hybrid`

### Documentation

#### New Documents

- `backend/PHASE3_COMPLETE.md`: Complete implementation guide
- `backend/PHASE3_IMPLEMENTATION.md`: Technical details
- `backend/PHASE3_ARCHITECTURE.md`: System diagrams
- `backend/QUICKSTART_PHASE3.md`: 5-minute setup guide
- `.kiro/steering/docs/05-backend-api/01-deployment-api.md`: API specification
- `.kiro/steering/docs/06-phase3-changes.md`: Changes summary for frontend

#### Updated Documents

- `.kiro/steering/docs/README.md`: Added Phase 3 section
- `.kiro/steering/docs/README_ROADMAP.md`: Marked Phase 3 as complete

### Testing

#### New Test Scripts

- `backend/verify_phase3.py`: Automated verification (7 tests, all passing ✅)
- `backend/test_github_service.py`: Interactive GitHub integration test
- `backend/setup_phase3.sh`: Automated setup script

#### Verification Results

- ✅ All module imports working
- ✅ Both provider types available
- ✅ Database schema updated
- ✅ Terraform module validated
- ✅ GitHub service functions verified
- ✅ Saga orchestrator functions verified

### Migration Guide

**For Backend Developers**:

```bash
cd backend
poetry install
poetry run alembic upgrade head
poetry run python verify_phase3.py
```

**For Frontend Developers**:

- Review `.kiro/steering/docs/06-phase3-changes.md`
- New fields available in Deployment API
- Phase 4 will add UI components

### Breaking Changes

**None**. Phase 3 is 100% backward compatible.

### Deprecations

**None**.

### Security

- GitHub App uses short-lived tokens (JWT: 10 min, Installation: 1 hour)
- Vault secrets auto-generated with strong randomness (32+ chars)
- Kubernetes auth roles bound to specific namespaces
- Terraform state encrypted at rest in S3

### Performance

- Micro-state pattern enables parallel deployments
- No state locking conflicts between applications
- Dynamic S3 state keys: `cmp/projects/<project>/<app>.tfstate`

### Known Issues

**None**.

### Contributors

- Backend Implementation: Kiro AI Agent
- Architecture Design: 3-Istor Team
- Documentation: Kiro AI Agent

---

## [Phase 2] - 2026-04-07

### Terraform-Based Deployments

**Summary**: Migrated from direct SDK calls to Terraform-based infrastructure provisioning.

### Added

- Terraform state management with S3 backend
- Dynamic state file generation
- Resource tracking and outputs

### Changed

- Removed direct OpenStack/AWS SDK calls from SAGA
- Deployment model simplified (removed VM-specific fields)

### Migration

- Migration: `b9751e077ee4_migrate_to_terraform_based_deployments`

---

## [Phase 1] - 2026-03-15

### Initial Release

**Summary**: Initial CMP platform with legacy hybrid deployments.

### Added

- FastAPI backend with SQLAlchemy ORM
- OpenStack VM provisioning
- AWS Auto Scaling Group provisioning
- SAGA orchestration pattern
- Basic deployment lifecycle management

### Features

- Create/Read/Delete deployments
- Health monitoring
- Status tracking

---

## Upcoming

### [Phase 4] - Frontend Refactoring (Planned)

**Goal**: Update the Next.js frontend to support multi-provider deployments.

**Planned Features**:

1. Dual Catalog View (IaaS vs PaaS)
2. GitHub Account Linking UI
3. Dynamic Deployment Cards
4. ArgoCD Health Integration
5. Day-2 Operations UI

**Estimated Duration**: 2-3 days

**Dependencies**: Phase 3 ✅ Complete

---

## Version History

| Phase   | Date       | Status      | Description                          |
| ------- | ---------- | ----------- | ------------------------------------ |
| Phase 1 | 2026-03-15 | ✅ Complete | Initial release with legacy hybrid   |
| Phase 2 | 2026-04-07 | ✅ Complete | Terraform-based deployments          |
| Phase 3 | 2026-05-24 | ✅ Complete | Kubernetes provider support          |
| Phase 4 | TBD        | 📋 Planned  | Frontend refactoring                 |
| Phase 5 | TBD        | 📋 Planned  | Day-2 operations & GitOps write-back |

---

## Support

For questions or issues:

- **Backend**: See `backend/PHASE3_COMPLETE.md`
- **API**: See `.kiro/steering/docs/05-backend-api/01-deployment-api.md`
- **Architecture**: See `.kiro/steering/docs/`
