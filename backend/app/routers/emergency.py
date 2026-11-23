"""Outage dashboard for platform admins."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers.account import _preference_key
from app.routers.placement import require_platform_admin
from app.services import emergency

router = APIRouter(tags=["emergency"])


@router.get("/emergency")
async def get_emergency(
    token: Annotated[dict, Depends(require_platform_admin)],
    db: Session = Depends(get_db),
) -> dict:
    return await emergency.overview(db, _preference_key(token))
