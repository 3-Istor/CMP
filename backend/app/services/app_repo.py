"""
The GitHub repository an app's Day-2 tools may read and write.

``github_repo_url`` is derived from the app name, not read back from GitHub, so
a row only names the repository Terraform created for it once its apply went
through: the repository resource fails on a name that already exists. Rows that
never got there (failed, deleted, still deploying) are refused, as are URLs
outside the platform org or not named after the app.
"""

from app.core.config import settings
from app.models.deployment import DeploymentStatus

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
        "landing",
        "project-template",
        "talos-config",
        "template-app-webapp-python-fastapi-react",
        "template-html-css",
    )
)

_BOUND_STATUSES = frozenset(
    {DeploymentStatus.RUNNING.value, DeploymentStatus.DEGRADED.value}
)


class RepoNotBound(ValueError):
    """The app has no repository its tools may touch."""


def is_platform_repo_name(name: str) -> bool:
    return name.strip().lower() in PLATFORM_REPO_NAMES


def repo_url_for(app_name: str) -> str:
    return f"https://github.com/{settings.GITHUB_ORG}/{app_name}"


def bound_repo(github_repo_url: str | None, app_name: str, status) -> str:
    """``owner/name`` of the app's own repository, or RepoNotBound."""
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
