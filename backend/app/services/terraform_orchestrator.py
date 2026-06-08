"""
Terraform Deployment Orchestrator

Manages the full lifecycle of Terraform-based deployments:
- Initialize Terraform
- Apply configuration
- Track outputs
- Destroy resources
"""

import json
import logging
from pathlib import Path

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.services.template_repository import get_repository
from app.services.terraform_executor import create_executor

logger = logging.getLogger(__name__)


def run_deployment(deployment_id: int) -> None:
    """
    Execute a Terraform deployment in the background.

    Flow:
    1. Get template from repository
    2. Initialize Terraform
    3. Apply with user variables
    4. Capture outputs
    5. Mark as RUNNING
    """
    # Create a new database session for this background task
    db = SessionLocal()
    try:
        deployment = db.get(Deployment, deployment_id)
        if not deployment:
            logger.error("Deployment %s not found", deployment_id)
            return

        try:
            # Parse user configuration
            app_config = json.loads(deployment.app_config or "{}")

            # CRITICAL: Inject app_name from deployment name
            # This ensures Terraform resources are named correctly
            app_config["app_name"] = deployment.name
            # Get template from repository
            repo = get_repository()
            template = repo.get_template_by_id(deployment.template_id)
            if not template:
                raise ValueError(
                    f"Template {deployment.template_id} not found"
                )

            template_path = Path(template["_template_path"])
            if not template_path.exists():
                raise ValueError(
                    f"Template path does not exist: {template_path}"
                )

            # Create Terraform executor
            executor = create_executor(template_path, deployment.name)

            # Step 1: Initialize
            _update(
                db,
                deployment,
                DeploymentStatus.INITIALIZING,
                "🔧 Initializing Terraform...",
            )
            executor.init()

            # Step 2: Plan (optional, for logging)
            _update(
                db,
                deployment,
                DeploymentStatus.PLANNING,
                "📋 Planning deployment...",
            )
            plan_output = executor.plan(app_config)
            logger.info("Terraform plan:\n%s", plan_output)

            # Step 3: Apply
            _update(
                db,
                deployment,
                DeploymentStatus.DEPLOYING,
                "🚀 Deploying resources...",
            )
            outputs = executor.apply(app_config)

            # Step 4: Capture outputs and state
            deployment.terraform_outputs = json.dumps(outputs)
            deployment.terraform_state_path = str(executor.state_dir)

            state_summary = executor.get_state_summary()
            deployment.resource_count = state_summary.get("resource_count", 0)

            db.commit()

            # Step 5: Success
            # Build a friendly message with key outputs
            output_msg = _format_outputs_message(outputs)
            _update(
                db,
                deployment,
                DeploymentStatus.RUNNING,
                f"✅ Running - {output_msg}",
            )

        except Exception as exc:
            logger.error(
                "Deployment %s failed: %s", deployment_id, exc, exc_info=True
            )
            _update(
                db,
                deployment,
                DeploymentStatus.FAILED,
                f"❌ Deployment failed: {str(exc)[:200]}",
            )
    finally:
        db.close()


def run_deletion(deployment_id: int) -> None:
    """
    Destroy all Terraform-managed resources for a deployment.
    """
    # Create a new database session for this background task
    db = SessionLocal()
    try:
        deployment = db.get(Deployment, deployment_id)
        if not deployment:
            logger.error("Deployment %s not found", deployment_id)
            return

        try:
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "🗑️ Destroying resources...",
            )

            # Get template and create executor
            repo = get_repository()
            template = repo.get_template_by_id(deployment.template_id)
            if not template:
                raise ValueError(
                    f"Template {deployment.template_id} not found"
                )

            template_path = Path(template["_template_path"])
            executor = create_executor(template_path, deployment.name)

            # Parse original config for destroy
            app_config = json.loads(deployment.app_config or "{}")

            # CRITICAL: Inject app_name for destroy to match apply
            app_config["app_name"] = deployment.name
            # Destroy resources
            executor.destroy(app_config)

            _update(
                db,
                deployment,
                DeploymentStatus.DELETED,
                "✅ Resources destroyed",
            )

        except Exception as exc:
            logger.error(
                "Deletion %s failed: %s", deployment_id, exc, exc_info=True
            )
            _update(
                db,
                deployment,
                DeploymentStatus.FAILED,
                f"❌ Deletion failed: {str(exc)[:200]}",
            )
    finally:
        db.close()


def _update(
    db,
    deployment: Deployment,
    status: DeploymentStatus,
    message: str,
) -> None:
    """Helper to persist status + message atomically."""
    deployment.status = status
    deployment.step_message = message
    db.commit()
    logger.info("[%s] %s", status.value, message)


def _format_outputs_message(outputs: dict) -> str:
    """
    Format Terraform outputs into a friendly message.
    Prioritizes common output names like 'ip', 'url', 'endpoint', etc.
    """
    if not outputs:
        return "No outputs"

    # Priority keys to display
    priority_keys = [
        "loadbalancer_ip",
        "lb_ip",
        "public_ip",
        "ip",
        "url",
        "endpoint",
        "dns",
        "address",
    ]

    for key in priority_keys:
        if key in outputs:
            return f"{key}: {outputs[key]}"

    # Fallback: show first output
    first_key = next(iter(outputs))
    return f"{first_key}: {outputs[first_key]}"
