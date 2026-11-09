"""
Project Registry Service

Projects a project's placement into ``cnp-projects/registry/projects/<name>.yaml``.

Per D-01, the Git record is the source of truth for ``targetCloud``; the ``projects``
table carries a mirror so the portal can list without reading Git. This module is the
only writer of the Git side.

Consumers of the record: the Argo CD ApplicationSet (WS-3), the per-project add-ons in
``cnp-project-base`` (WS-4/WS-5), the blackbox probe target list (WS-8), and the
``cnp-clean`` job (WS-9).
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from io import StringIO

from ruamel.yaml import YAML

from app.core.config import settings
from app.models.project import ProjectStatus, TargetCloud
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    delete_file,
    get_file_content,
    get_installation_token,
    list_directory,
    put_file_content,
)
from app.services.registry_names import check_app, check_new_project

logger = logging.getLogger(__name__)

RECORD_API_VERSION = "cnp.3istor.com/v1alpha1"
RECORD_KIND = "ProjectRecord"

_yaml = YAML()
_yaml.default_flow_style = False


class RegistryError(Exception):
    """Raised when the project registry cannot be read or written."""


class RecordNotFoundError(RegistryError):
    """Raised when a write needs a project's record and it has none."""


class ImmutableCloudError(RegistryError):
    """Raised when a write would change an existing record's target cloud.

    ``targetCloud`` is immutable in v1: changing it does not move a project, it
    orphans everything already provisioned on the old cloud.
    """


class ProtectedRecordError(RegistryError):
    """Raised when a write would reset a deletion-protected record."""


class NameCollisionError(RegistryError):
    """Raised when a record's derived names collide with another record's."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def record_path(project_name: str) -> str:
    return f"{settings.CNP_REGISTRY_PATH_PREFIX}/{project_name}.yaml"


def build_record(
    project_name: str,
    owner_username: str,
    target_cloud: TargetCloud,
    created_at: datetime | None = None,
    status: ProjectStatus = ProjectStatus.ACTIVE,
) -> str:
    """
    Render a ProjectRecord as YAML.

    ``environments`` carries the Prod/Staging seam from D-11 without lighting it
    up: every record written today is prod-only.
    """
    record = {
        "apiVersion": RECORD_API_VERSION,
        "kind": RECORD_KIND,
        "metadata": {
            "name": project_name,
            "owner": owner_username,
            "createdAt": (
                created_at or datetime.now(timezone.utc)
            ).isoformat(),
        },
        "spec": {
            "targetCloud": target_cloud.value,
            "status": status.value,
            "environments": [{"name": "prod"}],
            "apps": [],
            "features": {
                "gatus": True,
                "offhoursGuard": True,
                # An app's DNS record always points at the project's own
                # Cloudflare tunnel (D-06, k3s-gitops-app/main.tf), and that
                # tunnel forwards to the project's own gateway, there is no
                # code path where an app is reachable with either of these
                # off. Both need to be on from project creation, not opted
                # into later.
                "gateway": True,
                "tunnel": True,
            },
        },
    }
    buffer = StringIO()
    _yaml.dump(record, buffer)
    return buffer.getvalue()


async def read_record(project_name: str) -> tuple[dict, str] | None:
    """
    Read a project's record.

    Returns:
        ``(record, sha)``, or ``None`` if the project has no record yet.

    Raises:
        RegistryError: If the registry is unreachable or the record is malformed.
    """
    token = await _installation_token()
    try:
        raw, sha = await get_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            ref=settings.CNP_REGISTRY_BRANCH,
        )
    except FileNotInRepoError:
        return None
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not read the registry record for '{project_name}': {exc}"
        ) from exc

    parsed = _yaml.load(raw)
    if not isinstance(parsed, dict):
        raise RegistryError(
            f"Registry record for '{project_name}' is not a YAML mapping"
        )
    return parsed, sha


async def list_records() -> list[dict]:
    """
    Read every project record in the registry.

    Records that are not a YAML mapping are skipped: one malformed file must
    not block every other project from being created.

    Raises:
        RegistryError: If the registry is unreachable.
    """
    token = await _installation_token()
    try:
        names = await list_directory(
            token,
            settings.CNP_REGISTRY_REPO,
            settings.CNP_REGISTRY_PATH_PREFIX,
            ref=settings.CNP_REGISTRY_BRANCH,
        )
        contents = await asyncio.gather(
            *(
                get_file_content(
                    token,
                    settings.CNP_REGISTRY_REPO,
                    f"{settings.CNP_REGISTRY_PATH_PREFIX}/{name}",
                    ref=settings.CNP_REGISTRY_BRANCH,
                )
                for name in names
                if name.endswith(".yaml")
            )
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not list the registry records: {exc}"
        ) from exc

    records: list[dict] = []
    for raw, _ in contents:
        parsed = _yaml.load(raw)
        if isinstance(parsed, dict):
            records.append(parsed)
        else:
            logger.warning("Skipping a registry record that is not a mapping")
    return records


async def ensure_project_names_free(project_name: str) -> None:
    """
    Raises:
        NameCollisionError: If a name derived from the project collides with
            one derived from another project's record.
        RegistryError: If the registry cannot be read.
    """
    problems = check_new_project(project_name, await list_records())
    if problems:
        raise NameCollisionError(
            [f"Project '{project_name}': {p}" for p in problems]
        )


async def ensure_app_names_free(
    project_name: str,
    app_name: str,
    app_type: str,
    hostname: str | None = None,
) -> None:
    """
    Raises:
        NameCollisionError: If the app's name is reserved or invalid, or a
            name derived from it collides with another project's or app's.
        RegistryError: If the registry cannot be read.
    """
    app: dict = {"name": app_name, "type": app_type}
    if hostname:
        app["hostnames"] = {"prod": hostname}
    problems = check_app(project_name, app, await list_records())
    if problems:
        raise NameCollisionError(
            [
                f"App '{app_name}' of project '{project_name}': {p}"
                for p in problems
            ]
        )


async def publish_record(
    project_name: str,
    owner_username: str,
    target_cloud: TargetCloud,
    created_at: datetime | None = None,
) -> None:
    """
    Create a project's registry record. An existing one, left by the first
    attempt of a retried creation, is kept as is: rewriting it would drop its
    apps and flags.

    Raises:
        ImmutableCloudError: If a record already exists on a different cloud.
        ProtectedRecordError: If the existing record has deletion protection
            on: overwriting it would drop the flag.
        RegistryError: If the registry cannot be written.
    """
    existing = await read_record(project_name)
    if existing is not None:
        record, _ = existing
        if (record.get("spec") or {}).get("deletionProtection") is True:
            raise ProtectedRecordError(
                f"Project '{project_name}' is deletion-protected: turn "
                "protection off before re-creating it."
            )
        current = record.get("spec", {}).get("targetCloud")
        if current != target_cloud.value:
            raise ImmutableCloudError(
                f"Project '{project_name}' is already registered on '{current}'. "
                f"target_cloud is immutable, moving it to '{target_cloud.value}' "
                "would orphan every resource already provisioned on "
                f"'{current}'. Re-create the project instead."
            )
        return

    token = await _installation_token()
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            build_record(
                project_name, owner_username, target_cloud, created_at
            ),
            message=(
                f"feat(registry): register project {project_name} "
                f"on {target_cloud.value} [skip ci]"
            ),
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not write the registry record for '{project_name}': {exc}"
        ) from exc

    logger.info(
        "Registered project '%s' in the registry on '%s'",
        project_name,
        target_cloud.value,
    )


async def register_app(
    project_name: str,
    app_name: str,
    app_type: str,
    repo_url: str,
    hostname: str,
) -> None:
    """
    Add or update an app's entry in a project's registry record.

    This is what puts the app in the connector's routing table and the
    blackbox probe target list (see k3s-gitops-app/outputs.tf's ``hostname``
    output), an app whose Terraform apply succeeded but was never
    registered here is unreachable: its Cloudflare tunnel and Gateway exist,
    but nothing tells them the app's hostname.

    Idempotent: re-registering an existing app (redeploy) updates its entry
    in place rather than duplicating it.

    Raises:
        RegistryError: If the project has no record, or the registry cannot
            be written.
    """
    existing = await read_record(project_name)
    if existing is None:
        raise RegistryError(
            f"Cannot register app '{app_name}': project '{project_name}' "
            "has no registry record."
        )

    await ensure_app_names_free(project_name, app_name, app_type, hostname)

    record, sha = existing
    apps: list[dict] = record.setdefault("spec", {}).setdefault("apps", [])
    entry = {
        "name": app_name,
        "type": app_type,
        "repoURL": repo_url,
        "hostnames": {"prod": hostname},
    }
    for i, app in enumerate(apps):
        if app.get("name") == app_name:
            apps[i] = entry
            break
    else:
        apps.append(entry)

    buffer = StringIO()
    _yaml.dump(record, buffer)

    token = await _installation_token()
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            buffer.getvalue(),
            message=(
                f"feat(registry): register app {app_name} on "
                f"{project_name} [skip ci]"
            ),
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not register app '{app_name}' for '{project_name}': {exc}"
        ) from exc

    logger.info(
        "Registered app '%s' (%s) on project '%s' with hostname '%s'",
        app_name,
        app_type,
        project_name,
        hostname,
    )


async def unregister_app(project_name: str, app_name: str) -> None:
    """
    Remove an app's entry from a project's registry record.

    A project with no record, or a record with no matching app entry, is not
    an error, the app may already be gone, or the project itself may have
    been torn down first.

    Raises:
        RegistryError: If the registry cannot be written.
    """
    existing = await read_record(project_name)
    if existing is None:
        return

    record, sha = existing
    apps: list[dict] = record.get("spec", {}).get("apps", [])
    remaining = [app for app in apps if app.get("name") != app_name]
    if len(remaining) == len(apps):
        return
    record["spec"]["apps"] = remaining

    buffer = StringIO()
    _yaml.dump(record, buffer)

    token = await _installation_token()
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            buffer.getvalue(),
            message=(
                f"feat(registry): unregister app {app_name} on "
                f"{project_name} [skip ci]"
            ),
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not unregister app '{app_name}' for '{project_name}': {exc}"
        ) from exc

    logger.info(
        "Unregistered app '%s' from project '%s'", app_name, project_name
    )


async def remove_record(project_name: str) -> None:
    """
    Remove a project's registry record.

    Removing the record is what makes the generated Argo CD Applications and the
    AppProject disappear, so it is a step of teardown rather than a cleanup
    afterwards. A project with no record is not an error, teardown is retried.

    Raises:
        RegistryError: If the registry cannot be written.
    """
    existing = await read_record(project_name)
    if existing is None:
        logger.info(
            "Project '%s' has no registry record to remove", project_name
        )
        return

    _, sha = existing
    token = await _installation_token()
    try:
        await delete_file(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            message=f"feat(registry): deregister project {project_name} [skip ci]",
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not remove the registry record for '{project_name}': {exc}"
        ) from exc


async def _installation_token() -> str:
    if not settings.GITHUB_INSTALLATION_ID:
        raise RegistryError(
            "GITHUB_INSTALLATION_ID is not configured, the registry cannot be reached."
        )
    try:
        return await get_installation_token(settings.GITHUB_INSTALLATION_ID)
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not obtain a GitHub installation token: {exc}"
        ) from exc


async def write_security_policy(
    project_name: str, record: dict, sha: str, author: str
) -> None:
    """
    Write *record*, whose ``spec.securityPolicy`` was changed by a CNP admin.

    Raises:
        RegistryError: If the registry cannot be written, including when the
            record changed since it was read.
    """
    buffer = StringIO()
    _yaml.dump(record, buffer)
    token = await _installation_token()
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            buffer.getvalue(),
            message=(
                f"feat(registry): security policy of {project_name} "
                f"by {author} [skip ci]"
            ),
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not write the security policy of '{project_name}': {exc}"
        ) from exc


async def set_app_environments(
    project_name: str,
    app_name: str,
    environments: list[str],
    staging_hostname: str | None,
) -> None:
    """
    Turn an app's staging environment on or off in the registry record.

    This is what makes Argo CD create (or prune) the staging namespace,
    Applications, tunnel route and image updater.

    Raises:
        RegistryError: No record or no such app, staging over the project's
            cap, or the registry cannot be written.
    """
    existing = await read_record(project_name)
    if existing is None:
        raise RegistryError(
            f"Project '{project_name}' has no registry record."
        )
    record, sha = existing
    spec = record.setdefault("spec", {})
    apps = spec.get("apps") or []
    entry = next((app for app in apps if app.get("name") == app_name), None)
    if entry is None:
        raise RegistryError(
            f"App '{app_name}' is not in project '{project_name}'."
        )

    if "staging" in environments:
        staging = next(
            (
                env
                for env in spec.get("environments") or []
                if isinstance(env, dict) and env.get("name") == "staging"
            ),
            {},
        )
        # Without a staging entry a project gets one app; maxApps 0 forbids it.
        cap = staging.get("maxApps", 1)
        others = [
            app.get("name")
            for app in apps
            if app.get("name") != app_name
            and "staging" in (app.get("environments") or [])
        ]
        if len(others) >= cap:
            raise RegistryError(
                f"Project '{project_name}' already has {len(others)} app(s) with staging, "
                f"its limit is {cap}: {', '.join(others) or 'none'}. "
                "A platform admin can raise it."
            )
        problems = check_app(
            project_name,
            {**dict(entry), "environments": environments},
            await list_records(),
        )
        if problems:
            raise NameCollisionError(problems)

    if environments == ["prod"]:
        entry.pop("environments", None)
    else:
        entry["environments"] = list(environments)
    hostnames = entry.setdefault("hostnames", {})
    if staging_hostname:
        hostnames["staging"] = staging_hostname
    else:
        hostnames.pop("staging", None)

    buffer = StringIO()
    _yaml.dump(record, buffer)
    token = await _installation_token()
    verb = "enable" if "staging" in environments else "disable"
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            buffer.getvalue(),
            message=f"feat(registry): {verb} staging for {app_name} on {project_name} [skip ci]",
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not update the environments of '{app_name}': {exc}"
        ) from exc


async def _is_on(project_name: str, field: str) -> bool:
    existing = await read_record(project_name)
    if existing is None:
        return False
    return (existing[0].get("spec") or {}).get(field) is True


async def _commit_spec(
    project_name: str,
    change: Callable[[dict], None],
    message: str,
    label: str,
) -> None:
    """Apply ``change`` to the record's spec and commit it in one write."""
    existing = await read_record(project_name)
    if existing is None:
        raise RecordNotFoundError(
            f"Project '{project_name}' has no registry record."
        )
    record, sha = existing
    change(record.setdefault("spec", {}))

    buffer = StringIO()
    _yaml.dump(record, buffer)
    token = await _installation_token()
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            buffer.getvalue(),
            message=f"feat(registry): {message} for {project_name} [skip ci]",
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not update {label} of '{project_name}': {exc}"
        ) from exc


async def _set_on(
    project_name: str, field: str, enabled: bool, label: str
) -> None:
    def change(spec: dict) -> None:
        if enabled:
            spec[field] = True
        else:
            spec.pop(field, None)

    verb = "on" if enabled else "off"
    await _commit_spec(project_name, change, f"turn {label} {verb}", label)


async def is_deletion_protected(project_name: str) -> bool:
    """
    Whether the project's record turns deletion protection on.

    A project with no record is not protected.

    Raises:
        RegistryError: If the registry cannot be read.
    """
    return await _is_on(project_name, "deletionProtection")


async def set_deletion_protection(project_name: str, enabled: bool) -> None:
    """
    Turn a project's deletion protection on, or off by removing the field.

    Argo CD then flips the protection label on the project's cluster objects.

    Raises:
        RecordNotFoundError: The project has no registry record.
        RegistryError: The registry cannot be written.
    """
    await _set_on(
        project_name, "deletionProtection", enabled, "deletion protection"
    )


async def get_customer_access(project_name: str) -> str:
    """
    How customers get an account: ``closed``, ``companies`` or ``open``.

    A record from before the modes with ``selfRegistration: true`` is
    ``open``; no record, or an unknown value, is ``closed``.

    Raises:
        RegistryError: If the registry cannot be read.
    """
    existing = await read_record(project_name)
    spec = (existing[0].get("spec") or {}) if existing else {}
    mode = spec.get("customerAccess")
    if mode is None and spec.get("selfRegistration") is True:
        return "open"
    return mode if mode in ("companies", "open") else "closed"


async def set_customer_access(project_name: str, mode: str) -> None:
    """
    Save the customer access mode; ``closed`` removes the field.

    Raises:
        RecordNotFoundError: The project has no registry record.
        RegistryError: The registry cannot be written.
    """

    def change(spec: dict) -> None:
        spec.pop("selfRegistration", None)
        if mode == "closed":
            spec.pop("customerAccess", None)
        else:
            spec["customerAccess"] = mode

    await _commit_spec(
        project_name,
        change,
        f"set customer access to {mode}",
        "customer access",
    )


async def set_project_staging(project_name: str, max_apps: int) -> None:
    """
    Set how many apps of a project may run staging; 0 forbids it.

    Raises:
        RegistryError: No record, apps still use staging beyond the new cap,
            or the registry cannot be written.
    """
    existing = await read_record(project_name)
    if existing is None:
        raise RegistryError(
            f"Project '{project_name}' has no registry record."
        )
    record, sha = existing
    spec = record.setdefault("spec", {})
    using = [
        app.get("name")
        for app in spec.get("apps") or []
        if "staging" in (app.get("environments") or [])
    ]
    if len(using) > max_apps:
        raise RegistryError(
            f"{len(using)} app(s) use staging ({', '.join(using)}): turn it off on "
            f"them before lowering the limit to {max_apps}."
        )
    environments = [
        env
        for env in spec.get("environments") or [{"name": "prod"}]
        if env.get("name") != "staging"
    ]
    # Written even at 0: an absent entry would allow one app.
    environments.append(
        {"name": "staging", "offhours": True, "maxApps": max_apps}
    )
    spec["environments"] = environments

    buffer = StringIO()
    _yaml.dump(record, buffer)
    token = await _installation_token()
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            buffer.getvalue(),
            message=f"feat(registry): staging for up to {max_apps} app(s) on {project_name} [skip ci]",
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not update project '{project_name}': {exc}"
        ) from exc
