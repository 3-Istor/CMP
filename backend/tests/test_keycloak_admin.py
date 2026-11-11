from unittest import mock

import pytest

from app.services import keycloak_admin


def configure(monkeypatch, **values):
    defaults = {
        "KEYCLOAK_URL": "https://kc",
        "KEYCLOAK_PROVISIONER_CLIENT_ID": "",
        "KEYCLOAK_PROVISIONER_CLIENT_SECRET": "",
    }
    monkeypatch.setattr(
        keycloak_admin, "settings", mock.Mock(**{**defaults, **values})
    )


@pytest.fixture
def provisioner(monkeypatch):
    configure(
        monkeypatch,
        KEYCLOAK_PROVISIONER_CLIENT_ID="cmp-provisioner",
        KEYCLOAK_PROVISIONER_CLIENT_SECRET="s3cret",
    )


def test_master_token_form_uses_client_credentials(provisioner):
    assert keycloak_admin.master_token_form() == {
        "grant_type": "client_credentials",
        "client_id": "cmp-provisioner",
        "client_secret": "s3cret",
    }


def test_terraform_env_hands_the_client_credentials_to_the_modules(
    provisioner,
):
    env = keycloak_admin.terraform_env()

    assert env == {
        "TF_VAR_keycloak_url": "https://kc",
        "TF_VAR_keycloak_client_id": "cmp-provisioner",
        "TF_VAR_keycloak_client_secret": "s3cret",
    }


def test_terraform_env_never_hands_admin_user_credentials(monkeypatch):
    configure(monkeypatch)

    env = keycloak_admin.terraform_env()

    assert not any("admin" in key for key in env)


def test_is_configured_is_false_without_the_client_secret(monkeypatch):
    configure(monkeypatch, KEYCLOAK_PROVISIONER_CLIENT_ID="cmp-provisioner")

    assert not keycloak_admin.is_configured()
