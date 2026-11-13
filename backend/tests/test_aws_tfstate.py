import base64
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import pytest

from app.core.config import settings
from app.services import (
    aws_tfstate,
    keycloak_admin,
    monitoring_service,
    project_bootstrap,
    terraform_executor,
    vault_auth,
)
from app.services.terraform_executor import TerraformExecutor

ROLE = "arn:aws:iam::947554757826:role/cnp-tfstate-cmp"
EXPIRATION = datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def federated(monkeypatch, tmp_path):
    aws_tfstate.reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_ROLE_ARN", ROLE)
    monkeypatch.setattr(settings, "TF_BACKEND_OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setattr(settings, "TF_BACKEND_S3_ENABLED", True)
    monkeypatch.setattr(settings, "TF_BACKEND_S3_BUCKET", "istor-cnp-tfstate")
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_ACCESS_KEY_ID", "AKIASTATIC")
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_SECRET_ACCESS_KEY", "static")
    yield
    aws_tfstate.reset()


def _keycloak(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"access_token": "kc-jwt", "expires_in": 300}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _sts(expiration=EXPIRATION):
    sts = mock.Mock()
    sts.assume_role_with_web_identity.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIAFED",
            "SecretAccessKey": "fed-secret",
            "SessionToken": "fed-session",
            "Expiration": expiration,
        }
    }
    return sts


def test_token_comes_from_the_cnp_tfstate_client():
    seen = []

    aws_tfstate.assume(_keycloak(seen), _sts())

    assert (
        seen[0].headers["authorization"]
        == "Basic " + base64.b64encode(b"cnp-tfstate:s3cret").decode()
    )


def test_role_is_assumed_for_one_hour_with_the_keycloak_token():
    sts = _sts()

    aws_tfstate.assume(_keycloak([]), sts)

    sts.assume_role_with_web_identity.assert_called_once_with(
        RoleArn=ROLE,
        RoleSessionName="cmp-tfstate",
        WebIdentityToken="kc-jwt",
        DurationSeconds=3600,
    )


def test_assume_is_refused_without_a_client_secret(monkeypatch):
    monkeypatch.setattr(settings, "TF_BACKEND_OIDC_CLIENT_SECRET", "")

    with pytest.raises(ValueError):
        aws_tfstate.assume(_keycloak([]), _sts())


def test_credential_process_prints_the_sdk_document():
    output = aws_tfstate.credential_process_output(_keycloak([]), _sts())

    assert json.loads(output) == {
        "Version": 1,
        "AccessKeyId": "ASIAFED",
        "SecretAccessKey": "fed-secret",
        "SessionToken": "fed-session",
        "Expiration": "2026-11-11T13:00:00Z",
    }


def test_boto3_credentials_are_reused_until_close_to_expiry():
    sts = _sts(datetime.now(timezone.utc) + timedelta(hours=1))
    client = _keycloak([])

    aws_tfstate.boto3_credentials(client, sts)
    aws_tfstate.boto3_credentials(client, sts)

    assert sts.assume_role_with_web_identity.call_count == 1


def test_boto3_credentials_are_renewed_when_about_to_expire():
    sts = _sts(datetime.now(timezone.utc) + timedelta(seconds=30))
    client = _keycloak([])

    aws_tfstate.boto3_credentials(client, sts)
    aws_tfstate.boto3_credentials(client, sts)

    assert sts.assume_role_with_web_identity.call_count == 2


def test_terraform_env_drops_static_keys_for_the_federated_profile(tmp_path):
    env = {
        "AWS_ACCESS_KEY_ID": "AKIASTATIC",
        "AWS_SECRET_ACCESS_KEY": "static",
        "AWS_SESSION_TOKEN": "old",
    }

    aws_tfstate.apply_terraform_env(env, tmp_path)

    assert not set(aws_tfstate.STATIC_KEYS) & set(env)
    assert env["AWS_PROFILE"] == "cnp-tfstate"


def test_terraform_env_config_runs_this_module_as_credential_process(
    tmp_path,
):
    env = {}

    aws_tfstate.apply_terraform_env(env, tmp_path)

    config = open(env["AWS_CONFIG_FILE"], encoding="utf-8").read()
    assert "[profile cnp-tfstate]" in config
    assert "-m app.services.aws_tfstate" in config


def test_terraform_env_keeps_the_static_key_without_a_role(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_ROLE_ARN", "")
    env = {"AWS_ACCESS_KEY_ID": "AKIASTATIC"}

    aws_tfstate.apply_terraform_env(env, tmp_path)

    assert env == {"AWS_ACCESS_KEY_ID": "AKIASTATIC"}


def test_credential_process_command_imports_the_app_from_any_directory(
    tmp_path,
):
    config = aws_tfstate.write_config(tmp_path)
    command = (
        open(config, encoding="utf-8")
        .read()
        .split("credential_process = ", 1)[1]
        .splitlines()[0]
    )
    env = {**os.environ, "TF_BACKEND_OIDC_CLIENT_SECRET": ""}

    result = subprocess.run(
        ["sh", "-c", command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert (result.returncode, result.stdout) == (1, "")
    assert "TF_BACKEND_OIDC_CLIENT_SECRET is empty" in result.stderr


def test_backend_config_carries_no_key_when_federated(tmp_path):
    executor = TerraformExecutor(tmp_path, "app", "cnp/projects/p/app")

    config = executor._get_backend_config()

    assert not [c for c in config if "access_key" in c or "secret_key" in c]


def test_backend_config_keeps_the_static_key_without_a_role(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_ROLE_ARN", "")
    executor = TerraformExecutor(tmp_path, "app", "cnp/projects/p/app")

    config = executor._get_backend_config()

    assert "-backend-config=access_key=AKIASTATIC" in config


def test_health_check_clients_use_the_federated_session(monkeypatch):
    monkeypatch.setattr(
        aws_tfstate,
        "boto3_credentials",
        lambda: {"aws_access_key_id": "ASIAFED", "aws_session_token": "t"},
    )
    factory = mock.Mock()
    monkeypatch.setattr(monitoring_service.boto3, "client", factory)

    monitoring_service._get_boto3_client("autoscaling")

    factory.assert_called_once_with(
        "autoscaling",
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id="ASIAFED",
        aws_session_token="t",
    )


def test_main_writes_only_the_document_on_stdout(monkeypatch, capsys):
    monkeypatch.setattr(
        aws_tfstate, "credential_process_output", lambda: '{"Version": 1}'
    )

    code = aws_tfstate.main()

    assert (code, capsys.readouterr().out) == (0, '{"Version": 1}\n')


@pytest.fixture
def static_keys_everywhere(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPODENV")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "pod-env")
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", "AKIAPROVIDER")
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", "provider")
    monkeypatch.setattr(vault_auth, "terraform_env", dict)
    monkeypatch.setattr(keycloak_admin, "terraform_env", dict)


def _env_of_terraform_run(monkeypatch, tmp_path) -> dict:
    run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(terraform_executor.subprocess, "run", run)
    executor = TerraformExecutor(tmp_path, "app", "cnp/projects/p/app")

    executor._run_command_once(["terraform", "plan"])

    return run.call_args.kwargs["env"]


def _env_of_bootstrap_run(monkeypatch, tmp_path) -> dict:
    run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(project_bootstrap.subprocess, "run", run)

    project_bootstrap._run_once(["terraform", "plan"], tmp_path, tmp_path)

    return run.call_args.kwargs["env"]


@pytest.mark.usefixtures("static_keys_everywhere")
def test_terraform_run_gets_no_static_aws_key_when_federated(
    monkeypatch, tmp_path
):
    env = _env_of_terraform_run(monkeypatch, tmp_path)

    assert not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} & set(env)
    assert env["AWS_PROFILE"] == "cnp-tfstate"


@pytest.mark.usefixtures("static_keys_everywhere")
def test_bootstrap_run_gets_no_static_aws_key_when_federated(
    monkeypatch, tmp_path
):
    env = _env_of_bootstrap_run(monkeypatch, tmp_path)

    assert not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} & set(env)
    assert env["AWS_PROFILE"] == "cnp-tfstate"


@pytest.mark.usefixtures("static_keys_everywhere")
def test_terraform_run_keeps_the_provider_key_without_a_role(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_ROLE_ARN", "")

    env = _env_of_terraform_run(monkeypatch, tmp_path)

    assert env["AWS_ACCESS_KEY_ID"] == "AKIAPROVIDER"


@pytest.mark.usefixtures("static_keys_everywhere")
def test_bootstrap_run_keeps_the_backend_key_without_a_role(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_ROLE_ARN", "")

    env = _env_of_bootstrap_run(monkeypatch, tmp_path)

    assert env["AWS_ACCESS_KEY_ID"] == "AKIASTATIC"
