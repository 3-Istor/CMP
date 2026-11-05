"""
Useful links of a project and of an app, generated here so the frontend
never builds a URL.

Each link carries the weakest role allowed to see it; callers get only what
their role reaches.
"""

import logging
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel

from app.core.roles import ProjectRole, at_least
from app.models.deployment import Deployment
from app.services import app_security_data as security_data
from app.services.environments import layout
from app.services.grafana_service import GRAFANA_BASE_URL
from app.services.kube_client import KubeUnavailableError, kube_list

logger = logging.getLogger(__name__)

ARGOCD_URL = "https://argocd.3istor.com"
VAULT_URL = "https://vault.3istor.com"
KEYCLOAK_ADMIN_URL = "https://admin-auth.3istor.com"
DOMAIN = "3istor.com"
APPLICATIONS_PATH = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"

LinkGroup = Literal["view", "code", "admin"]


class Link(BaseModel):
    id: str
    label_key: str
    url: str
    external: bool = True
    group: LinkGroup
    min_role: ProjectRole
    requires: Literal["grafana_member"] | None = None
    component: str | None = None
    app: str | None = None


def vault_project_url(project: str) -> str:
    return f"{VAULT_URL}/ui/vault/secrets/kvv2/kv/list/projects/{project}/"


def keycloak_console_url(realm: str) -> str:
    return f"{KEYCLOAK_ADMIN_URL}/admin/{realm}/console/"


def vault_app_url(project: str, app: str) -> str:
    path = quote(f"projects/{project}/{app}", safe="")
    return f"{VAULT_URL}/ui/vault/secrets/kvv2/kv/{path}/details"


def argocd_application_url(name: str) -> str:
    return f"{ARGOCD_URL}/applications/argocd/{name}?resource="


def repo_url(github_repo_url: str) -> str:
    url = github_repo_url.strip().rstrip("/")
    return url.removesuffix(".git")


def _reachable(links: list[Link], role: ProjectRole) -> list[Link]:
    return [link for link in links if at_least(role, link.min_role)]


def application_names(deployment: Deployment) -> list[str]:
    """
    The Argo CD Applications of an app, as the cluster reports them.

    Falls back to the naming pattern when the Kubernetes API cannot be read,
    so the links still point somewhere real for the prod environment.
    """
    project = deployment.project_id or ""
    selector = f"cnp.3istor.com/app={deployment.name},cnp.3istor.com/project={project}"
    try:
        items = kube_list(APPLICATIONS_PATH, {"labelSelector": selector})
    except KubeUnavailableError as exc:
        logger.warning(
            "Argo CD Applications of %s/%s not readable, using the name "
            "pattern: %s",
            project,
            deployment.name,
            exc,
        )
        app_type = security_data.app_type_of(
            deployment.app_config, deployment.terraform_outputs
        )
        return [
            layout.application(project, deployment.name, component, "prod")
            for component in layout.components(app_type)
        ]
    return sorted(item["metadata"]["name"] for item in items)


def _repo_links(deployment: Deployment) -> list[Link]:
    if not deployment.github_repo_url:
        return []
    repo = repo_url(deployment.github_repo_url)
    return [
        Link(
            id="github_repo",
            label_key="links.github_repo",
            url=repo,
            group="code",
            min_role=ProjectRole.GUEST,
            app=deployment.name,
        ),
        Link(
            id="github_actions",
            label_key="links.github_actions",
            url=f"{repo}/actions",
            group="code",
            min_role=ProjectRole.MEMBER,
            app=deployment.name,
        ),
    ]


def app_links(
    deployment: Deployment, role: ProjectRole, names: list[str] | None = None
) -> list[Link]:
    project = deployment.project_id or ""
    names = application_names(deployment) if names is None else names
    prefix = f"{project}-{deployment.name}-"
    result = [
        Link(
            id="argocd_component",
            label_key="links.argocd_component",
            url=argocd_application_url(name),
            group="view",
            min_role=ProjectRole.MEMBER,
            component=name.removeprefix(prefix),
            app=deployment.name,
        )
        for name in names
    ]
    result.append(
        Link(
            id="vault",
            label_key="links.vault",
            url=vault_app_url(project, deployment.name),
            group="admin",
            min_role=ProjectRole.ADMIN,
            app=deployment.name,
        )
    )
    result.extend(_repo_links(deployment))
    return _reachable(result, role)


def project_links(
    project: str,
    role: ProjectRole,
    deployments: list[Deployment],
    grafana_org: int | None,
    has_staging: bool = False,
) -> list[Link]:
    result = [
        Link(
            id="argocd_project",
            label_key="links.argocd_project",
            url=f"{ARGOCD_URL}/applications?proj={project}",
            group="view",
            min_role=ProjectRole.MEMBER,
        ),
        Link(
            id="status",
            label_key="links.status",
            url=f"https://status-{project}.{DOMAIN}/",
            group="view",
            min_role=ProjectRole.MEMBER,
        ),
        Link(
            id="offhours",
            label_key="links.offhours",
            url=f"https://offhours-{project}.{DOMAIN}/",
            group="view",
            min_role=ProjectRole.MEMBER,
        ),
        Link(
            id="vault",
            label_key="links.vault",
            url=vault_project_url(project),
            group="admin",
            min_role=ProjectRole.ADMIN,
        ),
    ]
    # Project admins sign in with their platform account (project-admins carries realm-admin).
    result.append(
        Link(
            id="keycloak",
            label_key="links.keycloak",
            url=keycloak_console_url(project),
            group="admin",
            min_role=ProjectRole.ADMIN,
        )
    )
    if has_staging:
        result.append(
            Link(
                id="keycloak_staging",
                label_key="links.keycloak_staging",
                url=keycloak_console_url(f"{project}-staging"),
                group="admin",
                min_role=ProjectRole.ADMIN,
            )
        )
    if grafana_org is not None:
        result.append(
            Link(
                id="grafana_org",
                label_key="links.grafana_org",
                url=f"{GRAFANA_BASE_URL}/?orgId={grafana_org}",
                group="view",
                min_role=ProjectRole.MEMBER,
                requires="grafana_member",
            )
        )
    for deployment in deployments:
        result.extend(_repo_links(deployment)[:1])
    return _reachable(result, role)
