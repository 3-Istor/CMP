"""
AWS provisioning service using boto3.
Provisions 2 VMs via an Auto Scaling Group + ALB (stateless/web layer).
Budget constraint: ONLY t3.micro or t4g.nano instances allowed.
"""

import json
import logging
import time

import boto3

from app.core.config import settings

logger = logging.getLogger(__name__)

# Hardcoded budget-safe instance type — DO NOT change to larger instances
ALLOWED_INSTANCE_TYPES = {"t3.micro", "t4g.nano"}
INSTANCE_TYPE = settings.AWS_INSTANCE_TYPE
assert (
    INSTANCE_TYPE in ALLOWED_INSTANCE_TYPES
), f"Instance type {INSTANCE_TYPE} exceeds budget constraints!"

AMI_MAP = {
    "eu-west-3": "ami-0f15d55736fd476da",  # Ubuntu 22.04 LTS eu-west-3
    "eu-west-1": "ami-0694d931cee176e7d",
    "us-east-1": "ami-0c7217cdde317cfec",
}


def _ec2():
    return boto3.client(
        "ec2",
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _elbv2():
    return boto3.client(
        "elbv2",
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _autoscaling():
    return boto3.client(
        "autoscaling",
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def provision_web_layer(
    deployment_name: str,
    template_id: str,
    app_config: dict,
    os_db1_ip: str,
    os_db2_ip: str,
) -> dict:
    """
    Provision AWS web layer: Launch Template + ASG (min 2) + ALB.
    Returns dict with asg_name and alb_dns.
    Raises on failure so the SAGA orchestrator triggers OpenStack rollback.
    """
    ec2 = _ec2()
    elb = _elbv2()
    asg = _autoscaling()
    region = settings.AWS_DEFAULT_REGION
    ami = AMI_MAP.get(region, AMI_MAP["eu-west-3"])

    # Reuse existing VPC/subnet tagged for ARCL, or use defaults
    vpc_id, subnet_ids = _get_or_create_vpc(ec2)
    sg_id = _get_or_create_security_group(ec2, vpc_id, deployment_name)

    user_data = _build_cloud_init(
        template_id, app_config, os_db1_ip, os_db2_ip
    )

    # 1. Create Launch Template
    lt_name = f"arcl-{deployment_name}-lt"
    lt_resp = ec2.create_launch_template(
        LaunchTemplateName=lt_name,
        LaunchTemplateData={
            "ImageId": ami,
            "InstanceType": INSTANCE_TYPE,
            "SecurityGroupIds": [sg_id],
            "UserData": _b64(user_data),
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "arcl-app", "Value": deployment_name},
                        {"Key": "arcl-managed", "Value": "true"},
                    ],
                }
            ],
        },
    )
    lt_id = lt_resp["LaunchTemplate"]["LaunchTemplateId"]
    logger.info("Created Launch Template: %s", lt_id)

    # 2. Create ALB
    alb_name = f"arcl-{deployment_name}-alb"[:32]
    alb_resp = elb.create_load_balancer(
        Name=alb_name,
        Subnets=subnet_ids,
        SecurityGroups=[sg_id],
        Scheme="internet-facing",
        Type="application",
        IpAddressType="ipv4",
        Tags=[{"Key": "arcl-app", "Value": deployment_name}],
    )
    alb_arn = alb_resp["LoadBalancers"][0]["LoadBalancerArn"]
    alb_dns = alb_resp["LoadBalancers"][0]["DNSName"]
    logger.info("Created ALB: %s", alb_dns)

    # 3. Create Target Group
    tg_resp = elb.create_target_group(
        Name=f"arcl-{deployment_name}-tg"[:32],
        Protocol="HTTP",
        Port=80,
        VpcId=vpc_id,
        HealthCheckPath="/",
        TargetType="instance",
    )
    tg_arn = tg_resp["TargetGroups"][0]["TargetGroupArn"]

    # 4. Create ALB Listener
    elb.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
    )

    # 5. Create ASG (min 2 for HA, max 2 for budget)
    asg_name = f"arcl-{deployment_name}-asg"
    asg.create_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        LaunchTemplate={"LaunchTemplateId": lt_id, "Version": "$Latest"},
        MinSize=2,
        MaxSize=2,  # Hard cap for budget
        DesiredCapacity=2,
        VPCZoneIdentifier=",".join(subnet_ids),
        TargetGroupARNs=[tg_arn],
        HealthCheckType="ELB",
        HealthCheckGracePeriod=120,
        Tags=[
            {
                "Key": "arcl-app",
                "Value": deployment_name,
                "PropagateAtLaunch": True,
            }
        ],
    )
    logger.info("Created ASG: %s", asg_name)

    return {
        "asg_name": asg_name,
        "alb_dns": alb_dns,
        "alb_arn": alb_arn,
        "tg_arn": tg_arn,
        "lt_id": lt_id,
    }


def delete_web_layer(asg_name: str, deployment_name: str) -> None:
    """
    Delete all AWS resources for a deployment.
    Called on full app deletion or SAGA rollback.
    """
    asg = _autoscaling()
    elb = _elbv2()
    ec2 = _ec2()

    # Delete ASG first (terminates instances)
    try:
        asg.delete_auto_scaling_group(
            AutoScalingGroupName=asg_name, ForceDelete=True
        )
        logger.info("Deleted ASG: %s", asg_name)
        time.sleep(5)
    except Exception as exc:
        logger.error("Failed to delete ASG %s: %s", asg_name, exc)

    # Delete ALB + Target Group
    try:
        albs = elb.describe_load_balancers(
            Names=[f"arcl-{deployment_name}-alb"[:32]]
        )
        for alb in albs.get("LoadBalancers", []):
            listeners = elb.describe_listeners(
                LoadBalancerArn=alb["LoadBalancerArn"]
            )
            for l in listeners.get("Listeners", []):
                elb.delete_listener(ListenerArn=l["ListenerArn"])
            elb.delete_load_balancer(LoadBalancerArn=alb["LoadBalancerArn"])
        logger.info("Deleted ALB for %s", deployment_name)
    except Exception as exc:
        logger.error("Failed to delete ALB: %s", exc)

    # Delete Launch Template
    try:
        ec2.delete_launch_template(
            LaunchTemplateName=f"arcl-{deployment_name}-lt"
        )
    except Exception as exc:
        logger.error("Failed to delete Launch Template: %s", exc)


def get_asg_health(asg_name: str) -> dict:
    """Return health info for an ASG (used by dashboard health indicators)."""
    asg = _autoscaling()
    try:
        resp = asg.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )
        groups = resp.get("AutoScalingGroups", [])
        if not groups:
            return {"healthy": 0, "total": 0, "status": "unknown"}
        group = groups[0]
        instances = group.get("Instances", [])
        healthy = sum(
            1
            for i in instances
            if i.get("HealthStatus") == "Healthy"
            and i.get("LifecycleState") == "InService"
        )
        return {
            "healthy": healthy,
            "total": len(instances),
            "status": "healthy" if healthy == len(instances) else "degraded",
        }
    except Exception as exc:
        logger.error("Failed to get ASG health for %s: %s", asg_name, exc)
        return {"healthy": 0, "total": 0, "status": "error"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_or_create_vpc(ec2_client) -> tuple[str, list[str]]:
    """Find the default VPC and its subnets, or use ARCL-tagged ones."""
    vpcs = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )
    vpc_id = vpcs["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    # Use up to 2 subnets in different AZs for ALB
    subnet_ids = list(
        {
            s["AvailabilityZone"]: s["SubnetId"] for s in subnets["Subnets"]
        }.values()
    )[:2]
    return vpc_id, subnet_ids


def _get_or_create_security_group(
    ec2_client, vpc_id: str, deployment_name: str
) -> str:
    """Create a security group for the deployment's web layer."""
    sg_name = f"arcl-{deployment_name}-sg"
    try:
        existing = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [sg_name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )
        if existing["SecurityGroups"]:
            return existing["SecurityGroups"][0]["GroupId"]
    except Exception:
        pass

    sg = ec2_client.create_security_group(
        GroupName=sg_name,
        Description=f"ARCL security group for {deployment_name}",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]
    ec2_client.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 80,
                "ToPort": 80,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            },
            # Allow VPN access from WireGuard network
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": settings.VPN_NETWORK}],
            },
        ],
    )
    return sg_id


def _b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode()).decode()


def _build_cloud_init(
    template_id: str, app_config: dict, db1_ip: str, db2_ip: str
) -> str:
    """Generate cloud-init for the web/app layer, pointing to OpenStack DBs."""
    scripts = {
        "wordpress": f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y nginx php8.1-fpm php8.1-mysql php8.1-xml php8.1-curl php8.1-gd php8.1-mbstring php8.1-zip wget

# Download and configure WordPress
wget -q https://wordpress.org/latest.tar.gz -O /tmp/wordpress.tar.gz
tar -xzf /tmp/wordpress.tar.gz -C /var/www/html --strip-components=1
chown -R www-data:www-data /var/www/html

# WordPress config pointing to OpenStack DB
cp /var/www/html/wp-config-sample.php /var/www/html/wp-config.php
sed -i "s/database_name_here/wordpress/" /var/www/html/wp-config.php
sed -i "s/username_here/wordpress/" /var/www/html/wp-config.php
sed -i "s/password_here/{app_config.get('db_password', 'changeme')}/" /var/www/html/wp-config.php
sed -i "s/localhost/{db1_ip}/" /var/www/html/wp-config.php

# Nginx config
cat > /etc/nginx/sites-available/wordpress <<'NGINX'
server {{
    listen 80;
    root /var/www/html;
    index index.php;
    location / {{ try_files $uri $uri/ /index.php?$args; }}
    location ~ \\.php$ {{
        fastcgi_pass unix:/run/php/php8.1-fpm.sock;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
    }}
}}
NGINX
ln -sf /etc/nginx/sites-available/wordpress /etc/nginx/sites-enabled/default
systemctl enable nginx php8.1-fpm
systemctl restart nginx php8.1-fpm
""",
        "nextcloud": f"""#!/bin/bash
apt-get update -y
apt-get install -y nginx php-fpm nextcloud
# Point Nextcloud to OpenStack DB at {db1_ip}
systemctl enable nginx && systemctl start nginx
""",
        "gitlab": f"""#!/bin/bash
apt-get update -y
curl -sS https://packages.gitlab.com/install/repositories/gitlab/gitlab-ce/script.deb.sh | bash
EXTERNAL_URL="{app_config.get('external_url', 'http://localhost')}" apt-get install -y gitlab-ce
gitlab-rails runner "ApplicationSetting.last.update(database_host: '{db1_ip}')"
""",
        "grafana": f"""#!/bin/bash
apt-get update -y
apt-get install -y grafana
# Configure Grafana to use Prometheus on OpenStack at {db1_ip}:9090
cat > /etc/grafana/provisioning/datasources/prometheus.yaml <<EOF
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://{db1_ip}:9090
    isDefault: true
EOF
systemctl enable grafana-server && systemctl start grafana-server
""",
    }
    return scripts.get(template_id, "#!/bin/bash\napt-get update -y\n")
