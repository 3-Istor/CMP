import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import Base, engine
from app.routers import account, catalog, deployments, infra
from app.services.template_repository import get_repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup: Initialize template repository (this clones the repo)
    repo = get_repository()
    repo._ensure_repo()

    # Create all DB tables on startup (Alembic handles migrations in production)
    Base.metadata.create_all(bind=engine)

    # Mount static files after repository is cloned
    if os.path.exists("data/templates/templates"):
        app.mount(
            "/static/templates",
            StaticFiles(directory="data/templates/templates"),
            name="templates",
        )

    yield

    # Shutdown: cleanup if needed


app = FastAPI(
    title=settings.APP_NAME,
    description="Hybrid Cloud Management Platform — Terraform-based deployments",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "https://cmp.3istor.com",
    ],
    allow_origin_regex="https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(deployments.router, prefix="/api")
app.include_router(infra.router, prefix="/api")


@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    return {"status": "ok", "app": settings.APP_NAME}
