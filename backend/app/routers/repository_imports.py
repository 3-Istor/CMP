from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repository_import import RepositoryImport
from app.services import repository_imports as service

router = APIRouter(prefix="/repository-imports", tags=["Repository imports"])
bearer = HTTPBearer()


def user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    return service.verify_token(credentials.credentials)


def worker_identity(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
):
    return service.verify_token(credentials.credentials, worker=True)


class ImportRequest(BaseModel):
    project_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    app_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,40}[a-z0-9]$")
    repository_url: str = Field(max_length=255)
    project_path: str = Field(
        default=".", max_length=200, pattern=r"^[A-Za-z0-9_./-]+$"
    )
    stack: Literal[
        "auto", "react", "fastapi", "python", "html-css", "java", "cpp"
    ] = "auto"
    port: int = Field(ge=1, le=65535)
    health_path: str = Field(
        default="/", max_length=200, pattern=r"^/[A-Za-z0-9_./-]*$"
    )
    allow_changes: bool = False


def owned(db, import_id, claims):
    job = db.get(RepositoryImport, import_id)
    if not job or job.user_sub != claims["sub"]:
        raise HTTPException(404, "Import introuvable")
    service.fresh_project(db, job.project_name, job.user_sub, job.username)
    return job


@router.post("", status_code=201)
async def create(
    payload: ImportRequest, db: Session = Depends(get_db), claims=Depends(user)
):
    return await service.create(db, claims, payload)


@router.get("")
def list_imports(
    project_name: str, db: Session = Depends(get_db), claims=Depends(user)
):
    service.fresh_project(
        db, project_name, claims["sub"], claims["preferred_username"]
    )
    jobs = (
        db.query(RepositoryImport)
        .filter_by(project_name=project_name, user_sub=claims["sub"])
        .all()
    )
    from datetime import datetime, timezone

    from app.models.deployment import Deployment, DeploymentStatus

    for job in jobs:
        if job.status == "deploying" and job.deployment_id:
            deployment = db.get(Deployment, job.deployment_id)
            if deployment and deployment.status in {
                DeploymentStatus.RUNNING,
                DeploymentStatus.FAILED,
            }:
                job.status = (
                    "completed"
                    if deployment.status == DeploymentStatus.RUNNING
                    else "failed"
                )
                job.message = deployment.step_message
        elif (
            job.status not in service.TERMINAL
            and job.expires_at
            < datetime.now(timezone.utc).replace(tzinfo=None)
        ):
            job.status, job.message = (
                "failed",
                "Délai dépassé ; parcours arrêté",
            )
    db.commit()
    return [service.public(job) for job in jobs]


@router.post("/{import_id}/resume")
async def resume(
    import_id: str, db: Session = Depends(get_db), claims=Depends(user)
):
    return await service.resume(db, owned(db, import_id, claims))


@router.post("/{import_id}/cancel")
async def cancel(
    import_id: str, db: Session = Depends(get_db), claims=Depends(user)
):
    job = owned(db, import_id, claims)
    if job.status in service.TERMINAL or job.status == "deploying":
        service.stop("Ce parcours ne peut plus être annulé")
    run_id = job.run_id
    service.transition(
        db, job, job.status, "cancelled", message="Parcours annulé"
    )
    if run_id:
        try:
            token = await service.get_installation_token(
                service.settings.GITHUB_INSTALLATION_ID
            )
            await service.github(
                "POST",
                f"/repos/{service.settings.IMPORT_WORKFLOW_REPO}/actions/runs/{run_id}/cancel",
                token,
            )
        except Exception:
            job.message = "Parcours bloqué dans CMP ; l’arrêt de la CI distante n’a pas pu être confirmé"
            db.commit()
    return service.public(job)


@router.post("/{import_id}/retry")
async def retry(
    import_id: str, db: Session = Depends(get_db), claims=Depends(user)
):
    return await service.retry(db, owned(db, import_id, claims))


@router.post("/{import_id}/worker/{action}")
async def worker(
    import_id: str,
    action: str,
    payload: dict,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    claims=Depends(worker_identity),
):
    job = db.get(RepositoryImport, import_id)
    if not job:
        raise HTTPException(404, "Import introuvable")
    return await service.worker(db, job, claims, action, payload, tasks)
