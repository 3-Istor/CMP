import json
import subprocess
import time
from unittest import mock

import httpx
import pytest

from app.models.observability import TraceTenant
from app.models.project import Project
from app.services.observability import trace_routing, trace_tenants
from tests.obs_stack import (
    Process,
    binary,
    free_port,
    need,
    otlp_trace,
    store,
    wait_for_trace,
)
from tests.test_app_security_data import session_factory  # noqa: F401

# ---- tenants -------------------------------------------------------------


@pytest.fixture
def projects(session_factory):
    def add(*names):
        with session_factory() as db:
            db.add_all(
                Project(project_name=n, owner_username="a") for n in names
            )
            db.commit()

    def remove(name):
        with session_factory() as db:
            db.query(Project).filter(Project.project_name == name).delete()
            db.commit()

    with mock.patch.object(trace_tenants, "SessionLocal", session_factory):
        yield add, remove


def test_each_project_gets_a_stable_nonzero_account(projects):
    add, _ = projects
    add("shop", "iot")

    first = trace_tenants.accounts()
    again = trace_tenants.accounts()

    assert (first, 0 in first.values()) == (again, False)


def test_a_deleted_projects_account_is_never_reused(projects):
    add, remove = projects
    add("shop")
    old = trace_tenants.accounts()["shop"]
    remove("shop")
    trace_tenants.accounts()

    add("shop", "newcomer")
    current = trace_tenants.accounts()

    assert old not in current.values()


def test_a_retired_tenant_stays_on_record(projects, session_factory):
    add, remove = projects
    add("shop")
    trace_tenants.accounts()
    remove("shop")
    trace_tenants.accounts()

    with session_factory() as db:
        row = db.query(TraceTenant).one()
    assert row.retired_at is not None


# ---- routing table --------------------------------------------------------


def _config(accounts):
    return json.loads(trace_routing.render(accounts))


def test_each_project_is_exported_with_its_own_tenant_headers():
    exporter = _config({"shop": 7})["exporters"]["otlp_http/cnp_shop"]

    assert exporter["headers"] == {"AccountID": "7", "ProjectID": "0"}


def test_spans_without_a_known_project_go_to_the_counting_drop():
    connector = _config({"shop": 7})["connectors"]["routing/cnp_projects"]

    assert connector["default_pipelines"] == ["traces/cnp_unknown_project"]


def test_the_table_is_valid_without_any_project():
    table = _config({})["connectors"]["routing/cnp_projects"]["table"]

    assert len(table) == 1


def test_the_rendering_is_stable():
    assert trace_routing.render({"b": 2, "a": 1}) == trace_routing.render(
        {"a": 1, "b": 2}
    )


def _kube(configmap, deployment):
    return {
        "kube_get": mock.Mock(
            side_effect=lambda path: (
                configmap if "/configmaps/" in path else deployment
            )
        ),
        "kube_merge_patch": mock.Mock(),
    }


def _deployment(checksum=None):
    annotations = {trace_routing.CHECKSUM: checksum} if checksum else {}
    return {"spec": {"template": {"metadata": {"annotations": annotations}}}}


def test_a_new_table_rolls_the_gateway_by_patching_its_template():
    calls = _kube({"data": {"routing.yaml": "old"}}, _deployment("old"))

    with mock.patch.multiple(trace_routing, **calls):
        trace_routing.sync({"shop": 7})

    patched = [c.args[1] for c in calls["kube_merge_patch"].call_args_list]
    assert "template" in patched[1]["spec"]


def test_a_missing_configmap_is_reported_never_created():
    calls = _kube(None, _deployment())

    with mock.patch.multiple(trace_routing, **calls):
        with pytest.raises(trace_routing.KubeUnavailableError):
            trace_routing.sync({"shop": 7})

    assert calls["kube_merge_patch"].call_count == 0


def test_an_unchanged_table_writes_nothing():
    wanted = trace_routing.render({"shop": 7})
    digest = trace_routing.hashlib.sha256(wanted.encode()).hexdigest()
    calls = _kube({"data": {"routing.yaml": wanted}}, _deployment(digest))

    with mock.patch.multiple(trace_routing, **calls):
        changed = trace_routing.sync({"shop": 7})

    assert (changed, calls["kube_merge_patch"].call_count) == (False, 0)


# ---- the real collector ---------------------------------------------------

BASE = {
    "receivers": {"otlp": {"protocols": {"http": {"endpoint": None}}}},
    "processors": {"batch": {"timeout": "100ms"}},
    "service": {
        "pipelines": {
            "traces": {
                "receivers": ["otlp"],
                "processors": ["batch"],
                "exporters": ["routing/cnp_projects"],
            }
        },
        "telemetry": {
            "metrics": {
                "level": "detailed",
                "readers": [
                    {
                        "pull": {
                            "exporter": {
                                "prometheus": {
                                    "host": "127.0.0.1",
                                    "port": None,
                                }
                            }
                        }
                    }
                ],
            }
        },
    },
}


def _files(tmp_path, accounts, insert_url, otlp_port, telemetry_port):
    base = json.loads(json.dumps(BASE))
    base["receivers"]["otlp"]["protocols"]["http"][
        "endpoint"
    ] = f"127.0.0.1:{otlp_port}"
    reader = base["service"]["telemetry"]["metrics"]["readers"][0]
    reader["pull"]["exporter"]["prometheus"]["port"] = telemetry_port
    (tmp_path / "base.json").write_text(json.dumps(base))
    with mock.patch.object(
        trace_routing.settings, "OBS_VTRACES_INSERT_URL", insert_url
    ):
        (tmp_path / "routing.json").write_text(trace_routing.render(accounts))
    return [
        f"--config={tmp_path / 'base.json'}",
        f"--config={tmp_path / 'routing.json'}",
    ]


def test_otelcol_validates_the_merged_config(tmp_path):
    need("otelcol-contrib")
    files = _files(
        tmp_path, {"shop": 7, "iot-landing": 8}, "http://x/v1", 4318, 8888
    )

    result = subprocess.run(
        [binary("otelcol-contrib"), "validate", *files],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    need("otelcol-contrib", "victoria-traces")
    tmp = tmp_path_factory.mktemp("gateway")
    traces = store("victoria-traces", tmp)
    otlp_port, telemetry_port = free_port(), free_port()
    collector = Process(
        "otelcol-contrib",
        _files(
            tmp,
            {"shop": 7, "other": 8},
            f"{traces.url}/insert/opentelemetry/v1/traces",
            otlp_port,
            telemetry_port,
        ),
    ).wait(f"http://127.0.0.1:{telemetry_port}/metrics")
    sent = {
        "shop": "a" * 32,
        "other": "b" * 32,
        "ghost": "c" * 32,
        None: "d" * 32,
    }
    for project, trace in sent.items():
        httpx.post(
            f"http://127.0.0.1:{otlp_port}/v1/traces",
            headers={"Content-Type": "application/x-protobuf"},
            content=otlp_trace(trace, f"{project}-api", project),
        ).raise_for_status()
    wait_for_trace(traces.url, sent["shop"], 7)
    wait_for_trace(traces.url, sent["other"], 8)
    yield traces.url, sent, f"http://127.0.0.1:{telemetry_port}/metrics"
    collector.stop()
    traces.stop()


def _found(traces_url: str, trace: str, account: int) -> bool:
    body = httpx.get(
        f"{traces_url}/select/jaeger/api/traces/{trace}",
        headers={"AccountID": str(account)},
    ).json()
    return bool(body.get("data"))


def test_a_span_lands_only_in_its_projects_tenant(gateway):
    url, sent, _ = gateway

    assert [_found(url, sent["shop"], a) for a in (7, 8, 0)] == [
        True,
        False,
        False,
    ]


def test_unknown_projects_are_dropped_and_counted(gateway):
    url, sent, metrics = gateway
    time.sleep(1)

    stored = [
        _found(url, sent[p], a) for p in ("ghost", None) for a in (0, 7, 8)
    ]
    counted = [
        line
        for line in httpx.get(metrics).text.splitlines()
        if line.startswith("otelcol_processor_filter_spans_filtered")
        and "cnp_unknown_project" in line
    ]

    assert (any(stored), float(counted[0].rsplit(" ", 1)[1])) == (False, 2.0)
