"""What a forced move can still restore: the database servers of a project
and the time of the last WAL each archived in the on-prem bucket."""

import asyncio
import logging

import boto3
from botocore.config import Config
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus
from app.services import vault_client
from app.services.cloud_backups import db_base_name
from app.services.environments import layout
from app.services.security import restore as restores
from app.services.security import restore_commit

logger = logging.getLogger(__name__)

APP_TEMPLATE = "k3s-gitops-app"


def server_name(release: str, generation: int) -> str:
    """The CNPG cluster (and barman server) of a generation, as the chart names it."""
    if not generation:
        return db_base_name(release, "prod")
    suffix = f"-db-r{generation}"
    return release[: 50 - len(suffix)].removesuffix("-") + suffix


async def backed_up(db: Session, project: str) -> list[tuple[str, dict]]:
    """(app, values) of the project's apps with a backed-up database."""
    apps = sorted(
        {
            name
            for (name,) in db.query(Deployment.name).filter(
                Deployment.project_id == project,
                Deployment.template_id == APP_TEMPLATE,
                Deployment.status != DeploymentStatus.DELETED,
            )
        }
    )
    found = []
    for app in apps:
        row = (
            db.query(Deployment)
            .filter(
                Deployment.project_id == project,
                Deployment.name == app,
                Deployment.template_id == APP_TEMPLATE,
            )
            .order_by(Deployment.id.desc())
            .first()
        )
        try:
            found.append((app, await restore_commit.read_values(row)))
        except HTTPException as exc:
            # 409: no database, or its backups are off.
            if exc.status_code != 409:
                raise
    return found


def _newest(client, bucket: str, server: str):
    newest = None
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{server}/wals/"
    )
    for page in pages:
        for obj in page.get("Contents") or []:
            if newest is None or obj["LastModified"] > newest:
                newest = obj["LastModified"]
    return newest


async def _last_wal(project: str, app: str, values: dict):
    release = layout.application(project, app, "backend", "prod")
    server = server_name(release, restores.current_generation(values))
    key = await vault_client.read_kv(f"project-{project}", f"{app}/backup")
    if not key.get("AWS_ACCESS_KEY_ID"):
        raise RuntimeError("no backup key in Vault")
    backup = (values.get("db") or {}).get("backup") or {}
    client = boto3.client(
        "s3",
        endpoint_url=backup.get("endpointURL") or settings.BACKUP_S3_ENDPOINT,
        aws_access_key_id=key["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=key["AWS_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 2},
        ),
    )
    bucket = f"cnpg-{db_base_name(release, 'prod')}"
    newest = await asyncio.to_thread(_newest, client, bucket, server)
    return newest


async def last_archived(db: Session, project: str) -> list[dict]:
    """Per backed-up database: {database, last_wal_at (ISO time or None), reason}."""
    out = []
    for app, values in await backed_up(db, project):
        entry = {"database": app, "last_wal_at": None, "reason": None}
        try:
            # None: nothing archived yet.
            newest = await _last_wal(project, app, values)
            entry["last_wal_at"] = newest.isoformat() if newest else None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # One unreadable bucket must not hide the others' times.
            logger.warning(
                "last WAL of %s/%s unreadable: %s", project, app, exc
            )
            entry["reason"] = str(exc) or type(exc).__name__
        out.append(entry)
    return out
