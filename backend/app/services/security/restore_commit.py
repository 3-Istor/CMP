"""Commit a patch to an app's database values file (restores, moves)."""

import difflib
from collections.abc import Callable
from io import StringIO

from fastapi import HTTPException

from app.models.deployment import Deployment
from app.routers.deployments import _deep_merge, _yaml
from app.routers.security import _github_token
from app.services import app_security_data as security_data
from app.services.github_service import (
    get_default_branch,
    get_file_content,
    put_file_content,
)


def message(label: str, actor: str) -> str:
    return f"chore(cmp): {label} ({actor})"


async def _read(deployment: Deployment) -> tuple:
    """(token, repo, branch, path, raw, sha, values) of the file with ``db``."""
    app_type = security_data.app_type_of(
        deployment.app_config, deployment.terraform_outputs
    )
    _, path = security_data.values_files_for(app_type)
    token, repo = await _github_token(deployment)
    branch = await get_default_branch(token, repo)
    raw, sha = await get_file_content(token, repo, path, branch)
    values = _yaml.load(raw)
    if not isinstance(values, dict) or not security_data.has_database(values):
        raise HTTPException(
            status_code=409, detail="This app has no database."
        )
    if not security_data.read_backup(values).enabled:
        raise HTTPException(
            status_code=409,
            detail="Les sauvegardes de cette base sont désactivées.",
        )
    return token, repo, branch, path, raw, sha, values


def _dump(values: dict) -> str:
    out = StringIO()
    _yaml.dump(values, out)
    return out.getvalue()


async def read_values(deployment: Deployment) -> dict:
    """The app's values with ``db``; 409 without a backed-up database."""
    return (await _read(deployment))[-1]


async def values_diff(
    deployment: Deployment, make_patch: Callable[[dict], dict]
) -> str:
    """The unified diff ``make_patch`` would commit."""
    *_, path, raw, _, values = await _read(deployment)
    _deep_merge(values, make_patch(values))
    return "".join(
        difflib.unified_diff(
            raw.splitlines(keepends=True),
            _dump(values).splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


async def commit_values_patch(
    deployment: Deployment,
    label: str,
    make_patch: Callable[[dict], dict],
    actor: str,
) -> str:
    """Merge ``make_patch(values)`` into the values file, commit, return the SHA."""
    token, repo, branch, path, _, sha, values = await _read(deployment)
    _deep_merge(values, make_patch(values))
    result = await put_file_content(
        token, repo, path, _dump(values), message(label, actor), sha, branch
    )
    return (result.get("commit") or {}).get("sha", "")
