"""
Real-time Infrastructure Monitoring Service

Provides health monitoring for:
- Global infrastructure (VPNs, OpenStack hypervisors)
- Application-specific resources (AWS ASGs, OpenStack VMs)

Uses direct SDK calls (boto3, openstacksdk) to get real-time status,
bypassing unreliable Terraform state for auto-scaling resources.
"""

import asyncio
import logging
from typing import Any

import boto3
import openstack
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Pydantic Models for Response Schemas ──────────────────────────────────


from pydantic import BaseModel


class VPNStatus(BaseModel):
    name: str
    status: str
    ip: str | None


class HypervisorStatus(BaseModel):
    name: str
    state: str
    status: str
    ip: str | None


class GlobalHealthResponse(BaseModel):
    openstack_vpn: VPNStatus | None
    aws_vpns: list[VPNStatus]
    openstack_hypervisors: list[HypervisorStatus]


class VMInstance(BaseModel):
    instance_id: str
    private_ip: str | None
    state: str
    health: str | None  # For AWS: "healthy", "unhealthy", "unused", etc.


class AppHealthResponse(BaseModel):
    deployment_name: str
    status: str  # "healthy", "degraded", "down", "unknown"
    aws_frontend: dict[str, Any] | None
    openstack_backend: dict[str, Any] | None


# ── OpenStack Connection ───────────────────────────────────────────────────


def _get_openstack_connection(project_override: str | None = None) -> openstack.connection.Connection:
    """
    Create OpenStack connection from settings.

    Args:
        project_override: Optional project name to use instead of settings.OS_PROJECT_NAME.
                         Useful for operations requiring different project scope (e.g., admin).
    """
    if not settings.OS_AUTH_URL or not settings.OS_USERNAME or not settings.OS_PASSWORD:
        raise ValueError(
            "OpenStack credentials not configured. Please set OS_AUTH_URL, "
            "OS_USERNAME, and OS_PASSWORD in your .env file."
        )

    # Use override project if provided, otherwise use default from settings
    project_name = project_override if project_override is not None else settings.OS_PROJECT_NAME

    # Disable service discovery to avoid hanging
    return openstack.connect(
        auth_url=settings.OS_AUTH_URL,
        username=settings.OS_USERNAME,
        password=settings.OS_PASSWORD,
        project_name=project_name,
        user_domain_name=settings.OS_USER_DOMAIN_NAME,
        project_domain_name=settings.OS_PROJECT_DOMAIN_NAME,
    )


# ── AWS Clients ────────────────────────────────────────────────────────────


def _get_boto3_client(service: str):
    """Create boto3 client with credentials from settings."""
    return boto3.client(
        service,
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


# ── Global Infrastructure Health ───────────────────────────────────────────


async def get_global_health() -> GlobalHealthResponse:
    """
    Get health status of the base infrastructure:
    - OpenStack VPN gateway
    - AWS VPN instances
    - OpenStack hypervisors
    """
    logger.info("Starting global health check...")
    try:
        # Run all checks concurrently with timeout
        os_vpn_task = asyncio.create_task(_get_openstack_vpn_status())
        aws_vpns_task = asyncio.create_task(_get_aws_vpn_status())
        hypervisors_task = asyncio.create_task(_get_openstack_hypervisors())

        # Add timeout to prevent hanging (30 seconds total)
        try:
            os_vpn, aws_vpns, hypervisors = await asyncio.wait_for(
                asyncio.gather(
                    os_vpn_task,
                    aws_vpns_task,
                    hypervisors_task,
                    return_exceptions=True,
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error("Global health check timed out after 30 seconds")
            # Return empty results on timeout
            return GlobalHealthResponse(
                openstack_vpn=None,
                aws_vpns=[],
                openstack_hypervisors=[],
            )

        # Handle exceptions gracefully
        if isinstance(os_vpn, Exception):
            logger.error("Failed to get OpenStack VPN status: %s", os_vpn)
            os_vpn = None
        if isinstance(aws_vpns, Exception):
            logger.error("Failed to get AWS VPN status: %s", aws_vpns)
            aws_vpns = []
        if isinstance(hypervisors, Exception):
            logger.error("Failed to get hypervisor status: %s", hypervisors)
            hypervisors = []

        logger.info("Global health check completed successfully")
        return GlobalHealthResponse(
            openstack_vpn=os_vpn,
            aws_vpns=aws_vpns,
            openstack_hypervisors=hypervisors,
        )
    except Exception as exc:
        logger.error("Failed to get global health: %s", exc, exc_info=True)
        raise


async def _get_openstack_vpn_status() -> VPNStatus | None:
    """
    Get status of the OpenStack VPN gateway (server named 'vpn-gateway').

    Searches across all projects to ensure the VM is found regardless of current scope.
    """

    def _fetch():
        logger.info("Fetching OpenStack VPN status...")
        try:
            conn = _get_openstack_connection()
            logger.info("Searching for vpn-gateway server across all projects...")

            # List all servers across all projects and find vpn-gateway
            # This is more reliable than find_server() which can hang
            for server in conn.compute.servers(all_projects=True):
                if server.name == "vpn-gateway":
                    # Extract fixed IP
                    fixed_ip = None
                    for network_addresses in server.addresses.values():
                        for addr in network_addresses:
                            if addr.get("OS-EXT-IPS:type") == "fixed":
                                fixed_ip = addr["addr"]
                                break

                    logger.info("Found VPN gateway: %s (IP: %s)", server.name, fixed_ip)
                    return VPNStatus(
                        name=server.name, status=server.status.lower(), ip=fixed_ip
                    )

            logger.warning("VPN gateway server not found")
            return None
        except Exception as exc:
            logger.error("Error fetching OpenStack VPN: %s", exc, exc_info=True)
            return None

    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=10.0)
    except asyncio.TimeoutError:
        logger.error("OpenStack VPN status check timed out after 10 seconds")
        return None


async def _get_aws_vpn_status() -> list[VPNStatus]:
    """Get status of AWS VPN instances (tagged with Role=vpn)."""

    def _fetch():
        logger.info("Fetching AWS VPN status...")
        try:
            ec2 = _get_boto3_client("ec2")
            response = ec2.describe_instances(
                Filters=[{"Name": "tag:Role", "Values": ["vpn"]}]
            )

            vpns = []
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    vpns.append(
                        VPNStatus(
                            name=instance.get("InstanceId", "unknown"),
                            status=instance.get("State", {})
                            .get("Name", "unknown")
                            .lower(),
                            ip=instance.get("PrivateIpAddress"),
                        )
                    )
            logger.info("Found %d AWS VPN instances", len(vpns))
            return vpns
        except Exception as exc:
            logger.error("Error fetching AWS VPN: %s", exc, exc_info=True)
            return []

    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=10.0)
    except asyncio.TimeoutError:
        logger.error("AWS VPN status check timed out after 10 seconds")
        return []


async def _get_openstack_hypervisors() -> list[HypervisorStatus]:
    """
    Get status of OpenStack hypervisors (physical compute nodes).

    Uses admin project scope to bypass RBAC restrictions on hypervisor listing.
    """

    def _fetch():
        logger.info("Fetching OpenStack hypervisors...")
        try:
            # Connect with admin project to bypass RBAC restrictions
            conn = _get_openstack_connection(project_override="admin")
            hypervisors = []
            for hypervisor in conn.compute.hypervisors(details=True):
                # Extract host IP address (management IP)
                host_ip = getattr(hypervisor, 'host_ip', None)

                hypervisors.append(
                    HypervisorStatus(
                        name=hypervisor.name,
                        state=hypervisor.state.lower() if hypervisor.state else "unknown",
                        status=hypervisor.status.lower() if hypervisor.status else "unknown",
                        ip=host_ip,
                    )
                )
            logger.info("Found %d OpenStack hypervisors", len(hypervisors))
            return hypervisors
        except Exception as exc:
            error_msg = str(exc)
            if "403" in error_msg or "Forbidden" in error_msg or "Policy doesn't allow" in error_msg:
                logger.warning("OpenStack hypervisors access forbidden (403) - user lacks permissions")
            else:
                logger.error("Error fetching OpenStack hypervisors: %s", exc, exc_info=True)
            return []

    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=10.0)
    except asyncio.TimeoutError:
        logger.error("OpenStack hypervisors check timed out after 10 seconds")
        return []


# ── Application-Specific Health ────────────────────────────────────────────


async def get_app_health(deployment_name: str) -> AppHealthResponse:
    """
    Get health status for a specific deployed application.

    Checks:
    - AWS Frontend: ASG instances + ALB target health
    - OpenStack Backend: Database VMs

    Returns aggregated health status.
    """
    try:
        # Run checks concurrently
        aws_task = asyncio.create_task(
            _get_aws_frontend_health(deployment_name)
        )
        os_task = asyncio.create_task(
            _get_openstack_backend_health(deployment_name)
        )

        aws_health, os_health = await asyncio.gather(
            aws_task, os_task, return_exceptions=True
        )

        # Handle exceptions
        if isinstance(aws_health, Exception):
            logger.error(
                "Failed to get AWS health for %s: %s",
                deployment_name,
                aws_health,
            )
            aws_health = None
        if isinstance(os_health, Exception):
            logger.error(
                "Failed to get OpenStack health for %s: %s",
                deployment_name,
                os_health,
            )
            os_health = None

        # Aggregate status
        status = _aggregate_health_status(aws_health, os_health)

        return AppHealthResponse(
            deployment_name=deployment_name,
            status=status,
            aws_frontend=aws_health,
            openstack_backend=os_health,
        )
    except Exception as exc:
        logger.error(
            "Failed to get app health for %s: %s", deployment_name, exc
        )
        raise


async def _get_aws_frontend_health(
    deployment_name: str,
) -> dict[str, Any] | None:
    """
    Get AWS frontend health (ASG + ALB target group).

    Returns:
        Dict with ASG info, target health, and instance details.
        Status is degraded if healthy_count < desired_capacity.
    """

    def _fetch():
        try:
            # Updated naming convention after Terraform migration
            asg_name = f"{deployment_name}-asg"
            tg_name = f"{deployment_name}-tg"

            asg_client = _get_boto3_client("autoscaling")
            elbv2_client = _get_boto3_client("elbv2")
            ec2_client = _get_boto3_client("ec2")

            # Get ASG info
            asg_response = asg_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            asgs = asg_response.get("AutoScalingGroups", [])
            if not asgs:
                logger.warning("ASG not found: %s", asg_name)
                return None

            asg = asgs[0]
            desired_capacity = asg.get("DesiredCapacity", 0)
            instance_ids = [i["InstanceId"] for i in asg.get("Instances", [])]

            # Get target group ARN
            tg_response = elbv2_client.describe_target_groups(Names=[tg_name])
            target_groups = tg_response.get("TargetGroups", [])
            if not target_groups:
                logger.warning("Target group not found: %s", tg_name)
                return {
                    "asg_name": asg_name,
                    "desired_capacity": desired_capacity,
                    "instances": [],
                    "healthy_count": 0,
                    "total_count": 0,
                }

            tg_arn = target_groups[0]["TargetGroupArn"]

            # Get target health
            health_response = elbv2_client.describe_target_health(
                TargetGroupArn=tg_arn
            )
            target_health = {
                th["Target"]["Id"]: th["TargetHealth"]["State"]
                for th in health_response.get("TargetHealthDescriptions", [])
            }

            # Get instance details
            instances = []
            if instance_ids:
                ec2_response = ec2_client.describe_instances(
                    InstanceIds=instance_ids
                )
                for reservation in ec2_response.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        instance_id = instance["InstanceId"]
                        instances.append(
                            VMInstance(
                                instance_id=instance_id,
                                private_ip=instance.get("PrivateIpAddress"),
                                state=instance.get("State", {}).get(
                                    "Name", "unknown"
                                ),
                                health=target_health.get(
                                    instance_id, "unknown"
                                ),
                            ).model_dump()
                        )

            healthy_count = sum(
                1 for i in instances if i.get("health") == "healthy"
            )

            logger.info(
                "AWS health for %s: %d/%d healthy (desired: %d)",
                deployment_name,
                healthy_count,
                len(instances),
                desired_capacity,
            )

            return {
                "asg_name": asg_name,
                "desired_capacity": desired_capacity,
                "instances": instances,
                "healthy_count": healthy_count,
                "total_count": len(instances),
            }

        except (ClientError, BotoCoreError) as exc:
            logger.error("AWS API error for %s: %s", deployment_name, exc)
            return None

    return await asyncio.to_thread(_fetch)


async def _get_openstack_backend_health(
    deployment_name: str,
) -> dict[str, Any] | None:
    """
    Get OpenStack backend health for all VMs in this deployment.

    Searches for ALL servers matching pattern: {deployment_name}-*
    This includes DB nodes, web nodes, K3s nodes, tiebreakers, etc.
    Searches across all projects to ensure VMs are found regardless of current scope.
    """

    def _fetch():
        try:
            conn = _get_openstack_connection()
            servers = []

            # Search for ALL servers belonging to this deployment across all projects
            for server in conn.compute.servers(all_projects=True):
                if server.name.startswith(f"{deployment_name}-"):
                    # Extract fixed IP
                    fixed_ip = None
                    for network_addresses in server.addresses.values():
                        for addr in network_addresses:
                            if addr.get("OS-EXT-IPS:type") == "fixed":
                                fixed_ip = addr["addr"]
                                break

                    # A VM is only healthy if status is "ACTIVE"
                    is_healthy = server.status.lower() == "active"

                    servers.append(
                        VMInstance(
                            instance_id=server.id,
                            private_ip=fixed_ip,
                            state=server.status.lower(),
                            health="healthy" if is_healthy else "unhealthy",
                        ).model_dump()
                    )

            if not servers:
                logger.warning("No OpenStack VMs found for deployment: %s", deployment_name)
                return None

            healthy_count = sum(
                1 for s in servers if s.get("health") == "healthy"
            )

            logger.info(
                "OpenStack health for %s: %d/%d healthy VMs",
                deployment_name,
                healthy_count,
                len(servers),
            )

            return {
                "servers": servers,
                "healthy_count": healthy_count,
                "total_count": len(servers),
            }

        except Exception as exc:
            logger.error(
                "OpenStack API error for %s: %s", deployment_name, exc
            )
            return None

    return await asyncio.to_thread(_fetch)


def _aggregate_health_status(
    aws_health: dict[str, Any] | None, os_health: dict[str, Any] | None
) -> str:
    """
    Aggregate health status from AWS and OpenStack components.

    Returns:
        "healthy" - All components healthy and match desired capacity
        "degraded" - Some components unhealthy, missing, or provisioning
        "down" - No healthy components found
        "unknown" - Unable to determine status (no data from either cloud)
    """
    # If we have no data from either cloud, status is unknown
    if aws_health is None and os_health is None:
        logger.warning("No health data from AWS or OpenStack - deployment may not exist")
        return "unknown"

    total_healthy = 0
    total_count = 0
    total_desired = 0

    # AWS health metrics
    if aws_health:
        aws_healthy = aws_health.get("healthy_count", 0)
        aws_total = aws_health.get("total_count", 0)
        aws_desired = aws_health.get("desired_capacity", 0)

        total_healthy += aws_healthy
        total_count += aws_total
        total_desired += aws_desired

        # Check if AWS is degraded (instances missing or unhealthy)
        if aws_desired > aws_total:
            # ASG hasn't spawned all desired instances yet
            logger.debug(
                "AWS degraded: desired=%d, actual=%d", aws_desired, aws_total
            )
        if aws_healthy < aws_total:
            # Some instances are unhealthy
            logger.debug(
                "AWS degraded: healthy=%d, total=%d", aws_healthy, aws_total
            )

    # OpenStack health metrics
    if os_health:
        os_healthy = os_health.get("healthy_count", 0)
        os_total = os_health.get("total_count", 0)

        total_healthy += os_healthy
        total_count += os_total

        if os_healthy < os_total:
            logger.debug(
                "OpenStack degraded: healthy=%d, total=%d", os_healthy, os_total
            )

    # No resources found in either cloud - deployment might not exist or failed
    if total_count == 0:
        logger.warning("No resources found in AWS or OpenStack - deployment may have failed or been deleted")
        return "unknown"

    # Check for degraded state
    # 1. If AWS desired capacity doesn't match actual count
    if aws_health and aws_health.get("desired_capacity", 0) > aws_health.get("total_count", 0):
        return "degraded"

    # 2. If any unhealthy instances exist
    if total_healthy < total_count:
        return "degraded"

    # All instances are healthy and match desired state
    if total_healthy == total_count and total_count > 0:
        return "healthy"

    # Fallback (shouldn't reach here, but safety net)
    return "degraded"
