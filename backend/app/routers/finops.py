"""
FinOps Router — costs, budgets, recommendations and alerts.

All cost figures come from the deterministic simulation behind
:func:`app.services.finops.provider.get_cost_provider`; only budgets, alert
history and recommendation decisions are persisted.

Access control (spec matrix):
  * **Admin CNP** (``FINOPS_ADMIN_USERS``) — sees every project's costs.
  * **Project members/admins** — see their own projects' costs.
  * **Project Owner** — the ONLY role allowed to set a project budget.
"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.models.finops import (
    BudgetAlertState,
    CostAlert,
    ProjectBudget,
    RecommendationState,
)
from app.models.project import ProjectOwner
from app.schemas.finops import (
    ActionResponse,
    AppCostRow,
    BudgetRead,
    BudgetWrite,
    Breakdown,
    CostAlertRead,
    CostSeriesPoint,
    CostSummary,
    OverviewResponse,
    Recommendation,
)
from app.services.finops.provider import get_cost_provider, period_to_range
from app.services.keycloak_service import (
    fetch_user_projects_from_keycloak,
    get_current_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/finops", tags=["FinOps"])

_ACTIVE = [s for s in DeploymentStatus if s != DeploymentStatus.DELETED]


# ── RBAC helpers ──────────────────────────────────────────────────────────────


def _username(token: dict) -> str:
    return token.get("preferred_username") or token.get("sub", "")


def is_cnp_admin(token: dict) -> bool:
    """Global FinOps admin: configured username or a ``cnp-admin`` realm role."""
    admins = {
        u.strip().lower()
        for u in settings.FINOPS_ADMIN_USERS.split(",")
        if u.strip()
    }
    if _username(token).lower() in admins:
        return True
    roles = (token.get("realm_access") or {}).get("roles", [])
    return "cnp-admin" in roles


def _user_project_names(token: dict) -> list[str]:
    """
    Projects the caller belongs to, resolved in real time via the Keycloak
    Admin API — the same mechanism the projects/apps routers use. This avoids
    relying on the JWT ``groups`` claim, which is not populated in this setup.

    Uses the same ``get_user_id_from_token`` resolution as the projects router
    (``sub`` claim, with a username fallback) so results match exactly.
    """
    from app.routers.projects import get_user_id_from_token

    try:
        user_id = get_user_id_from_token(token)
    except HTTPException:
        return []
    if not user_id:
        return []
    return [p["name"] for p in fetch_user_projects_from_keycloak(user_id)]


def _require_project_access(token: dict, project: str) -> None:
    if is_cnp_admin(token):
        return
    if project not in _user_project_names(token):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: you are not a member of project '{project}'.",
        )


def _is_owner(db: Session, project: str, username: str) -> bool:
    owner = (
        db.query(ProjectOwner)
        .filter(ProjectOwner.project_name == project)
        .first()
    )
    return bool(owner and owner.owner_username == username)


def _load_deployments(
    db: Session,
    token: dict,
    project: str | None = None,
    app_id: int | None = None,
) -> list[Deployment]:
    """Return the deployments the caller may see, optionally filtered."""
    q = (
        db.query(Deployment)
        .filter(Deployment.status.in_(_ACTIVE))
        .filter(Deployment.project_id.isnot(None))
    )
    if project:
        _require_project_access(token, project)
        q = q.filter(Deployment.project_id == project)
    elif not is_cnp_admin(token):
        projects = _user_project_names(token)
        if not projects:
            return []
        q = q.filter(Deployment.project_id.in_(projects))
    if app_id is not None:
        q = q.filter(Deployment.id == app_id)
    return q.all()


# ── Budget helpers ────────────────────────────────────────────────────────────


def _budget_read(budget: ProjectBudget | None, spent: float) -> BudgetRead | None:
    if budget is None:
        return None
    amount = budget.monthly_amount_eur or 0.0
    consumed_pct = round(spent / amount * 100, 1) if amount > 0 else None
    if consumed_pct is None:
        status = "ok"
    elif consumed_pct >= budget.threshold_critical:
        status = "critical"
    elif consumed_pct >= budget.threshold_warn:
        status = "warning"
    else:
        status = "ok"
    return BudgetRead(
        project_name=budget.project_name,
        monthly_amount_eur=amount,
        threshold_warn=budget.threshold_warn,
        threshold_critical=budget.threshold_critical,
        currency=budget.currency,
        spent_eur=round(spent, 2),
        remaining_eur=round(amount - spent, 2),
        consumed_pct=consumed_pct,
        status=status,
        updated_by=budget.updated_by,
        updated_at=budget.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
    project: str | None = None,
    period: str = "30d",
    granularity: str = "daily",
):
    """Top KPIs + budget + app list + timeline for the Overview dashboard."""
    deployments = _load_deployments(db, token, project)
    provider = get_cost_provider()
    apps = provider.specs(deployments)

    summary_dict = provider.summary(apps)
    summary_dict["breakdown"] = Breakdown(**summary_dict["breakdown"])
    summary = CostSummary(**summary_dict)

    start, end = period_to_range(period)
    timeline = [
        CostSeriesPoint(**p) for p in provider.timeline(apps, start, end, granularity)
    ]
    app_rows = [AppCostRow(**r) for r in provider.app_rows(apps)]

    budget_read = None
    if project:
        budget = (
            db.query(ProjectBudget)
            .filter(ProjectBudget.project_name == project)
            .first()
        )
        budget_read = _budget_read(budget, summary.month_to_date_eur)

    return OverviewResponse(
        summary=summary,
        budget=budget_read,
        apps=app_rows,
        timeline=timeline,
    )


@router.get("/timeline", response_model=list[CostSeriesPoint])
def get_timeline(
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
    project: str | None = None,
    app: int | None = None,
    resource: str | None = Query(default=None, description="cpu|ram|storage|network"),
    granularity: str = "daily",
    period: str = "30d",
):
    """Stacked cost series with flexible granularity and filters."""
    deployments = _load_deployments(db, token, project, app)
    provider = get_cost_provider()
    apps = provider.specs(deployments)
    start, end = period_to_range(period)
    points = provider.timeline(apps, start, end, granularity)

    if resource and resource in ("cpu", "ram", "storage", "network"):
        # Zero out non-selected resources so the client can still stack.
        for p in points:
            for r in ("cpu", "ram", "storage", "network"):
                if r != resource:
                    p[r] = 0.0
            p["total"] = p[resource]
    return [CostSeriesPoint(**p) for p in points]


@router.get("/breakdown", response_model=Breakdown)
def get_breakdown(
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
    project: str | None = None,
    app: int | None = None,
):
    deployments = _load_deployments(db, token, project, app)
    provider = get_cost_provider()
    apps = provider.specs(deployments)
    return Breakdown(**provider.breakdown(apps))


@router.get("/apps", response_model=list[AppCostRow])
def get_apps(
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
    project: str | None = None,
):
    deployments = _load_deployments(db, token, project)
    provider = get_cost_provider()
    apps = provider.specs(deployments)
    return [AppCostRow(**r) for r in provider.app_rows(apps)]


@router.get("/recommendations", response_model=list[Recommendation])
def get_recommendations(
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
    project: str | None = None,
):
    deployments = _load_deployments(db, token, project)
    provider = get_cost_provider()
    apps = provider.specs(deployments)
    recs = provider.recommendations(apps)

    # Overlay persisted user decisions.
    states = {
        s.rec_id: s.status
        for s in db.query(RecommendationState)
        .filter(RecommendationState.rec_id.in_([r.id for r in recs]))
        .all()
    } if recs else {}

    result = []
    for r in recs:
        result.append(Recommendation(
            id=r.id,
            app_id=r.app_id,
            app_name=r.app_name,
            project_id=r.project_id,
            rec_type=r.rec_type,
            title=r.title,
            justification=r.justification,
            current=r.current,
            recommended=r.recommended,
            monthly_saving_eur=r.monthly_saving_eur,
            confidence=r.confidence,
            effort=r.effort,
            status=states.get(r.id, "pending"),
            can_apply=r.patch is not None,
        ))
    return result


def _find_recommendation(db: Session, token: dict, rec_id: str):
    """Recompute recommendations for the owning app and return the match."""
    try:
        app_id = int(rec_id.split(":", 1)[0])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid recommendation id.")

    deployment = db.get(Deployment, app_id)
    if not deployment or deployment.project_id is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    _require_project_access(token, deployment.project_id)

    provider = get_cost_provider()
    recs = provider.recommendations(provider.specs([deployment]))
    rec = next((r for r in recs if r.id == rec_id), None)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    return deployment, rec


def _upsert_state(db: Session, rec, username: str, status: str) -> None:
    state = (
        db.query(RecommendationState)
        .filter(RecommendationState.rec_id == rec.id)
        .first()
    )
    if state is None:
        state = RecommendationState(
            rec_id=rec.id,
            project_name=rec.project_id or "",
            app_id=rec.app_id,
            rec_type=rec.rec_type,
        )
        db.add(state)
    state.status = status
    state.updated_by = username
    db.commit()


@router.post("/recommendations/{rec_id}/apply", response_model=ActionResponse)
async def apply_recommendation(
    rec_id: str,
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """
    Apply a recommendation. For GitOps apps this commits the reduced
    replicas/resources to ``deploy/values.yaml`` (ArgoCD then syncs). For apps
    without a linked repo the decision is recorded without a commit.
    """
    deployment, rec = _find_recommendation(db, token, rec_id)
    if rec.patch is None:
        raise HTTPException(
            status_code=400,
            detail="This recommendation cannot be auto-applied.",
        )

    commit_sha: str | None = None
    if deployment.github_repo_url:
        commit_sha = await _apply_patch_to_gitops(deployment, rec.patch)

    _upsert_state(db, rec, _username(token), "applied")
    return ActionResponse(
        message=(
            "Recommandation appliquée (commit GitOps créé)."
            if commit_sha
            else "Recommandation marquée comme appliquée."
        ),
        rec_id=rec.id,
        status="applied",
        commit_sha=commit_sha,
    )


@router.post("/recommendations/{rec_id}/ignore", response_model=ActionResponse)
def ignore_recommendation(
    rec_id: str,
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    _deployment, rec = _find_recommendation(db, token, rec_id)
    _upsert_state(db, rec, _username(token), "ignored")
    return ActionResponse(message="Recommandation ignorée.", rec_id=rec.id, status="ignored")


@router.post("/recommendations/{rec_id}/notify", response_model=ActionResponse)
async def notify_recommendation(
    rec_id: str,
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    _deployment, rec = _find_recommendation(db, token, rec_id)
    from app.services import discord_service

    await discord_service.send_finops_recommendation(
        project_name=rec.project_id or "",
        app_name=rec.app_name,
        title=rec.title,
        saving_eur=rec.monthly_saving_eur,
    )
    _upsert_state(db, rec, _username(token), "notified")
    return ActionResponse(
        message="Project Owner notifié.", rec_id=rec.id, status="notified"
    )


@router.get("/budgets/{project_name}", response_model=BudgetRead | None)
def get_budget(
    project_name: str,
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    _require_project_access(token, project_name)
    deployments = _load_deployments(db, token, project_name)
    provider = get_cost_provider()
    spent = provider.summary(provider.specs(deployments))["month_to_date_eur"]
    budget = (
        db.query(ProjectBudget)
        .filter(ProjectBudget.project_name == project_name)
        .first()
    )
    return _budget_read(budget, spent)


@router.put("/budgets/{project_name}", response_model=BudgetRead)
def put_budget(
    project_name: str,
    payload: BudgetWrite,
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Create/update a project budget — **Project Owner only**."""
    username = _username(token)
    if not _is_owner(db, project_name, username):
        raise HTTPException(
            status_code=403,
            detail=(
                "Seul le Project Owner peut définir le budget de ce projet."
            ),
        )
    if payload.threshold_warn >= payload.threshold_critical:
        raise HTTPException(
            status_code=400,
            detail="Le seuil d'attention doit être inférieur au seuil critique.",
        )

    budget = (
        db.query(ProjectBudget)
        .filter(ProjectBudget.project_name == project_name)
        .first()
    )
    if budget is None:
        budget = ProjectBudget(project_name=project_name)
        db.add(budget)
    budget.monthly_amount_eur = payload.monthly_amount_eur
    budget.threshold_warn = payload.threshold_warn
    budget.threshold_critical = payload.threshold_critical
    budget.currency = payload.currency
    budget.updated_by = username
    db.commit()
    db.refresh(budget)

    # Reset anti-spam state so alerts re-evaluate against the new budget.
    state = (
        db.query(BudgetAlertState)
        .filter(BudgetAlertState.project_name == project_name)
        .first()
    )
    if state:
        state.last_level = 0
        db.commit()

    deployments = _load_deployments(db, token, project_name)
    provider = get_cost_provider()
    spent = provider.summary(provider.specs(deployments))["month_to_date_eur"]
    return _budget_read(budget, spent)


@router.get("/alerts", response_model=list[CostAlertRead])
def get_alerts(
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
    project: str | None = None,
    limit: int = 100,
):
    q = db.query(CostAlert)
    if project:
        _require_project_access(token, project)
        q = q.filter(CostAlert.project_name == project)
    elif not is_cnp_admin(token):
        projects = _user_project_names(token)
        if not projects:
            return []
        q = q.filter(CostAlert.project_name.in_(projects))
    alerts = q.order_by(CostAlert.triggered_at.desc()).limit(limit).all()
    return alerts


# ── GitOps apply helper (reuses the deployments router primitives) ────────────


async def _apply_patch_to_gitops(deployment: Deployment, patch: dict) -> str | None:
    """
    Deep-merge ``patch`` into the app's ``deploy/values.yaml`` and commit.
    Mirrors ``update_deployment_config`` but is triggered by a recommendation.
    """
    from io import StringIO

    from app.routers.deployments import (
        _CONFIG_FILE_PATH,
        _deep_merge,
        _extract_repo_full_name,
        _get_github_token_for_deployment,
        _yaml,
    )
    from app.services.github_service import (
        GitHubAppError,
        get_file_content,
        update_file_content,
    )

    try:
        token = await _get_github_token_for_deployment(deployment)
        repo = _extract_repo_full_name(deployment.github_repo_url)
        raw_content, sha = await get_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=_CONFIG_FILE_PATH,
        )
        parsed = _yaml.load(raw_content)
        _deep_merge(parsed, patch)
        out = StringIO()
        _yaml.dump(parsed, out)
        result = await update_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=_CONFIG_FILE_PATH,
            content=out.getvalue(),
            message="chore(finops): apply cost optimisation recommendation",
            sha=sha,
        )
        return result.get("commit", {}).get("sha", "")
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub error: {exc}") from exc
