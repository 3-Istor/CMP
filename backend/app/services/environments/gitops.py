"""Reading and committing an app's values files for its environments."""

import json
import re
from io import StringIO
from typing import Any

from ruamel.yaml import YAML

from app.models.deployment import Deployment
from app.services import app_repo
from app.services import app_security_data as security_data
from app.services.environments import layout
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    StaleBranchError,
    commit_files,
    get_branch_head,
    get_file_content,
    get_org_installation_token,
)

COMMIT_ATTEMPTS = 3
# Same rule as ReleaseSettings.smoke_path: the repo can be edited outside the
# CMP, and a path such as "@host:port/" would change the smoke GET's target.
_SMOKE_PATH = re.compile(r"^/\S*$")

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
# A pinned tag (1.4.0-rc.2@sha256:...) is longer than the default 80 columns;
# folded, it stays valid YAML but unreadable in a diff.
_yaml.width = 4096


class GitOpsError(Exception):
    pass


def app_type(deployment: Deployment) -> str:
    return security_data.app_type_of(
        deployment.app_config, deployment.terraform_outputs
    )


def target_cloud(deployment: Deployment) -> str:
    return (
        json.loads(deployment.app_config or "{}").get("target_cloud")
        or "onprem"
    )


def repo_of(deployment: Deployment) -> str:
    try:
        return app_repo.bound_repo(
            deployment.github_repo_url, deployment.name, deployment.status
        )
    except app_repo.RepoNotBound as exc:
        raise GitOpsError(str(exc)) from exc


async def token_for(_deployment: Deployment) -> str:
    try:
        return await get_org_installation_token()
    except GitHubAppError as exc:
        raise GitOpsError(f"GitHub token: {exc}") from exc


def load(text: str) -> Any:
    parsed = _yaml.load(text)
    return parsed if isinstance(parsed, dict) else {}


def dump(values: Any) -> str:
    out = StringIO()
    _yaml.dump(values, out)
    return out.getvalue()


async def read_values(
    token: str, repo: str, path: str, ref: str
) -> Any | None:
    """A values file at *ref*, None when it does not exist."""
    try:
        text, _ = await get_file_content(token, repo, path, ref=ref)
    except FileNotInRepoError:
        return None
    return load(text)


async def read_base(
    token: str, repo: str, kind: str, ref: str = "main"
) -> dict[str, Any]:
    """component -> parsed shared values file."""
    found = {}
    for component in layout.components(kind):
        values = await read_values(
            token, repo, layout.base_values_file(kind, component), ref
        )
        if values is None:
            raise GitOpsError(
                f"{layout.base_values_file(kind, component)} is missing"
            )
        found[component] = values
    return found


def smoke_path(values: dict[str, Any]) -> str:
    release = values.get("release") or {}
    path = (release.get("smoke") or {}).get("path") or (
        values.get("monitoring") or {}
    ).get("path")
    if isinstance(path, str) and _SMOKE_PATH.match(path) and len(path) <= 200:
        return path
    return "/"


async def commit(token: str, repo: str, build, message: str) -> str:
    """
    Commit what *build(head)* returns ({path: content or None}) on top of the
    current head, rebuilding it when the branch moved meanwhile (the image
    updater writes to the same branch).
    """
    for attempt in range(COMMIT_ATTEMPTS):
        head = await get_branch_head(token, repo)
        files = await build(head)
        if not files:
            return head
        try:
            return await commit_files(
                token, repo, files, message, parent_sha=head
            )
        except StaleBranchError:
            if attempt == COMMIT_ATTEMPTS - 1:
                raise GitOpsError(
                    "main kept moving while committing: try again in a moment."
                ) from None
        except GitHubAppError as exc:
            raise GitOpsError(str(exc)) from exc
    raise AssertionError("unreachable")
