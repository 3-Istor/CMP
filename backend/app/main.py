import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import Base, engine
from app.routers import account, catalog, deployments, infra, projects
from app.services import health_poller
from app.services.template_repository import get_repository

# ══════════════════════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

def setup_logging():
    """Configure logging to output to both console and file."""
    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # Create formatters
    detailed_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    simple_formatter = logging.Formatter(
        fmt="%(levelname)-8s | %(name)s | %(message)s"
    )

    # Console handler (INFO and above)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)

    # File handler (DEBUG and above) - detailed logs
    file_handler = logging.FileHandler(logs_dir / "app.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)

    # Deployment-specific handler (all deployment-related logs)
    deployment_handler = logging.FileHandler(logs_dir / "deployments.log", mode="a", encoding="utf-8")
    deployment_handler.setLevel(logging.DEBUG)
    deployment_handler.setFormatter(detailed_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Configure deployment-specific logger
    deployment_logger = logging.getLogger("app.services.terraform_orchestrator")
    deployment_logger.addHandler(deployment_handler)

    # Configure terraform executor logger
    terraform_logger = logging.getLogger("app.services.terraform_executor")
    terraform_logger.addHandler(deployment_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("git").setLevel(logging.WARNING)

    # Log startup message
    root_logger.info("="* 80)
    root_logger.info("🚀 Starting Cloud Management Platform (CMP)")
    root_logger.info("="* 80)
    root_logger.info(f"Logs directory: {logs_dir.absolute()}")
    root_logger.info(f"Application log: {logs_dir / 'app.log'}")
    root_logger.info(f"Deployment log: {logs_dir / 'deployments.log'}")
    root_logger.info("="* 80)


# Initialize logging immediately
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting application lifecycle...")

    # Startup: Initialize template repository (this clones the repo)
    logger.info("Initializing template repository...")
    repo = get_repository()
    repo._ensure_repo()
    logger.info("Template repository ready")

    # Create all DB tables on startup (Alembic handles migrations in production)
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # Backfill github_repo_url for GitOps apps created before it was persisted
    from app.services.terraform_orchestrator import backfill_gitops_repo_urls
    backfill_gitops_repo_urls()

    # Mount static files after repository is cloned
    if os.path.exists("data/templates/templates"):
        logger.info("Mounting static files...")
        app.mount(
            "/static/templates",
            StaticFiles(directory="data/templates/templates"),
            name="templates",
        )
        logger.info("Static files mounted")

    # Start background health poller
    logger.info("Starting health poller...")
    health_poller_task = asyncio.create_task(health_poller.health_poller_loop())
    logger.info("Health poller started")

    logger.info("✅ Application startup complete")

    yield

    # Shutdown: cancel background tasks
    logger.info("Shutting down application...")
    health_poller_task.cancel()
    try:
        await health_poller_task
    except asyncio.CancelledError:
        pass
    logger.info("✅ Application shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="Hybrid Cloud Management Platform - Terraform-based deployments",
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
app.include_router(projects.router, prefix="/api")


@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    return {"status": "ok", "app": settings.APP_NAME}
