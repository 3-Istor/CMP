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
import time
from pathlib import Path

import httpx

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.services.github_service import get_installation_token
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
    logger.info("="* 80)
    logger.info(f"🚀 Starting deployment task for deployment_id={deployment_id}")
    logger.info("="* 80)

    # Create a new database session for this background task
    db = SessionLocal()
    try:
        logger.debug(f"Fetching deployment {deployment_id} from database...")
        deployment = db.get(Deployment, deployment_id)
        if not deployment:
            logger.error(f"❌ Deployment {deployment_id} not found in database")
            return

        logger.info(f"✅ Found deployment: {deployment.name} (template={deployment.template_id}, provider={deployment.provider_type})")

        try:
            # Parse user configuration
            logger.debug("Parsing app_config...")
            app_config = json.loads(deployment.app_config or "{}")
            logger.info(f"Original app_config keys: {list(app_config.keys())}")

            # CRITICAL: Inject app_name from deployment name
            # This ensures Terraform resources are named correctly
            app_config["app_name"] = deployment.name
            logger.debug(f"Injected app_name={deployment.name} into config")

            # CRITICAL: Inject project_name (required by k3s-gitops-app template)
            if "project_name" not in app_config:
                if deployment.project_id:
                    app_config["project_name"] = deployment.project_id
                    logger.debug(f"Injected project_name={deployment.project_id} from deployment.project_id")
                else:
                    # Fallback for deployments without project_id
                    app_config["project_name"] = "default"
                    logger.warning(f"⚠️  No project_id found, using default project_name='default'")

            # CRITICAL: Generate GitHub token for deployment
            # The token is NOT stored in app_config (security), so we must generate it
            if "github_installation_id" in app_config and "github_token" not in app_config:
                logger.info("🔑 Generating GitHub installation token for deployment...")
                try:
                    import asyncio
                    installation_token = asyncio.run(get_installation_token(
                        int(app_config["github_installation_id"])
                    ))
                    app_config["github_token"] = installation_token
                    logger.info("✅ GitHub token generated successfully")

                    # Remove github_installation_id from app_config before passing to Terraform
                    # The template only needs github_token, not the installation_id
                    del app_config["github_installation_id"]
                    logger.debug("Removed github_installation_id from app_config (not needed by Terraform)")
                except Exception as token_error:
                    logger.error(f"❌ Failed to generate GitHub token: {token_error}")
                    raise RuntimeError(f"Cannot deploy without GitHub token: {token_error}")
            elif deployment.template_id == "k3s-gitops-app":
                if "github_token" not in app_config:
                    error_msg = (
                        "❌ DEPLOYMENT BLOCKED: Missing GitHub Integration\n\n"
                        "The k3s-gitops-app template requires:\n"
                        "  • 'github_installation_id' in app_config\n"
                        "  • 'project_name' in app_config\n\n"
                        f"Current app_config keys: {list(app_config.keys())}\n"
                        f"Expected keys: github_installation_id, project_name, github_owner, template_repo_name, app_type\n\n"
                        "SOLUTION:\n"
                        "1. User must link GitHub account in Account page\n"
                        "2. Frontend must include github_installation_id in app_config\n"
                        "3. Frontend must include project_name in app_config\n\n"
                        "See: DEPLOYMENT_API_REQUIREMENTS.md for details"
                    )
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            logger.info(f"Final app_config keys: {list(app_config.keys())}")
            logger.debug(f"App config values (sanitized): {_sanitize_config_for_logging(app_config)}")

            # Get template from repository
            logger.debug("Fetching template from repository...")
            repo = get_repository()
            template = repo.get_template_by_id(deployment.template_id)
            if not template:
                logger.error(f"❌ Template {deployment.template_id} not found in repository")
                raise ValueError(
                    f"Template {deployment.template_id} not found"
                )

            logger.info(f"✅ Template found: {template.get('name', 'Unknown')} at {template.get('_template_path', 'Unknown')}")

            template_path = Path(template["_template_path"])
            if not template_path.exists():
                logger.error(f"❌ Template path does not exist: {template_path}")
                raise ValueError(
                    f"Template path does not exist: {template_path}"
                )

            logger.debug(f"Template path verified: {template_path}")

            # Create Terraform executor
            logger.debug("Creating Terraform executor...")

            # For Kubernetes deployments, use structured S3 path: cnp/projects/{project}/{app}
            s3_key_path = None
            if deployment.project_id:
                s3_key_path = f"cnp/projects/{deployment.project_id}/{deployment.name}"
                logger.info(f"📁 Using structured S3 path: {s3_key_path}")

            log_file = Path("logs/deployments") / f"{deployment.id}.log"
            executor = create_executor(template_path, deployment.name, s3_key_path, log_file=log_file)
            logger.info(f"✅ Terraform executor created (working_dir={executor.working_dir})")

            # Step 1: Initialize
            logger.info("─" * 80)
            logger.info("STEP 1: Terraform Init")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.INITIALIZING,
                "🔧 Initializing Terraform...",
            )
            executor.init()
            logger.info("✅ Terraform initialized successfully")

            # Step 2: Plan (optional, for logging)
            logger.info("─" * 80)
            logger.info("STEP 2: Terraform Plan")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.PLANNING,
                "📋 Planning deployment...",
            )
            plan_output = executor.plan(app_config)
            logger.info(f"Terraform plan output (first 500 chars):\n{plan_output[:500]}")
            logger.debug(f"Full plan output:\n{plan_output}")

            # Step 3: Apply
            logger.info("─" * 80)
            logger.info("STEP 3: Terraform Apply")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.DEPLOYING,
                "🚀 Deploying resources...",
            )
            logger.info("Running terraform apply... (this may take several minutes)")
            outputs = executor.apply(app_config)
            logger.info(f"✅ Terraform apply completed. Outputs: {list(outputs.keys())}")
            logger.debug(f"Full outputs: {outputs}")

            # Step 4: Capture outputs and state
            logger.info("─" * 80)
            logger.info("STEP 4: Capturing State")
            logger.info("─" * 80)
            deployment.terraform_outputs = json.dumps(outputs)
            deployment.terraform_state_path = str(executor.state_dir)

            # Persist GitOps metadata into dedicated columns so the UI can link
            # to the repo / ArgoCD / namespace. Prefer the Terraform output when
            # present; the k3s-gitops-app template doesn't expose a
            # github_repo_url output, so reconstruct it from the deterministic
            # naming (repo name = app name, created under the GitHub org).
            repo_url = outputs.get("github_repo_url")
            if not repo_url and deployment.template_id == "k3s-gitops-app":
                owner = app_config.get("github_owner", "3-Istor")
                repo_url = f"https://github.com/{owner}/{deployment.name}"
            if repo_url:
                deployment.github_repo_url = repo_url
            if outputs.get("argocd_app_name"):
                deployment.argocd_app_name = outputs["argocd_app_name"]
            if outputs.get("k8s_namespace"):
                deployment.k8s_namespace = outputs["k8s_namespace"]

            state_summary = executor.get_state_summary()
            deployment.resource_count = state_summary.get("resource_count", 0)
            logger.info(f"Resources created: {deployment.resource_count}")

            db.commit()

            # Step 5: Wait for app URL to become accessible
            logger.info("─" * 80)
            logger.info("STEP 5: Waiting for app to be accessible")
            logger.info("─" * 80)
            app_url = outputs.get("app_url")
            if app_url:
                url_ready = _wait_for_url_ready(db, deployment, app_url)
                if not url_ready:
                    logger.warning(f"⚠️ {app_url} not accessible within timeout — ArgoCD may still be syncing")

            # Step 6: Success
            logger.info("─" * 80)
            logger.info("STEP 6: Finalization")
            logger.info("─" * 80)
            output_msg = _format_outputs_message(outputs)
            _update(
                db,
                deployment,
                DeploymentStatus.RUNNING,
                f"✅ Running - {output_msg}",
            )

            logger.info("="* 80)
            logger.info(f"✅ Deployment {deployment_id} completed successfully")
            logger.info("="* 80)

        except Exception as exc:
            logger.error("="* 80)
            logger.error(f"❌ Deployment {deployment_id} FAILED")
            logger.error("="* 80)
            logger.error(f"Error type: {type(exc).__name__}")
            logger.error(f"Error message: {str(exc)}")
            logger.exception("Full traceback:")
            _update(
                db,
                deployment,
                DeploymentStatus.FAILED,
                f"❌ Deployment failed: {str(exc)[:200]}",
            )
    finally:
        logger.debug("Closing database session")
        db.close()


def backfill_gitops_repo_urls() -> None:
    """
    Fill in ``github_repo_url`` for existing k3s-gitops-app deployments that
    predate repo-URL persistence. Idempotent: only touches rows where the column
    is empty. Safe to run on every startup.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Deployment)
            .filter(
                Deployment.template_id == "k3s-gitops-app",
                (Deployment.github_repo_url.is_(None))
                | (Deployment.github_repo_url == ""),
            )
            .all()
        )
        updated = 0
        for d in rows:
            owner = "3-Istor"
            try:
                cfg = json.loads(d.app_config or "{}")
                owner = cfg.get("github_owner", owner)
            except Exception:  # noqa: BLE001 — best-effort config parse
                pass
            d.github_repo_url = f"https://github.com/{owner}/{d.name}"
            updated += 1
        if updated:
            db.commit()
            logger.info("Backfilled github_repo_url for %d deployment(s)", updated)
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_repo_url backfill skipped: %s", exc)
    finally:
        db.close()


def run_deletion(deployment_id: int) -> None:
    """
    Destroy all Terraform-managed resources for a deployment.
    """
    logger.info("="* 80)
    logger.info(f"🗑️  Starting deletion task for deployment_id={deployment_id}")
    logger.info("="* 80)

    # Create a new database session for this background task
    db = SessionLocal()
    try:
        logger.debug(f"Fetching deployment {deployment_id} from database...")
        deployment = db.get(Deployment, deployment_id)
        if not deployment:
            logger.error(f"❌ Deployment {deployment_id} not found in database")
            return

        logger.info(f"✅ Found deployment: {deployment.name} (template={deployment.template_id})")

        try:
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "🗑️ Destroying resources...",
            )

            # Get template and create executor
            logger.debug("Fetching template from repository...")
            repo = get_repository()
            template = repo.get_template_by_id(deployment.template_id)
            if not template:
                logger.error(f"❌ Template {deployment.template_id} not found in repository")
                raise ValueError(
                    f"Template {deployment.template_id} not found"
                )

            logger.info(f"✅ Template found: {template.get('name', 'Unknown')}")

            template_path = Path(template["_template_path"])
            logger.debug("Creating Terraform executor...")

            # For Kubernetes deployments, use structured S3 path: cnp/projects/{project}/{app}
            s3_key_path = None
            if deployment.project_id:
                s3_key_path = f"cnp/projects/{deployment.project_id}/{deployment.name}"
                logger.info(f"📁 Using structured S3 path: {s3_key_path}")

            log_file = Path("logs/deployments") / f"{deployment.id}.log"
            executor = create_executor(template_path, deployment.name, s3_key_path, log_file=log_file)
            logger.info(f"✅ Terraform executor created (working_dir={executor.working_dir})")

            # Parse original config for destroy
            logger.debug("Parsing original app_config...")
            app_config = json.loads(deployment.app_config or "{}")

            # CRITICAL: Inject app_name for destroy to match apply
            app_config["app_name"] = deployment.name
            logger.debug(f"Injected app_name={deployment.name} into config")

            # CRITICAL: Inject project_name (required by k3s-gitops-app template)
            if "project_name" not in app_config:
                if deployment.project_id:
                    app_config["project_name"] = deployment.project_id
                    logger.debug(f"Injected project_name={deployment.project_id} from deployment.project_id")
                else:
                    # Fallback for old deployments without project_id
                    app_config["project_name"] = "default"
                    logger.warning(f"⚠️  No project_id found, using default project_name='default'")

            # CRITICAL: Regenerate GitHub token for destroy
            # The token is NOT stored in app_config (security), so we must regenerate it
            if "github_installation_id" in app_config:
                logger.info("🔑 Regenerating GitHub installation token for destroy...")
                try:
                    import asyncio
                    installation_token = asyncio.run(get_installation_token(
                        int(app_config["github_installation_id"])
                    ))
                    app_config["github_token"] = installation_token
                    logger.info("✅ GitHub token regenerated successfully")

                    # Remove github_installation_id from app_config before passing to Terraform
                    # The template only needs github_token, not the installation_id
                    del app_config["github_installation_id"]
                    logger.debug("Removed github_installation_id from app_config (not needed by Terraform)")
                except Exception as token_error:
                    logger.warning(
                        f"⚠️  Failed to regenerate GitHub token: {token_error}"
                    )
                    logger.warning(
                        "Continuing with destroy anyway (GitHub resources may need manual cleanup)"
                    )
                    # Remove github_installation_id even if token generation failed
                    if "github_installation_id" in app_config:
                        del app_config["github_installation_id"]
                    # Set a dummy token to prevent Terraform from prompting
                    app_config["github_token"] = "dummy-token-for-destroy"
            else:
                logger.warning("⚠️  No github_installation_id in config")
                logger.warning("This deployment may have been created before GitHub integration")
                # For k3s-gitops-app template, github_token is required
                # Set a dummy token to prevent Terraform from blocking on input
                if deployment.template_id == "k3s-gitops-app":
                    logger.warning("Setting dummy github_token for k3s-gitops-app template")
                    app_config["github_token"] = "dummy-token-for-destroy"

            logger.info(f"App config keys for destroy: {list(app_config.keys())}")
            logger.debug(f"App config values (sanitized): {_sanitize_config_for_logging(app_config)}")

            # Initialize Terraform backend before destroy
            logger.info("─" * 80)
            logger.info("STEP 1: Terraform Init")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "🔧 Initializing Terraform...",
            )
            executor.init()
            logger.info("✅ Terraform initialized successfully")

            # Destroy resources
            logger.info("─" * 80)
            logger.info("STEP 2: Terraform Destroy")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "🗑️ Destroying resources...",
            )
            logger.info("Running terraform destroy... (this may take several minutes)")
            executor.destroy(app_config)
            logger.info("✅ Terraform destroy completed")

            _update(
                db,
                deployment,
                DeploymentStatus.DELETED,
                "✅ Resources destroyed",
            )

            logger.info("="* 80)
            logger.info(f"✅ Deletion {deployment_id} completed successfully")
            logger.info("="* 80)

        except Exception as exc:
            logger.error("="* 80)
            logger.error(f"❌ Deletion {deployment_id} FAILED")
            logger.error("="* 80)
            logger.error(f"Error type: {type(exc).__name__}")
            logger.error(f"Error message: {str(exc)}")
            logger.exception("Full traceback:")
            _update(
                db,
                deployment,
                DeploymentStatus.FAILED,
                f"❌ Deletion failed: {str(exc)[:200]}",
            )
    finally:
        logger.debug("Closing database session")
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

    # Write to deployment-specific log file for real-time log streaming
    log_file = Path("logs/deployments") / f"{deployment.id}.log"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n--- [{status.value.upper()}] {message} ---\n")
    except Exception as e:
        logger.error("Failed to write to log file %s: %s", log_file, e)


def _wait_for_url_ready(
    db,
    deployment: Deployment,
    url: str,
    timeout_seconds: int = 300,
    poll_interval: int = 10,
) -> bool:
    """
    Poll url until a non-error HTTP response is received or timeout is reached.
    Keeps the deployment in DEPLOYING state and updates step_message each cycle
    so the frontend shows live progress.
    Returns True if the URL became accessible within the timeout.
    """
    logger.info(f"⏳ Polling {url} for HTTP 200 (timeout={timeout_seconds}s, interval={poll_interval}s)")
    start = time.monotonic()
    deadline = start + timeout_seconds

    while time.monotonic() < deadline:
        elapsed = int(time.monotonic() - start)
        _update(
            db,
            deployment,
            DeploymentStatus.DEPLOYING,
            f"⏳ Waiting for app to become accessible... ({elapsed}s / {timeout_seconds}s)",
        )
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            if resp.status_code < 400:
                logger.info(f"✅ {url} responded with HTTP {resp.status_code} after {elapsed}s")
                return True
            logger.debug(f"HTTP {resp.status_code} from {url} — retrying in {poll_interval}s")
        except Exception as exc:
            logger.debug(f"Connection attempt failed ({elapsed}s): {exc}")

        time.sleep(poll_interval)

    logger.warning(f"⚠️ {url} did not respond within {timeout_seconds}s")
    return False


def _format_outputs_message(outputs: dict) -> str:
    """
    Format Terraform outputs into a friendly message.
    Prioritizes common output names like 'ip', 'url', 'endpoint', etc.
    """
    if not outputs:
        return "No outputs"

    # Priority keys to display
    priority_keys = [
        "app_url",
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


def _sanitize_config_for_logging(config: dict) -> dict:
    """
    Sanitize sensitive values in config for logging.
    Replaces tokens, passwords, and secrets with [REDACTED].
    """
    sanitized = {}
    sensitive_keys = ["github_token", "password", "token", "secret", "key"]

    for key, value in config.items():
        if any(sensitive in key.lower() for sensitive in sensitive_keys):
            if isinstance(value, str) and len(value) > 10:
                sanitized[key] = f"{value[:8]}...[REDACTED]"
            else:
                sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value

    return sanitized
