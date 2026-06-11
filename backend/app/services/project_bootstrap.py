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
  keycloak_admin_password, vault_url, vault_token
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
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

    # S3 state key — isolated per project
    state_key = f"cmp/projects/{project_name}/bootstrap.tfstate"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)

            # ── Step 1: terraform init ─────────────────────────────────
            logger.info("[%s] Initialising Terraform…", project_name)
            _run(
                [
                    "terraform", "init",
                    "-backend-config=bucket=" + settings.TF_BACKEND_S3_BUCKET,
                    f"-backend-config=key={state_key}",
                    "-backend-config=region=" + settings.TF_BACKEND_AWS_REGION,
                    "-backend-config=encrypt=true",
                    *(
                        ["-backend-config=dynamodb_table=" + settings.TF_BACKEND_S3_DYNAMODB_TABLE]
                        if settings.TF_BACKEND_S3_DYNAMODB_TABLE
                        else []
                    ),
                    "-reconfigure",
                ],
                cwd=module_path,
                work_dir=work_dir,
            )

            # ── Step 2: terraform apply ────────────────────────────────
            logger.info("[%s] Applying Terraform configuration…", project_name)
            _run(
                [
                    "terraform", "apply",
                    "-auto-approve",
                    f"-var=project_name={project_name}",
                ],
                cwd=module_path,
                work_dir=work_dir,
            )

            logger.info(
                "Project bootstrap completed successfully for '%s'", project_name
            )

    except RuntimeError as exc:
        logger.error(
            "Project bootstrap failed for '%s': %s", project_name, exc
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path, work_dir: Path) -> None:
    """
    Execute a Terraform command, forwarding all necessary credentials as
    environment variables.

    Args:
        cmd:      Full Terraform command list.
        cwd:      Directory containing the Terraform module files.
        work_dir: Temporary working directory (used for TF_DATA_DIR).

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
        env["AWS_SECRET_ACCESS_KEY"] = settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY
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
        env["TF_VAR_keycloak_admin_username"] = settings.KEYCLOAK_ADMIN_USERNAME
    if settings.KEYCLOAK_ADMIN_PASSWORD:
        env["TF_VAR_keycloak_admin_password"] = settings.KEYCLOAK_ADMIN_PASSWORD

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
