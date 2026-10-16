"""
Demo traffic for the Release tab.

A canary is judged on the requests its version serves; a demo app has nobody
using it, so the analysis stays inconclusive. The CMP plays the visitors.

It goes through the app's real route, SSO included: a technical user of the
project realm logs in like a browser, and each request then comes from a
fresh visitor (no cnp-track cookie), so Envoy splits by the canary weights.
The cnp-track cookie of the answer says which version served it.
"""

import asyncio
import json
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TRAFFIC_USERNAME = "cnp-traffic"
MAX_RPS = 20
MAX_MINUTES = 15
MAX_SESSIONS = 3
RECENT_EVENTS = 200
REQUEST_TIMEOUT = 10.0

_LOGIN_ACTION = re.compile(r'"loginAction":\s*"([^"]+)"')


class TrafficError(Exception):
    """The demo traffic could not start; the message is shown to the user."""


@dataclass
class TrafficSession:  # pylint: disable=too-many-instance-attributes
    deployment_id: int
    app_url: str
    rps: int
    until: float
    started_by: str
    counts: dict[str, int] = field(
        default_factory=lambda: {"stable": 0, "canary": 0, "error": 0}
    )
    events: deque = field(default_factory=lambda: deque(maxlen=RECENT_EVENTS))
    sequence: int = 0
    stopped: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": not self.stopped,
            "rps": self.rps,
            "remainingSeconds": max(int(self.until - time.time()), 0),
            "startedBy": self.started_by,
            "counts": dict(self.counts),
        }

    def record(self, version: str, status: int, millis: int) -> None:
        self.counts[version] = self.counts.get(version, 0) + 1
        self.sequence += 1
        self.events.append(
            {
                "seq": self.sequence,
                "version": version,
                "status": status,
                "ms": millis,
            }
        )
        self.changed.set()
        self.changed = asyncio.Event()


_sessions: dict[int, TrafficSession] = {}


def session_for(deployment_id: int) -> TrafficSession | None:
    return _sessions.get(deployment_id)


async def _admin_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        f"{settings.KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": settings.KEYCLOAK_ADMIN_USERNAME,
            "password": settings.KEYCLOAK_ADMIN_PASSWORD,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def _prepare_user(client: httpx.AsyncClient, realm: str) -> str:
    """Ensure the realm's traffic user exists and give it a fresh password.

    The password is never stored: each start resets it and logs in at once.
    """
    headers = {"Authorization": f"Bearer {await _admin_token(client)}"}
    base = f"{settings.KEYCLOAK_URL}/admin/realms/{realm}"
    user = {
        "username": TRAFFIC_USERNAME,
        "enabled": True,
        "emailVerified": True,
        "email": f"{TRAFFIC_USERNAME}@{realm}.cnp.invalid",
        "firstName": "CNP",
        "lastName": "Demo traffic",
        "requiredActions": [],
    }
    found = await client.get(
        f"{base}/users",
        params={"username": TRAFFIC_USERNAME, "exact": "true"},
        headers=headers,
    )
    found.raise_for_status()
    if found.json():
        user_id = found.json()[0]["id"]
        (
            await client.put(
                f"{base}/users/{user_id}", json=user, headers=headers
            )
        ).raise_for_status()
    else:
        created = await client.post(
            f"{base}/users", json=user, headers=headers
        )
        created.raise_for_status()
        user_id = created.headers["location"].rsplit("/", 1)[1]

    # Apps may admit only project members (ingress.allowedGroups).
    groups = await client.get(
        f"{base}/groups", params={"search": "project-members"}, headers=headers
    )
    for group in groups.json() if groups.is_success else []:
        if group.get("name") == "project-members":
            await client.put(
                f"{base}/users/{user_id}/groups/{group['id']}", headers=headers
            )

    password = secrets.token_urlsafe(24)
    (
        await client.put(
            f"{base}/users/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
            headers=headers,
        )
    ).raise_for_status()
    return password


async def _follow(
    client: httpx.AsyncClient, response: httpx.Response
) -> httpx.Response:
    for _ in range(10):
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        response = await client.get(
            response.url.join(response.headers["location"])
        )
    return response


async def _login(app_url: str, realm: str) -> str:
    """Log the traffic user into the app, return its session as a Cookie header."""
    host = httpx.URL(app_url).host
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        password = await _prepare_user(client, realm)
        page = await _follow(client, await client.get(app_url))
        match = _LOGIN_ACTION.search(page.text)
        if match is None:
            raise TrafficError(
                "The app did not show its SSO login page: is it exposed with SSO?"
            )
        action = json.loads(f'"{match.group(1)}"')
        landed = await _follow(
            client,
            await client.post(
                action,
                data={
                    "username": TRAFFIC_USERNAME,
                    "password": password,
                    "credentialId": "",
                },
            ),
        )
        if landed.url.host != host:
            raise TrafficError("The traffic user could not log into the app.")
        return "; ".join(
            f"{cookie.name}={cookie.value}"
            for cookie in client.cookies.jar
            if cookie.domain.lstrip(".") == host and cookie.name != "cnp-track"
        )


async def _visit(
    client: httpx.AsyncClient, session: TrafficSession, cookie: str
) -> bool:
    """One fresh visitor. False when the SSO session has to be renewed."""
    client.cookies.clear()
    started = time.monotonic()
    try:
        response = await client.get(
            session.app_url, headers={"Cookie": cookie}, follow_redirects=False
        )
    except httpx.HTTPError:
        session.record("error", 0, int((time.monotonic() - started) * 1000))
        return True
    millis = int((time.monotonic() - started) * 1000)
    if response.status_code in (302, 303) and "openid-connect" in (
        response.headers.get("location", "")
    ):
        return False
    track = response.cookies.get("cnp-track")
    version = "error" if response.status_code >= 500 else (track or "stable")
    session.record(version, response.status_code, millis)
    return True


async def _run(session: TrafficSession, realm: str) -> None:
    try:
        cookie = await _login(session.app_url, realm)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            pending: set[asyncio.Task] = set()
            while not session.stopped and time.time() < session.until:
                visit = asyncio.create_task(_visit(client, session, cookie))
                pending.add(visit)
                visit.add_done_callback(pending.discard)
                await asyncio.sleep(1 / session.rps)
                if visit.done() and visit.result() is False:
                    cookie = await _login(session.app_url, realm)
            await asyncio.gather(*pending, return_exceptions=True)
    except TrafficError as exc:
        logger.warning("Demo traffic for %s stopped: %s", session.app_url, exc)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Demo traffic for %s failed", session.app_url)
    finally:
        session.stopped = True
        session.changed.set()
        if _sessions.get(session.deployment_id) is session:
            del _sessions[session.deployment_id]


async def start(
    deployment_id: int,
    app_url: str,
    realm: str,
    rps: int,
    minutes: int,
    started_by: str,
) -> TrafficSession:
    if deployment_id in _sessions:
        raise TrafficError("Demo traffic is already running for this app.")
    if len(_sessions) >= MAX_SESSIONS:
        raise TrafficError(
            "Too many demo traffic runs at once on the platform: retry later."
        )
    session = TrafficSession(
        deployment_id=deployment_id,
        app_url=app_url,
        rps=min(max(rps, 1), MAX_RPS),
        until=time.time() + 60 * min(max(minutes, 1), MAX_MINUTES),
        started_by=started_by,
    )
    _sessions[deployment_id] = session
    session.task = asyncio.create_task(_run(session, realm))
    return session


def stop(deployment_id: int) -> bool:
    session = _sessions.get(deployment_id)
    if session is None:
        return False
    session.stopped = True
    session.changed.set()
    return True
