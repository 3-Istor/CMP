import re
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repository_import import RepositoryImport
from app.services import repository_imports as service
from app.services.keycloak_service import get_current_user

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
    source_branch: str = Field(default="main", min_length=1, max_length=100)
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
    runtime_environment: dict[str, str] = Field(
        default_factory=dict, max_length=50
    )
    smoke_environment: dict[str, str] = Field(
        default_factory=dict, max_length=50
    )
    runtime_secret_keys: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("source_branch")
    @classmethod
    def validate_source_branch(cls, value):
        if (
            not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", value)
            or ".." in value
            or "//" in value
            or value.endswith((".", "/"))
            or any(
                part.startswith(".") or part.endswith(".lock")
                for part in value.split("/")
            )
        ):
            raise ValueError("Nom de branche Git invalide")
        return value

    @field_validator("runtime_environment", "smoke_environment")
    @classmethod
    def validate_environment(cls, values, info):
        for name, value in values.items():
            if (
                not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", name)
                or len(value) > 2048
                or any(character in value for character in "\0\r\n")
            ):
                raise ValueError("Variable d’environnement invalide")
            sensitive = re.search(
                r"SECRET|PASSWORD|TOKEN|PRIVATE_KEY|API_KEY", name
            )
            if re.search(
                r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@|-----BEGIN .*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}",
                value,
            ):
                raise ValueError("Les identifiants doivent rester dans Vault")
            if sensitive:
                if info.field_name == "runtime_environment":
                    raise ValueError(
                        "Les secrets de déploiement doivent être configurés dans Vault"
                    )
                if value != "__CNP_RANDOM_SECRET__" and not value.startswith(
                    "cnp-smoke-"
                ):
                    raise ValueError(
                        "Le test utilise seulement des valeurs cnp-smoke-* ou __CNP_RANDOM_SECRET__"
                    )
        if (
            sum(len(name) + len(value) for name, value in values.items())
            > 16384
        ):
            raise ValueError("Configuration d’environnement trop volumineuse")
        return values

    @model_validator(mode="after")
    def validate_runtime_contract(self):
        if set(self.runtime_secret_keys) & self.runtime_environment.keys():
            raise ValueError(
                "Une variable doit être publique ou provenir de Vault, pas les deux"
            )
        merged = {**self.runtime_environment, **self.smoke_environment}
        if (
            len(merged) > 50
            or sum(len(key) + len(value) for key, value in merged.items())
            > 16384
        ):
            raise ValueError("Configuration de test trop volumineuse")
        for key in self.runtime_secret_keys:
            sample = self.smoke_environment.get(key, "")
            if not (
                sample == "__CNP_RANDOM_SECRET__"
                or sample.startswith("cnp-smoke-")
                or sample.startswith("sqlite:///")
            ):
                raise ValueError(
                    "Chaque secret Vault exige une valeur de test non sensible"
                )
        return self

    @field_validator("runtime_secret_keys")
    @classmethod
    def validate_secret_keys(cls, keys):
        if len(set(keys)) != len(keys) or any(
            not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key) for key in keys
        ):
            raise ValueError("Noms de secrets Vault invalides ou dupliqués")
        return keys


def owned(db, import_id, claims):
    job = db.get(RepositoryImport, import_id)
    if not job or job.user_sub != claims["sub"]:
        raise HTTPException(404, "Import introuvable")
    service.fresh_project(db, job.project_name, job.user_sub, job.username)
    return job


@router.get("/availability")
def availability(claims=Depends(get_current_user)):
    """Authenticated status only; never grants access to a source repository."""
    if not service.settings.IMPORT_ENABLED:
        return {
            "ready": False,
            "message": "L’import est désactivé sur cet environnement. Le formulaire reste consultable.",
        }
    try:
        service.enabled()
    except HTTPException:
        return {
            "ready": False,
            "message": "L’import n’est pas encore configuré sur cet environnement.",
        }
    return {"ready": True, "message": ""}


@router.post("", status_code=201)
async def create(
    payload: ImportRequest, db: Session = Depends(get_db), claims=Depends(user)
):
    return await service.create(db, claims, payload)


@router.get("")
def list_imports(
    project_name: str, db: Session = Depends(get_db), claims=Depends(user)
):
    with db.no_autoflush:
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
            original_status, owner = job.status, service.job_owner(job)
            status, message = original_status, job.message
            if job.status == "deploying" and job.deployment_id:
                deployment = db.get(Deployment, job.deployment_id)
                if deployment and deployment.status in {
                    DeploymentStatus.RUNNING,
                    DeploymentStatus.FAILED,
                }:
                    status = (
                        "completed"
                        if deployment.status == DeploymentStatus.RUNNING
                        else "failed"
                    )
                    message = deployment.step_message
            elif (
                job.status not in service.TERMINAL
                and job.expires_at
                < datetime.now(timezone.utc).replace(tzinfo=None)
            ):
                status, message = "failed", "Délai dépassé ; parcours arrêté"
            if status != original_status:
                try:
                    service.transition(
                        db,
                        job,
                        original_status,
                        status,
                        expected_owner=owner,
                        message=message,
                    )
                except HTTPException as error:
                    if error.status_code != 409:
                        raise
                    with db.no_autoflush:
                        db.refresh(job)
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
    with db.no_autoflush:
        job = owned(db, import_id, claims)
        if job.status in service.TERMINAL or job.status == "deploying":
            service.stop("Ce parcours ne peut plus être annulé")
        run_id, owner = job.run_id, service.job_owner(job)
        service.transition(
            db,
            job,
            job.status,
            "cancelled",
            expected_owner=owner,
            message="Parcours annulé",
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
                try:
                    service.transition(
                        db,
                        job,
                        "cancelled",
                        "cancelled",
                        expected_owner=owner,
                        message="Parcours bloqué dans CMP ; l’arrêt de la CI distante n’a pas pu être confirmé",
                    )
                except HTTPException as error:
                    if error.status_code != 409:
                        raise
        with db.no_autoflush:
            db.refresh(job)
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
