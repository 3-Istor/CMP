"""
Background FinOps alert poller.

Mirrors the health-poller pattern: a long-lived asyncio loop (started from the
app lifespan) that, every few minutes:

  * evaluates each project budget against month-to-date simulated spend and
    raises a Discord alert + history row when its warn / critical threshold
    (70% / 90% by default) is newly
    crossed (``BudgetAlertState`` prevents repeat spam), and
  * flags anomalous consumption spikes per application.

With real costs it also warns once a month when a project's month-end
forecast crosses its budget, and spike detection reads the cost store
(:mod:`.anomalies`) instead of the simulation.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.finops import BudgetAlertState, CostAlert, ProjectBudget
from app.services import discord_service
from app.services.finops import anomalies, usage
from app.services.finops.forecast import forecast_for_apps
from app.services.finops.provider import get_cost_provider

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 180
# Recent days re-checked for anomalies: ingestion revises the last few days.
ANOMALY_LOOKBACK_DAYS = 3

_ACTIVE = [s for s in DeploymentStatus if s != DeploymentStatus.DELETED]


def _project_deployments(db, project_name: str) -> list[Deployment]:
    return (
        db.query(Deployment)
        .filter(Deployment.status.in_(_ACTIVE))
        .filter(Deployment.project_id == project_name)
        .all()
    )


def _level_for(consumed_pct: float, warn: int, critical: int) -> int:
    if consumed_pct >= critical:
        return 2
    if consumed_pct >= warn:
        return 1
    return 0


async def check_budgets() -> None:
    """Raise alerts for projects newly crossing a budget threshold."""
    db = SessionLocal()
    try:
        provider = get_cost_provider()
        budgets = (
            db.query(ProjectBudget)
            .filter(ProjectBudget.monthly_amount_eur > 0)
            .all()
        )
        for budget in budgets:
            deployments = _project_deployments(db, budget.project_name)
            spent = provider.summary(provider.specs(deployments))[
                "month_to_date_eur"
            ]
            consumed_pct = spent / budget.monthly_amount_eur * 100
            level = _level_for(
                consumed_pct, budget.threshold_warn, budget.threshold_critical
            )

            state = _budget_state(db, budget.project_name)

            # Only alert when severity increases (avoid repeat notifications).
            if level > state.last_level:
                level_name = "critical" if level == 2 else "warning"
                message = (
                    f"Budget consommé à {consumed_pct:.0f}% "
                    f"({spent:.2f} € / {budget.monthly_amount_eur:.2f} €)."
                )
                db.add(
                    CostAlert(
                        project_name=budget.project_name,
                        level=level_name,
                        kind="budget",
                        message=message,
                        value_pct=round(consumed_pct, 1),
                    )
                )
                db.commit()
                await discord_service.send_budget_alert(
                    project_name=budget.project_name,
                    level=level_name,
                    consumed_pct=consumed_pct,
                    spent_eur=spent,
                    budget_eur=budget.monthly_amount_eur,
                )
                logger.info(
                    "FinOps budget alert: %s at %.0f%% (level %d)",
                    budget.project_name,
                    consumed_pct,
                    level,
                )

            if level != state.last_level:
                state.last_level = level
                db.commit()
    except Exception as exc:
        logger.error("Error checking FinOps budgets: %s", exc, exc_info=True)
    finally:
        db.close()


def _budget_state(db, project_name: str) -> BudgetAlertState:
    state = (
        db.query(BudgetAlertState)
        .filter(BudgetAlertState.project_name == project_name)
        .first()
    )
    if state is None:
        state = BudgetAlertState(project_name=project_name, last_level=0)
        db.add(state)
        db.commit()
    return state


async def check_forecasts(today: date | None = None) -> None:
    """Warn once a month per project whose forecast crosses its budget."""
    if settings.FINOPS_PROVIDER != "real":
        return
    today = today or date.today()
    month = today.strftime("%Y-%m")
    db = SessionLocal()
    try:
        provider = get_cost_provider()
        budgets = (
            db.query(ProjectBudget)
            .filter(ProjectBudget.monthly_amount_eur > 0)
            .all()
        )
        for budget in budgets:
            state = _budget_state(db, budget.project_name)
            if state.forecast_alerted_month == month:
                continue
            apps = provider.specs(
                _project_deployments(db, budget.project_name)
            )
            forecast = forecast_for_apps(
                provider, apps, today, budget.monthly_amount_eur
            )
            overrun = forecast["overrun_date"]
            # Already past: the budget alert has said so.
            if overrun is None or overrun < today:
                continue
            db.add(
                CostAlert(
                    project_name=budget.project_name,
                    level="warning",
                    kind="forecast",
                    message=(
                        f"Dépassement prévu le {overrun:%d/%m/%Y} : "
                        f"prévision {forecast['projected_eur']:.2f} € "
                        f"({forecast['low_eur']:.2f} – "
                        f"{forecast['high_eur']:.2f} €) pour un budget de "
                        f"{budget.monthly_amount_eur:.2f} €."
                    ),
                    value_pct=round(
                        forecast["projected_eur"]
                        / budget.monthly_amount_eur
                        * 100,
                        1,
                    ),
                    subject_day=overrun,
                )
            )
            state.forecast_alerted_month = month
            db.commit()
            await discord_service.send_forecast_alert(
                project_name=budget.project_name,
                overrun_date=f"{overrun:%d/%m/%Y}",
                projected_eur=forecast["projected_eur"],
                low_eur=forecast["low_eur"],
                high_eur=forecast["high_eur"],
                budget_eur=budget.monthly_amount_eur,
            )
            logger.info(
                "FinOps forecast alert: %s overruns on %s",
                budget.project_name,
                overrun,
            )
    except Exception as exc:
        logger.error("Error checking FinOps forecasts: %s", exc, exc_info=True)
    finally:
        db.close()


async def check_anomalies(today: date | None = None) -> None:
    """Store and notify cost anomalies of the last few ingested days."""
    today = today or date.today()
    db = SessionLocal()
    try:
        deployments = (
            db.query(Deployment)
            .filter(Deployment.status.in_(_ACTIVE))
            .filter(Deployment.project_id.isnot(None))
            .all()
        )
        found = anomalies.find_anomalies(
            db,
            get_cost_provider(),
            deployments,
            today - timedelta(days=ANOMALY_LOOKBACK_DAYS),
            today - timedelta(days=1),
        )
        for row in anomalies.record_alerts(db, found):
            await discord_service.send_cost_anomaly(
                project_name=row["project_id"],
                app_name=row["app_name"],
                day=f"{row['date']:%d/%m/%Y}",
                cost_eur=row["cost_eur"],
                baseline_eur=row["baseline_eur"],
                cause=row["cause"],
            )
            logger.info(
                "FinOps cost anomaly for %s on %s",
                row["app_name"],
                row["date"],
            )
    except Exception as exc:
        logger.error("Error checking FinOps anomalies: %s", exc, exc_info=True)
    finally:
        db.close()


async def check_spikes() -> None:
    """Flag apps with a simulated consumption spike today (see usage.has_spike)."""
    if settings.FINOPS_PROVIDER == "real":
        await check_anomalies()
        return
    db = SessionLocal()
    try:
        today = date.today()
        since = datetime.utcnow() - timedelta(hours=24)
        deployments = (
            db.query(Deployment)
            .filter(Deployment.status.in_(_ACTIVE))
            .filter(Deployment.project_id.isnot(None))
            .all()
        )
        for deployment in deployments:
            spiking = [
                r
                for r in ("cpu", "ram", "network")
                if usage.has_spike(int(deployment.id), r, today)
            ]
            if not spiking:
                continue
            # De-dupe: at most one spike alert per app per 24h.
            existing = (
                db.query(CostAlert)
                .filter(CostAlert.app_id == deployment.id)
                .filter(CostAlert.kind == "spike")
                .filter(CostAlert.triggered_at >= since)
                .first()
            )
            if existing:
                continue
            db.add(
                CostAlert(
                    project_name=deployment.project_id,
                    app_id=int(deployment.id),
                    level="warning",
                    kind="spike",
                    message=(
                        f"Pic de consommation anormal détecté sur "
                        f"{', '.join(spiking)} (pic simulé)."
                    ),
                    value_pct=None,
                )
            )
            db.commit()
            logger.info(
                "FinOps spike alert for app %s (%s)", deployment.name, spiking
            )
    except Exception as exc:
        logger.error("Error checking FinOps spikes: %s", exc, exc_info=True)
    finally:
        db.close()


async def _budgets_then_forecasts() -> None:
    # Sequential: both may create the project's BudgetAlertState row.
    await check_budgets()
    await check_forecasts()


async def finops_alert_loop() -> None:
    """Main FinOps alert loop (budgets + spikes) every POLL_INTERVAL_SECONDS."""
    logger.info("FinOps alert poller started")
    while True:
        try:
            await asyncio.gather(
                _budgets_then_forecasts(),
                check_spikes(),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.error("Error in FinOps alert loop: %s", exc, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
