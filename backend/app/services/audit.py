"""
Journal of the changes requested through the CMP.

A middleware records every mutating API call once it has been answered, so a
route cannot forget it. Routes that know more than the request shows (the
values a config change replaced, say) add it with ``note``.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.audit import ActivityRecord, AuditEvent
from app.models.deployment import Deployment
from app.services.keycloak_service import decode_platform_token

logger = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

ACTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/projects/"): "project.create",
    ("DELETE", "/api/projects/{project_name}"): "project.delete",
    ("POST", "/api/projects/{project_name}/members"): "project.member.add",
    (
        "DELETE",
        "/api/projects/{project_name}/members/{username}",
    ): "project.member.remove",
    ("POST", "/api/deployments/"): "app.create",
    ("DELETE", "/api/deployments/{deployment_id}"): "app.delete",
    ("PATCH", "/api/deployments/{deployment_id}/config"): "app.config.update",
    (
        "PUT",
        "/api/deployments/{deployment_id}/security-data",
    ): "app.exposure.update",
    ("POST", "/api/security/scans"): "security.scan.request",
    (
        "POST",
        "/api/security/findings/{fingerprint}/exception",
    ): "security.exception.request",
    (
        "POST",
        "/api/security/exceptions/{exception_id}/approve",
    ): "security.exception.approve",
    (
        "DELETE",
        "/api/security/exceptions/{exception_id}",
    ): "security.exception.revoke",
    ("PUT", "/api/security/settings"): "security.settings.update",
    ("PUT", "/api/security/policy"): "security.policy.update",
    ("PUT", "/api/security/alerts"): "security.alerts.update",
    ("POST", "/api/security/alerts/test"): "security.alerts.test",
    ("POST", "/api/security/backups"): "database.backup",
    ("POST", "/api/security/restores"): "database.restore",
    ("PUT", "/api/finops/budgets/{project_name}"): "finops.budget.update",
    (
        "POST",
        "/api/finops/recommendations/{rec_id}/apply",
    ): "finops.recommendation.apply",
    (
        "POST",
        "/api/finops/recommendations/{rec_id}/ignore",
    ): "finops.recommendation.ignore",
    (
        "POST",
        "/api/finops/recommendations/{rec_id}/notify",
    ): "finops.recommendation.notify",
    (
        "POST",
        "/api/deployments/{deployment_id}/release/promote",
    ): "release.promote",
    (
        "POST",
        "/api/deployments/{deployment_id}/release/skip",
    ): "release.skip",
    (
        "POST",
        "/api/deployments/{deployment_id}/release/abort",
    ): "release.abort",
    (
        "POST",
        "/api/deployments/{deployment_id}/release/retry",
    ): "release.retry",
    (
        "PUT",
        "/api/deployments/{deployment_id}/release/settings",
    ): "release.settings.update",
    (
        "POST",
        "/api/deployments/{deployment_id}/release/traffic",
    ): "release.traffic.start",
    (
        "DELETE",
        "/api/deployments/{deployment_id}/release/traffic",
    ): "release.traffic.stop",
    ("PUT", "/api/alerting/{alert_id}"): "alerting.update",
    ("POST", "/api/catalog/sync"): "catalog.sync",
    ("POST", "/api/account/picture"): "account.picture.update",
    (
        "POST",
        "/api/agents/requests/{request_id}/approve",
    ): "agent.connect",
    ("DELETE", "/api/agents/grants/{grant_id}"): "agent.disconnect",
    (
        "POST",
        "/api/agents/confirmations/{confirmation_id}/{decision}",
    ): "agent.confirmation.decide",
    (
        "PUT",
        "/api/projects/{project_name}/agent-settings",
    ): "project.agents.update",
    (
        "POST",
        "/api/account/github-installation",
    ): "account.github.link",
    ("POST", "/api/clusters/{cloud}/up"): "cluster.up",
    ("POST", "/api/clusters/{cloud}/down"): "cluster.down",
}

# Matched on the raw path rather than on the route FastAPI selected: since
# FastAPI 0.13x the selected route only knows its path inside its router,
# without the /api/<prefix> the table is keyed on.
_ACTION_PATTERNS = [
    (
        method,
        re.compile(
            "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template) + "$"
        ),
        action,
    )
    for (method, template), action in ACTIONS.items()
]


def match_action(method: str, path: str) -> tuple[str | None, dict]:
    for pattern_method, pattern, action in _ACTION_PATTERNS:
        if pattern_method != method:
            continue
        found = pattern.match(path)
        if found:
            return action, found.groupdict()
    return None, {}


# A change worth a second look, shown with a badge in the activity feed.
NOTABLE = {
    "cluster.up",
    "cluster.down",
    "project.delete",
    "project.member.add",
    "project.member.remove",
    "app.delete",
    "app.exposure.update",
    "security.exception.approve",
    "security.policy.update",
    "database.restore",
    "release.skip",
    "release.abort",
    "release.settings.update",
    "agent.connect",
    "project.agents.update",
}

# Body fields copied into the journal. Anything else, config values in
# particular, may be a secret and is only listed by name.
_SAFE_FIELDS = {
    "project",
    "project_id",
    "project_name",
    "app",
    "name",
    "username",
    "role",
    "exposure",
    "reason",
    "mode",
    "template_id",
    "target",
    "enabled",
    "keep_after_delete",
    "retention_policy",
    "amount",
    "monthly_budget",
}

_SECRET_HINTS = ("password", "secret", "token", "key", "credential")


def note(request: Request, **fields: Any) -> None:
    """Attach details only the route knows to the journal entry."""
    request.state.audit = {**getattr(request.state, "audit", {}), **fields}


def masked(path: str, value: Any) -> Any:
    if any(hint in path.lower() for hint in _SECRET_HINTS):
        return "***"
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    return value


def changes(before: Any, patch: Any, prefix: str = "") -> list[dict]:
    """The leaves a deep merge of ``patch`` into ``before`` would change."""
    out: list[dict] = []
    if isinstance(patch, dict):
        old = before if isinstance(before, dict) else {}
        for key, value in patch.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(changes(old.get(key), value, path))
        return out
    if before != patch:
        out.append(
            {
                "path": prefix,
                "before": masked(prefix, before),
                "after": masked(prefix, patch),
            }
        )
    return out


def _body_fields(raw: bytes) -> dict:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    fields: dict[str, Any] = {}
    for key, value in body.items():
        if key in _SAFE_FIELDS and not isinstance(value, (dict, list)):
            fields[key] = value
        elif key == "backup" and isinstance(value, dict):
            fields["backup"] = {
                k: v for k, v in value.items() if k in _SAFE_FIELDS
            }
    others = sorted(k for k in body if k not in fields and k != "_sha")
    if others:
        fields["keys"] = others
    return fields


def _actor(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return "anonymous"
    try:
        claims = decode_platform_token(header[7:])
    except HTTPException:
        return "anonymous"
    return claims.get("preferred_username") or claims.get("sub") or "unknown"


def _assistant(request: Request) -> str | None:
    """The AI assistant behind the call, None when a person made it."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        claims = decode_platform_token(header[7:])
    except HTTPException:
        return None
    return (claims.get("cnp_agent") or {}).get("client")


def record_agent_event(
    claims: dict,
    action: str,
    project: str | None,
    app: str | None,
    target: str,
    succeeded: bool,
    details: dict,
) -> None:
    """
    Journal an assistant action that does not go through a REST endpoint,
    such as a commit or a release tag pushed straight to the app repository.
    """
    _store(
        AuditEvent(
            actor=claims.get("preferred_username") or claims.get("sub", ""),
            action=action,
            project=project,
            app=app,
            target=target,
            outcome="success" if succeeded else "failure",
            status_code=200 if succeeded else 400,
            source_ip="agent",
            details=json.dumps(
                {
                    **details,
                    "via": (claims.get("cnp_agent") or {}).get("client"),
                },
                default=str,
            ),
        )
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _deployment(deployment_id: Any) -> tuple[str | None, str | None]:
    try:
        key = int(deployment_id)
    except (TypeError, ValueError):
        return None, None
    with SessionLocal() as db:
        row = db.get(Deployment, key)
        return (row.project_id, row.name) if row else (None, None)


def _store(event: AuditEvent) -> None:
    with SessionLocal() as db:
        db.add(event)
        db.commit()


class AuditMiddleware:
    """
    Pure ASGI on purpose: Starlette's BaseHTTPMiddleware hides client
    disconnects from streaming endpoints, which left every closed SSE stream
    polling the cluster forever.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http"
            or scope["method"] not in _MUTATING
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        status_code = 500

        async def capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, replay, capture)

        request = Request(scope)
        action, params = match_action(scope["method"], scope["path"])
        # 401s are anonymous noise; a dry run changes nothing.
        if (
            action is None
            or status_code == 401
            or request.query_params.get("dry_run") == "true"
        ):
            return

        try:
            await _record(request, action, params, raw, status_code)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # A journal failure must never turn a done change into an error.
            logger.error("Audit journal write failed: %s", exc, exc_info=True)


async def _record(
    request: Request,
    action: str,
    params: dict,
    raw: bytes,
    status_code: int,
) -> None:
    fields = _body_fields(raw)
    noted = dict(getattr(request.state, "audit", {}))

    project = (
        noted.pop("project", None)
        or params.get("project_name")
        or fields.get("project")
        or fields.get("project_id")
        or fields.get("project_name")
        or request.query_params.get("project")
    )
    app = noted.pop("app", None) or fields.get("app")
    if "deployment_id" in params:
        dep_project, dep_name = await run_in_threadpool(
            _deployment, params["deployment_id"]
        )
        project = project or dep_project
        app = app or dep_name
    if action == "app.create":
        app = app or fields.get("name")

    target = next(
        (
            str(params[k])
            for k in (
                "username",
                "exception_id",
                "fingerprint",
                "rec_id",
                "alert_id",
            )
            if k in params
        ),
        app or project or "",
    )

    via = await run_in_threadpool(_assistant, request)
    if via:
        noted["via"] = via

    event = AuditEvent(
        actor=await run_in_threadpool(_actor, request),
        action=action,
        project=project,
        app=app,
        target=target,
        outcome="success" if status_code < 400 else "failure",
        status_code=status_code,
        source_ip=_client_ip(request),
        details=json.dumps({**fields, **noted}, default=str),
    )
    await run_in_threadpool(_store, event)


def purge_expired() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.AUDIT_RETENTION_DAYS
    )
    with SessionLocal() as db:
        result = db.execute(
            delete(AuditEvent).where(AuditEvent.created_at < cutoff)
        )
        collected = db.execute(
            delete(ActivityRecord).where(ActivityRecord.time < cutoff)
        )
        db.commit()
        return (result.rowcount or 0) + (collected.rowcount or 0)


async def audit_retention_loop() -> None:
    while True:
        try:
            removed = await run_in_threadpool(purge_expired)
            if removed:
                logger.info(
                    "Audit journal: %d expired events removed", removed
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Audit retention error: %s", exc, exc_info=True)
        await asyncio.sleep(6 * 3600)
