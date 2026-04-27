import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.schemas.deployment import DeploymentCreate, DeploymentRead
from app.services import terraform_orchestrator
from app.services.catalog_service import get_template_by_id

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
    Create a deployment record and kick off Terraform deployment in the background.
    Returns immediately with status=pending so the frontend can start polling.
    """
    # Validate template exists
    template = get_template_by_id(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    deployment = Deployment(
        name=payload.name,
        template_id=payload.template_id,
        template_name=template.name,
        template_icon=template.icon,
        template_category=template.category,
        app_config=json.dumps(payload.app_config),
        status=DeploymentStatus.PENDING,
        step_message="Queued...",
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    # Run Terraform deployment asynchronously
    # Note: Pass deployment ID, not the db session (session will be closed)
    background_tasks.add_task(
        terraform_orchestrator.run_deployment, deployment.id
    )

    return deployment


@router.get("/{deployment_id}", response_model=DeploymentRead)
async def get_deployment(deployment_id: int, db: Session = Depends(get_db)):
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@router.get("/{deployment_id}/outputs")
async def get_deployment_outputs(
    deployment_id: int, db: Session = Depends(get_db)
):
    """Return Terraform outputs for a deployment."""
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if not deployment.terraform_outputs:
        return {}

    try:
        return json.loads(deployment.terraform_outputs)
    except Exception:
        return {}


@router.delete("/{deployment_id}", status_code=202)
async def delete_deployment(
    deployment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger deletion of all Terraform-managed resources.
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

    background_tasks.add_task(
        terraform_orchestrator.run_deletion, deployment.id
    )
    return {"message": "Deletion started", "id": deployment_id}
