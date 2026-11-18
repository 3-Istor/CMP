"""Moving a project from one public cloud to the other."""

from datetime import datetime

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


def _get(db: Session, project: str, move_id: int) -> ProjectMove:
    move = db.get(ProjectMove, move_id)
    if move is None or move.project != project:
        raise HTTPException(404, "Move not found")
    return move


@router.post("/move", status_code=202, response_model=MoveRead)
async def start_move(
    body: MoveStart, ctx: RequireAdmin, db: Session = Depends(get_db)
) -> ProjectMove:
    assert_step_up(ctx.token)
    project = (
        db.query(Project).filter_by(project_name=ctx.project_name).first()
    )
    if project is None:
        raise HTTPException(404, "Project not found")
    # Checks and insert under one lock: two clicks cannot both pass.
    async with project_lock(ctx.project_name):
        try:
            target = steps.start_checks(db, project, body.confirm_data_loss)
        except steps.MoveRefused as exc:
            raise _refused(exc) from exc
        return steps.new_move(db, project, target, ctx.username)


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
    assert_step_up(ctx.token)
    async with project_lock(ctx.project_name):
        move = _get(db, ctx.project_name, move_id)
        try:
            return await steps.cancel(db, move)
        except steps.MoveRefused as exc:
            raise _refused(exc) from exc
