"""
The GitHub repository an app's Day-2 tools may read and write.

For template apps, ``github_repo_url`` is derived from the app name, not read
back from GitHub, so a row only names the repository Terraform created for it
once its apply went through: the repository resource fails on a name that
already exists. Rows that never got there (failed, deleted, still deploying)
are refused, as are URLs outside the platform org or not named after the app.

Imported apps (``import-*`` templates) name the repository the import reads
from: ``bound_repo`` never binds them. ``import_binding`` does, once the
import's setup pull request was merged and the app switched to the
repository's deploy/ folder: the platform then writes that folder like a
template app's, with a token down-scoped to the one repository.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.deployment import DeploymentStatus
from app.models.imports import ImportService, ImportSource

# Platform repositories in the org. An app named like one of them would get
# its URL, so these names are refused at creation and never resolved.
PLATFORM_REPO_NAMES = frozenset(
    name.lower()
    for name in (
        ".github",
        "app-templates",
        "CMP",
        "Cloud",
        "cnp-agent-kit",
        "cnp-docs",
        "cnp-project-base",
        "cnp-projects",
        "ia-project-template",
        "infra-templates",
        "K3s",
        "keycloak-theme",
        "project-template",
        "talos-config",
        "template-app-webapp-python-fastapi-react",
        "template-html-css",
    )
)

_BOUND_STATUSES = frozenset(
    {DeploymentStatus.RUNNING.value, DeploymentStatus.DEGRADED.value}
)


IMPORTED = "The app was imported: the platform does not manage its repository."
NOT_MANAGED = (
    "The app was imported and its repository is not managed by the platform "
    "yet: merge the import's setup pull request to manage it here."
)
NOT_MANAGED_CODE = "repo_not_managed"
# standard_pr.SWITCHED, not imported: standard_pr imports the routers' world.
SWITCHED = "switched"


class RepoNotBound(ValueError):
    """The app has no repository its tools may touch."""


class RepoNotManaged(RepoNotBound):
    """An imported app whose repository the platform does not write."""

    def __init__(self) -> None:
        super().__init__(NOT_MANAGED)


def not_managed_detail() -> dict[str, str]:
    """The 409 body the CMP answers for it, a code the UI recognises."""
    return {"code": NOT_MANAGED_CODE, "message": NOT_MANAGED}


def is_imported(template_id: str | None) -> bool:
    return (template_id or "").startswith("import-")


@dataclass(frozen=True)
class ImportBinding:
    """The repository of a switched import app, and how to reach it."""

    repo: str
    repo_id: int
    installation_id: int
    branch: str
    values_file: str


def import_source_of(
    db: Session, deployment_id: int
) -> tuple[ImportSource, ImportService] | None:
    row = (
        db.query(ImportService)
        .filter(ImportService.deployment_id == deployment_id)
        .first()
    )
    return (row.source, row) if row is not None else None


def is_managed_import(source: ImportSource, app_name: str) -> bool:
    """
    The platform writes the repository of an import's app once the setup
    pull request switched that app to it. Only a repository of the org can
    be switched (standard_pr.ineligible), checked again here: a source row
    renamed to a foreign repository must not be written.
    """
    record = source.standard_repo or {}
    owner = source.repo_full_name.split("/", 1)[0]
    return (
        record.get("state") == SWITCHED
        and app_name in (record.get("switched") or [])
        and owner.lower() == settings.GITHUB_ORG.lower()
    )


def import_binding(db: Session | None, deployment) -> ImportBinding:
    """The switched repository of an imported app, or RepoNotManaged."""
    found = (
        import_source_of(db, deployment.id)
        if db is not None and deployment.id is not None
        else None
    )
    if found is None:
        raise RepoNotManaged()
    source, service = found
    if not is_managed_import(source, service.app_name):
        raise RepoNotManaged()
    return ImportBinding(
        repo=source.repo_full_name,
        repo_id=source.repo_id,
        installation_id=source.installation_id,
        branch=source.branch,
        values_file=f"deploy/values-{service.app_name}.yaml",
    )


def is_platform_repo_name(name: str) -> bool:
    return name.strip().lower() in PLATFORM_REPO_NAMES


def repo_url_for(app_name: str) -> str:
    return f"https://github.com/{settings.GITHUB_ORG}/{app_name}"


def bound_repo(
    github_repo_url: str | None, app_name: str, status, template_id: str
) -> str:
    """``owner/name`` of the app's own repository, or RepoNotBound."""
    # An imported app's URL is the customer's repository, even when named
    # after the app: Day-2 tools must never write there.
    if template_id.startswith("import-"):
        raise RepoNotBound(IMPORTED)
    status = str(getattr(status, "value", status)).lower()
    if status not in _BOUND_STATUSES:
        raise RepoNotBound(
            f"The app is {status}: its repository is only reachable while "
            "it is running."
        )
    if not github_repo_url or is_platform_repo_name(app_name):
        raise RepoNotBound("The app has no repository of its own.")
    url = github_repo_url.strip().rstrip("/").removesuffix(".git")
    if url.lower() != repo_url_for(app_name).lower():
        raise RepoNotBound(
            "The app's repository is not the one created for it."
        )
    return "/".join(url.split("/")[-2:])
