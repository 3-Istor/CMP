"""Preserve the source/version validated by the central import pipeline."""

from fastapi import HTTPException

from app.models.repository_import import RepositoryImport


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
