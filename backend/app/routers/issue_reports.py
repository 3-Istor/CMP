"""
Platform issue reports (CNP-310): assistants send them through the MCP tool
report_issue, platform admins read and triage them here.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.issue_report import IssueReport
from app.routers.access import require_platform_admin
from app.services import issue_reports
from app.services.keycloak_service import get_current_user

router = APIRouter(prefix="/issue-reports", tags=["issue-reports"])

Category = Literal["missing_tool", "error", "documentation", "bug", "other"]
Status = Literal["new", "exported", "closed"]


def require_admin_person(
    token: Annotated[dict, Depends(require_platform_admin)],
) -> dict:
    # Reports name their senders: an infra user's assistant does not read
    # them on its behalf.
    if token.get("cnp_agent"):
        raise HTTPException(
            status_code=403,
            detail="Issue reports are read by a person in the CMP.",
        )
    return token


class ReportIn(BaseModel):
    category: Category
    summary: str = Field(min_length=10, max_length=issue_reports.SUMMARY_MAX)
    tool: str | None = Field(default=None, max_length=issue_reports.TOOL_MAX)
    steps: str | None = Field(default=None, max_length=issue_reports.STEPS_MAX)
    project: str | None = Field(default=None, max_length=100)


class StatusIn(BaseModel):
    status: Status


@router.post("", status_code=201)
def report_issue(
    body: ReportIn,
    token: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    username = token.get("preferred_username") or token.get("sub", "")
    assistant = (token.get("cnp_agent") or {}).get("client")
    try:
        report, duplicate = issue_reports.submit(
            db, username, assistant, **body.model_dump()
        )
    except issue_reports.QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {
        "id": report.id,
        "duplicate": duplicate,
        "occurrences": report.occurrences,
        "remaining_today": max(
            0,
            issue_reports.DAILY_QUOTA - issue_reports.used_today(db, username),
        ),
    }


@router.get("")
async def list_reports(
    _admin: Annotated[dict, Depends(require_admin_person)],
    db: Annotated[Session, Depends(get_db)],
    status: Status | None = None,
) -> list[dict]:
    def load() -> list[dict]:
        query = db.query(IssueReport)
        if status:
            query = query.filter(IssueReport.status == status)
        rows = query.order_by(IssueReport.last_reported_at.desc()).limit(500)
        return [issue_reports.view(r) for r in rows]

    return await run_in_threadpool(load)


@router.patch("/{report_id}")
def update_report(
    report_id: int,
    body: StatusIn,
    _admin: Annotated[dict, Depends(require_admin_person)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    report = db.get(IssueReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No such report.")
    report.status = body.status
    db.commit()
    return issue_reports.view(report)
