"""
Cloud billing readers without static cloud keys.

The CMP takes a client-credentials token from Keycloak (client cnp-finops) and
exchanges it at AWS STS (AssumeRoleWithWebIdentity on cnp-finops-reader) and
at Google STS (Workload Identity pool cnp-finops, then impersonation of the
reader service account). Both clouds trust only aud=cnp-finops and the sub of
that client's service account (Cloud repo, terraform/public/*/billing).
"""

import time
from datetime import datetime, timezone

import boto3
import httpx
from botocore import UNSIGNED
from botocore.config import Config

from app.core.config import settings

GOOGLE_STS = "https://sts.googleapis.com/v1/token"
IAM_CREDENTIALS = "https://iamcredentials.googleapis.com/v1"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
BIGQUERY = "https://www.googleapis.com/auth/bigquery"
# Refresh this long before expiry: credentials are frozen into the S3 client
# or the BigQuery header for a whole pass, which can read a month of files.
_MARGIN = 600
# The Keycloak token only lives for the exchange that follows at once.
_KEYCLOAK_MARGIN = 30

_keycloak: dict = {"value": None, "expires": 0.0}
_aws: dict = {"value": None, "expires": 0.0}
_gcp: dict = {"value": None, "expires": 0.0}


def _fresh(cache: dict, margin: int = _MARGIN) -> bool:
    return (
        cache["value"] is not None and time.time() < cache["expires"] - margin
    )


def keycloak_token(client: httpx.Client) -> str:
    if _fresh(_keycloak, _KEYCLOAK_MARGIN):
        return _keycloak["value"]
    if not settings.FINOPS_OIDC_CLIENT_SECRET:
        raise ValueError("FINOPS_OIDC_CLIENT_SECRET is empty")
    response = client.post(
        settings.FINOPS_OIDC_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(
            settings.FINOPS_OIDC_CLIENT_ID,
            settings.FINOPS_OIDC_CLIENT_SECRET,
        ),
    )
    response.raise_for_status()
    body = response.json()
    _keycloak["value"] = body["access_token"]
    _keycloak["expires"] = time.time() + int(body.get("expires_in", 300))
    return _keycloak["value"]


def aws_enabled() -> bool:
    return bool(settings.FINOPS_AWS_ROLE_ARN)


def aws_credentials(client: httpx.Client | None = None, sts=None) -> dict:
    """Keyword arguments for boto3.client: a one-hour STS session."""
    if _fresh(_aws):
        return _aws["value"]
    if client is None:
        with httpx.Client(timeout=30.0) as http:
            token = keycloak_token(http)
    else:
        token = keycloak_token(client)
    # The pod also carries AWS_* keys for the Terraform state bucket: the
    # call must stay unsigned so boto3 does not pick them up.
    sts = sts or boto3.client(
        "sts",
        region_name=settings.FINOPS_AWS_REGION,
        config=Config(signature_version=UNSIGNED),
    )
    session = sts.assume_role_with_web_identity(
        RoleArn=settings.FINOPS_AWS_ROLE_ARN,
        RoleSessionName="cnp-finops",
        WebIdentityToken=token,
        DurationSeconds=3600,
    )["Credentials"]
    expiration = session["Expiration"]
    if isinstance(expiration, datetime):
        expires = expiration.astimezone(timezone.utc).timestamp()
    else:
        expires = time.time() + 3600
    _aws["value"] = {
        "aws_access_key_id": session["AccessKeyId"],
        "aws_secret_access_key": session["SecretAccessKey"],
        "aws_session_token": session["SessionToken"],
    }
    _aws["expires"] = expires
    return _aws["value"]


def _expiry(expire_time: str) -> float:
    """RFC 3339 with up to nanoseconds; an org policy may shorten the hour."""
    try:
        whole = expire_time.rstrip("Z").split(".")[0]
        moment = datetime.fromisoformat(whole).replace(tzinfo=timezone.utc)
        return moment.timestamp()
    except ValueError:
        return time.time() + 3600


def gcp_enabled() -> bool:
    return bool(
        settings.FINOPS_GCP_WIF_AUDIENCE
        and settings.FINOPS_GCP_SERVICE_ACCOUNT
    )


def gcp_access_token(client: httpx.Client) -> str:
    if _fresh(_gcp):
        return _gcp["value"]
    response = client.post(
        GOOGLE_STS,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "audience": settings.FINOPS_GCP_WIF_AUDIENCE,
            "scope": CLOUD_PLATFORM,
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "subject_token": keycloak_token(client),
        },
    )
    response.raise_for_status()
    federated = response.json()["access_token"]
    response = client.post(
        f"{IAM_CREDENTIALS}/projects/-/serviceAccounts/"
        f"{settings.FINOPS_GCP_SERVICE_ACCOUNT}:generateAccessToken",
        headers={"Authorization": f"Bearer {federated}"},
        json={"scope": [BIGQUERY], "lifetime": "3600s"},
    )
    response.raise_for_status()
    body = response.json()
    _gcp["value"] = body["accessToken"]
    _gcp["expires"] = _expiry(body.get("expireTime", ""))
    return _gcp["value"]


def reset() -> None:
    for cache in (_keycloak, _aws, _gcp):
        cache["value"] = None
        cache["expires"] = 0.0
