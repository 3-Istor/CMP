"""
Difficulties assistants report to the platform team (CNP-310).

A report is masked before it is stored (secret values never reach the
table), deduplicated on its normalized content, and limited per user and
per UTC day. Only the reporter's username is kept about them.
"""

import hashlib
import re
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.issue_report import IssueReport, IssueReportSubmission
from app.services.agents.redact import mask_text

DAILY_QUOTA = 10
REPORT_RETENTION_DAYS = 90
# The quota only looks at the current UTC day.
SUBMISSION_RETENTION_DAYS = 7
CATEGORIES = ("missing_tool", "error", "documentation", "bug", "other")
STATUSES = ("new", "exported", "closed")
SUMMARY_MAX = 300
STEPS_MAX = 4000
TOOL_MAX = 100


class QuotaExceeded(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fingerprint(category: str, summary: str, tool: str | None) -> str:
    """Same category, tool and summary (case and spacing aside): same
    report, whoever sends it and whatever steps they give."""
    words = " ".join(re.sub(r"[^\w]+", " ", summary.lower()).split())
    key = f"{category}\n{(tool or '').strip().lower()}\n{words}"
    return hashlib.sha256(key.encode()).hexdigest()


def masked(report: dict) -> dict:
    return {
        key: mask_text(value) if isinstance(value, str) else value
        for key, value in report.items()
    }


def used_today(db: Session, username: str) -> int:
    midnight = datetime.combine(_now().date(), time.min)
    return (
        db.query(
            func.count(  # pylint: disable=not-callable
                IssueReportSubmission.id
            )
        )
        .filter(
            IssueReportSubmission.username == username,
            IssueReportSubmission.created_at >= midnight,
        )
        .scalar()
    )


def submit(  # pylint: disable=too-many-arguments
    db: Session,
    username: str,
    assistant: str | None,
    *,
    category: str,
    summary: str,
    tool: str | None = None,
    steps: str | None = None,
    project: str | None = None,
) -> tuple[IssueReport, bool]:
    """The stored report and whether it was already known. Raises
    QuotaExceeded past DAILY_QUOTA calls of this user today."""
    clean = masked(
        {"summary": summary, "tool": tool, "steps": steps, "project": project}
    )
    existing = (
        db.query(IssueReport)
        .filter(
            IssueReport.fingerprint
            == fingerprint(category, clean["summary"], clean["tool"])
        )
        .first()
    )
    if existing is not None and username in existing.reporters:
        # The same user sending the same thing again is a retry, not news.
        return existing, True
    if used_today(db, username) >= DAILY_QUOTA:
        raise QuotaExceeded(
            f"At most {DAILY_QUOTA} reports a day per user: try tomorrow."
        )
    now = _now()
    if existing is not None:
        existing.occurrences += 1
        existing.reporters = [*existing.reporters, username]
        existing.last_reported_at = now
        report = existing
    else:
        report = IssueReport(
            fingerprint=fingerprint(category, clean["summary"], clean["tool"]),
            category=category,
            assistant=assistant,
            reporters=[username],
            occurrences=1,
            status="new",
            created_at=now,
            last_reported_at=now,
            **clean,
        )
        db.add(report)
        db.flush()
    db.add(
        IssueReportSubmission(
            username=username, report_id=report.id, created_at=now
        )
    )
    db.commit()
    return report, existing is not None


def view(report: IssueReport) -> dict:
    return {
        "id": report.id,
        "category": report.category,
        "summary": report.summary,
        "tool": report.tool,
        "steps": report.steps,
        "project": report.project,
        "assistant": report.assistant,
        "reporters": report.reporters,
        "occurrences": report.occurrences,
        "status": report.status,
        "created_at": report.created_at.isoformat(),
        "last_reported_at": report.last_reported_at.isoformat(),
    }


def purge_expired(db: Session) -> int:
    """Drop reports nobody sent again for REPORT_RETENTION_DAYS, and quota
    rows past SUBMISSION_RETENTION_DAYS."""
    now = _now()
    removed = (
        db.execute(
            delete(IssueReportSubmission).where(
                IssueReportSubmission.created_at
                < now - timedelta(days=SUBMISSION_RETENTION_DAYS)
            )
        ).rowcount
        or 0
    )
    removed += (
        db.execute(
            delete(IssueReport).where(
                IssueReport.last_reported_at
                < now - timedelta(days=REPORT_RETENTION_DAYS)
            )
        ).rowcount
        or 0
    )
    db.commit()
    return removed


def purge_expired_now() -> int:
    with SessionLocal() as db:
        return purge_expired(db)
