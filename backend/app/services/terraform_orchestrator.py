"""
Terraform Deployment Orchestrator

Manages the full lifecycle of Terraform-based deployments:
- Initialize Terraform
- Apply configuration
- Track outputs
- Destroy resources
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.services import app_inputs, customer_identity, notifications
from app.services.app_repo import is_platform_repo_name, repo_url_for
from app.services.github_service import (
    GitHubAppError,
    get_org_installation_token,
)
from app.services.observability import reconcile as obs_reconcile
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
    logger.info("=" * 80)
    logger.info(f"Starting deployment task for deployment_id={deployment_id}")
    logger.info("=" * 80)

    # Create a new database session for this background task
    db = SessionLocal()
    try:
        logger.debug(f"Fetching deployment {deployment_id} from database...")
        deployment = db.get(Deployment, deployment_id)
        if not deployment:
            logger.error(f"Deployment {deployment_id} not found in database")
            return

        logger.info(
            f"Found deployment: {deployment.name} (template={deployment.template_id}, provider={deployment.provider_type})"
        )

        try:
            # Get template from repository
            logger.debug("Fetching template from repository...")
            repo = get_repository()
            template = repo.get_template_by_id(deployment.template_id)
            if not template:
                logger.error(
                    f"Template {deployment.template_id} not found in repository"
                )
                raise ValueError(
                    f"Template {deployment.template_id} not found"
                )

            logger.info(
                f"Template found: {template.get('name', 'Unknown')} at {template.get('_template_path', 'Unknown')}"
            )

            template_path = Path(template["_template_path"])
            if not template_path.exists():
                logger.error(f"Template path does not exist: {template_path}")
                raise ValueError(
                    f"Template path does not exist: {template_path}"
                )

            logger.debug(f"Template path verified: {template_path}")

            app_config = _terraform_variables(deployment, template)
            # App repos live in the org: a stored installation ID was sent by
            # the client and is never used to mint a token.
            if deployment.template_id == "k3s-gitops-app":
                try:
                    app_config["github_token"] = asyncio.run(
                        get_org_installation_token()
                    )
                except GitHubAppError as token_error:
                    raise RuntimeError(
                        f"Cannot deploy without GitHub token: {token_error}"
                    ) from token_error
            logger.info(f"Terraform variables: {sorted(app_config)}")

            # Create Terraform executor
            logger.debug("Creating Terraform executor...")

            # For Kubernetes deployments, use structured S3 path: cnp/projects/{project}/{app}
            s3_key_path = None
            if deployment.project_id:
                s3_key_path = (
                    f"cnp/projects/{deployment.project_id}/{deployment.name}"
                )
                logger.info(f"Using structured S3 path: {s3_key_path}")

            log_file = Path("logs/deployments") / f"{deployment.id}.log"
            executor = create_executor(
                template_path, deployment.name, s3_key_path, log_file=log_file
            )
            logger.info(
                f"Terraform executor created (working_dir={executor.working_dir})"
            )

            # Step 1: Initialize
            logger.info("─" * 80)
            logger.info("STEP 1: Terraform Init")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.INITIALIZING,
                "Initializing Terraform...",
            )
            executor.init()
            logger.info("Terraform initialized successfully")

            # Step 2: Plan (optional, for logging)
            logger.info("─" * 80)
            logger.info("STEP 2: Terraform Plan")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.PLANNING,
                "Planning deployment...",
            )
            plan_output = executor.plan(app_config)
            logger.info(
                f"Terraform plan output (first 500 chars):\n{plan_output[:500]}"
            )
            logger.debug(f"Full plan output:\n{plan_output}")

            # Step 3: Apply
            logger.info("─" * 80)
            logger.info("STEP 3: Terraform Apply")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.DEPLOYING,
                "Deploying resources...",
            )
            logger.info(
                "Running terraform apply... (this may take several minutes)"
            )
            outputs = executor.apply(app_config)
            logger.info(
                f"Terraform apply completed. Outputs: {list(outputs.keys())}"
            )
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
            # Set only here, after a successful apply: the repository
            # resource fails on an existing name, so this repo is the app's.
            repo_url = outputs.get("github_repo_url")
            if not repo_url and deployment.template_id == "k3s-gitops-app":
                repo_url = repo_url_for(deployment.name)
            if repo_url:
                deployment.github_repo_url = repo_url
            if outputs.get("argocd_app_name"):
                deployment.argocd_app_name = outputs["argocd_app_name"]
            if outputs.get("k8s_namespace"):
                deployment.k8s_namespace = outputs["k8s_namespace"]

            # Register the app's hostname in the project's registry record,
            # this is what puts it in the connector's routing table
            # (k3s-gitops-app/outputs.tf's hostname output description).
            # Without it the app's tunnel and Gateway exist but nothing tells
            # them the hostname, and every request 404s at the connector.
            # Best-effort: the deployment itself already succeeded, and a
            # registry write failure here shouldn't be reported as a failed
            # deployment.
            if deployment.template_id == "k3s-gitops-app" and outputs.get(
                "hostname"
            ):
                from app.services.project_registry import (
                    RegistryError,
                    register_app,
                )

                try:
                    asyncio.run(
                        register_app(
                            project_name=app_config["project_name"],
                            app_name=deployment.name,
                            app_type=outputs.get("app_type", "static"),
                            repo_url=repo_url or "",
                            hostname=outputs["hostname"],
                        )
                    )
                    logger.info(
                        "Registered '%s' in the project registry",
                        deployment.name,
                    )
                except RegistryError as exc:
                    logger.error(
                        "Could not register '%s' in the project "
                        "registry, its tunnel/Gateway will not route to "
                        "it until this is retried: %s",
                        deployment.name,
                        exc,
                    )

            state_summary = executor.get_state_summary()
            deployment.resource_count = state_summary.get("resource_count", 0)
            logger.info(f"Resources created: {deployment.resource_count}")

            db.commit()

            # The OIDC client exists after apply, so grant its rights before
            # the (long) URL wait. Only gitops apps have one. Swallows its own
            # errors: the deployment already succeeded.
            if deployment.template_id == "k3s-gitops-app":
                customer_identity.apply_if_enabled(app_config["project_name"])

            # Step 5: Wait for app URL to become accessible
            logger.info("─" * 80)
            logger.info("STEP 5: Waiting for app to be accessible")
            logger.info("─" * 80)
            app_url = outputs.get("app_url")
            if app_url:
                url_ready = _wait_for_url_ready(db, deployment, app_url)
                if not url_ready:
                    logger.warning(
                        f"{app_url} not accessible within timeout, ArgoCD may still be syncing"
                    )

            # Step 6: Success
            logger.info("─" * 80)
            logger.info("STEP 6: Finalization")
            logger.info("─" * 80)
            output_msg = _format_outputs_message(outputs)
            _update(
                db,
                deployment,
                DeploymentStatus.RUNNING,
                f"Running - {output_msg}",
            )
            _notify(deployment, "deployment_succeeded")
            obs_reconcile.request_sync()

            logger.info("=" * 80)
            logger.info(f"Deployment {deployment_id} completed successfully")
            logger.info("=" * 80)

        except Exception as exc:
            logger.error("=" * 80)
            logger.error(f"Deployment {deployment_id} FAILED")
            logger.error("=" * 80)
            logger.error(f"Error type: {type(exc).__name__}")
            logger.error(f"Error message: {str(exc)}")
            logger.exception("Full traceback:")
            _update(
                db,
                deployment,
                DeploymentStatus.FAILED,
                f"Deployment failed: {str(exc)[:200]}",
            )
            _notify(deployment, "deployment_failed")
    finally:
        logger.debug("Closing database session")
        db.close()


def backfill_gitops_repo_urls() -> None:
    """
    Fill in ``github_repo_url`` for existing k3s-gitops-app deployments that
    predate repo-URL persistence. Idempotent: only touches rows where the column
    is empty. Safe to run on every startup.

    Only rows whose apply went through (running or degraded): a failed row
    never created its repository, and its name may be another app's.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Deployment)
            .filter(
                Deployment.template_id == "k3s-gitops-app",
                Deployment.status.in_(
                    [DeploymentStatus.RUNNING, DeploymentStatus.DEGRADED]
                ),
                (Deployment.github_repo_url.is_(None))
                | (Deployment.github_repo_url == ""),
            )
            .all()
        )
        updated = 0
        for d in rows:
            if is_platform_repo_name(d.name):
                continue
            d.github_repo_url = repo_url_for(d.name)
            updated += 1
        if updated:
            db.commit()
            logger.info(
                "Backfilled github_repo_url for %d deployment(s)", updated
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_repo_url backfill skipped: %s", exc)
    finally:
        db.close()


def run_deletion(deployment_id: int) -> None:
    """
    Destroy all Terraform-managed resources for a deployment.
    """
    logger.info("=" * 80)
    logger.info(f"Starting deletion task for deployment_id={deployment_id}")
    logger.info("=" * 80)

    # Create a new database session for this background task
    db = SessionLocal()
    try:
        logger.debug(f"Fetching deployment {deployment_id} from database...")
        deployment = db.get(Deployment, deployment_id)
        if not deployment:
            logger.error(f"Deployment {deployment_id} not found in database")
            return

        logger.info(
            f"Found deployment: {deployment.name} (template={deployment.template_id})"
        )

        try:
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "Destroying resources...",
            )

            # Get template and create executor
            logger.debug("Fetching template from repository...")
            repo = get_repository()
            template = repo.get_template_by_id(deployment.template_id)
            if not template:
                logger.error(
                    f"Template {deployment.template_id} not found in repository"
                )
                raise ValueError(
                    f"Template {deployment.template_id} not found"
                )

            logger.info(f"Template found: {template.get('name', 'Unknown')}")

            template_path = Path(template["_template_path"])
            logger.debug("Creating Terraform executor...")

            # For Kubernetes deployments, use structured S3 path: cnp/projects/{project}/{app}
            s3_key_path = None
            if deployment.project_id:
                s3_key_path = (
                    f"cnp/projects/{deployment.project_id}/{deployment.name}"
                )
                logger.info(f"Using structured S3 path: {s3_key_path}")

            log_file = Path("logs/deployments") / f"{deployment.id}.log"
            executor = create_executor(
                template_path, deployment.name, s3_key_path, log_file=log_file
            )
            logger.info(
                f"Terraform executor created (working_dir={executor.working_dir})"
            )

            app_config = _terraform_variables(deployment, template)
            # The token is never stored: mint one, or pass a placeholder so
            # Terraform does not prompt for it.
            app_config["github_token"] = "dummy-token-for-destroy"
            if deployment.template_id == "k3s-gitops-app":
                try:
                    app_config["github_token"] = asyncio.run(
                        get_org_installation_token()
                    )
                except GitHubAppError as token_error:
                    logger.warning(
                        f"Failed to regenerate GitHub token, destroying "
                        f"anyway (GitHub resources may need manual cleanup): "
                        f"{token_error}"
                    )
            logger.info(f"Terraform variables: {sorted(app_config)}")

            # Initialize Terraform backend before destroy
            logger.info("─" * 80)
            logger.info("STEP 1: Terraform Init")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "Initializing Terraform...",
            )
            executor.init()
            logger.info("Terraform initialized successfully")

            # Destroy resources
            logger.info("─" * 80)
            logger.info("STEP 2: Terraform Destroy")
            logger.info("─" * 80)
            _update(
                db,
                deployment,
                DeploymentStatus.DELETING,
                "Destroying resources...",
            )
            logger.info(
                "Running terraform destroy... (this may take several minutes)"
            )
            from app.services.environments.activation import destroy_with_app

            destroy_with_app(db, deployment)
            executor.destroy(app_config)
            logger.info("Terraform destroy completed")

            if (
                deployment.template_id == "k3s-gitops-app"
                and deployment.project_id
            ):
                from app.services.project_registry import (
                    RegistryError,
                    unregister_app,
                )

                try:
                    asyncio.run(
                        unregister_app(
                            project_name=deployment.project_id,
                            app_name=deployment.name,
                        )
                    )
                except RegistryError as exc:
                    logger.error(
                        "Could not unregister '%s' from the project "
                        "registry, a stale hostname entry may remain: %s",
                        deployment.name,
                        exc,
                    )

            # The name is free again: another app may now own that repo.
            deployment.github_repo_url = None
            _update(
                db,
                deployment,
                DeploymentStatus.DELETED,
                "Resources destroyed",
            )
            _notify(deployment, "app_deleted")
            obs_reconcile.request_sync()

            logger.info("=" * 80)
            logger.info(f"Deletion {deployment_id} completed successfully")
            logger.info("=" * 80)

        except Exception as exc:
            logger.error("=" * 80)
            logger.error(f"Deletion {deployment_id} FAILED")
            logger.error("=" * 80)
            logger.error(f"Error type: {type(exc).__name__}")
            logger.error(f"Error message: {str(exc)}")
            logger.exception("Full traceback:")
            _update(
                db,
                deployment,
                DeploymentStatus.FAILED,
                f"Deletion failed: {str(exc)[:200]}",
            )
    finally:
        logger.debug("Closing database session")
        db.close()


def _notify(deployment: Deployment, kind: str) -> None:
    if not deployment.project_id:
        return
    link = (
        f"/projects/{deployment.project_id}"
        if kind == "app_deleted"
        else f"/projects/{deployment.project_id}/apps/{deployment.id}"
    )
    notifications.notify_project(
        deployment.project_id,
        kind=kind,
        title_key=kind,
        params={"app": deployment.name},
        link=link,
        app=deployment.name,
    )


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
    logger.info(
        f"Polling {url} for a non-error response (timeout={timeout_seconds}s, interval={poll_interval}s)"
    )
    start = time.monotonic()
    deadline = start + timeout_seconds

    while time.monotonic() < deadline:
        elapsed = int(time.monotonic() - start)
        _update(
            db,
            deployment,
            DeploymentStatus.DEPLOYING,
            f"Waiting for app to become accessible... ({elapsed}s / {timeout_seconds}s)",
        )
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            if resp.status_code < 400:
                logger.info(
                    f"{url} responded with HTTP {resp.status_code} after {elapsed}s"
                )
                return True
            logger.debug(
                f"HTTP {resp.status_code} from {url}, retrying in {poll_interval}s"
            )
        except Exception as exc:
            logger.debug(f"Connection attempt failed ({elapsed}s): {exc}")

        time.sleep(poll_interval)

    logger.warning(f"{url} did not respond within {timeout_seconds}s")
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


def _terraform_variables(deployment: Deployment, template: dict) -> dict:
    return app_inputs.terraform_variables(
        template.get("variables", []),
        deployment.app_config,
        app_name=deployment.name,
        project_name=deployment.project_id or "default",
        gitops=deployment.template_id == "k3s-gitops-app",
    )
