"""
SAGA Orchestrator - implements the "Design for Failure" pattern.

Flow:
  1. Deploy OpenStack VMs (DB layer)        → on failure: cleanup OS VMs, mark FAILED
  2. Deploy AWS ASG + ALB (web layer)       → on failure: rollback OS VMs, mark FAILED
  3. Mark deployment RUNNING

This runs as a FastAPI BackgroundTask so the API never blocks.
"""

import json
import logging

from sqlalchemy.orm import Session

from app.models.deployment import Deployment, DeploymentStatus
from app.services import aws_service, openstack_service

logger = logging.getLogger(__name__)


def run_deployment(deployment_id: int, db: Session) -> None:
    """
    Entry point called by the background task.
    Fetches the deployment record and runs the full SAGA.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        logger.error("Deployment %s not found", deployment_id)
        return

    app_config = json.loads(deployment.app_config or "{}")

    # ── Step 1: OpenStack ─────────────────────────────────────────────────
    _update(
        db,
        deployment,
        DeploymentStatus.DEPLOYING_OPENSTACK,  # pylint: disable=no-member
        "🔧 Deploying OpenStack DB VMs...",
    )
    try:
        vm1, vm2 = openstack_service.provision_db_vms(
            deployment.name, deployment.template_id, app_config
        )
        deployment.os_vm_db1_id = vm1["id"]
        deployment.os_vm_db1_ip = vm1["ip"]
        deployment.os_vm_db2_id = vm2["id"]
        deployment.os_vm_db2_ip = vm2["ip"]
        db.commit()
        logger.info("OpenStack VMs ready for deployment %s", deployment_id)
    except Exception as exc:
        logger.error("OpenStack step failed: %s", exc)
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            f"❌ OpenStack provisioning failed: {exc}",
        )
        return

    # ── Step 2: AWS ───────────────────────────────────────────────────────
    _update(
        db,
        deployment,
        DeploymentStatus.DEPLOYING_AWS,  # pylint: disable=no-member
        "☁️ Deploying AWS ASG + Load Balancer...",
    )
    try:
        aws_result = aws_service.provision_web_layer(
            deployment.name,
            deployment.template_id,
            app_config,
            deployment.os_vm_db1_ip,
            deployment.os_vm_db2_ip,
        )
        deployment.aws_asg_name = aws_result["asg_name"]
        deployment.aws_alb_dns = aws_result["alb_dns"]
        db.commit()
        logger.info("AWS layer ready for deployment %s", deployment_id)
    except Exception as exc:
        logger.error("AWS step failed - triggering SAGA rollback: %s", exc)
        _update(
            db,
            deployment,
            DeploymentStatus.ROLLING_BACK,  # pylint: disable=no-member
            "⏪ AWS failed - rolling back OpenStack VMs...",
        )
        openstack_service.rollback_db_vms(
            deployment.os_vm_db1_id, deployment.os_vm_db2_id
        )
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            f"❌ AWS provisioning failed. OpenStack rolled back. Error: {exc}",
        )
        return

    # ── Step 3: Done ──────────────────────────────────────────────────────
    _update(
        db,
        deployment,
        DeploymentStatus.RUNNING,
        f"✅ Running - {deployment.aws_alb_dns}",
    )


def run_deletion(deployment_id: int, db: Session) -> None:
    """
    Delete all cloud resources for a deployment (OpenStack + AWS).
    Called as a background task after double-confirmation.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        return

    _update(
        db,
        deployment,
        DeploymentStatus.DELETING,
        "🗑️ Deleting AWS resources...",
    )
    if deployment.aws_asg_name:
        aws_service.delete_web_layer(deployment.aws_asg_name, deployment.name)

    _update(
        db,
        deployment,
        DeploymentStatus.DELETING,
        "🗑️ Deleting OpenStack VMs...",
    )
    openstack_service.delete_db_vms(
        deployment.os_vm_db1_id, deployment.os_vm_db2_id
    )

    _update(db, deployment, DeploymentStatus.DELETED, "✅ Deleted")


def _update(
    db: Session,
    deployment: Deployment,
    status: DeploymentStatus,
    message: str,
) -> None:
    """Helper to persist status + message atomically."""
    deployment.status = status
    deployment.step_message = message
    db.commit()
    logger.info("[%s] %s", status.value, message)
