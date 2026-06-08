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

    def __init__(self, working_dir: Path, deployment_name: str):
        self.working_dir = working_dir
        self.deployment_name = deployment_name
        self.state_dir = Path("./data/terraform_states") / deployment_name
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.use_s3_backend = settings.TF_BACKEND_S3_ENABLED

    def _get_backend_config(self) -> list[str]:
        """Generate backend configuration for Terraform init."""
        if not self.use_s3_backend or not settings.TF_BACKEND_S3_BUCKET:
            # Use local backend
            return []

        # S3 backend configuration
        backend_config = [
            "-backend-config=bucket=" + settings.TF_BACKEND_S3_BUCKET,
            "-backend-config=key="
            + settings.TF_BACKEND_S3_KEY_PREFIX
            + self.deployment_name
            + "/terraform.tfstate",
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
        self, command: list[str], capture_output: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a Terraform command in the working directory."""
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

        # Sanitize command for logging (hide sensitive values)
        safe_command = self._sanitize_command_for_logging(command)
        logger.info(
            "Running: %s in %s", " ".join(safe_command), self.working_dir
        )

        result = subprocess.run(
            command,
            cwd=self.working_dir,
            env=env,
            capture_output=capture_output,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            logger.error("Command failed: %s", result.stderr)
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

        # Create provider override file before init
        self._create_provider_override()

        command = ["terraform", "init", "-no-color", "-reconfigure"]
        backend_config = self._get_backend_config()

        if backend_config:
            logger.info("Using S3 backend: %s", settings.TF_BACKEND_S3_BUCKET)
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
            ["terraform", "plan", "-no-color", *var_args]
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

        # Apply with auto-approve
        self._run_command(
            [
                "terraform",
                "apply",
                "-auto-approve",
                "-no-color",
                *var_args,
            ]
        )

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

        self._run_command(
            [
                "terraform",
                "destroy",
                "-auto-approve",
                "-no-color",
                *var_args,
            ]
        )

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
    template_path: Path, deployment_name: str
) -> TerraformExecutor:
    """Factory function to create a TerraformExecutor."""
    return TerraformExecutor(template_path, deployment_name)
