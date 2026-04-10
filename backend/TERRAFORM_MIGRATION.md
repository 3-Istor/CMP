# Terraform-Based Deployment Migration

## Overview

The CMP has been migrated from a hardcoded SAGA pattern (OpenStack + AWS) to a flexible Terraform-based deployment system. This allows deploying any infrastructure defined in Terraform templates from a Git repository.

## Key Changes

### Architecture

**Before:**

- Hardcoded deployment logic for OpenStack VMs + AWS ASG
- Static catalog defined in code
- SAGA orchestrator with rollback logic

**After:**

- Dynamic Terraform template execution
- Templates loaded from Git repository
- Flexible deployment supporting any cloud provider
- Automatic output capture (IPs, URLs, etc.)

### New Components

1. **Template Repository Manager** (`template_repository.py`)
   - Clones and syncs https://github.com/3-Istor/ia-project-template
   - Reads template manifests
   - Caches templates with 24h auto-sync
   - Validates `enabled: true` and manifest existence

2. **Terraform Executor** (`terraform_executor.py`)
   - Executes Terraform commands (init, plan, apply, destroy)
   - Captures outputs (LoadBalancer IPs, URLs, etc.)
   - Manages state files per deployment

3. **Terraform Orchestrator** (`terraform_orchestrator.py`)
   - Replaces old SAGA orchestrator
   - Manages deployment lifecycle
   - Tracks progress and outputs

### Database Schema Changes

**Removed columns:**

- `os_vm_db1_id`, `os_vm_db2_id`
- `os_vm_db1_ip`, `os_vm_db2_ip`
- `aws_asg_name`, `aws_alb_dns`, `aws_instance_ids`

**Added columns:**

- `terraform_outputs` (JSON) - All Terraform outputs
- `terraform_state_path` - Path to state directory
- `resource_count` - Number of managed resources
- `template_name`, `template_icon`, `template_category` - Template metadata

**Updated statuses:**

- Removed: `DEPLOYING_OPENSTACK`, `DEPLOYING_AWS`, `ROLLING_BACK`
- Added: `INITIALIZING`, `PLANNING`
- Kept: `PENDING`, `DEPLOYING`, `RUNNING`, `FAILED`, `DELETING`, `DELETED`

## Template Manifest Format

Templates must include a `manifest.json` file:

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
    },
    {
      "name": "git_repo_url",
      "label": "Git Repository URL",
      "type": "text",
      "required": true
    }
  ]
}
```

### Manifest Fields

- `enabled` (boolean, required) - Only enabled templates are shown
- `id` (string, required) - Unique template identifier
- `name` (string, required) - Display name
- `description` (string, required) - Template description
- `icon` (string, optional) - Emoji icon (default: 📦)
- `image_path` (string, optional) - Path to custom icon image (relative to template dir)
- `category` (string, optional) - Category for grouping (default: "Other")
- `variables` (array, required) - User-configurable variables

### Variable Types

- `text` - String input
- `number` - Numeric input
- `select` - Dropdown (requires `options` array)

## Terraform Template Requirements

Each template directory must contain:

1. `manifest.json` - Template metadata
2. `main.tf` - Main Terraform configuration
3. `variables.tf` - Variable definitions
4. `provider.tf` - Provider configuration (optional)
5. `outputs.tf` - Output definitions (recommended)

### Important: Outputs

Define outputs in `outputs.tf` to expose deployment information:

```hcl
output "loadbalancer_ip" {
  description = "Public IP of the load balancer"
  value       = openstack_networking_floatingip_v2.lb_ip.address
}

output "instance_ips" {
  description = "Private IPs of instances"
  value       = openstack_compute_instance_v2.web[*].access_ip_v4
}
```

The CMP will automatically capture and display these outputs. Priority is given to:

- `loadbalancer_ip`, `lb_ip`
- `public_ip`, `ip`
- `url`, `endpoint`, `dns`, `address`

## API Changes

### New Endpoints

- `POST /api/catalog/sync` - Force sync template repository
- `GET /api/deployments/{id}/outputs` - Get Terraform outputs

### Modified Endpoints

- `GET /api/catalog/` - Now returns templates from Git repo
- `POST /api/deployments/` - Now triggers Terraform deployment
- `DELETE /api/deployments/{id}` - Now runs `terraform destroy`

### Removed Endpoints

- `GET /api/deployments/{id}/health` - AWS-specific health check

## Deployment Flow

1. **User selects template** → Frontend calls `POST /api/deployments/`
2. **Backend creates deployment record** → Status: `PENDING`
3. **Background task starts:**
   - Status: `INITIALIZING` → Run `terraform init`
   - Status: `PLANNING` → Run `terraform plan`
   - Status: `DEPLOYING` → Run `terraform apply`
   - Status: `RUNNING` → Capture outputs, display key info
4. **Frontend polls** → `GET /api/deployments/{id}` every 3s
5. **Outputs displayed** → LoadBalancer IP, URLs, etc.

## Deletion Flow

1. **User confirms deletion** → Frontend calls `DELETE /api/deployments/{id}`
2. **Background task starts:**
   - Status: `DELETING` → Run `terraform destroy`
   - Status: `DELETED` → Cleanup complete

## Migration Steps

### 1. Install Dependencies

```bash
cd backend
poetry add gitpython
poetry install
```

### 2. Run Database Migration

```bash
poetry run alembic upgrade head
```

This will:

- Drop old OpenStack/AWS columns
- Add new Terraform columns
- Preserve existing deployment records (but they won't be functional)

### 3. Install Terraform

Ensure Terraform is installed and available in PATH:

```bash
terraform --version
```

### 4. Configure Environment

No changes needed to `.env` - OpenStack credentials are still used by Terraform templates.

### 5. Start Application

```bash
poetry run uvicorn app.main:app --reload --port 8000
```

On startup, the app will:

- Clone the template repository
- Load available templates
- Create necessary directories (`./data/templates`, `./data/terraform_states`)

## Directory Structure

```
backend/
├── app/
│   ├── services/
│   │   ├── template_repository.py      # NEW: Git repo manager
│   │   ├── terraform_executor.py       # NEW: Terraform wrapper
│   │   ├── terraform_orchestrator.py   # NEW: Deployment orchestrator
│   │   ├── catalog_service.py          # UPDATED: Uses Git repo
│   │   ├── saga_orchestrator.py        # DEPRECATED: Old SAGA logic
│   │   ├── openstack_service.py        # DEPRECATED: Direct OpenStack calls
│   │   └── aws_service.py              # DEPRECATED: Direct AWS calls
│   ├── models/
│   │   └── deployment.py               # UPDATED: New schema
│   ├── schemas/
│   │   ├── catalog.py                  # UPDATED: Added image_path, enabled
│   │   └── deployment.py               # UPDATED: New fields
│   └── routers/
│       ├── catalog.py                  # UPDATED: Added /sync endpoint
│       └── deployments.py              # UPDATED: Uses Terraform orchestrator
├── data/                               # NEW: Runtime data
│   ├── templates/                      # Cloned Git repo
│   └── terraform_states/               # Terraform state per deployment
└── alembic/
    └── versions/
        └── b9751e077ee4_*.py           # NEW: Migration script
```

## Frontend Updates Needed

The frontend needs updates to:

1. **Display Terraform outputs** instead of hardcoded fields
2. **Show new deployment statuses** (INITIALIZING, PLANNING)
3. **Handle template images** (if `image_path` is provided)
4. **Remove AWS health check** (no longer applicable)

### Example: Displaying Outputs

```typescript
// Old approach
<div>AWS ALB: {deployment.aws_alb_dns}</div>
<div>DB IP: {deployment.os_vm_db1_ip}</div>

// New approach
{deployment.terraform_outputs && (
  <div>
    {Object.entries(JSON.parse(deployment.terraform_outputs)).map(([key, value]) => (
      <div key={key}>{key}: {value}</div>
    ))}
  </div>
)}
```

## Testing

### Test Template Sync

```bash
curl http://localhost:8000/api/catalog/sync -X POST
```

### Test Template Listing

```bash
curl http://localhost:8000/api/catalog/
```

Should return templates from the Git repo with `enabled: true`.

### Test Deployment

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

### Monitor Deployment

```bash
# Get deployment status
curl http://localhost:8000/api/deployments/1

# Get outputs
curl http://localhost:8000/api/deployments/1/outputs
```

## Troubleshooting

### Template Repository Not Cloning

Check logs for Git errors. Ensure:

- Internet connectivity
- Git is installed
- Repository URL is accessible

### Terraform Commands Failing

Check:

- Terraform is installed and in PATH
- OpenStack credentials in `.env` are correct
- Template Terraform files are valid

### Outputs Not Captured

Ensure the template defines outputs in `outputs.tf`. The CMP automatically captures all outputs.

### State File Issues

State files are stored in `./data/terraform_states/{deployment_name}/`. If corrupted:

1. Delete the deployment from CMP
2. Manually clean up cloud resources
3. Remove the state directory

## Rollback Plan

If issues occur, you can rollback:

```bash
# Downgrade database
poetry run alembic downgrade -1

# Revert code changes
git revert <commit-hash>
```

Note: Existing Terraform deployments will need manual cleanup.

## Future Enhancements

- [ ] WebSocket support for real-time Terraform output streaming
- [ ] Terraform plan preview before apply
- [ ] Multi-cloud provider support (AWS, Azure, GCP)
- [ ] Template versioning
- [ ] Deployment history and audit logs
- [ ] Resource cost estimation
- [ ] Automated testing of templates
- [ ] Template marketplace/sharing

## Support

For issues or questions:

- Check logs: `./backend/logs/`
- Review Terraform state: `./data/terraform_states/`
- Consult template repository: https://github.com/3-Istor/ia-project-template
