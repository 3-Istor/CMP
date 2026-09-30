"""
Project Bootstrap Service

Executes the ``k3s-project-bootstrap`` Terraform module as a background task.

This module creates the Day-0 infrastructure for a new Project:
  - Keycloak groups: project-<name>-admins / project-<name>-members
  - Vault policy scoped to the project namespace
  - ArgoCD AppProject

The Terraform module is expected to be located in the cloned templates repository.

Terraform variables injected:
  project_name, target_cloud, keycloak_url, keycloak_admin_username,
  keycloak_admin_password, vault_url, vault_token,
  github_token, discord_webhook_url
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
from app.services.github_service import GitHubAppError, get_installation_token
from app.services.template_repository import get_repository

logger = logging.getLogger(__name__)


class TerraformBackendError(RuntimeError):
    """Raised when the Terraform state backend is unsafe to use."""


def _state_key(project_name: str, target_cloud: str) -> str:
    """
    Build the S3 state key for a project's bootstrap state.

    Keys are laid out per cloud (D-09) so that a per-cloud inventory — which is
    what the decommission runbook needs — is a prefix listing rather than a scan.
    """
    return f"cmp/{target_cloud}/projects/{project_name}/bootstrap.tfstate"


def _local_state_path(project_name: str, target_cloud: str) -> Path:
    """
    Build the on-disk state path used when the S3 backend is disabled.

    This lives under ``data/`` — the backend's persistent volume — and not in
    the run's temporary directory: a state file that disappears with the
    process leaves the teardown with nothing to destroy, which is how orphaned
    Vault policies and Keycloak groups accumulate.
    """
    return (
        Path("data/tfstate")
        / target_cloud
        / project_name
        / "bootstrap.tfstate"
    ).resolve()


def _stage_module(module_path: Path, work_dir: Path) -> Path:
    """
    Copy the Terraform module into the run's working directory.

    The module lives in the shared template clone, which is refreshed on a
    timer and read by every concurrent bootstrap. Terraform writes into its
    configuration directory (lock file, backend override), so running several
    projects straight out of the clone has them overwriting each other's
    files.
    """
    staged = work_dir / "module"
    shutil.copytree(module_path, staged)
    return staged


def _get_module_path() -> Path:
    """
    Get the path to the k3s-project-bootstrap Terraform module.

    Returns:
        Path: The absolute path to the Terraform module directory.

    Raises:
        FileNotFoundError: If the module doesn't exist in the repository.
    """
    repo = get_repository()

    # The module was renamed: its old name encoded the on-prem runtime and reads
    # wrong the moment AWS exists. Both names are accepted so the rename in
    # app-templates and this change can merge in either order; drop the fallback
    # once app-templates is on main.
    for name in ("project-bootstrap", "k3s-project-bootstrap"):
        module_path = repo.repo_path / "templates" / name
        if module_path.exists():
            return module_path

    raise FileNotFoundError(
        f"Terraform module not found under {repo.repo_path / 'templates'}. "
        "Expected 'project-bootstrap' (or the legacy 'k3s-project-bootstrap')."
    )


def run_project_bootstrap(
    project_name: str, target_cloud: str = "onprem"
) -> bool:
    """
    Entry point for the BackgroundTask.

    Runs ``terraform init`` + ``terraform apply`` for the
    ``k3s-project-bootstrap`` module.

    Args:
        project_name: Lowercase kebab-case project identifier.
        target_cloud: Which cloud the project runs on — selects the state key
            prefix and the per-provider module implementation.

    Returns:
        bool: Whether the module applied cleanly. Callers that depend on what
        the module creates — the Keycloak groups, above all — must check this
        rather than assume the resources exist.
    """
    logger.info(
        "Starting project bootstrap for '%s' on '%s'",
        project_name,
        target_cloud,
    )

    try:
        module_path = _get_module_path()
    except FileNotFoundError as exc:
        logger.error(
            "Terraform module not found — project bootstrap aborted: %s",
            exc,
        )
        return False

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
        return False

    # S3 state key — isolated per project, grouped per cloud
    state_key = _state_key(project_name, target_cloud)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            staged = _stage_module(module_path, work_dir)

            # ── Step 1: terraform init ─────────────────────────────────
            logger.info("[%s] Initialising Terraform…", project_name)
            _terraform_init(
                staged,
                work_dir,
                github_token,
                state_key,
                _local_state_path(project_name, target_cloud),
            )

            # ── Step 2: terraform apply ────────────────────────────────
            logger.info("[%s] Applying Terraform configuration…", project_name)
            _run(
                [
                    "terraform",
                    "apply",
                    "-auto-approve",
                    f"-var=project_name={project_name}",
                    f"-var=target_cloud={target_cloud}",
                ],
                cwd=staged,
                work_dir=work_dir,
                github_token=github_token,
            )

            logger.info(
                "Project bootstrap completed successfully for '%s'",
                project_name,
            )
            return True

    except (RuntimeError, OSError) as exc:
        logger.error(
            "Project bootstrap failed for '%s': %s", project_name, exc
        )
        return False


def run_project_teardown(
    project_name: str, target_cloud: str = "onprem"
) -> None:
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
        target_cloud: Which cloud the project runs on — selects the state key.
    """
    logger.info(
        "Starting project teardown for '%s' on '%s'",
        project_name,
        target_cloud,
    )

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
    state_key = _state_key(project_name, target_cloud)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            staged = _stage_module(module_path, work_dir)

            # ── Step 1: terraform init ─────────────────────────────────
            logger.info("[%s] Initialising Terraform…", project_name)
            _terraform_init(
                staged,
                work_dir,
                github_token,
                state_key,
                _local_state_path(project_name, target_cloud),
            )

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
                    f"-var=target_cloud={target_cloud}",
                ],
                cwd=staged,
                work_dir=work_dir,
                github_token=github_token,
            )

            logger.info(
                "Project teardown completed successfully for '%s'",
                project_name,
            )

    except (RuntimeError, OSError) as exc:
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
    local_state: Path,
) -> None:
    """
    Run ``terraform init`` against the project's state.

    Shared by bootstrap (apply) and teardown (destroy) so both operate on the
    exact same backend configuration and state key.

    Args:
        module_path: The staged copy of the module, safe to write into.
        local_state: Where to keep state when the S3 backend is disabled.

    Raises:
        TerraformBackendError: If the S3 backend is enabled but incomplete.
    """
    if not settings.TF_BACKEND_S3_ENABLED:
        _write_local_backend_override(module_path, local_state)
        logger.warning(
            "TF_BACKEND_S3_ENABLED is false: bootstrap state is kept locally "
            "at %s. This is fine for a single replica, but the state is not "
            "shared, not locked and not backed up.",
            local_state,
        )
        _run(
            ["terraform", "init", "-reconfigure"],
            cwd=module_path,
            work_dir=work_dir,
            github_token=github_token,
        )
        return

    missing = [
        name
        for name, value in (
            ("TF_BACKEND_S3_BUCKET", settings.TF_BACKEND_S3_BUCKET),
            (
                "TF_BACKEND_S3_DYNAMODB_TABLE",
                settings.TF_BACKEND_S3_DYNAMODB_TABLE,
            ),
        )
        if not value
    ]
    if missing:
        raise TerraformBackendError(
            f"TF_BACKEND_S3_ENABLED is true but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} empty. The S3 backend "
            "needs a bucket, and a lock table is not optional (D-09): two "
            "concurrent applies on the same project corrupt state with no "
            "warning. Set them, or set TF_BACKEND_S3_ENABLED=false to run on "
            "local state."
        )

    _run(
        [
            "terraform",
            "init",
            "-backend-config=bucket=" + settings.TF_BACKEND_S3_BUCKET,
            f"-backend-config=key={state_key}",
            "-backend-config=region=" + settings.TF_BACKEND_AWS_REGION,
            "-backend-config=encrypt=true",
            "-backend-config=dynamodb_table="
            + settings.TF_BACKEND_S3_DYNAMODB_TABLE,
            "-reconfigure",
        ],
        cwd=module_path,
        work_dir=work_dir,
        github_token=github_token,
    )


def _write_local_backend_override(
    module_path: Path, local_state: Path
) -> None:
    """
    Point the staged module at a local state file.

    The module hardcodes ``backend "s3" {}``, which cannot be switched off
    from the command line. Terraform's override mechanism replaces the backend
    block wholesale, which is the only way to run this module without AWS.
    """
    local_state.parent.mkdir(parents=True, exist_ok=True)
    (module_path / "backend_override.tf").write_text(
        'terraform {\n  backend "local" {\n'
        f'    path = "{local_state}"\n'
        "  }\n}\n"
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
