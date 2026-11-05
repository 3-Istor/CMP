"""
The project's staging Keycloak realm, `<project>-staging`.

Staging clients live there so the customers of the prod realm never exist in
staging. The module assumes the project bootstrap ran (Vault mount, groups).
"""

from pathlib import Path

from app.services.template_repository import get_repository
from app.services.terraform_executor import create_executor


def realm_name(project: str) -> str:
    return f"{project}-staging"


def _executor(project: str):
    module = get_repository().get_template_path("k3s-gitops-app")
    if module is None:
        raise RuntimeError("The k3s-gitops-app template is not available.")
    # "_" cannot appear in an app name, so neither the state key nor the local
    # state dir can collide with an app's.
    return create_executor(
        Path(module) / "staging-realm",
        f"{project}-_staging-realm",
        f"cnp/projects/{project}/_staging-realm",
        log_file=Path("logs/projects") / f"{project}-staging-realm.log",
    )


def ensure(project: str) -> str:
    """Create the realm if missing; returns its name. Blocking."""
    executor = _executor(project)
    executor.init()
    return executor.apply({"project_name": project})["realm"]


def destroy(project: str) -> None:
    """Delete the realm and everything in it. Blocking."""
    executor = _executor(project)
    executor.init()
    executor.destroy({"project_name": project})
