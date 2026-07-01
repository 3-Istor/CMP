"""
SAGA Orchestrator - implements the "Design for Failure" pattern.

Supports two deployment strategies:
  A. LEGACY_HYBRID: OpenStack VMs + AWS ASG (original SAGA pattern)
  B. KUBERNETES: GitHub + Terraform + ArgoCD (GitOps pattern)

This runs as a FastAPI BackgroundTask so the API never blocks.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.services import aws_service, github_service, openstack_service
from app.services.github_service import get_installation_token
from app.services.template_repository import get_repository

logger = logging.getLogger(__name__)


def run_deployment(deployment_id: int, db: Session) -> None:
    """
    Entry point called by the background task.
    Routes to the appropriate deployment strategy based on provider_type.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        logger.error("Deployment %s not found", deployment_id)
        return

    # Route based on provider type
    if deployment.provider_type == ProviderType.KUBERNETES:
        _run_kubernetes_deployment(deployment, db)
    else:
        _run_legacy_hybrid_deployment(deployment, db)


def _run_kubernetes_deployment(deployment: Deployment, db: Session) -> None:
    """
    Kubernetes GitOps deployment flow using Terraform Day-0 bootstrapping.

    Steps:
      1. Fetch GitHub Installation Token from Keycloak user profile
      2. Execute Terraform with dynamic S3 state key
      3. Terraform creates: GitHub Repo, K8s Namespace, Vault Secrets, ArgoCD App
      4. ArgoCD takes over (Day-1 deployment)
    """
    app_config = json.loads(deployment.app_config or "{}")

    _update(
        db,
        deployment,
        DeploymentStatus.DEPLOYING,
        "🔐 Authenticating with GitHub App...",
    )

    try:
        # Get GitHub installation ID from app_config (should be fetched from Keycloak)
        github_installation_id = app_config.get("github_installation_id")
        if not github_installation_id:
            raise ValueError("GitHub installation ID not found in app config")

        # Exchange for installation token
        import asyncio

        installation_token = asyncio.run(
            github_service.get_installation_token(github_installation_id)
        )

        _update(
            db,
            deployment,
            DeploymentStatus.DEPLOYING,
            "🛠️ Bootstrapping infrastructure with Terraform...",
        )

        # Execute Terraform with dynamic state
        tf_outputs = _execute_terraform_kubernetes(
            deployment=deployment,
            app_config=app_config,
            github_token=installation_token,
        )

        # Store outputs in deployment record
        deployment.github_repo_url = tf_outputs.get("github_repo_url")
        deployment.argocd_app_name = tf_outputs.get("argocd_app_name")
        deployment.k8s_namespace = tf_outputs.get("k8s_namespace")
        deployment.terraform_outputs = json.dumps(tf_outputs)
        db.commit()

        _update(
            db,
            deployment,
            DeploymentStatus.RUNNING,
            f"✅ Running - ArgoCD syncing from {deployment.github_repo_url}",
        )

    except Exception as exc:
        logger.error("Kubernetes deployment failed: %s", exc, exc_info=True)
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            f"❌ Deployment failed: {str(exc)[:200]}",
        )


def _execute_terraform_kubernetes(
    deployment: Deployment, app_config: dict, github_token: str
) -> dict:
    """
    Execute the ``github_bootstrap`` Terraform module with dynamic S3 state.

    This module creates the full Day-0 stack for a containerised application:
      - GitHub repository (bootstrapped from template)
      - Kubernetes namespace
      - Vault secrets
      - Cloudflare Tunnel (optional)
      - ArgoCD Application CRD

    All credentials are injected as ``TF_VAR_*`` environment variables so
    they never appear in tfvars files or Terraform state.

    Returns:
        dict: Terraform outputs (github_repo_url, argocd_app_name, k8s_namespace, …)
    """
    # Get the template path dynamically from the cloned repository
    repo = get_repository()
    template = repo.get_template_by_id(deployment.template_id)

    if not template:
        raise ValueError(
            f"Template '{deployment.template_id}' not found in repository. "
            "Please ensure the template exists and is enabled."
        )

    module_path = Path(template["_template_path"])

    if not module_path.exists():
        raise FileNotFoundError(
            f"Terraform module not found: {module_path}. "
            "Please ensure the template repository is properly cloned."
        )

    project_name = app_config.get("project_name", "default")
    state_key = f"cmp/projects/{project_name}/{deployment.name}.tfstate"
    deployment.terraform_state_path = state_key

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)

        logger.info(
            "Initialising Terraform (module: k3s-gitops-app, state: %s)",
            state_key,
        )

        # ── terraform init ────────────────────────────────────────────
        init_cmd = [
            "terraform",
            "init",
            f"-backend-config=bucket={settings.TF_BACKEND_S3_BUCKET or '3-istor-tf-infra-aws'}",
            f"-backend-config=key={state_key}",
            f"-backend-config=region={settings.TF_BACKEND_AWS_REGION}",
            "-backend-config=encrypt=true",
            *(
                [
                    f"-backend-config=dynamodb_table={settings.TF_BACKEND_S3_DYNAMODB_TABLE}"
                ]
                if settings.TF_BACKEND_S3_DYNAMODB_TABLE
                else []
            ),
            "-reconfigure",
        ]
        _run_terraform_command(
            init_cmd, module_path, work_dir, github_token=github_token
        )

        # ── terraform apply ───────────────────────────────────────────
        logger.info("Applying github_bootstrap Terraform module…")
        apply_cmd = [
            "terraform",
            "apply",
            "-auto-approve",
            # Non-sensitive variables passed as -var flags
            f"-var=project_name={project_name}",
            f"-var=app_name={deployment.name}",
            f"-var=replica_count={app_config.get('replica_count', 2)}",
            f"-var=sso_protected={str(app_config.get('sso_protected', False)).lower()}",
            f"-var=github_installation_id={app_config.get('github_installation_id', '')}",
            f"-var=github_owner={app_config.get('github_owner', '3-Istor')}",
            f"-var=template_repo_name={app_config.get('template_repo_name', 'template-html-css')}",
            f"-var=app_type={app_config.get('app_type', 'static')}",
        ]
        _run_terraform_command(
            apply_cmd, module_path, work_dir, github_token=github_token
        )

        # ── terraform output ──────────────────────────────────────────
        output_cmd = ["terraform", "output", "-json"]
        result = _run_terraform_command(
            output_cmd,
            module_path,
            work_dir,
            github_token=github_token,
            capture=True,
        )

        outputs_raw = json.loads(result.stdout)
        outputs = {k: v["value"] for k, v in outputs_raw.items()}
        logger.info("Terraform outputs: %s", list(outputs.keys()))
        return outputs


def _run_terraform_command(
    cmd: list[str],
    module_path: Path,
    work_dir: Path,
    github_token: str = "",
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """
    Execute a Terraform command with all required credentials injected as
    environment variables.

    Sensitive values are passed via ``TF_VAR_*`` env vars (never as -var flags
    on the command line) so they don't appear in shell history or logs.

    Args:
        cmd:          Terraform command list.
        module_path:  Directory of the Terraform module.
        work_dir:     Temporary working directory (used for TF_DATA_DIR).
        github_token: Short-lived GitHub installation access token.
        capture:      Whether to capture stdout/stderr for parsing.
    """
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["TF_DATA_DIR"] = str(work_dir / ".terraform")

    # ── S3 backend credentials ────────────────────────────────────────────
    if settings.TF_BACKEND_AWS_ACCESS_KEY_ID:
        env["AWS_ACCESS_KEY_ID"] = settings.TF_BACKEND_AWS_ACCESS_KEY_ID
        env["AWS_SECRET_ACCESS_KEY"] = (
            settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY
        )
        env["AWS_DEFAULT_REGION"] = settings.TF_BACKEND_AWS_REGION

    # ── GitHub (short-lived installation token, generated per operation) ──
    if github_token:
        env["TF_VAR_github_token"] = github_token

    # ── GitHub App (for repository creation and management) ───────────────
    if settings.GITHUB_APP_PRIVATE_KEY:
        env["TF_VAR_github_app_private_key"] = settings.GITHUB_APP_PRIVATE_KEY

    # ── GitHub Registry Token (PAT for pulling private images from GHCR) ──
    if settings.GITHUB_REGISTRY_TOKEN:
        env["TF_VAR_github_registry_token"] = settings.GITHUB_REGISTRY_TOKEN

    # ── Vault ─────────────────────────────────────────────────────────────
    if settings.VAULT_URL:
        env["TF_VAR_vault_url"] = settings.VAULT_URL
        env["VAULT_ADDR"] = settings.VAULT_URL
    if settings.VAULT_TOKEN:
        env["TF_VAR_vault_token"] = settings.VAULT_TOKEN
        env["VAULT_TOKEN"] = settings.VAULT_TOKEN

    # ── Keycloak ──────────────────────────────────────────────────────────
    if settings.KEYCLOAK_URL:
        env["TF_VAR_keycloak_url"] = settings.KEYCLOAK_URL
    if settings.KEYCLOAK_ADMIN_USERNAME:
        env["TF_VAR_keycloak_admin_username"] = (
            settings.KEYCLOAK_ADMIN_USERNAME
        )
    if settings.KEYCLOAK_ADMIN_PASSWORD:
        env["TF_VAR_keycloak_admin_password"] = (
            settings.KEYCLOAK_ADMIN_PASSWORD
        )

    # ── Cloudflare ────────────────────────────────────────────────────────
    if settings.CLOUDFLARE_API_TOKEN:
        env["TF_VAR_cloudflare_api_token"] = settings.CLOUDFLARE_API_TOKEN
        env["CLOUDFLARE_API_TOKEN"] = settings.CLOUDFLARE_API_TOKEN
    if settings.CLOUDFLARE_ZONE_ID:
        env["TF_VAR_cloudflare_zone_id"] = settings.CLOUDFLARE_ZONE_ID
    if settings.CLOUDFLARE_ACCOUNT_ID:
        env["TF_VAR_cloudflare_account_id"] = settings.CLOUDFLARE_ACCOUNT_ID

    try:
        result = subprocess.run(
            cmd,
            cwd=module_path,
            env=env,
            check=True,
            capture_output=capture,
            text=True,
        )
        return result
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr if capture else str(exc)
        logger.error("Terraform command failed: %s", stderr)
        raise RuntimeError(f"Terraform failed: {stderr[:500]}") from exc


def _run_legacy_hybrid_deployment(deployment: Deployment, db: Session) -> None:
    """
    Legacy SAGA pattern: OpenStack VMs + AWS ASG.
    Preserved for backward compatibility.
    """
    app_config = json.loads(deployment.app_config or "{}")

    # ── Step 1: OpenStack ─────────────────────────────────────────────────
    _update(
        db,
        deployment,
        DeploymentStatus.DEPLOYING,
        "🔧 Deploying OpenStack DB VMs...",
    )
    try:
        # Note: Legacy deployments stored IDs directly in deployment object
        # This is now stored in terraform_outputs for consistency
        vm_result = openstack_service.provision_db_vms(
            deployment.name, deployment.template_id, app_config
        )
        outputs = {"openstack_vms": vm_result}
        deployment.terraform_outputs = json.dumps(outputs)
        db.commit()
        logger.info("OpenStack VMs ready for deployment %s", deployment.id)
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
        DeploymentStatus.DEPLOYING,
        "☁️ Deploying AWS ASG + Load Balancer...",
    )
    try:
        vm_data = json.loads(deployment.terraform_outputs).get(
            "openstack_vms", {}
        )
        aws_result = aws_service.provision_web_layer(
            deployment.name,
            deployment.template_id,
            app_config,
            vm_data.get("vm1", {}).get("ip"),
            vm_data.get("vm2", {}).get("ip"),
        )
        outputs = json.loads(deployment.terraform_outputs)
        outputs["aws"] = aws_result
        deployment.terraform_outputs = json.dumps(outputs)
        db.commit()
        logger.info("AWS layer ready for deployment %s", deployment.id)
    except Exception as exc:
        logger.error("AWS step failed - triggering SAGA rollback: %s", exc)
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            "⏪ AWS failed - rolling back OpenStack VMs...",
        )
        # Rollback OpenStack
        vm_data = json.loads(deployment.terraform_outputs).get(
            "openstack_vms", {}
        )
        openstack_service.rollback_db_vms(
            vm_data.get("vm1", {}).get("id"), vm_data.get("vm2", {}).get("id")
        )
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            f"❌ AWS provisioning failed. OpenStack rolled back. Error: {exc}",
        )
        return

    # ── Step 3: Done ──────────────────────────────────────────────────────
    outputs = json.loads(deployment.terraform_outputs)
    alb_dns = outputs.get("aws", {}).get("alb_dns", "N/A")
    _update(
        db,
        deployment,
        DeploymentStatus.RUNNING,
        f"✅ Running - {alb_dns}",
    )

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
    Delete all cloud resources for a deployment.
    Routes to the appropriate deletion strategy based on provider_type.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        return

    if deployment.provider_type == ProviderType.KUBERNETES:
        _run_kubernetes_deletion(deployment, db)
    else:
        _run_legacy_hybrid_deletion(deployment, db)


def _run_kubernetes_deletion(deployment: Deployment, db: Session) -> None:
    """
    Delete Kubernetes deployment using Terraform destroy against the
    ``k3s-gitops-app`` module.
    """
    _update(
        db,
        deployment,
        DeploymentStatus.DELETING,
        "🗑️ Destroying Kubernetes resources via Terraform...",
    )

    try:
        # Get the template path dynamically from the cloned repository
        repo = get_repository()
        template = repo.get_template_by_id(deployment.template_id)

        if not template:
            raise ValueError(
                f"Template '{deployment.template_id}' not found in repository"
            )

        module_path = Path(template["_template_path"])

        if not module_path.exists():
            raise FileNotFoundError(
                f"Terraform module not found: {module_path}"
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)

            # Parse config and resolve GitHub token with fallback
            app_config = json.loads(deployment.app_config or "{}")
            github_installation_id = app_config.get("github_installation_id")
            github_token = ""

            if github_installation_id:
                try:
                    import asyncio

                    github_token = asyncio.run(
                        get_installation_token(int(github_installation_id))
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Failed to generate GitHub token: {e}")

            if not github_token and getattr(
                settings, "GITHUB_INSTALLATION_ID", None
            ):
                try:
                    import asyncio

                    github_token = asyncio.run(
                        get_installation_token(
                            int(settings.GITHUB_INSTALLATION_ID)
                        )
                    )
                    logger.info(
                        "✅ Used global GITHUB_INSTALLATION_ID fallback token"
                    )
                except Exception as e:
                    logger.warning(
                        f"⚠️ Failed to generate global fallback GitHub token: {e}"
                    )

            # Re-initialise with the same state key used during creation
            init_cmd = [
                "terraform",
                "init",
                f"-backend-config=bucket={settings.TF_BACKEND_S3_BUCKET or '3-istor-tf-infra-aws'}",
                f"-backend-config=key={deployment.terraform_state_path}",
                f"-backend-config=region={settings.TF_BACKEND_AWS_REGION}",
                "-backend-config=encrypt=true",
                *(
                    [
                        f"-backend-config=dynamodb_table={settings.TF_BACKEND_S3_DYNAMODB_TABLE}"
                    ]
                    if settings.TF_BACKEND_S3_DYNAMODB_TABLE
                    else []
                ),
                "-reconfigure",
            ]
            _run_terraform_command(
                init_cmd, module_path, work_dir, github_token=github_token
            )

            destroy_cmd = ["terraform", "destroy", "-auto-approve"]
            _run_terraform_command(
                destroy_cmd, module_path, work_dir, github_token=github_token
            )

            logger.info(
                "Kubernetes resources destroyed for deployment %s",
                deployment.id,
            )

    except Exception as exc:
        logger.error("Kubernetes deletion failed: %s", exc)
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            f"❌ Deletion failed: {exc}",
        )
        return

    _update(db, deployment, DeploymentStatus.DELETED, "✅ Deleted")


def _run_legacy_hybrid_deletion(deployment: Deployment, db: Session) -> None:
    """
    Delete legacy hybrid deployment (OpenStack + AWS).
    """
    _update(
        db,
        deployment,
        DeploymentStatus.DELETING,
        "🗑️ Deleting AWS resources...",
    )

    try:
        outputs = json.loads(deployment.terraform_outputs or "{}")
        aws_data = outputs.get("aws", {})

        if aws_data.get("asg_name"):
            aws_service.delete_web_layer(aws_data["asg_name"], deployment.name)

        _update(
            db,
            deployment,
            DeploymentStatus.DELETING,
            "🗑️ Deleting OpenStack VMs...",
        )

        vm_data = outputs.get("openstack_vms", {})
        openstack_service.delete_db_vms(
            vm_data.get("vm1", {}).get("id"), vm_data.get("vm2", {}).get("id")
        )

    except Exception as exc:
        logger.error("Legacy deletion failed: %s", exc)
        _update(
            db,
            deployment,
            DeploymentStatus.FAILED,
            f"❌ Deletion failed: {exc}",
        )
        return

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
