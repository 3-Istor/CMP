from unittest import mock

import pytest
import requests

from app.services import vault_auth
from app.services.vault_auth import VaultAuthError


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self._body = body if body is not None else {}
        self.status_code = status_code

    def json(self):
        return self._body


def login_response(token="s.login", ttl=3600):
    return FakeResponse(
        {"auth": {"client_token": token, "lease_duration": ttl}}
    )


def renew_response(ttl=3600):
    return FakeResponse({"auth": {"client_token": "x", "lease_duration": ttl}})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(vault_auth.time, "monotonic", fake)
    return fake


@pytest.fixture
def kubernetes(monkeypatch, tmp_path, clock):
    jwt = tmp_path / "token"
    jwt.write_text("sa-jwt\n")
    monkeypatch.setattr(
        vault_auth,
        "settings",
        mock.Mock(
            VAULT_URL="http://vault:8200",
            VAULT_AUTH="kubernetes",
            VAULT_KUBERNETES_MOUNT="kubernetes",
            VAULT_KUBERNETES_ROLE="cmp-backend",
            VAULT_KUBERNETES_JWT_PATH=str(jwt),
            VAULT_TOKEN="legacy",
        ),
    )
    vault_auth.reset()
    yield
    vault_auth.reset()


@pytest.fixture
def post(kubernetes):
    with mock.patch.object(vault_auth.requests, "post") as fake:
        yield fake


def paths(post):
    return [call.args[0].rsplit("/v1/", 1)[1] for call in post.call_args_list]


def test_token_returns_the_static_token_when_kubernetes_auth_is_off(
    monkeypatch,
):
    monkeypatch.setattr(
        vault_auth,
        "settings",
        mock.Mock(VAULT_AUTH="", VAULT_TOKEN="legacy", VAULT_URL="http://v"),
    )

    assert vault_auth.token() == "legacy"


def test_token_logs_in_with_the_service_account_jwt(post):
    post.return_value = login_response()

    token = vault_auth.token()

    assert token == "s.login"
    assert post.call_args.args[0] == (
        "http://vault:8200/v1/auth/kubernetes/login"
    )
    assert post.call_args.kwargs["json"] == {
        "role": "cmp-backend",
        "jwt": "sa-jwt",
    }


def test_token_is_reused_while_most_of_the_lease_is_left(post, clock):
    post.return_value = login_response()
    vault_auth.token()
    clock.now += 1800

    vault_auth.token()

    assert paths(post) == ["auth/kubernetes/login"]


def test_token_is_renewed_when_less_than_a_third_of_the_lease_is_left(
    post, clock
):
    post.side_effect = [login_response(), renew_response()]
    vault_auth.token()
    clock.now += 2500

    token = vault_auth.token()

    assert token == "s.login"
    assert paths(post) == ["auth/kubernetes/login", "auth/token/renew-self"]
    assert post.call_args.kwargs["headers"] == {"X-Vault-Token": "s.login"}


def test_token_logs_in_again_when_the_renewal_is_refused(post, clock):
    post.side_effect = [
        login_response("s.first"),
        FakeResponse(status_code=403),
        login_response("s.second"),
    ]
    vault_auth.token()
    clock.now += 2500

    assert vault_auth.token() == "s.second"


def test_token_logs_in_again_when_renewal_hits_the_max_ttl(post, clock):
    post.side_effect = [
        login_response("s.first"),
        renew_response(ttl=300),
        login_response("s.second"),
    ]
    vault_auth.token()
    clock.now += 2500

    assert vault_auth.token() == "s.second"


def test_token_logs_in_again_once_the_lease_has_expired(post, clock):
    post.side_effect = [login_response("s.first"), login_response("s.second")]
    vault_auth.token()
    clock.now += 4000

    assert vault_auth.token() == "s.second"
    assert paths(post) == ["auth/kubernetes/login", "auth/kubernetes/login"]


def test_token_renews_when_the_caller_needs_more_than_is_left(post, clock):
    post.side_effect = [login_response(), renew_response()]
    vault_auth.token()
    clock.now += 1200

    vault_auth.token(min_ttl=vault_auth.TERRAFORM_MIN_TTL)

    assert paths(post)[-1] == "auth/token/renew-self"


@pytest.mark.parametrize(
    "outcome",
    [FakeResponse(status_code=403), requests.ConnectionError("down")],
    ids=["refused", "unreachable"],
)
def test_token_raises_when_the_login_fails(post, outcome):
    post.side_effect = [outcome]

    with pytest.raises(VaultAuthError):
        vault_auth.token()


def test_token_raises_when_the_service_account_token_is_missing(
    post, tmp_path
):
    vault_auth.settings.VAULT_KUBERNETES_JWT_PATH = str(tmp_path / "missing")

    with pytest.raises(VaultAuthError):
        vault_auth.token()
    post.assert_not_called()


def test_terraform_env_skips_the_child_token_with_kubernetes_auth(post):
    post.return_value = login_response()

    env = vault_auth.terraform_env()

    assert env == {
        "TF_VAR_vault_url": "http://vault:8200",
        "VAULT_ADDR": "http://vault:8200",
        "TF_VAR_vault_token": "s.login",
        "VAULT_TOKEN": "s.login",
        "TERRAFORM_VAULT_SKIP_CHILD_TOKEN": "true",
    }


def test_terraform_env_passes_the_static_token_without_kubernetes_auth(
    monkeypatch,
):
    monkeypatch.setattr(
        vault_auth,
        "settings",
        mock.Mock(VAULT_AUTH="", VAULT_TOKEN="legacy", VAULT_URL="http://v"),
    )

    env = vault_auth.terraform_env()

    assert "TERRAFORM_VAULT_SKIP_CHILD_TOKEN" not in env
    assert env["TF_VAR_vault_token"] == "legacy"


def test_terraform_env_refuses_a_role_whose_tokens_are_too_short(post):
    post.return_value = login_response(ttl=600)

    with pytest.raises(VaultAuthError):
        vault_auth.terraform_env()


def test_token_accepts_a_short_lived_token_for_api_calls(post):
    post.return_value = login_response(ttl=600)

    assert vault_auth.token() == "s.login"


def test_vault_auth_error_is_a_runtime_error():
    assert issubclass(VaultAuthError, RuntimeError)
