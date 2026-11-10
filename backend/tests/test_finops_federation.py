from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import pytest
from botocore import UNSIGNED

from app.core.config import settings
from app.services.finops.ingest import (
    aws_focus,
    federation,
    gcp_focus,
    opencost,
)

ROLE = "arn:aws:iam::947554757826:role/cnp-finops-reader"
AUDIENCE = (
    "//iam.googleapis.com/projects/123/locations/global/"
    "workloadIdentityPools/cnp-finops/providers/keycloak"
)
READER = "cnp-finops-reader@project.iam.gserviceaccount.com"


@pytest.fixture(autouse=True)
def federated(monkeypatch):
    federation.reset()
    monkeypatch.setattr(settings, "FINOPS_OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setattr(settings, "FINOPS_AWS_ROLE_ARN", ROLE)
    monkeypatch.setattr(settings, "FINOPS_GCP_WIF_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(settings, "FINOPS_GCP_SERVICE_ACCOUNT", READER)
    yield
    federation.reset()


def _transport(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "auth.3istor.com":
            return httpx.Response(
                200, json={"access_token": "kc-jwt", "expires_in": 300}
            )
        if request.url.host == "sts.googleapis.com":
            return httpx.Response(200, json={"access_token": "federated"})
        return httpx.Response(
            200,
            json={
                "accessToken": "sa-token",
                "expireTime": "2026-11-10T21:00:00Z",
            },
        )

    return httpx.MockTransport(handler)


def _sts(expiration=None):
    sts = mock.Mock()
    sts.assume_role_with_web_identity.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA",
            "SecretAccessKey": "secret",
            "SessionToken": "session",
            "Expiration": expiration
            or datetime.now(timezone.utc) + timedelta(hours=1),
        }
    }
    return sts


def test_keycloak_token_uses_client_credentials_of_cnp_finops():
    seen = []

    federation.keycloak_token(httpx.Client(transport=_transport(seen)))

    assert seen[0].headers["authorization"].startswith("Basic ")


def test_keycloak_token_is_refused_without_a_client_secret(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_OIDC_CLIENT_SECRET", "")

    with pytest.raises(ValueError):
        federation.keycloak_token(httpx.Client(transport=_transport([])))


def test_aws_assumes_the_reader_role_with_the_keycloak_token():
    sts = _sts()

    federation.aws_credentials(httpx.Client(transport=_transport([])), sts)

    sts.assume_role_with_web_identity.assert_called_once_with(
        RoleArn=ROLE,
        RoleSessionName="cnp-finops",
        WebIdentityToken="kc-jwt",
        DurationSeconds=3600,
    )


def test_aws_credentials_are_reused_until_close_to_expiry():
    sts = _sts()
    client = httpx.Client(transport=_transport([]))

    federation.aws_credentials(client, sts)
    federation.aws_credentials(client, sts)

    assert sts.assume_role_with_web_identity.call_count == 1


def test_aws_credentials_are_renewed_when_about_to_expire():
    sts = _sts(datetime.now(timezone.utc) + timedelta(seconds=30))
    client = httpx.Client(transport=_transport([]))

    federation.aws_credentials(client, sts)
    federation.aws_credentials(client, sts)

    assert sts.assume_role_with_web_identity.call_count == 2


def test_aws_s3_client_gets_the_session_credentials(monkeypatch):
    monkeypatch.setattr(
        federation,
        "aws_credentials",
        lambda: {"aws_access_key_id": "ASIA", "aws_session_token": "t"},
    )
    factory = mock.Mock()
    monkeypatch.setattr(aws_focus.boto3, "client", factory)

    aws_focus.s3_client()

    factory.assert_called_once_with(
        "s3",
        region_name="eu-west-3",
        aws_access_key_id="ASIA",
        aws_session_token="t",
    )


def test_aws_s3_client_falls_back_to_environment_keys_without_a_role(
    monkeypatch,
):
    monkeypatch.setattr(settings, "FINOPS_AWS_ROLE_ARN", "")
    factory = mock.Mock()
    monkeypatch.setattr(aws_focus.boto3, "client", factory)

    aws_focus.s3_client()

    factory.assert_called_once_with("s3", region_name="eu-west-3")


def test_gcp_exchanges_the_keycloak_token_then_impersonates_the_reader():
    seen = []

    token = gcp_focus.access_token(httpx.Client(transport=_transport(seen)))

    assert token == "sa-token"
    exchange, impersonation = seen[1], seen[2]
    assert b"audience=%2F%2Fiam.googleapis.com" in exchange.content
    assert b"subject_token=kc-jwt" in exchange.content
    assert impersonation.url.path.endswith(f"{READER}:generateAccessToken")
    assert impersonation.headers["authorization"] == "Bearer federated"


def test_gcp_token_is_reused_within_its_hour():
    seen = []
    client = httpx.Client(transport=_transport(seen))

    gcp_focus.access_token(client)
    gcp_focus.access_token(client)

    assert len(seen) == 3


def test_gcp_accepts_a_wildcard_on_the_export_table(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_GCP_FOCUS_VIEW", "")
    monkeypatch.setattr(
        settings,
        "FINOPS_GCP_BILLING_TABLE",
        "p.cnp_billing.gcp_billing_export_resource_v1_*",
    )

    assert "gcp_billing_export_resource_v1_*`" in gcp_focus._sql()


def test_aws_sts_call_is_unsigned_so_pod_keys_are_never_used(monkeypatch):
    built = {}

    def factory(service, **kwargs):
        built[service] = kwargs
        return _sts()

    monkeypatch.setattr(federation.boto3, "client", factory)

    federation.aws_credentials(httpx.Client(transport=_transport([])))

    assert built["sts"]["config"].signature_version is UNSIGNED


def test_keycloak_token_is_reused_then_renewed_near_expiry(monkeypatch):
    seen = []
    client = httpx.Client(transport=_transport(seen))

    federation.keycloak_token(client)
    federation.keycloak_token(client)
    monkeypatch.setitem(federation._keycloak, "expires", 0.0)
    federation.keycloak_token(client)

    assert len(seen) == 2


def test_gcp_token_expiry_follows_the_returned_expire_time():
    assert federation._expiry("2026-11-10T21:00:00.123456789Z") == (
        datetime(2026, 11, 10, 21, tzinfo=timezone.utc).timestamp()
    )


def test_gcp_rejects_a_wildcard_on_the_focus_view(monkeypatch):
    monkeypatch.setattr(
        settings,
        "FINOPS_GCP_FOCUS_VIEW",
        "p.cnp_billing.gcp_billing_export_resource_v1_*",
    )

    with pytest.raises(ValueError):
        gcp_focus._sql()


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404),
        httpx.ConnectError("tunnel down"),
    ],
)
def test_opencost_is_unreachable_on_error_status_or_connect_error(
    monkeypatch, answer
):
    def get(url, timeout):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(opencost.httpx, "get", get)

    assert opencost.reachable("http://aws-apps:18081") is False
