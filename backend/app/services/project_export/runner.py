"""
One export end to end: collect, archive, store, record. And the retention:
an archive is deleted once its retrieval period is over.
"""

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import Project, TargetCloud
from app.models.project_export import ProjectExport
from app.services.github_service import get_scoped_installation_token
from app.services.project_export import collectors
from app.services.project_export.bundle import Bundle
from app.services.project_export.collectors import Sources
from app.services.project_export.inventory import EXCLUDED_TABLES, NOT_EXPORTED
from app.services.project_export.sources import (
    ExportStore,
    GitRepoArchiver,
    KeycloakIdentityReader,
    KubeClusterReader,
    LokiLogReader,
    PgDumper,
    S3BucketReader,
    S3ExportStore,
    VaultSecretReader,
    cnpg_resolver,
    export_store_configured,
    obc_buckets,
)

logger = logging.getLogger(__name__)

ACTIVE = ("pending", "running")
DOWNLOADABLE = ("ready", "partial")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def object_key(row: ProjectExport) -> str:
    return f"exports/{row.project}/{row.id}.tar.gz"


def platform_sources(cloud: TargetCloud) -> Sources:
    return Sources(
        cluster=KubeClusterReader(cloud),
        repos=GitRepoArchiver(),
        databases=PgDumper(cnpg_resolver(cloud)),
        buckets=S3BucketReader(obc_buckets(cloud)),
        secrets=VaultSecretReader(),
        identity=KeycloakIdentityReader(),
        logs=LokiLogReader(),
    )


async def _github_token(repositories: list[str]) -> str | None:
    """Read-only token on the org installation, for these repositories only."""
    if not settings.GITHUB_INSTALLATION_ID:
        return None
    return await get_scoped_installation_token(
        settings.GITHUB_INSTALLATION_ID,
        repositories,
        {"contents": "read", "metadata": "read"},
    )


def not_exported() -> list[dict]:
    return NOT_EXPORTED + [
        {"data": f"CMP table {table}", "owner": owner, "reason": reason}
        for table, (owner, reason) in sorted(EXCLUDED_TABLES.items())
    ]


async def build(
    row: ProjectExport,
    session_factory: Callable[[], Session],
    sources: Sources,
    root: Path,
    github_token: Callable = _github_token,
) -> Bundle:
    """Every collector into *root*; a failing one becomes a problem."""
    bundle = Bundle(root)
    project = row.project

    async def step(source: str, work) -> object:
        try:
            return await work
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Export %s: %s failed: %s", row.id, source, exc)
            bundle.problem(source, str(exc) or type(exc).__name__)
            return None

    async def cmp() -> list[dict]:
        def run() -> list[dict]:
            with session_factory() as session:
                return collectors.collect_cmp(session, project, bundle)

        return await asyncio.to_thread(run)

    async def repos(apps: list[dict]) -> None:
        await collectors.collect_repos(
            apps, sources.repos, github_token, bundle
        )

    async def images(objects: list[dict]) -> None:
        collectors.collect_images(objects, bundle)

    apps = await step("cmp-db", cmp()) or []
    namespaces, objects = await step(
        "kubernetes",
        collectors.collect_cluster(sources.cluster, project, bundle),
    ) or ([], [])
    end = utcnow()
    await step("images", images(objects))
    await step("git", repos(apps))
    await step(
        "databases",
        collectors.collect_databases(objects, sources.databases, bundle),
    )
    await step(
        "buckets",
        collectors.collect_buckets(project, objects, sources.buckets, bundle),
    )
    await step(
        "vault",
        collectors.collect_secrets(
            project, row.include_secret_values, sources.secrets, bundle
        ),
    )
    await step(
        "keycloak",
        collectors.collect_identity(project, sources.identity, bundle),
    )
    await step(
        "loki",
        collectors.collect_logs(
            project,
            namespaces,
            sources.logs,
            bundle,
            end - timedelta(days=settings.EXPORT_LOG_DAYS),
            end,
        ),
    )
    bundle.write_manifest(
        {
            "export_id": row.id,
            "project": project,
            "created_at": row.created_at.isoformat() + "Z",
            "include_secret_values": row.include_secret_values,
        },
        not_exported(),
    )
    return bundle


def _cloud(session: Session, project: str) -> TargetCloud:
    record = session.query(Project).filter_by(project_name=project).first()
    return record.target_cloud if record else TargetCloud.ONPREM


async def run_export(
    export_id: str,
    sources: Sources | None = None,
    store: ExportStore | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    github_token: Callable = _github_token,
) -> None:
    """Build, upload and record one export. Never raises."""
    with session_factory() as session:
        row = session.get(ProjectExport, export_id)
        if row is None or row.status != "pending":
            return
        row.status = "running"
        session.commit()
        session.refresh(row)
        session.expunge(row)
        cloud = _cloud(session, row.project)
    try:
        store = store or S3ExportStore.from_settings()
        sources = sources or platform_sources(cloud)
        with tempfile.TemporaryDirectory(prefix="cnp-export-") as work:
            bundle = await build(
                row,
                session_factory,
                sources,
                Path(work) / "data",
                github_token,
            )
            archive = Path(work) / "export.tar.gz"
            digest, size = await asyncio.to_thread(bundle.archive, archive)
            key = object_key(row)
            await asyncio.to_thread(store.put, key, archive)
        problems = bundle.problems
        status = "partial" if problems else "ready"
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Export %s failed: %s", export_id, exc)
        problems, status = [{"source": "export", "error": str(exc)}], "failed"
        key, digest, size = None, None, None
    now = utcnow()
    with session_factory() as session:
        row = session.get(ProjectExport, export_id)
        row.status = status
        row.finished_at = now
        row.problems = json.dumps(problems)
        if status != "failed":
            row.object_key, row.sha256, row.size = key, digest, size
            row.expires_at = now + timedelta(
                days=settings.EXPORT_RETENTION_DAYS
            )
        session.commit()
    logger.info("Export %s: %s", export_id, status)


def fail_interrupted(
    session_factory: Callable[[], Session] = SessionLocal
) -> int:
    """At startup: an export the last process was running will never end."""
    with session_factory() as session:
        rows = (
            session.query(ProjectExport)
            .filter(ProjectExport.status.in_(ACTIVE))
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.finished_at = utcnow()
            row.problems = json.dumps(
                [{"source": "export", "error": "interrupted by a restart"}]
            )
        session.commit()
        return len(rows)


def purge_expired(
    store: ExportStore,
    session_factory: Callable[[], Session] = SessionLocal,
    now: datetime | None = None,
) -> int:
    """Delete the archives whose retrieval period is over."""
    now = now or utcnow()
    purged = 0
    with session_factory() as session:
        rows = (
            session.query(ProjectExport)
            .filter(
                ProjectExport.status.in_(DOWNLOADABLE),
                ProjectExport.expires_at <= now,
            )
            .all()
        )
        for row in rows:
            try:
                store.delete(row.object_key)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Could not delete export %s: %s", row.id, exc)
                continue
            row.status = "expired"
            row.object_key = None
            purged += 1
        session.commit()
    return purged


async def export_retention_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        if not export_store_configured():
            continue
        try:
            purged = await asyncio.to_thread(
                purge_expired, S3ExportStore.from_settings()
            )
            if purged:
                logger.info("Deleted %d expired project exports", purged)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Export retention pass failed: %s", exc)
