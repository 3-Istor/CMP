"""Import Terraform variables retain their types without writing secret values."""

import json
import os
import shutil
import stat
import subprocess

import pytest

from app.services.terraform_executor import TerraformExecutor


def import_variables():
    return {
        "repository_mode": "existing",
        "existing_repository_url": "https://github.com/example/original",
        "runtime_secret_keys": ["APP_SECRET", "DATABASE_URL"],
        "secret_enabled": True,
        "secret_options": {"quoted": 'test "value"', "path": r"test\path"},
        "github_token": 'test "token"\\with-backslash\nand-newline',
    }


def executor_in(directory):
    executor = TerraformExecutor.__new__(TerraformExecutor)
    executor.working_dir = directory
    return executor


def test_import_keeps_typed_secret_inputs_and_raw_string_values(tmp_path):
    executor = executor_in(tmp_path)
    values = import_variables()

    assert executor._variable_args(values) == []
    environment = executor._import_sensitive
    assert (
        json.loads(environment["TF_VAR_runtime_secret_keys"])
        == values["runtime_secret_keys"]
    )
    assert json.loads(environment["TF_VAR_secret_enabled"]) is True
    assert (
        json.loads(environment["TF_VAR_secret_options"])
        == values["secret_options"]
    )
    assert environment["TF_VAR_github_token"] == values["github_token"]

    path = tmp_path / "import.auto.tfvars.json"
    assert json.loads(path.read_text()) == {
        "repository_mode": "existing",
        "existing_repository_url": values["existing_repository_url"],
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(
    shutil.which("terraform") is None, reason="Terraform unavailable"
)
def test_real_terraform_parses_the_import_environment_without_providers(
    tmp_path,
):
    executor = executor_in(tmp_path)
    values = import_variables()
    executor._variable_args(values)
    (tmp_path / "main.tf").write_text(
        "\n".join(
            f'variable "{name}" {{ type = {kind} }}'
            for name, kind in {
                "repository_mode": "string",
                "existing_repository_url": "string",
                "runtime_secret_keys": "list(string)",
                "secret_enabled": "bool",
                "secret_options": "map(string)",
                "github_token": "string",
            }.items()
        )
        + "\n"
    )
    expression = (
        "jsonencode({"
        + ", ".join(f"{name} = var.{name}" for name in values)
        + "})"
    )
    result = subprocess.run(
        [shutil.which("terraform"), "console", "-no-color"],
        cwd=tmp_path,
        input=expression + "\n",
        text=True,
        capture_output=True,
        timeout=30,
        env={
            "PATH": os.defpath,
            "TF_CLI_CONFIG_FILE": "/dev/null",
            "TF_IN_AUTOMATION": "1",
            **executor._import_sensitive,
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(json.loads(result.stdout.strip())) == values


def test_catalog_keeps_the_existing_command_line_arguments(tmp_path):
    executor = executor_in(tmp_path)
    values = {"repository_mode": "create", "app_name": "web", "replicas": 2}

    assert executor._variable_args(values) == [
        "-var",
        "repository_mode=create",
        "-var",
        "app_name=web",
        "-var",
        "replicas=2",
    ]
    assert not (tmp_path / "import.auto.tfvars.json").exists()
