# Quick Reference - ARCL CMP

## Setup

```bash
# Automated setup
./setup.sh

# Manual setup
cd backend && poetry install && poetry run alembic upgrade head
cd frontend && npm install
```

## Start Services

```bash
# Backend (Terminal 1)
cd backend
poetry run uvicorn app.main:app --reload --port 8000

# Frontend (Terminal 2)
cd frontend
npm run dev
```

## API Endpoints

### Catalog

```bash
# List templates
GET /api/catalog/

# Get specific template
GET /api/catalog/{id}

# Force sync templates
POST /api/catalog/sync
```

### Deployments

```bash
# List deployments
GET /api/deployments/

# Create deployment
POST /api/deployments/
{
  "name": "my-app",
  "template_id": "openstack-nginx",
  "app_config": {"instance_count": 2}
}

# Get deployment status
GET /api/deployments/{id}

# Get outputs
GET /api/deployments/{id}/outputs

# Delete deployment
DELETE /api/deployments/{id}
```

## Deployment Statuses

- `PENDING` - Queued
- `INITIALIZING` - Running terraform init
- `PLANNING` - Running terraform plan
- `DEPLOYING` - Running terraform apply
- `RUNNING` - Successfully deployed
- `FAILED` - Deployment failed
- `DELETING` - Running terraform destroy
- `DELETED` - Resources destroyed

## Template Manifest

```json
{
  "enabled": true,
  "id": "template-id",
  "name": "Template Name",
  "description": "Description",
  "icon": "🌐",
  "image_path": "icon.png",
  "category": "Web",
  "variables": [
    {
      "name": "var_name",
      "label": "Display Label",
      "type": "text|number|select",
      "default": "value",
      "required": true
    }
  ]
}
```

## Terraform Template Structure

```
templates/my-template/
├── manifest.json      # Required
├── main.tf           # Required
├── variables.tf      # Required
├── provider.tf       # Optional
├── outputs.tf        # Recommended
└── icon.png          # Optional
```

## Common Commands

```bash
# Sync templates
curl -X POST http://localhost:8000/api/catalog/sync

# Deploy app
curl -X POST http://localhost:8000/api/deployments/ \
  -H "Content-Type: application/json" \
  -d '{"name":"test","template_id":"openstack-nginx","app_config":{}}'

# Check status
curl http://localhost:8000/api/deployments/1

# Get outputs
curl http://localhost:8000/api/deployments/1/outputs

# Delete
curl -X DELETE http://localhost:8000/api/deployments/1
```

## Directory Structure

```
backend/
├── data/
│   ├── templates/           # Git repo clone
│   └── terraform_states/    # State files per deployment
├── app/
│   ├── services/
│   │   ├── template_repository.py
│   │   ├── terraform_executor.py
│   │   └── terraform_orchestrator.py
│   ├── models/deployment.py
│   ├── schemas/
│   └── routers/
└── alembic/
```

## Troubleshooting

### Templates not loading

```bash
# Check if repo is cloned
ls -la backend/data/templates/

# Force sync
curl -X POST http://localhost:8000/api/catalog/sync
```

### Deployment fails

```bash
# Check deployment status
curl http://localhost:8000/api/deployments/{id}

# Check Terraform state
ls -la backend/data/terraform_states/{deployment-name}/

# Check logs
tail -f backend/logs/app.log
```

### Database issues

```bash
# Reset database (WARNING: deletes all data)
cd backend
rm arcl.db
poetry run alembic upgrade head
```

## Environment Variables

```env
# OpenStack (required)
OS_AUTH_URL=http://192.168.1.210:5000/v3
OS_USERNAME=username
OS_PASSWORD=password
OS_PROJECT_NAME=project
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default

# AWS (optional, for future templates)
AWS_ACCESS_KEY_ID=key
AWS_SECRET_ACCESS_KEY=secret
AWS_DEFAULT_REGION=eu-west-3
```

## Key Files

- `backend/app/services/template_repository.py` - Git repo management
- `backend/app/services/terraform_executor.py` - Terraform wrapper
- `backend/app/services/terraform_orchestrator.py` - Deployment orchestrator
- `backend/app/models/deployment.py` - Database model
- `backend/alembic/versions/b9751e077ee4_*.py` - Migration

## Documentation

- `SETUP_INSTRUCTIONS.md` - Detailed setup guide
- `backend/TERRAFORM_MIGRATION.md` - Technical migration details
- `IMPLEMENTATION_SUMMARY.md` - Complete implementation overview
- `README.md` - Project overview

## Template Repository

https://github.com/3-Istor/ia-project-template

## URLs

- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs
- Frontend: http://localhost:3000
