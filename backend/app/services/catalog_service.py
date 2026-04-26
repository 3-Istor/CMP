"""
Catalog Service

Provides access to deployment templates from the Git repository.
Templates are dynamically loaded from the ia-project-template repository.
"""

from app.schemas.catalog import CatalogField, CatalogTemplate
from app.services.template_repository import get_repository


def get_all_templates() -> list[CatalogTemplate]:
    """
    Get all enabled templates from the repository.
    """
    repo = get_repository()
    manifests = repo.get_available_templates()

    templates = []
    for manifest in manifests:
        # Convert manifest variables to CatalogField objects
        fields = []
        for var in manifest.get("variables", []):
            fields.append(
                CatalogField(
                    name=var.get("name"),
                    label=var.get("label"),
                    type=var.get("type", "text"),
                    default=var.get("default"),
                    options=var.get("options"),
                    required=var.get("required", False),
                )
            )

        # Format image_path as a valid API path if present
        image_path = None
        if manifest.get("image_path"):
            template_id = manifest.get("id")
            image_filename = manifest.get("image_path")
            image_path = f"/static/templates/{template_id}/{image_filename}"

        templates.append(
            CatalogTemplate(
                id=manifest.get("id"),
                name=manifest.get("name"),
                description=manifest.get("description"),
                icon=manifest.get("icon", "📦"),
                category=manifest.get("category", "Other"),
                fields=fields,
                image_path=image_path,
                enabled=manifest.get("enabled", True),
            )
        )

    return templates


def get_template_by_id(template_id: str) -> CatalogTemplate | None:
    """Get a specific template by ID."""
    templates = get_all_templates()
    return next((t for t in templates if t.id == template_id), None)
