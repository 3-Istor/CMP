"""
Project Bootstrap Service

Executes the ``k3s-project-bootstrap`` Terraform module as a background task.

This module creates the Day-0 infrastructure for a new Project:
  - Keycloak groups: project-<name>-admins / project-<name>-members
  - Vault policy scoped to the project namespace
  - ArgoCD AppProject

The Terraform module is expected to be located at:
  backend/app/terraform/k3s-project-bootstrap/

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

logger = logging.getLogger(__name__)

# Path to the Terraform module (relative to this file's package root)
_MODULE_PATH = (
    Path(__file__).parent.parent / "terraform" / "k3s-project-bootstrap"
)


def run_project_bootstrap(project_name: str) -> None:
    """
    Entry point for the BackgroundTask.

    Runs ``terraform init`` + ``terraform apply`` for the
    ``k3s-project-bootstrap`` module.

    Args:
        project_name: Lowercase kebab-case project identifier.
    """
    logger.info("Starting project bootstrap for '%s'", project_name)

    if not _MODULE_PATH.exists():
        logger.error(
            "Terraform module not found at %s — project bootstrap aborted.",
            _MODULE_PATH,
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
                cwd=_MODULE_PATH,
                work_dir=work_dir,
            )

            # ── Step 2: terraform apply ────────────────────────────────
            logger.info("[%s] Applying Terraform configuration…", project_name)
            _run(
                [
                    "terraform", "apply",
                    "-auto-approve",
                    f"-var=project_name={project_name}",
                    f"-var=keycloak_url={settings.KEYCLOAK_URL}",
                    f"-var=keycloak_admin_username={settings.KEYCLOAK_ADMIN_USERNAME}",
                    f"-var=keycloak_admin_password={settings.KEYCLOAK_ADMIN_PASSWORD}",
                    f"-var=vault_url={settings.VAULT_URL}",
                    f"-var=vault_token={settings.VAULT_TOKEN}",
                ],
                cwd=_MODULE_PATH,
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

    # S3 backend credentials
    if settings.TF_BACKEND_AWS_ACCESS_KEY_ID:
        env["AWS_ACCESS_KEY_ID"] = settings.TF_BACKEND_AWS_ACCESS_KEY_ID
        env["AWS_SECRET_ACCESS_KEY"] = settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY
        env["AWS_DEFAULT_REGION"] = settings.TF_BACKEND_AWS_REGION

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
