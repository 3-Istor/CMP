import asyncio
import difflib
import json
from io import StringIO
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ruamel.yaml import YAML
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.models.repository_import import RepositoryImport
from app.routers.finops import is_cnp_admin
from app.schemas.deployment import DeploymentCreate, DeploymentRead
from app.services import app_security_data as security_data
from app.services import audit, terraform_orchestrator
from app.services.authz import assert_project_role
from app.services.catalog_service import get_template_by_id
from app.services.github_service import (
    GitHubAppError,
    get_file_content,
    get_installation_token,
    put_file_content,
)
from app.services.import_policy import (
    require_mutable_source,
    require_standard_platform_mode,
)
from app.services.keycloak_service import (
    fetch_user_projects_from_keycloak,
    get_current_user,
    get_project_role,
    get_user_id_from_token,
)
from app.services.project_registry import (
    NameCollisionError,
    RegistryError,
    ensure_app_names_free,
)

router = APIRouter(prefix="/deployments", tags=["Deployments"])
optional_bearer = HTTPBearer(auto_error=False)


def imported_portal_guard(db, deployment, credentials):
    from app.services import repository_imports

    job = (
        db.query(RepositoryImport)
        .filter_by(deployment_id=deployment.id)
        .one_or_none()
    )
    if job:
        if not credentials:
            raise HTTPException(401, "Authentifiez-vous dans le portail CNP")
        claims = repository_imports.verify_token(credentials.credentials)
        repository_imports.fresh_project(
            db, job.project_name, claims["sub"], claims["preferred_username"]
        )
    return job


# Type alias for the authenticated caller, so the endpoint signatures below
# stay readable.
CurrentUser = Annotated[dict, Depends(get_current_user)]

# ── ruamel.yaml instance (round-trip mode preserves comments & formatting) ──
_yaml = YAML()
_yaml.preserve_quotes = True
# The platform writes lists indented under their key; keep that on round trips.
_yaml.indent(mapping=2, sequence=4, offset=2)

_FULLSTACK_COMPONENTS = ["frontend", "backend"]


# ── Authorization helpers ───────────────────────────────────────────────────
#
# An application belongs to a project, so every permission question here is
# really a question about that project. ``Deployment.project_id`` holds the
# project *name* (there is no foreign key), which is exactly what
# ``assert_project_role`` takes.


def _user_project_names(db: Session, token: dict) -> list[str]:
    """
    Every project the caller can see: their Keycloak group memberships plus
    anything they own.

    Owned projects are included separately because ownership lives in the
    database, not in a group — the same reason ``GET /api/projects/`` merges
    the two.
    """
    user_id = get_user_id_from_token(token)
    username = token.get("preferred_username") or user_id

    names = {p["name"] for p in fetch_user_projects_from_keycloak(user_id)}

    if username:
        names.update(
            row.project_name
            for row in db.query(Project)
            .filter(Project.owner_username == username)
            .all()
        )

    return sorted(names)


def _authorize_app(
    db: Session,
    token: dict,
    deployment: Deployment,
    minimum: ProjectRole,
) -> None:
    """
    Raise unless the caller holds *minimum* on the application's project.

    A deployment with no ``project_id`` cannot be attributed to anyone, so it
    is refused rather than left open: "only the projects you are assigned to"
    has no sensible answer for an app that belongs to no project. Legacy rows
    in that state need a project_id backfilled before they are reachable
    again. Platform admins pass regardless.
    """
    if is_cnp_admin(token):
        return
    if not deployment.project_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "This application is not attached to a project, so access "
                "cannot be determined."
            ),
        )
    assert_project_role(db, token, deployment.project_id, minimum)


def _get_app_or_404(
    db: Session, token: dict, deployment_id: int, minimum: ProjectRole
) -> Deployment:
    """Fetch a deployment the caller is allowed to touch, or raise."""
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    _authorize_app(db, token, deployment, minimum)
    return deployment


@router.get("/", response_model=list[DeploymentRead])
def list_deployments(
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """
    Return the caller's non-deleted deployments.

    Scoped to the projects the caller belongs to — including as a guest, who
    is allowed to see which applications exist. Platform admins see them all.
    """
    query = db.query(Deployment).filter(
        Deployment.status != DeploymentStatus.DELETED
    )
    if not is_cnp_admin(token):
        query = query.filter(
            Deployment.project_id.in_(_user_project_names(db, token))
        )
    return query.all()


@router.post("/", response_model=DeploymentRead, status_code=202)
async def create_deployment(
    payload: DeploymentCreate,
    background_tasks: BackgroundTasks,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """
    Create a deployment record and kick off Terraform deployment in the background.
    Returns immediately with status=pending so the frontend can start polling.

    Access control: admin or owner of the target project. The project comes
    from the request body, so it is checked here rather than through a path
    dependency.
    """
    require_standard_platform_mode()
    if not payload.project_id:
        raise HTTPException(
            status_code=400,
            detail="project_id is required: an application must belong to a project.",
        )
    if not is_cnp_admin(token):
        # Blocking Keycloak call: off the event loop.
        await run_in_threadpool(
            assert_project_role,
            db,
            token,
            payload.project_id,
            ProjectRole.ADMIN,
        )

    # Imported repositories can only enter through the validated portal protocol.
    if (
        payload.app_config.get("repository_mode", "create") != "create"
        or "existing_repository_url" in payload.app_config
    ):
        raise HTTPException(
            403, "Use the repository import flow for an existing repository"
        )
    if db.query(RepositoryImport).filter_by(app_name=payload.name).first():
        raise HTTPException(409, "Ce nom est réservé par un parcours d’import")
    # Validate template exists
    template = get_template_by_id(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if payload.template_id == "k3s-gitops-app":
        # Terraform deploys into app_config.project_name, not project_id: the
        # two must agree or an admin of one project could deploy into another.
        project_name = payload.app_config.get("project_name")
        if project_name and project_name != payload.project_id:
            raise HTTPException(
                status_code=400,
                detail="app_config.project_name must match project_id.",
            )
        if payload.project_id:
            payload.app_config["project_name"] = project_name = (
                payload.project_id
            )
        # Forced from the DB: the Vault role mount depends on it, and a project
        # admin must not pick another cloud's mount.
        project_row = (
            db.query(Project)
            .filter(Project.project_name == project_name)
            .first()
        )
        payload.app_config["target_cloud"] = (
            project_row.target_cloud if project_row else TargetCloud.ONPREM
        ).value
        if project_name:
            try:
                await ensure_app_names_free(
                    project_name,
                    payload.name,
                    payload.app_config.get("app_type", "static"),
                )
            except NameCollisionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except RegistryError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not check the project registry: {exc}",
                ) from exc

    deployment = Deployment(
        name=payload.name,
        template_id=payload.template_id,
        template_name=template.name,
        template_icon=template.icon,
        template_category=template.category,
        # The portal never sends provider_type: the template decides.
        provider_type=(
            ProviderType.KUBERNETES
            if payload.template_id == "k3s-gitops-app"
            else payload.provider_type
        ),
        project_id=payload.project_id,
        app_config=json.dumps(payload.app_config),
        status=DeploymentStatus.PENDING,
        step_message="Queued...",
        created_via=(token.get("cnp_agent") or {}).get("client"),
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
async def get_deployment(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """Access control: any role on the application's project."""
    return _get_app_or_404(db, token, deployment_id, ProjectRole.GUEST)


@router.get("/{deployment_id}/outputs")
async def get_deployment_outputs(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """
    Return Terraform outputs for a deployment.

    Access control: any role. These are the app's public endpoints — the URLs
    and IPs already shown on its card.
    """
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.GUEST)

    if not deployment.terraform_outputs:
        return {}

    try:
        return json.loads(deployment.terraform_outputs)
    except Exception:
        return {}


def _still_running(deployment_id: int) -> bool:
    # A fresh session: the request's own one may be closed by now.
    from app.core.database import SessionLocal

    with SessionLocal() as check_db:
        dep = check_db.get(Deployment, deployment_id)
        return bool(dep) and dep.status in (
            DeploymentStatus.PENDING,
            DeploymentStatus.INITIALIZING,
            DeploymentStatus.PLANNING,
            DeploymentStatus.DEPLOYING,
            DeploymentStatus.DELETING,
        )


@router.get("/{deployment_id}/logs/stream")
async def stream_deployment_logs(
    deployment_id: int,
    request: Request,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """
    Stream Terraform execution logs for a deployment in real-time.

    Access control: member or above. Build output can carry infrastructure
    detail, so guests do not get it.
    """
    _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)

    log_file = Path("logs/deployments") / f"{deployment_id}.log"

    async def log_generator():
        # Ensure log directory and file exists
        if not log_file.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.touch()

        # Open and read log file incrementally
        with open(log_file, "r", encoding="utf-8") as f:
            while not await request.is_disconnected():
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                    continue
                if not await run_in_threadpool(_still_running, deployment_id):
                    # Read any remaining output lines that were written since last read
                    line = f.readline()
                    while line:
                        yield f"data: {line.rstrip()}\n\n"
                        line = f.readline()
                    break
                await asyncio.sleep(0.5)

    return StreamingResponse(log_generator(), media_type="text/event-stream")


@router.delete("/{deployment_id}", status_code=202)
async def delete_deployment(
    deployment_id: int,
    background_tasks: BackgroundTasks,
    token: CurrentUser,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(
        optional_bearer
    ),
):
    """
    Trigger deletion of all Terraform-managed resources.

    Access control: admin or owner of the application's project.
    Frontend must have already shown double-confirmation before calling this.
    """
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.ADMIN)
    imported_portal_guard(db, deployment, credentials)
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
    deployment_id: int,
    db: Session,
    token: dict,
    minimum: ProjectRole,
) -> Deployment:
    """
    Fetch a Kubernetes deployment the caller may touch, raising
    appropriate HTTP errors if it doesn't exist, they lack the role, it is not
    Kubernetes-type, or it has no GitHub repo linked.
    """
    deployment = _get_app_or_404(db, token, deployment_id, minimum)

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
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
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


def _config_file_for(app_type: str, component: str | None) -> str:
    if app_type != "fullstack":
        return security_data.SINGLE_VALUES_FILE
    if component in (None, "frontend"):
        return security_data.FRONTEND_VALUES_FILE
    if component == "backend":
        return security_data.BACKEND_VALUES_FILE
    raise HTTPException(
        status_code=400,
        detail=f"Unknown component '{component}', expected one of "
        f"{', '.join(_FULLSTACK_COMPONENTS)}.",
    )


@router.get("/{deployment_id}/config")
async def get_deployment_config(
    deployment_id: int,
    token: CurrentUser,
    component: str | None = Query(default=None),
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(
        optional_bearer
    ),
) -> dict[str, Any]:
    """
    Fetch the app's values file from its GitHub repository and return it as
    a JSON object. Fullstack apps have one file per component, picked with
    ``component`` (``frontend`` by default).

    This allows the frontend to pre-populate the Day-2 configuration form
    without the user needing to know the repository URL.

    Access control: member or above — members read the configuration, only
    admins may change it (see PATCH below).

    Raises:
        404 — Deployment not found.
        403 — Caller has no access to the application's project.
        400 — Not a Kubernetes deployment.
        409 — GitHub repo not linked yet.
        502 — GitHub API error.
    """
    deployment = _get_kubernetes_deployment_or_404(
        deployment_id, db, token, ProjectRole.MEMBER
    )
    app_type = _app_type_of(deployment)
    file_path = _config_file_for(app_type, component)
    job = imported_portal_guard(db, deployment, credentials)
    if job:
        from app.services.repository_imports import scoped_token

        gh_token = await scoped_token(job)
    else:
        gh_token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)

    try:
        raw_content, sha = await get_file_content(
            installation_token=gh_token,
            repo_full_name=repo,
            file_path=file_path,
        )
    except GitHubAppError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    # Parse YAML → plain Python dict for JSON serialisation
    parsed = _yaml.load(raw_content)
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=500,
            detail=f"'{file_path}' does not contain a YAML mapping.",
        )

    return {
        "repo": repo,
        "file_path": file_path,
        "components": _FULLSTACK_COMPONENTS if app_type == "fullstack" else [],
        # SHA must be echoed back by the frontend in PATCH requests
        "_sha": sha,
        "config": dict(parsed),
    }


@router.patch("/{deployment_id}/config")
async def update_deployment_config(
    deployment_id: int,
    request: Request,
    token: CurrentUser,
    component: str | None = Query(default=None),
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
    credentials: HTTPAuthorizationCredentials | None = Depends(
        optional_bearer
    ),
) -> dict[str, Any]:
    """
    Partially update the app's values file (same ``component`` rule as
    ``GET /config``) in its GitHub repository and create a commit on ``main``.

    ArgoCD will automatically detect the new commit and sync the application.

    **Request body**: a JSON object containing:
    - ``_sha`` *(required)* — the file SHA returned by ``GET /config``.
      GitHub uses this to prevent concurrent write conflicts (409 Conflict).
    - Any other keys to merge into the YAML file (supports nested objects).

    **Merge strategy**: deep merge — existing keys not mentioned in the
    payload are preserved, including YAML comments.

    Access control: admin or owner of the application's project. This commits
    to the repository and ArgoCD then changes what is running, so it is a
    write in every sense.

    Raises:
        400 — Missing ``_sha``, not a Kubernetes deployment, or no payload keys.
        403 — Caller is not an admin of the application's project.
        404 — Deployment not found.
        409 — SHA conflict (another commit was pushed in between) or repo not linked.
        502 — GitHub API error.
    """
    deployment = _get_kubernetes_deployment_or_404(
        deployment_id, db, token, ProjectRole.ADMIN
    )
    file_path = _config_file_for(_app_type_of(deployment), component)
    if imported_portal_guard(db, deployment, credentials):
        require_mutable_source(db, deployment)

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

    # Named gh_token so it cannot shadow the caller's JWT above.
    gh_token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)

    # ── 1. Fetch current file content ────────────────────────────────────
    try:
        raw_content, current_sha = await get_file_content(
            installation_token=gh_token,
            repo_full_name=repo,
            file_path=file_path,
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
            detail=f"'{file_path}' does not contain a YAML mapping.",
        )

    audit.note(request, file=file_path, changes=audit.changes(parsed, payload))
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
        result = await put_file_content(
            installation_token=gh_token,
            repo_full_name=repo,
            file_path=file_path,
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
        "file_path": file_path,
        "commit_sha": commit_sha,
        "changed_keys": list(payload.keys()),
    }


# ── Exposure and database backups ────────────────────────────────────────────


def _app_type_of(deployment: Deployment) -> str:
    return security_data.app_type_of(
        deployment.app_config, deployment.terraform_outputs
    )


async def _load_values_file(
    token: str, repo: str, file_path: str
) -> tuple[Any, str]:
    try:
        raw_content, sha = await get_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=file_path,
        )
    except GitHubAppError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    parsed = _yaml.load(raw_content)
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=500,
            detail=f"'{file_path}' does not contain a YAML mapping.",
        )
    return parsed, sha


@router.get(
    "/{deployment_id}/security-data",
    response_model=security_data.SecurityDataRead,
)
async def get_security_data(
    deployment_id: int,
    token_payload: CurrentUser,
    db: Session = Depends(get_db),
) -> security_data.SecurityDataRead:
    deployment = _get_kubernetes_deployment_or_404(
        deployment_id, db, token_payload, ProjectRole.MEMBER
    )
    can_edit = is_cnp_admin(token_payload) or (
        get_project_role(token_payload.get("sub", ""), deployment.project_id)
        == "admin"
    )

    token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)
    exposure_file, database_file = security_data.values_files_for(
        _app_type_of(deployment)
    )

    exposure_values, _ = await _load_values_file(token, repo, exposure_file)
    database_values = exposure_values
    if database_file != exposure_file:
        database_values, _ = await _load_values_file(
            token, repo, database_file
        )

    return security_data.SecurityDataRead(
        repo=repo,
        can_edit=can_edit,
        exposure=(
            security_data.read_exposure(exposure_values)
            if security_data.has_ingress(exposure_values)
            else None
        ),
        database=(
            security_data.DatabaseState(
                backup=security_data.read_backup(database_values)
            )
            if security_data.has_database(database_values)
            else None
        ),
    )


@router.put("/{deployment_id}/security-data")
async def update_security_data(
    deployment_id: int,
    payload: security_data.SecurityDataUpdate,
    request: Request,
    token_payload: CurrentUser,
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Write the exposure preset and/or the database backup settings into the
    app repository's values files. Project admins only; only the keys of the
    panel are ever written. With ``dry_run``, return the diff and commit
    message of each file instead, for the confirmation dialog.
    """
    deployment = _get_kubernetes_deployment_or_404(
        deployment_id, db, token_payload, ProjectRole.ADMIN
    )

    require_mutable_source(db, deployment)
    token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)
    exposure_file, database_file = security_data.values_files_for(
        _app_type_of(deployment)
    )

    patches: dict[str, dict] = {}
    labels: dict[str, list[str]] = {}
    loaded: dict[str, tuple[Any, str]] = {}

    async def load(file_path: str) -> Any:
        if file_path not in loaded:
            loaded[file_path] = await _load_values_file(token, repo, file_path)
        return loaded[file_path][0]

    if payload.exposure is not None:
        audit.note(
            request,
            exposure_before=security_data.read_exposure(
                await load(exposure_file)
            ),
        )
        if not security_data.has_ingress(await load(exposure_file)):
            raise HTTPException(
                status_code=400,
                detail="This application has no ingress to configure.",
            )
        patches[exposure_file] = security_data.exposure_patch(payload.exposure)
        labels[exposure_file] = [
            security_data.exposure_commit_label(payload.exposure)
        ]

    backup_labels = (
        security_data.backup_commit_labels(payload.backup)
        if payload.backup
        else []
    )
    if backup_labels:
        if not security_data.has_database(await load(database_file)):
            raise HTTPException(
                status_code=400,
                detail="This application has no database.",
            )
        _deep_merge(
            patches.setdefault(database_file, {}),
            security_data.backup_patch(payload.backup),
        )
        labels.setdefault(database_file, []).extend(backup_labels)

    commits: dict[str, str] = {}
    previews: dict[str, dict[str, str]] = {}
    for file_path, patch in patches.items():
        parsed, sha = loaded[file_path]
        before = StringIO()
        _yaml.dump(parsed, before)
        _deep_merge(parsed, patch)
        out = StringIO()
        _yaml.dump(parsed, out)
        message = f"chore(cmp): {', '.join(labels[file_path])}"
        if dry_run:
            previews[file_path] = {
                "message": message,
                "diff": "".join(
                    difflib.unified_diff(
                        before.getvalue().splitlines(keepends=True),
                        out.getvalue().splitlines(keepends=True),
                        fromfile=f"a/{file_path}",
                        tofile=f"b/{file_path}",
                    )
                ),
            }
            continue
        try:
            result = await put_file_content(
                installation_token=token,
                repo_full_name=repo,
                file_path=file_path,
                content=out.getvalue(),
                message=message,
                sha=sha,
            )
        except GitHubAppError as exc:
            status_code = 409 if "conflict" in str(exc).lower() else 502
            raise HTTPException(
                status_code=status_code, detail=str(exc)
            ) from exc
        commits[file_path] = result.get("commit", {}).get("sha", "")

    if dry_run:
        return {"repo": repo, "previews": previews}
    return {"repo": repo, "commits": commits}
