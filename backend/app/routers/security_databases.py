"""
Database backups and restores of an app, read live from the cluster.

A restore is a commit: CNPG only recovers into a new cluster, so the CMP
writes ``db.restore`` in the app's values file and the shared chart creates
the next generation of the database from the chosen backup.
"""

import difflib
import json
import logging
from datetime import datetime, timezone
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.routers.deployments import _deep_merge, _yaml
from app.routers.security import (
    CurrentUser,
    _app_deployment,
    _github_token,
    _require_admin,
    _require_member,
    _username,
)
from app.services import app_security_data as security_data
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    get_default_branch,
    get_file_content,
    put_file_content,
)
from app.services.kube_client import (
    KubeUnavailableError,
    kube_create,
    kube_list,
)
from app.services.security import restore as restores
from app.services.security.collector import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["Security"])


class BackupRequest(BaseModel):
    project: str
    app: str


@router.post("/backups", status_code=202)
async def request_backup(
    payload: BackupRequest, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    """On-demand backup of every database of the app that has backups enabled."""
    await _require_admin(token, payload.project)
    _app_deployment(db, payload.project, payload.app)
    namespace = f"{payload.project}-{payload.app}"
    try:
        clusters = await run_in_threadpool(
            kube_list,
            f"/apis/postgresql.cnpg.io/v1/namespaces/{namespace}/clusters",
        )
        enabled = [
            c["metadata"]["name"]
            for c in clusters
            if any(
                p.get("name") == "barman-cloud.cloudnative-pg.io"
                for p in (c.get("spec") or {}).get("plugins") or []
            )
        ]
        if not enabled:
            raise HTTPException(
                status_code=409,
                detail="Aucune base de cette app n'a la sauvegarde activée.",
            )
        stamp = utcnow().strftime("%Y%m%d%H%M%S")
        for name in enabled:
            await run_in_threadpool(
                kube_create,
                f"/apis/postgresql.cnpg.io/v1/namespaces/{namespace}/backups",
                {
                    "apiVersion": "postgresql.cnpg.io/v1",
                    "kind": "Backup",
                    "metadata": {
                        "name": f"{name}-manual-{stamp}",
                        "labels": {"cnp.3istor.com/requested-by": "cmp"},
                    },
                    "spec": {
                        "cluster": {"name": name},
                        "method": "plugin",
                        "pluginConfiguration": {
                            "name": "barman-cloud.cloudnative-pg.io"
                        },
                    },
                },
            )
    except KubeUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"backups": [f"{n}-manual-{stamp}" for n in enabled]}


class BackupRead(BaseModel):
    name: str
    database: str
    phase: str
    method: str
    started_at: datetime | None
    stopped_at: datetime | None
    error: str | None
    manual: bool
    backup_id: str | None
    restorable: bool


def _k8s_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
        tzinfo=None
    )


@router.get("/backups", response_model=list[BackupRead])
async def list_backups(
    token: CurrentUser,
    project: str,
    app: str,
    db: Session = Depends(get_db),
) -> list[BackupRead]:
    """The app's database backups, newest first, read live from the cluster."""
    await _require_member(token, project, "developer")
    _app_deployment(db, project, app)
    try:
        backups = await run_in_threadpool(
            kube_list,
            f"/apis/postgresql.cnpg.io/v1/namespaces/{project}-{app}/backups",
        )
    except KubeUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = [
        BackupRead(
            name=b["metadata"]["name"],
            database=((b.get("spec") or {}).get("cluster") or {}).get(
                "name", ""
            ),
            phase=(b.get("status") or {}).get("phase", "pending"),
            method=(b.get("spec") or {}).get("method", ""),
            started_at=_k8s_time((b.get("status") or {}).get("startedAt")),
            stopped_at=_k8s_time((b.get("status") or {}).get("stoppedAt")),
            error=(b.get("status") or {}).get("error"),
            manual=(b["metadata"].get("labels") or {}).get(
                "cnp.3istor.com/requested-by"
            )
            == "cmp",
            backup_id=(b.get("status") or {}).get("backupId"),
            restorable=restores.restorable(b),
        )
        for b in backups
    ]
    return sorted(
        rows,
        key=lambda r: r.started_at or datetime.min,
        reverse=True,
    )


@router.get("/databases", response_model=list[restores.DatabaseRead])
async def list_databases(
    token: CurrentUser,
    project: str,
    app: str,
    db: Session = Depends(get_db),
) -> list[restores.DatabaseRead]:
    """The app's database clusters, live, with what each was restored from."""
    await _require_member(token, project, "developer")
    _app_deployment(db, project, app)
    try:
        clusters = await run_in_threadpool(
            kube_list,
            f"/apis/postgresql.cnpg.io/v1/namespaces/{project}-{app}/clusters",
        )
    except KubeUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return sorted(
        (restores.database_read(c) for c in clusters), key=lambda d: d.name
    )


class RestoreRequest(BaseModel):
    project: str
    app: str
    backup: str
    target_time: datetime | None = None


@router.post("/restores")
async def request_restore(
    payload: RestoreRequest,
    token: CurrentUser,
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """
    Restore the app's database from one of its backups, optionally up to a
    later instant. Commits ``db.restore`` to the app's values file; Argo CD
    does the rest. With ``dry_run``, return the diff for the confirmation.
    """
    await _require_admin(token, payload.project)
    deployment = _app_deployment(db, payload.project, payload.app)
    namespace = f"{payload.project}-{payload.app}"
    target_time = payload.target_time
    if target_time is not None and target_time.tzinfo is not None:
        target_time = target_time.astimezone(timezone.utc).replace(tzinfo=None)

    try:
        cnpg = f"/apis/postgresql.cnpg.io/v1/namespaces/{namespace}"
        backups = await run_in_threadpool(kube_list, f"{cnpg}/backups")
        clusters = await run_in_threadpool(kube_list, f"{cnpg}/clusters")
    except KubeUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    backup = next(
        (b for b in backups if b["metadata"]["name"] == payload.backup), None
    )
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    busy = [c for c in clusters if not restores.database_read(c).healthy]
    if busy:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La base {busy[0]['metadata']['name']} n'est pas prête : "
                "attends la fin de l'opération en cours."
            ),
        )
    try:
        target = restores.target_from_backup(backup, target_time, utcnow())
    except restores.RestoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app_type = json.loads(deployment.app_config or "{}").get(
        "app_type", "static"
    )
    _, values_file = security_data.values_files_for(app_type)
    installation_token, repo = await _github_token(deployment)
    try:
        branch = await get_default_branch(installation_token, repo)
        raw, sha = await get_file_content(
            installation_token, repo, values_file, branch
        )
        values = _yaml.load(raw)
        if not isinstance(values, dict) or not security_data.has_database(
            values
        ):
            raise HTTPException(
                status_code=409, detail="This app has no database."
            )
        if not security_data.read_backup(values).enabled:
            raise HTTPException(
                status_code=409,
                detail="Les sauvegardes de cette base sont désactivées.",
            )
        _deep_merge(values, restores.restore_patch(values, target))
        out = StringIO()
        _yaml.dump(values, out)
        message = (
            f"chore(cmp): {restores.commit_label(target)} ({_username(token)})"
        )
        if dry_run:
            return {
                "message": message,
                "diff": "".join(
                    difflib.unified_diff(
                        raw.splitlines(keepends=True),
                        out.getvalue().splitlines(keepends=True),
                        fromfile=f"a/{values_file}",
                        tofile=f"b/{values_file}",
                    )
                ),
            }
        result = await put_file_content(
            installation_token,
            repo,
            values_file,
            out.getvalue(),
            message,
            sha,
            branch,
        )
    except FileNotInRepoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "Security: %s restored %s/%s from %s",
        _username(token),
        payload.project,
        payload.app,
        payload.backup,
    )
    return {
        "message": message,
        "commit": (result.get("commit") or {}).get("sha", ""),
    }
