"""
The routing table of the OpenTelemetry gateway (D10): each project's spans
go to its own VictoriaTraces tenant.

The gateway sets the resource attribute cnp.project from the pod the span
came from, after deleting whatever the client sent, and hands every project
span to the routing connector rendered here (platform spans go to tenant 0
before it). Its config is the platform's base file merged with this one
(the collector's --config can be given twice); this file replaces the
connector's table. Spans
with no known project are dropped by a filter, whose telemetry counts them
(otelcol_processor_filter_spans_filtered).

A change rolls the gateway Deployment through an annotation on its pod
template: two replicas and maxUnavailable 0 mean no span is refused.
"""

import hashlib
import json
import logging

from app.core.config import settings
from app.services.kube_client import (
    KubeUnavailableError,
    kube_get,
    kube_merge_patch,
)
from app.services.observability.trace_tenants import PROJECT_ID

logger = logging.getLogger(__name__)

CONNECTOR = "routing/cnp_projects"
UNKNOWN = "cnp_unknown_project"
ATTRIBUTE = "cnp.project"
KEY = "routing.yaml"
CHECKSUM = "cnp.3istor.com/routing-checksum"


def _pipeline(project: str) -> str:
    return f"traces/cnp_{project}"


def _exporter(project: str) -> str:
    return f"otlp_http/cnp_{project}"


def _route(condition: str, pipeline: str) -> dict:
    return {
        "context": "resource",
        "condition": condition,
        "pipelines": [pipeline],
    }


def render(accounts: dict[str, int]) -> str:
    projects = sorted(accounts)
    table = [
        # Keeps the table valid when there is no project yet.
        _route(f'attributes["{ATTRIBUTE}"] == ""', f"traces/{UNKNOWN}")
    ] + [
        _route(f'attributes["{ATTRIBUTE}"] == "{p}"', _pipeline(p))
        for p in projects
    ]
    config = {
        "connectors": {
            CONNECTOR: {
                "default_pipelines": [f"traces/{UNKNOWN}"],
                "error_mode": "ignore",
                "table": table,
            }
        },
        "processors": {
            f"filter/{UNKNOWN}": {
                "error_mode": "ignore",
                "traces": {"span": ["true"]},
            }
        },
        "exporters": {
            f"nop/{UNKNOWN}": {},
            **{
                _exporter(p): {
                    "traces_endpoint": settings.OBS_VTRACES_INSERT_URL,
                    "headers": {
                        "AccountID": str(accounts[p]),
                        "ProjectID": str(PROJECT_ID),
                    },
                }
                for p in projects
            },
        },
        "service": {
            "pipelines": {
                f"traces/{UNKNOWN}": {
                    "receivers": [CONNECTOR],
                    "processors": [f"filter/{UNKNOWN}"],
                    "exporters": [f"nop/{UNKNOWN}"],
                },
                **{
                    _pipeline(p): {
                        "receivers": [CONNECTOR],
                        "exporters": [_exporter(p)],
                    }
                    for p in projects
                },
            }
        },
    }
    # JSON is valid YAML for the collector.
    return json.dumps(config, indent=2, sort_keys=True) + "\n"


def _configmap_path() -> str:
    return f"/api/v1/namespaces/{settings.OBS_COLLECTOR_NAMESPACE}/configmaps"


def _deployment_path() -> str:
    return (
        f"/apis/apps/v1/namespaces/{settings.OBS_COLLECTOR_NAMESPACE}"
        f"/deployments/{settings.OBS_COLLECTOR_DEPLOYMENT}"
    )


def sync(accounts: dict[str, int]) -> bool:
    """
    Write the table when it changed and roll the gateway; True when either
    happened.

    Raises:
        KubeUnavailableError: The cluster could not be read or written, or
            the ConfigMap Argo CD creates is missing.
    """
    wanted = render(accounts)
    digest = hashlib.sha256(wanted.encode()).hexdigest()
    path = f"{_configmap_path()}/{settings.OBS_COLLECTOR_ROUTING_CONFIGMAP}"
    # Argo CD creates the ConfigMap and leaves its data to the CMP, which
    # may only get and patch it.
    current = kube_get(path)
    if current is None:
        raise KubeUnavailableError(f"GET {path}: HTTP 404")
    changed = False
    if (current.get("data") or {}).get(KEY) != wanted:
        kube_merge_patch(path, {"data": {KEY: wanted}})
        changed = True
    deployment = kube_get(_deployment_path())
    if deployment is None:
        logger.warning(
            "Trace routing written, but no %s Deployment to roll",
            settings.OBS_COLLECTOR_DEPLOYMENT,
        )
        return changed
    template = deployment["spec"]["template"].get("metadata") or {}
    if (template.get("annotations") or {}).get(CHECKSUM) != digest:
        # A patch of the pod template is a rolling update, never a delete.
        kube_merge_patch(
            _deployment_path(),
            {
                "spec": {
                    "template": {
                        "metadata": {"annotations": {CHECKSUM: digest}}
                    }
                }
            },
        )
        logger.info(
            "Trace routing: %d project(s), gateway rolled", len(accounts)
        )
        changed = True
    return changed
