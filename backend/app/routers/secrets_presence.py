"""
Which secrets an app expects and whether each one is set in Vault.

Only key names leave the Vault client, never values. Members and up, like the
other app reads.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment
from app.routers.deployments import (
    _extract_repo_full_name,
    _get_github_token_for_deployment,
    _get_kubernetes_deployment_or_404,
    _load_values_file,
    _values_files,
)
from app.services import app_repo, secret_presence, vault_client
from app.services.imports import generate
from app.services.keycloak_service import get_current_user

router = APIRouter(prefix="/deployments", tags=["Secrets"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


async def _repo_values(deployment: Deployment) -> list[dict[str, Any]]:
    gh_token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment)
    files = list(dict.fromkeys(reversed(_values_files(deployment))))
    values_files: list[dict[str, Any]] = []
    for file_path in files:
        values, _ = await _load_values_file(gh_token, repo, file_path)
        values_files.append(values)
    return values_files


def _import_values(
    db: Session, deployment: Deployment
) -> list[dict[str, Any]]:
    """
    The secrets block the import generated for the app, from its spec: the
    same whether or not its repository is managed, and no GitHub call.
    """
    found = app_repo.import_source_of(db, deployment.id)
    if found is None:
        return []
    source, service = found
    spec = generate.AppSpec.from_dict(service.spec or {})
    if not generate.needs_secret(spec):
        return []
    return [
        {
            "secrets": {
                "enabled": True,
                "vaultPath": f"project-{source.project_name}/"
                f"{generate.secret_path(service.app_name)}",
                "keys": sorted({e["env"] for e in spec.secret_env}),
            }
        }
    ]


@router.get(
    "/{deployment_id}/secrets/presence",
    response_model=secret_presence.SecretPresence,
)
async def get_secret_presence(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> secret_presence.SecretPresence:
    deployment = _get_kubernetes_deployment_or_404(
        deployment_id, db, token, ProjectRole.MEMBER, allow_import=True
    )
    if app_repo.is_imported(deployment.template_id):
        values_files = _import_values(db, deployment)
    else:
        values_files = await _repo_values(deployment)

    expected = secret_presence.expected_names(values_files)
    location = secret_presence.vault_location(
        values_files, deployment.project_id or "", deployment.name
    )
    if location is None:
        return secret_presence.compute(expected, [])

    try:
        present = await vault_client.read_key_names(*location)
    except vault_client.VaultError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Secrets cannot be checked right now: {exc}",
        ) from None
    return secret_presence.compute(expected, present, location)
