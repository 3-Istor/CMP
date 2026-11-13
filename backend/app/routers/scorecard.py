"""
Scorecard of an app. Members and up, like the security and cost reads it
summarises.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import ProviderType
from app.routers.deployments import (
    _extract_repo_full_name,
    _get_app_or_404,
    _get_github_token_for_deployment,
)
from app.services import scorecard
from app.services.github_service import FileNotInRepoError, get_file_content
from app.services.keycloak_service import get_current_user

router = APIRouter(prefix="/deployments", tags=["Scorecard"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.get("/{deployment_id}/scorecard", response_model=scorecard.Scorecard)
async def get_scorecard(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> scorecard.Scorecard:
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    if deployment.provider_type != ProviderType.KUBERNETES:
        raise HTTPException(
            status_code=400, detail="Only Kubernetes apps have a scorecard."
        )

    async def read_readme() -> str | None:
        try:
            repo = _extract_repo_full_name(deployment)
        except HTTPException:
            return None
        gh_token = await _get_github_token_for_deployment(deployment)
        try:
            content, _ = await get_file_content(
                installation_token=gh_token,
                repo_full_name=repo,
                file_path="README.md",
            )
        except FileNotInRepoError:
            return None
        return content

    return await scorecard.scorecard_for(db, deployment, read_readme)
