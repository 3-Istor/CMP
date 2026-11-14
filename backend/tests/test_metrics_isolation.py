import base64
import json
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.services import metrics_isolation as mi

NS = {"shop-system": "shop", "shop-web": "shop", "shopping-api": "shopping"}


@pytest.fixture(autouse=True)
def key():
    with mock.patch.object(mi.settings, "METRICS_PROJECT_KEY", "k" * 64):
        yield


def users():
    return {u["username"]: u for u in json.loads(mi.render(NS))["users"]}


def test_each_project_is_filtered_to_its_exact_namespaces():
    prefix = unquote(users()["project-shop"]["url_prefix"])

    assert prefix.endswith(
        'extra_filters[]={namespace=~"shop-system|shop-web"}'
        '&extra_filters={namespace=~"shop-system|shop-web"}'
    )


def test_both_filter_arg_spellings_are_pinned_so_a_client_cannot_add_one():
    query = parse_qs(urlsplit(users()["project-shop"]["url_prefix"]).query)

    assert (query["extra_filters[]"], query["extra_filters"]) == (
        ['{namespace=~"shop-system|shop-web"}'],
        ['{namespace=~"shop-system|shop-web"}'],
    )


def test_a_project_name_prefix_does_not_leak_into_another_project():
    prefix = unquote(users()["project-shopping"]["url_prefix"])

    assert prefix.endswith('{namespace=~"shopping-api"}')


def test_nobody_gets_in_without_credentials():
    assert "unauthorized_user" not in json.loads(mi.render(NS))


def test_passwords_differ_per_project_and_are_stable():
    assert (
        mi.password("shop") == mi.password("shop"),
        mi.password("shop") != mi.password("shopping"),
    ) == (True, True)


def test_sync_writes_only_when_the_config_changed():
    rendered = base64.b64encode(mi.render(NS).encode()).decode()
    with (
        mock.patch.object(
            mi, "kube_get", return_value={"data": {"auth.yml": rendered}}
        ),
        mock.patch.object(mi, "kube_merge_patch") as patch,
    ):
        changed = mi.sync(NS)

    assert (changed, patch.call_count) == (False, 0)


def test_a_new_namespace_rewrites_the_config():
    with (
        mock.patch.object(
            mi, "kube_get", return_value={"data": {"auth.yml": ""}}
        ),
        mock.patch.object(mi, "kube_merge_patch") as patch,
    ):
        mi.sync(NS)

    written = base64.b64decode(
        patch.call_args.args[1]["data"]["auth.yml"]
    ).decode()
    assert "project-shop" in written


def test_render_refuses_empty_namespaces():
    with pytest.raises(ValueError):
        mi.render({"": "shop"})


def test_without_new_stores_the_config_is_the_legacy_one_byte_for_byte():
    legacy = {
        "users": [
            {
                "username": mi.username(p),
                "password": mi.password(p),
                "url_prefix": mi.url_prefix(
                    sorted(n for n, q in NS.items() if q == p)
                ),
            }
            for p in ("shop", "shopping")
        ]
    }

    assert mi.render(NS, {"shop": 7}) == json.dumps(legacy, indent=2) + "\n"


@pytest.fixture
def stores():
    with (
        mock.patch.object(mi.settings, "OBS_VMSINGLE_URL", "http://vms:8428"),
        mock.patch.object(mi.settings, "OBS_VLOGS_URL", "http://vl:9428"),
        mock.patch.object(mi.settings, "OBS_VTRACES_URL", "http://vt:10428"),
    ):
        yield


def _entries(project, accounts=None):
    user = {
        u["username"]: u
        for u in json.loads(mi.render(NS, accounts or {"shop": 7}))["users"]
    }[f"project-{project}"]
    return user


def test_with_stores_a_user_has_no_catch_all_route(stores):
    user = _entries("shop")

    assert ("url_prefix" in user, len(user["url_map"])) == (False, 3)


def test_traces_force_the_projects_own_tenant(stores):
    traces = _entries("shop")["url_map"][2]

    assert traces["headers"] == ["AccountID: 7", "ProjectID: 0"]


def test_a_project_without_a_tenant_gets_no_traces_route(stores):
    assert len(_entries("shopping")["url_map"]) == 2


def test_logs_pin_every_filter_spelling_and_the_single_tenant(stores):
    logs = _entries("shop")["url_map"][1]
    query = parse_qs(urlsplit(logs["url_prefix"]).query)

    assert (
        query["extra_filters"],
        query["extra_filters[]"],
        query["extra_stream_filters"],
        query["extra_stream_filters[]"],
        logs["headers"],
    ) == (
        ['namespace:in("shop-system","shop-web")'],
        ['namespace:in("shop-system","shop-web")'],
        ['{namespace in ("shop-system","shop-web")}'],
        ['{namespace in ("shop-system","shop-web")}'],
        ["AccountID: 0", "ProjectID: 0"],
    )


def test_the_logs_filter_is_an_exact_namespace_filter_never_project(stores):
    logs = _entries("shop")["url_map"][1]
    query = parse_qs(urlsplit(logs["url_prefix"]).query)

    assert not any("project" in v for values in query.values() for v in values)


def test_metrics_go_to_vmsingle_with_both_filter_spellings(stores):
    metrics = _entries("shop")["url_map"][0]
    query = parse_qs(urlsplit(metrics["url_prefix"]).query)

    assert (
        metrics["url_prefix"].startswith("http://vms:8428?"),
        set(query),
    ) == (True, {"extra_filters", "extra_filters[]"})


def test_no_route_reaches_admin_write_or_tenant_paths(stores):
    paths = [p for e in _entries("shop")["url_map"] for p in e["src_paths"]]

    assert not any(
        word in p
        for p in paths
        for word in (
            "tenant_ids",
            "delete",
            "internal",
            "insert",
            "import",
            "write",
            "admin",
        )
    )
