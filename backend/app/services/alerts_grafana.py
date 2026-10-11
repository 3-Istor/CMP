"""
Provisions the catalogue alerts as Grafana-managed rules in a project's org.

Rules are created through the provisioning API, so Grafana shows them as
managed by the CMP and nobody edits them by accident. They notify the Discord
webhooks set in the Security tab: the app's when it has one, else the
project's, else the platform's.
"""

import hashlib
import logging
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.alerts_catalog import AlertTemplate, namespace_matcher
from app.services.grafana_service import GRAFANA_BASE_URL, _grafana_org_name

logger = logging.getLogger(__name__)

FOLDER_UID = "cnp-alerts"
RULE_GROUP = "cnp-catalogue"
PROJECT_RECEIVER = "CNP projet"


class GrafanaAlertingError(Exception):
    pass


def rule_uid(template_id: str, app: str | None) -> str:
    """At most 40 characters, Grafana's limit, whatever the app's name."""
    scope = (
        hashlib.sha1((app or "").encode()).hexdigest()[:8]
        if app
        else "project"
    )
    return f"cnp-{template_id}-{scope}"[:40]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=GRAFANA_BASE_URL,
        auth=("admin", settings.GRAFANA_ADMIN_PASSWORD),
        timeout=20,
    )


async def _call(
    client, method: str, path: str, org: int, **kwargs
) -> httpx.Response:
    response = await client.request(
        method, path, headers={"X-Grafana-Org-Id": str(org)}, **kwargs
    )
    if response.status_code >= 400 and response.status_code != 404:
        raise GrafanaAlertingError(
            f"{method} {path}: {response.status_code} {response.text[:200]}"
        )
    return response


async def org_id(project: str) -> int:
    async with _client() as client:
        response = await client.get(
            f"/api/orgs/name/{quote(_grafana_org_name(project))}"
        )
    if response.status_code != 200:
        raise GrafanaAlertingError(f"No Grafana org for project {project}")
    return response.json()["id"]


async def _metrics_datasource(client, org: int) -> str:
    response = await _call(client, "GET", "/api/datasources", org)
    for ds in response.json():
        if ds["type"] == "prometheus":
            return ds["uid"]
    raise GrafanaAlertingError("The project's org has no metrics datasource")


async def sync_notifications(
    project: str, org: int, targets: dict[str, str], platform: str
) -> None:
    """One Discord contact point per webhook, routed by the rule's app label."""
    async with _client() as client:
        folder = await _call(client, "GET", f"/api/folders/{FOLDER_UID}", org)
        if folder.status_code == 404:
            await _call(
                client,
                "POST",
                "/api/folders",
                org,
                json={"uid": FOLDER_UID, "title": "Alertes CNP"},
            )
        existing = {
            cp["name"]: cp
            for cp in (
                await _call(
                    client, "GET", "/api/v1/provisioning/contact-points", org
                )
            ).json()
        }
        wanted = {PROJECT_RECEIVER: targets.get("project") or platform}
        routes = []
        for key, url in targets.items():
            if key.startswith("app:") and url:
                app = key.removeprefix("app:")
                name = f"CNP app {app}"
                wanted[name] = url
                routes.append(
                    {"receiver": name, "object_matchers": [["app", "=", app]]}
                )
        for name, url in wanted.items():
            if not url:
                continue
            body = {
                "name": name,
                "type": "discord",
                "settings": {"url": url, "use_discord_username": False},
                "disableResolveMessage": False,
            }
            if name in existing:
                await _call(
                    client,
                    "PUT",
                    f"/api/v1/provisioning/contact-points/{existing[name]['uid']}",
                    org,
                    json={**body, "uid": existing[name]["uid"]},
                )
            else:
                await _call(
                    client,
                    "POST",
                    "/api/v1/provisioning/contact-points",
                    org,
                    json=body,
                )
        if not wanted[PROJECT_RECEIVER]:
            return
        await _call(
            client,
            "PUT",
            "/api/v1/provisioning/policies",
            org,
            json={
                "receiver": PROJECT_RECEIVER,
                "group_by": ["alertname", "namespace"],
                "routes": routes,
            },
        )


async def upsert_rule(
    project: str,
    org: int,
    template: AlertTemplate,
    app: str | None,
    params: dict[str, int],
    namespaces: list[str],
) -> None:
    expr = template.expr(namespace_matcher(namespaces), params)
    scope = f"app {app}" if app else f"projet {project}"
    async with _client() as client:
        datasource = await _metrics_datasource(client, org)
        uid = rule_uid(template.id, app)
        rule = {
            "uid": uid,
            "title": f"{template.title} · {scope}",
            "folderUID": FOLDER_UID,
            "ruleGroup": RULE_GROUP,
            "condition": "C",
            "for": template.wait,
            "noDataState": "OK",
            "execErrState": "Error",
            "labels": {
                "severity": template.severity,
                "project": project,
                "app": app or "",
                "cnp_alert": template.id,
            },
            "annotations": {"summary": template.description},
            "data": [
                {
                    "refId": "A",
                    "datasourceUid": datasource,
                    "relativeTimeRange": {"from": 900, "to": 0},
                    "model": {"refId": "A", "expr": expr, "instant": True},
                },
                {
                    "refId": "B",
                    "datasourceUid": "__expr__",
                    "model": {
                        "refId": "B",
                        "type": "reduce",
                        "expression": "A",
                        "reducer": "last",
                    },
                },
                {
                    "refId": "C",
                    "datasourceUid": "__expr__",
                    "model": {
                        "refId": "C",
                        "type": "threshold",
                        "expression": "B",
                        "conditions": [
                            {"evaluator": {"type": "gt", "params": [0]}}
                        ],
                    },
                },
            ],
        }
        current = await _call(
            client, "GET", f"/api/v1/provisioning/alert-rules/{uid}", org
        )
        if current.status_code == 200:
            await _call(
                client,
                "PUT",
                f"/api/v1/provisioning/alert-rules/{uid}",
                org,
                json=rule,
            )
        else:
            await _call(
                client,
                "POST",
                "/api/v1/provisioning/alert-rules",
                org,
                json=rule,
            )


async def delete_rule(org: int, template_id: str, app: str | None) -> None:
    async with _client() as client:
        await _call(
            client,
            "DELETE",
            f"/api/v1/provisioning/alert-rules/{rule_uid(template_id, app)}",
            org,
        )


async def rule_states(org: int) -> dict[str, str]:
    """Rule uid -> normal, pending, firing, error or nodata."""
    async with _client() as client:
        response = await _call(
            client, "GET", "/api/prometheus/grafana/api/v1/rules", org
        )
    states: dict[str, str] = {}
    for group in response.json().get("data", {}).get("groups", []):
        for rule in group.get("rules", []):
            uid = rule.get("uid")
            if not uid:
                continue
            if rule.get("health") == "error":
                states[uid] = "error"
            elif rule.get("state") == "inactive":
                states[uid] = "normal"
            else:
                states[uid] = rule.get("state", "normal")
    return states
