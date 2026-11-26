"""
What the CMP writes outside Keycloak for AI: the values files of the app
repositories (always through a pull request a person merges, CNP-304
parity), the client secret in the app's Vault path, and a refresh of the
project's Argo CD Applications so a rights change is rendered at once.
"""

import asyncio
import logging
import secrets as token_bytes

from ruamel.yaml import YAML
from sqlalchemy.orm import Session

from app.models.project import Project, TargetCloud
from app.services import vault_client
from app.services.clusters.state import PUBLIC_CLOUDS
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    commit_files,
    create_branch,
    get_branch_head,
    get_file_content,
    get_org_installation_token,
    get_pull_request,
    open_pull_request,
)
from app.services.imports import generate
from app.services.imports.generate import dump_yaml
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
    kube_merge_patch,
)

logger = logging.getLogger(__name__)

BRANCH_PREFIX = "cnp/ai"
SECRET_NAME = "ai-oidc"
APPLICATIONS = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"


class GitOpsError(RuntimeError):
    pass


def secret_mount(project: str) -> str:
    return f"project-{project}"


def secret_path(app: str) -> str:
    """The path the chart derives for prod."""
    return f"{app}/prod/{SECRET_NAME}"


def secret_ref(project: str, app: str) -> str:
    return f"{secret_mount(project)}/{secret_path(app)}"


# ---------------------------------------------------------------------------
# App repositories
# ---------------------------------------------------------------------------


async def read_values(repo: str, path: str, ref: str = "main") -> dict | None:
    """The values file as a mapping, None when it does not exist."""
    token = await get_org_installation_token()
    try:
        text, _ = await get_file_content(token, repo, path, ref=ref)
    except FileNotInRepoError:
        return None
    loaded = YAML(typ="safe").load(text)
    return loaded if isinstance(loaded, dict) else {}


async def open_pr(
    repo: str,
    files: dict[str, str | None],
    slug: str,
    title: str,
    body: str,
) -> dict:
    """
    One branch from main, one commit, one pull request on *repo*.

    Raises:
        GitOpsError: GitHub refused a step.
    """
    try:
        token = await get_org_installation_token()
        head = await get_branch_head(token, repo, "main")
        branch = f"{BRANCH_PREFIX}-{slug}-{token_bytes.token_hex(3)}"
        await create_branch(token, repo, branch, head)
        await commit_files(
            token, repo, files, title, parent_sha=head, branch=branch
        )
        pull = await open_pull_request(
            token, repo, branch, "main", title, body
        )
    except GitHubAppError as exc:
        raise GitOpsError(
            f"Could not open the pull request on {repo}: {exc}"
        ) from exc
    return {
        "number": int(pull["number"]),
        "url": pull["html_url"],
        "state": "open",
        "branch": branch,
        "repo": repo,
    }


async def pull_state(pull: dict) -> dict:
    """*pull* with its state read again from GitHub, unchanged on failure."""
    try:
        token = await get_org_installation_token()
        live = await get_pull_request(token, pull["repo"], int(pull["number"]))
    except (GitHubAppError, KeyError) as exc:
        logger.info("Pull request state unreadable: %s", exc)
        return pull
    state = "merged" if live.get("merged") else live.get("state", "open")
    return {**pull, "state": state}


def values_with(current: dict | None, key: str, block: dict | None) -> str:
    """The values file with *key* replaced (removed when *block* is None)."""
    values = dict(current or {})
    if block is None:
        values.pop(key, None)
    else:
        values[key] = block
    return dump_yaml(values)


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


def _auth_mounts(db: Session, project: str) -> list[str]:
    row = db.query(Project).filter_by(project_name=project).first()
    cloud = TargetCloud((row.target_cloud if row else None) or "onprem")
    if cloud == TargetCloud.ONPREM:
        return ["kubernetes"]
    return [f"kubernetes-{c.value}" for c in PUBLIC_CLOUDS]


async def store_client_secret(
    db: Session, project: str, app: str, client_id: str, secret: str
) -> None:
    """The secret where the app's VaultSecret reads it, with that app's
    own Vault role (an agent app has no other secret to have created it)."""
    await vault_client.write_kv(
        secret_mount(project),
        secret_path(app),
        {"client_id": client_id, "client_secret": secret},
    )
    policy = generate.app_policy_name(project, app)
    await vault_client.write_policy(policy, generate.app_policy(project, app))
    for mount in _auth_mounts(db, project):
        await vault_client.write_app_role(
            mount, generate.app_role_name(project, app), policy
        )


async def client_secret_in_place(
    db: Session, project: str, app: str, client_id: str, secret: str
) -> bool:
    """What :func:`store_client_secret` writes is there, secret included."""
    current = await vault_client.read_kv(
        secret_mount(project), secret_path(app)
    )
    if current != {"client_id": client_id, "client_secret": secret}:
        return False
    role = generate.app_role_name(project, app)
    for mount in _auth_mounts(db, project):
        if not await vault_client.app_role_exists(mount, role):
            return False
    return True


async def drop_client_secret(db: Session, project: str, app: str) -> None:
    await vault_client.delete_kv(secret_mount(project), secret_path(app))
    for mount in _auth_mounts(db, project):
        await vault_client.delete_app_role(
            mount, generate.app_role_name(project, app)
        )
    await vault_client.delete_policy(generate.app_policy_name(project, app))


# ---------------------------------------------------------------------------
# Argo CD
# ---------------------------------------------------------------------------


def _refresh(project: str) -> int:
    found = kube_list(
        APPLICATIONS, {"labelSelector": f"cnp.3istor.com/project={project}"}
    )
    for app in found:
        kube_merge_patch(
            f"{APPLICATIONS}/{app['metadata']['name']}",
            {
                "metadata": {
                    "annotations": {"argocd.argoproj.io/refresh": "hard"}
                }
            },
        )
    return len(found)


async def refresh_project(project: str) -> None:
    """Ask Argo CD to re-read Git for the project's Applications now
    instead of at its next poll. Best effort: Git is already right."""
    try:
        count = await asyncio.to_thread(_refresh, project)
        logger.info("Refreshed %d Applications of %s", count, project)
    except (KubeUnavailableError, ClusterNotConnectedError) as exc:
        logger.warning("Argo CD refresh of %s skipped: %s", project, exc)
