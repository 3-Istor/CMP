# CMP

## Rôle

Portail de gestion de la CNP : catalogue, projets, déploiements et suivi des ressources. Le backend orchestre Terraform et publie les projets et applications dans le registre Git ; le frontend expose ces opérations aux utilisateurs.

## Technologies

Python 3.12+, FastAPI, SQLAlchemy/Alembic ; TypeScript, Next.js 16, React 19, Tailwind CSS ; Terraform, Docker, Helm et GitHub Actions.

## Entrées

| Origine / destinataire | Contenu et transmission |
| --- | --- |
| app-templates | Clone Git du catalogue : `templates/*/manifest.json`, modules et configurations Terraform. |
| cnp-projects | Lecture des `registry/projects/*.yaml` via la GitHub App avant mise à jour du registre. |
| Services de plateforme | Identité Keycloak, secrets Vault, API GitHub, Cloudflare et fournisseurs cloud ; paramètres de déploiement saisis par les utilisateurs. |
| cnp-docs | Documentation déclarée comme sous-module dans `.kiro/steering/docs` pour les développeurs et assistants. |

## Sorties et consommateurs

| Origine / destinataire | Contenu et transmission |
| --- | --- |
| cnp-projects | Création et mise à jour des ProjectRecord et de leur liste d’applications par commits via la GitHub App. |
| Dépôts applicatifs | Création et configuration via les templates Terraform exécutés : sources initiales et valeurs `deploy/*.yaml`. |
| K3s / opérateurs | Images backend/frontend et chart `arcl-cmp` publiés dans GHCR ; consommés par la configuration GitOps de K3s. |
| Utilisateurs / clients API | API HTTP, interface web, état des opérations, URL et outputs Terraform. |

## Documentation CNP

[Fiche `CMP` et workflows inter-repo](https://github.com/3-Istor/cnp-docs/blob/main/docs/04-templates/00-github-repositories-landscape.md#cmp).

## Prerequisites

| Tool      | Version | Install                              |
| --------- | ------- | ------------------------------------ |
| Python    | 3.12+   | [python.org](https://python.org)     |
| Poetry    | 2.0+    | `pip install poetry`                 |
| Terraform | 1.0+    | [terraform.io](https://terraform.io) |
| Node.js   | Compatible avec Next.js 16 | [nodejs.org](https://nodejs.org)     |
| npm       | 9+      | bundled with Node                    |
| Git       | any     | [git-scm.com](https://git-scm.com)   |

---

## Quick Start

### 1. Clone & Setup

```bash
# Clone the repository
git clone https://github.com/3-Istor/CMP.git
cd CMP

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
OS_AUTH_URL=http://localhost:5000/v3
OS_USERNAME=your_username
OS_PASSWORD=your_password
OS_PROJECT_NAME=your_project
```

### 3. Configure Authentication (Optional)

CMP supports two authentication modes:

**Development Mode (Skip Auth)** - Recommended for local development:

```bash
cd frontend
echo "NEXT_PUBLIC_SKIP_AUTH=true" > .env.local
```

**Production Mode (Keycloak)** - For production deployment:

```bash
cd frontend
cat > .env.local << EOF
NEXT_PUBLIC_SKIP_AUTH=false
NEXTAUTH_URL=https://cmp.3istor.com
NEXTAUTH_SECRET=$(openssl rand -base64 32)
KEYCLOAK_CLIENT_ID=3-istor-openid
KEYCLOAK_CLIENT_SECRET=your-secret
KEYCLOAK_ISSUER=https://auth.3istor.com/realms/3istor
EOF
```

See [le guide des identités](https://github.com/3-Istor/cnp-docs/blob/main/docs/02-core-components/02-identity-keycloak.md) for complete authentication documentation.

### 4. Start Backend

```bash
cd backend
poetry run uvicorn app.main:app --reload --port 8000
```

Backend will be available at http://localhost:8000

### 5. Start Frontend (in a new terminal)

```bash
cd frontend
npm run dev
```

Frontend will be available at http://localhost:3000

### Manual Setup

See [backend/README.md](backend/README.md) for detailed manual setup steps.

---

## Configuration

### Backend - `backend/.env`

```env
# ── AWS ──────────────────────────────────────────────────────
# Never commit real credentials. Use IAM roles in production.
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_DEFAULT_REGION=eu-west-3

# Budget constraint: ONLY t3.micro or t4g.nano allowed
AWS_INSTANCE_TYPE=t3.micro

# ── OpenStack ─────────────────────────────────────────────────
OS_AUTH_URL=http://localhost:5000/v3
OS_USERNAME=arcl-cmp
OS_PASSWORD=your_openstack_password
OS_PROJECT_NAME=3-istor-cloud
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
```

### Frontend - `frontend/.env.local`

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

---

## Project Structure

```
CMP/
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

Le catalogue est lu dans les manifests activés de [app-templates](https://github.com/3-Istor/app-templates/tree/main/templates). Il comprend notamment le bootstrap de projet, le chemin applicatif K3s/GitOps et des templates OpenStack/AWS. Les manifests présents font foi pour les options disponibles.

Each template can be deployed **multiple times** with different configurations.

### Adding New Templates

1. Fork https://github.com/3-Istor/app-templates
2. Add your template directory with `manifest.json` and Terraform files
3. Set `"enabled": true` in the manifest
4. The CMP will automatically sync and load your template

See [le provisionneur Terraform](https://github.com/3-Istor/cnp-docs/blob/main/docs/04-templates/03-terraform-provisioner.md) for template requirements.

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
- Les templates AWS sont présents ; leur usage nécessite les accès et infrastructures correspondants
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

# Run backend with Uvicorn
cd backend
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000
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

## Docker & Kubernetes Deployment

CMP can be deployed using Docker and Kubernetes (k3s).

### Docker Compose (Local Development)

```bash
# Build and start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Kubernetes with Helm (Production)

```bash
# Quick deploy to k3s
./scripts/deploy-k3s.sh

# Or manually with Helm
helm install arcl-cmp ./helm/arcl-cmp \
  --namespace arcl-cmp \
  --create-namespace \
  --values values-secrets.yaml
```

### CI/CD with GitHub Actions

Automated workflows for building and deploying:

- **Build on tag**: Push `v*.*.*` tag to build and publish Docker images
- **Helm release**: Push `helm-v*.*.*` tag to publish Helm chart
- **Auto-test**: Runs on every push/PR

```bash
# Release Docker images
git tag v1.0.0 && git push origin v1.0.0

# Release Helm chart
git tag helm-v1.0.0 && git push origin helm-v1.0.0
```

See [le runbook CMP](https://github.com/3-Istor/cnp-docs/blob/main/docs/05-cmp-backend-api/10-cmp-onboarding-runbook.md) for detailed instructions and [helm/arcl-cmp/README.md](helm/arcl-cmp/README.md) for command reference.

---

## Évolutions

Consulter [la roadmap centrale](https://github.com/3-Istor/cnp-docs/blob/main/docs/README_ROADMAP.md) et [le chantier multicloud](https://github.com/3-Istor/cnp-docs/blob/main/docs/06-multicloud/00-index.md). Le code local contient déjà la gestion des projets/RBAC, des templates AWS et des services FinOps ; cela ne constitue pas une vérification de leur déploiement en production.

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Follow the coding standards in `.cursorrules`
4. Commit with conventional commits: `feat:`, `fix:`, `chore:`
5. Open a Pull Request against `main`

---

## Team

Built by the **3-Istor** student team - [github.com/3-Istor](https://github.com/3-Istor)

---

<div align="center">
<sub>CMP · MIT License · Made with ☕ by 3-Istor</sub>
</div>
