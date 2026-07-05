"""
Project Bootstrap Service

Executes the ``k3s-project-bootstrap`` Terraform module as a background task.

This module creates the Day-0 infrastructure for a new Project:
  - Keycloak groups: project-<name>-admins / project-<name>-members
  - Vault policy scoped to the project namespace
  - ArgoCD AppProject

The Terraform module is expected to be located in the cloned templates repository.

Terraform variables injected:
  project_name, keycloak_url, keycloak_admin_username,
  keycloak_admin_password, vault_url, vault_token,
  github_token, discord_webhook_url
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
from app.services.github_service import GitHubAppError, get_installation_token
from app.services.template_repository import get_repository

logger = logging.getLogger(__name__)


def _get_module_path() -> Path:
    """
    Get the path to the k3s-project-bootstrap Terraform module.

    Returns:
        Path: The absolute path to the Terraform module directory.

    Raises:
        FileNotFoundError: If the module doesn't exist in the repository.
    """
    repo = get_repository()
    module_path = repo.repo_path / "templates" / "k3s-project-bootstrap"

    if not module_path.exists():
        raise FileNotFoundError(
            f"Terraform module not found at {module_path}. "
            "Please ensure the templates repository contains k3s-project-bootstrap."
        )

    return module_path


def run_project_bootstrap(project_name: str) -> None:
    """
    Entry point for the BackgroundTask.

    Runs ``terraform init`` + ``terraform apply`` for the
    ``k3s-project-bootstrap`` module.

    Args:
        project_name: Lowercase kebab-case project identifier.
    """
    logger.info("Starting project bootstrap for '%s'", project_name)

    try:
        module_path = _get_module_path()
    except FileNotFoundError as exc:
        logger.error(
            "Terraform module not found — project bootstrap aborted: %s",
            exc,
        )
        return

    # GitHub installation token for the "github" provider (writes to the
    # cnp-projects repository) — minted once and reused for init + apply
    try:
        github_token = (
            asyncio.run(
                get_installation_token(settings.GITHUB_INSTALLATION_ID)
            )
            if settings.GITHUB_INSTALLATION_ID
            else ""
        )
    except GitHubAppError as exc:
        logger.error(
            "Project bootstrap failed for '%s': could not obtain GitHub installation token: %s",
            project_name,
            exc,
        )
        return

    # S3 state key — isolated per project
    state_key = f"cmp/projects/{project_name}/bootstrap.tfstate"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)

            # ── Step 1: terraform init ─────────────────────────────────
            logger.info("[%s] Initialising Terraform…", project_name)
            _terraform_init(module_path, work_dir, github_token, state_key)

            # ── Step 2: terraform apply ────────────────────────────────
            logger.info("[%s] Applying Terraform configuration…", project_name)
            _run(
                [
                    "terraform",
                    "apply",
                    "-auto-approve",
                    f"-var=project_name={project_name}",
                ],
                cwd=module_path,
                work_dir=work_dir,
                github_token=github_token,
            )

            logger.info(
                "Project bootstrap completed successfully for '%s'",
                project_name,
            )

    except RuntimeError as exc:
        logger.error(
            "Project bootstrap failed for '%s': %s", project_name, exc
        )


def run_project_teardown(project_name: str) -> None:
    """
    Entry point for the BackgroundTask triggered on project deletion.

    Runs ``terraform init`` + ``terraform destroy`` for the
    ``k3s-project-bootstrap`` module, reusing the project's per-project S3 state
    key. This destroys everything the bootstrap created — Vault policy, ArgoCD
    AppProject and any GitHub resources. Keycloak groups are already removed
    synchronously by the delete endpoint; if they no longer exist, Terraform's
    refresh simply drops them from state and the destroy proceeds.

    Args:
        project_name: Lowercase kebab-case project identifier.
    """
    logger.info("Starting project teardown for '%s'", project_name)

    try:
        module_path = _get_module_path()
    except FileNotFoundError as exc:
        logger.error(
            "Terraform module not found — project teardown aborted "
            "(Vault/GitHub/ArgoCD resources may need manual cleanup): %s",
            exc,
        )
        return

    # GitHub installation token for the "github" provider — required so the
    # provider can authenticate while destroying GitHub resources.
    try:
        github_token = (
            asyncio.run(
                get_installation_token(settings.GITHUB_INSTALLATION_ID)
            )
            if settings.GITHUB_INSTALLATION_ID
            else ""
        )
    except GitHubAppError as exc:
        logger.error(
            "Project teardown failed for '%s': could not obtain GitHub installation token: %s",
            project_name,
            exc,
        )
        return

    # Same S3 state key used by the bootstrap — destroy operates on that state.
    state_key = f"cmp/projects/{project_name}/bootstrap.tfstate"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)

            # ── Step 1: terraform init ─────────────────────────────────
            logger.info("[%s] Initialising Terraform…", project_name)
            _terraform_init(module_path, work_dir, github_token, state_key)

            # ── Step 2: terraform destroy ──────────────────────────────
            logger.info(
                "[%s] Destroying Terraform configuration…", project_name
            )
            _run(
                [
                    "terraform",
                    "destroy",
                    "-auto-approve",
                    f"-var=project_name={project_name}",
                ],
                cwd=module_path,
                work_dir=work_dir,
                github_token=github_token,
            )

            logger.info(
                "Project teardown completed successfully for '%s'",
                project_name,
            )

    except RuntimeError as exc:
        logger.error(
            "Project teardown failed for '%s' "
            "(Vault/GitHub/ArgoCD resources may need manual cleanup): %s",
            project_name,
            exc,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _terraform_init(
    module_path: Path,
    work_dir: Path,
    github_token: str,
    state_key: str,
) -> None:
    """
    Run ``terraform init`` against the project's per-project S3 state.

    Shared by bootstrap (apply) and teardown (destroy) so both operate on the
    exact same backend configuration and state key.
    """
    _run(
        [
            "terraform",
            "init",
            "-backend-config=bucket=" + settings.TF_BACKEND_S3_BUCKET,
            f"-backend-config=key={state_key}",
            "-backend-config=region=" + settings.TF_BACKEND_AWS_REGION,
            "-backend-config=encrypt=true",
            *(
                [
                    "-backend-config=dynamodb_table="
                    + settings.TF_BACKEND_S3_DYNAMODB_TABLE
                ]
                if settings.TF_BACKEND_S3_DYNAMODB_TABLE
                else []
            ),
            "-reconfigure",
        ],
        cwd=module_path,
        work_dir=work_dir,
        github_token=github_token,
    )


def _run(
    cmd: list[str], cwd: Path, work_dir: Path, github_token: str = ""
) -> None:
    """
    Execute a Terraform command, forwarding all necessary credentials as
    environment variables.

    Args:
        cmd:          Full Terraform command list.
        cwd:          Directory containing the Terraform module files.
        work_dir:     Temporary working directory (used for TF_DATA_DIR).
        github_token: Installation token for the "github" provider.

    Raises:
        RuntimeError: If the command exits non-zero.
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

    # ── GitHub (write access to the cnp-projects repository) ───────────────
    if github_token:
        env["TF_VAR_github_token"] = github_token

    # ── Discord (alerting webhook, stored into the project's Vault namespace) ──
    if settings.DISCORD_WEBHOOK_URL:
        env["TF_VAR_discord_webhook_url"] = settings.DISCORD_WEBHOOK_URL

    if settings.GRAFANA_ADMIN_PASSWORD:
            env["TF_VAR_grafana_admin_password"] = settings.GRAFANA_ADMIN_PASSWORD

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout:
            logger.debug("Terraform stdout: %s", result.stdout[-2000:])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        logger.error("Terraform error: %s", stderr[-2000:])
        raise RuntimeError(
            f"Terraform command failed: {stderr[-500:]}"
        ) from exc
