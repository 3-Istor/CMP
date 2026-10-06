import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, ProviderType
from app.routers import deployments as deployments_router
from app.services import app_security_data as sd
from app.services import keycloak_service
from app.services.keycloak_service import get_current_user

FRONTEND_YAML = """\
ingress:
  enabled: true
  sso_protected: true   # keep me
  allowedGroups: []
"""

BACKEND_YAML = """\
db:
  enabled: true
  backup:
    enabled: false
    keepAfterDelete: false
"""

SINGLE_YAML = FRONTEND_YAML + BACKEND_YAML


@pytest.mark.parametrize(
    "ingress,expected",
    [
        ({"sso_protected": False, "allowedGroups": []}, "public"),
        ({"sso_protected": False}, "public"),
        ({"sso_protected": True, "allowedGroups": []}, "project_users"),
        (
            {
                "sso_protected": True,
                "allowedGroups": ["project-admins", "project-members"],
            },
            "project_members",
        ),
        (
            {"sso_protected": True, "allowedGroups": ["project-admins"]},
            "project_admins",
        ),
        (
            {"sso_protected": True, "allowedGroups": ["other"]},
            "custom",
        ),
        (
            {"sso_protected": False, "allowedGroups": ["project-admins"]},
            "custom",
        ),
    ],
)
def test_read_exposure_maps_values_to_preset(ingress, expected):
    assert sd.read_exposure({"ingress": ingress}) == expected


@pytest.mark.parametrize("preset", list(sd.ExposurePreset))
def test_exposure_patch_round_trips_through_read_exposure(preset):
    assert sd.read_exposure(sd.exposure_patch(preset)) == preset.value


def test_exposure_patch_does_not_share_group_lists():
    patch = sd.exposure_patch(sd.ExposurePreset.PROJECT_ADMINS)
    patch["ingress"]["allowedGroups"].append("x")

    assert sd.PROJECT_ADMINS_GROUPS == ["project-admins"]


@pytest.mark.parametrize("retention", ["7d", "4w", "12m", "30d"])
def test_retention_accepts_valid_values(retention):
    sd.SecurityDataUpdate(backup={"retention_policy": retention})


@pytest.mark.parametrize(
    "retention", ["0d", "07d", "7", "d", "7y", "-1d", "7d "]
)
def test_retention_rejects_invalid_values(retention):
    with pytest.raises(ValidationError):
        sd.SecurityDataUpdate(backup={"retention_policy": retention})


def test_update_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        sd.SecurityDataUpdate(exposure="public", ingress={"enabled": False})


def test_update_rejects_empty_payload():
    with pytest.raises(ValidationError):
        sd.SecurityDataUpdate(backup={})


def test_update_rejects_custom_exposure():
    with pytest.raises(ValidationError):
        sd.SecurityDataUpdate(exposure="custom")


def test_values_files_for_fullstack_splits_frontend_and_backend():
    assert sd.values_files_for("fullstack") == (
        "deploy/values-frontend.yaml",
        "deploy/values-backend.yaml",
    )


def test_values_files_for_static_uses_single_file():
    assert sd.values_files_for("static") == ("deploy/values.yaml",) * 2


class FakeRepo:
    def __init__(self, files: dict[str, str]):
        self.files = dict(files)
        self.commits: list[tuple[str, str, str]] = []

    async def get(
        self, installation_token, repo_full_name, file_path, ref="main"
    ):
        return self.files[file_path], f"sha-{file_path}"

    async def put(
        self,
        installation_token,
        repo_full_name,
        file_path,
        content,
        message,
        sha,
    ):
        self.files[file_path] = content
        self.commits.append((file_path, message, content))
        return {"commit": {"sha": f"commit-{len(self.commits)}"}}


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def add_deployment(factory, app_type: str) -> int:
    with factory() as db:
        row = Deployment(
            name="shop",
            template_id="k3s-gitops-app",
            provider_type=ProviderType.KUBERNETES,
            project_id="demo",
            github_repo_url="https://github.com/3-istor/shop",
            app_config=json.dumps(
                {"app_type": app_type, "github_installation_id": "1"}
            ),
        )
        db.add(row)
        db.commit()
        return row.id


def make_client(session_factory, repo: FakeRepo, role: str | None):
    app = FastAPI()
    app.include_router(deployments_router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "user-1"}

    patches = [
        mock.patch.object(deployments_router, "get_file_content", repo.get),
        mock.patch.object(deployments_router, "update_file_content", repo.put),
        mock.patch.object(
            deployments_router,
            "get_installation_token",
            mock.AsyncMock(return_value="tok"),
        ),
        mock.patch.object(
            deployments_router, "get_project_role", lambda user, project: role
        ),
        mock.patch.object(
            keycloak_service, "get_project_role", lambda user, project: role
        ),
    ]
    for patch in patches:
        patch.start()
    return TestClient(app), patches


@pytest.fixture
def client_factory(session_factory):
    started = []

    def build(files: dict[str, str], role: str | None, app_type: str):
        deployment_id = add_deployment(session_factory, app_type)
        repo = FakeRepo(files)
        client, patches = make_client(session_factory, repo, role)
        started.extend(patches)
        return client, repo, deployment_id

    yield build
    for patch in started:
        patch.stop()


FULLSTACK_FILES = {
    "deploy/values-frontend.yaml": FRONTEND_YAML,
    "deploy/values-backend.yaml": BACKEND_YAML,
}


def test_put_rejects_project_members(client_factory):
    client, repo, deployment_id = client_factory(
        FULLSTACK_FILES, "member", "fullstack"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"exposure": "public"},
    )

    assert response.status_code == 403
    assert repo.commits == []


def test_put_rejects_users_outside_the_project(client_factory):
    client, repo, deployment_id = client_factory(
        FULLSTACK_FILES, None, "fullstack"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"exposure": "public"},
    )

    assert response.status_code == 403
    assert repo.commits == []


def test_put_rejects_arbitrary_keys_with_422(client_factory):
    client, repo, deployment_id = client_factory(
        FULLSTACK_FILES, "admin", "fullstack"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"exposure": "public", "image": {"tag": "evil"}},
    )

    assert response.status_code == 422
    assert repo.commits == []


def test_put_exposure_on_fullstack_writes_frontend_file_only(client_factory):
    client, repo, deployment_id = client_factory(
        FULLSTACK_FILES, "admin", "fullstack"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"exposure": "project_members"},
    )

    assert response.status_code == 200
    assert [c[0] for c in repo.commits] == ["deploy/values-frontend.yaml"]
    assert repo.commits[0][1] == "chore(cmp): set exposure to project members"
    written = repo.files["deploy/values-frontend.yaml"]
    assert "- project-members" in written
    assert "- project-admins" in written
    assert "# keep me" in written


def test_put_backup_on_fullstack_writes_backend_file_only(client_factory):
    client, repo, deployment_id = client_factory(
        FULLSTACK_FILES, "admin", "fullstack"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"backup": {"enabled": True, "retention_policy": "14d"}},
    )

    assert response.status_code == 200
    assert [c[0] for c in repo.commits] == ["deploy/values-backend.yaml"]
    written = repo.files["deploy/values-backend.yaml"]
    assert "enabled: true" in written
    assert "retentionPolicy: 14d" in written
    assert repo.commits[0][1] == (
        "chore(cmp): enable database backups, set backup retention to 14d"
    )


def test_put_both_on_static_app_makes_one_commit_on_single_file(
    client_factory,
):
    client, repo, deployment_id = client_factory(
        {"deploy/values.yaml": SINGLE_YAML}, "admin", "static"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"exposure": "public", "backup": {"keep_after_delete": True}},
    )

    assert response.status_code == 200
    assert len(repo.commits) == 1
    assert repo.commits[0][0] == "deploy/values.yaml"
    assert repo.commits[0][1] == (
        "chore(cmp): set exposure to public, keep backups after app deletion"
    )
    written = repo.files["deploy/values.yaml"]
    assert "sso_protected: false" in written
    assert "keepAfterDelete: true" in written


def test_put_backup_without_database_returns_400(client_factory):
    client, repo, deployment_id = client_factory(
        {"deploy/values.yaml": FRONTEND_YAML}, "admin", "static"
    )

    response = client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"backup": {"enabled": True}},
    )

    assert response.status_code == 400
    assert repo.commits == []


def test_get_returns_state_from_both_fullstack_files(client_factory):
    client, _, deployment_id = client_factory(
        FULLSTACK_FILES, "member", "fullstack"
    )

    response = client.get(f"/api/deployments/{deployment_id}/security-data")

    assert response.status_code == 200
    assert response.json() == {
        "repo": "3-istor/shop",
        "can_edit": False,
        "exposure": "project_users",
        "database": {
            "backup": {
                "enabled": False,
                "keep_after_delete": False,
                "retention_policy": None,
            }
        },
    }


def test_get_hides_database_when_backend_has_none(client_factory):
    client, _, deployment_id = client_factory(
        {
            "deploy/values-frontend.yaml": FRONTEND_YAML,
            "deploy/values-backend.yaml": "db:\n  enabled: false\n",
        },
        "admin",
        "fullstack",
    )

    body = client.get(f"/api/deployments/{deployment_id}/security-data").json()

    assert body["database"] is None
    assert body["can_edit"] is True


def test_get_rejects_users_outside_the_project(client_factory):
    client, _, deployment_id = client_factory(
        FULLSTACK_FILES, None, "fullstack"
    )

    response = client.get(f"/api/deployments/{deployment_id}/security-data")

    assert response.status_code == 403
