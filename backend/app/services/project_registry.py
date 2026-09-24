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

import logging
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
    put_file_content,
)

logger = logging.getLogger(__name__)

RECORD_API_VERSION = "cnp.3istor.com/v1alpha1"
RECORD_KIND = "ProjectRecord"

_yaml = YAML()
_yaml.default_flow_style = False


class RegistryError(Exception):
    """Raised when the project registry cannot be read or written."""


class ImmutableCloudError(RegistryError):
    """Raised when a write would change an existing record's target cloud.

    ``targetCloud`` is immutable in v1: changing it does not move a project, it
    orphans everything already provisioned on the old cloud.
    """


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
                # tunnel forwards to the project's own gateway — there is no
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


async def publish_record(
    project_name: str,
    owner_username: str,
    target_cloud: TargetCloud,
    created_at: datetime | None = None,
) -> None:
    """
    Create or refresh a project's registry record.

    Raises:
        ImmutableCloudError: If a record already exists on a different cloud.
        RegistryError: If the registry cannot be written.
    """
    existing = await read_record(project_name)
    sha: str | None = None

    if existing is not None:
        record, sha = existing
        current = record.get("spec", {}).get("targetCloud")
        if current != target_cloud.value:
            raise ImmutableCloudError(
                f"Project '{project_name}' is already registered on '{current}'. "
                f"target_cloud is immutable — moving it to '{target_cloud.value}' "
                "would orphan every resource already provisioned on "
                f"'{current}'. Re-create the project instead."
            )

    token = await _installation_token()
    verb = "Update" if sha else "Register"
    try:
        await put_file_content(
            token,
            settings.CNP_REGISTRY_REPO,
            record_path(project_name),
            build_record(
                project_name, owner_username, target_cloud, created_at
            ),
            message=(
                f"feat(registry): {verb.lower()} project {project_name} "
                f"on {target_cloud.value} [skip ci]"
            ),
            sha=sha,
            branch=settings.CNP_REGISTRY_BRANCH,
        )
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not write the registry record for '{project_name}': {exc}"
        ) from exc

    logger.info(
        "%sed project '%s' in the registry on '%s'",
        verb,
        project_name,
        target_cloud.value,
    )


async def remove_record(project_name: str) -> None:
    """
    Remove a project's registry record.

    Removing the record is what makes the generated Argo CD Applications and the
    AppProject disappear, so it is a step of teardown rather than a cleanup
    afterwards. A project with no record is not an error — teardown is retried.

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
            "GITHUB_INSTALLATION_ID is not configured — the registry cannot be reached."
        )
    try:
        return await get_installation_token(settings.GITHUB_INSTALLATION_ID)
    except GitHubAppError as exc:
        raise RegistryError(
            f"Could not obtain a GitHub installation token: {exc}"
        ) from exc
