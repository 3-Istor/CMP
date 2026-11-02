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
from app.models.environment import AppEnvironment
from app.routers import environments as router
from app.services.environments import diff, gitops, runs
from tests.test_release_api import USER_TOKENS, keycloak  # noqa: F401

DIGEST_A = "ghcr.io/o/web@sha256:" + "a" * 64
DIGEST_B = "ghcr.io/o/web@sha256:" + "b" * 64


def _pod(tag, digest, cpu="100m", memory="128Mi", ready=True):
    return {
        "metadata": {"name": "p"},
        "spec": {
            "containers": [
                {
                    "name": "cnp-generic-app",
                    "image": f"ghcr.io/o/web:{tag}",
                    "resources": {
                        "requests": {"cpu": cpu, "memory": memory},
                    },
                }
            ]
        },
        "status": {
            "containerStatuses": [
                {
                    "name": "cnp-generic-app",
                    "imageID": digest,
                    "ready": ready,
                }
            ]
        },
    }


def _replica_set(replicas):
    return {"spec": {"replicas": replicas}, "metadata": {}}


def _runtime(tag, digest, **kwargs):
    return diff.runtime_of([_pod(tag, digest, **kwargs)], [_replica_set(1)])


def _field(result, key):
    return next(f for f in result.fields if f.key == key)


class TestRuntime:
    def test_reads_tag_digest_and_requests_from_the_pods(self):
        runtime = _runtime("1.4.0-rc.2", DIGEST_A, cpu="250m", memory="1Gi")

        assert (
            runtime.image_tag,
            runtime.image_digest,
            runtime.ready_replicas,
            runtime.cpu_request,
            runtime.memory_request,
        ) == ("1.4.0-rc.2", DIGEST_A, "1/1", "250m", "1Gi")

    def test_desired_replicas_come_from_the_replica_sets(self):
        runtime = diff.runtime_of([_pod("1.0.0", DIGEST_A)], [_replica_set(3)])

        assert runtime.ready_replicas == "1/3"

    def test_a_scaled_down_environment_reads_zero_of_zero(self):
        runtime = diff.runtime_of([], [_replica_set(0)])

        assert runtime.ready_replicas == "0/0"


class TestFields:
    def test_a_field_differs_when_both_sides_are_known_and_unequal(self):
        result = diff.compute(
            True,
            {"app": _runtime("1.4.0", DIGEST_B)},
            {"app": _runtime("1.3.0", DIGEST_A)},
            {"app": {}},
            {"app": {}},
        )

        assert _field(result, "imageTag").differs is True
        assert _field(result, "cpuRequest").differs is False

    def test_an_unknown_side_does_not_count_as_a_difference(self):
        result = diff.compute(
            True,
            {"app": diff.Runtime()},
            {"app": _runtime("1.3.0", DIGEST_A)},
            {"app": {}},
            {"app": {}},
        )

        assert not any(f.differs for f in result.fields)

    def test_a_fullstack_app_prefixes_each_field_with_its_component(self):
        runtime = {
            c: _runtime("1.0.0", DIGEST_A) for c in ("frontend", "backend")
        }
        empty = {c: {} for c in runtime}

        result = diff.compute(True, runtime, runtime, empty, empty)

        assert "backend.imageTag" in {f.key for f in result.fields}

    def test_staging_absent_gives_an_explicit_empty_state(self):
        result = diff.compute(False, {}, {}, {}, {})

        assert (result.staging_available, result.fields, result.values) == (
            False,
            [],
            [],
        )


class TestValues:
    def test_each_environment_file_overrides_the_shared_one(self):
        shared = {"replicaCount": 2, "resources": {"limits": {"cpu": "1"}}}
        staging = diff.merged(shared, {"replicaCount": 1})
        prod = diff.merged(
            shared, {"resources": {"limits": {"memory": "2Gi"}}}
        )

        rows = diff.values_diff({"app": staging}, {"app": prod})

        assert {(r.path, r.staging, r.prod) for r in rows} == {
            ("replicaCount", 1, 2),
            ("resources.limits.memory", None, "2Gi"),
        }

    def test_merging_does_not_alter_the_shared_values(self):
        shared = {"a": {"b": 1}}

        diff.merged(shared, {"a": {"b": 2}})

        assert shared == {"a": {"b": 1}}

    def test_identical_values_are_not_listed(self):
        values = {"app": {"replicaCount": 2}}

        assert diff.values_diff(values, values) == []

    @pytest.mark.parametrize(
        "key", ["secrets", "dbPassword", "apiKey", "authToken"]
    )
    def test_secret_looking_keys_never_leave(self, key):
        staging = {"app": {key: {"vaultPath": "a"}, "ok": 1}}
        prod = {"app": {key: {"vaultPath": "b"}, "ok": 2}}

        rows = diff.values_diff(staging, prod)

        assert [r.path for r in rows] == ["ok"]

    def test_the_image_is_left_to_the_fields(self):
        rows = diff.values_diff(
            {"app": {"image": {"tag": "1"}}}, {"app": {"image": {"tag": "2"}}}
        )

        assert rows == []

    def test_a_fullstack_value_is_prefixed_with_its_component(self):
        rows = diff.values_diff(
            {"frontend": {"x": 1}, "backend": {"x": 1}},
            {"frontend": {"x": 2}, "backend": {"x": 1}},
        )

        assert [r.path for r in rows] == ["frontend.x"]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(
            Deployment(
                id=7,
                name="web",
                template_id="k3s-gitops-app",
                project_id="shop",
                provider_type=ProviderType.KUBERNETES,
                status=DeploymentStatus.RUNNING,
                github_repo_url="https://github.com/3-Istor/web",
                app_config=json.dumps({"github_installation_id": "42"}),
                terraform_outputs=json.dumps({"app_type": "static"}),
            )
        )
        session.commit()
        yield session


@pytest.fixture
def client(db, keycloak):
    def build(who):
        app = FastAPI()
        app.include_router(router.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[router.get_current_user] = lambda: (
            USER_TOKENS[who]
        )
        return TestClient(app)

    return build


def _enable_staging(db):
    db.add(
        AppEnvironment(
            deployment_id=7,
            name="staging",
            state="ready",
            hostname="web-shop-staging.3istor.com",
            updated_at=runs.now(),
        )
    )
    db.commit()


FILES = {
    "deploy/values.yaml": {"replicaCount": 2, "secrets": {"vaultPath": "x"}},
    "deploy/env/staging/app.yaml": {
        "replicaCount": 1,
        "secrets": {"vaultPath": "staging"},
    },
    "deploy/env/prod/app.yaml": {"image": {"tag": "1.3.0"}},
}


async def _read_values(token, repo, path, ref):
    return FILES.get(path)


def _kube(pods_by_namespace):
    def kube_list(path, params=None, cloud=None):
        namespace = path.split("/namespaces/")[1].split("/")[0]
        if path.endswith("/replicasets"):
            return [_replica_set(1)]
        return pods_by_namespace[namespace]

    return kube_list


class TestApi:
    def test_a_guest_reads_the_fields_but_not_the_configuration_values(
        self, client, db
    ):
        _enable_staging(db)
        pods = {
            "shop-web-stg": [_pod("1.4.0-rc.2", DIGEST_B)],
            "shop-web": [_pod("1.3.0", DIGEST_A)],
        }

        with mock.patch.object(
            gitops, "read_values", _read_values
        ), mock.patch.object(
            gitops, "token_for", mock.AsyncMock(return_value="t")
        ), mock.patch.object(
            diff, "kube_list", _kube(pods)
        ):
            response = client("guest").get(
                "/api/deployments/7/environments/diff"
            )

        body = response.json()
        assert response.status_code == 200
        assert "vaultPath" not in response.text
        assert body["values"] == []
        assert {f["key"]: f["differs"] for f in body["fields"]}[
            "imageTag"
        ] is True

    def test_a_member_reads_the_differences_without_any_secret(
        self, client, db
    ):
        _enable_staging(db)
        pods = {
            "shop-web-stg": [_pod("1.4.0-rc.2", DIGEST_B)],
            "shop-web": [_pod("1.3.0", DIGEST_A)],
        }

        with mock.patch.object(
            gitops, "read_values", _read_values
        ), mock.patch.object(
            gitops, "token_for", mock.AsyncMock(return_value="t")
        ), mock.patch.object(
            diff, "kube_list", _kube(pods)
        ):
            response = client("dev").get(
                "/api/deployments/7/environments/diff"
            )

        body = response.json()
        assert response.status_code == 200
        assert "vaultPath" not in response.text
        assert [v["path"] for v in body["values"]] == ["replicaCount"]
        assert {f["key"]: f["differs"] for f in body["fields"]}[
            "imageTag"
        ] is True

    def test_staging_absent_returns_the_empty_state(self, client):
        response = client("guest").get("/api/deployments/7/environments/diff")

        assert response.json() == {
            "stagingAvailable": False,
            "fields": [],
            "values": [],
        }

    def test_an_outsider_is_refused(self, client):
        assert (
            client("outsider")
            .get("/api/deployments/7/environments/diff")
            .status_code
            == 403
        )

    def test_an_unknown_app_is_a_404(self, client):
        assert (
            client("guest")
            .get("/api/deployments/999/environments/diff")
            .status_code
            == 404
        )
