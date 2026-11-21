"""
Background Health Poller

Continuously monitors application and infrastructure health,
triggering Discord alerts on state changes.
"""

import asyncio
import logging
from typing import Dict

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.services import discord_service, monitoring_service, notifications
from app.services.app_health import get_kubernetes_app_health

logger = logging.getLogger(__name__)

# In-memory state tracking for infrastructure components
previous_infra_state: Dict[str, str] = {}


async def _notify_health(deployment: Deployment, kind: str) -> None:
    if not deployment.project_id:
        return
    await asyncio.to_thread(
        notifications.notify_project,
        deployment.project_id,
        kind="alert",
        title_key=kind,
        params={"app": deployment.name},
        link=f"/projects/{deployment.project_id}/apps/{deployment.id}",
        app=deployment.name,
    )


async def check_app_health() -> None:
    """
    Check health of all running/degraded applications.
    Update DB and send Discord alerts on state changes.
    """
    db = SessionLocal()
    try:
        # Get all deployments that should be monitored
        deployments = (
            db.query(Deployment)
            .filter(
                Deployment.status.in_(
                    [DeploymentStatus.RUNNING, DeploymentStatus.DEGRADED]
                ),
                # This probe reads AWS ASGs and OpenStack VMs: a Kubernetes
                # app has neither, and each probe costs a Keystone login.
                Deployment.provider_type != ProviderType.KUBERNETES,
            )
            .all()
        )

        for deployment in deployments:
            try:
                # Fetch real-time health
                health = await monitoring_service.get_app_health(
                    deployment.name
                )

                current_db_status = deployment.status
                health_status = (
                    health.status
                )  # "healthy", "degraded", "down", "unknown"

                # Determine if we need to update the DB status
                should_update = False
                new_db_status = current_db_status

                if current_db_status == DeploymentStatus.RUNNING:
                    if health_status in ["degraded", "down"]:
                        # App was healthy but is now degraded/down
                        new_db_status = DeploymentStatus.DEGRADED
                        should_update = True

                        # Send Discord alert
                        details = f"Healthy: {health.aws_frontend.get('healthy_count', 0) if health.aws_frontend else 0}/{health.aws_frontend.get('total_count', 0) if health.aws_frontend else 0} AWS, "
                        details += f"{health.openstack_backend.get('healthy_count', 0) if health.openstack_backend else 0}/{health.openstack_backend.get('total_count', 0) if health.openstack_backend else 0} OpenStack"

                        await discord_service.send_app_alert(
                            app_name=deployment.name,
                            previous_state="RUNNING",
                            new_state="DEGRADED",
                            details=details,
                        )
                        await _notify_health(deployment, "app_degraded")

                elif current_db_status == DeploymentStatus.DEGRADED:
                    if health_status == "healthy":
                        # App recovered
                        new_db_status = DeploymentStatus.RUNNING
                        should_update = True

                        await discord_service.send_app_alert(
                            app_name=deployment.name,
                            previous_state="DEGRADED",
                            new_state="RUNNING",
                            details="All components are now healthy",
                        )
                        await _notify_health(deployment, "app_recovered")

                # Update DB if needed
                if should_update:
                    deployment.status = new_db_status
                    db.commit()
                    logger.info(
                        "Updated %s status: %s -> %s",
                        deployment.name,
                        current_db_status.value,
                        new_db_status.value,
                    )

            except Exception as exc:
                logger.error(
                    "Error checking health for %s: %s",
                    deployment.name,
                    exc,
                    exc_info=True,
                )

    finally:
        db.close()


async def recover_failed_apps() -> None:
    """Mark running the failed apps whose apply finished and that are healthy.

    Catalog Kubernetes apps only: imports keep their own run, and a failed
    deletion ("Deletion ...") stays visible until it is deleted again.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Deployment)
            .filter(
                Deployment.status == DeploymentStatus.FAILED,
                Deployment.provider_type == ProviderType.KUBERNETES,
                Deployment.terraform_outputs.isnot(None),
                ~Deployment.template_id.startswith("import-"),
                Deployment.step_message.startswith("Deployment"),
            )
            .order_by(Deployment.id)
            .all()
        )
        for deployment in rows:
            try:
                health = await get_kubernetes_app_health(deployment)
                if health.status != "healthy":
                    continue
                # Only if still failed: a delete may have started meanwhile.
                updated = (
                    db.query(Deployment)
                    .filter(
                        Deployment.id == deployment.id,
                        Deployment.status == DeploymentStatus.FAILED,
                    )
                    .update(
                        {
                            Deployment.status: DeploymentStatus.RUNNING,
                            Deployment.step_message: (
                                "Running - recovered after a failed deployment"
                            ),
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
                if not updated:
                    continue
                logger.info("%s is healthy again: running", deployment.name)
                await _notify_health(deployment, "app_recovered")
            # One app must not stop the others.
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.error(
                    "Error recovering %s: %s",
                    deployment.name,
                    exc,
                    exc_info=True,
                )
    finally:
        db.close()


async def check_infra_health() -> None:
    """
    Check global infrastructure health (hypervisors).
    Send Discord alerts on state changes.
    """
    try:
        health = await monitoring_service.get_global_health()

        # Check OpenStack hypervisors
        for hv in health.openstack_hypervisors:
            key = f"hypervisor:{hv.name}"
            current_state = hv.state
            previous_state = previous_infra_state.get(key)

            if previous_state and previous_state != current_state:
                # State changed
                await discord_service.send_infra_alert(
                    component_name=hv.name,
                    previous_state=previous_state,
                    new_state=current_state,
                    component_type="hypervisor",
                )

            previous_infra_state[key] = current_state

    except Exception as exc:
        logger.error(
            "Error checking infrastructure health: %s", exc, exc_info=True
        )


async def health_poller_loop() -> None:
    """
    Main background loop that runs health checks every 60 seconds.
    """
    logger.info("Health poller started")

    while True:
        try:
            # Run both checks concurrently
            await asyncio.gather(
                check_app_health(),
                recover_failed_apps(),
                check_infra_health(),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.error("Error in health poller loop: %s", exc, exc_info=True)

        # Wait 60 seconds before next check
        await asyncio.sleep(60)
