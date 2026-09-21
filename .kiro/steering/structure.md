---
inclusion: always
---

# Project Structure

This workspace is organized as a monorepo containing the Control Plane.

## File Organization

- `backend/app/`: The FastAPI application code.
  - `models/`: SQLAlchemy models (e.g., `deployment.py`).
  - `routers/`: FastAPI routes.
  - `services/`: Core business logic (Saga orchestrator, AWS/OpenStack connectors, and now GitHub/K8s).
- `backend/app/terraform/`: Houses Terraform local modules (e.g., `github_bootstrap`).
- `.kiro/steering/docs/`: The cloned `cnp-docs` repository containing deep architectural designs.

## Naming & Import Conventions

- Use relative imports within the `app` package (e.g., `from app.core.database import Base`).
- Follow PEP8 styling (Black formatter, Pylint).
