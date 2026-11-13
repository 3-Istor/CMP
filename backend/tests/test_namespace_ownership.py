import asyncio
from unittest import mock

import pytest

from app.services import kube_client
from app.services import project_registry as registry


def namespace(name: str, project: str | None = None) -> dict:
    labels = {"cnp.3istor.com/project": project} if project else {}
    return {"metadata": {"name": name, "labels": labels}}


@pytest.fixture
def cluster():
    live: dict[str, dict] = {}
    with (
        mock.patch.object(kube_client, "in_cluster", return_value=True),
        mock.patch.object(
            kube_client,
            "kube_get",
            side_effect=lambda path: live.get(path.rsplit("/", 1)[-1]),
        ),
    ):
        yield live


def test_foreign_namespaces_refuses_an_existing_namespace_without_the_project_label(
    cluster,
):
    cluster["envoy-gateway-system"] = namespace("envoy-gateway-system")

    problems = registry.foreign_namespaces("envoy", ["envoy-gateway-system"])

    assert problems == [
        "namespace 'envoy-gateway-system' already exists and does not "
        "belong to project 'envoy'"
    ]


def test_foreign_namespaces_refuses_a_namespace_labelled_for_another_project(
    cluster,
):
    cluster["foo-bar-x"] = namespace("foo-bar-x", "foo-bar")

    problems = registry.foreign_namespaces("foo", ["foo-bar-x"])

    assert len(problems) == 1


def test_foreign_namespaces_accepts_the_project_s_own_namespace_on_a_retry(
    cluster,
):
    cluster["iot-system"] = namespace("iot-system", "iot")

    problems = registry.foreign_namespaces("iot", ["iot-system"])

    assert not problems


def test_foreign_namespaces_accepts_a_namespace_that_does_not_exist(cluster):
    problems = registry.foreign_namespaces("iot", ["iot-new"])

    assert not problems


def test_foreign_namespaces_fails_closed_when_the_cluster_cannot_be_read():
    with (
        mock.patch.object(kube_client, "in_cluster", return_value=True),
        mock.patch.object(
            kube_client,
            "kube_get",
            side_effect=kube_client.KubeUnavailableError("HTTP 403"),
        ),
        pytest.raises(registry.RegistryError),
    ):
        registry.foreign_namespaces("iot", ["iot-new"])


def test_ensure_app_names_free_refuses_an_app_landing_in_an_unlabelled_namespace(
    cluster,
):
    # Arrange: not in the reserved list, but already there and not the project's.
    cluster["acme-tools"] = namespace("acme-tools")

    # Act
    with (
        mock.patch.object(
            registry,
            "list_records",
            mock.AsyncMock(return_value=[{"metadata": {"name": "acme"}}]),
        ),
        pytest.raises(registry.NameCollisionError) as exc,
    ):
        asyncio.run(registry.ensure_app_names_free("acme", "tools", "static"))

    # Assert
    assert "namespace 'acme-tools' already exists" in str(exc.value)
