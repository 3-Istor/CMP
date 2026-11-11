"""
AWS identity of the CMP without a static key: Terraform state bucket and the
legacy ASG/ALB health checks.

Same exchange as the FinOps readers: a client-credentials token of the
Keycloak client cnp-tfstate, traded at AWS STS for a one-hour session of the
role cnp-tfstate-cmp (Cloud repo, terraform/public/aws/cmp-iam).

Terraform gets the session through ``credential_process``: the AWS SDK runs
this module again whenever the session nears its end, so an apply that
outlives the hour still writes its state. A static session in the environment
would expire mid-apply and leave the state behind in errored.tfstate.

TF_BACKEND_AWS_ROLE_ARN empty keeps the static TF_BACKEND_AWS_* key.
"""

import json
import os
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import httpx
from botocore import UNSIGNED
from botocore.config import Config

from app.core.config import settings

PROFILE = "cnp-tfstate"
SESSION_NAME = "cmp-tfstate"
SESSION_SECONDS = 3600
STATIC_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MARGIN = 300

_session: dict = {"value": None, "expires": 0.0}


def enabled() -> bool:
    return bool(settings.TF_BACKEND_AWS_ROLE_ARN)


def _keycloak_token(http: httpx.Client) -> str:
    if not settings.TF_BACKEND_OIDC_CLIENT_SECRET:
        raise ValueError("TF_BACKEND_OIDC_CLIENT_SECRET is empty")
    response = http.post(
        settings.TF_BACKEND_OIDC_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(
            settings.TF_BACKEND_OIDC_CLIENT_ID,
            settings.TF_BACKEND_OIDC_CLIENT_SECRET,
        ),
    )
    response.raise_for_status()
    return response.json()["access_token"]


def assume(http: httpx.Client | None = None, sts=None) -> dict:
    """A new STS session: AccessKeyId, SecretAccessKey, SessionToken, Expiration."""
    if http is None:
        with httpx.Client(timeout=30.0) as client:
            token = _keycloak_token(client)
    else:
        token = _keycloak_token(http)
    # Unsigned, or boto3 would sign with whatever static AWS_* key the pod
    # still carries during the rollout.
    sts = sts or boto3.client(
        "sts",
        region_name=settings.TF_BACKEND_AWS_REGION,
        config=Config(signature_version=UNSIGNED),
    )
    return sts.assume_role_with_web_identity(
        RoleArn=settings.TF_BACKEND_AWS_ROLE_ARN,
        RoleSessionName=SESSION_NAME,
        WebIdentityToken=token,
        DurationSeconds=SESSION_SECONDS,
    )["Credentials"]


def _expires_at(session: dict) -> datetime:
    expiration = session.get("Expiration")
    if isinstance(expiration, datetime):
        return expiration.astimezone(timezone.utc)
    return datetime.fromtimestamp(
        time.time() + SESSION_SECONDS, tz=timezone.utc
    )


def boto3_credentials(http: httpx.Client | None = None, sts=None) -> dict:
    """Keyword arguments for boto3.client, cached until near expiry."""
    if (
        _session["value"] is not None
        and time.time() < _session["expires"] - _MARGIN
    ):
        return _session["value"]
    session = assume(http, sts)
    _session["value"] = {
        "aws_access_key_id": session["AccessKeyId"],
        "aws_secret_access_key": session["SecretAccessKey"],
        "aws_session_token": session["SessionToken"],
    }
    _session["expires"] = _expires_at(session).timestamp()
    return _session["value"]


def credential_process_output(
    http: httpx.Client | None = None, sts=None
) -> str:
    """The JSON document the AWS SDK expects from a credential_process."""
    session = assume(http, sts)
    return json.dumps(
        {
            "Version": 1,
            "AccessKeyId": session["AccessKeyId"],
            "SecretAccessKey": session["SecretAccessKey"],
            "SessionToken": session["SessionToken"],
            "Expiration": _expires_at(session).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


def _config_text() -> str:
    # The SDK runs the command with sh -c from Terraform's working directory,
    # where the app package cannot be imported.
    command = (
        f"cd {shlex.quote(str(BACKEND_ROOT))} && exec "
        f"{shlex.quote(sys.executable)} -m app.services.aws_tfstate"
    )
    return (
        f"[profile {PROFILE}]\n"
        f"credential_process = {command}\n"
        f"region = {settings.TF_BACKEND_AWS_REGION}\n"
    )


def write_config(directory: Path) -> Path:
    """Write the shared config file atomically: runs in parallel read it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "aws-config"
    text = _config_text()
    if path.exists() and path.read_text() == text:
        return path
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".aws-config-")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


def apply_terraform_env(
    env: dict[str, str], config_dir: Path | None = None
) -> None:
    """
    Point a Terraform run at the federated profile. A no-op when the role is
    not configured.

    Static keys are removed, not just left aside: the SDK prefers environment
    keys over the profile, so a key still in the pod would silently win.
    """
    if not enabled():
        return
    for name in STATIC_KEYS:
        env.pop(name, None)
    config = write_config(config_dir or Path("data/aws").resolve())
    env["AWS_CONFIG_FILE"] = str(config)
    env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    env["AWS_PROFILE"] = PROFILE
    env["AWS_REGION"] = settings.TF_BACKEND_AWS_REGION
    env["AWS_DEFAULT_REGION"] = settings.TF_BACKEND_AWS_REGION


def reset() -> None:
    _session["value"] = None
    _session["expires"] = 0.0


def main() -> int:
    try:
        print(credential_process_output())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The SDK shows stderr in Terraform's error; the type and message say
        # which leg failed without echoing the token.
        print(
            f"cnp-tfstate credentials: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
