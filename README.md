<div align="center">

# ⚡ ARCL CMP

### Hybrid Cloud Management Platform

**Self-service deployment of application stacks across OpenStack + AWS**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-15-black?logo=next.js)](https://nextjs.org)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

</div>

---

## What is ARCL CMP?

ARCL CMP is an internal web platform that lets your team deploy full application stacks on a **hybrid cloud** (private OpenStack + public AWS) in a few clicks — no CLI, no Terraform knowledge required, no IT ticket.

The platform uses **Terraform templates** loaded from a Git repository, allowing flexible deployment of any infrastructure configuration. Each template defines the resources to provision, and the CMP handles the entire lifecycle: deployment, tracking, and cleanup.

**Key Features:**

- 🚀 **Template-based deployments** - Load templates from Git repository
- 🔄 **Automatic syncing** - Templates sync every 24 hours
- 📊 **Real-time tracking** - Monitor deployment progress live
- 🎯 **Output capture** - Automatically display IPs, URLs, and endpoints
- 🗑️ **Clean destruction** - One-click resource cleanup
- 🌐 **Multi-cloud ready** - Currently OpenStack, AWS support coming soon

---

## Screenshots

> App Catalog → click Deploy → configure → watch live progress → running app with public URL

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Browser                         │
│              Next.js 15 + Tailwind + Shadcn/UI              │
│         (polls /api/deployments/:id every 3s for status)    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼──────────────────────────────────┐
│                    FastAPI Backend                          │
│              Python 3.12 · SQLite · Alembic                 │
│                                                             │
│  POST /api/deployments  →  BackgroundTask: Terraform        │
│                                    │                        │
│                         ┌──────────▼──────────┐            │
│                         │  Template Repository │            │
│                         │  Git Clone/Sync      │            │
│                         └──────────┬──────────┘            │
│                                    │                        │
│                         ┌──────────▼──────────┐            │
│                         │  Terraform Executor  │            │
│                         │  init → plan → apply │            │
│                         └──────────┬──────────┘            │
│                                    │                        │
│                         ┌──────────▼──────────┐            │
│                         │  Capture Outputs     │            │
│                         │  IPs, URLs, etc.     │            │
│                         └──────────────────────┘            │
└────────────────────────────────────┼────────────────────────┘
                                     │
              ┌──────────────────────┴──────────────────────┐
              │                                             │
   ┌──────────▼──────────┐                    ┌────────────▼────────────┐
   │  Private OpenStack  │◄──── WireGuard ────►│     Public AWS          │
   │  192.168.1.0/24     │       VPN           │     10.1.0.0/16         │
   │  172.16.0.0/24      │   10.0.0.0/24       │  (Future Support)       │
   └─────────────────────┘                    └─────────────────────────┘
```

### Template Repository

Templates are loaded from: https://github.com/3-Istor/ia-project-template

- Automatically cloned on startup
- Synced every 24 hours
- Only enabled templates are shown
- Each template includes Terraform configuration and metadata

---

## Prerequisites

| Tool      | Version | Install                              |
| --------- | ------- | ------------------------------------ |
| Python    | 3.12+   | [python.org](https://python.org)     |
| Poetry    | 2.0+    | `pip install poetry`                 |
| Terraform | 1.0+    | [terraform.io](https://terraform.io) |
| Node.js   | 18+     | [nodejs.org](https://nodejs.org)     |
| npm       | 9+      | bundled with Node                    |
| Git       | any     | [git-scm.com](https://git-scm.com)   |

---

## Quick Start

### 1. Clone & Setup

```bash
# Clone the repository
git clone https://github.com/3-Istor/arcl-cmp.git
cd arcl-cmp

# Run automated setup (installs dependencies, creates env files, runs migrations)
./setup.sh
```

### 2. Configure Credentials

Edit `backend/.env` with your OpenStack credentials:

```bash
nano backend/.env
```

Required variables:

```env
OS_AUTH_URL=http://192.168.1.210:5000/v3
OS_USERNAME=your_username
OS_PASSWORD=your_password
OS_PROJECT_NAME=your_project
```

### 3. Start Backend

```bash
cd backend
poetry run uvicorn app.main:app --reload --port 8000
```

Backend will be available at http://localhost:8000

### 4. Start Frontend (in a new terminal)

```bash
cd frontend
npm run dev
```

Frontend will be available at http://localhost:3000

### Manual Setup

See [SETUP_INSTRUCTIONS.md](SETUP_INSTRUCTIONS.md) for detailed manual setup steps.

---

## Configuration

### Backend — `backend/.env`

```env
# ── AWS ──────────────────────────────────────────────────────
# Never commit real credentials. Use IAM roles in production.
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_DEFAULT_REGION=eu-west-3

# Budget constraint: ONLY t3.micro or t4g.nano allowed
AWS_INSTANCE_TYPE=t3.micro

# ── OpenStack ─────────────────────────────────────────────────
OS_AUTH_URL=http://192.168.1.210:5000/v3
OS_USERNAME=arcl-cmp
OS_PASSWORD=your_openstack_password
OS_PROJECT_NAME=3-istor-cloud
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
```

### Frontend — `frontend/.env.local`

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

---

## Project Structure

```
arcl-cmp/
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py          # Settings from env vars
│   │   │   └── database.py        # SQLAlchemy engine + session
│   │   ├── models/
│   │   │   └── deployment.py      # Deployment DB model + status enum
│   │   ├── routers/
│   │   │   ├── catalog.py         # GET /api/catalog + sync
│   │   │   └── deployments.py     # CRUD /api/deployments
│   │   ├── schemas/
│   │   │   ├── catalog.py         # Pydantic schemas for templates
│   │   │   └── deployment.py      # Pydantic schemas for deployments
│   │   ├── services/
│   │   │   ├── template_repository.py  # Git repo management
│   │   │   ├── terraform_executor.py   # Terraform wrapper
│   │   │   ├── terraform_orchestrator.py # Deployment orchestrator
│   │   │   └── catalog_service.py      # Template loading
│   │   └── main.py                # FastAPI app entry point
│   ├── alembic/                   # Database migrations
│   ├── data/                      # Runtime data (auto-created)
│   │   ├── templates/             # Cloned Git repository
│   │   └── terraform_states/      # Terraform state files
│   ├── pyproject.toml
│   └── .env.example
│
├── frontend/
│   └── src/
│       ├── app/
│       │   ├── layout.tsx         # Root layout + font + Toaster
│       │   ├── page.tsx           # Main page (Catalog + Dashboard)
│       │   └── globals.css        # Tailwind + theme tokens
│       ├── components/
│       │   ├── catalog/
│       │   │   ├── CatalogGrid.tsx    # App template cards
│       │   │   └── DeployModal.tsx    # Config form dialog
│       │   ├── dashboard/
│       │   │   ├── Dashboard.tsx      # Deployed apps grid
│       │   │   └── DeploymentCard.tsx # Card with stepper + delete
│       │   ├── stepper/
│       │   │   └── DeploymentStepper.tsx  # Live progress tracker
│       │   └── ui/                    # Shadcn/UI components
│       ├── lib/
│       │   ├── api.ts             # Typed fetch wrappers
│       │   └── hooks.ts           # useDeploymentPolling, useDeploymentsList
│       └── types/
│           └── index.ts           # Shared TypeScript types
│
├── docs/
│   └── ia/
│       └── ARCL_AI_CONTEXT.md     # AI context: network, SAGA, budget rules
├── .cursorrules                   # AI coding rules for this repo
└── README.md
```

---

## API Reference

| Method   | Endpoint                        | Description                       |
| -------- | ------------------------------- | --------------------------------- |
| `GET`    | `/health`                       | Health check                      |
| `GET`    | `/api/catalog/`                 | List all enabled templates        |
| `GET`    | `/api/catalog/{id}`             | Get a specific template           |
| `POST`   | `/api/catalog/sync`             | Force sync template repository    |
| `GET`    | `/api/deployments/`             | List all deployments              |
| `POST`   | `/api/deployments/`             | Create & start a deployment (202) |
| `GET`    | `/api/deployments/{id}`         | Get deployment status             |
| `GET`    | `/api/deployments/{id}/outputs` | Get Terraform outputs             |
| `DELETE` | `/api/deployments/{id}`         | Delete all cloud resources (202)  |

Full interactive docs available at `http://localhost:8000/docs` when the backend is running.

---

## App Catalog

Templates are dynamically loaded from the Git repository. Current available templates:

| App              | Category | Provider  | Description                |
| ---------------- | -------- | --------- | -------------------------- |
| 🌐 Nginx Website | Web      | OpenStack | Static website with Nginx  |
| 🌐 Git Website   | Web      | OpenStack | Deploy from Git repository |

Each template can be deployed **multiple times** with different configurations.

### Adding New Templates

1. Fork https://github.com/3-Istor/ia-project-template
2. Add your template directory with `manifest.json` and Terraform files
3. Set `"enabled": true` in the manifest
4. The CMP will automatically sync and load your template

See [backend/TERRAFORM_MIGRATION.md](backend/TERRAFORM_MIGRATION.md) for template requirements.

---

## Deployment Lifecycle

The platform manages the full lifecycle of Terraform-based deployments:

```
User Action → Template Selection → Configuration
                    ↓
            Terraform Initialize
                    ↓
              Terraform Plan
                    ↓
              Terraform Apply
                    ↓
            Capture Outputs (IPs, URLs)
                    ↓
              Status: RUNNING
```

On deletion:

```
User Confirms → Terraform Destroy → Status: DELETED
```

All Terraform state is managed automatically, ensuring clean deployments and deletions.

---

## Network Topology

> These CIDRs are hardcoded in the codebase. Do not change them.

| Network            | CIDR             | Notes                     |
| ------------------ | ---------------- | ------------------------- |
| WireGuard VPN      | `10.0.0.0/24`    | Nodes: .1, .2, .3         |
| OpenStack External | `192.168.1.0/24` | GW: .254, Kolla VIP: .210 |
| OpenStack Internal | `172.16.0.0/24`  | Project: 3-istor-cloud    |
| AWS VPC            | `10.1.0.0/16`    | Non-overlapping with VPN  |

---

## Budget

Cloud costs are controlled through template configuration:

- Templates define resource sizes and counts
- OpenStack resources are managed by your private cloud
- AWS support coming soon with budget constraints
- Estimated cost per deployment varies by template

Monitor resource usage through the dashboard's resource count display.

---

## Running the Project

### Development Mode

Start both backend and frontend in separate terminals:

```bash
# Terminal 1 - Backend
cd backend
poetry run uvicorn app.main:app --reload --port 8000

# Terminal 2 - Frontend
cd frontend
npm run dev
```

Access the application:

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Production Mode

```bash
# Build frontend
cd frontend
npm run build
npm start

# Run backend with gunicorn
cd backend
poetry run gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Development

### Running tests (backend)

```bash
cd backend
poetry run pytest
```

### Linting & formatting

```bash
# Backend
cd backend
poetry run black app/
poetry run isort app/
poetry run pylint app/

# Frontend
cd frontend
npm run lint
```

---

## Roadmap

- [x] Terraform-based deployment system
- [x] Git repository template loading
- [x] Automatic output capture
- [ ] AWS template support
- [ ] WebSocket support for real-time Terraform logs
- [ ] Template versioning
- [ ] Multi-user support with RBAC
- [ ] Cost estimation per deployment
- [ ] Monitoring dashboard integration
- [ ] K3s migration for CMP hosting
- [ ] GitHub Actions CI/CD pipeline

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Follow the coding standards in `.cursorrules`
4. Commit with conventional commits: `feat:`, `fix:`, `chore:`
5. Open a Pull Request against `main`

---

## Team

Built by the **3-Istor** student team — [github.com/3-Istor](https://github.com/3-Istor)

---

<div align="center">
<sub>ARCL CMP · MIT License · Made with ☕ by 3-Istor</sub>
</div>
