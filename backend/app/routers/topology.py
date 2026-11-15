"""
Project topology and links between apps (CNP-242, CNP-240).

Members and the security-auditor platform group read the map; admins and
owners (and the infra group, admin on every project) link and unlink apps.
Other guests get nothing: the map names every app and who calls whom.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.services import audit, topology
from app.services.authz import RequireAdmin, RequireGuest, is_auditor
from app.services.imports import app_links
from app.services.imports.service import ImportRefused
from app.services.project_registry import RegistryError

router = APIRouter(prefix="/projects", tags=["Topology"])

DbSession = Annotated[Session, Depends(get_db)]
APP_NAME = r"^[a-z][-a-z0-9]{0,39}$"


class LinkRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from", pattern=APP_NAME)
    to: str = Field(pattern=APP_NAME)
    port: int = Field(ge=1, le=65535)
    alias: str | None = Field(default=None, max_length=63)


@router.get("/{project_name}/topology")
async def get_topology(ctx: RequireGuest, db: DbSession):
    if ctx.role is ProjectRole.GUEST and not is_auditor(ctx.token):
        raise HTTPException(403, "Guests do not see the project topology")
    try:
        return await topology.cached_build(db, ctx.project_name)
    except RegistryError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/{project_name}/topology/links", status_code=201)
async def link_apps(
    body: LinkRequest, ctx: RequireAdmin, db: DbSession, request: Request
):
    try:
        link = await app_links.add_link(
            db, ctx.project_name, body.from_, body.to, body.port, body.alias
        )
    except ImportRefused as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except RegistryError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit.note(request, target=f"{body.from_}->{body.to}")
    return link


@router.delete("/{project_name}/topology/links")
async def unlink_apps(  # pylint: disable=too-many-arguments
    ctx: RequireAdmin,
    db: DbSession,
    request: Request,
    source: Annotated[str, Query(alias="from", pattern=APP_NAME)],
    target: Annotated[str, Query(alias="to", pattern=APP_NAME)],
    alias: Annotated[str | None, Query(max_length=63)] = None,
):
    try:
        commit = await app_links.remove_link(
            db, ctx.project_name, source, target, alias
        )
    except ImportRefused as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except RegistryError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit.note(request, target=f"{source}->{target}")
    return {"commit": commit}
