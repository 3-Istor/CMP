"""
Keeps the CMP's observability objects in step with projects and apps, like
metrics_isolation does for vmauth: computed from the database, Vault and the
cluster, written only when they changed, deleted when their app or project
is gone. Runs on every activity collector tick and right after a project,
an app, an SLO target or a webhook changes.

A source that cannot be read stops the deletions it would justify: a Vault
outage leaves a project's routing as it is, an unreadable route list leaves
every SLO as it is.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.agent import ProjectAgentSettings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.observability import SloSetting
from app.models.project import Project, ProjectStatus, TargetCloud
from app.services.activity_sources import PROJECT_NAME
from app.services.agents.policy import DEFAULT_LEVEL
from app.services.environments import layout
from app.services.kube_client import (
    KubeUnavailableError,
    in_cluster,
    kube_create,
    kube_delete,
    kube_get,
    kube_replace,
)
from app.services.observability import routing, slo
from app.services.security import alerts as targets_store
from app.services.vault_client import VaultError

logger = logging.getLogger(__name__)

OWNER_SELECTOR = "cnp.3istor.com/owner=cmp"
PROJECT_LABEL = "cnp.3istor.com/project"
# Compared instead of the live spec, which the API server may default.
CHECKSUM = "cnp.3istor.com/checksum"
HTTPROUTES = "/apis/gateway.networking.k8s.io/v1/httproutes"
OPERATOR_API = "/apis/operator.victoriametrics.com/v1beta1/namespaces/{ns}"
GONE = (DeploymentStatus.DELETED, DeploymentStatus.DELETING)


@dataclass(frozen=True)
class Kind:
    collection: str
    # Top-level fields that make two objects the same.
    compared: tuple[str, ...]

    def path(self) -> str:
        return self.collection.format(ns=settings.OBS_RULES_NAMESPACE)


RULES = Kind(OPERATOR_API + "/vmrules", ("spec",))
CONFIGS = Kind(OPERATOR_API + "/vmalertmanagerconfigs", ("spec",))
SECRETS = Kind("/api/v1/namespaces/{ns}/secrets", ("type", "data"))


@dataclass(frozen=True)
class AppRow:
    project: str
    app: str
    app_id: int
    level: str
    onprem: bool


@dataclass
class Inventory:
    projects: list[str]
    apps: list[AppRow]
    targets: dict[tuple[str, str], tuple[float, float]]


@dataclass
class Desired:
    objects: dict[Kind, dict[str, dict]] = field(default_factory=dict)
    # Projects whose objects must stay as they are this round.
    frozen: set[str] = field(default_factory=set)


def inventory() -> Inventory:
    with SessionLocal() as db:
        projects = [
            p
            for (p,) in db.query(Project.project_name).filter(
                Project.status != ProjectStatus.DECOMMISSIONING
            )
            if PROJECT_NAME.match(p)
        ]
        rows = (
            db.query(
                Deployment.id,
                Deployment.name,
                Deployment.project_id,
                Project.target_cloud,
                ProjectAgentSettings.level,
            )
            .join(Project, Project.project_name == Deployment.project_id)
            .outerjoin(
                ProjectAgentSettings,
                ProjectAgentSettings.project_name == Deployment.project_id,
            )
            .filter(
                Deployment.provider_type == ProviderType.KUBERNETES,
                Deployment.status.notin_(GONE),
                Project.status != ProjectStatus.DECOMMISSIONING,
            )
            .all()
        )
        targets = {
            (s.project, s.app): (s.availability, s.latency)
            for s in db.query(SloSetting)
        }
    apps = [
        AppRow(
            project=project,
            app=name,
            app_id=app_id,
            level=level or DEFAULT_LEVEL,
            onprem=cloud == TargetCloud.ONPREM,
        )
        for app_id, name, project, cloud, level in rows
        if PROJECT_NAME.match(project or "") and PROJECT_NAME.match(name)
    ]
    return Inventory(projects=projects, apps=apps, targets=targets)


def routed_namespaces() -> set[str]:
    """
    Raises:
        KubeUnavailableError: Includes an unknown API: an empty answer would
            delete every SLO.
    """
    body = kube_get(HTTPROUTES)
    if body is None:
        raise KubeUnavailableError(f"GET {HTTPROUTES}: HTTP 404")
    return {item["metadata"]["namespace"] for item in body.get("items", [])}


def app_slo(row: AppRow, targets: tuple[float, float] | None) -> slo.AppSlo:
    availability, latency = targets or (
        slo.DEFAULT_AVAILABILITY,
        slo.DEFAULT_LATENCY,
    )
    return slo.AppSlo(
        project=row.project,
        app=row.app,
        app_id=row.app_id,
        namespace=layout.namespace(row.project, row.app, "prod"),
        period_days=slo.period_days(row.level),
        availability=availability,
        latency=latency,
    )


async def desired_state(found: Inventory, routed: set[str]) -> Desired:
    desired = Desired(objects={RULES: {}, CONFIGS: {}, SECRETS: {}})
    for row in found.apps:
        target = app_slo(row, found.targets.get((row.project, row.app)))
        if row.onprem and target.namespace in routed:
            rule = slo.render(target)
            desired.objects[RULES][rule["metadata"]["name"]] = rule
    apps_of: dict[str, list[str]] = {}
    for row in found.apps:
        apps_of.setdefault(row.project, []).append(row.app)
    for project in found.projects:
        try:
            targets = await targets_store.read_targets(project)
        except VaultError as exc:
            logger.warning("Alert routing of %s kept: %s", project, exc)
            desired.frozen.add(project)
            continue
        plan = routing.ProjectRouting(
            project=project,
            apps=apps_of.get(project, []),
            targets=targets,
            platform=targets_store.platform_default(),
        )
        for kind, obj in (
            (CONFIGS, routing.render_config(plan)),
            (SECRETS, routing.render_secret(plan)),
        ):
            if obj:
                desired.objects[kind][obj["metadata"]["name"]] = obj
    return desired


def _with_checksum(obj: dict, kind: Kind) -> dict:
    content = json.dumps(
        {f: obj.get(f) for f in kind.compared}, sort_keys=True
    )
    metadata = obj["metadata"]
    return {
        **obj,
        "metadata": {
            **metadata,
            "annotations": {
                **(metadata.get("annotations") or {}),
                CHECKSUM: hashlib.sha256(content.encode()).hexdigest(),
            },
        },
    }


def _same(current: dict, wanted: dict) -> bool:
    metadata = current.get("metadata") or {}
    labels = metadata.get("labels") or {}
    annotations = metadata.get("annotations") or {}
    return (
        all(
            labels.get(k) == v for k, v in wanted["metadata"]["labels"].items()
        )
        and annotations.get(CHECKSUM)
        == wanted["metadata"]["annotations"][CHECKSUM]
    )


def apply(kind: Kind, wanted: dict[str, dict], frozen: set[str]) -> int:
    """Create, replace or delete the owned objects of one kind; count them."""
    body = kube_get(kind.path(), {"labelSelector": OWNER_SELECTOR})
    if body is None:
        raise KubeUnavailableError(f"GET {kind.path()}: HTTP 404")
    current = {o["metadata"]["name"]: o for o in body.get("items", [])}
    changes = 0
    for name, plain in wanted.items():
        obj = _with_checksum(plain, kind)
        found = current.get(name)
        if found is None:
            kube_create(kind.path(), obj)
        elif not _same(found, obj):
            replacement = {
                **obj,
                "metadata": {
                    **obj["metadata"],
                    "resourceVersion": found["metadata"]["resourceVersion"],
                },
            }
            kube_replace(f"{kind.path()}/{name}", replacement)
        else:
            continue
        changes += 1
    for name, obj in current.items():
        project = (obj["metadata"].get("labels") or {}).get(PROJECT_LABEL)
        if name in wanted or project in frozen:
            continue
        kube_delete(f"{kind.path()}/{name}")
        changes += 1
    return changes


async def sync() -> None:
    """
    Raises:
        KubeUnavailableError: The cluster could not be read or written.
    """
    found = await run_in_threadpool(inventory)
    routed = await run_in_threadpool(routed_namespaces)
    desired = await desired_state(found, routed)
    for kind, wanted in desired.objects.items():
        changes = await run_in_threadpool(apply, kind, wanted, desired.frozen)
        if changes:
            logger.info(
                "Observability: %d %s change(s)",
                changes,
                kind.collection.rsplit("/", 1)[-1],
            )


class _Runs:
    loop: asyncio.AbstractEventLoop | None = None
    running = False
    again = False


_runs = _Runs()


def bind(loop: asyncio.AbstractEventLoop) -> None:
    """The loop request_sync schedules on, set by the collector."""
    _runs.loop = loop


async def sync_quietly() -> None:
    """sync(), one at a time, rerun once if asked meanwhile; never raises."""
    if not settings.OBS_V3_ENABLED or not in_cluster():
        return
    if _runs.running:
        _runs.again = True
        return
    _runs.running = True
    try:
        while True:
            _runs.again = False
            try:
                await sync()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Observability objects not synced: %s", exc)
            if not _runs.again:
                break
    finally:
        _runs.running = False


def request_sync() -> None:
    """Ask for a sync soon; safe from any thread, a no-op when disabled."""
    loop = _runs.loop
    if not settings.OBS_V3_ENABLED or loop is None or loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(sync_quietly(), loop)
