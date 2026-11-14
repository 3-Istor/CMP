"""
The CMP's objects in each project's Grafana org (D17): the traces
datasource through vmauth as the project's user, and the dashboards per
role, written only when they changed.
"""

import asyncio
import json
from unittest import mock

import httpx
import pytest

from app.services import grafana_service, metrics_isolation
from app.services.observability import grafana_project as gp


@pytest.fixture(autouse=True)
def configured():
    gp._applied.clear()  # pylint: disable=protected-access
    with (
        mock.patch.object(gp.settings, "METRICS_PROJECT_KEY", "k" * 64),
        mock.patch.object(
            gp.settings, "OBS_VTRACES_URL", "http://vtraces.test"
        ),
        mock.patch.object(gp.settings, "GRAFANA_ADMIN_PASSWORD", "x"),
        mock.patch.object(
            gp.settings, "OBS_GRAFANA_PROVISIONING_ENABLED", True
        ),
    ):
        yield


class FakeGrafana:
    def __init__(self, existing=()):
        self.sent: list[httpx.Request] = []
        self.existing = set(existing)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        path = request.url.path
        if path == "/api/orgs/name/Project Shop":
            return httpx.Response(200, json={"id": 42})
        if path.startswith("/api/orgs/name/"):
            return httpx.Response(404)
        if request.method == "GET":
            found = path.rsplit("/", 1)[-1] in self.existing
            return httpx.Response(200 if found else 404, json={})
        return httpx.Response(200, json={})

    def writes(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.sent if r.method != "GET"]


def _sync(fake: FakeGrafana, project: str = "shop") -> bool:
    async def run():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handler),
            base_url="http://grafana.test",
        )
        with (
            mock.patch.object(gp, "_get_http_client", lambda: client),
            mock.patch.object(
                grafana_service, "_get_http_client", lambda: client
            ),
            mock.patch.object(
                grafana_service, "GRAFANA_BASE_URL", "http://grafana.test"
            ),
        ):
            return await gp.sync_project(project)

    return asyncio.run(run())


def test_the_traces_datasource_reads_through_vmauth_as_the_project():
    ds = gp.traces_datasource("shop")

    assert (ds["url"], ds["basicAuthUser"]) == (
        f"{gp.settings.OBS_VMAUTH_PROJECTS_URL}/select/jaeger",
        "project-shop",
    )


def test_the_traces_datasource_password_is_the_projects_vmauth_one():
    ds = gp.traces_datasource("shop")

    assert ds["secureJsonData"]["basicAuthPassword"] == (
        metrics_isolation.password("shop")
    )


def test_no_traces_datasource_before_the_traces_store_exists():
    with mock.patch.object(gp.settings, "OBS_VTRACES_URL", ""):
        wanted = gp.desired("shop")

    assert "datasource" not in wanted


def test_a_first_sync_writes_folder_datasource_and_both_dashboards():
    fake = FakeGrafana()

    _sync(fake)

    assert fake.writes() == [
        ("POST", "/api/folders"),
        ("POST", "/api/datasources"),
        ("POST", "/api/dashboards/db"),
        ("POST", "/api/dashboards/db"),
    ]


def test_every_write_targets_the_projects_own_org():
    fake = FakeGrafana()

    _sync(fake)

    orgs = {
        r.headers.get("X-Grafana-Org-Id")
        for r in fake.sent
        if not r.url.path.startswith("/api/orgs/")
    }
    assert orgs == {"42"}


def test_an_existing_datasource_is_replaced_not_duplicated():
    fake = FakeGrafana(existing={"cnp-portal", "cnp-traces-shop"})

    _sync(fake)

    assert ("PUT", "/api/datasources/uid/cnp-traces-shop") in fake.writes()


def test_an_unchanged_project_is_not_written_twice():
    fake = FakeGrafana()
    _sync(fake)
    fake.sent.clear()

    _sync(fake)

    assert fake.sent == []


def test_a_project_without_an_org_yet_is_left_for_later():
    fake = FakeGrafana()

    written = _sync(fake, "neworg")

    assert (written, fake.writes()) == (False, [])


def test_nothing_is_written_with_the_flag_off():
    with (
        mock.patch.object(
            gp.settings, "OBS_GRAFANA_PROVISIONING_ENABLED", False
        ),
        mock.patch.object(gp, "sync_project") as sync,
    ):
        asyncio.run(gp.sync_quietly())

    sync.assert_not_called()


@pytest.mark.parametrize(
    "dashboard", [gp.app_dashboard(), gp.project_dashboard()]
)
def test_dashboards_are_generated_read_only(dashboard):
    assert (dashboard["editable"], "generated" in dashboard["tags"]) == (
        False,
        True,
    )


@pytest.mark.parametrize(
    "dashboard", [gp.app_dashboard(), gp.project_dashboard()]
)
def test_dashboards_never_name_a_project_or_a_tenant(dashboard):
    text = json.dumps(dashboard)

    assert ("cnp_project" not in text, "AccountID" not in text) == (
        True,
        True,
    )
