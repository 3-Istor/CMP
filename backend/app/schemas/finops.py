"""Pydantic schemas for the FinOps API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Breakdown(BaseModel):
    cpu: float = 0.0
    ram: float = 0.0
    storage: float = 0.0
    network: float = 0.0
    ai: float = 0.0


class CostSummary(BaseModel):
    month_to_date_eur: float
    projected_month_eur: float
    previous_month_eur: float
    trend_pct: float | None = None
    breakdown: Breakdown
    potential_savings_eur: float
    app_count: int
    currency: str = "EUR"


class CostSeriesPoint(BaseModel):
    date: str
    cpu: float
    ram: float
    storage: float
    network: float
    total: float
    ai: float = 0.0


class AppCostRow(BaseModel):
    app_id: int
    name: str
    project_id: str | None = None
    cost_per_day_eur: float
    cost_month_estimate_eur: float
    month_to_date_eur: float
    trend_pct: float | None = None


class SavingsTotals(BaseModel):
    avoided_eur: float = 0.0
    realized_eur: float = 0.0
    energy_avoided_kwh: float = 0.0
    co2e_avoided_kg: float = 0.0


class SavingsYtd(BaseModel):
    avoided_eur: float = 0.0
    realized_eur: float = 0.0


class SavingsCategory(BaseModel):
    category: str
    avoided_eur: float
    realized_eur: float


class SavingsDay(BaseModel):
    date: str
    avoided_eur: float
    realized_eur: float


class SavingsRow(BaseModel):
    project: str
    app: str
    category: str
    avoided_eur: float
    realized_eur: float
    energy_avoided_kwh: float
    days: int


class SavingsResponse(BaseModel):
    totals: SavingsTotals
    by_category: list[SavingsCategory]
    timeline: list[SavingsDay]
    rows: list[SavingsRow]
    since: date | None = None


class CarbonTotals(BaseModel):
    energy_kwh: float = 0.0
    co2e_direct_kg: float = 0.0
    co2e_lca_kg: float = 0.0
    co2e_avoided_kg: float = 0.0


class CarbonMethod(BaseModel):
    id: str
    label: str
    source: str
    g_per_kwh: float | None = None


class CarbonDay(BaseModel):
    date: str
    energy_kwh: float
    co2e_kg: float
    g_per_kwh: float | None = None


class CarbonRow(BaseModel):
    project: str
    app: str
    energy_kwh: float
    co2e_direct_kg: float
    co2e_lca_kg: float


class CarbonLatest(BaseModel):
    g_per_kwh: float
    day: date
    source: str


class CarbonResponse(BaseModel):
    totals: CarbonTotals
    methods: list[CarbonMethod]
    timeline: list[CarbonDay]
    rows: list[CarbonRow]
    latest: CarbonLatest | None = None
    has_estimate: bool = False


class OverviewResponse(BaseModel):
    summary: CostSummary
    budget: "BudgetRead | None" = None
    apps: list[AppCostRow]
    timeline: list[CostSeriesPoint]
    by_cloud: dict[str, float] = Field(
        default_factory=lambda: {"onprem": 0.0, "aws": 0.0, "gcp": 0.0}
    )
    savings_ytd: SavingsYtd = Field(default_factory=SavingsYtd)
    # Real costs only: the month-end forecast behind projected_month_eur.
    forecast: "CostForecast | None" = None


class SourceMeta(BaseModel):
    source: str
    last_ingested_at: datetime | None = None
    latest_day: date | None = None
    final_through: date | None = None
    has_estimate: bool = False


class MetaResponse(BaseModel):
    provider: str
    sources: list[SourceMeta]
    role: Literal["admin", "finance", "member"] = "member"


class Recommendation(BaseModel):
    id: str
    app_id: int
    app_name: str
    project_id: str | None = None
    rec_type: str
    title: str
    justification: str
    current: dict
    recommended: dict
    monthly_saving_eur: float
    confidence: int
    effort: str
    status: str = "pending"
    can_apply: bool = False


class BudgetRead(BaseModel):
    model_config = {"from_attributes": True}

    project_name: str
    monthly_amount_eur: float
    threshold_warn: int
    threshold_critical: int
    currency: str = "EUR"
    # Derived, month-to-date consumption vs budget:
    spent_eur: float = 0.0
    remaining_eur: float = 0.0
    consumed_pct: float | None = None
    status: str = "ok"  # ok | warning | critical
    updated_by: str | None = None
    updated_at: datetime | None = None


class BudgetWrite(BaseModel):
    monthly_amount_eur: float = Field(ge=0)
    threshold_warn: int = Field(default=70, ge=1, le=100)
    threshold_critical: int = Field(default=90, ge=1, le=100)
    currency: str = "EUR"


class CostAlertRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    project_name: str
    app_id: int | None = None
    level: str
    kind: str
    message: str
    value_pct: float | None = None
    triggered_at: datetime
    subject_day: date | None = None


class ActionResponse(BaseModel):
    message: str
    rec_id: str
    status: str
    commit_sha: str | None = None


class ForecastDay(BaseModel):
    date: str
    eur: float
    low_eur: float
    high_eur: float


class CostForecast(BaseModel):
    projected_eur: float
    low_eur: float
    high_eur: float
    actual_eur: float
    method: str  # weekday_mean | mean | none
    days_of_history: int
    low_history: bool
    confidence: float
    budget_eur: float | None = None
    overrun_date: date | None = None
    daily: list[ForecastDay] = Field(default_factory=list)


class AnomalyEvent(BaseModel):
    kind: str  # argocd_sync | release | config_change
    time: datetime
    actor: str | None = None
    revision: str | None = None
    summary: str
    changes: list[dict] = Field(default_factory=list)


class CostAnomaly(BaseModel):
    app_id: int
    app_name: str
    project_id: str | None = None
    date: str
    cost_eur: float
    baseline_eur: float
    delta_eur: float
    delta_pct: float | None = None
    cause: str
    events: list[AnomalyEvent] = Field(default_factory=list)


OverviewResponse.model_rebuild()


class CostCenterRow(BaseModel):
    project_name: str
    cost_center: str | None = None
    budget_owner: str | None = None


class CostCentersWrite(BaseModel):
    centers: list[CostCenterRow]


class MonthCloseRead(BaseModel):
    billing_period: str
    closed_by: str
    closed_at: datetime | None = None
