import asyncio
from io import StringIO
from unittest import mock

import pytest
from fastapi import HTTPException
from ruamel.yaml import YAML

from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers import releases
from app.services.release import settings
from app.services.release.settings import ReleaseSettings
from tests.test_release_api import (
    USER_TOKENS,
    FakeSource,
    _client,
    _paused,
    session_factory,
    keycloak,
)  # noqa: F401
from tests.test_release_view import REPLICA_SETS, _rollout
from app.services.release.source import RolloutSnapshot

CANARY = ReleaseSettings(
    strategy="canary",
    preset="fast",
    stickiness="cookie",
    smoke_path="/healthz",
    error_rate_max=2,
    min_requests=20,
    allow_force=False,
)

STATIC_VALUES = """\
image:
  repository: ghcr.io/3-istor/web
ingress:
  enabled: true  # exposed by the CMP
"""


def _load(text):
    return YAML().load(text)


def _dump(values):
    out = StringIO()
    YAML().dump(values, out)
    return out.getvalue()


class TestRead:
    def test_an_app_without_release_block_reads_as_the_chart_defaults(self):
        read = settings.read(_load(STATIC_VALUES))

        assert (read.strategy, read.preset, read.min_requests) == (
            "rolling",
            "safe",
            50,
        )

    def test_a_value_the_chart_refuses_reads_as_its_default(self):
        read = settings.read(
            _load("release:\n  strategy: bluegreen\n  preset: fast\n")
        )

        assert (read.strategy, read.preset) == ("rolling", "fast")


class TestApply:
    def test_writes_the_block_and_keeps_comments(self):
        values = _load(STATIC_VALUES)

        settings.apply(values, CANARY)

        text = _dump(values)
        assert (
            "# exposed by the CMP" in text
            and settings.read(_load(text)) == CANARY
        )

    def test_keeps_release_keys_the_form_does_not_know(self):
        values = _load(
            "release:\n  strategy: rolling\n  extra: kept  # mine\n"
        )

        settings.apply(values, CANARY)

        assert "extra: kept  # mine" in _dump(values)


class TestRefusal:
    @pytest.mark.parametrize(
        "values, expected",
        [
            ("ingress:\n  enabled: false\n", "expose"),
            (
                "ingress:\n  enabled: true\nservice:\n  enabled: false\n",
                "Service",
            ),
            (
                "ingress:\n  enabled: true\ndb:\n  enabled: true\n",
                "migrations",
            ),
        ],
    )
    def test_refuses_a_canary_the_chart_would_not_render(
        self, values, expected
    ):
        assert expected in settings.refusal(_load(values), CANARY)

    def test_accepts_a_canary_with_migrations_on_a_database(self):
        values = _load(
            "ingress:\n  enabled: true\ndb:\n  enabled: true\nmigrations:\n  enabled: true\n"
        )

        assert settings.refusal(values, CANARY) is None

    def test_never_refuses_rolling(self):
        rolling = CANARY.model_copy(update={"strategy": "rolling"})

        assert (
            settings.refusal(_load("ingress:\n  enabled: false\n"), rolling)
            is None
        )


class FakeRepo:
    def __init__(self, files):
        self.files = {
            path: (text, f"sha-{path}") for path, text in files.items()
        }
        self.commits = []

    async def load(self, token, repo, file_path):
        text, sha = self.files[file_path]
        return _load(text), sha

    async def put(
        self,
        installation_token,
        repo_full_name,
        file_path,
        content,
        message,
        sha,
    ):
        assert sha == self.files[file_path][1]
        self.commits.append((file_path, content, message))
        return {"commit": {"sha": "c0ffee"}}


BODY = {
    "strategy": "canary",
    "preset": "fast",
    "stickiness": "cookie",
    "smokePath": "/healthz",
    "errorRateMax": 2,
    "minRequests": 20,
    "allowForce": False,
}


@pytest.fixture
def linked(session_factory):
    with session_factory() as db:
        deployment = db.get(Deployment, 7)
        deployment.github_repo_url = "https://github.com/3-istor/web"
        db.commit()
    return session_factory


def _api(
    session_factory,
    files,
    source=None,
    who="dev",
    app_type="static",
    role="admin",
):
    repo = FakeRepo(files)
    client = _client(session_factory, source or FakeSource(None), who)
    patches = [
        mock.patch.object(releases, "_load_values_file", repo.load),
        mock.patch.object(releases, "put_file_content", repo.put),
        mock.patch.object(
            releases,
            "_get_github_token_for_deployment",
            mock.AsyncMock(return_value="gh"),
        ),
        mock.patch.object(releases, "_app_type_of", return_value=app_type),
        mock.patch.object(
            releases,
            "_authorize_app",
            side_effect=lambda db, token, deployment, minimum: _check(
                role, minimum
            ),
        ),
        mock.patch(
            "app.routers.deployments._authorize_app",
            side_effect=lambda db, token, deployment, minimum: _check(
                role, minimum
            ),
        ),
    ]
    for patch in patches:
        patch.start()
    return client, repo, patches


def _check(role, minimum):
    order = ["guest", "member", "admin", "owner"]
    if order.index(role) < order.index(minimum.value):
        raise HTTPException(status_code=403, detail="forbidden")


@pytest.fixture
def api(linked):
    started = []

    def build(*args, **kwargs):
        client, repo, patches = _api(linked, *args, **kwargs)
        started.extend(patches)
        return client, repo

    yield build
    for patch in started:
        patch.stop()


class TestEndpoints:
    def test_reads_defaults_for_an_app_without_release_block(self, api):
        client, _ = api({"deploy/values.yaml": STATIC_VALUES})

        body = client.get("/api/deployments/7/release/settings").json()

        assert (
            body["declared"],
            body["settings"]["strategy"],
            body["canEdit"],
        ) == (False, "rolling", True)

    def test_a_member_reads_but_cannot_edit(self, api):
        client, _ = api({"deploy/values.yaml": STATIC_VALUES}, role="member")

        assert (
            client.get("/api/deployments/7/release/settings").json()["canEdit"]
            is False
        )

    def test_a_member_cannot_change_the_settings(self, api):
        client, repo = api(
            {"deploy/values.yaml": STATIC_VALUES}, role="member"
        )

        response = client.put("/api/deployments/7/release/settings", json=BODY)

        assert (response.status_code, repo.commits) == (403, [])

    def test_switching_to_canary_commits_the_release_block(self, api):
        client, repo = api({"deploy/values.yaml": STATIC_VALUES})

        response = client.put("/api/deployments/7/release/settings", json=BODY)

        ((file_path, content, message),) = repo.commits
        assert response.status_code == 200
        assert (file_path, settings.read(_load(content)), message) == (
            "deploy/values.yaml",
            CANARY,
            "chore(release): canary, preset fast via CMP",
        )

    def test_an_unchanged_setting_makes_no_commit(self, api):
        values = _load(STATIC_VALUES)
        settings.apply(values, CANARY)
        client, repo = api({"deploy/values.yaml": _dump(values)})

        client.put("/api/deployments/7/release/settings", json=BODY)

        assert repo.commits == []

    def test_a_canary_the_chart_would_refuse_is_refused_before_any_commit(
        self, api
    ):
        client, repo = api(
            {"deploy/values.yaml": "ingress:\n  enabled: false\n"}
        )

        response = client.put("/api/deployments/7/release/settings", json=BODY)

        assert (response.status_code, repo.commits) == (422, [])

    def test_refused_while_a_release_is_in_progress(self, api):
        client, repo = api(
            {"deploy/values.yaml": STATIC_VALUES}, source=FakeSource(_paused())
        )

        response = client.put("/api/deployments/7/release/settings", json=BODY)

        assert (response.status_code, repo.commits) == (409, [])

    def test_an_unknown_preset_is_rejected(self, api):
        client, repo = api({"deploy/values.yaml": STATIC_VALUES})

        response = client.put(
            "/api/deployments/7/release/settings",
            json={**BODY, "preset": "yolo"},
        )

        assert (response.status_code, repo.commits) == (422, [])

    def test_fullstack_canary_goes_to_the_exposed_component_only(self, api):
        client, repo = api(
            {
                "deploy/values-frontend.yaml": STATIC_VALUES,
                "deploy/values-backend.yaml": "service:\n  enabled: true\n",
            },
            app_type="fullstack",
        )

        body = client.put(
            "/api/deployments/7/release/settings", json=BODY
        ).json()

        assert body["strategies"] == {
            "deploy/values-frontend.yaml": "canary",
            "deploy/values-backend.yaml": "rolling",
        }
