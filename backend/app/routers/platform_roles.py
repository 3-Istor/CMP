"""
Platform groups seen and granted from the portal: infra reads all three,
grants finops and security-auditor. infra itself stays in the K3s Terraform.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.services.access import groups
from app.services.access_review import directory
from app.services.audit import note
from app.services.authz import (
    AUDITOR_GROUP,
    FINOPS_GROUP,
    PLATFORM_ADMIN_GROUP,
    is_cnp_admin,
)
from app.services.keycloak_service import get_current_user
from app.services.step_up import assert_step_up

router = APIRouter(prefix="/platform-roles", tags=["platform-roles"])

GROUPS = (PLATFORM_ADMIN_GROUP, AUDITOR_GROUP, FINOPS_GROUP)


def require_infra(token: Annotated[dict, Depends(get_current_user)]) -> dict:
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403, detail="Platform roles are managed by infra."
        )
    return token


def require_step_up(token: Annotated[dict, Depends(require_infra)]) -> dict:
    # An assistant token carries its user's groups; a person grants roles.
    if token.get("cnp_agent"):
        raise HTTPException(
            status_code=403,
            detail="An assistant cannot change platform roles.",
        )
    assert_step_up(token)
    return token


Infra = Annotated[dict, Depends(require_infra)]
SteppedUp = Annotated[dict, Depends(require_step_up)]


class PlatformRoleRead(BaseModel):
    group: str
    managed: bool
    members: list[str]


@router.get("", response_model=list[PlatformRoleRead])
def list_roles(_: Infra):
    try:
        return [
            PlatformRoleRead(
                group=group,
                managed=group in groups.PLATFORM_MANAGED,
                members=directory.group_members(group),
            )
            for group in GROUPS
        ]
    except directory.DirectoryError as exc:
        raise HTTPException(
            status_code=502, detail="Keycloak could not list the members."
        ) from exc


async def _change(request: Request, group: str, username: str, add: bool):
    if group not in groups.PLATFORM_MANAGED:
        raise HTTPException(
            status_code=403, detail=f"'{group}' is managed in Terraform."
        )
    apply = groups.add_member if add else groups.remove_member
    try:
        await run_in_threadpool(apply, username, group)
    except groups.GroupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    note(
        request,
        action=f"platform_role.{'add' if add else 'remove'}",
        group=group,
        target=username,
    )
    return Response(status_code=204)


@router.put("/{group}/members/{username}", status_code=204)
async def add_member(
    group: str, username: str, request: Request, _: SteppedUp
):
    return await _change(request, group, username, add=True)


@router.delete("/{group}/members/{username}", status_code=204)
async def remove_member(
    group: str, username: str, request: Request, _: SteppedUp
):
    return await _change(request, group, username, add=False)
