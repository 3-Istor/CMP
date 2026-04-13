# ARCL CMP - Setup Instructions

## Overview

This guide will help you set up the ARCL Cloud Management Platform with the new Terraform-based deployment system.

## Prerequisites

Ensure you have the following installed:

- **Python 3.12+** - [python.org](https://python.org)
- **Poetry 2.0+** - `pip install poetry`
- **Terraform** - [terraform.io/downloads](https://terraform.io/downloads)
- **Git** - [git-scm.com](https://git-scm.com)
- **Node.js 18+** - [nodejs.org](https://nodejs.org)

Verify installations:

```bash
python --version    # Should be 3.12+
poetry --version    # Should be 2.0+
terraform --version # Should be 1.0+
git --version
node --version      # Should be 18+
```

## Backend Setup

### 1. Install Dependencies

```bash
cd backend
poetry install
```

This will install all required Python packages including:

- FastAPI
- SQLAlchemy
- Alembic
- GitPython (for template repository management)
- OpenStack SDK
- Boto3 (for future AWS support)

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# ── OpenStack ─────────────────────────────────────────────────
OS_AUTH_URL=http://192.168.1.210:5000/v3
OS_USERNAME=your_username
OS_PASSWORD=your_password
OS_PROJECT_NAME=your_project
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default

# ── AWS (Optional - for future templates) ────────────────────
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=eu-west-3

# ── Database ──────────────────────────────────────────────────
DATABASE_URL=sqlite:///./arcl.db
```

### 3. Run Database Migrations

```bash
poetry run alembic upgrade head
```

This will:

- Create the deployments table with new Terraform-based schema
- Set up all necessary database structures

### 4. Verify Template Repository

On first startup, the application will automatically:

- Clone https://github.com/3-Istor/ia-project-template
- Store it in `./data/templates/`
- Load available templates

You can verify this manually:

```bash
# Start the backend
poetry run uvicorn app.main:app --reload --port 8000

# In another terminal, test the catalog endpoint
curl http://localhost:8000/api/catalog/
```

You should see templates like `openstack-nginx` and `openstack-web-git`.

### 5. Test Terraform Integration

Verify Terraform is accessible:

```bash
terraform --version
```

The backend will use Terraform to:

- Initialize templates
- Plan deployments
- Apply infrastructure
- Capture outputs (IPs, URLs)
- Destroy resources

## Frontend Setup

### 1. Install Dependencies

```bash
cd frontend
npm install
```

### 2. Configure Environment

```bash
cp .env.local.example .env.local
```

Edit `.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

### 3. Start Development Server

```bash
npm run dev
```

The frontend will be available at http://localhost:3000

## Template Repository Structure

The CMP loads templates from: https://github.com/3-Istor/ia-project-template

### Current Available Templates

1. **openstack-nginx** - Nginx web server on OpenStack
2. **openstack-web-git** - Git-based static website on OpenStack

### Template Requirements

Each template must have:

```
templates/
└── openstack-nginx/
    ├── manifest.json      # Template metadata
    ├── main.tf           # Terraform configuration
    ├── variables.tf      # Variable definitions
    ├── provider.tf       # Provider setup
    ├── outputs.tf        # Output definitions
    └── icon.png          # Optional custom icon
```

### Example manifest.json

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

## Deployment Workflow

### 1. Browse Templates

Navigate to http://localhost:3000 and browse available templates.

### 2. Deploy an Application

1. Click "Deploy" on a template
2. Configure variables (instance count, etc.)
3. Click "Deploy"
4. Monitor progress in real-time

The backend will:

- Initialize Terraform
- Plan the deployment
- Apply infrastructure
- Capture outputs (LoadBalancer IP, etc.)

### 3. View Deployment Details

The dashboard shows:

- Deployment status (Pending → Initializing → Planning → Deploying → Running)
- Step messages
- Terraform outputs (IPs, URLs)
- Resource count

### 4. Delete Deployment

1. Click "Delete" on a deployment
2. Confirm deletion
3. Backend runs `terraform destroy`
4. All resources are cleaned up

## API Endpoints

### Catalog

- `GET /api/catalog/` - List all enabled templates
- `GET /api/catalog/{id}` - Get specific template
- `POST /api/catalog/sync` - Force sync template repository

### Deployments

- `GET /api/deployments/` - List all deployments
- `POST /api/deployments/` - Create new deployment
- `GET /api/deployments/{id}` - Get deployment details
- `GET /api/deployments/{id}/outputs` - Get Terraform outputs
- `DELETE /api/deployments/{id}` - Destroy deployment

### Health

- `GET /health` - API health check

## Directory Structure

After setup, your directory structure will be:

```
arcl-cmp/
├── backend/
│   ├── app/                    # Application code
│   ├── alembic/               # Database migrations
│   ├── data/                  # Runtime data (created automatically)
│   │   ├── templates/         # Cloned Git repository
│   │   └── terraform_states/  # Terraform state files
│   ├── arcl.db               # SQLite database
│   └── .env                  # Configuration
├── frontend/
│   ├── src/                  # React/Next.js code
│   └── .env.local           # Frontend config
└── docs/
    └── ia/                  # Documentation
```

## Syncing Templates

The template repository is automatically synced:

- On application startup
- Every 24 hours

To force a sync:

```bash
curl -X POST http://localhost:8000/api/catalog/sync
```

Or restart the backend.

## Troubleshooting

### Backend won't start

**Error: "ModuleNotFoundError: No module named 'git'"**

Solution:

```bash
cd backend
poetry add gitpython
poetry install
```

**Error: "Terraform not found"**

Solution: Install Terraform from https://terraform.io/downloads

### Templates not loading

**Check logs:**

```bash
# Backend logs will show Git clone/sync status
poetry run uvicorn app.main:app --reload --port 8000
```

**Manually verify repository:**

```bash
ls -la backend/data/templates/
```

Should contain the cloned repository.

### Deployment fails

**Check Terraform logs:**

Terraform output is captured in the deployment `step_message`. Check:

```bash
curl http://localhost:8000/api/deployments/1
```

**Verify credentials:**

Ensure OpenStack credentials in `.env` are correct:

```bash
# Test OpenStack connection
openstack server list
```

**Check state files:**

```bash
ls -la backend/data/terraform_states/
```

### Database migration issues

**Reset database (WARNING: deletes all data):**

```bash
cd backend
rm arcl.db
poetry run alembic upgrade head
```

## Production Deployment

### Backend

Use Gunicorn with Uvicorn workers:

```bash
cd backend
poetry run gunicorn app.main:app \
  -w 4 \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

### Frontend

Build and serve:

```bash
cd frontend
npm run build
npm start
```

Or use a reverse proxy (Nginx, Caddy) to serve the built files.

### Environment Variables

In production:

- Use PostgreSQL instead of SQLite
- Store `.env` securely (not in Git)
- Use environment variables or secrets management
- Enable HTTPS
- Configure CORS properly

## Next Steps

1. **Explore Templates** - Browse available templates at http://localhost:3000
2. **Deploy Test App** - Try deploying `openstack-nginx`
3. **Monitor Outputs** - Check LoadBalancer IPs and URLs
4. **Create Custom Template** - Fork the template repository and add your own
5. **Configure Production** - Set up proper database, secrets, and hosting

## Support

- **Documentation**: See `backend/TERRAFORM_MIGRATION.md` for technical details
- **Template Repository**: https://github.com/3-Istor/ia-project-template
- **Issues**: Check logs in `backend/` and Terraform state in `backend/data/terraform_states/`

## Quick Reference

```bash
# Backend
cd backend
poetry install                              # Install dependencies
poetry run alembic upgrade head            # Run migrations
poetry run uvicorn app.main:app --reload   # Start dev server

# Frontend
cd frontend
npm install                                # Install dependencies
npm run dev                                # Start dev server

# Sync templates
curl -X POST http://localhost:8000/api/catalog/sync

# Test deployment
curl -X POST http://localhost:8000/api/deployments/ \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "template_id": "openstack-nginx", "app_config": {"instance_count": 2}}'
```
