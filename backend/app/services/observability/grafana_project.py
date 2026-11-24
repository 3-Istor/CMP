"""
What the CMP owns in each project's Grafana org (D17): Terraform creates the
org, the CMP its traces datasource and the dashboards per role, generated
here as JSON. Nobody else writes these objects; the next sync puts back
whatever was changed by hand (members are Editors and can copy them).

The traces datasource reads through vmauth as the project's user, whose
traces route forces the project's tenant: like the metrics and logs ones,
an Editor cannot point it anywhere else. Its credentials are the vmauth
ones, derived, never stored.

The dashboards themselves are built in grafana_dashboards. They read series
that carry the project's namespace, so they pass its vmauth filter; the raw
Envoy, Hubble and OpenCost series do not.
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
from app.services.observability import grafana_dashboards as gd

logger = logging.getLogger(__name__)

LOGS_DATASOURCE_TYPE = "victoriametrics-logs-datasource"
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


def logs_uid(project: str) -> str:
    return f"cnp-logs-{project}"


def logs_datasource(project: str) -> dict:
    return {
        "uid": logs_uid(project),
        "name": f"Logs - {_title_case_project_name(project)}",
        "type": LOGS_DATASOURCE_TYPE,
        "access": "proxy",
        "url": settings.OBS_VMAUTH_PROJECTS_URL,
        "basicAuth": True,
        "basicAuthUser": metrics_isolation.username(project),
        "secureJsonData": {
            "basicAuthPassword": metrics_isolation.password(project)
        },
        "jsonData": {},
        "editable": False,
    }


def _logs_dashboard(project: str) -> dict:
    dashboard = gd.log_explorer_dashboard()
    for variable in dashboard["templating"]["list"]:
        if variable["name"] == gd.LOGS_DS_VAR:
            variable["regex"] = f"/^{logs_uid(project)}$/"
    return dashboard


def desired(project: str) -> dict:
    """Folders, datasources and dashboards, each dashboard with its folder."""
    dashboards = [(folder[0], build()) for folder, build in gd.DASHBOARDS]
    datasources = []
    if settings.OBS_VTRACES_URL:
        datasources.append(traces_datasource(project))
    if settings.OBS_VLOGS_URL:
        datasources.append(logs_datasource(project))
        dashboards.append((gd.LOGS_FOLDER[0], _logs_dashboard(project)))
    return {
        "folders": [{"uid": uid, "title": t} for uid, t in gd.FOLDERS],
        "datasources": datasources,
        "dashboards": [("", gd.home_dashboard()), *dashboards],
        "home": gd.HOME_UID,
    }


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
    base = grafana_service.GRAFANA_BASE_URL
    for folder in wanted["folders"]:
        await _ensure_folder(client, headers, folder)
    for datasource in wanted["datasources"]:
        await _upsert_datasource(client, headers, datasource)
    for folder_uid, dashboard in wanted["dashboards"]:
        (
            await client.post(
                f"{base}/api/dashboards/db",
                json={
                    "dashboard": dashboard,
                    "folderUid": folder_uid,
                    "overwrite": True,
                    "message": "Generated by the CMP",
                },
                headers=headers,
            )
        ).raise_for_status()
    (
        await client.put(
            f"{base}/api/org/preferences",
            json={"homeDashboardUID": wanted["home"]},
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
