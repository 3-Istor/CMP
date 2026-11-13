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
from app.routers.deployments import (
    _app_type_of,
    _extract_repo_full_name,
    _get_github_token_for_deployment,
    _get_kubernetes_deployment_or_404,
    _load_values_file,
)
from app.services import app_security_data as security_data
from app.services import secret_presence, vault_client
from app.services.keycloak_service import get_current_user

router = APIRouter(prefix="/deployments", tags=["Secrets"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


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
        deployment_id, db, token, ProjectRole.MEMBER
    )
    gh_token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment)
    files = list(
        dict.fromkeys(
            reversed(security_data.values_files_for(_app_type_of(deployment)))
        )
    )
    values_files: list[dict[str, Any]] = []
    for file_path in files:
        values, _ = await _load_values_file(gh_token, repo, file_path)
        values_files.append(values)

    expected = secret_presence.expected_names(values_files)
    try:
        location = secret_presence.vault_location(
            values_files, deployment.project_id or ""
        )
    except secret_presence.ForeignVaultPathError:
        raise HTTPException(
            status_code=422,
            detail="secrets.vaultPath does not belong to this project.",
        ) from None
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
