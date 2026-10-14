import base64
import json
from unittest import mock
from urllib.parse import unquote

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
