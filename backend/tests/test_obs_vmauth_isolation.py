"""
R1 against the real binaries: project "shop" reads metrics, logs and traces
through vmauth with the config the CMP renders, and tries every known way to
reach project "other". Each attempt must come back empty or refused.
"""

import http.client
import json
import subprocess
import time
from unittest import mock

import httpx
import pytest

from app.services import metrics_isolation as mi
from tests.obs_stack import (
    Process,
    binary,
    need,
    otlp_trace,
    store,
    wait_for_trace,
)

NS = {"shop-web": "shop", "other-web": "other"}
ACCOUNTS = {"shop": 7, "other": 8}
SHOP_TRACE = "11111111111111111111111111111111"
OTHER_TRACE = "22222222222222222222222222222222"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    need("vmauth", "victoria-metrics", "victoria-logs", "victoria-traces")
    data = tmp_path_factory.mktemp("stores")
    metrics = store("victoria-metrics", data)
    logs = store("victoria-logs", data)
    traces = store("victoria-traces", data)

    httpx.post(
        f"{metrics.url}/api/v1/import/prometheus",
        content='app_up{namespace="shop-web"} 1\n'
        'app_up{namespace="other-web"} 1\n',
    ).raise_for_status()
    lines = [
        ({}, {"namespace": "shop-web", "msg": "shop line"}),
        ({}, {"namespace": "other-web", "msg": "other line"}),
        ({"AccountID": "5"}, {"namespace": "shop-web", "msg": "tenant 5"}),
    ]
    for headers, line in lines:
        httpx.post(
            f"{logs.url}/insert/jsonline",
            params={"_stream_fields": "namespace", "_msg_field": "msg"},
            headers=headers,
            content=json.dumps(line),
        ).raise_for_status()
    for trace, project in ((SHOP_TRACE, "shop"), (OTHER_TRACE, "other")):
        httpx.post(
            f"{traces.url}/insert/opentelemetry/v1/traces",
            headers={
                "Content-Type": "application/x-protobuf",
                "AccountID": str(ACCOUNTS[project]),
            },
            content=otlp_trace(trace, f"{project}-api", project),
        ).raise_for_status()
    for store_ in (metrics, logs):
        httpx.get(f"{store_.url}/internal/force_flush")
    for trace, project in ((SHOP_TRACE, "shop"), (OTHER_TRACE, "other")):
        wait_for_trace(traces.url, trace, ACCOUNTS[project])

    with (
        mock.patch.object(mi.settings, "METRICS_PROJECT_KEY", "k" * 64),
        mock.patch.object(mi.settings, "OBS_VMSINGLE_URL", metrics.url),
        mock.patch.object(mi.settings, "OBS_VLOGS_URL", logs.url),
        mock.patch.object(mi.settings, "OBS_VTRACES_URL", traces.url),
    ):
        config = mi.render(NS, ACCOUNTS)
        password = mi.password("shop")
    config_file = data / "auth.json"
    config_file.write_text(config)
    vmauth = Process("vmauth", [f"-auth.config={config_file}"])
    vmauth.wait(f"{vmauth.url}/health")
    time.sleep(1)

    yield vmauth.url, ("project-shop", password), config_file
    for process in (vmauth, metrics, logs, traces):
        process.stop()


def _get(stack, path, params=None, headers=None, auth=None):
    url, shop, _ = stack
    return httpx.get(
        f"{url}{path}",
        params=params,
        headers=headers,
        auth=auth or shop,
        timeout=10,
    )


def _namespaces(response) -> set[str]:
    return {
        r["metric"]["namespace"] for r in response.json()["data"]["result"]
    }


def _log_lines(response) -> set[str]:
    return {json.loads(line)["_msg"] for line in response.text.splitlines()}


def test_vmauth_accepts_the_rendered_config(stack):
    _, _, config_file = stack

    result = subprocess.run(
        [binary("vmauth"), "-dryRun", f"-auth.config={config_file}"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]


def test_metrics_return_only_the_projects_series(stack):
    response = _get(stack, "/api/v1/query", {"query": "app_up"})

    assert _namespaces(response) == {"shop-web"}


@pytest.mark.parametrize("arg", ["extra_filters", "extra_filters[]"])
def test_a_client_metrics_filter_cannot_widen_the_scope(stack, arg):
    response = _get(
        stack,
        "/api/v1/query",
        {"query": "app_up", arg: '{namespace="other-web"}'},
    )

    assert _namespaces(response) <= {"shop-web"}


def test_naming_another_namespace_in_the_query_returns_nothing(stack):
    response = _get(
        stack, "/api/v1/query", {"query": 'app_up{namespace="other-web"}'}
    )

    assert _namespaces(response) == set()


def test_logs_return_only_the_projects_lines(stack):
    response = _get(stack, "/select/logsql/query", {"query": "*"})

    assert _log_lines(response) == {"shop line"}


@pytest.mark.parametrize(
    "arg, value",
    [
        ("extra_filters", "*"),
        ("extra_filters[]", 'namespace:"other-web"'),
        ("extra_stream_filters", '{namespace="other-web"}'),
        ("extra_stream_filters[]", '{namespace="other-web"}'),
    ],
)
def test_a_client_logs_filter_cannot_widen_the_scope(stack, arg, value):
    response = _get(stack, "/select/logsql/query", {"query": "*", arg: value})

    assert _log_lines(response) <= {"shop line"}


def test_a_forged_logs_tenant_is_overridden(stack):
    response = _get(
        stack,
        "/select/logsql/query",
        {"query": "*"},
        headers={"AccountID": "5"},
    )

    assert "tenant 5" not in _log_lines(response)


def test_the_projects_own_trace_is_found(stack):
    response = _get(stack, f"/select/jaeger/api/traces/{SHOP_TRACE}")

    assert response.json()["data"][0]["traceID"] == SHOP_TRACE


def test_another_projects_trace_id_is_not_found(stack):
    response = _get(stack, f"/select/jaeger/api/traces/{OTHER_TRACE}")

    assert not (response.json().get("data") or [])


def test_a_forged_trace_tenant_header_is_overridden(stack):
    response = _get(
        stack,
        f"/select/jaeger/api/traces/{OTHER_TRACE}",
        headers={"AccountID": "8", "ProjectID": "0"},
    )

    assert not (response.json().get("data") or [])


def test_services_list_only_the_projects_own(stack):
    response = _get(stack, "/select/jaeger/api/services")

    assert response.json()["data"] == ["shop-api"]


@pytest.mark.parametrize(
    "path",
    [
        "/select/tenant_ids",
        "/delete/run_task",
        "/delete/active_tasks",
        "/internal/force_flush",
        "/insert/jsonline",
        "/insert/opentelemetry/v1/traces",
        "/api/v1/import/prometheus",
        "/api/v1/admin/tsdb/delete_series",
        "/api/v1/rules",
        "/select/vmui/",
        "/select/tempo/api/search",
    ],
)
def test_everything_but_the_read_apis_is_unreachable(stack, path):
    response = _get(stack, path)

    assert response.status_code == 400


def test_dot_segments_do_not_escape_an_allowed_path(stack):
    url, (user, password), _ = stack
    connection = http.client.HTTPConnection(url.removeprefix("http://"))
    token = httpx.BasicAuth(user, password)._auth_header

    connection.request(
        "GET",
        "/select/jaeger/api/traces/../../../tenant_ids",
        headers={"Authorization": token},
    )

    assert connection.getresponse().status == 400


def test_another_projects_password_is_refused(stack):
    response = _get(
        stack,
        "/api/v1/query",
        {"query": "app_up"},
        auth=("project-other", "not-the-password"),
    )

    assert response.status_code == 401
