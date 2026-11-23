"""
A project moving between clusters (on-prem or a public cloud), in idempotent steps.

The database goes by a final CNPG backup on the source, then a restore at
generation N+1 on the target from the same bucket. The CMP writes to the
clusters to prepare the target (VaultSecret copies), stop the source apps,
take that backup and delete the source namespaces once the target is healthy
(or the target ones, on cancel); the rest are registry commits.
"""

# pylint: disable=too-many-lines

import inspect
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.models.project_move import ACTIVE, STEP_NAMES, ProjectMove
from app.services import project_registry, vault_client
from app.services.audit import record_system_event
from app.services.clusters.state import (
    PUBLIC_CLOUDS,
    fresh_state,
    pick_public_cloud,
)
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
from app.services.moves import active_move, capacity, prepull, wal
from app.services.notifications import notify_project, notify_users
from app.services.orphans import VAULT_AUTH_MOUNTS
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
CLUSTERS = {c.value for c in TargetCloud}
# Longest a move waits on a source step before skipping it: the final backups
# of moves 6 to 10 took 15 to 30 s, so 2 min is four times the worst.
SOURCE_WAIT = timedelta(minutes=2)
# Set on a namespace right before the move deletes it, once the project lives
# elsewhere: the deletion-protection policy lets that one delete through.
MOVE_CLEANUP = "cnp.3istor.com/move-cleanup"


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
    db: Session,
    project: Project,
    confirm_data_loss: bool,
    target: str = "public",
) -> str:
    """The cluster *project* would move to; MoveRefused if it cannot move now.

    Only the target has to be up (the restore needs it): the source steps are
    best effort, whatever the source state.
    """
    source = TargetCloud(project.target_cloud)
    if active_move(db, project.project_name):
        raise MoveRefused(409, "This project is already moving.")
    if target == "onprem":
        if source == TargetCloud.ONPREM:
            raise MoveRefused(409, "This project is already on-prem.")
        destination = TargetCloud.ONPREM
    elif source == TargetCloud.ONPREM:
        try:
            destination = pick_public_cloud(
                {c: fresh_state(c) for c in PUBLIC_CLOUDS}, None
            )
        except HTTPException as exc:
            raise MoveRefused(exc.status_code, exc.detail) from exc
    else:
        destination = next(c for c in PUBLIC_CLOUDS if c != source)
    # On-prem is up whenever the CMP answers.
    state = fresh_state(destination) if destination in PUBLIC_CLOUDS else "up"
    if state != "up":
        raise MoveRefused(
            409, f"The {destination.value.upper()} cluster is {state}."
        )
    if source == TargetCloud.ONPREM:
        await _check_leaving_onprem(project.project_name, destination)
    full = await run_in_threadpool(
        _no_room, project.project_name, source, destination
    )
    if full:
        raise MoveRefused(409, full)
    if not confirm_data_loss:
        lost = await data_loss(db, project)
        if lost:
            raise MoveRefused(409, f"Data in {', '.join(lost)} will be lost.")
    return destination.value


def _needed(project: str, source: TargetCloud) -> tuple[float, float]:
    """What the project requests: read on the source, else its quotas."""
    try:
        return capacity.project_requests(
            _app_namespaces(project, source), source
        )
    except (ClusterNotConnectedError, KubeUnavailableError):
        namespaces = {
            (app.get("spec") or {}).get("destination", {}).get("namespace")
            for app in _project_apps(project)
        } - {None, f"{project}-system"}
        return capacity.quota_requests(len(namespaces))


def _no_room(
    project: str, source: TargetCloud, target: TargetCloud
) -> str | None:
    return capacity.refusal(_needed(project, source), target)


async def _check_leaving_onprem(project: str, destination: TargetCloud):
    # An OBC in an app namespace would be deleted with it, bucket included.
    for ns in await run_in_threadpool(
        _app_namespaces, project, TargetCloud.ONPREM
    ):
        claims = await run_in_threadpool(
            kube_list,
            f"/apis/objectbucket.io/v1alpha1/namespaces/{ns}/objectbucketclaims",
        )
        if claims:
            raise MoveRefused(
                409,
                f"Move the backups of {_app_env(project, ns)[0]} "
                f"to {project}-backups first.",
            )
    if destination in PUBLIC_CLOUDS:
        try:
            found = await project_registry.read_record(project)
        except project_registry.RegistryError as exc:
            raise MoveRefused(
                502, f"Could not read the project record: {exc}"
            ) from exc
        features = ((found or ({},))[0].get("spec") or {}).get(
            "features"
        ) or {}
        if features.get("keycloak"):
            raise MoveRefused(
                409, "Projects with their own Keycloak stay on-prem."
            )


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
    except (ClusterNotConnectedError, KubeUnavailableError):
        # A lost source: its databases come back from the archive.
        unrestored = []
    return sorted({*imported, *unrestored})


def new_move(
    db: Session,
    project: Project,
    target: str,
    actor: str,
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
    """The project's namespaces on *cloud*, the label checked here too.

    <p>-backups (on-prem, holds the database buckets) is never listed: no
    caller may stop or delete it.
    """
    return [
        ns
        for ns in kube_list(
            NAMESPACES, {"labelSelector": f"{LABEL}={project}"}, cloud=cloud
        )
        if (ns["metadata"].get("labels") or {}).get(LABEL) == project
        and ns["metadata"]["name"] != f"{project}-backups"
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
    """The project's Applications that move: not <p>-appproject (pinned
    on-prem) nor the in-cluster ones (image updaters)."""
    return [
        app
        for app in kube_list(
            f"{ARGOCD}/applications", {"labelSelector": f"{LABEL}={project}"}
        )
        if app["metadata"]["name"] != f"{project}-appproject"
        and _destination(app) in CLUSTERS
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


def _source_reachable(move: ProjectMove) -> bool:
    """Whether the source API answers; the source steps are best effort."""
    try:
        _namespaces(move.project, TargetCloud(move.source))
    except (ClusterNotConnectedError, KubeUnavailableError):
        return False
    return True


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


def _roles_best_effort(move: ProjectMove) -> list[str]:
    try:
        return _vault_roles(move)
    except (ClusterNotConnectedError, KubeUnavailableError):
        return []  # source lost: nothing to check against


async def _precheck(move: ProjectMove) -> None:
    problems = []
    mount = VAULT_AUTH_MOUNTS[TargetCloud(move.target)]
    roles = await run_in_threadpool(_roles_best_effort, move)
    missing = [
        role
        for role in roles
        if not await vault_client.app_role_exists(mount, role)
    ]
    if missing:
        problems.append(
            f"Missing on Vault {mount}: {', '.join(missing)} "
            "(apply the project's Terraform or redeploy its imports)"
        )
    if problems:
        raise StepFailed("; ".join(problems))


async def _restores_from_values(db: Session, move: ProjectMove) -> list[dict]:
    """The same, for a lost source: the app repository names the servers."""
    out = []
    for app, values in await wal.backed_up(db, move.project):
        generation = restores.current_generation(values)
        release = layout.application(move.project, app, "backend", "prod")
        out.append(
            {
                "app": app,
                "env": "prod",
                "generation": generation + 1,
                "sourceServer": wal.server_name(release, generation),
            }
        )
    return out


async def _restores(db: Session, move: ProjectMove) -> list[dict]:
    """Per backed-up prod database: its next generation, from the source."""
    if not await run_in_threadpool(_source_reachable, move):
        return await _restores_from_values(db, move)
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


def _bootstrap(project: str) -> dict:
    return kube_get(f"{ARGOCD}/applications/{project}-bootstrap") or {}


def _copy_secrets(move: ProjectMove) -> list[str]:
    """Copy the source VaultSecrets to the target; what is still missing there.

    The bootstrap creates the target namespaces, so a missing one is a wait. The
    copy carries no Argo CD tracking: the app Application adopts it after the
    switch.
    """
    source, target = TargetCloud(move.source), TargetCloud(move.target)
    there = {
        ns["metadata"]["name"] for ns in _namespaces(move.project, target)
    }
    waiting = []
    for ns in _app_namespaces(move.project, source):
        path = VAULT_SECRETS.format(ns)
        found = kube_list(path, cloud=source)
        if found and ns not in there:
            waiting.append(f"namespace {ns}")
            continue
        for secret in found:
            meta = secret["metadata"]
            name = meta["name"]
            if kube_get(f"{path}/{name}", cloud=target) is None:
                labels = {
                    k: v
                    for k, v in (meta.get("labels") or {}).items()
                    # Set by Argo CD when it tracks by label.
                    if k != "app.kubernetes.io/instance"
                }
                body = {
                    "apiVersion": "ricoberger.de/v1alpha1",
                    "kind": "VaultSecret",
                    "metadata": {"name": name, "namespace": ns},
                    "spec": secret.get("spec") or {},
                }
                if labels:
                    body["metadata"]["labels"] = labels
                kube_create(path, body, cloud=target)
            # The operator names the Secret after its VaultSecret.
            if (
                kube_get(f"{NAMESPACES}/{ns}/secrets/{name}", cloud=target)
                is None
            ):
                waiting.append(f"Secret {ns}/{name}")
    return waiting


def _target_prepared(move: ProjectMove, copy: bool = True) -> list[str]:
    """What the target still lacks before the switch; empty once ready."""
    waiting = _copy_secrets(move) if copy else []
    if not _ready_on(_bootstrap(move.project), move.target):
        waiting.append(f"{move.project}-bootstrap on {move.target}")
    return waiting


async def prepare(db: Session, move: ProjectMove, step: dict, _steps) -> bool:
    if step["status"] == "pending":
        # Again here: the cluster may have filled since the click.
        full = await run_in_threadpool(
            _no_room,
            move.project,
            TargetCloud(move.source),
            TargetCloud(move.target),
        )
        if full:
            raise StepFailed(full)
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
    # The VaultSecret copy and the pre-pull read the source: a lost one is left out.
    lost = not await run_in_threadpool(_source_reachable, move)
    waiting = await run_in_threadpool(_target_prepared, move, not lost)
    if waiting:
        step["detail"] = f"Waiting for {', '.join(waiting)}"
        return False
    if lost:
        step["detail"] = "source unreachable: no secret copy, no pre-pull"
        return True
    waiting = await run_in_threadpool(prepull.pending, move)
    if waiting:
        step["detail"] = "; ".join(waiting)
        return False
    await run_in_threadpool(prepull.cleanup, move)
    step["detail"] = None
    return True


def _replicated(pod: dict) -> bool:
    """A pod of a Deployment or Rollout (CNPG and Job pods are not)."""
    # Evicted pods stay owned by their ReplicaSet until PodGC.
    if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
        return False
    owners = pod["metadata"].get("ownerReferences") or []
    return any(o.get("kind") == "ReplicaSet" for o in owners)


def _best_effort(run, move: ProjectMove, step: dict) -> bool:
    """*run* the source step; what stops it skips it, the move goes on."""
    try:
        if run(move, step):
            return True
        waited = _now() - datetime.fromisoformat(step["started_at"])
        if waited < SOURCE_WAIT:
            return False
        reason = (
            f"waited {int(SOURCE_WAIT.total_seconds())} s: {step['detail']}"
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        reason = str(exc)
    step.update(status="skipped", detail=reason)
    return True


def stop_source(_db, move: ProjectMove, step: dict, _steps) -> bool:
    return _best_effort(_stop_source, move, step)


def _stop_source(move: ProjectMove, step: dict) -> bool:
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
    return _best_effort(_final_backup, move, step)


def _final_backup(move: ProjectMove, step: dict) -> bool:
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


def _delete_source_namespace(name: str, cloud: TargetCloud):
    """Delete a namespace of a cluster the project does not live on (after
    the registry switch, or the target of a cancel). The annotation lets the delete through a protected one.
    """
    kube_merge_patch(
        f"{NAMESPACES}/{name}",
        {"metadata": {"annotations": {MOVE_CLEANUP: "true"}}},
        cloud=cloud,
    )
    kube_delete(f"{NAMESPACES}/{name}", cloud=cloud)


def cleanup_source(_db, move: ProjectMove, step: dict, steps) -> bool:
    if _named(steps, "wait_target")["status"] != "done":
        raise StepFailed("The target is not healthy yet")
    # Checked again live before each round of deletions.
    waiting = _target_waiting(move)
    if waiting:
        step["detail"] = waiting
        return False
    cloud = TargetCloud(move.source)
    try:
        left = _namespaces(move.project, cloud)
    except (ClusterNotConnectedError, KubeUnavailableError):
        # cleanup_returned finishes it when the cluster answers again.
        step.update(
            status="skipped",
            detail=f"On return of {move.source.upper()}",
            deferred=True,
        )
        return True
    for ns in left:
        # A terminating namespace answers 409 to a second DELETE.
        if not ns["metadata"].get("deletionTimestamp"):
            _delete_source_namespace(ns["metadata"]["name"], cloud)
    names = [ns["metadata"]["name"] for ns in left]
    step["detail"] = f"Deleting {', '.join(names)}" if names else None
    return not names


def cleanup_returned(cloud: TargetCloud) -> list[str]:
    """Finish the deferred source cleanups of the moves out of *cloud*.

    Raises while the cluster does not answer; the caller retries.
    """
    did: list[str] = []
    with SessionLocal() as db:
        found = (
            db.query(ProjectMove)
            .filter(
                ProjectMove.status == "done",
                ProjectMove.source == cloud.value,
            )
            .all()
        )
        for move in found:
            step = _named(move.steps, "cleanup_source")
            if step["status"] != "skipped" or not step.get("deferred"):
                continue
            project = move.project
            row = db.query(Project).filter_by(project_name=project).first()
            current = active_move(db, project)
            # The project came back to this cloud: its namespaces are live.
            if (row and row.target_cloud == cloud) or (
                current and current.target == cloud.value
            ):
                continue
            did += _clean_returned(db, move, step, cloud)
    return did


def _clean_returned(
    db: Session, move: ProjectMove, step: dict, cloud: TargetCloud
) -> list[str]:
    project = move.project
    did = []
    try:
        # First, so the users stop reaching the stale copy.
        kube_merge_patch(
            f"/apis/apps/v1/namespaces/{project}-system/deployments/"
            f"{project}-tunnel",
            {"spec": {"replicas": 0}},
            cloud=cloud,
        )
        did.append(f"{project}: tunnel stopped on {cloud.value}")
    except KubeUnavailableError as exc:
        if exc.status != 404:
            raise
    left = _namespaces(project, cloud)
    for ns in left:
        name = ns["metadata"]["name"]
        if not ns["metadata"].get("deletionTimestamp"):
            _delete_source_namespace(name, cloud)
            did.append(f"{project}: deleting {name} on {cloud.value}")
    if not left:
        # A new list: SQLAlchemy does not see a JSON dict changed in place.
        move.steps = [
            (
                {**s, "status": "done", "detail": None, "deferred": False}
                if s is step
                else s
            )
            for s in move.steps
        ]
        db.commit()
        did.append(f"{project}: cleanup done on {cloud.value}")
    return did


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


# The on-prem API server answered 403 for about 50 s each time it restarted
# (three times on 22/11): a 401/403 fails a step only once it outlasts this.
REFUSAL_GRACE = timedelta(minutes=2)


def _refusal_outlasted(step: dict, exc: Exception) -> bool:
    """Whether an auth refusal has lasted past REFUSAL_GRACE; starts its clock."""
    if getattr(exc, "status", None) not in (401, 403):
        return True
    since = step.setdefault("refused_since", _now().isoformat())
    return _now() - datetime.fromisoformat(since) >= REFUSAL_GRACE


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
    Take a running (or cancelling) move as far as it goes now. Safe to call often: each step
    is idempotent, and a step that waits leaves the rest pending.
    """
    if move.status == "cancelling":
        return await _cancelling(db, move)
    if move.status != "running":
        return move
    steps = [dict(step) for step in move.steps]
    for step in steps:
        if step["status"] in ("done", "skipped"):
            continue
        step["started_at"] = step["started_at"] or _now().isoformat()
        run = STEPS[step["name"]]
        try:
            if inspect.iscoroutinefunction(run):
                done = await run(db, move, step, steps)
            else:
                done = await run_in_threadpool(run, db, move, step, steps)
        except (ClusterNotConnectedError, KubeUnavailableError) as exc:
            if _refused(exc) and _refusal_outlasted(step, exc):
                return await _fail(db, move, steps, step, str(exc))
            # A cluster out of reach is a wait, not a failure.
            step["detail"] = str(exc)
            done = False
        except Exception as exc:  # pylint: disable=broad-exception-caught
            detail = str(getattr(exc, "detail", None) or exc)
            return await _fail(db, move, steps, step, detail)
        else:
            step.pop("refused_since", None)
        if not done:
            step["status"] = "running"
            _save(db, move, steps)
            return move
        # A best-effort step marks itself skipped before returning True.
        if step["status"] != "skipped":
            step["status"] = "done"
        step["finished_at"] = _now().isoformat()
        _save(db, move, steps)
    move.status = "done"
    move.finished_at = _now()
    _save(db, move, steps)
    return move


def _undo_target(move: ProjectMove) -> str | None:
    """Delete what prepare left on the target; None once nothing is left."""
    if _destination(_bootstrap(move.project)) != move.source:
        return f"Waiting for {move.project}-bootstrap to target {move.source}"
    cloud = TargetCloud(move.target)
    left = _namespaces(move.project, cloud)
    for ns in left:
        # A terminating namespace answers 409 to a second DELETE.
        if not ns["metadata"].get("deletionTimestamp"):
            # Prepare gave them the protection label; the project lives on the source.
            _delete_source_namespace(ns["metadata"]["name"], cloud)
    names = [ns["metadata"]["name"] for ns in left]
    return f"Deleting {', '.join(names)} on {move.target}" if names else None


async def _cancelling(db: Session, move: ProjectMove) -> ProjectMove:
    try:
        move.detail = await run_in_threadpool(_undo_target, move)
    except (ClusterNotConnectedError, KubeUnavailableError) as exc:
        move.detail = str(exc)
        logger.warning("move %s cancel waits: %s", move.id, exc)
    if move.detail is None:
        move.status = "cancelled"
        move.finished_at = _now()
    db.commit()
    return move


async def cancel(db: Session, move: ProjectMove) -> ProjectMove:
    """Drop a move that has not touched the source yet.

    The target is cleaned asynchronously: the loop waits until the bootstrap
    targets the source again, then deletes the target namespaces.
    """
    if move.status not in ACTIVE or move.status == "cancelling":
        raise MoveRefused(409, "This move is over")
    # Once stop_source starts, apps may sit at 0 replicas that nothing restores.
    if _named(move.steps, "stop_source")["status"] != "pending":
        raise MoveRefused(409, "The source is already stopped: retry instead")
    await project_registry.set_move(move.project, None)
    move.status = "cancelling"
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
