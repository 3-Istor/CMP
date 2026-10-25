"""
Environments tab: an app's optional staging environment and its promotion
to prod.

Reading needs any role on the project. Running a suite on staging or asking
for a promotion needs member; turning staging on or off, changing the policy,
approving and running a suite on prod need admin.
"""

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment
from app.models.environment import (
    AppEnvironment,
    Promotion,
    PromotionPolicy,
    VerificationRun,
)
from app.models.project import TargetCloud
from app.routers.deployments import (
    _authorize_app,
    _get_kubernetes_deployment_or_404,
)
from app.schemas.release import _Camel
from app.services import project_registry
from app.services.authz import is_cnp_admin
from app.services.clusters.state import ensure_cluster_up
from app.services.environments import (
    activation,
    gitops,
    layout,
    observe,
    promotion,
    runs,
    suites,
)
from app.services.environments.suites import SuiteSpec
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError

router = APIRouter(prefix="/deployments", tags=["Environments"])
callbacks = APIRouter(prefix="/verification-runs", tags=["Environments"])
projects = APIRouter(prefix="/projects", tags=["Environments"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
Environment = Literal["prod", "staging"]


def _actor(token: dict) -> str:
    return token.get("preferred_username") or token.get("sub") or "unknown"


def _holds(
    db: Session, token: dict, deployment: Deployment, role: ProjectRole
) -> bool:
    try:
        _authorize_app(db, token, deployment, role)
    except HTTPException:
        return False
    return True


def _app(
    db: Session, token: dict, deployment_id: int, role: ProjectRole
) -> Deployment:
    return _get_kubernetes_deployment_or_404(deployment_id, db, token, role)


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


# ── Shapes ───────────────────────────────────────────────────────────────────


class Offhours(_Camel):
    enabled: bool = True
    sleep_at: str = Field(
        default=layout.DEFAULT_STAGING_OFFHOURS["sleepAt"],
        pattern=r"^[0-9*,/\- ]{9,64}$",
    )
    wake_at: str = Field(
        default=layout.DEFAULT_STAGING_OFFHOURS["wakeAt"],
        pattern=r"^[0-9*,/\- ]{9,64}$",
    )
    timezone: str = Field(
        default="Europe/Paris", pattern=r"^[A-Za-z_]+(/[A-Za-z_]+)*$"
    )

    def values(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class EnableRequest(_Camel):
    offhours: Offhours = Offhours()


class EnvironmentRead(_Camel):
    name: Environment
    namespace: str
    hostname: str | None
    version: str | None
    digests: dict[str, str]
    converged: bool
    asleep: bool
    reachable: bool


class StagingRead(_Camel):
    state: str
    detail: str | None
    hostname: str | None
    updated_at: datetime


class PolicyRead(_Camel):
    suites: list[dict]
    soak_minutes: int
    require_approval: bool
    auto_promote: bool
    updated_by: str | None = None
    updated_at: datetime | None = None


class PolicyUpdate(_Camel):
    suites: list[SuiteSpec] = Field(default_factory=list, max_length=10)
    soak_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)
    require_approval: bool = False
    auto_promote: bool = False

    @field_validator("suites")
    @classmethod
    def _valid_suites(cls, value: list[SuiteSpec]) -> list[SuiteSpec]:
        problems = [problem for spec in value for problem in spec.problems()]
        names = [spec.name for spec in value]
        if len(set(names)) != len(names):
            problems.append("two suites have the same name")
        if problems:
            raise ValueError("; ".join(problems))
        return value


class RunRead(_Camel):
    id: int
    environment: str
    suite: str
    kind: str
    version: str | None
    digests: dict
    status: str
    summary: str | None
    details_url: str | None
    measurements: dict | None
    promotion_id: int | None
    actor: str
    started_at: datetime
    finished_at: datetime | None


class RunRequest(_Camel):
    suite: str


class RunResult(_Camel):
    passed: bool
    summary: str = Field(default="", max_length=500)
    details_url: str | None = Field(
        default=None, max_length=512, pattern=r"^https://"
    )


class PromotionRead(_Camel):
    id: int
    source_env: str
    target_env: str
    version: str | None
    digests: dict
    status: str
    steps: list[dict]
    emergency: bool
    reason: str | None
    rollback_of: int | None
    approvals: list[dict]
    actor: str
    commit_sha: str | None
    detail: str | None
    created_at: datetime
    finished_at: datetime | None


class PromotionRequest(_Camel):
    emergency: bool = False
    reason: str | None = Field(default=None, max_length=500)
    # Go back to the release of an earlier promotion that reached prod.
    rollback_to: int | None = None


class EnvironmentsRead(_Camel):
    staging: StagingRead | None
    environments: list[EnvironmentRead]
    policy: PolicyRead
    active_promotion: PromotionRead | None
    can_operate: bool
    can_administer: bool


class ProjectStagingUpdate(_Camel):
    max_apps: int = Field(ge=0, le=20)


# ── Reading ──────────────────────────────────────────────────────────────────


def _observe(
    deployment: Deployment, environment: str, hostname: str | None
) -> EnvironmentRead:
    try:
        state = observe.of(deployment, environment)
    except KubeUnavailableError:
        return EnvironmentRead(
            name=environment,
            namespace=layout.namespace(
                deployment.project_id, deployment.name, environment
            ),
            hostname=hostname,
            version=None,
            digests={},
            converged=False,
            asleep=False,
            reachable=False,
        )
    return EnvironmentRead(
        name=environment,
        namespace=state.namespace,
        hostname=hostname,
        version=state.version,
        digests=state.digests,
        converged=state.converged,
        asleep=state.asleep,
        reachable=True,
    )


def _policy_read(policy: PromotionPolicy) -> PolicyRead:
    return PolicyRead(
        suites=policy.suites or [],
        soak_minutes=policy.soak_minutes or 0,
        require_approval=bool(policy.require_approval),
        auto_promote=bool(policy.auto_promote),
        updated_by=policy.updated_by,
        updated_at=policy.updated_at,
    )


@router.get("/{deployment_id}/environments", response_model=EnvironmentsRead)
def get_environments(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
):
    deployment = _app(db, token, deployment_id, ProjectRole.GUEST)
    row = db.get(AppEnvironment, (deployment.id, "staging"))
    prod_host = runs.public_url(db, deployment, "prod")
    environments = [
        _observe(
            deployment,
            "prod",
            prod_host and prod_host.removeprefix("https://").rstrip("/"),
        )
    ]
    if row is not None and row.state == "ready":
        environments.insert(0, _observe(deployment, "staging", row.hostname))
    active = promotion.active_promotion(db, deployment.id)
    return EnvironmentsRead(
        staging=(
            StagingRead.model_validate(row, from_attributes=True)
            if row
            else None
        ),
        environments=environments,
        policy=_policy_read(promotion.policy_of(db, deployment.id)),
        active_promotion=(
            PromotionRead.model_validate(active, from_attributes=True)
            if active
            else None
        ),
        can_operate=_holds(db, token, deployment, ProjectRole.MEMBER),
        can_administer=_holds(db, token, deployment, ProjectRole.ADMIN),
    )


# ── Turning staging on and off ───────────────────────────────────────────────


@router.post(
    "/{deployment_id}/environments/staging",
    response_model=StagingRead,
    status_code=202,
)
async def enable_staging(
    deployment_id: int,
    payload: EnableRequest,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.ADMIN
    )
    await ensure_cluster_up(TargetCloud(gitops.target_cloud(deployment)))
    try:
        row = activation.start_enable(
            db, deployment, payload.offhours.values()
        )
    except activation.ActivationRefused as exc:
        raise _conflict(exc) from exc
    return StagingRead.model_validate(row, from_attributes=True)


@router.delete(
    "/{deployment_id}/environments/staging",
    response_model=StagingRead,
    status_code=202,
)
async def disable_staging(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
):
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.ADMIN
    )
    try:
        row = await activation.start_disable(db, deployment)
    except (activation.ActivationRefused, gitops.GitOpsError) as exc:
        raise _conflict(exc) from exc
    return StagingRead.model_validate(row, from_attributes=True)


@router.get(
    "/{deployment_id}/environments/staging/offhours", response_model=Offhours
)
async def get_staging_offhours(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
):
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.GUEST
    )
    try:
        return Offhours.model_validate(
            await activation.get_offhours(deployment)
        )
    except (activation.ActivationRefused, gitops.GitOpsError) as exc:
        raise _conflict(exc) from exc


@router.put("/{deployment_id}/environments/staging/offhours")
async def update_staging_offhours(
    deployment_id: int,
    payload: Offhours,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.ADMIN
    )
    row = db.get(AppEnvironment, (deployment.id, "staging"))
    if row is None or row.state != "ready":
        raise HTTPException(
            status_code=409, detail="This app has no staging environment."
        )
    try:
        sha = await activation.set_offhours(deployment, payload.values())
    except (activation.ActivationRefused, gitops.GitOpsError) as exc:
        raise _conflict(exc) from exc
    return {"commit": sha}


@router.post("/{deployment_id}/environments/staging/wake")
async def wake_staging(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, int]:
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.MEMBER
    )
    namespace = layout.namespace(
        deployment.project_id, deployment.name, "staging"
    )
    try:
        woken = await asyncio.to_thread(observe.wake, namespace)
    except KubeUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"Cluster unreachable: {exc}"
        ) from exc
    return {"woken": woken}


# ── Promotion policy ─────────────────────────────────────────────────────────


@router.get("/{deployment_id}/promotion-policy", response_model=PolicyRead)
def get_policy(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
):
    deployment = _app(db, token, deployment_id, ProjectRole.GUEST)
    return _policy_read(promotion.policy_of(db, deployment.id))


@router.put("/{deployment_id}/promotion-policy", response_model=PolicyRead)
def update_policy(
    deployment_id: int,
    payload: PolicyUpdate,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = _app(db, token, deployment_id, ProjectRole.ADMIN)
    policy = db.get(PromotionPolicy, deployment.id) or PromotionPolicy(
        deployment_id=deployment.id
    )
    policy.suites = [
        spec.model_dump(by_alias=True, exclude_none=True)
        for spec in payload.suites
    ]
    policy.soak_minutes = payload.soak_minutes
    policy.require_approval = payload.require_approval
    policy.auto_promote = payload.auto_promote
    policy.updated_by = _actor(token)
    policy.updated_at = runs.now()
    db.add(policy)
    db.commit()
    return _policy_read(policy)


# ── Verification runs ────────────────────────────────────────────────────────


def _suite(db: Session, deployment_id: int, name: str) -> dict:
    if name == suites.SMOKE:
        return dict(suites.SMOKE_SPEC)
    for spec in promotion.policy_of(db, deployment_id).suites or []:
        if spec["name"] == name:
            return spec
    raise HTTPException(status_code=404, detail=f"No suite named '{name}'.")


@router.post(
    "/{deployment_id}/environments/{environment}/runs",
    response_model=RunRead,
    status_code=202,
)
async def start_run(
    deployment_id: int,
    environment: Environment,
    payload: RunRequest,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    role = ProjectRole.ADMIN if environment == "prod" else ProjectRole.MEMBER
    deployment = await run_in_threadpool(_app, db, token, deployment_id, role)
    spec = _suite(db, deployment.id, payload.suite)
    if environment == "prod" and not spec.get("prodSafe"):
        raise HTTPException(
            status_code=403,
            detail=f"Suite '{spec['name']}' may write data: it is not marked safe for prod.",
        )
    if environment == "staging":
        row = db.get(AppEnvironment, (deployment.id, "staging"))
        if row is None or row.state != "ready":
            raise HTTPException(
                status_code=409, detail="This app has no staging environment."
            )
    try:
        run = await runs.start(
            db, deployment, environment, spec, _actor(token)
        )
    except runs.RunRefused as exc:
        raise _conflict(exc) from exc
    except KubeUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"Cluster unreachable: {exc}"
        ) from exc
    return run


@router.get(
    "/{deployment_id}/environments/{environment}/runs",
    response_model=list[RunRead],
)
def list_runs(
    deployment_id: int,
    environment: Environment,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = _app(db, token, deployment_id, ProjectRole.GUEST)
    return (
        db.query(VerificationRun)
        .filter_by(deployment_id=deployment.id, environment=environment)
        .order_by(VerificationRun.started_at.desc())
        .limit(50)
        .all()
    )


def _run(db: Session, deployment: Deployment, run_id: int) -> VerificationRun:
    run = db.get(VerificationRun, run_id)
    if run is None or run.deployment_id != deployment.id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{deployment_id}/runs/{run_id}", response_model=RunRead)
def get_run(
    deployment_id: int,
    run_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    return _run(db, _app(db, token, deployment_id, ProjectRole.GUEST), run_id)


@router.post("/{deployment_id}/runs/{run_id}/cancel", response_model=RunRead)
def cancel_run(
    deployment_id: int,
    run_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    run = _run(db, _app(db, token, deployment_id, ProjectRole.MEMBER), run_id)
    if run.status != "running":
        raise HTTPException(
            status_code=409, detail="This run is already over."
        )
    runs.finish(
        run, suites.Outcome("cancelled", f"Cancelled by {_actor(token)}")
    )
    db.commit()
    return run


@callbacks.post("/{run_id}/result", response_model=dict)
def post_run_result(
    run_id: int,
    payload: RunResult,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """
    Result of an external suite, posted by the app's workflow with the
    one-time token the CMP gave it. Not a user call: no Keycloak token here.
    """
    run = db.get(VerificationRun, run_id)
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if (
        run is None
        or not run.token_hash
        or not presented
        or suites.hash_token(presented) != run.token_hash
    ):
        raise HTTPException(status_code=403, detail="Unknown run or token.")
    if run.status != "running":
        raise HTTPException(
            status_code=409, detail="This run is already over."
        )
    runs.finish(
        run,
        suites.Outcome(
            "passed" if payload.passed else "failed",
            payload.summary or ("passed" if payload.passed else "failed"),
        ),
    )
    run.details_url = payload.details_url
    db.commit()
    return {"status": run.status}


# ── Promotions ───────────────────────────────────────────────────────────────


def _promotion(
    db: Session, deployment: Deployment, promotion_id: int
) -> Promotion:
    item = db.get(Promotion, promotion_id)
    if item is None or item.deployment_id != deployment.id:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return item


@router.post(
    "/{deployment_id}/promotions",
    response_model=PromotionRead,
    status_code=201,
)
async def start_promotion(
    deployment_id: int,
    payload: PromotionRequest,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.MEMBER
    )
    is_admin = await run_in_threadpool(
        _holds, db, token, deployment, ProjectRole.ADMIN
    )
    if payload.emergency and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only a project admin can start an emergency promotion.",
        )
    rollback_of = None
    if payload.rollback_to is not None:
        rollback_of = _promotion(db, deployment, payload.rollback_to)
        if rollback_of.status != "done" or rollback_of.target_env != "prod":
            raise HTTPException(
                status_code=409,
                detail="Only a release that reached prod can be gone back to.",
            )
    elif db.get(AppEnvironment, (deployment.id, "staging")) is None:
        raise HTTPException(
            status_code=409,
            detail="This app has no staging environment to promote from.",
        )
    await ensure_cluster_up(TargetCloud(gitops.target_cloud(deployment)))
    try:
        item = await promotion.start(
            db,
            deployment,
            _actor(token),
            is_admin,
            emergency=payload.emergency,
            reason=payload.reason,
            rollback_of=rollback_of,
        )
        item = await promotion.advance(db, item)
    except promotion.PromotionRefused as exc:
        raise _conflict(exc) from exc
    except KubeUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"Cluster unreachable: {exc}"
        ) from exc
    return item


@router.get("/{deployment_id}/promotions", response_model=list[PromotionRead])
def list_promotions(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
):
    deployment = _app(db, token, deployment_id, ProjectRole.GUEST)
    return (
        db.query(Promotion)
        .filter_by(deployment_id=deployment.id)
        .order_by(Promotion.created_at.desc())
        .limit(50)
        .all()
    )


@router.get(
    "/{deployment_id}/promotions/{promotion_id}", response_model=PromotionRead
)
def get_promotion(
    deployment_id: int,
    promotion_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    return _promotion(
        db, _app(db, token, deployment_id, ProjectRole.GUEST), promotion_id
    )


@router.post(
    "/{deployment_id}/promotions/{promotion_id}/approve",
    response_model=PromotionRead,
)
async def approve_promotion(
    deployment_id: int,
    promotion_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.ADMIN
    )
    item = _promotion(db, deployment, promotion_id)
    try:
        promotion.approve(item, _actor(token), is_cnp_admin(token))
        db.commit()
        return await promotion.advance(db, item)
    except promotion.PromotionRefused as exc:
        raise _conflict(exc) from exc


@router.post(
    "/{deployment_id}/promotions/{promotion_id}/steps/{index}/accept",
    response_model=PromotionRead,
)
async def accept_step(
    deployment_id: int,
    promotion_id: int,
    index: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = await run_in_threadpool(
        _app, db, token, deployment_id, ProjectRole.ADMIN
    )
    item = _promotion(db, deployment, promotion_id)
    try:
        promotion.accept_step(item, index, _actor(token))
        db.commit()
        return await promotion.advance(db, item)
    except promotion.PromotionRefused as exc:
        raise _conflict(exc) from exc


@router.post(
    "/{deployment_id}/promotions/{promotion_id}/cancel",
    response_model=PromotionRead,
)
def cancel_promotion(
    deployment_id: int,
    promotion_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = _app(db, token, deployment_id, ProjectRole.MEMBER)
    item = _promotion(db, deployment, promotion_id)
    try:
        promotion.cancel(item, _actor(token))
    except promotion.PromotionRefused as exc:
        raise _conflict(exc) from exc
    db.commit()
    return item


# ── Project: allowing staging ────────────────────────────────────────────────


@projects.put("/{project_name}/environments/staging")
async def set_project_staging(
    project_name: str, payload: ProjectStagingUpdate, token: CurrentUser
) -> dict[str, int]:
    """How many apps of a project may run staging. Platform admins only: it costs RAM."""
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403, detail="Only a platform admin can change this."
        )
    try:
        await project_registry.set_project_staging(
            project_name, payload.max_apps
        )
    except project_registry.RegistryError as exc:
        raise _conflict(exc) from exc
    return {"maxApps": payload.max_apps}
