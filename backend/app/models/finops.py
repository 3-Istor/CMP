"""
FinOps persistence models.

Budgets, alert history, recommendation actions and alert anti-spam state, plus
the real cost store: FOCUS cost lines, FX rates, cost centers, closed months
and the savings ledger. The simulated provider still recomputes its costs from
:mod:`app.services.finops.engine` and stores none.
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
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
    # 0 = nominal, 1 = warning, 2 = critical (thresholds from ProjectBudget)
    last_level: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )


class CostLineItem(Base):
    """
    One cost fact in FOCUS 1.2 shape, from any source (OpenCost, cloud billing
    exports, on-prem TCO, AI gateway, SaaS). Columns prefixed ``x_`` are CNP
    extensions. A row is revised in place until ``x_is_final``; after a month
    is closed a late revision becomes an adjustment on the next month.
    """

    __tablename__ = "cost_line_items"
    __table_args__ = (
        UniqueConstraint(
            "x_source",
            "provider_name",
            "resource_id",
            "charge_period_start",
            "charge_category",
            name="uq_cost_line_items_identity",
        ),
        Index(
            "ix_cost_line_items_period_project", "billing_period", "project"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    x_source: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    service_name: Mapped[str] = mapped_column(String(128), default="")
    service_category: Mapped[str] = mapped_column(String(64), default="")
    charge_category: Mapped[str] = mapped_column(String(16), default="Usage")
    charge_period_start: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True
    )
    charge_period_end: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(255), default="")
    billed_cost: Mapped[float] = mapped_column(Float, default=0.0)
    effective_cost: Mapped[float] = mapped_column(Float, default=0.0)
    list_cost: Mapped[float] = mapped_column(Float, default=0.0)
    billing_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    consumed_quantity: Mapped[float | None] = mapped_column(Float)
    consumed_unit: Mapped[str | None] = mapped_column(String(32))
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    # Copies of tags used by every query, so they can be indexed.
    project: Mapped[str | None] = mapped_column(String(100), index=True)
    app: Mapped[str | None] = mapped_column(String(100))
    env: Mapped[str | None] = mapped_column(String(32))
    cloud: Mapped[str] = mapped_column(String(16), default="onprem")
    x_cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    x_fx_rate: Mapped[float] = mapped_column(Float, default=1.0)
    # direct | opencost | prorata | manual
    x_allocation_method: Mapped[str] = mapped_column(
        String(16), default="direct"
    )
    # invoice | public_price | tco
    x_pricing_basis: Mapped[str] = mapped_column(String(16), default="invoice")
    x_energy_kwh: Mapped[float | None] = mapped_column(Float)
    x_co2e_kg: Mapped[float | None] = mapped_column(Float)
    x_carbon_method: Mapped[str | None] = mapped_column(String(32))
    x_is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    # Built on a placeholder price (mock: true in the FinOps inputs).
    x_is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    x_ingested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )


class FxRate(Base):
    """ECB reference rate: 1 EUR = ``rate`` units of ``currency``."""

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("day", "currency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)


class CostCenter(Base):
    """Finance mapping of a project to a cost center and its budget owner."""

    __tablename__ = "cost_centers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    cost_center: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_owner: Mapped[str | None] = mapped_column(String(255))


class MonthClose(Base):
    """A closed billing month: its cost lines no longer change."""

    __tablename__ = "month_close"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    billing_period: Mapped[str] = mapped_column(
        String(7), nullable=False, unique=True
    )
    closed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )


class SavingsEntry(Base):
    """
    Savings ledger: one row per day, app and mechanism. ``avoided_eur`` is
    allocated cost freed (pods asleep); ``realized_eur`` is what actually left
    the bill (a node removed). They are never added together (D-F5).
    """

    __tablename__ = "savings_ledger"
    __table_args__ = (UniqueConstraint("day", "project", "app", "category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    project: Mapped[str] = mapped_column(String(100), nullable=False)
    app: Mapped[str] = mapped_column(String(100), default="")
    # offhours | rightsizing_applied | idle_cleanup
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    avoided_eur: Mapped[float] = mapped_column(Float, default=0.0)
    realized_eur: Mapped[float] = mapped_column(Float, default=0.0)
    energy_avoided_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    co2e_avoided_kg: Mapped[float] = mapped_column(Float, default=0.0)
    cloud: Mapped[str] = mapped_column(String(16), default="onprem")


class CarbonIntensity(Base):
    """Daily mean carbon intensity of a grid zone, gCO2e per kWh."""

    __tablename__ = "carbon_intensity"
    __table_args__ = (UniqueConstraint("day", "zone", "method"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    zone: Mapped[str] = mapped_column(String(8), nullable=False)
    g_per_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
