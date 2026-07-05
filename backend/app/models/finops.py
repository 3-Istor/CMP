"""
FinOps persistence models.

Costs themselves are **not** stored — they are recomputed deterministically by
:mod:`app.services.finops.engine`. These tables hold only the state that cannot
be derived: project budgets, the alert history, per-recommendation user actions,
and anti-spam bookkeeping for budget alerts.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProjectBudget(Base):
    """Monthly budget of a project — editable only by the Project Owner."""

    __tablename__ = "project_budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    monthly_amount_eur: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    threshold_warn: Mapped[int] = mapped_column(Integer, default=70)  # %
    threshold_critical: Mapped[int] = mapped_column(Integer, default=90)  # %
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )


class CostAlert(Base):
    """History of budget / consumption alerts that were raised."""

    __tablename__ = "cost_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    app_id: Mapped[int | None] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(
        String(16), default="info"
    )  # info|warning|critical
    kind: Mapped[str] = mapped_column(
        String(16), default="budget"
    )  # budget|spike
    message: Mapped[str] = mapped_column(String(500), default="")
    value_pct: Mapped[float | None] = mapped_column(Float)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )


class RecommendationState(Base):
    """
    User action on a (recomputed) recommendation.

    ``rec_id`` matches the stable id produced by the recommendation engine
    (``<app_id>:<type>``), so a persisted decision keeps applying after recompute.
    """

    __tablename__ = "recommendation_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    rec_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    app_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rec_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|applied|ignored|notified
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )


class BudgetAlertState(Base):
    """Last budget threshold level notified per project (Discord anti-spam)."""

    __tablename__ = "budget_alert_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    # 0 = nominal, 1 = warning (70%), 2 = critical (90%)
    last_level: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )
