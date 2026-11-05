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
from app.services.environments import diff, runs
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
    def test_reads_tag_and_requests_from_the_pods(self):
        runtime = _runtime("1.4.0-rc.2", DIGEST_A, cpu="250m", memory="1Gi")

        assert (
            runtime.image_tag,
            runtime.ready_replicas,
            runtime.cpu_request,
            runtime.memory_request,
        ) == ("1.4.0-rc.2", "1/1", "250m", "1Gi")

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
        )

        assert _field(result, "imageTag").status == "differs"

    def test_equal_known_sides_are_same(self):
        result = diff.compute(
            True,
            {"app": _runtime("1.4.0", DIGEST_B)},
            {"app": _runtime("1.3.0", DIGEST_A)},
        )

        assert _field(result, "cpuRequest").status == "same"

    def test_an_unknown_side_is_neither_same_nor_different(self):
        result = diff.compute(
            True,
            {"app": diff.Runtime()},
            {"app": _runtime("1.3.0", DIGEST_A)},
        )

        assert {f.status for f in result.fields} == {"unknown"}

    def test_no_digest_and_no_values_are_returned(self):
        result = diff.compute(
            True,
            {"app": _runtime("1.4.0", DIGEST_B)},
            {"app": _runtime("1.3.0", DIGEST_A)},
        )

        assert {f.key for f in result.fields} == {
            "imageTag",
            "readyReplicas",
            "cpuRequest",
            "memoryRequest",
        }
        assert "values" not in result.model_dump()

    def test_an_asleep_staging_marks_every_field_unknown(self):
        asleep = diff.runtime_of([], [_replica_set(0)])

        result = diff.compute(
            True, {"app": asleep}, {"app": _runtime("1", "")}
        )

        assert result.staging_asleep is True
        assert {f.status for f in result.fields} == {"unknown"}

    def test_a_half_asleep_staging_is_not_asleep(self):
        runtime = {
            "frontend": diff.runtime_of([], [_replica_set(0)]),
            "backend": _runtime("1.0.0", DIGEST_A),
        }

        result = diff.compute(True, runtime, runtime)

        assert result.staging_asleep is False
        assert _field(result, "backend.imageTag").status == "same"

        # An unreadable component is not asleep either.
        runtime["backend"] = diff.Runtime()
        assert diff.is_asleep(runtime) is False

    def test_a_sleeping_component_is_never_claimed_to_differ(self):
        staging = {
            "frontend": diff.runtime_of([], [_replica_set(0)]),
            "backend": _runtime("2.0.0", DIGEST_B),
        }
        prod = {
            "frontend": _runtime("1.0.0", DIGEST_A),
            "backend": _runtime("1.0.0", DIGEST_A),
        }

        result = diff.compute(True, staging, prod)

        assert result.staging_asleep is False
        assert {
            f.status for f in result.fields if f.key.startswith("frontend.")
        } == {"unknown"}
        assert _field(result, "backend.imageTag").status == "differs"
        assert _field(result, "backend.cpuRequest").status == "same"

    def test_no_component_is_not_asleep(self):
        assert diff.is_asleep({}) is False

    def test_a_fullstack_app_prefixes_each_field_with_its_component(self):
        runtime = {
            c: _runtime("1.0.0", DIGEST_A) for c in ("frontend", "backend")
        }

        result = diff.compute(True, runtime, runtime)

        assert "backend.imageTag" in {f.key for f in result.fields}

    def test_staging_absent_gives_an_explicit_empty_state(self):
        result = diff.compute(False, {}, {})

        assert (
            result.staging_available,
            result.staging_asleep,
            result.fields,
        ) == (False, False, [])


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


def _kube(pods_by_namespace):
    def kube_list(path, params=None, cloud=None):
        namespace = path.split("/namespaces/")[1].split("/")[0]
        if path.endswith("/replicasets"):
            return [_replica_set(1)]
        return pods_by_namespace[namespace]

    return kube_list


class TestApi:
    @pytest.mark.parametrize("who", ["guest", "dev"])
    def test_a_reader_gets_the_statuses_and_no_values(self, client, db, who):
        _enable_staging(db)
        pods = {
            "shop-web-stg": [_pod("1.4.0-rc.2", DIGEST_B)],
            "shop-web": [_pod("1.3.0", DIGEST_A)],
        }

        with mock.patch.object(diff, "kube_list", _kube(pods)):
            response = client(who).get("/api/deployments/7/environments/diff")

        body = response.json()
        assert response.status_code == 200
        assert "values" not in body
        assert "sha256" not in response.text
        assert body["stagingAsleep"] is False
        assert {f["key"]: f["status"] for f in body["fields"]}[
            "imageTag"
        ] == "differs"

    def test_staging_absent_returns_the_empty_state(self, client):
        response = client("guest").get("/api/deployments/7/environments/diff")

        assert response.json() == {
            "stagingAvailable": False,
            "stagingAsleep": False,
            "fields": [],
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
