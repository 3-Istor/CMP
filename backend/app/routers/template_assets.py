"""Catalog images of the app-templates clone.

Terraform runs inside the clone, so the template folders also hold generated
files (provider overrides, TF_DATA_DIR backend metadata). Only the image a
manifest declares is served, never a directory listing of the clone.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.template_repository import get_repository

router = APIRouter(tags=["catalog"])

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def resolve_template_image(template_id: str, filename: str) -> Path | None:
    repo = get_repository()
    manifest = next(
        (
            m
            for m in repo.get_available_templates()
            if m.get("id") == template_id
        ),
        None,
    )
    if manifest is None or manifest.get("image_path") != filename:
        return None
    if Path(filename).suffix.lower() not in IMAGE_MEDIA_TYPES:
        return None
    templates_root = (repo.repo_path / "templates").resolve()
    image = (templates_root / template_id / filename).resolve()
    if (
        not image.is_relative_to(templates_root / template_id)
        or not image.is_file()
    ):
        return None
    return image


@router.get(
    "/static/templates/{template_id}/{filename}", include_in_schema=False
)
def template_image(template_id: str, filename: str) -> FileResponse:
    image = resolve_template_image(template_id, filename)
    if image is None:
        raise HTTPException(status_code=404)
    return FileResponse(
        image,
        media_type=IMAGE_MEDIA_TYPES[image.suffix.lower()],
        headers={"X-Content-Type-Options": "nosniff"},
    )
