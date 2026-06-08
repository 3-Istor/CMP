import json
from io import StringIO
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from ruamel.yaml import YAML
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.schemas.deployment import DeploymentCreate, DeploymentRead
from app.services import terraform_orchestrator
from app.services.catalog_service import get_template_by_id
from app.services.github_service import (
    GitHubAppError,
    get_file_content,
    get_installation_token,
    update_file_content,
)

router = APIRouter(prefix="/deployments", tags=["Deployments"])

# ── ruamel.yaml instance (round-trip mode preserves comments & formatting) ──
_yaml = YAML()
_yaml.preserve_quotes = True

# Path of the GitOps configuration file inside every app repository
_CONFIG_FILE_PATH = "deploy/values.yaml"


@router.get("/", response_model=list[DeploymentRead])
async def list_deployments(db: Session = Depends(get_db)):
    """Return all non-deleted deployments for the dashboard."""
    return (
        db.query(Deployment)
        .filter(Deployment.status != DeploymentStatus.DELETED)
        .all()
    )


@router.post("/", response_model=DeploymentRead, status_code=202)
async def create_deployment(
    payload: DeploymentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Create a deployment record and kick off Terraform deployment in the background.
    Returns immediately with status=pending so the frontend can start polling.
    """
    # Validate template exists
    template = get_template_by_id(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    deployment = Deployment(
        name=payload.name,
        template_id=payload.template_id,
        template_name=template.name,
        template_icon=template.icon,
        template_category=template.category,
        provider_type=payload.provider_type,
        project_id=payload.project_id,
        app_config=json.dumps(payload.app_config),
        status=DeploymentStatus.PENDING,
        step_message="Queued...",
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    # Run Terraform deployment asynchronously
    # Note: Pass deployment ID, not the db session (session will be closed)
    background_tasks.add_task(
        terraform_orchestrator.run_deployment, deployment.id
    )

    return deployment


@router.get("/{deployment_id}", response_model=DeploymentRead)
async def get_deployment(deployment_id: int, db: Session = Depends(get_db)):
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@router.get("/{deployment_id}/outputs")
async def get_deployment_outputs(
    deployment_id: int, db: Session = Depends(get_db)
):
    """Return Terraform outputs for a deployment."""
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if not deployment.terraform_outputs:
        return {}

    try:
        return json.loads(deployment.terraform_outputs)
    except Exception:
        return {}


@router.delete("/{deployment_id}", status_code=202)
async def delete_deployment(
    deployment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger deletion of all Terraform-managed resources.
    Frontend must have already shown double-confirmation before calling this.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if deployment.status in (
        DeploymentStatus.DELETING,
        DeploymentStatus.DELETED,
    ):
        raise HTTPException(
            status_code=409, detail="Deployment is already being deleted"
        )

    background_tasks.add_task(
        terraform_orchestrator.run_deletion, deployment.id
    )
    return {"message": "Deletion started", "id": deployment_id}


# ── Day-2 Operations: GitOps write-back ─────────────────────────────────────


def _get_kubernetes_deployment_or_404(
    deployment_id: int, db: Session
) -> Deployment:
    """
    Fetch a RUNNING Kubernetes deployment, raising appropriate HTTP errors if
    it doesn't exist, is not Kubernetes-type, or has no GitHub repo linked.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if deployment.provider_type != ProviderType.KUBERNETES:
        raise HTTPException(
            status_code=400,
            detail="Day-2 config operations are only available for Kubernetes deployments.",
        )

    if not deployment.github_repo_url:
        raise HTTPException(
            status_code=409,
            detail=(
                "This deployment has no GitHub repository linked yet. "
                "The Terraform bootstrap may still be running."
            ),
        )

    return deployment


def _extract_repo_full_name(github_repo_url: str) -> str:
    """
    Extract ``owner/repo`` from a GitHub HTTPS URL.

    Examples:
      ``https://github.com/3-istor/my-app``  → ``"3-istor/my-app"``
      ``https://github.com/3-istor/my-app.git`` → ``"3-istor/my-app"``
    """
    # Strip trailing .git and take the last two path segments
    clean = github_repo_url.rstrip("/").removesuffix(".git")
    parts = clean.split("/")
    if len(parts) < 2:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot parse GitHub repo from URL: '{github_repo_url}'",
        )
    return "/".join(parts[-2:])


def _deep_merge(base: Any, updates: Any) -> Any:
    """
    Recursively merge *updates* into *base* (ruamel.yaml CommentedMap-aware).

    - Dict keys in *updates* override or extend *base*.
    - Non-dict values in *updates* replace the corresponding value in *base*.
    - Keys present in *base* but absent from *updates* are preserved.
    """
    if isinstance(updates, dict) and isinstance(base, dict):
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                _deep_merge(base[key], value)
            else:
                base[key] = value
        return base
    return updates


async def _get_github_token_for_deployment(deployment: Deployment) -> str:
    """
    Retrieve a fresh GitHub installation token for the given deployment.

    The installation ID is read from the deployment's ``app_config`` JSON.

    Raises:
        HTTPException 400 — if no installation ID is stored.
        HTTPException 502 — if GitHub token exchange fails.
    """
    app_config: dict = json.loads(deployment.app_config or "{}")
    installation_id: str | None = app_config.get("github_installation_id")

    if not installation_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No GitHub installation ID found for this deployment. "
                "The user must link their GitHub account first."
            ),
        )

    try:
        return await get_installation_token(installation_id)
    except GitHubAppError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to obtain GitHub token: {exc}",
        ) from exc


@router.get("/{deployment_id}/config")
async def get_deployment_config(
    deployment_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Fetch the current ``deploy/values.yaml`` from the application's GitHub
    repository and return it as a JSON object.

    This allows the frontend to pre-populate the Day-2 configuration form
    without the user needing to know the repository URL.

    Raises:
        404 — Deployment not found.
        400 — Not a Kubernetes deployment.
        409 — GitHub repo not linked yet.
        502 — GitHub API error.
    """
    deployment = _get_kubernetes_deployment_or_404(deployment_id, db)
    token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)

    try:
        raw_content, sha = await get_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=_CONFIG_FILE_PATH,
        )
    except GitHubAppError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    # Parse YAML → plain Python dict for JSON serialisation
    parsed = _yaml.load(raw_content)
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=500,
            detail=f"'{_CONFIG_FILE_PATH}' does not contain a YAML mapping.",
        )

    return {
        "repo": repo,
        "file_path": _CONFIG_FILE_PATH,
        # SHA must be echoed back by the frontend in PATCH requests
        "_sha": sha,
        "config": dict(parsed),
    }


@router.patch("/{deployment_id}/config")
async def update_deployment_config(
    deployment_id: int,
    # The request body must include the `_sha` field (obtained from GET /config)
    # plus any top-level or nested keys to update.
    payload: dict[str, Any] = Body(
        ...,
        openapi_examples={
            "toggle_database": {
                "summary": "Enable database",
                "value": {
                    "_sha": "abc123def456...",
                    "database": {"enabled": True, "name": "my-app-db"},
                },
            },
            "scale_replicas": {
                "summary": "Change replica count",
                "value": {"_sha": "abc123def456...", "replicaCount": 3},
            },
        },
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Partially update ``deploy/values.yaml`` in the application's GitHub
    repository and create a commit on ``main``.

    ArgoCD will automatically detect the new commit and sync the application.

    **Request body**: a JSON object containing:
    - ``_sha`` *(required)* — the file SHA returned by ``GET /config``.
      GitHub uses this to prevent concurrent write conflicts (409 Conflict).
    - Any other keys to merge into the YAML file (supports nested objects).

    **Merge strategy**: deep merge — existing keys not mentioned in the
    payload are preserved, including YAML comments.

    Raises:
        400 — Missing ``_sha``, not a Kubernetes deployment, or no payload keys.
        404 — Deployment not found.
        409 — SHA conflict (another commit was pushed in between) or repo not linked.
        502 — GitHub API error.
    """
    deployment = _get_kubernetes_deployment_or_404(deployment_id, db)

    # Validate _sha is present in the body
    sha: str | None = payload.pop("_sha", None)
    if not sha:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing required field '_sha'. "
                "Call GET /config first to obtain the current file SHA."
            ),
        )

    if not payload:
        raise HTTPException(
            status_code=400,
            detail="No configuration keys provided — nothing to update.",
        )

    token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)

    # ── 1. Fetch current file content ────────────────────────────────────
    try:
        raw_content, current_sha = await get_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=_CONFIG_FILE_PATH,
        )
    except GitHubAppError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    # Guard against stale SHA supplied by the client
    if current_sha != sha:
        raise HTTPException(
            status_code=409,
            detail=(
                "Conflict: the file has been modified since you last fetched it. "
                "Call GET /config again to obtain the latest SHA and retry."
            ),
        )

    # ── 2. Merge changes (ruamel preserves comments & ordering) ──────────
    parsed = _yaml.load(raw_content)
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=500,
            detail=f"'{_CONFIG_FILE_PATH}' does not contain a YAML mapping.",
        )

    _deep_merge(parsed, payload)

    # Serialise back to a YAML string
    out = StringIO()
    _yaml.dump(parsed, out)
    updated_content = out.getvalue()

    # ── 3. Commit to GitHub ───────────────────────────────────────────────
    # Build a descriptive commit message listing the top-level keys changed
    changed_keys = ", ".join(str(k) for k in payload)
    commit_message = (
        f"chore: update app configuration via CMP\n\n"
        f"Modified keys: {changed_keys}"
    )

    try:
        result = await update_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=_CONFIG_FILE_PATH,
            content=updated_content,
            message=commit_message,
            sha=sha,
        )
    except GitHubAppError as exc:
        status_code = 409 if "conflict" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    commit_sha: str = result.get("commit", {}).get("sha", "")

    return {
        "message": "Configuration updated successfully. ArgoCD will sync shortly.",
        "repo": repo,
        "file_path": _CONFIG_FILE_PATH,
        "commit_sha": commit_sha,
        "changed_keys": list(payload.keys()),
    }
