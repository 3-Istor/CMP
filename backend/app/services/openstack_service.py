"""
OpenStack provisioning service using openstacksdk.
Provisions 2 VMs (stateful/DB layer) per application deployment.
"""

import logging

import openstack

from app.core.config import settings

logger = logging.getLogger(__name__)

# VM flavor for DB nodes - adjust to your OpenStack flavors
OS_FLAVOR = "m1.small"
OS_IMAGE = "Ubuntu-22.04"
OS_NETWORK = "3-istor-internal"
OS_SECURITY_GROUP = "arcl-db-sg"


def _get_connection() -> openstack.connection.Connection:
    """Build an OpenStack connection from environment-based settings."""
    return openstack.connect(
        auth_url=settings.OS_AUTH_URL,
        username=settings.OS_USERNAME,
        password=settings.OS_PASSWORD,
        project_name=settings.OS_PROJECT_NAME,
        user_domain_name=settings.OS_USER_DOMAIN_NAME,
        project_domain_name=settings.OS_PROJECT_DOMAIN_NAME,
    )


def provision_db_vms(
    deployment_name: str, template_id: str, app_config: dict
) -> tuple[dict, dict]:
    """
    Provision 2 OpenStack VMs for the DB layer.
    Returns (vm1_info, vm2_info) dicts with id and ip.
    Raises on failure so the SAGA orchestrator can trigger rollback.
    """
    conn = _get_connection()
    vm1 = vm2 = None

    try:
        user_data = _build_cloud_init(template_id, app_config, node_index=1)
        vm1 = conn.compute.create_server(
            name=f"{deployment_name}-db-1",
            flavor_id=conn.compute.find_flavor(OS_FLAVOR).id,
            image_id=conn.compute.find_image(OS_IMAGE).id,
            networks=[{"uuid": conn.network.find_network(OS_NETWORK).id}],
            security_groups=[{"name": OS_SECURITY_GROUP}],
            user_data=user_data,
        )
        vm1 = conn.compute.wait_for_server(vm1)
        logger.info("OpenStack VM1 ready: %s", vm1.id)

        user_data2 = _build_cloud_init(template_id, app_config, node_index=2)
        vm2 = conn.compute.create_server(
            name=f"{deployment_name}-db-2",
            flavor_id=conn.compute.find_flavor(OS_FLAVOR).id,
            image_id=conn.compute.find_image(OS_IMAGE).id,
            networks=[{"uuid": conn.network.find_network(OS_NETWORK).id}],
            security_groups=[{"name": OS_SECURITY_GROUP}],
            user_data=user_data2,
        )
        vm2 = conn.compute.wait_for_server(vm2)
        logger.info("OpenStack VM2 ready: %s", vm2.id)

        vm1_ip = _get_fixed_ip(vm1)
        vm2_ip = _get_fixed_ip(vm2)

        return (
            {"id": vm1.id, "ip": vm1_ip},
            {"id": vm2.id, "ip": vm2_ip},
        )

    except Exception as exc:
        logger.error("OpenStack provisioning failed: %s", exc)
        # Clean up any partially created VMs before re-raising
        _safe_delete_server(conn, vm1)
        _safe_delete_server(conn, vm2)
        raise


def rollback_db_vms(vm1_id: str | None, vm2_id: str | None) -> None:
    """
    SAGA rollback: destroy OpenStack VMs when AWS deployment fails.
    Called by the orchestrator - must not raise.
    """
    conn = _get_connection()
    for vm_id in [vm1_id, vm2_id]:
        if vm_id:
            try:
                conn.compute.delete_server(vm_id, force=True)
                conn.compute.wait_for_delete(
                    conn.compute.get_server(vm_id), wait=120
                )
                logger.info("Rolled back OpenStack VM: %s", vm_id)
            except Exception as exc:
                # Log but don't raise - rollback must be best-effort
                logger.error(
                    "Failed to rollback OpenStack VM %s: %s", vm_id, exc
                )


def delete_db_vms(vm1_id: str | None, vm2_id: str | None) -> None:
    """Delete VMs as part of a full app deletion."""
    rollback_db_vms(vm1_id, vm2_id)


def _get_fixed_ip(server) -> str:
    """Extract the first fixed IP from a server's network addresses."""
    for network_addresses in server.addresses.values():
        for addr in network_addresses:
            if addr.get("OS-EXT-IPS:type") == "fixed":
                return addr["addr"]
    return ""


def _safe_delete_server(conn, server) -> None:
    if server:
        try:
            conn.compute.delete_server(server.id, force=True)
        except Exception:
            pass


def _build_cloud_init(
    template_id: str, app_config: dict, node_index: int
) -> str:
    """Generate a minimal cloud-init user-data script per template."""
    scripts = {
        "wordpress": f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y mysql-server

# Secure MySQL and create WordPress database
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{app_config.get('db_password', 'changeme')}';"  # pylint: disable=line-too-long
mysql -uroot -p'{app_config.get('db_password', 'changeme')}' -e "  # pylint: disable=line-too-long
  CREATE DATABASE IF NOT EXISTS wordpress CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;  # pylint: disable=line-too-long
  CREATE USER IF NOT EXISTS 'wordpress'@'%' IDENTIFIED BY '{app_config.get('db_password', 'changeme')}';  # pylint: disable=line-too-long
  GRANT ALL PRIVILEGES ON wordpress.* TO 'wordpress'@'%';
  FLUSH PRIVILEGES;
"

# Allow remote connections (web layer on AWS will connect)
sed -i 's/bind-address.*/bind-address = 0.0.0.0/' /etc/mysql/mysql.conf.d/mysqld.cnf
systemctl restart mysql
systemctl enable mysql
""",
        "nextcloud": f"""#!/bin/bash
apt-get update -y
apt-get install -y postgresql
sudo -u postgres psql -c "CREATE USER nextcloud WITH PASSWORD '{app_config.get('admin_password', 'changeme')}';"  # pylint: disable=line-too-long
sudo -u postgres psql -c "CREATE DATABASE nextcloud OWNER nextcloud;"
""",
        "gitlab": f"""#!/bin/bash
apt-get update -y
apt-get install -y postgresql redis-server
sudo -u postgres psql -c "CREATE USER gitlab WITH PASSWORD '{app_config.get('root_password', 'changeme')}';"  # pylint: disable=line-too-long
sudo -u postgres psql -c "CREATE DATABASE gitlabhq_production OWNER gitlab;"
""",
        "grafana": """#!/bin/bash
apt-get update -y
apt-get install -y prometheus
systemctl enable prometheus && systemctl start prometheus
""",
    }
    return scripts.get(template_id, "#!/bin/bash\napt-get update -y\n")
