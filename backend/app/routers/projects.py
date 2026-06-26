"""
Projects Router

Implements the Project > Application hierarchy.

Endpoints:
  GET  /api/projects                       - List projects the current user belongs to
  POST /api/projects                       - Bootstrap a new project via Terraform
  GET  /api/projects/{project_name}/apps   - List applications in a project
"""

import logging
from typing import Annotated

import requests
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import ProjectOwner
from app.schemas.deployment import DeploymentRead
from app.schemas.project import (
    ProjectCreate,
    ProjectCreateResponse,
    ProjectRead,
)
from app.services.keycloak_service import (
    add_user_to_project,
    fetch_user_projects_from_keycloak,
    get_current_user,
    has_project_access,
    list_project_members,
    remove_user_from_project,
    verify_project_access,
)
from app.services.project_bootstrap import (
    run_project_bootstrap,
    run_project_teardown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])

# Temporary storage for project creators (until they're added to Keycloak groups)
# Format: {project_name: user_id}
_project_creators: dict[str, str] = {}


# ---------------------------------------------------------------------------
# GET /api/projects/users/search  — Keycloak user search for member autocomplete
# ---------------------------------------------------------------------------


@router.get("/users/search")
async def search_keycloak_users(
    q: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
) -> list[dict]:
    """
    Search Keycloak users by username or email (prefix search).

    Used by the Members panel autocomplete to find users to add to a project.
    Returns at most 10 results.

    Query params:
        q: Search string (minimum 2 chars).

    Returns:
        List of ``{"username": str, "email": str, "first_name": str, "last_name": str}``
    """
    if len(q.strip()) < 2:
        return []

    try:
        from app.services.keycloak_service import _get_admin_token

        admin_token = _get_admin_token()
        url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users"

        # Keycloak search matches username, email, firstName, lastName
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"search": q.strip(), "max": 10},
            timeout=10,
        )
        response.raise_for_status()
        users = response.json()

        return [
            {
                "username": u.get("username", ""),
                "email": u.get("email", ""),
                "first_name": u.get("firstName", ""),
                "last_name": u.get("lastName", ""),
            }
            for u in users
            if u.get("username")
        ]

    except Exception as exc:
        logger.warning("User search failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Helper function to get user_id from token
# ---------------------------------------------------------------------------


def get_user_id_from_token(token_payload: dict) -> str:
    """
    Extract user_id from token, with fallback to username lookup if sub is missing.

    Args:
        token_payload: Decoded JWT token payload

    Returns:
        str: User ID (UUID)

    Raises:
        HTTPException: If user cannot be determined
    """
    user_id = token_payload.get("sub", "")
    username = token_payload.get("preferred_username", "")

    # If sub is empty but we have username, lookup user_id from Keycloak
    if not user_id and username:
        logger.warning(f"⚠️  Token missing 'sub' claim, looking up user_id from username '{username}'")
        try:
            from app.services.keycloak_service import (
                _find_user_by_username,
                _get_admin_token,
            )
            admin_token = _get_admin_token()
            user = _find_user_by_username(username, admin_token)
            if user:
                user_id = user["id"]
                logger.info(f"✅ Found user_id '{user_id}' for username '{username}'")
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User '{username}' not found in Keycloak",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to lookup user: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to lookup user: {e}",
            )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not determine user identity from token.",
        )

    return user_id


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ProjectRead])
async def list_projects(
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> list[ProjectRead]:
    """
    Return the list of projects the authenticated user belongs to.

    Projects are derived exclusively from Keycloak group membership:
      ``project-<name>-admins`` or ``project-<name>-members``

    The JWT is used only for authentication; group data is fetched fresh
    from the Keycloak Admin API to avoid stale JWT claims.

    Projects the user owns (created) are returned with role ``"owner"``.
    """
    username = token_payload.get("preferred_username", "")
    logger.info(f"🔍 Fetching projects for username='{username}'")

    user_id = get_user_id_from_token(token_payload)
    logger.info(f"🔍 Using user_id='{user_id}'")

    projects = fetch_user_projects_from_keycloak(user_id)

    logger.info(f"📋 Found {len(projects)} projects for user: {[p['name'] for p in projects]}")

    # Promote owned projects to role "owner"
    if username:
        owned = {
            o.project_name
            for o in db.query(ProjectOwner)
            .filter(ProjectOwner.owner_username == username)
            .all()
        }
        for p in projects:
            if p["name"] in owned:
                p["role"] = "owner"

    return [ProjectRead(**p) for p in projects]


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------


@router.post("/", response_model=ProjectCreateResponse, status_code=202)
async def create_project(
    payload: ProjectCreate,
    background_tasks: BackgroundTasks,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> ProjectCreateResponse:
    """
    Bootstrap a new project by executing the ``k3s-project-bootstrap`` Terraform module.

    This is an async operation — Terraform runs in a background task.
    The module creates:
    - Keycloak groups: ``project-<name>-admins`` / ``project-<name>-members``
    - Vault policies scoped to the project
    - ArgoCD AppProject

    Required settings (from .env):
    - KEYCLOAK_URL, KEYCLOAK_ADMIN_USERNAME, KEYCLOAK_ADMIN_PASSWORD
    - VAULT_URL, VAULT_TOKEN
    - GITHUB_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY
    - DISCORD_WEBHOOK_URL
    """
    # Validate required settings are present before accepting the request
    missing = []
    if not settings.KEYCLOAK_URL:
        missing.append("KEYCLOAK_URL")
    if not settings.KEYCLOAK_ADMIN_USERNAME:
        missing.append("KEYCLOAK_ADMIN_USERNAME")
    if not settings.KEYCLOAK_ADMIN_PASSWORD:
        missing.append("KEYCLOAK_ADMIN_PASSWORD")
    if not settings.VAULT_URL:
        missing.append("VAULT_URL")
    if not settings.VAULT_TOKEN:
        missing.append("VAULT_TOKEN")
    if not settings.GITHUB_INSTALLATION_ID:
        missing.append("GITHUB_INSTALLATION_ID")
    if not settings.GITHUB_APP_PRIVATE_KEY:
        missing.append("GITHUB_APP_PRIVATE_KEY")
    if not settings.DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")

    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Server configuration incomplete. Missing: {', '.join(missing)}",
        )

    # Get user info
    user_id = get_user_id_from_token(token_payload)
    username = token_payload.get("preferred_username") or user_id

    # Store creator temporarily (will be removed once added to Keycloak group)
    _project_creators[payload.project_name] = user_id
    logger.info(f"🔐 Stored creator user_id='{user_id}' for project '{payload.project_name}'")

    # Persist the project owner (creator) — immutable, can never be removed.
    if not db.query(ProjectOwner).filter(
        ProjectOwner.project_name == payload.project_name
    ).first():
        db.add(
            ProjectOwner(
                project_name=payload.project_name,
                owner_username=username,
            )
        )
        db.commit()
        logger.info(
            "👑 Recorded '%s' as owner of project '%s'",
            username,
            payload.project_name,
        )

    # Trigger bootstrap
    background_tasks.add_task(
        run_project_bootstrap,
        project_name=payload.project_name,
    )

    # Add creator to project as admin (after a short delay to let Terraform finish)
    async def add_creator_to_project():
        """Add the project creator as admin after bootstrap completes."""
        import asyncio

        # Wait for Terraform to create the groups
        await asyncio.sleep(8)  # Reduced from 10s but not too short
        try:
            add_user_to_project(username, payload.project_name, "admin")
            logger.info(
                "✅ Added user '%s' as admin of project '%s'",
                username,
                payload.project_name,
            )
            # Remove from temporary storage once successfully added
            _project_creators.pop(payload.project_name, None)
        except Exception as e:
            logger.error(
                "❌ Failed to add creator to project '%s': %s",
                payload.project_name,
                e,
            )

    background_tasks.add_task(add_creator_to_project)

    logger.info(
        "Project bootstrap triggered for '%s' by user '%s'",
        payload.project_name,
        username,
    )

    return ProjectCreateResponse(
        message=(
            f"Project '{payload.project_name}' bootstrap started. "
            "Keycloak groups, Vault policies, and ArgoCD AppProject will be created shortly. "
            f"You will be added as project admin."
        ),
        project_name=payload.project_name,
    )


# ---------------------------------------------------------------------------
# GET /api/projects/{project_name}/apps
# ---------------------------------------------------------------------------


@router.get("/{project_name}/apps", response_model=list[DeploymentRead])
async def list_project_apps(
    project_name: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> list[DeploymentRead]:
    """
    Return all applications (deployments) belonging to *project_name*.

    Access control: the authenticated user must be a member or admin of the
    project (verified via real-time Keycloak API query).
    """
    user_id = get_user_id_from_token(token_payload)

    logger.info(f"🔍 Checking access for user_id='{user_id}' to project '{project_name}'")

    # Quick check: is this user the creator (temporary during bootstrap)?
    if _project_creators.get(project_name) == user_id:
        logger.info(f"✅ User '{user_id}' is creator of project '{project_name}' (bootstrap in progress)")
        # Allow access immediately for creator
        apps = (
            db.query(Deployment)
            .filter(
                Deployment.project_id == project_name,
                Deployment.status != DeploymentStatus.DELETED,
            )
            .all()
        )
        return apps
    else:
        creator_id = _project_creators.get(project_name)
        if creator_id:
            logger.warning(f"⚠️  Creator mismatch: stored='{creator_id}', current='{user_id}'")
        else:
            logger.info(f"ℹ️  No creator stored for project '{project_name}', checking Keycloak groups")

    # Check if user has access to this project via Keycloak groups
    try:
        from app.services.keycloak_service import (
            _check_user_in_group_realtime,
            _find_group_by_name,
            _get_admin_token,
        )

        admin_token = _get_admin_token()
        has_access = False

        for suffix in ("admins", "members"):
            group_name = f"project-{project_name}-{suffix}"
            group = _find_group_by_name(group_name, admin_token)
            if group and _check_user_in_group_realtime(user_id, group["id"], admin_token):
                has_access = True
                break

        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: you are not a member of project '{project_name}'.",
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to verify access for project '%s': %s", project_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to verify project access: {exc}",
        ) from exc

    apps = (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project_name,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
    )

    return apps


# ---------------------------------------------------------------------------
# Member Management (Keycloak RBAC)
# ---------------------------------------------------------------------------


@router.get("/{project_name}/members")
async def get_project_members(
    project_name: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """
    List all members of a project (both admins and members).

    Access control: requires membership in the project (admin or member).

    Returns:
        dict: ``{"project_name": "...", "members": [...]}``
    """
    user_id = get_user_id_from_token(token_payload)

    # Quick check: is this user the creator (temporary during bootstrap)?
    is_creator = _project_creators.get(project_name) == user_id

    # Check if user has access to this project
    try:
        from app.services.keycloak_service import (
            _check_user_in_group_realtime,
            _find_group_by_name,
            _get_admin_token,
        )

        admin_token = _get_admin_token()
        has_access = is_creator  # Creator always has access

        if not has_access:
            for suffix in ("admins", "members"):
                group_name = f"project-{project_name}-{suffix}"
                group = _find_group_by_name(group_name, admin_token)
                if group and _check_user_in_group_realtime(user_id, group["id"], admin_token):
                    has_access = True
                    break

        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: you are not a member of project '{project_name}'.",
            )

        members = list_project_members(project_name)

        # Mark the owner (creator) — they always rank above admin and cannot
        # be removed from the project.
        owner = (
            db.query(ProjectOwner)
            .filter(ProjectOwner.project_name == project_name)
            .first()
        )
        if owner:
            for member in members:
                if member["username"] == owner.owner_username:
                    member["role"] = "owner"

        return {"project_name": project_name, "members": members}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to list members for project '%s': %s", project_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch project members: {exc}",
        ) from exc


@router.post("/{project_name}/members", status_code=201)
async def add_project_member(
    project_name: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
    username: Annotated[str, Body(..., embed=True)],
    role: Annotated[str, Body(..., embed=True)] = "member",
) -> dict:
    """
    Add a user to a project Keycloak group.

    Access control: requires admin role in the project.
    Optimised: admin check and user lookup run in parallel.
    """
    user_id = get_user_id_from_token(token_payload)

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from app.services.keycloak_service import (
            _check_user_in_group_realtime,
            _find_group_by_name,
            _find_user_by_username,
            _get_admin_token,
        )

        admin_token = _get_admin_token()

        # ── Run admin-group lookup AND user lookup in parallel ────────────
        with ThreadPoolExecutor(max_workers=2) as pool:
            admin_group_future = pool.submit(
                _find_group_by_name,
                f"project-{project_name}-admins",
                admin_token,
            )
            user_future = pool.submit(
                _find_user_by_username, username, admin_token
            )
            admin_group = admin_group_future.result()
            target_user = user_future.result()

        if not admin_group or not _check_user_in_group_realtime(
            user_id, admin_group["id"], admin_token
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: admin role required for project '{project_name}'.",
            )

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User '{username}' not found in Keycloak.",
            )

        # Find target group and add user in parallel
        group_suffix = "admins" if role == "admin" else "members"
        target_group = _find_group_by_name(
            f"project-{project_name}-{group_suffix}", admin_token
        )

        if not target_group:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Group 'project-{project_name}-{group_suffix}' not found. "
                    "Ensure the project has been bootstrapped."
                ),
            )

        # Add user to group
        import requests as _requests
        resp = _requests.put(
            f"{settings.KEYCLOAK_URL}/admin/realms/3istor"
            f"/users/{target_user['id']}/groups/{target_group['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        resp.raise_for_status()

        return {
            "message": f"User '{username}' added to project '{project_name}' with role '{role}'.",
            "project_name": project_name,
            "username": username,
            "role": role,
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to add user '%s' to project '%s': %s", username, project_name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to add user to project: {exc}",
        ) from exc


@router.delete("/{project_name}/members/{username}", status_code=204)
async def remove_project_member(
    project_name: str,
    username: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> None:
    """
    Remove a user from a project (both admin and member groups).

    Access control: requires admin role in the project.

    Raises:
        400: User not found, or target is the project owner.
        403: Caller is not a project admin.
        502: Keycloak API error.
    """
    user_id = get_user_id_from_token(token_payload)

    # The owner (creator) can never be removed from their project.
    owner = (
        db.query(ProjectOwner)
        .filter(ProjectOwner.project_name == project_name)
        .first()
    )
    if owner and owner.owner_username == username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{username}' is the project owner and cannot be removed.",
        )

    try:
        from app.services.keycloak_service import (
            _check_user_in_group_realtime,
            _find_group_by_name,
            _get_admin_token,
        )

        admin_token = _get_admin_token()
        admin_group_name = f"project-{project_name}-admins"
        admin_group = _find_group_by_name(admin_group_name, admin_token)

        if not admin_group or not _check_user_in_group_realtime(
            user_id, admin_group["id"], admin_token
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: admin role required for project '{project_name}'.",
            )

        remove_user_from_project(username, project_name)

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error(
            "Failed to remove user '%s' from project '%s': %s",
            username,
            project_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to remove user from project: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# DELETE Project
# ---------------------------------------------------------------------------


@router.delete("/{project_name}", status_code=202)
async def delete_project(
    project_name: str,
    background_tasks: BackgroundTasks,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> None:
    """
    Delete a project and tear down its Day-0 infrastructure.

    Keycloak groups and the ownership record are removed synchronously so the
    project disappears from listings immediately; the remaining infrastructure
    (Vault policy, ArgoCD AppProject, GitHub resources and the per-project
    Terraform state) is destroyed in the background via ``terraform destroy``.

    Requirements:
    - User must be project admin
    - Project must have NO applications (all apps must be deleted first)

    Raises:
        400: Project has active applications
        403: User is not project admin
        404: Project not found
    """
    user_id = get_user_id_from_token(token_payload)

    # Check admin access
    try:
        from app.services.keycloak_service import (
            _check_user_in_group_realtime,
            _find_group_by_name,
            _get_admin_token,
        )

        admin_token = _get_admin_token()
        admin_group_name = f"project-{project_name}-admins"
        admin_group = _find_group_by_name(admin_group_name, admin_token)

        if not admin_group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_name}' not found.",
            )

        if not _check_user_in_group_realtime(user_id, admin_group["id"], admin_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: admin role required for project '{project_name}'.",
            )

        # Check if project has any applications
        app_count = (
            db.query(Deployment)
            .filter(
                Deployment.project_id == project_name,
                Deployment.status != DeploymentStatus.DELETED,
            )
            .count()
        )

        if app_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete project '{project_name}': it has {app_count} active application(s). Delete all applications first.",
            )

        # Delete Keycloak groups
        logger.info(f"🗑️  Deleting project '{project_name}'...")

        for suffix in ("admins", "members"):
            group_name = f"project-{project_name}-{suffix}"
            group = _find_group_by_name(group_name, admin_token)

            if group:
                group_id = group["id"]
                delete_url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/groups/{group_id}"
                response = requests.delete(
                    delete_url,
                    headers={"Authorization": f"Bearer {admin_token}"},
                    timeout=10,
                )
                response.raise_for_status()
                logger.info(f"✅ Deleted Keycloak group: {group_name}")

        # Remove persisted ownership record
        db.query(ProjectOwner).filter(
            ProjectOwner.project_name == project_name
        ).delete()
        db.commit()

        # Tear down the remaining Day-0 infrastructure — Vault policy, ArgoCD
        # AppProject, GitHub resources and the per-project Terraform state — via
        # `terraform destroy`. Runs in the background because it shells out to
        # Terraform and can take a while; the Keycloak groups above are removed
        # synchronously so the project vanishes from listings right away.
        background_tasks.add_task(
            run_project_teardown,
            project_name=project_name,
        )

        logger.info(
            "✅ Project '%s': Keycloak groups & ownership removed, "
            "infrastructure teardown scheduled",
            project_name,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Failed to delete project '{project_name}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete project: {exc}",
        ) from exc
