"""
Background Health Poller

Continuously monitors application and infrastructure health,
triggering Discord alerts on state changes.
"""

import asyncio
import logging
from typing import Dict

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.services import discord_service, monitoring_service

logger = logging.getLogger(__name__)

# In-memory state tracking for infrastructure components
previous_infra_state: Dict[str, str] = {}


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
                Deployment.status.in_([
                    DeploymentStatus.RUNNING,
                    DeploymentStatus.DEGRADED
                ])
            )
            .all()
        )

        for deployment in deployments:
            try:
                # Fetch real-time health
                health = await monitoring_service.get_app_health(deployment.name)

                current_db_status = deployment.status
                health_status = health.status  # "healthy", "degraded", "down", "unknown"

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
                            details=details
                        )

                elif current_db_status == DeploymentStatus.DEGRADED:
                    if health_status == "healthy":
                        # App recovered
                        new_db_status = DeploymentStatus.RUNNING
                        should_update = True

                        await discord_service.send_app_alert(
                            app_name=deployment.name,
                            previous_state="DEGRADED",
                            new_state="RUNNING",
                            details="All components are now healthy"
                        )

                # Update DB if needed
                if should_update:
                    deployment.status = new_db_status
                    db.commit()
                    logger.info(
                        "Updated %s status: %s -> %s",
                        deployment.name,
                        current_db_status.value,
                        new_db_status.value
                    )

            except Exception as exc:
                logger.error(
                    "Error checking health for %s: %s",
                    deployment.name,
                    exc,
                    exc_info=True
                )

    finally:
        db.close()


async def check_infra_health() -> None:
    """
    Check global infrastructure health (hypervisors, VPNs).
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
                    component_type="hypervisor"
                )

            previous_infra_state[key] = current_state

        # Check OpenStack VPN
        if health.openstack_vpn:
            key = f"vpn:{health.openstack_vpn.name}"
            current_state = health.openstack_vpn.status
            previous_state = previous_infra_state.get(key)

            if previous_state and previous_state != current_state:
                await discord_service.send_infra_alert(
                    component_name=health.openstack_vpn.name,
                    previous_state=previous_state,
                    new_state=current_state,
                    component_type="vpn"
                )

            previous_infra_state[key] = current_state

        # Check AWS VPNs
        for vpn in health.aws_vpns:
            key = f"aws-vpn:{vpn.name}"
            current_state = vpn.status
            previous_state = previous_infra_state.get(key)

            if previous_state and previous_state != current_state:
                await discord_service.send_infra_alert(
                    component_name=vpn.name,
                    previous_state=previous_state,
                    new_state=current_state,
                    component_type="aws-vpn"
                )

            previous_infra_state[key] = current_state

    except Exception as exc:
        logger.error("Error checking infrastructure health: %s", exc, exc_info=True)


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
                check_infra_health(),
                return_exceptions=True
            )
        except Exception as exc:
            logger.error("Error in health poller loop: %s", exc, exc_info=True)

        # Wait 60 seconds before next check
        await asyncio.sleep(60)
