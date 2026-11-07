from unittest import mock

import pytest
import requests

from app.services import keycloak_admin, project_preflight, vault_auth
from app.services.project_preflight import (
    PreflightUnavailableError,
    find_leftovers,
)


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self._body = body if body is not None else {}
        self.status_code = status_code

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def route(
    dns=(),
    realm_exists=False,
    clients=(),
    groups=(),
    mounts=("secret/", "sys/"),
):
    def handler(url, **kwargs):
        if "/dns_records" in url:
            hostname = kwargs["params"]["name"]
            return FakeResponse({"result": [{}] if hostname in dns else []})
        if "/v1/sys/mounts" in url:
            return FakeResponse({"data": {m: {} for m in mounts}})
        if url.endswith("/clients"):
            return FakeResponse([{"clientId": c} for c in clients])
        if url.endswith("/groups"):
            return FakeResponse([{"name": g} for g in groups])
        if "/admin/realms/" in url:
            if realm_exists == "forbidden":
                return FakeResponse(status_code=403)
            return FakeResponse(status_code=200 if realm_exists else 404)
        raise AssertionError(f"unexpected GET {url}")

    return handler


@pytest.fixture
def configured():
    settings = mock.Mock(
        KEYCLOAK_URL="https://kc",
        KEYCLOAK_PROVISIONER_CLIENT_ID="",
        KEYCLOAK_PROVISIONER_CLIENT_SECRET="",
        KEYCLOAK_ADMIN_USERNAME="admin",
        KEYCLOAK_ADMIN_PASSWORD="pw",
        VAULT_URL="https://vault",
        VAULT_AUTH="",
        VAULT_TOKEN="vt",
        CLOUDFLARE_API_TOKEN="cf",
        CLOUDFLARE_ZONE_ID="zone",
    )
    with (
        mock.patch.object(project_preflight, "settings", settings),
        mock.patch.object(keycloak_admin, "settings", settings),
        mock.patch.object(vault_auth, "settings", settings),
    ):
        yield settings


@pytest.fixture
def http(configured):
    with mock.patch.object(project_preflight, "requests") as fake:
        fake.RequestException = requests.RequestException
        fake.post.return_value = FakeResponse({"access_token": "tok"})
        fake.get.side_effect = route()
        yield fake


def test_find_leftovers_returns_nothing_when_the_project_never_existed(http):
    assert find_leftovers("test-p") == []


def test_find_leftovers_lists_every_leftover_resource(http):
    http.get.side_effect = route(
        dns={"status-test-p.3istor.com", "offhours-test-p.3istor.com"},
        realm_exists=True,
        clients={"broker-test-p", "broker-test-p-other"},
        groups={"project-test-p-admins", "project-test-p-members-old"},
        mounts=("secret/", "project-test-p/"),
    )

    leftovers = find_leftovers("test-p")

    assert leftovers == [
        "Cloudflare DNS record 'status-test-p.3istor.com'",
        "Cloudflare DNS record 'offhours-test-p.3istor.com'",
        "Keycloak realm 'test-p'",
        "Keycloak client 'broker-test-p' in realm '3istor'",
        "Keycloak group 'project-test-p-admins' in realm '3istor'",
        "Vault mount 'project-test-p/'",
    ]


def test_find_leftovers_sends_a_non_default_user_agent_to_every_service(http):
    find_leftovers("test-p")

    calls = http.get.call_args_list + http.post.call_args_list
    assert calls
    for call in calls:
        assert not call.kwargs["headers"]["User-Agent"].startswith("python")


def test_find_leftovers_skips_cloudflare_when_it_is_not_configured(http):
    project_preflight.settings.CLOUDFLARE_API_TOKEN = ""

    find_leftovers("test-p")

    queried = [c.args[0] for c in http.get.call_args_list]
    assert not any("dns_records" in url for url in queried)


def test_find_leftovers_fails_when_a_service_cannot_be_queried(http):
    http.get.side_effect = requests.ConnectionError("boom")

    with pytest.raises(PreflightUnavailableError):
        find_leftovers("test-p")


def test_find_leftovers_counts_a_realm_the_provisioner_cannot_see(
    http, configured
):
    configured.KEYCLOAK_PROVISIONER_CLIENT_ID = "cmp-provisioner"
    configured.KEYCLOAK_PROVISIONER_CLIENT_SECRET = "s3cret"
    http.get.side_effect = route(realm_exists="forbidden")

    assert find_leftovers("test-p") == ["Keycloak realm 'test-p'"]


def test_find_leftovers_fails_on_a_forbidden_realm_with_the_admin_user(http):
    http.get.side_effect = route(realm_exists="forbidden")

    with pytest.raises(PreflightUnavailableError):
        find_leftovers("test-p")


def test_find_leftovers_logs_in_with_the_provisioner_client(http, configured):
    configured.KEYCLOAK_PROVISIONER_CLIENT_ID = "cmp-provisioner"
    configured.KEYCLOAK_PROVISIONER_CLIENT_SECRET = "s3cret"

    find_leftovers("test-p")

    form = http.post.call_args.kwargs["data"]
    assert form["grant_type"] == "client_credentials"
