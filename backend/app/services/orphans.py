"""
Resources an app or a project left behind (CNP-306), listed for platform
admins. Read only: nothing here deletes, a person decides.

What exists is compared with what the CMP knows is alive: projects, and
apps whose row is not deleted (a deleting app is alive until it is gone).
Each source that cannot be read is reported in ``errors``: a check that did
not run is not a pass. docs/cycle-de-vie-apps.md lists what each path
creates and who deletes it.
"""

import asyncio
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.services import (
    aws_tfstate,
    kube_client,
    project_registry,
    vault_client,
)

logger = logging.getLogger(__name__)

STATE_PREFIX = "cnp/projects/"
PROJECT_POLICIES = ("dev", "read", "system")
VAULT_AUTH_MOUNTS = {
    TargetCloud.ONPREM: "kubernetes",
    TargetCloud.AWS: "kubernetes-aws",
    TargetCloud.GCP: "kubernetes-gcp",
}
# Folders under project-<p> and under cnp/projects/<p> that are the
# project's own, not an app's.
PROJECT_FOLDERS = {"system", "_staging-realm"}


@dataclass(frozen=True)
class Orphan:
    kind: str
    name: str
    project: str
    app: str | None
    reason: str
    cloud: str | None = None


@dataclass
class Alive:
    """What the CMP knows exists: project -> cloud, project -> its apps."""

    projects: dict[str, str]
    apps: dict[str, set[str]] = field(default_factory=dict)

    def has_app(self, project: str, app: str) -> bool:
        return app in self.apps.get(project, set())


def alive_from_db() -> Alive:
    with SessionLocal() as db:
        projects = {
            p.project_name: p.target_cloud.value for p in db.query(Project)
        }
        apps: dict[str, set[str]] = {}
        for row in db.query(Deployment).filter(
            Deployment.status != DeploymentStatus.DELETED,
            Deployment.project_id.isnot(None),
        ):
            apps.setdefault(row.project_id, set()).add(row.name)
    return Alive(projects, apps)


def _verdict(
    alive: Alive, project: str, app: str | None
) -> tuple[str | None, str | None]:
    """(app, reason) when (project, app) is gone, (None, None) when alive."""
    if project not in alive.projects:
        return app, "project no longer exists in the CMP"
    if app is None or app in PROJECT_FOLDERS:
        return None, None
    if not alive.has_app(project, app):
        return app, "no app of that name in the project"
    return None, None


def _split(name: str, projects: Iterable[str]) -> tuple[str, str] | None:
    """(project, rest) for '<project>-<rest>', the longest project first:
    'shop-eu-api' belongs to 'shop-eu' when both projects exist."""
    for project in sorted(projects, key=len, reverse=True):
        if name.startswith(f"{project}-") and len(name) > len(project) + 1:
            return project, name[len(project) + 1 :]
    return None


# ── Checks: each takes what is observed and returns the orphans in it ──────


def check_registry(alive: Alive, records: list[dict]) -> list[Orphan]:
    found = []
    for record in records:
        project = (record.get("metadata") or {}).get("name", "")
        if project not in alive.projects:
            found.append(
                Orphan(
                    "registry_project",
                    project,
                    project,
                    None,
                    "registry record of a project the CMP does not have",
                )
            )
            continue
        for entry in (record.get("spec") or {}).get("apps") or []:
            app = entry.get("name", "")
            if not alive.has_app(project, app):
                found.append(
                    Orphan(
                        "registry_app",
                        f"{project}/{app}",
                        project,
                        app,
                        "registry entry without a live app",
                    )
                )
    return found


def check_namespaces(
    alive: Alive, cloud: str, namespaces: list[dict]
) -> list[Orphan]:
    found = []
    for namespace in namespaces:
        name = namespace["metadata"]["name"]
        labels = namespace["metadata"].get("labels") or {}
        project = labels.get(project_registry.PROJECT_LABEL)
        if not project:
            continue
        rest = (
            name[len(project) + 1 :] if name.startswith(f"{project}-") else ""
        )
        app, reason = _verdict(
            alive, project, re.sub(r"-stg$", "", rest) or None
        )
        if reason:
            found.append(
                Orphan("namespace", name, project, app, reason, cloud)
            )
    return found


def check_kv(alive: Alive, project: str, paths: list[str]) -> list[Orphan]:
    found = []
    for folder in sorted({p.split("/", 1)[0] for p in paths}):
        app, reason = _verdict(alive, project, folder)
        if reason:
            found.append(
                Orphan(
                    "vault_secret",
                    f"project-{project}/{folder}",
                    project,
                    app,
                    reason,
                )
            )
    return found


def _vault_owner(name: str, suffix: str, projects: set[str]):
    """(project, app or None) a role or policy named by the CNP layouts
    belongs to, None when it is not one of ours."""
    project_level = re.fullmatch(rf"project-(.+)-(\w+)-{suffix}", name)
    if project_level and project_level.group(2) in PROJECT_POLICIES:
        return project_level.group(1), None
    staging = re.fullmatch(rf"(?:project-)?(.+)-staging-{suffix}", name)
    base = staging.group(1) if staging else None
    if base is None and name.endswith(f"-{suffix}"):
        base = name[: -len(suffix) - 1]
    return None if base is None else _split(base, projects)


def check_vault_names(
    alive: Alive,
    kind: str,
    suffix: str,
    names: list[str],
    known_projects: set[str],
    cloud: str | None = None,
) -> list[Orphan]:
    found = []
    for name in names:
        owner = _vault_owner(name, suffix, known_projects)
        if owner is None:
            continue
        project, app = owner
        app, reason = _verdict(alive, project, app)
        if reason:
            found.append(Orphan(kind, name, project, app, reason, cloud))
    return found


def check_states(alive: Alive, keys: list[str]) -> list[Orphan]:
    found = []
    seen = set()
    for key in keys:
        parts = key[len(STATE_PREFIX) :].split("/")
        if len(parts) < 2:
            continue
        project, folder = parts[0], parts[1]
        if (project, folder) in seen:
            continue
        seen.add((project, folder))
        app, reason = _verdict(alive, project, folder)
        if reason:
            found.append(
                Orphan(
                    "tfstate",
                    f"{STATE_PREFIX}{project}/{folder}/",
                    project,
                    app,
                    reason,
                )
            )
    return found


# ── Sources ────────────────────────────────────────────────────────────────


async def find(alive: Alive | None = None) -> dict:
    """Every orphan found, and the sources that could not be read."""
    alive = alive or await asyncio.to_thread(alive_from_db)
    orphans: list[Orphan] = []
    errors: list[str] = []

    async def run(source: str, check: Callable) -> None:
        try:
            orphans.extend(await check())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Orphan check %s failed: %s", source, exc)
            errors.append(f"{source}: {exc}")

    records: list[dict] = []

    async def registry():
        records.extend(await project_registry.list_records())
        return check_registry(alive, records)

    await run("registry", registry)
    known = set(alive.projects) | {
        (r.get("metadata") or {}).get("name", "") for r in records
    }
    clouds = {TargetCloud.ONPREM} | {
        TargetCloud(c) for c in alive.projects.values()
    }
    for cloud in sorted(clouds, key=lambda c: c.value):

        async def namespaces(cloud=cloud):
            items = await asyncio.to_thread(
                kube_client.kube_list,
                "/api/v1/namespaces",
                {"labelSelector": project_registry.PROJECT_LABEL},
                cloud,
            )
            known.update(
                (n["metadata"].get("labels") or {}).get(
                    project_registry.PROJECT_LABEL, ""
                )
                for n in items
            )
            return check_namespaces(alive, cloud.value, items)

        await run(f"namespaces ({cloud.value})", namespaces)
    known.discard("")
    for project in sorted(alive.projects):

        async def kv(project=project):
            return check_kv(
                alive,
                project,
                await vault_client.list_paths(f"project-{project}"),
            )

        await run(f"vault secrets (project-{project})", kv)
    for cloud, mount in VAULT_AUTH_MOUNTS.items():
        if cloud not in clouds:
            continue

        async def roles(mount=mount, cloud=cloud):
            return check_vault_names(
                alive,
                "vault_role",
                "role",
                await vault_client.list_names(f"auth/{mount}/role"),
                known,
                cloud.value,
            )

        await run(f"vault roles ({mount})", roles)

    async def policies():
        return check_vault_names(
            alive,
            "vault_policy",
            "policy",
            await vault_client.list_names("sys/policies/acl"),
            known,
        )

    await run("vault policies", policies)

    async def states():
        return check_states(
            alive,
            await asyncio.to_thread(aws_tfstate.state_keys, STATE_PREFIX),
        )

    if settings.TF_BACKEND_S3_BUCKET:
        await run("terraform states", states)
    return {
        "orphans": [asdict(o) for o in orphans],
        "errors": errors,
    }
