"""Preserve the source/version validated by the central import pipeline."""

import re

from fastapi import HTTPException

from app.core.config import settings
from app.models.repository_import import RepositoryImport

_GHCR_IMAGE_PREFIX = re.compile(
    r"ghcr\.io/([a-z0-9]+(?:-[a-z0-9]+)*)"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*"
)


def valid_import_image_prefix(value: str) -> bool:
    """Accept only a canonical GHCR owner and optional OCI repository path."""
    if len(value) > 255:
        return False
    match = _GHCR_IMAGE_PREFIX.fullmatch(value)
    return bool(match and len(match.group(1)) <= 39)


def is_imported_source(db, deployment):
    return (
        db.query(RepositoryImport)
        .filter_by(deployment_id=deployment.id)
        .first()
        is not None
    )


def require_mutable_source(db, deployment):
    if is_imported_source(db, deployment):
        raise HTTPException(
            409,
            "Ce repo utilise une révision validée : toute modification doit repasser par la CI d’import",
        )


def require_standard_platform_mode():
    if settings.IMPORT_ALLOWED_PROJECT:
        raise HTTPException(
            409,
            "Cette instance de recette est réservée à l’import dans le projet existant ; "
            "utilisez le CMP standard pour créer ou supprimer un projet ou déployer le catalogue",
        )
