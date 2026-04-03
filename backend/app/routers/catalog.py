from fastapi import APIRouter, HTTPException

from app.schemas.catalog import CatalogTemplate
from app.services.catalog_service import get_all_templates, get_template_by_id

router = APIRouter(prefix="/catalog", tags=["Catalog"])


@router.get("/", response_model=list[CatalogTemplate])
async def list_templates() -> list[CatalogTemplate]:
    """Return all available app templates."""
    return get_all_templates()


@router.get("/{template_id}", response_model=CatalogTemplate)
async def get_template(template_id: str) -> CatalogTemplate:
    template = get_template_by_id(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template
