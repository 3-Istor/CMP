"""
A project moving from one public cloud to the other, in idempotent steps.

The database goes by a final CNPG backup on the source, then a restore at
generation N+1 on the target from the same bucket. The CMP writes to the
clusters only to stop the source apps, take that backup and delete the source
namespaces once the target is healthy; the rest are registry commits.
"""

import inspect
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.models.project_move import ACTIVE, STEP_NAMES, ProjectMove
from app.services import project_registry
from app.services.audit import record_system_event
from app.services.clusters.state import PUBLIC_CLOUDS, fresh_state
from app.services.environments.layout import SUFFIX
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_create,
    kube_delete,
    kube_get,
    kube_list,
    kube_merge_patch,
)
from app.services.notifications import notify_project
from app.services.security import restore as restores
from app.services.security import restore_commit

ARGOCD = "/apis/argoproj.io/v1alpha1/namespaces/argocd"
NAMESPACES = "/api/v1/namespaces"
CNPG = "/apis/postgresql.cnpg.io/v1/namespaces/{}"
LABEL = "cnp.3istor.com/project"
WORKLOADS = (
    "/apis/apps/v1/namespaces/{}/deployments",
    "/apis/argoproj.io/v1alpha1/namespaces/{}/rollouts",
)
IMPORTED = ("import-service", "import-backing")
APP_TEMPLATE = "k3s-gitops-app"
PUBLIC = {c.value for c in PUBLIC_CLOUDS}


class MoveRefused(Exception):
    """A move cannot start or change now; the detail says why, for people."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class StepFailed(Exception):
    """A step cannot go on: the move waits for a retry or a cancel."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _named(steps: list[dict], name: str) -> dict:
    return next(s for s in steps if s["name"] == name)


def active_move(db: Session, project: str) -> ProjectMove | None:
    return (
        db.query(ProjectMove)
        .filter(ProjectMove.project == project)
        .filter(ProjectMove.status.in_(ACTIVE))
        .first()
    )


def start_checks(
    db: Session, project: Project, confirm_data_loss: bool
) -> str:
    """The cloud *project* would move to; MoveRefused if it cannot move now."""
    source = TargetCloud(project.target_cloud)
    if source not in PUBLIC_CLOUDS:
        raise MoveRefused(
            409, "Moves to or from on-prem are not supported yet"
        )
    if active_move(db, project.project_name):
        raise MoveRefused(409, "This project is already moving.")
    target = next(c for c in PUBLIC_CLOUDS if c != source)
    for cloud in (source, target):
        state = fresh_state(cloud)
        if state != "up":
            raise MoveRefused(
                409, f"The {cloud.value.upper()} cluster is {state}."
            )
    if not confirm_data_loss:
        # Imported apps keep their data outside CNPG: nothing copies it.
        imported = [
            name
            for (name,) in db.query(Deployment.name)
            .filter(
                Deployment.project_id == project.project_name,
                Deployment.template_id.in_(IMPORTED),
                Deployment.status != DeploymentStatus.DELETED,
            )
            .distinct()
            .order_by(Deployment.name)
        ]
        if imported:
            raise MoveRefused(
                409, f"Data in {', '.join(imported)} will be lost."
            )
    return target.value


def new_move(
    db: Session, project: Project, target: str, actor: str
) -> ProjectMove:
    move = ProjectMove(
        project=project.project_name,
        source=TargetCloud(project.target_cloud).value,
        target=target,
        status="running",
        steps=[
            {
                "name": name,
                "status": "pending",
                "detail": None,
                "started_at": None,
                "finished_at": None,
            }
            for name in STEP_NAMES
        ],
        restores=[],
        actor=actor,
        created_at=_now(),
    )
    db.add(move)
    db.commit()
    db.refresh(move)
    return move


# ── Reads ───────────────────────────────────────────────────────────────────


def _namespaces(project: str, cloud: TargetCloud) -> list[dict]:
    """The project's namespaces on *cloud*; the label is checked here too."""
    return [
        ns
        for ns in kube_list(
            NAMESPACES, {"labelSelector": f"{LABEL}={project}"}, cloud=cloud
        )
        if (ns["metadata"].get("labels") or {}).get(LABEL) == project
    ]


def _app_namespaces(project: str, cloud: TargetCloud) -> list[str]:
    return [
        ns["metadata"]["name"]
        for ns in _namespaces(project, cloud)
        if ns["metadata"]["name"] != f"{project}-system"
    ]


def _databases(project: str, cloud: TargetCloud) -> list[tuple[str, str]]:
    """(namespace, cluster) of every database with backups enabled."""
    return [
        (ns, cluster["metadata"]["name"])
        for ns in _app_namespaces(project, cloud)
        for cluster in kube_list(f"{CNPG.format(ns)}/clusters", cloud=cloud)
        if restores.database_read(cluster).backups_enabled
    ]


def _app_env(project: str, namespace: str) -> tuple[str, str]:
    app = namespace.removeprefix(f"{project}-")
    if app.endswith(SUFFIX["staging"]):
        return app.removesuffix(SUFFIX["staging"]), "staging"
    return app, "prod"


def _deployment(db: Session, project: str, app: str) -> Deployment:
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
    if row is None:
        raise StepFailed(f"App {app} not found")
    return row


# ── Steps: True when done, False while waiting (with a detail) ──────────────


def _appprojects_waiting(move: ProjectMove) -> list[str]:
    waiting = []
    for name in (f"{move.project}-platform", move.project):
        found = kube_get(f"{ARGOCD}/appprojects/{name}") or {}
        destinations = (found.get("spec") or {}).get("destinations") or []
        # The tenant AppProject of a project without apps has no destination.
        if name == move.project and not destinations:
            continue
        if not any(d.get("name") == move.target for d in destinations):
            waiting.append(name)
    return waiting


async def prepare(_db, move: ProjectMove, step: dict, _steps) -> bool:
    if step["status"] == "pending":
        await project_registry.set_move(
            move.project,
            {
                "id": move.id,
                "source": move.source,
                "target": move.target,
                "step": "prepare",
            },
        )
    waiting = await run_in_threadpool(_appprojects_waiting, move)
    step["detail"] = (
        f"Waiting for {', '.join(waiting)} to allow {move.target}"
        if waiting
        else None
    )
    return not waiting


def stop_source(_db, move: ProjectMove, step: dict, _steps) -> bool:
    cloud = TargetCloud(move.source)
    running = []
    for ns in _app_namespaces(move.project, cloud):
        for collection in WORKLOADS:
            path = collection.format(ns)
            for item in kube_list(path, cloud=cloud):
                name = item["metadata"]["name"]
                # The apps ApplicationSet ignores replicas: Argo CD keeps the 0.
                if (item.get("spec") or {}).get("replicas") != 0:
                    kube_merge_patch(
                        f"{path}/{name}",
                        {"spec": {"replicas": 0}},
                        cloud=cloud,
                    )
                if (item.get("status") or {}).get("readyReplicas"):
                    running.append(name)
    step["detail"] = (
        f"Waiting for {', '.join(running)} to stop" if running else None
    )
    return not running


def final_backup(_db, move: ProjectMove, step: dict, _steps) -> bool:
    cloud = TargetCloud(move.source)
    waiting = []
    for ns, cluster in _databases(move.project, cloud):
        name = f"{cluster}-move-{move.id}"
        path = f"{CNPG.format(ns)}/backups"
        backup = kube_get(f"{path}/{name}", cloud=cloud)
        if backup is None:
            kube_create(path, restores.backup_body(name, cluster), cloud=cloud)
            backup = {}
        status = backup.get("status") or {}
        if status.get("phase") == "failed":
            raise StepFailed(
                f"Backup {name} failed: {status.get('error') or 'no reason'}"
            )
        if status.get("phase") != "completed":
            waiting.append(name)
    step["detail"] = f"Waiting for {', '.join(waiting)}" if waiting else None
    return not waiting


async def _restores(db: Session, move: ProjectMove) -> list[dict]:
    """Per backed-up database: its next generation, from the source cluster."""
    clusters: dict[str, list[str]] = {}
    found = await run_in_threadpool(
        _databases, move.project, TargetCloud(move.source)
    )
    for ns, cluster in found:
        clusters.setdefault(ns, []).append(cluster)
    out = []
    for ns, names in clusters.items():
        app, env = _app_env(move.project, ns)
        values = await restore_commit.read_values(
            _deployment(db, move.project, app)
        )
        server = restores.current_cluster(values, names)
        if server is None:
            raise StepFailed(f"Cannot tell which database of {app} is current")
        out.append(
            {
                "app": app,
                "env": env,
                "generation": restores.current_generation(values) + 1,
                "sourceServer": server,
            }
        )
    return out


async def switch(db: Session, move: ProjectMove, _step, _steps) -> bool:
    if not move.restores:
        # Saved before the commit: a retry reuses them, one generation bump.
        move.restores = await _restores(db, move)
        db.commit()
    await project_registry.switch_cloud(
        move.project,
        {
            "id": move.id,
            "source": move.source,
            "target": move.target,
            "step": "switch",
            "restores": move.restores,
        },
    )
    project = (
        db.query(Project)
        .filter(Project.project_name == move.project)
        .one_or_none()
    )
    if project is not None:
        project.target_cloud = TargetCloud(move.target)
        db.commit()
    return True


def _destination(app: dict) -> str | None:
    return ((app.get("spec") or {}).get("destination") or {}).get("name")


def _ready(app: dict) -> bool:
    status = app.get("status") or {}
    return (status.get("sync") or {}).get("status") == "Synced" and (
        status.get("health") or {}
    ).get("status") == "Healthy"


def wait_target(_db, move: ProjectMove, step: dict, _steps) -> bool:
    # The project's on-prem Applications (AppProjects, image updaters) stay.
    apps = [
        app
        for app in kube_list(
            f"{ARGOCD}/applications",
            {"labelSelector": f"{LABEL}={move.project}"},
        )
        if _destination(app) in PUBLIC
    ]
    if not any(_destination(app) == move.target for app in apps):
        step["detail"] = f"Waiting for the apps to run on {move.target}"
        return False
    waiting = [
        app["metadata"]["name"]
        for app in apps
        if _destination(app) != move.target or not _ready(app)
    ]
    step["detail"] = f"Waiting for {', '.join(waiting)}" if waiting else None
    return not waiting


async def persist_restores(
    db: Session, move: ProjectMove, _step, _steps
) -> bool:
    for restore in move.restores:
        # Exact values, so the app repository says what runs on the target.
        patch = {
            "db": {
                "restore": {
                    "generation": restore["generation"],
                    "sourceServer": restore["sourceServer"],
                    "backupID": "",
                    "targetTime": "",
                }
            }
        }
        await restore_commit.commit_values_patch(
            _deployment(db, move.project, restore["app"]),
            f"move to {move.target}",
            lambda _values, patch=patch: patch,
            move.actor,
        )
    return True


def cleanup_source(_db, move: ProjectMove, step: dict, steps) -> bool:
    if _named(steps, "wait_target")["status"] != "done":
        raise StepFailed("The target is not healthy yet")
    cloud = TargetCloud(move.source)
    left = _namespaces(move.project, cloud)
    for ns in left:
        # A terminating namespace answers 409 to a second DELETE.
        if not ns["metadata"].get("deletionTimestamp"):
            kube_delete(f"{NAMESPACES}/{ns['metadata']['name']}", cloud=cloud)
    names = [ns["metadata"]["name"] for ns in left]
    step["detail"] = f"Deleting {', '.join(names)}" if names else None
    return not names


async def finish(_db, move: ProjectMove, _step, steps) -> bool:
    await project_registry.set_move(move.project, None)
    cutover = datetime.fromisoformat(
        _named(steps, "wait_target")["finished_at"]
    ) - datetime.fromisoformat(_named(steps, "stop_source")["started_at"])
    record_system_event(
        "project.move",
        move.project,
        {
            "source": move.source,
            "target": move.target,
            "cutover_seconds": cutover.total_seconds(),
        },
        project=move.project,
    )
    await run_in_threadpool(
        notify_project,
        move.project,
        kind="move",
        title_key="moveDone",
        params={"target": move.target},
    )
    return True


STEPS = {
    "prepare": prepare,
    "stop_source": stop_source,
    "final_backup": final_backup,
    "switch": switch,
    "wait_target": wait_target,
    "persist_restores": persist_restores,
    "cleanup_source": cleanup_source,
    "finish": finish,
}


# ── Driving ─────────────────────────────────────────────────────────────────


def _save(db: Session, move: ProjectMove, steps: list[dict]) -> None:
    # A new list, so SQLAlchemy sees the JSON column change.
    move.steps = [dict(step) for step in steps]
    db.commit()


async def advance(db: Session, move: ProjectMove) -> ProjectMove:
    """
    Take a running move as far as it goes now. Safe to call often: each step
    is idempotent, and a step that waits leaves the rest pending.
    """
    if move.status != "running":
        return move
    steps = [dict(step) for step in move.steps]
    for step in steps:
        if step["status"] == "done":
            continue
        step["started_at"] = step["started_at"] or _now().isoformat()
        run = STEPS[step["name"]]
        try:
            if inspect.iscoroutinefunction(run):
                done = await run(db, move, step, steps)
            else:
                done = await run_in_threadpool(run, db, move, step, steps)
        except (ClusterNotConnectedError, KubeUnavailableError) as exc:
            # A cluster out of reach is a wait, not a failure.
            step["detail"] = str(exc)
            done = False
        except Exception as exc:  # pylint: disable=broad-exception-caught
            detail = str(getattr(exc, "detail", None) or exc)
            step.update(
                status="failed", detail=detail, finished_at=_now().isoformat()
            )
            move.status = "failed"
            move.detail = f"{step['name']}: {detail}"
            _save(db, move, steps)
            return move
        if not done:
            step["status"] = "running"
            _save(db, move, steps)
            return move
        step.update(status="done", finished_at=_now().isoformat())
        _save(db, move, steps)
    move.status = "done"
    move.finished_at = _now()
    _save(db, move, steps)
    return move


async def cancel(db: Session, move: ProjectMove) -> ProjectMove:
    """Drop a move that has not touched the source yet."""
    if move.status not in ACTIVE:
        raise MoveRefused(409, "This move is over")
    # Once stop_source starts, apps may sit at 0 replicas that nothing restores.
    if _named(move.steps, "stop_source")["status"] != "pending":
        raise MoveRefused(409, "The source is already stopped: retry instead")
    await project_registry.set_move(move.project, None)
    move.status = "cancelled"
    move.finished_at = _now()
    db.commit()
    return move


def retry(db: Session, move: ProjectMove) -> ProjectMove:
    """Run the failed step again, from the start of that step."""
    if move.status != "failed":
        raise MoveRefused(409, "Only a failed move can be retried")
    steps = [dict(step) for step in move.steps]
    for step in steps:
        if step["status"] == "failed":
            step.update(status="pending", detail=None, finished_at=None)
    move.status = "running"
    move.detail = None
    _save(db, move, steps)
    return move
