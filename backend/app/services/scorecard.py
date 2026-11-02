"""
Score of an app over five criteria, from data the backend already holds.

Every criterion is worth 20 points, the total is out of 100. Computed on
read, cached in memory for five minutes per app.

| Criterion      | Rule                                                              |
|----------------|-------------------------------------------------------------------|
| security       | 20, -10 per open critical (core) finding, -4 per open high        |
|                | (important) finding, floor 0. Excepted findings do not count.     |
|                | No scan yet: 0, reason no_scan.                                   |
| cost           | 20 when the project has a budget and the app's month-to-date      |
|                | cost is at most budget * day / days in month. Over that pace: 0,  |
|                | reason over_budget_pace. No budget: 10, reason no_budget. Then    |
|                | -5 per open FinOps recommendation (pending or notified), floor 0. |
| reliability    | healthy 20, asleep 15, degraded 10, down 0, unknown or            |
|                | not_connected 10 with that reason. Then -5 when the app restarted |
|                | more than 5 times in 15 minutes, floor 0.                         |
| documentation  | 10 for a README.md at the repo root, plus 10 when it has more     |
|                | than 20 lines. No docs URL field exists on apps or templates, so  |
|                | the README length stands in for it.                               |
| owner          | 10 with reason project_owner. Apps have no owner field, so only   |
|                | the project owner is known; 20 is reserved for an app owner.      |

A criterion whose source fails gets 0 with reason source_unavailable.
Grade: A >= 85, B >= 70, C >= 50, D >= 30, else E.
"""

import calendar
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.deployment import Deployment
from app.models.finops import ProjectBudget, RecommendationState
from app.models.project import Project
from app.models.security import SecurityScan
from app.services import app_health
from app.services.finops.provider import get_cost_provider
from app.services.security import store
from app.services.security.model import Tier

logger = logging.getLogger(__name__)

CRITERION_MAX = 20
CACHE_TTL_SECONDS = 300
RESTARTS_PENALTY_THRESHOLD = 5
README_DETAILED_LINES = 20
OPEN_RECOMMENDATION_STATUSES = ("pending", "notified")
HEALTH_POINTS = {"healthy": 20, "asleep": 15, "degraded": 10, "down": 0}
HEALTH_UNCERTAIN_POINTS = 10
GRADE_THRESHOLDS = (("A", 85), ("B", 70), ("C", 50), ("D", 30))

Grade = Literal["A", "B", "C", "D", "E"]


class Criterion(BaseModel):
    key: str
    points: int
    max: int = CRITERION_MAX
    reasons: list[str]
    actions: list[str]


class Scorecard(BaseModel):
    score: int
    max: int
    grade: Grade
    criteria: list[Criterion]


@dataclass
class SecurityInput:
    scanned: bool
    critical: int
    high: int


@dataclass
class CostInput:
    month_to_date_eur: float
    budget_eur: float | None
    open_recommendations: int


@dataclass
class HealthInput:
    status: str
    restarts_15m: int


def grade_of(score: int) -> Grade:
    for letter, minimum in GRADE_THRESHOLDS:
        if score >= minimum:
            return letter  # type: ignore[return-value]
    return "E"


def _bounded(points: int) -> int:
    return max(0, min(CRITERION_MAX, points))


def unavailable(key: str) -> Criterion:
    return Criterion(
        key=key, points=0, reasons=["source_unavailable"], actions=[]
    )


def security_criterion(data: SecurityInput) -> Criterion:
    if not data.scanned:
        return Criterion(
            key="security",
            points=0,
            reasons=["no_scan"],
            actions=["run_scan"],
        )
    reasons = []
    actions = []
    if data.critical:
        reasons.append("critical_findings")
        actions.append("fix_critical")
    if data.high:
        reasons.append("high_findings")
        actions.append("fix_high")
    return Criterion(
        key="security",
        points=_bounded(CRITERION_MAX - 10 * data.critical - 4 * data.high),
        reasons=reasons,
        actions=actions,
    )


def budget_pace_eur(budget_eur: float, today: date) -> float:
    days = calendar.monthrange(today.year, today.month)[1]
    return budget_eur * today.day / days


def cost_criterion(data: CostInput, today: date) -> Criterion:
    reasons = []
    actions = []
    if data.budget_eur is None:
        points = 10
        reasons.append("no_budget")
        actions.append("set_budget")
    elif data.month_to_date_eur <= budget_pace_eur(data.budget_eur, today):
        points = CRITERION_MAX
    else:
        points = 0
        reasons.append("over_budget_pace")
        actions.append("reduce_cost")
    if data.open_recommendations:
        points -= 5 * data.open_recommendations
        reasons.append("open_recommendations")
        actions.append("apply_recommendations")
    return Criterion(
        key="cost", points=_bounded(points), reasons=reasons, actions=actions
    )


def reliability_criterion(data: HealthInput) -> Criterion:
    reasons = []
    actions = []
    if data.status in HEALTH_POINTS:
        points = HEALTH_POINTS[data.status]
        if data.status != "healthy":
            reasons.append(data.status)
        if data.status in ("degraded", "down"):
            actions.append("fix_health")
    else:
        points = HEALTH_UNCERTAIN_POINTS
        reasons.append(
            "not_connected" if data.status == "not_connected" else "unknown"
        )
    if data.restarts_15m > RESTARTS_PENALTY_THRESHOLD:
        points -= 5
        reasons.append("restarts")
        actions.append("fix_restarts")
    return Criterion(
        key="reliability",
        points=_bounded(points),
        reasons=reasons,
        actions=actions,
    )


def documentation_criterion(readme: str | None) -> Criterion:
    if readme is None:
        return Criterion(
            key="documentation",
            points=0,
            reasons=["no_readme"],
            actions=["add_readme"],
        )
    if len(readme.splitlines()) > README_DETAILED_LINES:
        return Criterion(
            key="documentation", points=20, reasons=[], actions=[]
        )
    return Criterion(
        key="documentation",
        points=10,
        reasons=["readme_short"],
        actions=["expand_readme"],
    )


def owner_criterion(project_owner: str | None) -> Criterion:
    if not project_owner:
        return Criterion(
            key="owner", points=0, reasons=["no_owner"], actions=[]
        )
    return Criterion(
        key="owner", points=10, reasons=["project_owner"], actions=[]
    )


def assemble(criteria: list[Criterion]) -> Scorecard:
    total = sum(c.points for c in criteria)
    return Scorecard(
        score=total,
        max=CRITERION_MAX * len(criteria),
        grade=grade_of(total),
        criteria=criteria,
    )


def _read_security(db: Session, deployment: Deployment) -> SecurityInput:
    project = deployment.project_id or ""
    scanned = any(
        (row.last_run_at or row.collected_at) is not None
        for row in db.query(SecurityScan).filter(
            SecurityScan.project == project,
            SecurityScan.app == deployment.name,
        )
    )
    items = store.for_audience(
        store.merged_findings(db, project, date.today(), app=deployment.name),
        platform=False,
    )
    open_ = [m for m in items if not m.excepted]
    return SecurityInput(
        scanned=scanned,
        critical=sum(1 for m in open_ if m.tier is Tier.CORE),
        high=sum(1 for m in open_ if m.tier is Tier.IMPORTANT),
    )


def _read_costs(deployment: Deployment) -> tuple[float, list[str]]:
    provider = get_cost_provider()
    specs = provider.specs([deployment])
    spent = sum(
        row["month_to_date_eur"]
        for row in provider.app_rows(specs)
        if row["app_id"] == deployment.id
    )
    return spent, [rec.id for rec in provider.recommendations(specs)]


async def _read_cost(db: Session, deployment: Deployment) -> CostInput:
    spent, recommendation_ids = await run_in_threadpool(
        _read_costs, deployment
    )
    closed = {
        state.rec_id
        for state in db.query(RecommendationState).filter(
            RecommendationState.rec_id.in_(recommendation_ids),
            RecommendationState.status.notin_(OPEN_RECOMMENDATION_STATUSES),
        )
    }
    budget = (
        db.query(ProjectBudget)
        .filter(ProjectBudget.project_name == deployment.project_id)
        .first()
    )
    return CostInput(
        month_to_date_eur=spent,
        budget_eur=(budget.monthly_amount_eur or None) if budget else None,
        open_recommendations=len(set(recommendation_ids) - closed),
    )


async def _read_health(deployment: Deployment) -> HealthInput:
    health = await app_health.get_kubernetes_app_health(deployment)
    return HealthInput(
        status=health.status,
        restarts_15m=sum(c.restarts_15m or 0 for c in health.components),
    )


def _read_project_owner(db: Session, deployment: Deployment) -> str | None:
    project = (
        db.query(Project)
        .filter(Project.project_name == deployment.project_id)
        .first()
    )
    return project.owner_username if project else None


ReadmeReader = Callable[[], Awaitable[str | None]]

_cache: dict[int, tuple[float, Scorecard]] = {}


def clear_cache() -> None:
    _cache.clear()


async def _guarded(
    key: str, compute: Callable[[], Awaitable[Criterion]]
) -> Criterion:
    try:
        return await compute()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Scorecard source %s unavailable: %s", key, exc)
        return unavailable(key)


async def compute_scorecard(
    db: Session,
    deployment: Deployment,
    read_readme: ReadmeReader,
    today: date | None = None,
) -> Scorecard:
    today = today or date.today()

    async def security() -> Criterion:
        return security_criterion(_read_security(db, deployment))

    async def cost() -> Criterion:
        return cost_criterion(await _read_cost(db, deployment), today)

    async def reliability() -> Criterion:
        return reliability_criterion(await _read_health(deployment))

    async def documentation() -> Criterion:
        return documentation_criterion(await read_readme())

    async def owner() -> Criterion:
        return owner_criterion(_read_project_owner(db, deployment))

    return assemble(
        [
            await _guarded("security", security),
            await _guarded("cost", cost),
            await _guarded("reliability", reliability),
            await _guarded("documentation", documentation),
            await _guarded("owner", owner),
        ]
    )


def _now() -> float:
    return time.monotonic()


async def scorecard_for(
    db: Session, deployment: Deployment, read_readme: ReadmeReader
) -> Scorecard:
    cached = _cache.get(deployment.id)
    if cached and _now() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    card = await compute_scorecard(db, deployment, read_readme)
    if not any("source_unavailable" in c.reasons for c in card.criteria):
        _cache[deployment.id] = (_now(), card)
    return card
