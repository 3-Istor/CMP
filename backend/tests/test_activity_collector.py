import asyncio
from unittest import mock

from app.services import activity_collector


def test_collect_once_skips_cluster_reads_when_running_outside_kubernetes():
    namespace_projects = mock.Mock(side_effect=AssertionError("kube read"))
    deployment_events = mock.Mock(side_effect=AssertionError("kube read"))
    collect_source = mock.AsyncMock(return_value=0)

    with (
        mock.patch.object(
            activity_collector, "in_cluster", return_value=False
        ),
        mock.patch.object(
            activity_collector.src, "namespace_projects", namespace_projects
        ),
        mock.patch.object(
            activity_collector.src, "deployment_events", deployment_events
        ),
        mock.patch.object(
            activity_collector, "collect_source", collect_source
        ),
    ):
        asyncio.run(activity_collector.collect_once())

    collect_source.assert_awaited()
    namespace_projects.assert_not_called()
    deployment_events.assert_not_called()


def test_collect_once_reads_cluster_namespaces_inside_kubernetes():
    namespace_projects = mock.Mock(return_value={"demo-web": "demo"})
    collect_source = mock.AsyncMock(return_value=0)

    with (
        mock.patch.object(activity_collector, "in_cluster", return_value=True),
        mock.patch.object(
            activity_collector.src, "namespace_projects", namespace_projects
        ),
        mock.patch.object(
            activity_collector.src, "deployment_events", return_value=[]
        ),
        mock.patch.object(
            activity_collector, "collect_source", collect_source
        ),
        mock.patch.object(activity_collector, "store", return_value=0),
        mock.patch.object(
            activity_collector.settings, "METRICS_PROJECT_KEY", ""
        ),
    ):
        asyncio.run(activity_collector.collect_once())

    collect_source.assert_awaited_with(mock.ANY, {"demo-web": "demo"})
