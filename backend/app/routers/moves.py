"""Moving a project between clusters."""

from datetime import datetime

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.project import Project
from app.models.project_move import ProjectMove
from app.services.authz import RequireAdmin, RequireGuest
from app.services.moves import steps
from app.services.moves.loop import project_lock
from app.services.step_up import assert_step_up

router = APIRouter(prefix="/projects/{project_name}", tags=["moves"])


class MoveStart(BaseModel):
    confirm_data_loss: bool = False
    target: Literal["onprem", "public"] = "public"


class MoveRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    target: str
    status: str
    steps: list[dict]
    detail: str | None
    actor: str
    created_at: datetime
    finished_at: datetime | None


def _refused(exc: steps.MoveRefused) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


def _person(token: dict) -> None:
    """A move is a person's decision, step-up enabled or not."""
    if token.get("cnp_agent"):
        raise HTTPException(403, "Assistants cannot move a project.")


def _project(db: Session, name: str) -> Project:
    project = db.query(Project).filter_by(project_name=name).first()
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _get(db: Session, project: str, move_id: int) -> ProjectMove:
    move = db.get(ProjectMove, move_id)
    if move is None or move.project != project:
        raise HTTPException(404, "Move not found")
    return move


@router.post("/move", status_code=202, response_model=MoveRead)
async def start_move(
    body: MoveStart, ctx: RequireAdmin, db: Session = Depends(get_db)
) -> ProjectMove:
    _person(ctx.token)
    assert_step_up(ctx.token)
    project = _project(db, ctx.project_name)
    # Checks and insert under one lock: two clicks cannot both pass.
    async with project_lock(ctx.project_name):
        try:
            target = await steps.start_checks(
                db, project, body.confirm_data_loss, body.target
            )
        except steps.MoveRefused as exc:
            raise _refused(exc) from exc
        return steps.new_move(db, project, target, ctx.username)


@router.get("/moves/data-loss", response_model=list[str])
async def move_data_loss(
    ctx: RequireAdmin, db: Session = Depends(get_db)
) -> list[str]:
    """The apps whose data would start empty on the other cloud."""
    try:
        return await steps.data_loss(db, _project(db, ctx.project_name))
    except steps.MoveRefused as exc:
        raise _refused(exc) from exc


@router.get("/moves/current", response_model=MoveRead | None)
def current_move(
    ctx: RequireGuest, db: Session = Depends(get_db)
) -> ProjectMove | None:
    return steps.active_move(db, ctx.project_name)


@router.get("/moves", response_model=list[MoveRead])
def list_moves(
    ctx: RequireGuest, db: Session = Depends(get_db)
) -> list[ProjectMove]:
    return (
        db.query(ProjectMove)
        .filter(ProjectMove.project == ctx.project_name)
        .order_by(ProjectMove.created_at.desc(), ProjectMove.id.desc())
        .limit(10)
        .all()
    )


@router.post("/moves/{move_id}/retry", response_model=MoveRead)
async def retry_move(
    move_id: int, ctx: RequireAdmin, db: Session = Depends(get_db)
) -> ProjectMove:
    _person(ctx.token)
    assert_step_up(ctx.token)
    async with project_lock(ctx.project_name):
        move = _get(db, ctx.project_name, move_id)
        try:
            return steps.retry(db, move)
        except steps.MoveRefused as exc:
            raise _refused(exc) from exc


@router.post("/moves/{move_id}/cancel", response_model=MoveRead)
async def cancel_move(
    move_id: int, ctx: RequireAdmin, db: Session = Depends(get_db)
) -> ProjectMove:
    _person(ctx.token)
    assert_step_up(ctx.token)
    async with project_lock(ctx.project_name):
        move = _get(db, ctx.project_name, move_id)
        try:
            return await steps.cancel(db, move)
        except steps.MoveRefused as exc:
            raise _refused(exc) from exc
