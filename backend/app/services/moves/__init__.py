"""Moving a project between public clouds. The guard here imports nothing
heavy, so any router can call it."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.project_move import ACTIVE, ProjectMove


def active_move(db: Session, project: str) -> ProjectMove | None:
    return (
        db.query(ProjectMove)
        .filter(ProjectMove.project == project)
        .filter(ProjectMove.status.in_(ACTIVE))
        .first()
    )


def assert_not_moving(db: Session, project: str | None) -> None:
    """409 while *project* moves: its apps and data are in flight."""
    if project and active_move(db, project):
        raise HTTPException(status_code=409, detail="This project is moving.")
