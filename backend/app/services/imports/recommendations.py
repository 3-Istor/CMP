"""
Managed-service recommendations of the analyzer (CNP-305), and what an
imported backing container lacks against its managed alternative (CNP-302).

The analyzer writes them into the plan of each analysis; a project may
ignore one, and it stays ignored for every later analysis of the project.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.imports import (
    ImportRecommendationDismissal,
    ImportService,
    ImportSource,
    RunKind,
    RunStatus,
)


def dismissed_ids(db: Session, project: str) -> set[str]:
    return {
        row.recommendation_id
        for row in db.query(ImportRecommendationDismissal)
        .filter_by(project_name=project)
        .all()
    }


def _renames(source: ImportSource) -> dict[str, str]:
    """Plan name -> app name, as the last applied plan renamed them."""
    for run in reversed(source.runs):
        if run.kind == RunKind.BUILD.value and run.plan:
            return dict((run.plan.get("edits") or {}).get("rename") or {})
    return {}


def _latest_plan(source: ImportSource) -> dict | None:
    for run in reversed(source.runs):
        if (
            run.kind == RunKind.ANALYZE.value
            and run.status == RunStatus.SUCCEEDED.value
            and run.plan
        ):
            return run.plan
    return None


def mark_dismissed(recommendations: list[dict], ignored: set[str]) -> list:
    return [
        {**r, "dismissed": r.get("id") in ignored} for r in recommendations
    ]


def for_project(db: Session, project: str, app: str | None = None) -> dict:
    """
    The recommendations of the latest analysis of each import of the
    project, named after the deployed app, and the backing containers
    with what they lack. Filtered on *app* when given.
    """
    ignored = dismissed_ids(db, project)
    found: list[dict] = []
    for source in db.query(ImportSource).filter_by(project_name=project).all():
        plan = _latest_plan(source)
        renames = _renames(source)
        for item in (plan or {}).get("recommendations") or []:
            subject = renames.get(item["subject"], item["subject"])
            if app is None or subject == app:
                found.append(
                    {
                        **item,
                        "app": subject,
                        "repository": source.repo_full_name,
                    }
                )
    found = mark_dismissed(found, ignored)
    backing = []
    rows = (
        db.query(ImportService)
        .join(ImportSource)
        .filter(
            ImportSource.project_name == project,
            ImportService.kind == "backing",
        )
        .all()
    )
    for row in rows:
        if app is not None and row.app_name != app:
            continue
        target = next(
            (
                r["target"]
                for r in found
                if r["app"] == row.app_name and not r["dismissed"]
            ),
            None,
        )
        backing.append(
            {
                "app": row.app_name,
                "kind": row.spec.get("backing_kind") or "",
                "backed_up": False,
                "managed_alternative": target,
            }
        )
    return {"recommendations": found, "backing": backing}


def dismiss(db: Session, project: str, recommendation_id: str, user: str):
    """Idempotent: ignoring an ignored recommendation changes nothing."""
    if recommendation_id in dismissed_ids(db, project):
        return
    db.add(
        ImportRecommendationDismissal(
            project_name=project,
            recommendation_id=recommendation_id,
            dismissed_by=user,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def restore(db: Session, project: str, recommendation_id: str) -> None:
    db.query(ImportRecommendationDismissal).filter_by(
        project_name=project, recommendation_id=recommendation_id
    ).delete()
    db.commit()
