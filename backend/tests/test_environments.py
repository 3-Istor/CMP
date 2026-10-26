import asyncio
import json
from datetime import timedelta
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.environment import (
    AppEnvironment,
    Promotion,
    PromotionPolicy,
    VerificationRun,
)
from app.routers import environments as router
from app.services.clusters import state
from app.services.environments import (
    activation,
    gitops,
    layout,
    observe,
    promotion,
    runs,
    suites,
)
from app.services.environments.observe import ComponentState, EnvironmentState
from tests.test_release_api import USER_TOKENS, keycloak  # noqa: F401

RC2 = {"app": "ghcr.io/3-istor/web@sha256:" + "2" * 64}
RC3 = {"app": "ghcr.io/3-istor/web@sha256:" + "3" * 64}
PROD = {"app": "ghcr.io/3-istor/web@sha256:" + "1" * 64}


def _pod(
    image, image_id, ready=True, name="cnp-generic-app", status_image=None
):
    return {
        "metadata": {"name": "p"},
        "spec": {"containers": [{"name": name, "image": image}]},
        "status": {
            "containerStatuses": [
                {
                    "name": name,
                    "image": status_image or image,
                    "imageID": image_id,
                    "ready": ready,
                }
            ]
        },
    }


def _state(digests, version, namespace="shop-web-stg", pods=1):
    return EnvironmentState(
        namespace=namespace,
        components={
            c: ComponentState(
                pods=pods,
                ready=pods,
                digest=d if pods else None,
                version=version if pods else None,
            )
            for c, d in digests.items()
        },
    )


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
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
                terraform_outputs=json.dumps(
                    {"app_type": "static", "hostname": "web-shop.3istor.com"}
                ),
            )
        )
        session.add(
            AppEnvironment(
                deployment_id=7,
                name="staging",
                state="ready",
                hostname="web-shop-staging.3istor.com",
                updated_at=runs.now(),
            )
        )
        session.commit()
        yield session


def _observed(staging, prod=None):
    states = {
        "staging": staging,
        "prod": prod or _state(PROD, "1.3.2", "shop-web"),
    }
    return mock.patch.object(
        observe, "observe", side_effect=lambda p, a, k, env: states[env]
    )


def _passed_run(db, suite, digests, age=timedelta(0)):
    run = VerificationRun(
        deployment_id=7,
        environment="staging",
        suite=suite,
        kind="smoke",
        digests=digests,
        version="1.4.0-rc.2",
        status="passed",
        actor="dev",
        started_at=runs.now() - age,
        deadline_at=runs.now(),
        finished_at=runs.now(),
    )
    db.add(run)
    db.commit()
    return run


class TestObservation:
    def test_reads_the_digest_and_version_the_pods_run(self):
        state = observe.component_state(
            [
                _pod(
                    "ghcr.io/3-istor/web:1.4.0-rc.2@sha256:" + "a" * 64,
                    "ghcr.io/3-istor/web@sha256:" + "b" * 64,
                )
            ]
        )

        assert (state.digest, state.version) == (
            "ghcr.io/3-istor/web@sha256:" + "b" * 64,
            "1.4.0-rc.2",
        )

    def test_ignores_the_config_id_containerd_sometimes_reports_as_image(self):
        digest = "ghcr.io/o/web@sha256:" + "4" * 64
        pods = [
            _pod("ghcr.io/o/web:1.2.0-rc.1@sha256:" + "4" * 64, digest),
            _pod(
                "ghcr.io/o/web:1.2.0-rc.1@sha256:" + "4" * 64,
                digest,
                status_image="sha256:" + "8" * 64,
            ),
        ]

        state = observe.component_state(pods)

        assert (state.digest, state.version) == (digest, "1.2.0-rc.1")

    def test_keeps_the_registry_port_in_the_repository(self):
        state = observe.component_state(
            [
                _pod(
                    "registry:5000/web:1.0.0",
                    "registry:5000/web@sha256:" + "c" * 64,
                )
            ]
        )

        assert state.digest == "registry:5000/web@sha256:" + "c" * 64

    def test_two_images_running_side_by_side_have_no_single_digest(self):
        state = observe.component_state(
            [
                _pod(
                    "ghcr.io/o/web:1.0.0", "ghcr.io/o/web@sha256:" + "1" * 64
                ),
                _pod(
                    "ghcr.io/o/web:1.1.0", "ghcr.io/o/web@sha256:" + "2" * 64
                ),
            ]
        )

        assert state.digest is None

    def test_an_environment_with_unready_pods_has_not_converged(self):
        state = EnvironmentState(
            namespace="n",
            components={"app": ComponentState(pods=2, ready=1, digest="d")},
        )

        assert not state.converged

    def test_components_on_different_versions_are_not_a_release(self):
        state = EnvironmentState(
            namespace="n",
            components={
                "frontend": ComponentState(
                    pods=1, ready=1, digest="f", version="1.2.0-rc.1"
                ),
                "backend": ComponentState(
                    pods=1, ready=1, digest="b", version="1.2.0-rc.2"
                ),
            },
        )

        assert not state.converged

    def test_an_environment_scaled_to_zero_is_asleep(self):
        assert _state(RC2, "x", pods=0).asleep

    def test_an_environment_with_one_component_scaled_to_zero_is_asleep(self):
        state = EnvironmentState(
            namespace="n",
            components={
                "frontend": ComponentState(
                    pods=1, ready=1, digest="f", version="1.2.0-rc.2"
                ),
                "backend": ComponentState(pods=0),
            },
        )

        assert state.asleep


class TestStagingValues:
    def test_staging_never_reuses_prod_secrets_identity_or_backups(self):
        values = layout.staging_values(
            {"repository": "ghcr.io/o/web", "tag": "1.3.2"},
            "shop",
            "web",
            "app",
            "web-shop-staging.3istor.com",
            "project-shop/web/staging",
            "shop-web-staging-role",
            "cnp-shop-web-stg",
            layout.DEFAULT_STAGING_OFFHOURS,
            exposed=True,
            has_auth=False,
        )

        assert (
            values["secrets"],
            values["ingress"]["oidc"]["clientID"],
            values["db"]["backup"]["enabled"],
        ) == (
            {
                "vaultPath": "project-shop/web/staging",
                "vaultRole": "shop-web-staging-role",
            },
            "cnp-shop-web-stg",
            False,
        )

    def test_staging_objects_keep_the_prod_names_so_short_service_names_resolve(
        self,
    ):
        values = layout.staging_values(
            {"repository": "r", "tag": "t"},
            "shop",
            "web",
            "backend",
            "h",
            "p",
            "r",
            "c",
            layout.DEFAULT_STAGING_OFFHOURS,
            exposed=False,
            has_auth=False,
        )

        assert values["fullnameOverride"] == "shop-web-backend-cnp-generic-app"

    def test_staging_is_put_to_sleep_by_default(self):
        values = layout.staging_values(
            {"repository": "r", "tag": "t"},
            "shop",
            "web",
            "app",
            "h",
            "p",
            "r",
            "c",
            layout.DEFAULT_STAGING_OFFHOURS,
            exposed=False,
            has_auth=False,
        )

        assert values["offhours"]["enabled"] is True


class TestPromotionSteps:
    def test_a_member_who_starts_a_promotion_needs_an_admin_approval(self, db):
        with _observed(_state(RC2, "1.4.0-rc.2")):
            item = asyncio.run(
                promotion.start(
                    db,
                    db.get(Deployment, 7),
                    "dev-alice",
                    actor_is_admin=False,
                )
            )

        assert [s["kind"] for s in item.steps] == ["run", "approve", "promote"]

    def test_an_emergency_skips_soak_and_approval_but_not_the_smoke(self, db):
        policy = PromotionPolicy(
            suites=[],
            soak_minutes=30,
            require_approval=True,
            auto_promote=False,
        )

        steps = promotion.build_steps(
            policy, needs_approval=True, emergency=True, rollback=False
        )

        assert [(s["name"], s["status"]) for s in steps] == [
            ("smoke", "pending"),
            ("soak", "skipped"),
            ("approval", "skipped"),
            ("prod", "pending"),
        ]

    def test_a_promotion_refuses_a_staging_that_is_asleep(self, db):
        with _observed(_state(RC2, "1.4.0-rc.2", pods=0)), pytest.raises(
            promotion.PromotionRefused
        ):
            asyncio.run(
                promotion.start(
                    db, db.get(Deployment, 7), "admin", actor_is_admin=True
                )
            )

    def test_a_promotion_refuses_what_prod_already_runs(self, db):
        with _observed(
            _state(RC2, "1.4.0-rc.2"), _state(RC2, "1.4.0-rc.2", "shop-web")
        ), pytest.raises(promotion.PromotionRefused):
            asyncio.run(
                promotion.start(
                    db, db.get(Deployment, 7), "admin", actor_is_admin=True
                )
            )


class TestPromotionProgress:
    def _start(self, db, staging=None, admin=True):
        with _observed(staging or _state(RC2, "1.4.0-rc.2")):
            return asyncio.run(
                promotion.start(
                    db, db.get(Deployment, 7), "admin", actor_is_admin=admin
                )
            )

    def test_reuses_a_recent_passing_smoke_of_the_same_digests_and_commits_them(
        self, db
    ):
        _passed_run(db, "smoke", RC2)
        item = self._start(db)
        committed = {}

        async def fake_commit(deployment, prom, step):
            committed.update(prom.digests)
            step["status"] = "passed"

        with _observed(_state(RC2, "1.4.0-rc.2")), mock.patch.object(
            promotion, "_commit", fake_commit
        ):
            asyncio.run(promotion.advance(db, item))

        assert (item.status, committed) == ("done", RC2)

    def test_a_week_old_smoke_is_no_evidence(self, db):
        _passed_run(db, "smoke", RC2, age=timedelta(days=8))

        assert runs.fresh_pass(db, 7, "staging", "smoke", RC2) is None

    def test_a_smoke_of_another_release_is_no_evidence(self, db):
        _passed_run(db, "smoke", RC3)

        assert runs.fresh_pass(db, 7, "staging", "smoke", RC2) is None

    def test_fails_when_staging_moved_on_before_its_checks_ran(self, db):
        item = self._start(db)

        with _observed(_state(RC3, "1.4.0-rc.3")):
            asyncio.run(promotion.advance(db, item))

        assert item.status == "failed"

    def test_waits_for_an_approval_then_goes_on(self, db):
        _passed_run(db, "smoke", RC2)
        item = self._start(db, admin=False)

        async def fake_commit(deployment, prom, step):
            step["status"] = "passed"

        with _observed(_state(RC2, "1.4.0-rc.2")), mock.patch.object(
            promotion, "_commit", fake_commit
        ):
            asyncio.run(promotion.advance(db, item))
            waiting = item.status
            promotion.approve(
                item, "bob-admin", approver_is_platform_admin=False
            )
            asyncio.run(promotion.advance(db, item))

        assert (waiting, item.status) == ("waiting_approval", "done")

    def test_the_requester_cannot_approve_their_own_promotion(self, db):
        item = self._start(db, admin=False)

        with pytest.raises(promotion.PromotionRefused):
            promotion.approve(item, "admin", approver_is_platform_admin=False)

    def test_an_inconclusive_metrics_run_waits_for_a_decision_when_the_suite_asks(
        self, db
    ):
        db.add(
            PromotionPolicy(
                deployment_id=7,
                suites=[
                    {
                        "name": "errors",
                        "kind": "metrics",
                        "onInconclusive": "ask",
                    }
                ],
                soak_minutes=0,
                require_approval=False,
                auto_promote=False,
            )
        )
        db.commit()
        _passed_run(db, "smoke", RC2)
        inconclusive = _passed_run(db, "errors", RC2)
        inconclusive.status = "inconclusive"
        db.commit()
        item = self._start(db)

        async def started(*args, **kwargs):
            return inconclusive

        with _observed(_state(RC2, "1.4.0-rc.2")), mock.patch.object(
            runs, "start", started
        ):
            asyncio.run(promotion.advance(db, item))

        assert (item.status, item.steps[1]["status"]) == (
            "waiting_approval",
            "waiting",
        )

    def test_soak_counts_only_the_time_staging_is_awake(self, db):
        db.add(
            PromotionPolicy(
                deployment_id=7,
                suites=[],
                soak_minutes=1,
                require_approval=False,
                auto_promote=False,
            )
        )
        db.commit()
        _passed_run(db, "smoke", RC2)
        item = self._start(db)

        with _observed(_state(RC2, "1.4.0-rc.2", pods=0)):
            asyncio.run(promotion.advance(db, item, tick_seconds=120))

        assert item.steps[1]["status"] == "running"


class TestExternalCallback:
    def _client(self, db):
        app = FastAPI()
        app.include_router(router.callbacks, prefix="/api")

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        return TestClient(app)

    def _external_run(self, db):
        token, digest = suites.new_token()
        run = VerificationRun(
            deployment_id=7,
            environment="staging",
            suite="e2e",
            kind="external",
            digests=RC2,
            status="running",
            token_hash=digest,
            actor="dev",
            started_at=runs.now(),
            deadline_at=runs.now() + timedelta(minutes=30),
        )
        db.add(run)
        db.commit()
        return run, token

    def test_records_the_result_posted_with_the_run_token(self, db):
        run, token = self._external_run(db)

        response = self._client(db).post(
            f"/api/verification-runs/{run.id}/result",
            json={"passed": True, "summary": "48/48"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert (response.status_code, run.status, run.summary) == (
            200,
            "passed",
            "48/48",
        )

    def test_refuses_a_result_without_the_run_token(self, db):
        run, _ = self._external_run(db)

        response = self._client(db).post(
            f"/api/verification-runs/{run.id}/result",
            json={"passed": True},
            headers={"Authorization": "Bearer guessed"},
        )

        assert response.status_code == 403

    def test_the_token_works_only_once(self, db):
        run, token = self._external_run(db)
        client = self._client(db)
        client.post(
            f"/api/verification-runs/{run.id}/result",
            json={"passed": False},
            headers={"Authorization": f"Bearer {token}"},
        )

        response = client.post(
            f"/api/verification-runs/{run.id}/result",
            json={"passed": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert (response.status_code, run.status) == (403, "failed")


class TestRunExpiry:
    def test_a_run_past_its_deadline_ends_in_error(self, db):
        run = VerificationRun(
            deployment_id=7,
            environment="staging",
            suite="e2e",
            kind="external",
            digests=RC2,
            status="running",
            actor="dev",
            started_at=runs.now() - timedelta(hours=1),
            deadline_at=runs.now() - timedelta(minutes=1),
        )
        db.add(run)
        db.commit()

        runs.expire(db)

        assert run.status == "error"


class TestSuiteSpec:
    def test_an_external_suite_names_its_workflow(self):
        assert suites.SuiteSpec(name="e2e", kind="external").problems()

    def test_smoke_is_reserved(self):
        assert suites.SuiteSpec(name="smoke", kind="metrics").problems()


class TestEnvironmentsApi:
    @pytest.fixture
    def client(self, db, keycloak):
        def build(who):
            app = FastAPI()
            app.include_router(router.router, prefix="/api")

            def override():
                yield db

            app.dependency_overrides[get_db] = override
            app.dependency_overrides[router.get_current_user] = (
                lambda: USER_TOKENS[who]
            )
            return TestClient(app)

        return build

    def test_a_guest_sees_both_environments(self, client):
        with _observed(_state(RC2, "1.4.0-rc.2")):
            response = client("guest").get("/api/deployments/7/environments")

        assert [e["name"] for e in response.json()["environments"]] == [
            "staging",
            "prod",
        ]

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("post", "/api/deployments/7/environments/staging", {}),
            ("post", "/api/deployments/7/promotions", {}),
        ],
    )
    def test_a_stopped_cluster_refuses_deploys(
        self, client, db, method, path, body
    ):
        db.get(Deployment, 7).app_config = json.dumps(
            {"github_installation_id": "42", "target_cloud": "aws"}
        )
        db.commit()

        with mock.patch.object(router, "_holds", return_value=True), (
            mock.patch.object(router, "_app", return_value=db.get(Deployment, 7))
        ), mock.patch.object(state, "fresh_state", lambda cloud: "down"):
            response = getattr(client("dev"), method)(path, json=body)

        assert response.status_code == 409
        assert "AWS cluster is down" in response.json()["detail"]

    def test_a_member_cannot_change_the_promotion_policy(self, client):
        response = client("dev").put(
            "/api/deployments/7/promotion-policy", json={"suites": []}
        )

        assert response.status_code == 403

    def test_an_outsider_cannot_read_the_environments(self, client):
        assert (
            client("outsider")
            .get("/api/deployments/7/environments")
            .status_code
            == 403
        )


class TestCloudObservation:
    def _application(self, images, sync="Synced", health="Healthy"):
        return {
            "status": {
                "sync": {"status": sync},
                "health": {"status": health},
                "operationState": {"phase": "Succeeded"},
                "summary": {"images": images},
            }
        }

    def test_reads_a_cloud_environment_from_argo_cd_and_resolves_the_tag_at_ghcr(
        self,
    ):
        apps = {
            "shop-web-frontend-stg": self._application(
                ["ghcr.io/3-istor/web/frontend:1.4.0-rc.2"]
            ),
            "shop-web-backend-stg": self._application(
                [
                    "ghcr.io/3-istor/web/backend:1.4.0-rc.2",
                    "cloudflare/cloudflared:2026.10.0",
                ]
            ),
        }

        with mock.patch.object(
            observe,
            "kube_get",
            side_effect=lambda path: apps[path.rsplit("/", 1)[1]],
        ), mock.patch.object(
            observe.registry,
            "resolve",
            side_effect=lambda repo, tag: f"{repo}@sha256:{tag}",
        ):
            state = observe.observe_through_argocd(
                "shop", "web", "fullstack", "staging"
            )

        assert (state.converged, state.version, state.digests["backend"]) == (
            True,
            "1.4.0-rc.2",
            "ghcr.io/3-istor/web/backend@sha256:1.4.0-rc.2",
        )

    def test_a_pinned_prod_image_needs_no_registry_lookup(self):
        pinned = "ghcr.io/3-istor/web:1.4.0-rc.2@sha256:" + "9" * 64

        with mock.patch.object(
            observe, "kube_get", return_value=self._application([pinned])
        ), mock.patch.object(
            observe.registry,
            "resolve",
            side_effect=AssertionError("no lookup"),
        ):
            state = observe.observe_through_argocd(
                "shop", "web", "static", "prod"
            )

        assert state.digests == {
            "app": "ghcr.io/3-istor/web@sha256:" + "9" * 64
        }

    def test_a_cloud_environment_still_syncing_has_not_converged(self):
        with mock.patch.object(
            observe,
            "kube_get",
            return_value=self._application(
                ["ghcr.io/3-istor/web:1.0.0@sha256:" + "1" * 64],
                sync="OutOfSync",
            ),
        ):
            state = observe.observe_through_argocd(
                "shop", "web", "static", "prod"
            )

        assert not state.converged

    def test_cloud_apps_are_observed_through_argo_cd(self, db):
        deployment = db.get(Deployment, 7)
        deployment.app_config = json.dumps(
            {"github_installation_id": "42", "target_cloud": "aws"}
        )

        with mock.patch.object(
            observe, "observe_through_argocd", return_value="argo"
        ) as through, mock.patch.object(
            observe,
            "observe",
            side_effect=AssertionError("no pod reads on a cloud"),
        ):
            assert observe.of(deployment, "staging") == "argo"

        through.assert_called_once_with("shop", "web", "static", "staging")


class TestTurningStagingOff:
    def test_prod_moves_to_the_final_build_of_its_candidate(self, db):
        async def prod_values(token, repo, path, ref):
            return {
                "image": {
                    "repository": "ghcr.io/3-istor/web",
                    "tag": "1.2.0-rc.1@sha256:" + "1" * 64,
                }
            }

        with mock.patch.object(
            activation.gitops, "token_for", return_value="t"
        ), mock.patch.object(
            activation.gitops, "read_values", side_effect=prod_values
        ), mock.patch.object(
            activation.registry,
            "resolve",
            return_value="ghcr.io/3-istor/web@sha256:x",
        ):
            images = asyncio.run(
                activation.final_versions(db.get(Deployment, 7))
            )

        assert images == {
            "app": {"repository": "ghcr.io/3-istor/web", "tag": "1.2.0"}
        }

    def test_refuses_when_the_final_version_was_never_pushed(self, db):
        async def prod_values(token, repo, path, ref):
            return {
                "image": {
                    "repository": "ghcr.io/3-istor/web",
                    "tag": "1.2.0-rc.1@sha256:" + "1" * 64,
                }
            }

        with mock.patch.object(
            activation.gitops, "token_for", return_value="t"
        ), mock.patch.object(
            activation.gitops, "read_values", side_effect=prod_values
        ), mock.patch.object(
            activation.registry,
            "resolve",
            side_effect=activation.registry.RegistryLookupError("404"),
        ), pytest.raises(
            activation.ActivationRefused, match="push v1.2.0 first"
        ):
            asyncio.run(activation.final_versions(db.get(Deployment, 7)))
