"""
Database restore, the GitOps way.

CNPG never restores in place: it recovers a backup into a new cluster. The
CMP therefore writes ``db.restore`` in the app's values file, and the shared
chart (infra-templates) creates the next generation of the cluster from the
chosen backup and switches the app to it. Argo CD then removes the previous
cluster; its backups and WAL stay in the bucket, so a restore is undone the
same way, from a backup of the previous generation.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

PLUGIN = "barman-cloud.cloudnative-pg.io"
HEALTHY = "Cluster in healthy state"


class RestoreTarget(BaseModel):
    server: str
    backup_id: str
    backup_stopped_at: datetime
    target_time: datetime | None = None


class RestoreError(ValueError):
    pass


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def current_generation(values: dict) -> int:
    raw = _mapping(_mapping(values.get("db")).get("restore")).get("generation")
    return raw if isinstance(raw, int) and raw > 0 else 0


def restorable(backup: dict) -> bool:
    spec = _mapping(backup.get("spec"))
    status = _mapping(backup.get("status"))
    return (
        spec.get("method") == "plugin"
        and status.get("phase") == "completed"
        and bool(status.get("backupId"))
        and bool(status.get("stoppedAt"))
    )


def target_from_backup(
    backup: dict, target_time: datetime | None, now: datetime
) -> RestoreTarget:
    """The restore a backup allows; target_time and now are naive UTC."""
    if not restorable(backup):
        raise RestoreError(
            "Cette sauvegarde n'est pas terminée ou pas restaurable."
        )
    status = backup["status"]
    stopped_at = (
        datetime.fromisoformat(status["stoppedAt"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    if target_time is not None and not stopped_at <= target_time <= now:
        raise RestoreError(
            "L'instant choisi doit être entre la fin de la sauvegarde et maintenant."
        )
    return RestoreTarget(
        server=backup["spec"]["cluster"]["name"],
        backup_id=status["backupId"],
        backup_stopped_at=stopped_at,
        target_time=target_time,
    )


def restore_patch(values: dict, target: RestoreTarget) -> dict:
    return {
        "db": {
            "restore": {
                "generation": current_generation(values) + 1,
                "sourceServer": target.server,
                "backupID": target.backup_id,
                "targetTime": (
                    target.target_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if target.target_time
                    else ""
                ),
            }
        }
    }


def commit_label(target: RestoreTarget) -> str:
    label = f"restore database {target.server} from backup {target.backup_id}"
    if target.target_time:
        label += f" up to {target.target_time.strftime('%Y-%m-%d %H:%M')} UTC"
    return label


class DatabaseRead(BaseModel):
    name: str
    phase: str
    healthy: bool
    instances: int
    ready_instances: int
    backups_enabled: bool
    restored_from: str | None
    restored_backup: str | None
    restored_to: str | None


def database_read(cluster: dict) -> DatabaseRead:
    spec = _mapping(cluster.get("spec"))
    status = _mapping(cluster.get("status"))
    recovery = _mapping(_mapping(spec.get("bootstrap")).get("recovery"))
    origin = next(
        (
            _mapping(_mapping(c.get("plugin")).get("parameters"))
            for c in spec.get("externalClusters") or []
            if c.get("name") == recovery.get("source")
        ),
        {},
    )
    target = _mapping(recovery.get("recoveryTarget"))
    phase = status.get("phase") or "Setting up primary"
    return DatabaseRead(
        name=cluster["metadata"]["name"],
        phase=phase,
        healthy=phase == HEALTHY,
        instances=spec.get("instances") or 1,
        ready_instances=status.get("readyInstances") or 0,
        backups_enabled=any(
            p.get("name") == PLUGIN for p in spec.get("plugins") or []
        ),
        restored_from=origin.get("serverName") if recovery else None,
        restored_backup=target.get("backupID") if recovery else None,
        restored_to=(target.get("targetTime") or None) if recovery else None,
    )
