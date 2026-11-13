"""
The human side of AI assistants: approving a connection, listing and
revoking connected assistants, approving sensitive actions, and setting how
much is at stake in a project.

Every write here refuses an assistant's own token: an assistant must never
approve itself.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.agent import AgentConfirmation, AgentGrant
from app.models.audit import AuditEvent
from app.models.deployment import Deployment, DeploymentStatus
from app.services.agents import policy, store
from app.services.audit import note
from app.services.authz import assert_project_role, is_cnp_admin
from app.services.keycloak_service import get_current_user
from app.services.step_up import assert_step_up

router = APIRouter(prefix="/agents", tags=["AI assistants"])
projects_router = APIRouter(prefix="/projects", tags=["AI assistants"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _human(token: dict) -> dict:
    if token.get("cnp_agent"):
        raise HTTPException(
            status_code=403,
            detail="An assistant cannot approve or change this; a person "
            "does it in the CMP.",
        )
    return token


def require_human(token: CurrentUser) -> dict:
    return _human(token)


Human = Annotated[dict, Depends(require_human)]


def require_infra_human(token: Human) -> dict:
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403,
            detail="Only infra manages other people's assistants.",
        )
    return token


InfraHuman = Annotated[dict, Depends(require_infra_human)]


# ---------------------------------------------------------------------------
# Connection consent
# ---------------------------------------------------------------------------


class AuthRequestView(BaseModel):
    client_name: str
    scopes: list[str]
    redirect_host: str


class ApproveRequest(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: list(store.SCOPES))


class RedirectView(BaseModel):
    redirect_url: str


def _pending_or_404(request_id: str) -> store.PendingRequest:
    request = store.load_auth_request(request_id)
    if request is None:
        raise HTTPException(
            status_code=404,
            detail="This connection request expired. Start the connection "
            "again from your assistant.",
        )
    return request


@router.get("/requests/{request_id}", response_model=AuthRequestView)
def get_auth_request(request_id: str, _: Human) -> AuthRequestView:
    request = _pending_or_404(request_id)
    return AuthRequestView(
        client_name=request.client_name,
        # The user picks, whatever the client asked: clients request the
        # resource's scopes_supported, which only names the read scope
        # required on every call.
        scopes=list(store.SCOPES),
        redirect_host=urlparse(request.params["redirect_uri"]).netloc
        or request.params["redirect_uri"].split(":", 1)[0],
    )


@router.post("/requests/{request_id}/approve", response_model=RedirectView)
def approve_auth_request(
    request_id: str, body: ApproveRequest, token: Human
) -> RedirectView:
    # A grant outlives the session that consents to it by weeks: a stolen
    # session must not be able to turn itself into one.
    assert_step_up(token)
    request = _pending_or_404(request_id)
    scopes = [s for s in store.SCOPES if s in body.scopes]
    if store.READ_SCOPE not in scopes:
        raise HTTPException(
            status_code=400, detail="Read access is the minimum."
        )
    if not token.get("sub"):
        raise HTTPException(status_code=400, detail="Token without subject.")
    code = store.save_auth_code(request, token, scopes)
    return RedirectView(
        redirect_url=construct_redirect_uri(
            request.params["redirect_uri"],
            code=code,
            state=request.params.get("state"),
        )
    )


@router.post("/requests/{request_id}/deny", response_model=RedirectView)
def deny_auth_request(request_id: str, _: Human) -> RedirectView:
    request = _pending_or_404(request_id)
    store.consume_auth_request(request_id)
    return RedirectView(
        redirect_url=construct_redirect_uri(
            request.params["redirect_uri"],
            error="access_denied",
            error_description="The user declined the connection.",
            state=request.params.get("state"),
        )
    )


# ---------------------------------------------------------------------------
# Connected assistants
# ---------------------------------------------------------------------------


class GrantRead(BaseModel):
    id: int
    client_name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime


@router.get("/grants", response_model=list[GrantRead])
def list_my_grants(token: CurrentUser) -> list[GrantRead]:
    return [
        GrantRead(**g.__dict__)
        for g in store.list_grants(token.get("preferred_username", ""))
    ]


@router.delete("/grants/{grant_id}", status_code=204)
def revoke_my_grant(grant_id: int, token: CurrentUser) -> None:
    if not store.revoke_grant(grant_id, token.get("preferred_username", "")):
        raise HTTPException(status_code=404, detail="No such assistant.")


class AdminGrantRead(GrantRead):
    username: str
    subject: str


class RevokedGrants(BaseModel):
    revoked: list[int]


@router.get("/admin/grants", response_model=list[AdminGrantRead])
def list_all_grants(
    _: InfraHuman, username: str | None = None
) -> list[AdminGrantRead]:
    return [
        AdminGrantRead(**g.__dict__) for g in store.list_all_grants(username)
    ]


@router.delete("/admin/grants/{grant_id}", status_code=204)
def revoke_any_grant(grant_id: int, request: Request, _: InfraHuman) -> None:
    grant = store.revoke_any_grant(grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="No such assistant.")
    note(
        request,
        target=grant.username,
        grant_id=grant.id,
        client=grant.client_name,
    )


@router.delete("/admin/users/{username}/grants", response_model=RevokedGrants)
def revoke_user_grants(
    username: str, request: Request, _: InfraHuman
) -> RevokedGrants:
    revoked = [g.id for g in store.revoke_user(username)]
    note(request, target=username, grant_ids=revoked)
    return RevokedGrants(revoked=revoked)


class AgentSummary(BaseModel):
    assistants: list[GrantRead]
    apps_created: int
    last_action_at: datetime | None
    last_action: str | None
    pending_confirmations: int


@router.get("/summary", response_model=AgentSummary)
def my_agent_summary(
    token: CurrentUser, db: Session = Depends(get_db)
) -> AgentSummary:
    username = token.get("preferred_username", "")
    grants = store.list_grants(username)
    last = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.actor == username,
            AuditEvent.details.like('%"via": %'),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    ).first()
    created = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.actor == username,
            AuditEvent.action == "app.create",
            AuditEvent.outcome == "success",
            AuditEvent.details.like('%"via": %'),
        )
    )
    pending = len(
        [
            c
            for c in _confirmations_for(db, username, None)
            if c.status == "pending"
        ]
    )
    return AgentSummary(
        assistants=[GrantRead(**g.__dict__) for g in grants],
        apps_created=created or 0,
        last_action_at=last.created_at if last else None,
        last_action=last.action if last else None,
        pending_confirmations=pending,
    )


class PublicStats(BaseModel):
    apps_created_this_month: int
    connected_assistants: int


@router.get("/stats", response_model=PublicStats)
def public_stats(db: Session = Depends(get_db)) -> PublicStats:
    """Aggregates only, for the public landing page."""
    start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    apps = db.scalar(
        select(func.count(Deployment.id)).where(
            Deployment.created_via.is_not(None),
            Deployment.created_at >= start,
        )
    )
    assistants = db.scalar(
        select(func.count(AgentGrant.id)).where(
            AgentGrant.revoked_at.is_(None),
            AgentGrant.expires_at > datetime.now(timezone.utc),
        )
    )
    return PublicStats(
        apps_created_this_month=apps or 0,
        connected_assistants=assistants or 0,
    )


# ---------------------------------------------------------------------------
# Confirmations of sensitive actions
# ---------------------------------------------------------------------------


class ConfirmationRead(BaseModel):
    id: str
    username: str
    project: str
    deployment_id: int | None
    tool: str
    summary: str
    status: str
    created_at: datetime
    expires_at: datetime
    decided_by: str | None
    # Where a person reviews a draft of a portal-only tool (policy
    # PORTAL_TOOLS) instead of approving it here.
    review_link: str | None = None
    # The plan edits the assistant proposed, prefilled on that page only
    # for the analysis they were proposed for.
    draft: dict | None = None
    draft_run_id: str | None = None


def _confirmation_read(view: policy.ConfirmationView) -> ConfirmationRead:
    portal = view.tool in policy.PORTAL_TOOLS
    return ConfirmationRead(
        **{k: v for k, v in view.__dict__.items() if k != "arguments"},
        review_link=policy.draft_review_link(view) if portal else None,
        draft=view.arguments.get("edits") if portal else None,
        draft_run_id=view.arguments.get("run_id") if portal else None,
    )


def _confirmations_for(
    db: Session, username: str, project: str | None
) -> list[policy.ConfirmationView]:
    query = select(AgentConfirmation.id).where(
        AgentConfirmation.created_at
        > datetime.now(timezone.utc) - timedelta(days=7)
    )
    if project:
        query = query.where(AgentConfirmation.project == project)
    else:
        query = query.where(AgentConfirmation.username == username)
    return [
        view
        for cid in db.scalars(query)
        if (view := policy.load_confirmation(cid)) is not None
    ]


def _may_decide(db: Session, token: dict, view: policy.ConfirmationView):
    """The person who asked, or an admin of the project, decides."""
    if view.username == token.get("preferred_username"):
        assert_project_role(db, token, view.project, ProjectRole.MEMBER)
        return
    assert_project_role(db, token, view.project, ProjectRole.ADMIN)


@router.get(
    "/confirmations/{confirmation_id}", response_model=ConfirmationRead
)
def get_confirmation(
    confirmation_id: str, token: CurrentUser, db: Session = Depends(get_db)
) -> ConfirmationRead:
    view = policy.load_confirmation(confirmation_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No such request.")
    assert_project_role(db, token, view.project, ProjectRole.MEMBER)
    return _confirmation_read(view)


@router.post(
    "/confirmations/{confirmation_id}/{decision}",
    response_model=ConfirmationRead,
)
def decide_confirmation(
    confirmation_id: str,
    decision: Literal["approve", "deny"],
    token: Human,
    db: Session = Depends(get_db),
) -> ConfirmationRead:
    view = policy.load_confirmation(confirmation_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No such request.")
    _may_decide(db, token, view)
    if decision == "approve" and view.tool in policy.PORTAL_TOOLS:
        raise HTTPException(
            status_code=409,
            detail="Review and apply this import from its page in the CMP.",
        )
    if decision == "approve":
        assert_step_up(token)
    if view.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"This request is already {view.status}."
        )
    decided = policy.decide_confirmation(
        confirmation_id,
        decision == "approve",
        token.get("preferred_username", ""),
    )
    return _confirmation_read(decided)


@router.get("/confirmations", response_model=list[ConfirmationRead])
def list_confirmations(
    token: CurrentUser,
    project: str | None = None,
    db: Session = Depends(get_db),
) -> list[ConfirmationRead]:
    if project:
        assert_project_role(db, token, project, ProjectRole.MEMBER)
    views = _confirmations_for(
        db, token.get("preferred_username", ""), project
    )
    return [
        _confirmation_read(view)
        for view in sorted(views, key=lambda v: v.created_at, reverse=True)
    ]


# ---------------------------------------------------------------------------
# Project stake
# ---------------------------------------------------------------------------


class AgentSettingsRead(BaseModel):
    project: str
    level: str
    effective_level: str
    autonomous_until: datetime | None
    suggested_level: str
    suggestion_reason: str
    rules: dict[str, str]
    levels: list[str]


class AgentSettingsUpdate(BaseModel):
    level: Literal["sandbox", "demo", "standard", "critical"] | None = None
    autonomous_hours: float | None = Field(default=None, ge=0, le=8)


def _suggest(db: Session, project: str) -> tuple[str, str]:
    """A starting point from what the CMP already knows; the owner decides."""
    apps = db.scalars(
        select(Deployment).where(
            Deployment.project_id == project,
            Deployment.status != DeploymentStatus.DELETED,
        )
    ).all()
    if not apps:
        return "demo", "No app yet."
    if any("fastapi" in (a.app_config or "") for a in apps):
        return "standard", "At least one app stores data in a database."
    return "demo", "Only static apps, no database."


def _settings_view(db: Session, project: str) -> AgentSettingsRead:
    stake = policy.project_stake(project)
    level = stake.effective_level
    suggested, reason = _suggest(db, project)
    return AgentSettingsRead(
        project=project,
        level=stake.level,
        effective_level=level,
        autonomous_until=stake.autonomous_until,
        suggested_level=suggested,
        suggestion_reason=reason,
        rules={
            action: row[level]
            for action, row in policy.rules()["actions"].items()
        },
        levels=list(policy.LEVELS),
    )


@projects_router.get(
    "/{project_name}/agent-settings", response_model=AgentSettingsRead
)
def get_agent_settings(
    project_name: str, token: CurrentUser, db: Session = Depends(get_db)
) -> AgentSettingsRead:
    assert_project_role(db, token, project_name, ProjectRole.GUEST)
    return _settings_view(db, project_name)


@projects_router.put(
    "/{project_name}/agent-settings", response_model=AgentSettingsRead
)
async def update_agent_settings(
    project_name: str,
    body: AgentSettingsUpdate,
    token: Human,
    db: Session = Depends(get_db),
) -> AgentSettingsRead:
    await run_in_threadpool(
        assert_project_role, db, token, project_name, ProjectRole.ADMIN
    )
    await run_in_threadpool(
        policy.set_project_stake,
        project_name,
        token.get("preferred_username", ""),
        body.level,
        body.autonomous_hours,
    )
    return _settings_view(db, project_name)
