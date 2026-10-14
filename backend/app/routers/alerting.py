"""
Alerts tab: a catalogue of ready-made alerts a project or an app switches on,
provisioned in the project's Grafana org. Anything else is written in Grafana.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.models.alerting import AlertSetting
from app.models.deployment import Deployment
from app.routers.finops import is_cnp_admin
from app.services import alerts_grafana as grafana
from app.services.activity_sources import PROJECT_NAME, namespace_projects
from app.services.alerts_catalog import BY_ID, CATALOG, AlertTemplate
from app.services.grafana_service import GRAFANA_BASE_URL
from app.services.keycloak_service import (
    get_current_user,
    require_project_role,
)
from app.services.kube_client import KubeUnavailableError
from app.services.security import alerts as targets_store

router = APIRouter(prefix="/alerting", tags=["Alerting"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


class ParamRead(BaseModel):
    key: str
    label: str
    unit: str
    value: int
    minimum: int
    maximum: int


class AlertRead(BaseModel):
    id: str
    title: str
    description: str
    severity: str
    enabled: bool
    # normal, pending, firing, error, nodata, or None when switched off.
    state: str | None
    params: list[ParamRead]


class AlertCatalogRead(BaseModel):
    items: list[AlertRead]
    can_edit: bool
    # Where notifications go: "app", "project", "platform", or None.
    notifies: str | None
    grafana_rules_url: str | None
    grafana_new_rule_url: str | None


class AlertUpdate(BaseModel):
    enabled: bool
    params: dict[str, int] = {}


async def _role(token: dict, project: str) -> str:
    if not PROJECT_NAME.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name.")
    if is_cnp_admin(token):
        return "admin"
    return await run_in_threadpool(require_project_role, token, project)


def _check_app(db: Session, project: str, app: str | None) -> None:
    if app and not db.scalar(
        select(Deployment.id).where(
            Deployment.project_id == project, Deployment.name == app
        )
    ):
        raise HTTPException(status_code=404, detail="Unknown app.")


def _namespaces(project: str, app: str | None) -> list[str]:
    if app:
        return [f"{project}-{app}"]
    return sorted(ns for ns, p in namespace_projects().items() if p == project)


def _params(
    template: AlertTemplate, stored: dict, update: dict
) -> dict[str, int]:
    values = {**template.defaults(), **stored}
    for p in template.params:
        if p.key in update:
            value = update[p.key]
            if not p.minimum <= value <= p.maximum:
                raise HTTPException(
                    status_code=400,
                    detail=f"{p.label}: between {p.minimum} and {p.maximum}.",
                )
            values[p.key] = value
    return values


def _settings(
    db: Session, project: str, app: str | None
) -> dict[str, AlertSetting]:
    rows = db.scalars(
        select(AlertSetting).where(
            AlertSetting.project == project, AlertSetting.app == (app or "")
        )
    )
    return {r.alert_id: r for r in rows}


async def _notifies(project: str, app: str | None) -> str | None:
    try:
        targets = await targets_store.read_targets(project)
    except Exception:  # pylint: disable=broad-exception-caught
        targets = {}
    if app and targets.get(f"app:{app}"):
        return "app"
    if targets.get(targets_store.PROJECT_KEY):
        return "project"
    return "platform" if targets_store.platform_default() else None


@router.get("", response_model=AlertCatalogRead)
async def list_alerts(
    token: CurrentUser,
    project: str,
    app: str | None = None,
    db: Session = Depends(get_db),
) -> AlertCatalogRead:
    role = await _role(token, project)
    _check_app(db, project, app)
    stored = _settings(db, project, app)
    try:
        org = await grafana.org_id(project)
        states = await grafana.rule_states(org)
    except (
        grafana.GrafanaAlertingError,
        Exception,
    ):  # pylint: disable=broad-exception-caught
        org, states = None, {}

    # A project-wide rule lists the namespaces it watches: add new apps.
    if org and not app and any(s.enabled for s in stored.values()):
        try:
            current = await run_in_threadpool(_namespaces, project, None)
        except KubeUnavailableError:
            current = None
        for setting in stored.values():
            if (
                current
                and setting.enabled
                and setting.namespaces != ",".join(current)
            ):
                template = BY_ID[setting.alert_id]
                await grafana.upsert_rule(
                    project,
                    org,
                    template,
                    None,
                    _params(template, json.loads(setting.params), {}),
                    current,
                )
                setting.namespaces = ",".join(current)
        db.commit()

    items = []
    for template in CATALOG:
        setting = stored.get(template.id)
        values = _params(
            template, json.loads(setting.params) if setting else {}, {}
        )
        enabled = bool(setting and setting.enabled)
        items.append(
            AlertRead(
                id=template.id,
                title=template.title,
                description=template.description,
                severity=template.severity,
                enabled=enabled,
                state=(
                    states.get(grafana.rule_uid(template.id, app), "pending")
                    if enabled
                    else None
                ),
                params=[
                    ParamRead(
                        key=p.key,
                        label=p.label,
                        unit=p.unit,
                        value=values[p.key],
                        minimum=p.minimum,
                        maximum=p.maximum,
                    )
                    for p in template.params
                ],
            )
        )
    return AlertCatalogRead(
        items=items,
        can_edit=role == "admin",
        notifies=await _notifies(project, app),
        grafana_rules_url=(
            f"{GRAFANA_BASE_URL}/alerting/list?orgId={org}" if org else None
        ),
        grafana_new_rule_url=(
            f"{GRAFANA_BASE_URL}/alerting/new/alerting?orgId={org}"
            if org
            else None
        ),
    )


@router.put("/{alert_id}", response_model=AlertRead)
async def update_alert(
    alert_id: str,
    payload: AlertUpdate,
    token: CurrentUser,
    project: str,
    app: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db),
) -> AlertRead:
    """Switch a catalogue alert on or off, or change its thresholds. Admins."""
    if await _role(token, project) != "admin":
        raise HTTPException(status_code=403, detail="Project admins only.")
    _check_app(db, project, app)
    template = BY_ID.get(alert_id)
    if not template:
        raise HTTPException(status_code=404, detail="Unknown alert.")
    setting = _settings(db, project, app).get(alert_id) or AlertSetting(
        project=project, app=app or "", alert_id=alert_id
    )
    values = _params(
        template, json.loads(setting.params or "{}"), payload.params
    )

    try:
        org = await grafana.org_id(project)
        if payload.enabled:
            namespaces = await run_in_threadpool(_namespaces, project, app)
            if not namespaces:
                raise HTTPException(
                    status_code=409, detail="The project has no namespace yet."
                )
            targets = await targets_store.read_targets(project)
            await grafana.sync_notifications(
                project, org, targets, targets_store.platform_default()
            )
            await grafana.upsert_rule(
                project, org, template, app, values, namespaces
            )
            setting.namespaces = ",".join(namespaces)
        else:
            await grafana.delete_rule(org, alert_id, app)
    except grafana.GrafanaAlertingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except KubeUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    setting.enabled = payload.enabled
    setting.params = json.dumps(values)
    setting.updated_by = token.get("preferred_username") or token.get(
        "sub", ""
    )
    db.add(setting)
    db.commit()
    return AlertRead(
        id=template.id,
        title=template.title,
        description=template.description,
        severity=template.severity,
        enabled=setting.enabled,
        state="pending" if setting.enabled else None,
        params=[
            ParamRead(
                key=p.key,
                label=p.label,
                unit=p.unit,
                value=values[p.key],
                minimum=p.minimum,
                maximum=p.maximum,
            )
            for p in template.params
        ],
    )
