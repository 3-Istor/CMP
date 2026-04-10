# Instructions for Kiro - ARCL CMP Implementation

## Overview

This document provides step-by-step instructions for completing the Terraform-based deployment system implementation for the ARCL Cloud Management Platform.

## What Has Been Done

✅ **Backend Architecture**

- Created `template_repository.py` - Manages Git repository cloning and syncing
- Created `terraform_executor.py` - Wraps Terraform CLI commands
- Created `terraform_orchestrator.py` - Orchestrates deployment lifecycle
- Updated `catalog_service.py` - Loads templates from Git repository
- Updated `deployment.py` model - New schema for Terraform-based deployments
- Updated schemas - Support for Terraform outputs and template metadata
- Updated routers - New endpoints and Terraform integration
- Created database migration - Migrates from SAGA to Terraform schema

✅ **Documentation**

- `SETUP_INSTRUCTIONS.md` - Complete setup guide
- `backend/TERRAFORM_MIGRATION.md` - Technical migration details
- `IMPLEMENTATION_SUMMARY.md` - Implementation overview
- `QUICK_REFERENCE.md` - Quick reference for developers
- `VERIFICATION_CHECKLIST.md` - Testing checklist
- Updated `README.md` - New architecture and features

✅ **Scripts**

- `setup.sh` - Automated setup script

## What Needs to Be Done

### 1. Install Dependencies

```bash
cd backend
poetry add gitpython
poetry install
```

This adds GitPython for Git repository management.

### 2. Run Database Migration

```bash
cd backend
poetry run alembic upgrade head
```

This will:

- Remove old OpenStack/AWS specific columns
- Add new Terraform-related columns
- Update deployment statuses

### 3. Test Backend Startup

```bash
cd backend
poetry run uvicorn app.main:app --reload --port 8000
```

Expected behavior:

- Application starts without errors
- Logs show: "Cloning template repository from https://github.com/3-Istor/ia-project-template"
- Directory `backend/data/templates/` is created
- Templates are loaded from the repository

### 4. Verify Template Loading

```bash
curl http://localhost:8000/api/catalog/
```

Expected response:

- JSON array of templates
- Each template has: `id`, `name`, `description`, `icon`, `category`, `variables`, `enabled`
- Only templates with `enabled: true` are returned

### 5. Test Deployment (Optional - requires OpenStack credentials)

```bash
curl -X POST http://localhost:8000/api/deployments/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-nginx",
    "template_id": "openstack-nginx",
    "app_config": {
      "instance_count": 2
    }
  }'
```

Expected behavior:

- Returns 202 Accepted
- Deployment record created with status `PENDING`
- Background task starts Terraform execution
- Status progresses: PENDING → INITIALIZING → PLANNING → DEPLOYING → RUNNING

### 6. Frontend Updates (If Needed)

The frontend needs updates to work with the new backend. Key changes:

**Update deployment display to show Terraform outputs:**

```typescript
// Old approach (remove)
<div>AWS ALB: {deployment.aws_alb_dns}</div>
<div>DB IP: {deployment.os_vm_db1_ip}</div>

// New approach (add)
{deployment.terraform_outputs && (
  <div>
    <h3>Outputs</h3>
    {Object.entries(JSON.parse(deployment.terraform_outputs)).map(([key, value]) => (
      <div key={key}>
        <strong>{key}:</strong> {value}
      </div>
    ))}
  </div>
)}
```

**Update status display:**

Add new statuses:

- `INITIALIZING` - "Initializing Terraform..."
- `PLANNING` - "Planning deployment..."

Remove old statuses:

- `DEPLOYING_OPENSTACK`
- `DEPLOYING_AWS`
- `ROLLING_BACK`

**Remove health check endpoint:**

Remove any calls to `/api/deployments/{id}/health` (no longer exists).

**Add sync button (optional):**

```typescript
<button onClick={() => fetch('/api/catalog/sync', { method: 'POST' })}>
  Sync Templates
</button>
```

## File Structure

```
arcl-cmp/
├── backend/
│   ├── app/
│   │   ├── services/
│   │   │   ├── template_repository.py      ✨ NEW
│   │   │   ├── terraform_executor.py       ✨ NEW
│   │   │   ├── terraform_orchestrator.py   ✨ NEW
│   │   │   ├── catalog_service.py          🔄 UPDATED
│   │   │   ├── saga_orchestrator.py        ⚠️ DEPRECATED
│   │   │   ├── openstack_service.py        ⚠️ DEPRECATED
│   │   │   └── aws_service.py              ⚠️ DEPRECATED
│   │   ├── models/deployment.py            🔄 UPDATED
│   │   ├── schemas/                        🔄 UPDATED
│   │   ├── routers/                        🔄 UPDATED
│   │   └── main.py                         🔄 UPDATED
│   ├── data/                               ✨ AUTO-CREATED
│   │   ├── templates/                      # Git repo clone
│   │   └── terraform_states/               # State files
│   ├── alembic/versions/
│   │   └── b9751e077ee4_*.py              ✨ NEW
│   ├── TERRAFORM_MIGRATION.md              ✨ NEW
│   └── pyproject.toml                      🔄 UPDATED
├── SETUP_INSTRUCTIONS.md                   ✨ NEW
├── IMPLEMENTATION_SUMMARY.md               ✨ NEW
├── QUICK_REFERENCE.md                      ✨ NEW
├── VERIFICATION_CHECKLIST.md               ✨ NEW
├── KIRO_INSTRUCTIONS.md                    ✨ NEW (this file)
├── setup.sh                                ✨ NEW
├── .gitignore                              🔄 UPDATED
└── README.md                               🔄 UPDATED
```

## Key Concepts

### Template Repository

- **URL**: https://github.com/3-Istor/ia-project-template
- **Sync**: Automatic on startup + every 24 hours
- **Location**: `backend/data/templates/`
- **Structure**: Each template has a directory with `manifest.json` and Terraform files

### Template Manifest

Each template requires a `manifest.json`:

```json
{
  "enabled": true,
  "id": "openstack-nginx",
  "name": "Nginx Website (OpenStack)",
  "description": "Deploy a static website using Nginx on OpenStack.",
  "icon": "🌐",
  "image_path": "icon.png",
  "category": "Web",
  "variables": [
    {
      "name": "instance_count",
      "label": "Number of Nodes",
      "type": "number",
      "default": 2
    }
  ]
}
```

### Deployment Flow

1. User selects template → `POST /api/deployments/`
2. Backend creates deployment record (status: PENDING)
3. Background task starts:
   - INITIALIZING: `terraform init`
   - PLANNING: `terraform plan`
   - DEPLOYING: `terraform apply`
   - RUNNING: Capture outputs
4. Frontend polls `GET /api/deployments/{id}` every 3s
5. Outputs displayed (LoadBalancer IP, URLs, etc.)

### Terraform Outputs

Templates should define outputs in `outputs.tf`:

```hcl
output "loadbalancer_ip" {
  description = "Public IP of the load balancer"
  value       = openstack_networking_floatingip_v2.lb_ip.address
}
```

The CMP automatically captures all outputs and displays them. Priority is given to:

- `loadbalancer_ip`, `lb_ip`
- `public_ip`, `ip`
- `url`, `endpoint`, `dns`, `address`

## Troubleshooting

### Issue: Template repository not cloning

**Symptoms**: No templates returned from `/api/catalog/`

**Solution**:

1. Check internet connectivity
2. Verify Git is installed: `git --version`
3. Check logs for errors
4. Manually test: `git clone https://github.com/3-Istor/ia-project-template.git`

### Issue: Terraform commands failing

**Symptoms**: Deployments fail with status FAILED

**Solution**:

1. Verify Terraform is installed: `terraform --version`
2. Check OpenStack credentials in `.env`
3. Test Terraform manually:
   ```bash
   cd backend/data/templates/templates/openstack-nginx
   terraform init
   terraform plan
   ```

### Issue: Database migration fails

**Symptoms**: `alembic upgrade head` fails

**Solution**:

1. Check if database is locked
2. Backup database: `cp backend/arcl.db backend/arcl.db.backup`
3. Try again
4. If still fails, reset database:
   ```bash
   rm backend/arcl.db
   poetry run alembic upgrade head
   ```

### Issue: Import errors

**Symptoms**: `ModuleNotFoundError: No module named 'git'`

**Solution**:

```bash
cd backend
poetry add gitpython
poetry install
```

## Testing Checklist

Use `VERIFICATION_CHECKLIST.md` for complete testing. Quick tests:

```bash
# 1. Start backend
cd backend
poetry run uvicorn app.main:app --reload --port 8000

# 2. Test catalog
curl http://localhost:8000/api/catalog/

# 3. Test sync
curl -X POST http://localhost:8000/api/catalog/sync

# 4. Test deployment (requires OpenStack credentials)
curl -X POST http://localhost:8000/api/deployments/ \
  -H "Content-Type: application/json" \
  -d '{"name":"test","template_id":"openstack-nginx","app_config":{"instance_count":2}}'

# 5. Check status
curl http://localhost:8000/api/deployments/1

# 6. Get outputs
curl http://localhost:8000/api/deployments/1/outputs
```

## Next Steps

1. **Install dependencies**: `poetry add gitpython && poetry install`
2. **Run migration**: `poetry run alembic upgrade head`
3. **Test backend**: Start and verify template loading
4. **Update frontend**: Modify to use new API structure
5. **Test deployment**: Create a test deployment
6. **Verify outputs**: Check that Terraform outputs are captured
7. **Test deletion**: Verify `terraform destroy` works
8. **Review documentation**: Ensure all docs are accurate

## Important Notes

- **Existing deployments**: Old deployments (pre-migration) won't work with the new system
- **State management**: Terraform state is local (not remote backend yet)
- **Concurrent deployments**: No locking mechanism yet (future improvement)
- **Template validation**: No pre-deployment validation of Terraform syntax (future improvement)

## Support Resources

- **Setup Guide**: `SETUP_INSTRUCTIONS.md`
- **Technical Details**: `backend/TERRAFORM_MIGRATION.md`
- **Implementation Overview**: `IMPLEMENTATION_SUMMARY.md`
- **Quick Reference**: `QUICK_REFERENCE.md`
- **Testing**: `VERIFICATION_CHECKLIST.md`

## Success Criteria

✅ Backend starts without errors
✅ Template repository is cloned
✅ Templates are loaded from Git
✅ Only enabled templates are shown
✅ Deployments use Terraform
✅ Outputs are captured
✅ Deletions work
✅ Database migration succeeds
✅ No Python syntax errors

## Final Steps

Once everything is working:

1. Mark deprecated files for removal:
   - `backend/app/services/saga_orchestrator.py`
   - `backend/app/services/openstack_service.py`
   - `backend/app/services/aws_service.py`

2. Update frontend to use new API structure

3. Test full deployment cycle

4. Review and update documentation if needed

5. Commit changes with descriptive message:

   ```bash
   git add .
   git commit -m "feat: migrate to Terraform-based deployment system

   - Add template repository management with Git sync
   - Add Terraform executor for infrastructure deployment
   - Update database schema for Terraform outputs
   - Add comprehensive documentation
   - Deprecate old SAGA orchestrator

   BREAKING CHANGE: Old deployments are not compatible with new system"
   ```

## Questions?

Refer to the documentation files or check the implementation in the code. All services are well-documented with docstrings and comments.

Good luck with the implementation! 🚀
