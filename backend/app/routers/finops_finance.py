"""
FinOps finance pack — exports, showback statements, month close, cost centers.

Reads follow the FinOps rule (admin and finance see everything, members see
their projects); the exports and the month close are for finance and admin
only. Mounted under ``/api/finops`` next to :mod:`app.routers.finops`.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.finops import CostCenter, ProjectBudget
from app.models.project import Project
from app.routers.finops import (
    _require_project_role,
    _sees_all,
    _user_project_names,
    _username,
)
from app.schemas.finops import CostCenterRow, CostCentersWrite, MonthCloseRead
from app.services.finops import finance, reconciliation
from app.services.keycloak_service import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/finops", tags=["FinOps"])

Token = Annotated[dict, Depends(get_current_user)]
PeriodQuery = Annotated[str, Query(pattern=finance.PERIOD_PATTERN)]

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _require_finance(token: dict) -> None:
    if not _sees_all(token):
        raise HTTPException(
            status_code=403,
            detail="Réservé à la finance et aux admins CNP.",
        )


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.xlsx")
def export_xlsx(
    token: Token, period: PeriodQuery, db: Session = Depends(get_db)
):
    """Month workbook for finance: summary, projects, providers, gaps, savings, lines."""
    _require_finance(token)
    content = finance.build_xlsx(
        period,
        finance.month_lines(db, period),
        finance.month_savings(db, period),
        finance.cost_centers(db),
        {
            provider: reconciliation.reconcile(db, provider, period)
            for provider in reconciliation.PROVIDERS
        },
    )
    return _download(content, XLSX_TYPE, f"finops-{period}.xlsx")


@router.get("/export.parquet")
def export_parquet(
    token: Token, period: PeriodQuery, db: Session = Depends(get_db)
):
    """FOCUS cost lines of the month, typed."""
    _require_finance(token)
    content = finance.build_parquet(finance.month_lines(db, period))
    return _download(
        content, "application/vnd.apache.parquet", f"finops-{period}.parquet"
    )


@router.get("/statement.pdf")
def statement_pdf(
    token: Token,
    period: PeriodQuery,
    project: str,
    db: Session = Depends(get_db),
):
    """One-page showback statement of a project."""
    _require_project_role(db, token, project)
    budget = db.scalar(
        select(ProjectBudget).where(ProjectBudget.project_name == project)
    )
    content = finance.build_statement_pdf(
        project,
        period,
        finance.month_lines(db, period, [project]),
        finance.month_savings(db, period, project),
        finance.cost_centers(db).get(project),
        budget.monthly_amount_eur if budget else None,
    )
    return _download(
        content, "application/pdf", f"releve-{project}-{period}.pdf"
    )


@router.post("/close/{period}", response_model=MonthCloseRead)
def close_period(
    token: Token,
    period: Annotated[str, Path(pattern=finance.PERIOD_PATTERN)],
    force: bool = False,
    db: Session = Depends(get_db),
):
    """Freeze a month: later revisions of its lines become adjustments."""
    _require_finance(token)
    try:
        return finance.close_month(db, period, _username(token), force)
    except finance.CloseRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/closes", response_model=list[MonthCloseRead])
def list_closes(token: Token, db: Session = Depends(get_db)):
    del token
    return finance.closed_months(db)


@router.get("/cost-centers", response_model=list[CostCenterRow])
def get_cost_centers(token: Token, db: Session = Depends(get_db)):
    """Finance and admin see every project, a member sees theirs."""
    centers = finance.cost_centers(db)
    if _sees_all(token):
        names = set(db.scalars(select(Project.project_name))) | set(centers)
    else:
        names = set(_user_project_names(token))
    return [
        CostCenterRow(
            project_name=name,
            cost_center=centers[name].cost_center if name in centers else None,
            budget_owner=(
                centers[name].budget_owner if name in centers else None
            ),
        )
        for name in sorted(names)
    ]


@router.put("/cost-centers", response_model=list[CostCenterRow])
def put_cost_centers(
    payload: CostCentersWrite, token: Token, db: Session = Depends(get_db)
):
    """Set project to cost center mappings; an empty cost center removes one."""
    _require_finance(token)
    known = set(db.scalars(select(Project.project_name)))
    existing = finance.cost_centers(db)
    for row in payload.centers:
        if row.project_name not in known:
            raise HTTPException(
                status_code=400,
                detail=f"Projet inconnu : {row.project_name}.",
            )
        value = (row.cost_center or "").strip()
        if len(value) > 64:
            raise HTTPException(
                status_code=400, detail="Centre de coûts trop long (64 max)."
            )
        current = existing.get(row.project_name)
        if not value:
            if current:
                db.delete(current)
            continue
        if current is None:
            current = CostCenter(project_name=row.project_name)
            db.add(current)
        current.cost_center = value
        current.budget_owner = (row.budget_owner or "").strip() or None
    db.commit()
    return get_cost_centers(token, db)
