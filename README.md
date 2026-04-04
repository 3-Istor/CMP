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

ARCL CMP is an internal web platform that lets your team deploy full application stacks on a **hybrid cloud** (private OpenStack + public AWS) in a few clicks — no CLI, no Terraform, no IT ticket.

Each deployment provisions **4 VMs automatically**:

- **2 OpenStack VMs** — stateful layer (database)
- **2 AWS instances** — stateless layer (web/app) behind an Auto Scaling Group + Load Balancer

If the AWS step fails, the platform **automatically rolls back** the OpenStack VMs (SAGA pattern). The UI shows live progress at every step.

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
│  POST /api/deployments  →  BackgroundTask: SAGA Orchestrator│
│                                    │                        │
│                         ┌──────────▼──────────┐            │
│                         │  Step 1: OpenStack  │            │
│                         │  2× DB VMs via SDK  │            │
│                         └──────────┬──────────┘            │
│                                    │ success                │
│                         ┌──────────▼──────────┐            │
│                         │   Step 2: AWS        │            │
│                         │  ASG + ALB via boto3 │            │
│                         └──────────┬──────────┘            │
│                                    │ failure → rollback OS  │
└────────────────────────────────────┼────────────────────────┘
                                     │
              ┌──────────────────────┴──────────────────────┐
              │                                             │
   ┌──────────▼──────────┐                    ┌────────────▼────────────┐
   │  Private OpenStack  │◄──── WireGuard ────►│     Public AWS          │
   │  192.168.1.0/24     │       VPN           │     10.1.0.0/16         │
   │  172.16.0.0/24      │   10.0.0.0/24       │  ASG · ALB · t3.micro   │
   └─────────────────────┘                    └─────────────────────────┘
```

---

## Prerequisites

| Tool    | Version | Install                            |
| ------- | ------- | ---------------------------------- |
| Python  | 3.12+   | [python.org](https://python.org)   |
| Poetry  | 2.0+    | `pip install poetry`               |
| Node.js | 18+     | [nodejs.org](https://nodejs.org)   |
| npm     | 9+      | bundled with Node                  |
| Git     | any     | [git-scm.com](https://git-scm.com) |

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/3-Istor/arcl-cmp.git
cd arcl-cmp
```

### 2. Backend setup

```bash
cd backend

# Install dependencies
poetry install

# Copy and fill in your credentials
cp .env.example .env
# Edit .env with your AWS + OpenStack credentials (see Configuration section)

# Run database migrations
poetry run alembic upgrade head

# Start the API server
poetry run uvicorn app.main:app --reload --port 8000
```

The API is now available at **http://localhost:8000**
Interactive docs (Swagger UI): **http://localhost:8000/docs**

### 3. Frontend setup

```bash
cd frontend

# Install dependencies
npm install

# Copy environment file
cp .env.local.example .env.local

# Start the dev server
npm run dev
```

The UI is now available at **http://localhost:3000**

> **No backend? No problem.** The frontend loads the App Catalog from a static fallback, so you can browse and test the UI without any cloud credentials configured.

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
│   │   │   ├── catalog.py         # GET /api/catalog
│   │   │   └── deployments.py     # CRUD /api/deployments
│   │   ├── schemas/
│   │   │   ├── catalog.py         # Pydantic schemas for templates
│   │   │   └── deployment.py      # Pydantic schemas for deployments
│   │   ├── services/
│   │   │   ├── saga_orchestrator.py   # SAGA pattern: deploy + rollback
│   │   │   ├── openstack_service.py   # openstacksdk: provision DB VMs
│   │   │   ├── aws_service.py         # boto3: ASG + ALB + Launch Template
│   │   │   └── catalog_service.py     # Static app template definitions
│   │   └── main.py                # FastAPI app entry point
│   ├── alembic/                   # Database migrations
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

| Method   | Endpoint                       | Description                       |
| -------- | ------------------------------ | --------------------------------- |
| `GET`    | `/health`                      | Health check                      |
| `GET`    | `/api/catalog/`                | List all app templates            |
| `GET`    | `/api/catalog/{id}`            | Get a specific template           |
| `GET`    | `/api/deployments/`            | List all deployments              |
| `POST`   | `/api/deployments/`            | Create & start a deployment (202) |
| `GET`    | `/api/deployments/{id}`        | Get deployment status             |
| `GET`    | `/api/deployments/{id}/health` | Get live AWS ASG health           |
| `DELETE` | `/api/deployments/{id}`        | Delete all cloud resources (202)  |

Full interactive docs available at `http://localhost:8000/docs` when the backend is running.

---

## App Catalog

| App                     | Category   | DB (OpenStack)     | Web (AWS)       |
| ----------------------- | ---------- | ------------------ | --------------- |
| 📝 WordPress            | CMS        | MySQL 8            | Nginx + PHP 8.1 |
| ☁️ Nextcloud            | Storage    | PostgreSQL         | Nextcloud app   |
| 🦊 GitLab CE            | DevOps     | PostgreSQL + Redis | GitLab web      |
| 📊 Grafana + Prometheus | Monitoring | Prometheus         | Grafana         |

Each template can be deployed **multiple times** with different names.

---

## SAGA Pattern — Design for Failure

The orchestrator (`saga_orchestrator.py`) guarantees a clean state at all times:

```
Step 1 — OpenStack VMs
  ✓ success → continue
  ✗ failure → mark FAILED (nothing to clean up)

Step 2 — AWS ASG + ALB
  ✓ success → mark RUNNING
  ✗ failure → destroy OpenStack VMs → mark FAILED
```

This means you will **never** have orphaned VMs consuming budget.

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

AWS costs are strictly controlled:

- Instance type locked to `t3.micro` or `t4g.nano` (enforced in code)
- ASG capped at `MaxSize=2` per deployment
- Estimated cost per running app: **~$15–20/month**
- Hard budget limit: **$100 total**

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

### Building for production

```bash
# Frontend
cd frontend
npm run build
npm start

# Backend (with gunicorn)
cd backend
poetry run gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

---

## Roadmap

- [ ] Monitoring dashboard (Grafana/Prometheus integration)
- [ ] WebSocket support for real-time deployment logs
- [ ] K3s migration for CMP hosting
- [ ] GitHub Actions CI/CD pipeline
- [ ] Multi-user support with OpenStack project isolation

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
