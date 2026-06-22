"""
Terraform Executor Service

Handles Terraform operations: init, plan, apply, destroy.
Captures outputs and tracks deployment progress.
Supports S3 backend for remote state storage.
"""

import json
import logging
import os
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class TerraformExecutor:
    """Executes Terraform commands and manages state."""

    def __init__(
        self,
        working_dir: Path,
        deployment_name: str,
        s3_key_path: str | None = None,
        log_file: Path | None = None,
    ):
        self.working_dir = working_dir
        self.deployment_name = deployment_name
        self.s3_key_path = s3_key_path  # Custom S3 key path (e.g., "cnp/projects/my-project/my-app")
        self.state_dir = Path("./data/terraform_states") / deployment_name
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.use_s3_backend = settings.TF_BACKEND_S3_ENABLED
        self.log_file = log_file

    def _log_message(self, message: str, level: int = logging.INFO) -> None:
        """Log message to standard logger and dynamic log file if configured."""
        logger.log(level, message)
        if self.log_file:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"{message}\n")
            except Exception as e:
                logger.error("Failed to write to log file %s: %s", self.log_file, e)

    def _get_backend_config(self) -> list[str]:
        """Generate backend configuration for Terraform init."""
        if not self.use_s3_backend or not settings.TF_BACKEND_S3_BUCKET:
            # Use local backend
            return []

        # Determine S3 key path
        if self.s3_key_path:
            # Use custom path (e.g., "cnp/projects/my-project/my-app")
            s3_key = f"{self.s3_key_path}/terraform.tfstate"
        else:
            # Use default prefix + deployment name (legacy behavior)
            s3_key = (
                settings.TF_BACKEND_S3_KEY_PREFIX
                + self.deployment_name
                + "/terraform.tfstate"
            )

        # S3 backend configuration
        backend_config = [
            "-backend-config=bucket=" + settings.TF_BACKEND_S3_BUCKET,
            "-backend-config=key=" + s3_key,
            "-backend-config=region=" + settings.TF_BACKEND_AWS_REGION,
            "-backend-config=encrypt=true",
        ]

        # Add DynamoDB table only if specified (optional for locking)
        if settings.TF_BACKEND_S3_DYNAMODB_TABLE:
            backend_config.append(
                "-backend-config=dynamodb_table="
                + settings.TF_BACKEND_S3_DYNAMODB_TABLE
            )

        # Add S3 backend credentials if provided
        if settings.TF_BACKEND_AWS_ACCESS_KEY_ID:
            backend_config.append(
                "-backend-config=access_key="
                + settings.TF_BACKEND_AWS_ACCESS_KEY_ID
            )
        if settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY:
            backend_config.append(
                "-backend-config=secret_key="
                + settings.TF_BACKEND_AWS_SECRET_ACCESS_KEY
            )

        return backend_config

    def _run_command(
        self, command: list[str], capture_output: bool = True, stream_output: bool = False
    ) -> subprocess.CompletedProcess:
        """
        Run a Terraform command in the working directory.

        Args:
            command: Command to run
            capture_output: Capture stdout/stderr for return
            stream_output: Log output in real-time (useful for long operations)
        """
        env = os.environ.copy()

        # Set local state directory if not using S3 backend
        if not self.use_s3_backend:
            env["TF_DATA_DIR"] = str(self.state_dir / ".terraform")

        # Pass OpenStack credentials to Terraform
        if settings.OS_AUTH_URL:
            env["OS_AUTH_URL"] = settings.OS_AUTH_URL
        if settings.OS_USERNAME:
            env["OS_USERNAME"] = settings.OS_USERNAME
        if settings.OS_PASSWORD:
            env["OS_PASSWORD"] = settings.OS_PASSWORD
        if settings.OS_PROJECT_NAME:
            env["OS_PROJECT_NAME"] = settings.OS_PROJECT_NAME
        if settings.OS_USER_DOMAIN_NAME:
            env["OS_USER_DOMAIN_NAME"] = settings.OS_USER_DOMAIN_NAME
        if settings.OS_PROJECT_DOMAIN_NAME:
            env["OS_PROJECT_DOMAIN_NAME"] = settings.OS_PROJECT_DOMAIN_NAME

        # Pass AWS credentials to Terraform (for AWS provider)
        if settings.AWS_ACCESS_KEY_ID:
            env["AWS_ACCESS_KEY_ID"] = settings.AWS_ACCESS_KEY_ID
        if settings.AWS_SECRET_ACCESS_KEY:
            env["AWS_SECRET_ACCESS_KEY"] = settings.AWS_SECRET_ACCESS_KEY
        if settings.AWS_DEFAULT_REGION:
            env["AWS_DEFAULT_REGION"] = settings.AWS_DEFAULT_REGION

        # Pass Cloudflare credentials to Terraform
        # Need BOTH:
        # 1. TF_VAR_* for Terraform input variables (if template declares them)
        # 2. CLOUDFLARE_* for the Cloudflare provider authentication
        if settings.CLOUDFLARE_API_TOKEN:
            env["TF_VAR_cloudflare_api_token"] = settings.CLOUDFLARE_API_TOKEN
            env["CLOUDFLARE_API_TOKEN"] = settings.CLOUDFLARE_API_TOKEN
        if settings.CLOUDFLARE_ZONE_ID:
            env["TF_VAR_cloudflare_zone_id"] = settings.CLOUDFLARE_ZONE_ID
            env["CLOUDFLARE_ZONE_ID"] = settings.CLOUDFLARE_ZONE_ID
        if settings.CLOUDFLARE_ACCOUNT_ID:
            env["TF_VAR_cloudflare_account_id"] = settings.CLOUDFLARE_ACCOUNT_ID
            env["CLOUDFLARE_ACCOUNT_ID"] = settings.CLOUDFLARE_ACCOUNT_ID

        # Pass Keycloak credentials to Terraform (for k3s-gitops-app template)
        if settings.KEYCLOAK_ADMIN_USERNAME:
            env["TF_VAR_keycloak_admin_username"] = settings.KEYCLOAK_ADMIN_USERNAME
        if settings.KEYCLOAK_ADMIN_PASSWORD:
            env["TF_VAR_keycloak_admin_password"] = settings.KEYCLOAK_ADMIN_PASSWORD
        if settings.KEYCLOAK_URL:
            env["TF_VAR_keycloak_url"] = settings.KEYCLOAK_URL

        # Pass Vault credentials to Terraform (for k3s-gitops-app template)
        if settings.VAULT_URL:
            env["TF_VAR_vault_url"] = settings.VAULT_URL
        if settings.VAULT_TOKEN:
            env["TF_VAR_vault_token"] = settings.VAULT_TOKEN

        # Pass GitHub Registry credentials to Terraform (for image pull secrets)
        if settings.GITHUB_REGISTRY_TOKEN:
            env["TF_VAR_github_registry_token"] = settings.GITHUB_REGISTRY_TOKEN
        # GitHub registry username (hardcoded as it's always the same org)
        env["TF_VAR_github_registry_username"] = "3-Istor"

        # Sanitize command for logging (hide sensitive values)
        safe_command = self._sanitize_command_for_logging(command)
        self._log_message(
            f"Running: {' '.join(safe_command)} in {self.working_dir}"
        )

        # If streaming is requested, log output in real-time
        if stream_output:
            self._log_message("📺 Streaming Terraform output in real-time...")
            import time

            # Force unbuffered output with PYTHONUNBUFFERED and TF_IN_AUTOMATION
            env["PYTHONUNBUFFERED"] = "1"
            env["TF_IN_AUTOMATION"] = "1"  # Makes Terraform output more machine-friendly

            process = subprocess.Popen(
                command,
                cwd=self.working_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,  # Unbuffered
            )

            stdout_lines = []
            start_time = time.time()

            self._log_message("⏱️  Waiting for Terraform output...")

            # Read with timeout detection
            import select
            while True:
                # Check if process is still running
                if process.poll() is not None:
                    # Process finished, read remaining output
                    remaining = process.stdout.read()
                    if remaining:
                        for line in remaining.splitlines():
                            if line.strip():
                                self._log_message(f"[TF] {line}")
                                stdout_lines.append(line)
                    break

                # Use select to check if data is available (with timeout)
                readable, _, _ = select.select([process.stdout], [], [], 1.0)

                if readable:
                    line = process.stdout.readline()
                    if line:
                        line = line.rstrip()
                        if line:
                            self._log_message(f"[TF] {line}")
                            stdout_lines.append(line)
                else:
                    # No output for 1 second, log that we're still waiting
                    elapsed = time.time() - start_time
                    if int(elapsed) % 10 == 0 and int(elapsed) > 0:  # Every 10 seconds
                        self._log_message(f"⏱️  Still waiting for Terraform... ({int(elapsed)}s elapsed)")

            stdout_output = "\n".join(stdout_lines)
            elapsed_time = time.time() - start_time
            self._log_message(f"⏱️  Terraform command completed in {elapsed_time:.1f}s")

            if process.returncode != 0:
                self._log_message(f"❌ Command failed with exit code {process.returncode}", logging.ERROR)
                self._log_message(f"Last 500 chars of output: {stdout_output[-500:]}", logging.ERROR)
                raise RuntimeError(
                    f"Terraform command failed with exit code {process.returncode}"
                )

            # Create a fake CompletedProcess for compatibility
            class FakeResult:
                def __init__(self, returncode, stdout):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = ""

            return FakeResult(process.returncode, stdout_output)

        # Normal execution (no streaming)
        result = subprocess.run(
            command,
            cwd=self.working_dir,
            env=env,
            capture_output=capture_output,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            self._log_message(f"Command failed: {result.stderr}", logging.ERROR)
            raise RuntimeError(
                f"Terraform command failed: {result.stderr or result.stdout}"
            )

        return result

    def _sanitize_command_for_logging(self, command: list[str]) -> list[str]:
        """Replace sensitive values in command with [REDACTED] for logging."""
        sanitized = []
        for part in command:
            # Hide any -var arguments containing sensitive keywords
            if "=" in part and any(
                keyword in part.lower()
                for keyword in [
                    "password",
                    "token",
                    "secret",
                    "key",
                    "cloudflare",
                ]
            ):
                key = part.split("=")[0]
                sanitized.append(f"{key}=[REDACTED]")
            else:
                sanitized.append(part)
        return sanitized

    def _create_provider_override(self) -> None:
        """
        Generate a Terraform provider override file dynamically.

        This overrides hardcoded "localhost" and "admin" values in templates
        with the actual CMP configuration from .env.

        The override file is created in the working directory and won't
        pollute the Git repository.
        """
        if not settings.OS_AUTH_URL:
            logger.warning("OS_AUTH_URL not set, skipping provider override")
            return

        # Extract the real OpenStack endpoint from .env
        parsed = urllib.parse.urlparse(settings.OS_AUTH_URL)
        host = parsed.hostname
        scheme = parsed.scheme
        port = parsed.port or (443 if scheme == "https" else 80)

        # Build the base URL
        base_url = f"{scheme}://{host}"

        override_content = f'''# Auto-generated by CMP - DO NOT EDIT
# This file overrides provider configuration to use actual endpoints

terraform {{
  required_providers {{
    openstack = {{
      source = "terraform-provider-openstack/openstack"
    }}
  }}
}}

provider "openstack" {{
  user_name           = "{settings.OS_USERNAME}"
  password            = "{settings.OS_PASSWORD}"
  tenant_name         = "{settings.OS_PROJECT_NAME}"
  user_domain_name    = "{settings.OS_USER_DOMAIN_NAME}"
  project_domain_name = "{settings.OS_PROJECT_DOMAIN_NAME}"
  auth_url            = "{settings.OS_AUTH_URL}"

  # Override endpoints to use real IPs instead of localhost
  endpoint_overrides = {{
    "identity"      = "{base_url}:5000/v3/"
    "network"       = "{base_url}:9696/v2.0/"
    "compute"       = "{base_url}:8774/v2.1/"
    "image"         = "{base_url}:9292/v2/"
    "load-balancer" = "{base_url}:9876/v2/"
  }}
}}
'''

        # Write override file in the working directory
        override_file = self.working_dir / "cmp_override.tf"
        with open(override_file, "w", encoding="utf-8") as f:
            f.write(override_content)

        logger.info(
            "Created provider override file: %s (using %s)",
            override_file.name,
            base_url
        )

    def init(self) -> None:
        """Initialize Terraform in the working directory."""
        logger.info("Initializing Terraform for %s", self.deployment_name)

        # Delete stale lock file to prevent provider version conflicts
        lock_file = self.working_dir / ".terraform.lock.hcl"
        if lock_file.exists():
            logger.info("Removing stale lock file: %s", lock_file)
            lock_file.unlink()

        # Create provider override file before init
        self._create_provider_override()

        command = ["terraform", "init", "-no-color", "-reconfigure", "-upgrade", "-input=false"]
        backend_config = self._get_backend_config()

        if backend_config:
            # Determine S3 key for logging
            if self.s3_key_path:
                s3_key = f"{self.s3_key_path}/terraform.tfstate"
            else:
                s3_key = (
                    settings.TF_BACKEND_S3_KEY_PREFIX
                    + self.deployment_name
                    + "/terraform.tfstate"
                )

            logger.info("📦 Using S3 backend: s3://%s/%s", settings.TF_BACKEND_S3_BUCKET, s3_key)
            command.extend(backend_config)
        else:
            logger.info("Using local backend")

        self._run_command(command)

    def plan(self, variables: dict[str, Any]) -> str:
        """Run terraform plan and return the output."""
        logger.info(
            "Planning Terraform deployment for %s", self.deployment_name
        )

        var_args = []
        for key, value in variables.items():
            # Terraform -var flag doesn't need quotes around values
            # The subprocess handles shell escaping automatically
            var_args.extend(["-var", f"{key}={value}"])

        result = self._run_command(
            ["terraform", "plan", "-no-color", "-input=false", *var_args]
        )
        return result.stdout

    def apply(self, variables: dict[str, Any]) -> dict[str, Any]:
        """
        Run terraform apply and return the outputs.

        Returns:
            Dictionary of Terraform outputs
        """
        logger.info(
            "Applying Terraform deployment for %s", self.deployment_name
        )

        var_args = []
        for key, value in variables.items():
            # Terraform -var flag doesn't need quotes around values
            # The subprocess handles shell escaping automatically
            var_args.extend(["-var", f"{key}={value}"])

        # Apply with auto-approve and stream output
        logger.info("🚀 Starting terraform apply (streaming output)...")
        self._run_command(
            [
                "terraform",
                "apply",
                "-auto-approve",
                "-no-color",
                "-input=false",
                *var_args,
            ],
            stream_output=True,  # Enable real-time logging
        )
        logger.info("✅ Terraform apply completed successfully")

        # Get outputs
        return self.get_outputs()

    def get_outputs(self) -> dict[str, Any]:
        """Retrieve Terraform outputs as a dictionary."""
        try:
            result = self._run_command(["terraform", "output", "-json"])
            outputs_raw = json.loads(result.stdout)

            # Terraform outputs are in format: {"key": {"value": "actual_value"}}
            outputs = {}
            for key, data in outputs_raw.items():
                outputs[key] = data.get("value")

            return outputs
        except Exception as exc:
            logger.error("Failed to get Terraform outputs: %s", exc)
            return {}

    def destroy(self, variables: dict[str, Any]) -> None:
        """Destroy all Terraform-managed resources."""
        logger.info(
            "Destroying Terraform deployment for %s", self.deployment_name
        )

        var_args = []
        for key, value in variables.items():
            # Terraform -var flag doesn't need quotes around values
            # The subprocess handles shell escaping automatically
            var_args.extend(["-var", f"{key}={value}"])

        logger.info("🗑️  Starting terraform destroy (streaming output)...")
        self._run_command(
            [
                "terraform",
                "destroy",
                "-auto-approve",
                "-no-color",
                "-input=false",
                *var_args,
            ],
            stream_output=True,  # Enable real-time logging
        )
        logger.info("✅ Terraform destroy completed successfully")

    def get_state_summary(self) -> dict[str, Any]:
        """Get a summary of the current Terraform state."""
        try:
            result = self._run_command(["terraform", "show", "-json"])
            state = json.loads(result.stdout)

            resources = (
                state.get("values", {})
                .get("root_module", {})
                .get("resources", [])
            )

            return {
                "resource_count": len(resources),
                "resources": [
                    {
                        "type": r.get("type"),
                        "name": r.get("name"),
                        "address": r.get("address"),
                    }
                    for r in resources
                ],
            }
        except Exception as exc:
            logger.error("Failed to get state summary: %s", exc)
            return {"resource_count": 0, "resources": []}


def create_executor(
    template_path: Path,
    deployment_name: str,
    s3_key_path: str | None = None,
    log_file: Path | None = None,
) -> TerraformExecutor:
    """
    Factory function to create a TerraformExecutor.

    Args:
        template_path: Path to the Terraform template directory
        deployment_name: Name of the deployment (used for local state dir)
        s3_key_path: Optional custom S3 key path (e.g., "cnp/projects/my-project/my-app")
                     If not provided, uses default prefix + deployment_name
        log_file: Optional path to log file to write all output to.
    """
    return TerraformExecutor(template_path, deployment_name, s3_key_path, log_file)
