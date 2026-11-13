import pytest

from tests.test_app_security_data import (  # noqa: F401
    FRONTEND_YAML,
    FULLSTACK_FILES,
    client_factory,
    session_factory,
)

STATIC_FILES = {"deploy/values.yaml": FRONTEND_YAML}


def test_get_config_of_static_app_reads_values_yaml(client_factory):
    client, _, deployment_id = client_factory(STATIC_FILES, "member", "static")

    response = client.get(f"/api/deployments/{deployment_id}/config")

    assert response.json()["file_path"] == "deploy/values.yaml"


def test_get_config_of_static_app_lists_no_components(client_factory):
    client, _, deployment_id = client_factory(STATIC_FILES, "member", "static")

    response = client.get(f"/api/deployments/{deployment_id}/config")

    assert response.json()["components"] == []


def test_get_config_of_fullstack_app_defaults_to_frontend(client_factory):
    client, _, deployment_id = client_factory(
        FULLSTACK_FILES, "member", "fullstack"
    )

    response = client.get(f"/api/deployments/{deployment_id}/config")

    assert response.json()["file_path"] == "deploy/values-frontend.yaml"


def test_get_config_of_fullstack_app_lists_both_components(client_factory):
    client, _, deployment_id = client_factory(
        FULLSTACK_FILES, "member", "fullstack"
    )

    response = client.get(f"/api/deployments/{deployment_id}/config")

    assert response.json()["components"] == ["frontend", "backend"]


def test_get_config_reads_backend_file_when_asked(client_factory):
    client, _, deployment_id = client_factory(
        FULLSTACK_FILES, "member", "fullstack"
    )

    response = client.get(
        f"/api/deployments/{deployment_id}/config?component=backend"
    )

    assert response.json()["config"]["db"]["enabled"] is True


def test_get_config_rejects_unknown_component(client_factory):
    client, _, deployment_id = client_factory(
        FULLSTACK_FILES, "member", "fullstack"
    )

    response = client.get(
        f"/api/deployments/{deployment_id}/config?component=worker"
    )

    assert response.status_code == 400


def test_patch_config_commits_to_the_chosen_component_file(client_factory):
    client, repo, deployment_id = client_factory(
        FULLSTACK_FILES, "admin", "fullstack"
    )

    response = client.patch(
        f"/api/deployments/{deployment_id}/config?component=backend",
        json={"_sha": "sha-deploy/values-backend.yaml", "replicaCount": 2},
    )

    assert response.status_code == 200
    assert [path for path, _, _ in repo.commits] == [
        "deploy/values-backend.yaml"
    ]


TEMPLATE_APP_YAML = """\
project_name: "shop"
app_name: "web"
replicaCount: 1
image:
  repository: ghcr.io/3-istor/web
  tag: "1.0.0"
secrets:
  enabled: true
  vaultPath: "project-shop/web"
  vaultRole: "shop-web-role"
ingress:
  enabled: true
  sso_protected: true
"""

TEMPLATE_FILES = {"deploy/values.yaml": TEMPLATE_APP_YAML}
SHA = "sha-deploy/values.yaml"


@pytest.mark.parametrize(
    "payload",
    [
        {"secrets": {"vaultRole": "vault-secrets-operator-role"}},
        {"secrets": {"vaultPath": "kvv2/arcl-cmp/credentials"}},
        {"secrets": {"vaultRole": None}},
        {"project_name": "victim"},
        {"cnp": {"project": "arcl", "app": "cmp"}},
        {"registrySecret": {"scope": "project"}},
        {"links": [{"app": "vault", "port": 8200}]},
        {"image": {"repository": "ghcr.io/attacker/miner"}},
        {"fullnameOverride": "other"},
    ],
)
def test_patch_config_refuses_platform_managed_values(client_factory, payload):
    # Arrange
    client, repo, deployment_id = client_factory(
        TEMPLATE_FILES, "admin", "static"
    )

    # Act
    response = client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": SHA, **payload},
    )

    # Assert
    assert (response.status_code, repo.commits) == (422, [])


def test_patch_config_names_the_refused_values(client_factory):
    client, _, deployment_id = client_factory(
        TEMPLATE_FILES, "admin", "static"
    )

    response = client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": SHA, "secrets": {"vaultRole": "arcl-cmp-role"}},
    )

    assert "secrets.vaultRole" in response.json()["detail"]


def test_patch_config_accepts_the_whole_file_echoed_with_an_editable_change(
    client_factory,
):
    # Arrange: the config panel sends every key back, platform ones unchanged.
    client, repo, deployment_id = client_factory(
        TEMPLATE_FILES, "admin", "static"
    )
    whole_file = {
        "project_name": "shop",
        "app_name": "web",
        "replicaCount": 3,
        "image": {"repository": "ghcr.io/3-istor/web", "tag": "1.0.0"},
        "secrets": {
            "enabled": True,
            "vaultPath": "project-shop/web",
            "vaultRole": "shop-web-role",
        },
        "ingress": {"enabled": True, "sso_protected": True},
    }

    # Act
    response = client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": SHA, **whole_file},
    )

    # Assert
    assert response.status_code == 200
    assert "replicaCount: 3" in repo.commits[0][2]


@pytest.mark.parametrize(
    "payload",
    [
        {"replicaCount": 2},
        {"image": {"tag": "1.1.0"}},
        {"db": {"enabled": True, "name": "web"}},
        {"resources": {"limits": {"memory": "512Mi"}}},
        {"offhours": {"enabled": False}},
        {"ingress": {"sso_protected": False}},
    ],
)
def test_patch_config_accepts_user_editable_values(client_factory, payload):
    client, _, deployment_id = client_factory(
        TEMPLATE_FILES, "admin", "static"
    )

    response = client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": SHA, **payload},
    )

    assert response.status_code == 200
