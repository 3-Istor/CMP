"""
Muting alerts from the portal (R7). The CMP is Alertmanager's only client
(NetworkPolicy, C3), so the rules live here: every silence carries an exact
cnp_project matcher set server side, other matchers are exact values on a
short list of labels, and a silence is listed or expired only when its
project matcher is the caller's project.
"""

from datetime import datetime, timedelta

import httpx

from app.core.config import settings

PROJECT_LABEL = "cnp_project"
MUTABLE_LABELS = frozenset({"alertname", "cnp_app", "cnp_slo", "severity"})
MAX_VALUE_LENGTH = 200
TIMEOUT = 10


class AlertmanagerError(Exception):
    pass


class SilenceNotFoundError(Exception):
    pass


def _matcher(name: str, value: str) -> dict:
    return {"name": name, "value": value, "isRegex": False, "isEqual": True}


def build(
    project: str,
    matchers: dict[str, str],
    duration: timedelta,
    author: str,
    comment: str,
    now: datetime,
) -> dict:
    """
    Raises:
        ValueError: A label outside MUTABLE_LABELS, an empty or too long
            value, or a duration outside 1 minute to OBS_SILENCE_MAX_HOURS.
    """
    unknown = set(matchers) - MUTABLE_LABELS
    if unknown:
        raise ValueError(f"Labels not allowed: {', '.join(sorted(unknown))}")
    for value in matchers.values():
        if not value or len(value) > MAX_VALUE_LENGTH:
            raise ValueError("Matcher values must be 1 to 200 characters.")
    longest = timedelta(hours=settings.OBS_SILENCE_MAX_HOURS)
    if not timedelta(minutes=1) <= duration <= longest:
        raise ValueError(
            f"A silence lasts 1 minute to {settings.OBS_SILENCE_MAX_HOURS} h."
        )
    return {
        "matchers": [_matcher(PROJECT_LABEL, project)]
        + [_matcher(k, v) for k, v in sorted(matchers.items())],
        "startsAt": now.isoformat(),
        "endsAt": (now + duration).isoformat(),
        "createdBy": author,
        "comment": comment or "Mis en sourdine depuis le portail",
    }


def belongs(silence: dict, project: str) -> bool:
    """True only with an exact, non-regex, positive project matcher."""
    return any(
        m.get("name") == PROJECT_LABEL
        and m.get("value") == project
        and not m.get("isRegex", False)
        and m.get("isEqual", True)
        for m in silence.get("matchers") or []
    )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.ALERTMANAGER_URL, timeout=TIMEOUT
    )


async def _call(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        async with _client() as client:
            response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise AlertmanagerError(str(exc)) from exc
    if response.status_code >= 400 and response.status_code != 404:
        raise AlertmanagerError(
            f"{method} {path}: HTTP {response.status_code}"
        )
    return response


async def create(body: dict) -> str:
    response = await _call("POST", "/api/v2/silences", json=body)
    return response.json()["silenceID"]


async def list_for(project: str) -> list[dict]:
    """The project's silences that are active or yet to start."""
    response = await _call(
        "GET",
        "/api/v2/silences",
        params={"filter": f'{PROJECT_LABEL}="{project}"'},
    )
    return [
        s
        for s in response.json()
        if belongs(s, project)
        and (s.get("status") or {}).get("state") != "expired"
    ]


async def expire(project: str, silence_id: str) -> None:
    """
    Raises:
        SilenceNotFoundError: Unknown, or another project's silence: both
            look the same to the caller.
    """
    response = await _call("GET", f"/api/v2/silence/{silence_id}")
    if response.status_code == 404 or not belongs(response.json(), project):
        raise SilenceNotFoundError(silence_id)
    await _call("DELETE", f"/api/v2/silence/{silence_id}")
