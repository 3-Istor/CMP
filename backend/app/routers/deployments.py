import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.schemas.deployment import DeploymentCreate, DeploymentRead
from app.services import aws_service
from app.services.saga_orchestrator import run_deletion, run_deployment

router = APIRouter(prefix="/deployments", tags=["Deployments"])


@router.get("/", response_model=list[DeploymentRead])
async def list_deployments(db: Session = Depends(get_db)):
    """Return all non-deleted deployments for the dashboard."""
    return (
        db.query(Deployment)
        .filter(Deployment.status != DeploymentStatus.DELETED)
        .all()
    )


@router.post("/", response_model=DeploymentRead, status_code=202)
async def create_deployment(
    payload: DeploymentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Create a deployment record and kick off the SAGA in the background.
    Returns immediately with status=pending so the frontend can start polling.
    """
    deployment = Deployment(
        name=payload.name,
        template_id=payload.template_id,
        app_config=json.dumps(payload.app_config),
        status=DeploymentStatus.PENDING,
        step_message="Queued...",
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    # Run SAGA asynchronously — API returns 202 immediately
    background_tasks.add_task(run_deployment, deployment.id, db)

    return deployment


@router.get("/{deployment_id}", response_model=DeploymentRead)
async def get_deployment(
    deployment_id: int, db: Session = Depends(get_db)
):
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@router.get("/{deployment_id}/health")
async def get_deployment_health(
    deployment_id: int, db: Session = Depends(get_db)
):
    """Return live AWS ASG health for a running deployment."""
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if not deployment.aws_asg_name:
        return {"status": "not_deployed", "healthy": 0, "total": 0}
    return aws_service.get_asg_health(deployment.aws_asg_name)


@router.delete("/{deployment_id}", status_code=202)
async def delete_deployment(
    deployment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger deletion of all cloud resources.
    Frontend must have already shown double-confirmation before calling this.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if deployment.status in (
        DeploymentStatus.DELETING,
        DeploymentStatus.DELETED,
    ):
        raise HTTPException(
            status_code=409, detail="Deployment is already being deleted"
        )

    background_tasks.add_task(run_deletion, deployment.id, db)
    return {"message": "Deletion started", "id": deployment_id}
