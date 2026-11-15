"""
What the CMP owns in each project's Grafana org (D17): Terraform creates the
org, the CMP its traces datasource and the dashboards per role, generated
here as JSON. Nobody else writes these objects; the next sync puts back
whatever was changed by hand (members are Editors and can copy them).

The traces datasource reads through vmauth as the project's user, whose
traces route forces the project's tenant: like the metrics and logs ones,
an Editor cannot point it anywhere else. Its credentials are the vmauth
ones, derived, never stored.

The dashboards read the series the SLO rules record (cnp_slo:*, slo:*),
which carry the app's namespace and so pass the project's vmauth filter;
the raw Envoy series live in the gateway's namespace and do not.
"""

import hashlib
import json
import logging

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import Project, ProjectStatus
from app.services import grafana_service, metrics_isolation
from app.services.activity_sources import PROJECT_NAME
from app.services.grafana_service import (
    _get_http_client,
    _get_org_id_by_name,
    _grafana_org_name,
    _title_case_project_name,
)

logger = logging.getLogger(__name__)

FOLDER_UID = "cnp-portal"
FOLDER_TITLE = "CNP"
APP_DASHBOARD_UID = "cnp-app-signals"
PROJECT_DASHBOARD_UID = "cnp-project-overview"
DATASOURCE_VAR = {
    "name": "ds",
    "label": "Metrics",
    "type": "datasource",
    "query": "prometheus",
    "hide": 2,
}
DS = {"type": "prometheus", "uid": "${ds}"}
_applied: dict[str, str] = {}


def traces_uid(project: str) -> str:
    return f"cnp-traces-{project}"


def traces_datasource(project: str) -> dict:
    return {
        "uid": traces_uid(project),
        "name": f"Traces - {_title_case_project_name(project)}",
        "type": "jaeger",
        "access": "proxy",
        "url": f"{settings.OBS_VMAUTH_PROJECTS_URL}/select/jaeger",
        "basicAuth": True,
        "basicAuthUser": metrics_isolation.username(project),
        "secureJsonData": {
            "basicAuthPassword": metrics_isolation.password(project)
        },
        "jsonData": {},
        "editable": False,
    }


def _target(expr: str, legend: str = "", instant: bool = False) -> dict:
    return {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legend,
        "refId": "A",
        "instant": instant,
        "range": not instant,
    }


def _panel(  # pylint: disable=too-many-arguments
    panel_id: int,
    title: str,
    expr: str,
    grid: tuple[int, int, int, int],
    unit: str = "short",
    kind: str = "timeseries",
    legend: str = "",
) -> dict:
    x, y, w, h = grid
    return {
        "id": panel_id,
        "type": kind,
        "title": title,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": [_target(expr, legend, instant=kind != "timeseries")],
    }


def _variable(name: str, label: str, query: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "query": {"query": query, "refId": "var"},
        "definition": query,
        "refresh": 2,
        "sort": 1,
    }


def _dashboard(uid: str, title: str, panels: list, variables: list) -> dict:
    return {
        "uid": uid,
        "title": title,
        "tags": ["cnp", "generated"],
        "editable": False,
        "schemaVersion": 39,
        "time": {"from": "now-24h", "to": "now"},
        "refresh": "1m",
        "templating": {"list": [DATASOURCE_VAR, *variables]},
        "panels": panels,
    }


def app_dashboard() -> dict:
    """For developers: one app's golden signals, budget and resources."""
    app = 'cnp_app="$app"'
    avail = f'{app}, cnp_slo="availability"'
    latency = f'{app}, cnp_slo="latency"'
    pods = 'namespace="$namespace", container!="", container!="POD"'
    panels = [
        _panel(
            1,
            "Requests per second",
            f"sum(cnp_slo:requests:rate5m{{{avail}}})",
            (0, 0, 8, 8),
            "reqps",
        ),
        _panel(
            2,
            "5xx share",
            f"sum(cnp_slo:errors:rate5m{{{avail}}})"
            f" / sum(cnp_slo:requests:rate5m{{{avail}}})",
            (8, 0, 8, 8),
            "percentunit",
        ),
        _panel(
            3,
            "Share slower than 500 ms",
            f"sum(cnp_slo:errors:rate5m{{{latency}}})"
            f" / sum(cnp_slo:requests:rate5m{{{latency}}})",
            (16, 0, 8, 8),
            "percentunit",
        ),
        _panel(
            4,
            "Error budget left",
            f"slo:period_error_budget_remaining:ratio{{{app}}}",
            (0, 8, 8, 6),
            "percentunit",
            "stat",
            "{{cnp_slo}}",
        ),
        _panel(
            5,
            "Burn rate over 1 h",
            f"slo:sli_error:ratio_rate1h{{{app}}}"
            f" / on (cnp_project, cnp_app, cnp_slo) slo:error_budget:ratio{{{app}}}",
            (8, 8, 8, 6),
            "short",
            "stat",
            "{{cnp_slo}}",
        ),
        _panel(
            6,
            "Restarts over 1 h",
            "sum(increase(kube_pod_container_status_restarts_total"
            '{namespace="$namespace"}[1h]))',
            (16, 8, 8, 6),
            "short",
            "stat",
        ),
        _panel(
            7,
            "CPU (cores)",
            "sum by (pod) (rate(container_cpu_usage_seconds_total"
            f"{{{pods}}}[5m]))",
            (0, 14, 12, 8),
            "short",
            legend="{{pod}}",
        ),
        _panel(
            8,
            "Memory",
            f"sum by (pod) (container_memory_working_set_bytes{{{pods}}})",
            (12, 14, 12, 8),
            "bytes",
            legend="{{pod}}",
        ),
    ]
    return _dashboard(
        APP_DASHBOARD_UID,
        "App - golden signals",
        panels,
        [
            _variable(
                "app", "App", "label_values(cnp_slo:requests:rate5m, cnp_app)"
            ),
            _variable(
                "namespace",
                "Namespace",
                'label_values(cnp_slo:requests:rate5m{cnp_app="$app"},'
                " namespace)",
            ),
        ],
    )


def project_dashboard() -> dict:
    """For project owners: every app's traffic, errors and budget."""
    avail = 'cnp_slo="availability"'
    panels = [
        _panel(
            1,
            "Requests per second by app",
            f"sum by (cnp_app) (cnp_slo:requests:rate5m{{{avail}}})",
            (0, 0, 12, 8),
            "reqps",
            legend="{{cnp_app}}",
        ),
        _panel(
            2,
            "5xx share by app",
            f"sum by (cnp_app) (cnp_slo:errors:rate5m{{{avail}}})"
            f" / sum by (cnp_app) (cnp_slo:requests:rate5m{{{avail}}})",
            (12, 0, 12, 8),
            "percentunit",
            legend="{{cnp_app}}",
        ),
        _panel(
            3,
            "Availability budget left",
            f"slo:period_error_budget_remaining:ratio{{{avail}}}",
            (0, 8, 12, 8),
            "percentunit",
            "bargauge",
            "{{cnp_app}}",
        ),
        _panel(
            4,
            "Latency budget left",
            'slo:period_error_budget_remaining:ratio{cnp_slo="latency"}',
            (12, 8, 12, 8),
            "percentunit",
            "bargauge",
            "{{cnp_app}}",
        ),
        _panel(
            5,
            "CPU by namespace (cores)",
            "sum by (namespace) (rate(container_cpu_usage_seconds_total"
            '{container!="", container!="POD"}[5m]))',
            (0, 16, 24, 8),
            "short",
            legend="{{namespace}}",
        ),
    ]
    return _dashboard(PROJECT_DASHBOARD_UID, "Project - overview", panels, [])


def desired(project: str) -> dict:
    wanted = {
        "folder": {"uid": FOLDER_UID, "title": FOLDER_TITLE},
        "dashboards": [app_dashboard(), project_dashboard()],
    }
    if settings.OBS_VTRACES_URL:
        wanted["datasource"] = traces_datasource(project)
    return wanted


def _checksum(wanted: dict) -> str:
    return hashlib.sha256(
        json.dumps(wanted, sort_keys=True).encode()
    ).hexdigest()


async def _upsert_datasource(
    client: httpx.AsyncClient, headers: dict, body: dict
) -> None:
    base = f"{grafana_service.GRAFANA_BASE_URL}/api/datasources"
    found = await client.get(f"{base}/uid/{body['uid']}", headers=headers)
    if found.status_code == 404:
        response = await client.post(base, json=body, headers=headers)
    else:
        found.raise_for_status()
        response = await client.put(
            f"{base}/uid/{body['uid']}", json=body, headers=headers
        )
    response.raise_for_status()


async def _ensure_folder(
    client: httpx.AsyncClient, headers: dict, folder: dict
) -> None:
    base = f"{grafana_service.GRAFANA_BASE_URL}/api/folders"
    found = await client.get(f"{base}/{folder['uid']}", headers=headers)
    if found.status_code == 404:
        (
            await client.post(base, json=folder, headers=headers)
        ).raise_for_status()
    else:
        found.raise_for_status()


async def sync_project(project: str) -> bool:
    """Write the project's objects when they changed; True when it did."""
    wanted = desired(project)
    checksum = _checksum(wanted)
    if _applied.get(project) == checksum:
        return False
    org = await _get_org_id_by_name(_grafana_org_name(project))
    if org is None:
        return False
    headers = {"X-Grafana-Org-Id": str(org)}
    client = _get_http_client()
    await _ensure_folder(client, headers, wanted["folder"])
    if "datasource" in wanted:
        await _upsert_datasource(client, headers, wanted["datasource"])
    for dashboard in wanted["dashboards"]:
        (
            await client.post(
                f"{grafana_service.GRAFANA_BASE_URL}/api/dashboards/db",
                json={
                    "dashboard": dashboard,
                    "folderUid": FOLDER_UID,
                    "overwrite": True,
                    "message": "Generated by the CMP",
                },
                headers=headers,
            )
        ).raise_for_status()
    _applied[project] = checksum
    return True


def _projects() -> list[str]:
    with SessionLocal() as db:
        return [
            p
            for (p,) in db.query(Project.project_name).filter(
                Project.status == ProjectStatus.ACTIVE
            )
            if PROJECT_NAME.match(p)
        ]


async def sync_quietly() -> None:
    """Every active project, behind its flag; never raises."""
    if not (
        settings.OBS_GRAFANA_PROVISIONING_ENABLED
        and settings.GRAFANA_ADMIN_PASSWORD
        and settings.METRICS_PROJECT_KEY
    ):
        return
    try:
        projects = _projects()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Grafana provisioning skipped: %s", exc)
        return
    for project in projects:
        try:
            if await sync_project(project):
                logger.info("Grafana objects of %s written", project)
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.warning(
                "Grafana objects of %s not written: %s", project, exc
            )
