"""
A project moving from one public cloud to the other, in idempotent steps.

The database goes by a final CNPG backup on the source, then a restore at
generation N+1 on the target from the same bucket. The CMP writes to the
clusters only to stop the source apps, take that backup and delete the source
namespaces once the target is healthy; the rest are registry commits.
"""

import inspect
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.models.project_move import ACTIVE, STEP_NAMES, ProjectMove
from app.services import project_registry, vault_client
from app.services.audit import record_system_event
from app.services.clusters.state import PUBLIC_CLOUDS, fresh_state
from app.services.deletion_protection import still_labelled
from app.services.environments import layout
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_create,
    kube_delete,
    kube_get,
    kube_list,
    kube_merge_patch,
)
from app.services.moves import active_move
from app.services.notifications import notify_project, notify_users
from app.services.security import restore as restores
from app.services.security import restore_commit

logger = logging.getLogger(__name__)

ARGOCD = "/apis/argoproj.io/v1alpha1/namespaces/argocd"
NAMESPACES = "/api/v1/namespaces"
CNPG = "/apis/postgresql.cnpg.io/v1/namespaces/{}"
VAULT_SECRETS = "/apis/ricoberger.de/v1alpha1/namespaces/{}/vaultsecrets"
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


async def start_checks(
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
    # The admission policy refuses to delete protected source namespaces.
    try:
        protected = await project_registry.is_deletion_protected(
            project.project_name
        ) or await run_in_threadpool(
            still_labelled, project.project_name, source
        )
    except project_registry.RegistryError as exc:
        raise MoveRefused(
            502, f"Could not check deletion protection: {exc}"
        ) from exc
    if protected:
        raise MoveRefused(
            409, "Turn deletion protection off to move this project."
        )
    if not confirm_data_loss:
        lost = await data_loss(db, project)
        if lost:
            raise MoveRefused(409, f"Data in {', '.join(lost)} will be lost.")
    return target.value


async def data_loss(db: Session, project: Project) -> list[str]:
    """Apps whose data starts empty on the target: nothing copies it."""
    # Imported apps keep their data outside CNPG.
    imported = [
        name
        for (name,) in db.query(Deployment.name)
        .filter(
            Deployment.project_id == project.project_name,
            Deployment.template_id.in_(IMPORTED),
            Deployment.status != DeploymentStatus.DELETED,
        )
        .distinct()
    ]
    try:
        unrestored = await run_in_threadpool(
            _unrestored,
            project.project_name,
            TargetCloud(project.target_cloud),
        )
    except (ClusterNotConnectedError, KubeUnavailableError) as exc:
        raise MoveRefused(503, f"Could not read the databases: {exc}") from exc
    return sorted({*imported, *unrestored})


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
        restores=None,
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


def _clusters(project: str, cloud: TargetCloud) -> list[tuple[str, dict]]:
    """(namespace, CNPG Cluster) of every database of the project's apps."""
    return [
        (ns, cluster)
        for ns in _app_namespaces(project, cloud)
        for cluster in kube_list(f"{CNPG.format(ns)}/clusters", cloud=cloud)
    ]


def _databases(project: str, cloud: TargetCloud) -> list[tuple[str, str]]:
    """(namespace, cluster) of every database with backups enabled."""
    return [
        (ns, cluster["metadata"]["name"])
        for ns, cluster in _clusters(project, cloud)
        if restores.database_read(cluster).backups_enabled
    ]


def _app_env(project: str, namespace: str) -> tuple[str, str]:
    app = namespace.removeprefix(f"{project}-")
    if app.endswith(layout.SUFFIX["staging"]):
        return app.removesuffix(layout.SUFFIX["staging"]), "staging"
    return app, "prod"


def _unrestored(project: str, cloud: TargetCloud) -> list[str]:
    """Apps with a database no restore covers: no backups, or staging."""
    lost = set()
    for ns, cluster in _clusters(project, cloud):
        app, env = _app_env(project, ns)
        if env != "prod":
            lost.add(f"{app} ({env})")
        elif not restores.database_read(cluster).backups_enabled:
            lost.add(app)
    return sorted(lost)


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


def _project_apps(project: str) -> list[dict]:
    """The project's Applications on a public cloud (on-prem ones stay)."""
    return [
        app
        for app in kube_list(
            f"{ARGOCD}/applications", {"labelSelector": f"{LABEL}={project}"}
        )
        if _destination(app) in PUBLIC
    ]


def _destination(app: dict) -> str | None:
    return ((app.get("spec") or {}).get("destination") or {}).get("name")


def _ready_on(app: dict, cloud: str) -> bool:
    status = app.get("status") or {}
    sync = status.get("sync") or {}
    # Until Argo CD compares against the new cloud, its status is the source's.
    compared = (sync.get("comparedTo") or {}).get("destination") or {}
    return (
        _destination(app) == cloud
        and compared.get("name") == cloud
        and sync.get("status") == "Synced"
        and (status.get("health") or {}).get("status") == "Healthy"
    )


# ── Checks before the source stops ──────────────────────────────────────────


def _source_problems(move: ProjectMove) -> list[str]:
    """What would break the move once the source is stopped, read live."""
    problems = []
    unhealthy = [
        app["metadata"]["name"]
        for app in _project_apps(move.project)
        if _destination(app) == move.source and not _ready_on(app, move.source)
    ]
    if unhealthy:
        problems.append(
            f"Not synced and healthy on {move.source}: {', '.join(unhealthy)}"
        )
    # Without archiving, the final backup and the restore would miss WAL.
    stuck = [
        cluster["metadata"]["name"]
        for _, cluster in _clusters(move.project, TargetCloud(move.source))
        if restores.database_read(cluster).backups_enabled
        and not any(
            c.get("type") == "ContinuousArchiving"
            and c.get("status") == "True"
            for c in (cluster.get("status") or {}).get("conditions") or []
        )
    ]
    if stuck:
        problems.append(f"Not archiving its WAL: {', '.join(stuck)}")
    return problems


def _vault_roles(move: ProjectMove) -> list[str]:
    """The Vault roles the project's secrets are synced with on the source."""
    cloud = TargetCloud(move.source)
    return sorted(
        {
            role
            for ns in _namespaces(move.project, cloud)
            for secret in kube_list(
                VAULT_SECRETS.format(ns["metadata"]["name"]), cloud=cloud
            )
            if (role := (secret.get("spec") or {}).get("vaultRole"))
        }
    )


async def _precheck(move: ProjectMove) -> None:
    problems = await run_in_threadpool(_source_problems, move)
    mount = f"kubernetes-{move.target}"
    missing = [
        role
        for role in await run_in_threadpool(_vault_roles, move)
        if not await vault_client.app_role_exists(mount, role)
    ]
    if missing:
        problems.append(
            f"Missing on Vault {mount}: {', '.join(missing)} "
            "(apply the project's Terraform or redeploy its imports)"
        )
    if problems:
        raise StepFailed("; ".join(problems))


async def _restores(db: Session, move: ProjectMove) -> list[dict]:
    """Per backed-up prod database: its next generation, from the source."""
    clusters: dict[str, list[str]] = {}
    found = await run_in_threadpool(
        _databases, move.project, TargetCloud(move.source)
    )
    for ns, cluster in found:
        clusters.setdefault(ns, []).append(cluster)
    out = []
    for ns, names in clusters.items():
        app, env = _app_env(move.project, ns)
        # Restores land in the values file prod and staging share.
        if env != "prod":
            continue
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


async def _ensure_restores(db: Session, move: ProjectMove) -> None:
    if move.restores is None:
        # Saved before any commit: a retry reuses them, one generation bump.
        move.restores = await _restores(db, move)
        db.commit()


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


async def prepare(db: Session, move: ProjectMove, step: dict, _steps) -> bool:
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
    if waiting:
        step["detail"] = (
            f"Waiting for {', '.join(waiting)} to allow {move.target}"
        )
        return False
    # Last step a cancel can undo: refuse here what would strand the move.
    await _precheck(move)
    await _ensure_restores(db, move)
    step["detail"] = None
    return True


def _replicated(pod: dict) -> bool:
    """A pod of a Deployment or Rollout (CNPG and Job pods are not)."""
    # Evicted pods stay owned by their ReplicaSet until PodGC.
    if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
        return False
    owners = pod["metadata"].get("ownerReferences") or []
    return any(o.get("kind") == "ReplicaSet" for o in owners)


def stop_source(_db, move: ProjectMove, step: dict, _steps) -> bool:
    cloud = TargetCloud(move.source)
    left = []
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
        # Stopped once the pods are gone: a terminating one may still write.
        left += [
            pod["metadata"]["name"]
            for pod in kube_list(f"{NAMESPACES}/{ns}/pods", cloud=cloud)
            if _replicated(pod)
        ]
    step["detail"] = f"Waiting for {', '.join(left)} to stop" if left else None
    return not left


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
            waiting.append(f"{name} ({status.get('phase') or 'pending'})")
    step["detail"] = f"Waiting for {', '.join(waiting)}" if waiting else None
    return not waiting


async def switch(db: Session, move: ProjectMove, _step, _steps) -> bool:
    await _ensure_restores(db, move)
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


def _target_waiting(move: ProjectMove) -> str | None:
    """What the target still waits for, read live; None once it is healthy."""
    apps = _project_apps(move.project)
    if not any(_destination(app) == move.target for app in apps):
        return f"Waiting for the apps to run on {move.target}"
    waiting = [
        app["metadata"]["name"]
        for app in apps
        if not _ready_on(app, move.target)
    ]
    return f"Waiting for {', '.join(waiting)}" if waiting else None


def wait_target(_db, move: ProjectMove, step: dict, _steps) -> bool:
    step["detail"] = _target_waiting(move)
    return step["detail"] is None


async def _deployed(
    db: Session, move: ProjectMove, app: str, sha: str
) -> bool:
    """Whether Argo CD renders the backend of *app* from *sha* or later."""
    name = layout.application(move.project, app, "backend", "prod")
    found = await run_in_threadpool(kube_get, f"{ARGOCD}/applications/{name}")
    sync = ((found or {}).get("status") or {}).get("sync") or {}
    revisions = sync.get("revisions") or [sync.get("revision")]
    if sha in revisions:
        return True
    sources = ((found or {}).get("spec") or {}).get("sources") or []
    values = next(
        (
            rev
            for source, rev in zip(sources, revisions)
            if source.get("ref") == "values"
        ),
        None,
    )
    # Another commit may have landed on top of ours since.
    return bool(values) and await restore_commit.includes(
        _deployment(db, move.project, app), values, sha
    )


async def persist_restores(
    db: Session, move: ProjectMove, step: dict, _steps
) -> bool:
    # Kept on the step, so a retry or a wait never commits twice.
    commits = step.setdefault("commits", {})
    for restore in move.restores or []:
        if restore["app"] in commits:
            continue
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
        commits[restore["app"]] = await restore_commit.commit_values_patch(
            _deployment(db, move.project, restore["app"]),
            f"move to {move.target}",
            lambda _values, patch=patch: patch,
            move.actor,
        )
    # Until Argo CD renders the commit, dropping status.move would render the
    # old generation and prune the restored database.
    waiting = [
        layout.application(move.project, app, "backend", "prod")
        for app, sha in commits.items()
        if not await _deployed(db, move, app, sha)
    ]
    step["detail"] = (
        f"Waiting for {', '.join(waiting)} to deploy the restore"
        if waiting
        else None
    )
    return not waiting


def cleanup_source(_db, move: ProjectMove, step: dict, steps) -> bool:
    if _named(steps, "wait_target")["status"] != "done":
        raise StepFailed("The target is not healthy yet")
    # Checked again live before each round of deletions.
    waiting = _target_waiting(move)
    if waiting:
        step["detail"] = waiting
        return False
    cloud = TargetCloud(move.source)
    left = _namespaces(move.project, cloud)
    for ns in left:
        # A terminating namespace answers 409 to a second DELETE.
        if not ns["metadata"].get("deletionTimestamp"):
            kube_delete(f"{NAMESPACES}/{ns['metadata']['name']}", cloud=cloud)
    names = [ns["metadata"]["name"] for ns in left]
    step["detail"] = f"Deleting {', '.join(names)}" if names else None
    return not names


def _params(move: ProjectMove) -> dict:
    return {"project": move.project, "cloud": move.target.upper()}


async def finish(_db, move: ProjectMove, _step, steps) -> bool:
    await project_registry.set_move(move.project, None)
    cutover = datetime.fromisoformat(
        _named(steps, "wait_target")["finished_at"]
    ) - datetime.fromisoformat(_named(steps, "stop_source")["started_at"])
    try:
        record_system_event(
            "project.move.done",
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
            title_key="move_done",
            params=_params(move),
            link=f"/projects/{move.project}/settings",
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # The project has moved: a lost event or notice must not undo that.
        logger.exception("move %s: event or notification lost", move.id)
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


def _refused(exc: Exception) -> bool:
    """A cluster that answered no: waiting would not change its mind."""
    status = getattr(exc, "status", None) or 0
    # 408 and 429 are an API server shedding load: a wait, like a 5xx.
    return 400 <= status < 500 and status not in (408, 429)


async def _fail(
    db: Session, move: ProjectMove, steps: list[dict], step: dict, detail: str
) -> ProjectMove:
    step.update(status="failed", detail=detail, finished_at=_now().isoformat())
    move.status = "failed"
    move.detail = f"{step['name']}: {detail}"
    _save(db, move, steps)
    owner = (
        db.query(Project.owner_username)
        .filter(Project.project_name == move.project)
        .scalar()
    )
    await run_in_threadpool(
        notify_users,
        [owner, move.actor],
        kind="move",
        title_key="move_failed",
        params=_params(move),
        link=f"/projects/{move.project}/settings",
        project=move.project,
    )
    return move


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
            if _refused(exc):
                return await _fail(db, move, steps, step, str(exc))
            # A cluster out of reach is a wait, not a failure.
            step["detail"] = str(exc)
            done = False
        except Exception as exc:  # pylint: disable=broad-exception-caught
            detail = str(getattr(exc, "detail", None) or exc)
            return await _fail(db, move, steps, step, detail)
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
