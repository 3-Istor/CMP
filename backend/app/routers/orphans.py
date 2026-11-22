"""Resources left behind by deleted apps and projects (CNP-306), read only."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.routers.access import require_platform_admin
from app.services import orphans

router = APIRouter(prefix="/orphans", tags=["orphans"])


@router.get("")
async def list_orphans(
    _admin: Annotated[dict, Depends(require_platform_admin)],
) -> dict:
    """What no live app or project owns any more. Nothing is deleted: a
    platform admin checks each entry and removes it by hand."""
    return await orphans.find()
