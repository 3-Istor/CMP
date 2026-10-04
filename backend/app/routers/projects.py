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
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.schemas.deployment import DeploymentRead
from app.schemas.project import (
    AddMemberRequest,
    ProjectCreate,
    ProjectCreateResponse,
    ProjectRead,
)
from app.services.authz import RequireAdmin, RequireGuest, RequireMember
from app.services.grafana_service import (
    add_user_to_project_org,
    remove_user_from_project_org,
)
from app.services.keycloak_service import (
    ProjectGroupMissingError,
    add_user_to_project,
    fetch_user_projects_from_keycloak,
    get_current_user,
    get_user_id_from_token,  # noqa: F401  (re-exported: finops imports it)
    list_project_members,
    project_guests_supported,
    remove_user_from_project,
)
from app.services.project_bootstrap import (
    run_project_bootstrap,
    run_project_teardown,
)
from app.services.project_registry import (
    ImmutableCloudError,
    RegistryError,
    publish_record,
    remove_record,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


def _administers_any_project(db: Session, token_payload: dict) -> bool:
    """
    Return True if the caller is admin or owner of at least one project.

    Used by the endpoints that are not scoped to a single project but should
    still not be open to every authenticated account.
    """
    user_id = get_user_id_from_token(token_payload)
    username = token_payload.get("preferred_username") or user_id

    if any(
        entry["role"] == ProjectRole.ADMIN.value
        for entry in fetch_user_projects_from_keycloak(user_id)
    ):
        return True

    return (
        db.query(Project)
        .filter(Project.owner_username == username)
        .first()
        is not None
    )


# ---------------------------------------------------------------------------
# GET /api/projects/users/search  — Keycloak user search for member autocomplete
# ---------------------------------------------------------------------------


@router.get("/users/search")
async def search_keycloak_users(
    q: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Search Keycloak users by username or email (prefix search).

    Used by the Members panel autocomplete to find users to add to a project.
    Returns at most 10 results.

    Access control: only users who administer at least one project. The
    endpoint exists to fill the "add a member" field, and that field is
    admin-only — without this check any authenticated account could
    enumerate every username and email in the realm.

    Query params:
        q: Search string (minimum 2 chars).

    Returns:
        List of ``{"username", "email", "first_name", "last_name"}``
    """
    if len(q.strip()) < 2:
        return []

    if not _administers_any_project(db, token_payload):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access denied: only project admins can search for users."
            ),
        )

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
# GET /api/projects
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ProjectRead])
async def list_projects(
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> list[ProjectRead]:
    """
    Return the list of projects the authenticated user belongs to.

    Membership comes from Keycloak group membership, fetched fresh from the
    Admin API rather than read off the JWT, which carries stale claims:
      ``project-<name>-<admins|members|guests>``

    Projects the user owns are merged in on top, with role ``"owner"``. They
    are added even when no group grants access, so a creator whose Keycloak
    group assignment is still in flight — or failed outright — can still see
    what they made.
    """
    username = token_payload.get("preferred_username", "")
    logger.info("🔍 Fetching projects for username='%s'", username)

    user_id = get_user_id_from_token(token_payload)
    logger.info("🔍 Using user_id='%s'", user_id)

    projects = fetch_user_projects_from_keycloak(user_id)
    by_name = {p["name"]: p for p in projects}

    logger.info(
        "📋 Found %d projects for user: %s", len(projects), list(by_name)
    )

    # Owned projects, plus the registry mirror used to render the cloud badge
    # without a round-trip to GitHub on every listing.
    owned = (
        db.query(Project).filter(Project.owner_username == username).all()
        if username
        else []
    )
    for row in owned:
        by_name.setdefault(row.project_name, {"name": row.project_name})

    rows = {
        row.project_name: row
        for row in db.query(Project)
        .filter(Project.project_name.in_(list(by_name)))
        .all()
    }

    for name, entry in by_name.items():
        row = rows.get(name)
        # Projects created before the multicloud chantier have no row; they are
        # on-prem by definition, which is what the schema default says.
        if row is not None:
            entry["target_cloud"] = row.target_cloud
            if username and row.owner_username == username:
                entry["role"] = ProjectRole.OWNER.value

    return [
        ProjectRead(**entry)
        for _, entry in sorted(by_name.items())
        if entry.get("role")
    ]


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

    logger.info(
        "🔐 Creating project '%s' for user_id='%s'",
        payload.project_name,
        user_id,
    )

    # The Git registry is the source of truth for placement (D-01), so it is
    # written first and synchronously: a project whose record failed to commit
    # has nothing for Argo CD to reconcile, and half-creating it is worse than
    # refusing.
    try:
        await publish_record(
            project_name=payload.project_name,
            owner_username=username,
            target_cloud=payload.target_cloud,
        )
    except ImmutableCloudError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except RegistryError as exc:
        logger.error(
            "Registry write failed for '%s': %s", payload.project_name, exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not register the project: {exc}",
        ) from exc

    # Persist the project owner (creator) — immutable, can never be removed —
    # and mirror the cloud choice for the portal. This row is what grants the
    # creator access from the very next request: authorization resolves owner
    # from the database, so it does not wait on Terraform or on the background
    # task below.
    if (
        not db.query(Project)
        .filter(Project.project_name == payload.project_name)
        .first()
    ):
        db.add(
            Project(
                project_name=payload.project_name,
                owner_username=username,
                target_cloud=payload.target_cloud,
            )
        )
        db.commit()
        logger.info(
            "👑 Recorded '%s' as owner of project '%s' on '%s'",
            username,
            payload.project_name,
            payload.target_cloud.value,
        )

    # Trigger bootstrap
    background_tasks.add_task(
        run_project_bootstrap,
        project_name=payload.project_name,
        target_cloud=payload.target_cloud.value,
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

            # Sync to Grafana (non-blocking, best-effort)
            try:
                await add_user_to_project_org(
                    payload.project_name, username, "admin"
                )
            except Exception as grafana_exc:
                logger.warning(
                    "⚠️  Grafana sync failed for creator '%s' in project '%s': %s",
                    username,
                    payload.project_name,
                    grafana_exc,
                )

        except Exception as e:
            logger.error(
                "❌ Failed to add creator to project '%s': %s",
                payload.project_name,
                e,
            )

    background_tasks.add_task(add_creator_to_project)

    logger.info(
        "Project bootstrap triggered for '%s' on '%s' by user '%s'",
        payload.project_name,
        payload.target_cloud.value,
        username,
    )

    return ProjectCreateResponse(
        message=(
            f"Project '{payload.project_name}' bootstrap started on "
            f"{payload.target_cloud.value}. "
            "Keycloak groups, Vault policies, and ArgoCD AppProject will be created shortly. "
            f"You will be added as project admin."
        ),
        project_name=payload.project_name,
        target_cloud=payload.target_cloud,
    )


# ---------------------------------------------------------------------------
# GET /api/projects/{project_name}/apps
# ---------------------------------------------------------------------------


@router.get("/{project_name}/apps", response_model=list[DeploymentRead])
async def list_project_apps(
    ctx: RequireGuest,
    db: Session = Depends(get_db),
) -> list[DeploymentRead]:
    """
    Return all applications (deployments) belonging to a project.

    Access control: any role on the project, guests included — seeing the
    application list is the whole of what a guest is for.
    """
    logger.info(
        "🔍 Listing apps of '%s' for '%s' (%s)",
        ctx.project_name,
        ctx.username,
        ctx.role.value,
    )

    return (
        db.query(Deployment)
        .filter(
            Deployment.project_id == ctx.project_name,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Member Management (Keycloak RBAC)
# ---------------------------------------------------------------------------


@router.get("/{project_name}/members")
async def get_project_members(
    ctx: RequireMember,
    db: Session = Depends(get_db),
) -> dict:
    """
    List every member of a project, across all role groups.

    Access control: member or above. Guests are excluded on purpose — who
    else works on the project is not theirs to see.

    ``guests_supported`` is False for projects bootstrapped before the guests
    group existed; the UI uses it to hide the Guest option rather than offer
    a role that would be rejected.

    Returns:
        dict: ``{"project_name", "members", "guests_supported"}``
    """
    project_name = ctx.project_name

    try:
        members = list_project_members(project_name)

        # Mark the owner (creator) — they always rank above admin and cannot
        # be removed from the project.
        owner = (
            db.query(Project)
            .filter(Project.project_name == project_name)
            .first()
        )
        if owner:
            for member in members:
                if member["username"] == owner.owner_username:
                    member["role"] = ProjectRole.OWNER.value

        return {
            "project_name": project_name,
            "members": members,
            "guests_supported": project_guests_supported(project_name),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to list members for project '%s': %s", project_name, exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch project members: {exc}",
        ) from exc


@router.post("/{project_name}/members", status_code=201)
async def add_project_member(
    payload: AddMemberRequest,
    ctx: RequireAdmin,
) -> dict:
    """
    Add a user to a project, or change the role they already hold.

    Access control: admin or owner.

    The user is first removed from every role group of the project, then
    added to the requested one. Skipping that first step is what used to make
    a demotion a silent no-op: the old group stayed, and role resolution
    takes the strongest group a user is in.

    Raises:
        409: the target group does not exist — the project predates guest
            support and its Terraform module needs re-running.
    """
    project_name = ctx.project_name
    username = payload.username
    role = payload.role

    try:
        # Clear any role the user already holds here, so the new one is the
        # only one left. No-op for a brand new member.
        try:
            remove_user_from_project(username, project_name)
        except ValueError as exc:
            # User genuinely does not exist in Keycloak — report it as such
            # rather than letting the add below fail more obscurely.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        add_user_to_project(username, project_name, role)

        # Sync to Grafana (non-blocking, best-effort)
        try:
            await add_user_to_project_org(project_name, username, role)
        except Exception as grafana_exc:
            logger.warning(
                "⚠️  Grafana sync failed for user '%s' in project '%s': %s",
                username,
                project_name,
                grafana_exc,
            )

        logger.info(
            "👥 '%s' set '%s' to role '%s' on project '%s'",
            ctx.username,
            username,
            role,
            project_name,
        )

        return {
            "message": (
                f"User '{username}' added to project '{project_name}' "
                f"with role '{role}'."
            ),
            "project_name": project_name,
            "username": username,
            "role": role,
        }

    except HTTPException:
        raise
    except ProjectGroupMissingError as exc:
        logger.error(
            "Cannot assign role '%s' on '%s': %s",
            role,
            project_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Group '{exc.group_name}' does not exist. This project was "
                "bootstrapped before guest support was added — re-run the "
                f"project-bootstrap Terraform module for '{project_name}' "
                "to create it."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.error(
            "Failed to add user '%s' to project '%s': %s",
            username,
            project_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to add user to project: {exc}",
        ) from exc


@router.delete("/{project_name}/members/{username}", status_code=204)
async def remove_project_member(
    username: str,
    ctx: RequireAdmin,
    db: Session = Depends(get_db),
) -> None:
    """
    Remove a user from a project, whatever role they held.

    Access control: admin or owner.

    Raises:
        400: User not found, or target is the project owner.
        403: Caller is not a project admin.
        502: Keycloak API error.
    """
    project_name = ctx.project_name

    # The owner (creator) can never be removed from their project.
    owner = (
        db.query(Project).filter(Project.project_name == project_name).first()
    )
    if owner and owner.owner_username == username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{username}' is the project owner and cannot be removed.",
        )

    try:
        remove_user_from_project(username, project_name)

        # Sync to Grafana (non-blocking, best-effort)
        try:
            await remove_user_from_project_org(project_name, username)
        except Exception as grafana_exc:
            logger.warning(
                "⚠️  Grafana sync failed for removing user '%s' from project '%s': %s",
                username,
                project_name,
                grafana_exc,
            )

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
    ctx: RequireAdmin,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> None:
    """
    Delete a project and tear down its Day-0 infrastructure.

    The ownership record is removed synchronously so the project disappears
    from listings immediately; the rest (Keycloak groups, Vault policy, ArgoCD
    AppProject, GitHub resources and the per-project Terraform state) is
    destroyed in the background via ``terraform destroy``.

    Requirements:
    - Caller must be project admin or owner
    - Project must have NO applications (all apps must be deleted first)

    Raises:
        400: Project has active applications
        403: Caller is not a project admin
    """
    project_name = ctx.project_name

    try:
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

        logger.info(f"🗑️  Scheduling teardown for project '{project_name}'...")

        row = (
            db.query(Project)
            .filter(Project.project_name == project_name)
            .first()
        )
        target_cloud = (
            row.target_cloud if row is not None else TargetCloud.ONPREM
        )

        # Removing the registry record is what makes the generated Argo CD
        # Applications and the AppProject disappear, so it is a teardown step
        # rather than a cleanup afterwards — and it has to happen before the
        # Terraform destroy, not after, or Argo CD re-creates what Terraform
        # just removed.
        try:
            await remove_record(project_name)
        except RegistryError as exc:
            logger.error(
                "Could not deregister '%s' — aborting teardown so the project "
                "is not half-deleted: %s",
                project_name,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not deregister the project: {exc}",
            ) from exc

        db.query(Project).filter(Project.project_name == project_name).delete()
        db.commit()

        background_tasks.add_task(
            run_project_teardown,
            project_name=project_name,
            target_cloud=target_cloud.value,
        )

        logger.info(
            "✅ Project '%s' teardown scheduled on '%s'",
            project_name,
            target_cloud.value,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Failed to delete project '{project_name}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete project: {exc}",
        ) from exc
