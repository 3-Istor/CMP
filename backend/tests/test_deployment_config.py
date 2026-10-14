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
