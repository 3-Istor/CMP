"""Pydantic schemas for the FinOps API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Breakdown(BaseModel):
    cpu: float = 0.0
    ram: float = 0.0
    storage: float = 0.0
    network: float = 0.0


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


class AppCostRow(BaseModel):
    app_id: int
    name: str
    project_id: str | None = None
    cost_per_day_eur: float
    cost_month_estimate_eur: float
    month_to_date_eur: float
    trend_pct: float | None = None


class OverviewResponse(BaseModel):
    summary: CostSummary
    budget: "BudgetRead | None" = None
    apps: list[AppCostRow]
    timeline: list[CostSeriesPoint]


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


class ActionResponse(BaseModel):
    message: str
    rec_id: str
    status: str
    commit_sha: str | None = None


OverviewResponse.model_rebuild()
