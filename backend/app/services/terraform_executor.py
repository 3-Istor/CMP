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

        logger.info("Running: %s in %s", " ".join(command), self.working_dir)

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

    def init(self) -> None:
        """Initialize Terraform in the working directory."""
        logger.info("Initializing Terraform for %s", self.deployment_name)

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
        logger.info("Planning Terraform deployment for %s", self.deployment_name)

        var_args = []
        for key, value in variables.items():
            # Handle different types appropriately
            if isinstance(value, (int, float)):
                var_args.extend(["-var", f"{key}={value}"])
            else:
                var_args.extend(["-var", f'{key}="{value}"'])

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
        logger.info("Applying Terraform deployment for %s", self.deployment_name)

        var_args = []
        for key, value in variables.items():
            if isinstance(value, (int, float)):
                var_args.extend(["-var", f"{key}={value}"])
            else:
                var_args.extend(["-var", f'{key}="{value}"'])

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
            result = self._run_command(
                ["terraform", "output", "-json"]
            )
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
        logger.info("Destroying Terraform deployment for %s", self.deployment_name)

        var_args = []
        for key, value in variables.items():
            if isinstance(value, (int, float)):
                var_args.extend(["-var", f"{key}={value}"])
            else:
                var_args.extend(["-var", f'{key}="{value}"'])

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
            result = self._run_command(
                ["terraform", "show", "-json"]
            )
            state = json.loads(result.stdout)

            resources = state.get("values", {}).get("root_module", {}).get("resources", [])

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
