from unittest import mock

import pytest

from app.services import keycloak_admin


def configure(monkeypatch, **values):
    defaults = {
        "KEYCLOAK_URL": "https://kc",
        "KEYCLOAK_PROVISIONER_CLIENT_ID": "",
        "KEYCLOAK_PROVISIONER_CLIENT_SECRET": "",
        "KEYCLOAK_ADMIN_USERNAME": "",
        "KEYCLOAK_ADMIN_PASSWORD": "",
    }
    monkeypatch.setattr(
        keycloak_admin, "settings", mock.Mock(**{**defaults, **values})
    )


@pytest.fixture
def both(monkeypatch):
    configure(
        monkeypatch,
        KEYCLOAK_PROVISIONER_CLIENT_ID="cmp-provisioner",
        KEYCLOAK_PROVISIONER_CLIENT_SECRET="s3cret",
        KEYCLOAK_ADMIN_USERNAME="admin",
        KEYCLOAK_ADMIN_PASSWORD="pw",
    )


def test_master_token_form_prefers_the_provisioner_client(both):
    assert keycloak_admin.master_token_form() == {
        "grant_type": "client_credentials",
        "client_id": "cmp-provisioner",
        "client_secret": "s3cret",
    }


def test_master_token_form_falls_back_to_the_admin_user(monkeypatch):
    configure(
        monkeypatch,
        KEYCLOAK_ADMIN_USERNAME="admin",
        KEYCLOAK_ADMIN_PASSWORD="pw",
    )

    assert keycloak_admin.master_token_form()["grant_type"] == "password"


def test_master_token_form_ignores_a_client_without_secret(monkeypatch):
    configure(
        monkeypatch,
        KEYCLOAK_PROVISIONER_CLIENT_ID="cmp-provisioner",
        KEYCLOAK_ADMIN_USERNAME="admin",
        KEYCLOAK_ADMIN_PASSWORD="pw",
    )

    assert keycloak_admin.master_token_form()["client_id"] == "admin-cli"


def test_terraform_env_does_not_hand_the_admin_password_to_the_client(both):
    env = keycloak_admin.terraform_env()

    assert env == {
        "TF_VAR_keycloak_url": "https://kc",
        "TF_VAR_keycloak_client_id": "cmp-provisioner",
        "TF_VAR_keycloak_client_secret": "s3cret",
    }


def test_is_configured_is_false_without_any_identity(monkeypatch):
    configure(monkeypatch, KEYCLOAK_ADMIN_USERNAME="admin")

    assert not keycloak_admin.is_configured()
