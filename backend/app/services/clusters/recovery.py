"""Resume cluster operations interrupted by a pod restart; reconcile state with reality."""

import asyncio
import logging
import tempfile
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cluster import Cluster, ClusterState
from app.services.clusters import state
from app.services.clusters.ops import (
    _DIRS,
    _env,
    _stream,
    cluster_exists,
    run_operation,
)
from app.services.state_lock import format_state_lock_message, parse_state_lock

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 60
VAULT_RENEW_INTERVAL = 24 * 3600
_last_reconcile: dict = {}
_last_renew: float | None = None
_tasks: set[asyncio.Task] = set()  # strong refs: the loop only keeps weak ones


def is_own_stale_lock(who: str) -> bool:
    # Called at startup with a single replica: any backend-pod lock, same hostname included
    # (container restart), belongs to a dead process.
    return who.rpartition("@")[2].startswith(settings.CLUSTER_OPS_POD_PREFIX)


def _append_log(row: Cluster, text: str) -> None:
    if row.log_path:
        with open(row.log_path, "a") as log:
            log.write(text if text.endswith("\n") else text + "\n")


async def renew_vault_token(force: bool = False) -> None:
    """Renew the cluster-ops token; at most once per 24 h unless forced. Never raises."""
    global _last_renew
    now = time.monotonic()
    if not settings.CLUSTER_OPS_VAULT_TOKEN or (
        not force
        and _last_renew is not None
        and now - _last_renew < VAULT_RENEW_INTERVAL
    ):
        return
    _last_renew = now
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.VAULT_URL}/v1/auth/token/renew-self",
                headers={"X-Vault-Token": settings.CLUSTER_OPS_VAULT_TOKEN},
            )
        if resp.status_code != 200:
            _last_renew = None  # retry on the next call
            logger.warning("Vault token renewal failed (%s)", resp.status_code)
    except Exception:  # noqa: BLE001
        _last_renew = None
        logger.warning("Vault token renewal failed", exc_info=True)


async def _release_stale_lock(row: Cluster) -> str | None:
    """Unlock our own dead pod's lock; return an error message if a foreign lock remains."""
    with tempfile.TemporaryDirectory(prefix="cluster-recover-") as tmp:
        return await _release_stale_lock_in(row, _env(row.cloud, tmp))


async def _release_stale_lock_in(row: Cluster, env: dict) -> str | None:
    cwd = Path(settings.CLUSTER_TF_ROOT) / _DIRS[row.cloud]
    out: list[str] = []
    # .terraform is ephemeral: plan and force-unlock need an initialised backend.
    rc = await _stream(
        ["terraform", "init", "-no-color", "-input=false"],
        cwd,
        env,
        None,
        out.append,
        out.append,
    )
    if rc:
        _append_log(row, "\n".join(out))
        return f"terraform init failed (exit {rc})"
    out.clear()
    rc = await _stream(
        [
            "terraform",
            "plan",
            "-no-color",
            "-lock-timeout=0s",
            "-input=false",
            "-refresh=false",
        ],
        cwd,
        env,
        None,
        out.append,
        out.append,
    )
    lock = parse_state_lock("\n".join(out))
    if lock is None:
        if (
            rc
        ):  # not a lock problem: the resumed operation will surface the real error
            _append_log(row, "\n".join(out))
        return None
    if not (lock.lock_id and is_own_stale_lock(lock.who)):
        return format_state_lock_message(lock)
    await _stream(
        ["terraform", "force-unlock", "-no-color", "-force", lock.lock_id],
        cwd,
        env,
        None,
        out.append,
        out.append,
    )
    _append_log(row, f"Released stale lock {lock.lock_id} from {lock.who}")
    return None


async def resume_interrupted() -> None:
    await renew_vault_token(force=True)
    with SessionLocal() as db:
        rows = (
            db.query(Cluster)
            .filter(
                Cluster.state.in_(
                    [ClusterState.STARTING, ClusterState.STOPPING]
                )
            )
            .all()
        )
        for row in rows:
            cloud = row.cloud
            if row.attempts >= 2:
                state.finish(db, cloud, ok=False, error="Interrupted twice")
                continue
            try:
                error = await _release_stale_lock(row)
            except Exception as exc:  # noqa: BLE001
                error = f"Unexpected error: {exc}"
            if error:
                state.finish(db, cloud, ok=False, error=error)
                continue
            row.attempts += 1
            db.commit()
            task = asyncio.create_task(run_operation(cloud, row.operation))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)


async def reconcile(db) -> None:
    """Align up|down rows with the real cluster; in-flight rows are left alone. Never raises."""
    try:
        await renew_vault_token()
        for cloud in state.PUBLIC_CLOUDS:
            row = db.get(Cluster, cloud)
            if row and row.state not in (ClusterState.UP, ClusterState.DOWN):
                continue
            now = time.monotonic()
            if (
                now - _last_reconcile.get(cloud, -RECONCILE_INTERVAL)
                < RECONCILE_INTERVAL
            ):
                continue
            _last_reconcile[cloud] = now
            exists = await cluster_exists(cloud)
            db.expire_all()  # an operation may have started during the lookup
            row = db.get(Cluster, cloud)
            if row and row.state not in (ClusterState.UP, ClusterState.DOWN):
                continue
            if exists is None and row:
                continue
            target = ClusterState.UP if exists else ClusterState.DOWN
            if row is None:
                db.add(Cluster(cloud=cloud, state=target))
            else:
                row.state = target
            db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Cluster reconciliation failed", exc_info=True)
        db.rollback()
