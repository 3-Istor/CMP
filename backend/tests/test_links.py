import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers import links as links_router
from app.services import keycloak_service
from app.services import links as links_service
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError

USER_TOKENS = {
    "admin": {"preferred_username": "adm", "realm_access": {"roles": []}},
    "member": {"preferred_username": "mem", "realm_access": {"roles": []}},
    "viewer": {"preferred_username": "vie", "realm_access": {"roles": []}},
    "outsider": {"preferred_username": "eve", "realm_access": {"roles": []}},
}
KEYCLOAK_IDS = {
    "adm": "uid-adm",
    "mem": "uid-mem",
    "vie": "uid-vie",
    "eve": "uid-eve",
}
ROLES = {
    ("uid-adm", "shop"): "admin",
    ("uid-mem", "shop"): "member",
    ("uid-vie", "shop"): "guest",
}


def _application(name):
    return {"metadata": {"name": name}}


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all(
            [
                Deployment(
                    id=1,
                    name="web",
                    template_id="k3s-gitops-app",
                    project_id="shop",
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                    github_repo_url="https://github.com/3-Istor/shop-web.git",
                    terraform_outputs=json.dumps({"app_type": "fullstack"}),
                ),
                Deployment(
                    id=2,
                    name="site",
                    template_id="k3s-gitops-app",
                    project_id="shop",
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                    github_repo_url="https://github.com/3-Istor/shop-site",
                    terraform_outputs=json.dumps({"app_type": "static"}),
                ),
                Deployment(
                    id=3,
                    name="gone",
                    template_id="k3s-gitops-app",
                    project_id="shop",
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.DELETED,
                    github_repo_url="https://github.com/3-Istor/shop-gone",
                ),
            ]
        )
        db.commit()
    return factory


@pytest.fixture
def keycloak():
    with (
        mock.patch.object(
            keycloak_service, "_get_admin_token", return_value="admin"
        ),
        mock.patch.object(
            keycloak_service,
            "_find_user_by_username",
            side_effect=lambda username, _: {"id": KEYCLOAK_IDS[username]},
        ),
        mock.patch.object(
            keycloak_service,
            "get_project_role",
            side_effect=lambda user_id, project: ROLES.get((user_id, project)),
        ),
    ):
        yield


@pytest.fixture
def kube():
    with mock.patch.object(links_service, "kube_list") as kube_list:
        kube_list.return_value = []
        yield kube_list


@pytest.fixture(autouse=True)
def grafana_org():
    with mock.patch.object(
        links_router.grafana, "org_id", new=mock.AsyncMock(return_value=5)
    ) as org:
        yield org


def _client(session_factory, who):
    app = FastAPI()
    app.include_router(links_router.projects, prefix="/api")
    app.include_router(links_router.deployments, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS[who]
    return TestClient(app)


def _by_id(body):
    return {link["id"]: link for link in body}


class TestProjectLinks:
    def test_admin_gets_every_project_link(self, session_factory, keycloak):
        client = _client(session_factory, "admin")

        body = client.get("/api/projects/shop/links").json()

        assert {link["id"] for link in body} == {
            "argocd_project",
            "status",
            "offhours",
            "vault",
            "grafana_org",
            "github_repo",
        }

    def test_argocd_project_targets_the_project_applications(
        self, session_factory, keycloak
    ):
        client = _client(session_factory, "admin")

        link = _by_id(client.get("/api/projects/shop/links").json())[
            "argocd_project"
        ]

        assert (
            link["url"] == "https://argocd.3istor.com/applications?proj=shop"
        )

    def test_status_and_offhours_use_the_project_hosts(
        self, session_factory, keycloak
    ):
        client = _client(session_factory, "admin")

        links = _by_id(client.get("/api/projects/shop/links").json())

        assert (links["status"]["url"], links["offhours"]["url"]) == (
            "https://status-shop.3istor.com/",
            "https://offhours-shop.3istor.com/",
        )

    def test_vault_points_at_the_project_folder_of_the_kv_mount(
        self, session_factory, keycloak
    ):
        client = _client(session_factory, "admin")

        link = _by_id(client.get("/api/projects/shop/links").json())["vault"]

        assert link["url"] == (
            "https://vault.3istor.com/ui/vault/secrets/kvv2/kv/list/projects/shop/"
        )

    def test_grafana_org_is_flagged_as_requiring_org_membership(
        self, session_factory, keycloak
    ):
        client = _client(session_factory, "admin")

        link = _by_id(client.get("/api/projects/shop/links").json())[
            "grafana_org"
        ]

        assert (link["url"], link["requires"]) == (
            "https://grafana.3istor.com/?orgId=5",
            "grafana_member",
        )

    def test_grafana_link_is_absent_when_the_org_does_not_exist(
        self, session_factory, keycloak, grafana_org
    ):
        grafana_org.side_effect = RuntimeError("no org")
        client = _client(session_factory, "admin")

        body = client.get("/api/projects/shop/links").json()

        assert "grafana_org" not in _by_id(body)

    def test_github_repo_lists_the_repo_of_each_live_app(
        self, session_factory, keycloak
    ):
        client = _client(session_factory, "admin")

        body = client.get("/api/projects/shop/links").json()

        assert sorted(
            link["url"] for link in body if link["id"] == "github_repo"
        ) == [
            "https://github.com/3-Istor/shop-site",
            "https://github.com/3-Istor/shop-web",
        ]

    def test_member_does_not_get_vault(self, session_factory, keycloak):
        client = _client(session_factory, "member")

        body = client.get("/api/projects/shop/links").json()

        assert "vault" not in _by_id(body)

    def test_member_keeps_the_observability_links(
        self, session_factory, keycloak
    ):
        client = _client(session_factory, "member")

        body = client.get("/api/projects/shop/links").json()

        assert {"argocd_project", "status", "offhours", "grafana_org"} <= set(
            _by_id(body)
        )

    def test_viewer_only_gets_repositories(self, session_factory, keycloak):
        client = _client(session_factory, "viewer")

        body = client.get("/api/projects/shop/links").json()

        assert {link["id"] for link in body} == {"github_repo"}

    def test_outsider_is_refused(self, session_factory, keycloak):
        client = _client(session_factory, "outsider")

        assert client.get("/api/projects/shop/links").status_code == 403


class TestAppLinks:
    def test_fullstack_app_gets_one_link_per_real_application(
        self, session_factory, keycloak, kube
    ):
        kube.return_value = [
            _application("shop-web-frontend"),
            _application("shop-web-backend"),
        ]
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/1/links").json()

        assert [
            (link["component"], link["url"])
            for link in body
            if link["id"] == "argocd_component"
        ] == [
            (
                "backend",
                "https://argocd.3istor.com/applications/argocd/shop-web-backend?resource=",
            ),
            (
                "frontend",
                "https://argocd.3istor.com/applications/argocd/shop-web-frontend?resource=",
            ),
        ]

    def test_single_component_app_gets_the_app_application(
        self, session_factory, keycloak, kube
    ):
        kube.return_value = [_application("shop-site-app")]
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/2/links").json()

        assert [
            link["component"]
            for link in body
            if link["id"] == "argocd_component"
        ] == ["app"]

    def test_applications_are_read_with_the_app_and_project_labels(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "admin")

        client.get("/api/deployments/1/links")

        kube.assert_called_once_with(
            links_service.APPLICATIONS_PATH,
            {
                "labelSelector": "cnp.3istor.com/app=web,cnp.3istor.com/project=shop"
            },
        )

    def test_staging_application_keeps_its_suffix_in_the_component(
        self, session_factory, keycloak, kube
    ):
        kube.return_value = [
            _application("shop-site-app"),
            _application("shop-site-app-stg"),
        ]
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/2/links").json()

        assert [
            link["component"]
            for link in body
            if link["id"] == "argocd_component"
        ] == ["app", "app-stg"]

    def test_fullstack_fallback_uses_the_backend_and_frontend_names(
        self, session_factory, keycloak, kube
    ):
        kube.side_effect = KubeUnavailableError("down")
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/1/links").json()

        assert [
            link["component"]
            for link in body
            if link["id"] == "argocd_component"
        ] == ["frontend", "backend"]

    def test_single_component_fallback_uses_the_app_name(
        self, session_factory, keycloak, kube
    ):
        kube.side_effect = KubeUnavailableError("down")
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/2/links").json()

        assert [
            link["url"] for link in body if link["id"] == "argocd_component"
        ] == [
            "https://argocd.3istor.com/applications/argocd/shop-site-app?resource="
        ]

    def test_fallback_is_logged(self, session_factory, keycloak, kube, caplog):
        kube.side_effect = KubeUnavailableError("down")
        client = _client(session_factory, "admin")

        client.get("/api/deployments/2/links")

        assert "not readable" in caplog.text

    def test_vault_points_at_the_secret_of_the_app(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "admin")

        link = _by_id(client.get("/api/deployments/1/links").json())["vault"]

        assert link["url"] == (
            "https://vault.3istor.com/ui/vault/secrets/kvv2/kv/"
            "projects%2Fshop%2Fweb/details"
        )

    def test_github_repo_drops_the_git_suffix(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "admin")

        link = _by_id(client.get("/api/deployments/1/links").json())[
            "github_repo"
        ]

        assert link["url"] == "https://github.com/3-Istor/shop-web"

    def test_github_actions_hangs_off_the_repo(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "admin")

        link = _by_id(client.get("/api/deployments/1/links").json())[
            "github_actions"
        ]

        assert link["url"] == "https://github.com/3-Istor/shop-web/actions"

    def test_member_gets_argocd_and_code_but_not_vault(
        self, session_factory, keycloak, kube
    ):
        kube.return_value = [_application("shop-site-app")]
        client = _client(session_factory, "member")

        body = client.get("/api/deployments/2/links").json()

        assert {link["id"] for link in body} == {
            "argocd_component",
            "github_repo",
            "github_actions",
        }

    def test_viewer_only_gets_the_repository(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "viewer")

        body = client.get("/api/deployments/1/links").json()

        assert {link["id"] for link in body} == {"github_repo"}

    def test_outsider_is_refused(self, session_factory, keycloak, kube):
        client = _client(session_factory, "outsider")

        assert client.get("/api/deployments/1/links").status_code == 403

    def test_unknown_app_is_a_404(self, session_factory, keycloak, kube):
        client = _client(session_factory, "admin")

        assert client.get("/api/deployments/99/links").status_code == 404

    def test_links_are_external_and_labelled_with_i18n_keys(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/1/links").json()

        assert all(
            link["external"] and link["label_key"].startswith("links.")
            for link in body
        )

    def test_no_app_level_grafana_link_is_emitted(
        self, session_factory, keycloak, kube
    ):
        client = _client(session_factory, "admin")

        body = client.get("/api/deployments/1/links").json()

        assert not [link for link in body if "grafana" in link["id"]]
