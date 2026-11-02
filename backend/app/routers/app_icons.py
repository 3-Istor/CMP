from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment
from app.routers.deployments import _get_app_or_404
from app.schemas.deployment import AppIconRead
from app.services import app_icons
from app.services.keycloak_service import get_current_user

router = APIRouter(prefix="/deployments", tags=["App icons"])

CurrentUser = Annotated[dict, Depends(get_current_user)]

_MULTIPART_OVERHEAD = 16 * 1024


class BuiltinIcon(BaseModel):
    kind: str
    name: str
    color: str

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value != "builtin":
            raise ValueError("kind must be 'builtin'")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if value not in app_icons.BUILTIN_ICONS:
            raise ValueError("unknown icon")
        return value

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if value not in app_icons.BUILTIN_COLORS:
            raise ValueError("unknown colour")
        return value


def _http(exc: app_icons.IconError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _clear(deployment: Deployment) -> None:
    deployment.icon_kind = None
    deployment.icon_name = None
    deployment.icon_color = None
    deployment.icon_key = None
    deployment.icon_hash = None


@router.get("/{deployment_id}/icon")
async def get_icon(
    deployment_id: int,
    request: Request,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """Access control: any role on the application's project."""
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.GUEST)
    if deployment.icon_kind != "upload" or not deployment.icon_key:
        raise HTTPException(status_code=404, detail="No custom icon.")

    etag = f'"{deployment.icon_hash}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=300"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    try:
        content = await run_in_threadpool(app_icons.fetch, deployment.icon_key)
    except app_icons.IconError as exc:
        raise _http(exc) from exc
    return Response(content=content, media_type="image/webp", headers=headers)


async def _read_upload(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if (
        declared.isdigit()
        and int(declared) > app_icons.MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD
    ):
        raise app_icons.IconError(413, "The image is larger than 512 KB.")
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise app_icons.IconError(400, "Missing 'file' part.")
    return await file.read(app_icons.MAX_UPLOAD_BYTES + 1)


@router.put("/{deployment_id}/icon", response_model=AppIconRead)
async def set_icon(
    deployment_id: int,
    request: Request,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """
    Choose a built-in icon (JSON body) or upload an image (multipart, part
    'file'). Access control: member and up.
    """
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    previous_key = deployment.icon_key

    if request.headers.get("content-type", "").startswith(
        "multipart/form-data"
    ):
        if not app_icons.is_configured():
            raise _http(app_icons.unavailable())
        try:
            content = app_icons.normalize_upload(await _read_upload(request))
            key = app_icons.object_key(
                deployment.project_id or "", deployment.name, content
            )
            await run_in_threadpool(app_icons.store, key, content)
        except app_icons.IconError as exc:
            raise _http(exc) from exc
        _clear(deployment)
        deployment.icon_kind = "upload"
        deployment.icon_key = key
        deployment.icon_hash = key.rsplit("/", 1)[-1].removesuffix(".webp")
    else:
        try:
            choice = BuiltinIcon.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="Invalid icon choice."
            ) from exc
        _clear(deployment)
        deployment.icon_kind = "builtin"
        deployment.icon_name = choice.name
        deployment.icon_color = choice.color

    db.commit()
    if previous_key and previous_key != deployment.icon_key:
        await run_in_threadpool(app_icons.remove, previous_key)
    return deployment.icon


@router.delete("/{deployment_id}/icon", status_code=204)
async def reset_icon(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    """Back to the template icon. Access control: member and up."""
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    previous_key = deployment.icon_key
    _clear(deployment)
    db.commit()
    if previous_key:
        await run_in_threadpool(app_icons.remove, previous_key)
    return Response(status_code=204)
