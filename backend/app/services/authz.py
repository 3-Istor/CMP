"""
Project authorization.

One place decides whether a caller may touch a project, so the rule lives
here instead of being re-typed into every endpoint.

Use the ready-made annotations on any route with a ``{project_name}`` path
parameter::

    @router.get("/{project_name}/apps")
    async def list_apps(ctx: RequireGuest, db: Session = Depends(get_db)):
        ...            # ctx.role tells you who they are

    @router.delete("/{project_name}")
    async def delete_project(ctx: RequireAdmin, ...):
        ...

For the cases where the project is not in the path, it comes off a
deployment row, say, call :func:`assert_project_role` directly.

Note on the shape: the factory takes only the minimum role. The dependency
it returns declares ``project_name`` in its own signature, which is what
lets FastAPI bind it from the request path. The previous attempt at this
(``verify_project_access(project_name, ...)``, now deleted) took the project
name as a factory argument, so it was resolved at import time and could
never see the actual request, which is why every router ended up inlining
the check by hand.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import requests
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.roles import ProjectRole, at_least, rank
from app.models.project import Project
from app.services import keycloak_service
from app.services.keycloak_service import (
    get_current_user,
    get_user_id_from_token,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectContext:
    """Who the caller is, and what they may do on one project."""

    project_name: str
    user_id: str
    username: str
    role: ProjectRole
    token: dict

    def at_least(self, minimum: ProjectRole) -> bool:
        """Return True if this caller holds *minimum* or better."""
        return at_least(self.role, minimum)

    @property
    def is_owner(self) -> bool:
        """Return True if this caller created the project."""
        return self.role is ProjectRole.OWNER


def _resolve_role(
    db: Session, token: dict, project_name: str
) -> tuple[str, str, ProjectRole | None]:
    """
    Work out the caller's role on *project_name*.

    Returns ``(user_id, username, role)``, where role is None when the caller
    has no standing on the project at all.
    """
    user_id = get_user_id_from_token(token)
    username = token.get("preferred_username") or user_id

    # The owner is authoritative and costs nothing to check: create_project
    # commits the row before it schedules the Keycloak group work, so the
    # creator is recognised from the very first request, without waiting for
    # Terraform or the background task that adds them to -admins.
    row = (
        db.query(Project).filter(Project.project_name == project_name).first()
    )
    if row is not None and row.owner_username == username:
        return user_id, username, ProjectRole.OWNER

    # Through keycloak_service.get_project_role so every router (and every
    # test double) resolves group roles the same way.
    role = keycloak_service.get_project_role(user_id, project_name)
    group_role = ProjectRole(role) if role else None
    platform_role = platform_project_role(token)
    if platform_role is not None:
        if group_role is None or rank(platform_role) > rank(group_role):
            return user_id, username, platform_role
    return user_id, username, group_role


def assert_project_role(
    db: Session,
    token: dict,
    project_name: str,
    minimum: ProjectRole,
) -> ProjectContext:
    """
    Raise unless the caller holds *minimum* or better on *project_name*.

    The imperative twin of :func:`require_project_role`, for call sites where
    the project isn't a path parameter.

    Raises:
        HTTPException: 403 if the caller lacks the role, 503 if Keycloak
            could not be reached.
    """
    try:
        user_id, username, role = _resolve_role(db, token, project_name)
    except HTTPException:
        raise
    except requests.RequestException as exc:
        logger.error(
            "Could not verify access to project '%s': %s", project_name, exc
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to verify project access. Please try again.",
        ) from exc

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied: you are not a member of project "
                f"'{project_name}'."
            ),
        )

    if not at_least(role, minimum):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied: {minimum.value} role required for project "
                f"'{project_name}' (you are {role.value})."
            ),
        )

    return ProjectContext(
        project_name=project_name,
        user_id=user_id,
        username=username,
        role=role,
        token=token,
    )


def require_project_role(
    minimum: ProjectRole,
) -> Callable[..., ProjectContext]:
    """
    Build a FastAPI dependency enforcing *minimum* on ``{project_name}``.

    Prefer the module-level annotations below over calling this per route:
    each call builds a fresh dependency, and FastAPI caches by dependency
    identity, so two distinct objects on one request would resolve the role
    twice.
    """

    def _dep(
        project_name: str,
        token_payload: Annotated[dict, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> ProjectContext:
        return assert_project_role(db, token_payload, project_name, minimum)

    return _dep


def require_project_member(
    token: dict, project_name: str, require_admin: bool = False
) -> str:
    """
    :func:`assert_project_role` for routers without a db session, member
    minimum (admin when *require_admin*). Returns "admin" or "member": the
    owner is reported as "admin", the routers using this compare strings.

    Guests are refused: the routers using this (security, activity, alerts)
    expose logs and findings, which guests do not get.
    """
    minimum = ProjectRole.ADMIN if require_admin else ProjectRole.MEMBER
    with SessionLocal() as db:
        role = assert_project_role(db, token, project_name, minimum).role
    return ProjectRole.ADMIN.value if role is ProjectRole.OWNER else role.value


def project_role_or_none(token: dict, project_name: str) -> str | None:
    """
    The caller's role on *project_name* as the UI shows it ("admin" for the
    owner too, "member", "guest"), or None without any standing.
    """
    with SessionLocal() as db:
        try:
            role = assert_project_role(
                db, token, project_name, ProjectRole.GUEST
            ).role
        except HTTPException as exc:
            if exc.status_code == status.HTTP_403_FORBIDDEN:
                return None
            raise
    return ProjectRole.ADMIN.value if role is ProjectRole.OWNER else role.value


# Built once, at import time, see require_project_role's docstring.
RequireGuest = Annotated[
    ProjectContext, Depends(require_project_role(ProjectRole.GUEST))
]
RequireMember = Annotated[
    ProjectContext, Depends(require_project_role(ProjectRole.MEMBER))
]
RequireAdmin = Annotated[
    ProjectContext, Depends(require_project_role(ProjectRole.ADMIN))
]
RequireOwner = Annotated[
    ProjectContext, Depends(require_project_role(ProjectRole.OWNER))
]


# Platform identities of the access matrix (cnp-docs model.yaml): Keycloak
# groups, read from the token's ``groups`` claim. Keycloak is the only source:
# no username list, no realm role. A removal applies when the token expires.
PLATFORM_ADMIN_GROUP = "infra"
AUDITOR_GROUP = "security-auditor"
FINOPS_GROUP = "finops"


def _groups(token: dict) -> set[str]:
    # Keycloak prefixes group paths with "/" when full_path is on.
    return {str(g).lstrip("/") for g in token.get("groups") or []}


def platform_roles(token: dict) -> list[str]:
    """The caller's platform identities, for the UI."""
    groups = _groups(token)
    return [
        g
        for g in (PLATFORM_ADMIN_GROUP, AUDITOR_GROUP, FINOPS_GROUP)
        if g in groups
    ]


def is_cnp_admin(token: dict) -> bool:
    """Platform admin, infra and portal alike: the ``infra`` group."""
    return PLATFORM_ADMIN_GROUP in _groups(token)


def is_auditor(token: dict) -> bool:
    """Reads every project except logs and secrets, approves critical CVE exceptions."""
    return AUDITOR_GROUP in _groups(token)


def is_finops(token: dict) -> bool:
    """Every project's costs, exports and month close."""
    return FINOPS_GROUP in _groups(token)


def reads_platform_security(token: dict) -> bool:
    """Security findings and activity of every project."""
    return is_cnp_admin(token) or is_auditor(token)


def platform_project_role(token: dict) -> ProjectRole | None:
    """
    The role a platform identity holds on a project it is not a member of:
    admin for a platform admin, guest (read) for the auditor. FinOps has
    none: it reads costs through the FinOps routes only.
    """
    if is_cnp_admin(token):
        return ProjectRole.ADMIN
    if is_auditor(token):
        return ProjectRole.GUEST
    return None
