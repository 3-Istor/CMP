"""
Monthly FinOps report: on the 5th (Europe/Paris) the summary of the previous
month goes to the platform Discord, once. The last month reported is kept in
``finops_state`` so a restart cannot send it twice.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.finops import FinopsState
from app.services import discord_service
from app.services.finops import finance

logger = logging.getLogger(__name__)

STATE_KEY = "monthly_report_last_month"
REPORT_DAY = 5
CHECK_INTERVAL_SECONDS = 3600
_PARIS = ZoneInfo("Europe/Paris")


def _euro(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " €"


def build_report(db: Session, period: str) -> dict:
    lines = finance.month_lines(db, period)
    avoided, realized = finance.savings_totals(
        finance.month_savings(db, period)
    )
    return {
        "period": period,
        "total": round(sum(line.x_cost_eur or 0.0 for line in lines), 2),
        "providers": finance.provider_sums(lines),
        "projects": [
            p
            for p in finance.totals_by_project(lines)
            if not finance.is_unallocated(p[0]) and p[0] != "(non alloué)"
        ][:5],
        "avoided": avoided,
        "realized": realized,
        "unallocated_pct": finance.unallocated_pct(lines),
    }


def format_report(report: dict) -> str:
    providers = "\n".join(
        f"- {name} : {_euro(cost)}" for name, cost in report["providers"]
    )
    projects = "\n".join(
        f"{rank}. {name} : {_euro(cost)}"
        for rank, (name, cost) in enumerate(report["projects"], start=1)
    )
    link = f"{settings.CMP_PUBLIC_URL.rstrip('/')}/finops/finance"
    return (
        f"**Rapport FinOps {report['period']}**\n"
        f"Total : {_euro(report['total'])}\n\n"
        f"**Par fournisseur**\n{providers or '- aucun coût'}\n\n"
        f"**Top 5 projets**\n{projects or '- aucun projet'}\n\n"
        f"**Économies** : {_euro(report['avoided'])} évités, "
        f"{_euro(report['realized'])} réalisés (non cumulables)\n"
        f"**Non alloué** : {report['unallocated_pct']:.1f} %\n\n"
        f"Détail et exports : {link}"
    )


def is_due(db: Session, now: datetime) -> str | None:
    """The month to report, or None when it is not time or already sent."""
    local = now.astimezone(_PARIS)
    if local.day < REPORT_DAY:
        return None
    period = finance.previous_period(local.date())
    state = db.get(FinopsState, STATE_KEY)
    return None if state and state.value == period else period


async def send_monthly_report(now: datetime | None = None) -> bool:
    """Send the report if due; return whether one went out."""
    now = now or datetime.now(_PARIS)
    with SessionLocal() as db:
        period = is_due(db, now)
        if period is None:
            return False
        message = format_report(build_report(db, period))
        if not await discord_service.send_finops_monthly_report(message):
            return False
        state = db.get(FinopsState, STATE_KEY)
        if state is None:
            db.add(FinopsState(key=STATE_KEY, value=period))
        else:
            state.value = period
        db.commit()
        logger.info("FinOps monthly report %s sent", period)
        return True


async def finops_monthly_report_loop() -> None:
    logger.info("FinOps monthly report loop started")
    while True:
        try:
            await send_monthly_report()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(
                "FinOps monthly report failed: %s", exc, exc_info=True
            )
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
