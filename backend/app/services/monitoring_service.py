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


def _get_openstack_connection() -> openstack.connection.Connection:
    """Create OpenStack connection from settings."""
    return openstack.connect(
        auth_url=settings.OS_AUTH_URL,
        username=settings.OS_USERNAME,
        password=settings.OS_PASSWORD,
        project_name=settings.OS_PROJECT_NAME,
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
    try:
        # Run all checks concurrently
        os_vpn_task = asyncio.create_task(_get_openstack_vpn_status())
        aws_vpns_task = asyncio.create_task(_get_aws_vpn_status())
        hypervisors_task = asyncio.create_task(_get_openstack_hypervisors())

        os_vpn, aws_vpns, hypervisors = await asyncio.gather(
            os_vpn_task,
            aws_vpns_task,
            hypervisors_task,
            return_exceptions=True,
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

        return GlobalHealthResponse(
            openstack_vpn=os_vpn,
            aws_vpns=aws_vpns,
            openstack_hypervisors=hypervisors,
        )
    except Exception as exc:
        logger.error("Failed to get global health: %s", exc)
        raise


async def _get_openstack_vpn_status() -> VPNStatus | None:
    """Get status of the OpenStack VPN gateway (server named 'vpn-gateway')."""

    def _fetch():
        conn = _get_openstack_connection()
        server = conn.compute.find_server("vpn-gateway")
        if not server:
            return None

        # Extract fixed IP
        fixed_ip = None
        for network_addresses in server.addresses.values():
            for addr in network_addresses:
                if addr.get("OS-EXT-IPS:type") == "fixed":
                    fixed_ip = addr["addr"]
                    break

        return VPNStatus(
            name=server.name, status=server.status.lower(), ip=fixed_ip
        )

    return await asyncio.to_thread(_fetch)


async def _get_aws_vpn_status() -> list[VPNStatus]:
    """Get status of AWS VPN instances (tagged with Role=vpn)."""

    def _fetch():
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
        return vpns

    return await asyncio.to_thread(_fetch)


async def _get_openstack_hypervisors() -> list[HypervisorStatus]:
    """Get status of OpenStack hypervisors (physical compute nodes)."""

    def _fetch():
        conn = _get_openstack_connection()
        hypervisors = []
        for hypervisor in conn.compute.hypervisors():
            hypervisors.append(
                HypervisorStatus(
                    name=hypervisor.name,
                    state=hypervisor.state.lower(),
                    status=hypervisor.status.lower(),
                )
            )
        return hypervisors

    return await asyncio.to_thread(_fetch)


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
        Dict with ASG info, target health, and instance details
    """

    def _fetch():
        try:
            asg_name = f"arcl-{deployment_name}-asg"
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
                return None

            asg = asgs[0]
            desired_capacity = asg.get("DesiredCapacity", 0)
            instance_ids = [i["InstanceId"] for i in asg.get("Instances", [])]

            # Get target group ARN
            tg_response = elbv2_client.describe_target_groups(Names=[tg_name])
            target_groups = tg_response.get("TargetGroups", [])
            if not target_groups:
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
    Get OpenStack backend health (database VMs).

    Looks for servers matching pattern: {deployment_name}-db-*
    """

    def _fetch():
        try:
            conn = _get_openstack_connection()
            servers = []

            # Search for servers with naming convention
            for server in conn.compute.servers():
                if server.name.startswith(f"{deployment_name}-db-"):
                    # Extract fixed IP
                    fixed_ip = None
                    for network_addresses in server.addresses.values():
                        for addr in network_addresses:
                            if addr.get("OS-EXT-IPS:type") == "fixed":
                                fixed_ip = addr["addr"]
                                break

                    servers.append(
                        VMInstance(
                            instance_id=server.id,
                            private_ip=fixed_ip,
                            state=server.status.lower(),
                            health=(
                                "healthy"
                                if server.status.lower() == "active"
                                else "unhealthy"
                            ),
                        ).model_dump()
                    )

            if not servers:
                return None

            healthy_count = sum(
                1 for s in servers if s.get("health") == "healthy"
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
        "healthy" - All components healthy
        "degraded" - Some components unhealthy or provisioning
        "down" - No healthy components
        "unknown" - Unable to determine status
    """
    if aws_health is None and os_health is None:
        return "unknown"

    total_healthy = 0
    total_count = 0

    if aws_health:
        total_healthy += aws_health.get("healthy_count", 0)
        total_count += aws_health.get("total_count", 0)

    if os_health:
        total_healthy += os_health.get("healthy_count", 0)
        total_count += os_health.get("total_count", 0)

    if total_count == 0:
        return "down"

    if total_healthy == total_count:
        return "healthy"
    elif total_healthy > 0:
        return "degraded"
    else:
        return "down"
