"""
Where a project's security alerts go: the platform default, or a Discord
webhook the team sets for the project or for one app.

Webhook URLs are secrets. They are written to Vault and never sent back to
the browser, only a hint to recognise them.
"""

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers.security import (
    CurrentUser,
    _app_deployment,
    _require_admin,
    _require_member,
    _username,
)
from app.services.security import alerts
from app.services.security.model import Audience
from app.services.vault_client import VaultError

router = APIRouter(prefix="/security", tags=["Security"])

Origin = Literal["app", "project", "platform"]


class WebhookRead(BaseModel):
    configured: bool
    hint: str | None


class AlertTargetsRead(BaseModel):
    project: WebhookRead
    app: WebhookRead | None
    platform: WebhookRead
    effective: Origin | None


class AlertTargetUpdate(BaseModel):
    project: str
    app: str | None = None
    webhook_url: str | None = None


class AlertTest(BaseModel):
    project: str
    app: str | None = None


def _webhook(url: str | None) -> WebhookRead:
    return WebhookRead(
        configured=bool(url), hint=alerts.hint(url) if url else None
    )


async def _targets(project: str) -> dict[str, str]:
    try:
        return await alerts.read_targets(project)
    except VaultError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/alerts", response_model=AlertTargetsRead)
async def get_alert_targets(
    token: CurrentUser,
    project: str,
    app: str | None = None,
    db: Session = Depends(get_db),
) -> AlertTargetsRead:
    await _require_member(token, project, "developer")
    if app:
        _app_deployment(db, project, app)
    targets = await _targets(project)
    resolved = alerts.resolve(targets, app, Audience.DEVELOPER)
    return AlertTargetsRead(
        project=_webhook(targets.get(alerts.PROJECT_KEY)),
        app=_webhook(targets.get(f"app:{app}")) if app else None,
        platform=_webhook(alerts.platform_default() or None),
        effective=resolved[1] if resolved else None,
    )


@router.put("/alerts", response_model=AlertTargetsRead)
async def set_alert_target(
    payload: AlertTargetUpdate,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> AlertTargetsRead:
    """Set or remove (webhook_url null) the project's or an app's webhook."""
    await _require_admin(token, payload.project)
    if payload.app:
        _app_deployment(db, payload.project, payload.app)
    url = (payload.webhook_url or "").strip() or None
    if url and not alerts.is_discord_webhook(url):
        raise HTTPException(
            status_code=400,
            detail="Ce n'est pas une URL de webhook Discord "
            "(https://discord.com/api/webhooks/…).",
        )
    try:
        await alerts.write_target(payload.project, payload.app, url)
    except VaultError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return await get_alert_targets(token, payload.project, payload.app, db)


@router.post("/alerts/test", status_code=204)
async def test_alert_target(
    payload: AlertTest,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> None:
    await _require_admin(token, payload.project)
    if payload.app:
        _app_deployment(db, payload.project, payload.app)
    resolved = alerts.resolve(
        await _targets(payload.project), payload.app, Audience.DEVELOPER
    )
    if resolved is None:
        raise HTTPException(status_code=409, detail="Aucun webhook configuré.")
    scope = f"l'app {payload.app}" if payload.app else "le projet"
    try:
        await alerts.post(
            resolved[0],
            [],
            content=(
                f"Test des alertes sécurité pour {scope} "
                f"**{payload.project}**, envoyé par {_username(token)}."
            ),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Discord a refusé le message : {exc}"
        ) from exc
