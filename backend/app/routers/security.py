"""
Security dashboard API.

Reads only the CMP database, filled by the background collector: a page view
never calls GitHub or the cluster. Writes are the page's buttons: start a
scan or a backup, and ignore or reactivate a finding.
"""

import difflib
import json
import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.models.security import SecurityException, SecurityScan
from app.routers.finops import _user_project_names, is_cnp_admin
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    dispatch_workflow,
    get_default_branch,
    get_file_content,
    get_installation_token,
    put_file_content,
)
from app.services.keycloak_service import (
    get_current_user,
    get_project_role,
    require_project_role,
)
from app.services.kube_client import (
    KubeUnavailableError,
    kube_create,
    kube_delete_collection,
    kube_list,
)
from app.services.project_registry import (
    RegistryError,
    read_record,
    write_security_policy,
)
from app.services.security import exceptions as rules
from app.services.security import scoring
from app.services.security import settings as app_settings
from app.services.security import store
from app.services.security.collector import (
    K8S_TEMPLATE,
    SECURITY_WORKFLOW,
    repo_full_name,
    utcnow,
)
from app.services.security.model import Category, Source, Tier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["Security"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
View = Literal["developer", "platform"]
MANUAL_SCAN_COOLDOWN = timedelta(minutes=15)


# ── Access ───────────────────────────────────────────────────────────────────


def _username(token: dict) -> str:
    return token.get("preferred_username") or token.get("sub", "")


async def _require_member(token: dict, project: str, view: View) -> None:
    if is_cnp_admin(token):
        return
    if view == "platform":
        raise HTTPException(
            status_code=403, detail="The platform view is for CNP admins."
        )
    await run_in_threadpool(require_project_role, token, project)


async def _require_admin(token: dict, project: str) -> None:
    if is_cnp_admin(token):
        return
    await run_in_threadpool(require_project_role, token, project, True)


def _app_deployment(db: Session, project: str, app: str) -> Deployment:
    deployment = (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project,
            Deployment.name == app,
            Deployment.template_id == K8S_TEMPLATE,
        )
        .order_by(Deployment.id.desc())
        .first()
    )
    if deployment is None:
        raise HTTPException(status_code=404, detail="App not found")
    return deployment


# ── Schemas ──────────────────────────────────────────────────────────────────


class ExceptionRead(BaseModel):
    id: int
    app: str | None
    fingerprint: str
    rule: str
    kind: str
    status: str
    justification: str | None
    statement: str
    expires_on: date
    author: str
    created_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    revoked_by: str | None
    revoked_at: datetime | None
    pending_approval: bool = False


class FindingRead(BaseModel):
    fingerprint: str
    app: str | None
    category: Category
    tier: Tier
    audience: str
    rule: str
    title: str
    detail: str
    fix: str
    location: str
    link: str | None
    raw: str
    sources: list[str]
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None
    exception: ExceptionRead | None


class SegmentRead(BaseModel):
    category: Category
    label: str
    worst: Tier | None
    counts: dict[str, int]
    points_lost: int


class ScanRead(BaseModel):
    app: str | None
    source: str
    status: str
    message: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    collected_at: datetime | None
    requested_at: datetime | None


class AppSummary(BaseModel):
    app: str
    deployment_id: int | None
    score: int
    grade: str
    actions: int
    core: int


class Guarantee(BaseModel):
    label: str
    ok: bool


class Summary(BaseModel):
    project: str
    app: str | None
    view: View
    score: int
    grade: str
    actions: int
    core: int
    excepted: int
    segments: list[SegmentRead]
    apps: list[AppSummary]
    scans: list[ScanRead]
    guarantees: list[Guarantee]


class TrendPoint(BaseModel):
    day: date
    score: int
    grade: str
    core: int
    important: int
    recommended: int
    new_major: int


class ProjectOverview(BaseModel):
    project: str
    score: int
    grade: str
    actions: int
    core: int


def _exception_read(e: SecurityException, tier: Tier | None) -> ExceptionRead:
    pending = e.approved_at is None and (
        tier is Tier.CORE or e.status == rules.Status.ACCEPTED_RISK.value
    )
    return ExceptionRead(
        id=e.id,
        app=e.app,
        fingerprint=e.fingerprint,
        rule=e.rule,
        kind=e.kind,
        status=e.status,
        justification=e.justification,
        statement=e.statement,
        expires_on=e.expires_on,
        author=e.author,
        created_at=e.created_at,
        approved_by=e.approved_by,
        approved_at=e.approved_at,
        revoked_by=e.revoked_by,
        revoked_at=e.revoked_at,
        pending_approval=pending,
    )


def _finding_read(
    m: store.Merged, pending: dict[str, SecurityException]
) -> FindingRead:
    exception = m.exception or pending.get(m.fingerprint)
    return FindingRead(
        fingerprint=m.fingerprint,
        app=m.app,
        category=m.category,
        tier=m.tier,
        audience=m.audience.value,
        rule=m.rule,
        title=m.title,
        detail=m.detail,
        fix=m.fix,
        location=m.location,
        link=m.link,
        raw=m.raw,
        sources=m.sources,
        first_seen=m.first_seen,
        last_seen=m.last_seen,
        resolved_at=m.resolved_at,
        exception=_exception_read(exception, m.tier) if exception else None,
    )


def _scan_read(s: SecurityScan) -> ScanRead:
    return ScanRead(
        app=s.app or None,
        source=s.source,
        status=s.status,
        message=s.message,
        last_run_at=s.last_run_at,
        next_run_at=s.next_run_at,
        collected_at=s.collected_at,
        requested_at=s.requested_at,
    )


# ── Reads ────────────────────────────────────────────────────────────────────


def _guarantees(items: list[store.Merged]) -> list[Guarantee]:
    platform_open = {m.rule for m in items if m.audience.value == "platform"}
    return [
        Guarantee(
            label="Isolé des autres projets",
            ok="network-isolation" not in platform_open,
        ),
        Guarantee(label="Pods privilégiés refusés", ok=True),
    ]


def _summary(
    db: Session, project: str, app: str | None, view: View
) -> Summary:
    today = utcnow().date()
    everything = store.merged_findings(db, project, today)
    items = store.for_audience(everything, platform=view == "platform")
    scoped = [m for m in items if app is None or m.app == app]
    scored = [m.scored() for m in scoped]

    deployment_ids = {
        d.name: d.id
        for d in db.query(Deployment)
        .filter(
            Deployment.project_id == project,
            Deployment.template_id == K8S_TEMPLATE,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .order_by(Deployment.id)
    }
    app_names = sorted(set(deployment_ids) | {m.app for m in items if m.app})
    apps = []
    app_scores = []
    for name in app_names:
        subset = [m.scored() for m in items if m.app == name]
        value = scoring.score(subset)
        app_scores.append(value)
        apps.append(
            AppSummary(
                app=name,
                deployment_id=deployment_ids.get(name),
                score=value,
                grade=scoring.grade(value, scoring.has_core(subset)),
                actions=len(scoring.actionable(subset)),
                core=sum(
                    1
                    for f in scoring.actionable(subset)
                    if f.tier is Tier.CORE
                ),
            )
        )

    if app is None:
        value = scoring.project_score(
            app_scores, [m.scored() for m in items if m.app is None]
        )
    else:
        value = scoring.score(scored)
    open_ = scoring.actionable(scored)
    return Summary(
        project=project,
        app=app,
        view=view,
        score=value,
        grade=scoring.grade(value, scoring.has_core(scored)),
        actions=len(open_),
        core=sum(1 for f in open_ if f.tier is Tier.CORE),
        excepted=sum(1 for m in scoped if m.excepted),
        segments=[SegmentRead(**vars(s)) for s in scoring.segments(scored)],
        apps=apps if app is None else [a for a in apps if a.app == app],
        scans=[_scan_read(s) for s in store.scans(db, project, app)],
        guarantees=_guarantees(everything),
    )


@router.get("/overview", response_model=list[ProjectOverview])
async def get_overview(
    token: CurrentUser, db: Session = Depends(get_db)
) -> list[ProjectOverview]:
    """Grade of every project the caller can see, for lists and badges."""
    if is_cnp_admin(token):
        projects = sorted(
            {
                d.project_id
                for d in db.query(Deployment).filter(
                    Deployment.template_id == K8S_TEMPLATE,
                    Deployment.project_id.isnot(None),
                )
            }
        )
    else:
        projects = sorted(await run_in_threadpool(_user_project_names, token))
    result = []
    for project in projects:
        s = _summary(db, project, None, "developer")
        result.append(
            ProjectOverview(
                project=project,
                score=s.score,
                grade=s.grade,
                actions=s.actions,
                core=s.core,
            )
        )
    return result


@router.get("/summary", response_model=Summary)
async def get_summary(
    token: CurrentUser,
    project: str,
    app: str | None = None,
    view: View = "developer",
    db: Session = Depends(get_db),
) -> Summary:
    await _require_member(token, project, view)
    return _summary(db, project, app, view)


@router.get("/findings", response_model=list[FindingRead])
async def get_findings(
    token: CurrentUser,
    project: str,
    app: str | None = None,
    view: View = "developer",
    category: Category | None = None,
    state: Literal["open", "excepted", "resolved"] = "open",
    include_info: bool = False,
    db: Session = Depends(get_db),
) -> list[FindingRead]:
    await _require_member(token, project, view)
    today = utcnow().date()
    items = store.for_audience(
        store.merged_findings(
            db, project, today, app, include_resolved=state == "resolved"
        ),
        platform=view == "platform",
    )
    if category is not None:
        items = [m for m in items if m.category is category]
    if state == "open":
        items = [m for m in items if not m.excepted and m.resolved_at is None]
        if not include_info:
            items = [m for m in items if m.tier is not Tier.INFO]
    elif state == "excepted":
        items = [m for m in items if m.excepted]
    else:
        items = [m for m in items if m.resolved_at is not None]
    items.sort(key=lambda m: (-store.TIER_RANK[m.tier], m.app or "", m.title))

    pending = {
        e.fingerprint: e for e in store.active_exceptions(db, project).values()
    }
    return [_finding_read(m, pending) for m in items]


@router.get("/trend", response_model=list[TrendPoint])
async def get_trend(
    token: CurrentUser,
    project: str,
    app: str | None = None,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
    db: Session = Depends(get_db),
) -> list[TrendPoint]:
    await _require_member(token, project, "developer")
    return [
        TrendPoint(
            day=s.day,
            score=s.score,
            grade=s.grade,
            core=s.core,
            important=s.important,
            recommended=s.recommended,
            new_major=s.new_major,
        )
        for s in store.snapshots(db, project, app, days, utcnow().date())
    ]


# ── Manual scans and backups ─────────────────────────────────────────────────


class ScanRequest(BaseModel):
    project: str
    app: str
    source: Literal["ci", "trivy-operator"]


async def _github_token(deployment: Deployment) -> tuple[str, str]:
    installation_id = json.loads(deployment.app_config or "{}").get(
        "github_installation_id"
    )
    repo = repo_full_name(deployment.github_repo_url)
    if not installation_id or not repo:
        raise HTTPException(
            status_code=409, detail="This app has no GitHub repository linked."
        )
    try:
        return await get_installation_token(installation_id), repo
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/scans", response_model=ScanRead, status_code=202)
async def request_scan(
    payload: ScanRequest, token: CurrentUser, db: Session = Depends(get_db)
) -> ScanRead:
    """Start a scan now; its result arrives through the collector."""
    await _require_member(token, payload.project, "developer")
    deployment = _app_deployment(db, payload.project, payload.app)
    source = Source(payload.source)
    state = store.scan_state(db, payload.project, payload.app, source)
    now = utcnow()
    if state.requested_at and now - state.requested_at < MANUAL_SCAN_COOLDOWN:
        raise HTTPException(
            status_code=429,
            detail="Un scan a déjà été lancé il y a moins de 15 minutes.",
        )

    if source is Source.CI:
        installation_token, repo = await _github_token(deployment)
        try:
            branch = await get_default_branch(installation_token, repo)
            await dispatch_workflow(
                installation_token, repo, SECURITY_WORKFLOW, branch
            )
        except GitHubAppError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        namespace = f"{payload.project}-{payload.app}"
        try:
            for kind in ("vulnerabilityreports", "exposedsecretreports"):
                await run_in_threadpool(
                    kube_delete_collection,
                    f"/apis/aquasecurity.github.io/v1alpha1/namespaces/{namespace}/{kind}",
                )
        except KubeUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    state.requested_at = now
    db.commit()
    return _scan_read(state)


class BackupRequest(BaseModel):
    project: str
    app: str


@router.post("/backups", status_code=202)
async def request_backup(
    payload: BackupRequest, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    """On-demand backup of every database of the app that has backups enabled."""
    await _require_admin(token, payload.project)
    _app_deployment(db, payload.project, payload.app)
    namespace = f"{payload.project}-{payload.app}"
    try:
        clusters = await run_in_threadpool(
            kube_list,
            f"/apis/postgresql.cnpg.io/v1/namespaces/{namespace}/clusters",
        )
        enabled = [
            c["metadata"]["name"]
            for c in clusters
            if any(
                p.get("name") == "barman-cloud.cloudnative-pg.io"
                for p in (c.get("spec") or {}).get("plugins") or []
            )
        ]
        if not enabled:
            raise HTTPException(
                status_code=409,
                detail="Aucune base de cette app n'a la sauvegarde activée.",
            )
        stamp = utcnow().strftime("%Y%m%d%H%M%S")
        for name in enabled:
            await run_in_threadpool(
                kube_create,
                f"/apis/postgresql.cnpg.io/v1/namespaces/{namespace}/backups",
                {
                    "apiVersion": "postgresql.cnpg.io/v1",
                    "kind": "Backup",
                    "metadata": {
                        "name": f"{name}-manual-{stamp}",
                        "labels": {"cnp.3istor.com/requested-by": "cmp"},
                    },
                    "spec": {
                        "cluster": {"name": name},
                        "method": "plugin",
                        "pluginConfiguration": {
                            "name": "barman-cloud.cloudnative-pg.io"
                        },
                    },
                },
            )
    except KubeUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"backups": [f"{n}-manual-{stamp}" for n in enabled]}


# ── Exceptions ───────────────────────────────────────────────────────────────


async def _sync_exceptions_file(
    db: Session, project: str, app: str | None, author: str
) -> str | None:
    """Rewrite the app's exceptions file; returns the commit sha, if any."""
    if app is None:
        return None
    deployment = _app_deployment(db, project, app)
    installation_token, repo = await _github_token(deployment)
    today = utcnow().date()
    tiers = {
        m.fingerprint: m.tier
        for m in store.merged_findings(
            db, project, today, app, include_resolved=True
        )
    }
    in_force = [
        e
        for e in db.query(SecurityException).filter_by(
            project=project, app=app
        )
        if store.exception_applies(
            e, tiers.get(e.fingerprint, Tier.INFO), today
        )
    ]
    content = rules.render_file(in_force, today)
    try:
        branch = await get_default_branch(installation_token, repo)
        try:
            current, sha = await get_file_content(
                installation_token, repo, rules.EXCEPTIONS_FILE, branch
            )
        except FileNotInRepoError:
            current, sha = None, None
        if current == content:
            return None
        result = await put_file_content(
            installation_token,
            repo,
            rules.EXCEPTIONS_FILE,
            content,
            f"chore(cmp): update security exceptions ({author})",
            sha,
            branch,
        )
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return (result.get("commit") or {}).get("sha")


@router.post(
    "/findings/{fingerprint}/exception",
    response_model=ExceptionRead,
    status_code=201,
)
async def create_exception(
    fingerprint: str,
    payload: rules.ExceptionRequest,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> ExceptionRead:
    await _require_member(token, payload.project, "developer")
    today = utcnow().date()
    finding = next(
        (
            m
            for m in store.merged_findings(db, payload.project, today)
            if m.fingerprint == fingerprint
        ),
        None,
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    if fingerprint in store.active_exceptions(db, payload.project):
        raise HTTPException(
            status_code=409, detail="Cette alerte est déjà ignorée."
        )
    kind = rules.kind_of(finding.category, finding.sources[0])
    try:
        expires_on, _ = rules.validate(payload, kind, finding.tier, today)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    author = _username(token)
    row = SecurityException(
        project=payload.project,
        app=finding.app,
        fingerprint=fingerprint,
        rule=finding.rule,
        kind=kind,
        status=payload.status.value,
        justification=(
            payload.justification.value if payload.justification else None
        ),
        statement=payload.statement,
        expires_on=expires_on,
        author=author,
    )
    db.add(row)
    db.commit()
    row.commit_sha = await _sync_exceptions_file(
        db, payload.project, finding.app, author
    )
    db.commit()
    return _exception_read(row, finding.tier)


def _exception_or_404(db: Session, exception_id: int) -> SecurityException:
    row = db.get(SecurityException, exception_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Exception not found")
    return row


@router.get("/exceptions", response_model=list[ExceptionRead])
async def list_exceptions(
    token: CurrentUser,
    project: str,
    app: str | None = None,
    include_revoked: bool = False,
    db: Session = Depends(get_db),
) -> list[ExceptionRead]:
    await _require_member(token, project, "developer")
    query = db.query(SecurityException).filter_by(project=project)
    if app is not None:
        query = query.filter_by(app=app)
    if not include_revoked:
        query = query.filter(SecurityException.revoked_at.is_(None))
    tiers = {
        m.fingerprint: m.tier
        for m in store.merged_findings(
            db, project, utcnow().date(), app, include_resolved=True
        )
    }
    return [
        _exception_read(e, tiers.get(e.fingerprint))
        for e in query.order_by(SecurityException.created_at.desc())
    ]


@router.post(
    "/exceptions/{exception_id}/approve", response_model=ExceptionRead
)
async def approve_exception(
    exception_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> ExceptionRead:
    row = _exception_or_404(db, exception_id)
    await _require_admin(token, row.project)
    approver = _username(token)
    if approver == row.author:
        raise HTTPException(
            status_code=403,
            detail="Un autre admin du projet que l'auteur doit valider.",
        )
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Exception réactivée.")
    row.approved_by, row.approved_at = approver, utcnow()
    db.commit()
    row.commit_sha = (
        await _sync_exceptions_file(db, row.project, row.app, approver)
        or row.commit_sha
    )
    db.commit()
    return _exception_read(row, None)


@router.delete("/exceptions/{exception_id}", response_model=ExceptionRead)
async def revoke_exception(
    exception_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> ExceptionRead:
    """Reactivate the alert."""
    row = _exception_or_404(db, exception_id)
    await _require_member(token, row.project, "developer")
    if row.revoked_at is None:
        row.revoked_by, row.revoked_at = _username(token), utcnow()
        db.commit()
        await _sync_exceptions_file(db, row.project, row.app, row.revoked_by)
    return _exception_read(row, None)


@router.get("/me")
async def get_my_role(
    token: CurrentUser, project: str
) -> dict[str, str | bool | None]:
    """The caller's role, so the page shows only the buttons they can use."""
    admin = is_cnp_admin(token)
    role = (
        "admin"
        if admin
        else await run_in_threadpool(
            get_project_role, token.get("sub", ""), project
        )
    )
    return {"role": role, "cnp_admin": admin, "username": _username(token)}


# ── Settings and project policy ──────────────────────────────────────────────


class SettingsRead(BaseModel):
    ci_fail_on: app_settings.EffectiveSetting
    policy: app_settings.ProjectPolicy


class SettingsUpdate(BaseModel):
    project: str
    app: str
    ci_fail_on: app_settings.FailOn


class PolicyUpdate(BaseModel):
    project: str
    policy: app_settings.ProjectPolicy


async def _policy(
    project: str,
) -> tuple[app_settings.ProjectPolicy, dict, str | None]:
    try:
        existing = await read_record(project)
    except RegistryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if existing is None:
        return app_settings.ProjectPolicy(), {}, None
    record, sha = existing
    return app_settings.policy_from_record(record), record, sha


async def _write_app_settings(
    deployment: Deployment, fail_on: str, author: str, dry_run: bool
) -> dict[str, str]:
    installation_token, repo = await _github_token(deployment)
    content = app_settings.render_settings(fail_on)
    message = f"chore(cmp): set CI blocking to {fail_on} ({author})"
    try:
        branch = await get_default_branch(installation_token, repo)
        try:
            current, sha = await get_file_content(
                installation_token, repo, app_settings.SETTINGS_FILE, branch
            )
        except FileNotInRepoError:
            current, sha = "", None
        if dry_run:
            return {
                "message": message,
                "diff": "".join(
                    difflib.unified_diff(
                        current.splitlines(keepends=True),
                        content.splitlines(keepends=True),
                        fromfile=f"a/{app_settings.SETTINGS_FILE}",
                        tofile=f"b/{app_settings.SETTINGS_FILE}",
                    )
                ),
            }
        if current == content:
            return {"message": message, "commit": ""}
        result = await put_file_content(
            installation_token,
            repo,
            app_settings.SETTINGS_FILE,
            content,
            message,
            sha,
            branch,
        )
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "message": message,
        "commit": (result.get("commit") or {}).get("sha", ""),
    }


@router.get("/settings", response_model=SettingsRead)
async def get_settings(
    token: CurrentUser,
    project: str,
    app: str,
    db: Session = Depends(get_db),
) -> SettingsRead:
    await _require_member(token, project, "developer")
    deployment = _app_deployment(db, project, app)
    policy, _, _ = await _policy(project)
    installation_token, repo = await _github_token(deployment)
    try:
        branch = await get_default_branch(installation_token, repo)
        try:
            raw, _ = await get_file_content(
                installation_token, repo, app_settings.SETTINGS_FILE, branch
            )
        except FileNotInRepoError:
            raw = None
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SettingsRead(
        ci_fail_on=app_settings.effective_fail_on(
            policy, app_settings.parse_settings(raw)
        ),
        policy=policy,
    )


@router.put("/settings")
async def update_settings(
    payload: SettingsUpdate,
    token: CurrentUser,
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Commit the app's ``deploy/security.yaml``; refused on a locked value."""
    await _require_admin(token, payload.project)
    deployment = _app_deployment(db, payload.project, payload.app)
    policy, _, _ = await _policy(payload.project)
    if policy.ci_fail_on and policy.ci_fail_on.locked:
        raise HTTPException(
            status_code=403,
            detail="Ce réglage est imposé par l'admin du projet.",
        )
    return await _write_app_settings(
        deployment, payload.ci_fail_on.value, _username(token), dry_run
    )


@router.get("/policy", response_model=app_settings.ProjectPolicy)
async def get_policy(
    token: CurrentUser, project: str
) -> app_settings.ProjectPolicy:
    await _require_member(token, project, "developer")
    policy, _, _ = await _policy(project)
    return policy


@router.put("/policy")
async def update_policy(
    payload: PolicyUpdate, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    """
    Set the project's security policy (CNP admins). A locked value is also
    written to every app's settings file, so the CI applies it at once.
    """
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403, detail="The project policy is for CNP admins."
        )
    _, record, sha = await _policy(payload.project)
    if sha is None:
        raise HTTPException(
            status_code=404, detail="This project has no registry record."
        )
    author = _username(token)
    try:
        await write_security_policy(
            payload.project,
            app_settings.policy_to_record(record, payload.policy),
            sha,
            author,
        )
    except RegistryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    updated, failed = [], []
    locked = payload.policy.ci_fail_on
    if locked and locked.locked:
        deployments = (
            db.query(Deployment)
            .filter(
                Deployment.project_id == payload.project,
                Deployment.template_id == K8S_TEMPLATE,
                Deployment.status == DeploymentStatus.RUNNING,
            )
            .all()
        )
        for deployment in deployments:
            try:
                await _write_app_settings(
                    deployment, locked.value.value, author, dry_run=False
                )
                updated.append(deployment.name)
            except HTTPException as exc:
                logger.warning(
                    "Security: policy not written to '%s': %s",
                    deployment.name,
                    exc.detail,
                )
                failed.append(deployment.name)
    return {"updated": updated, "failed": failed}
