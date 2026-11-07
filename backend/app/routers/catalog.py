from fastapi import APIRouter, Depends, HTTPException

from app.schemas.catalog import CatalogTemplate
from app.services.authz import is_cnp_admin
from app.services.catalog_service import get_all_templates, get_template_by_id
from app.services.keycloak_service import get_current_user
from app.services.template_repository import get_repository

router = APIRouter(
    prefix="/catalog",
    tags=["Catalog"],
    dependencies=[Depends(get_current_user)],
)


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


@router.post("/sync")
async def sync_templates(token: dict = Depends(get_current_user)):
    """Force sync the template repository from Git (platform admins)."""
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403,
            detail="Only platform admins can sync the catalog.",
        )
    try:
        get_repository().force_sync()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Template repository sync failed: {exc}"
        ) from exc
    return {"message": "Template repository synced successfully"}
