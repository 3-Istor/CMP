import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import template_assets
from app.services import terraform_executor
from app.services.terraform_executor import TerraformExecutor


class _Repo:
    def __init__(self, root):
        self.repo_path = root

    def get_available_templates(self):
        return [
            json.loads(p.read_text())
            for p in (self.repo_path / "templates").glob("*/manifest.json")
        ]


@pytest.fixture
def client(tmp_path):
    template = tmp_path / "templates" / "k3s-gitops-app"
    (template / "data" / "terraform_states" / "shop" / ".terraform").mkdir(
        parents=True
    )
    (template / "manifest.json").write_text(
        json.dumps({"id": "k3s-gitops-app", "image_path": "logo.png"})
    )
    (template / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (template / "cmp_override.tf").write_text('password = "secret"')
    (
        template
        / "data"
        / "terraform_states"
        / "shop"
        / ".terraform"
        / "terraform.tfstate"
    ).write_text('{"backend": {"config": {"secret_key": "secret"}}}')
    app = FastAPI()
    app.include_router(template_assets.router)
    with mock.patch.object(
        template_assets, "get_repository", return_value=_Repo(tmp_path)
    ):
        yield TestClient(app)


def test_serves_the_image_the_manifest_declares(client):
    response = client.get("/static/templates/k3s-gitops-app/logo.png")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "k3s-gitops-app/cmp_override.tf",
        "k3s-gitops-app/manifest.json",
        "k3s-gitops-app/data/terraform_states/shop/.terraform/terraform.tfstate",
        "k3s-gitops-app/..%2Fk3s-gitops-app%2Fcmp_override.tf",
        "k3s-gitops-app/",
    ],
)
def test_never_serves_generated_or_undeclared_files(client, path):
    response = client.get(f"/static/templates/{path}")

    assert response.status_code == 404


def test_provider_override_holds_no_openstack_credentials(tmp_path):
    settings = mock.Mock(
        OS_AUTH_URL="https://openstack.example:5000/v3",
        OS_USERNAME="cmp",
        OS_PASSWORD="dummy-password-marker",
        OS_PROJECT_NAME="cnp",
        OS_USER_DOMAIN_NAME="Default",
        OS_PROJECT_DOMAIN_NAME="Default",
    )
    executor = TerraformExecutor.__new__(TerraformExecutor)
    executor.working_dir = tmp_path

    with mock.patch.object(terraform_executor, "settings", settings):
        executor._create_provider_override()

    assert (
        "dummy-password-marker"
        not in (tmp_path / "cmp_override.tf").read_text()
    )
