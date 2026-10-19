# CNP: context for AI assistants working on the CMP

This file used to describe ARCL, the first version of the project (VMs on
OpenStack and AWS, provisioned by a Python saga). That design is gone. CNP
runs apps on Kubernetes through GitOps; this is what an assistant changing
the CMP must know.

## What CNP is

An internal developer platform. Teams get **projects** (Keycloak groups,
Vault policies, an Argo CD AppProject, a namespace set) and deploy **apps**
from templates. Each app is a GitHub repository in the 3-Istor organization,
built by its CI and deployed by Argo CD with the generic Helm chart
(`infra-templates`). Users work through the CMP (`cmp.3istor.com`) or through
an AI assistant connected to the CMP's MCP server.

| Piece | Where |
|---|---|
| CMP backend | `backend/`: FastAPI, SQLAlchemy, Alembic, SQLite on a PVC, one replica |
| CMP frontend | `frontend/`: Next.js 16 (read `frontend/AGENTS.md` first), NextAuth with Keycloak |
| Clusters | K3s on OpenStack (on-prem), plus AWS and GCP clusters for multicloud projects |
| GitOps | Argo CD + Image Updater, app registry in `3-Istor/cnp-projects` |
| Releases | git tag `vX.Y.Z` on the app repo, Argo Rollouts canary when configured |
| Identity | Keycloak `auth.3istor.com`, realm `3istor` |
| Secrets | Vault + Vault Secrets Operator |
| Observability | VictoriaMetrics, Loki, Grafana per project |
| Docs | `3-Istor/cnp-docs` (https://3-istor.github.io/cnp-docs/) |

## Invariants

- **Git is the source of truth.** The CMP changes things by committing (app
  repo `deploy/*.yaml`, registry) or by calling Terraform modules from
  `app-templates`; never by editing the cluster by hand.
- **Authorization on every request.** Project roles come live from Keycloak
  groups (`project-<name>-admins|members|guests`), the owner from the
  database. Use `app/services/authz.py`; never trust the frontend.
- **Every change is journaled** (`app/services/audit.py`), with the AI
  assistant that made it when there is one.
- **AI assistants** use `/mcp` (`app/mcp_server.py`). The CMP is their OAuth
  server; their tokens carry the user's identity, so the REST API and RBAC
  apply unchanged. What they may do alone depends on the project's stake
  level (`app/services/agents/policy.yaml`).
- **The cluster is tight on memory**: one replica for platform components,
  no new pod without a reason.

## Working on this repository

- Backend: `poetry run pytest`, `black`, `isort`, `pylint --fail-under=9`
  (the CI runs the linters).
- Frontend: `npm run lint`, `npm run build`.
- Conventional commits, feature branches, pull requests to `main`. A release
  is a `vX.Y.Z` tag; CI builds the images and Image Updater deploys them.
