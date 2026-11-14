"""
An app's alerts as Alertmanager holds them, and the project silences that
cover it (06-alerting: the Alerts tab reads the alert chain, not Grafana).
"""

from datetime import datetime
from typing import Any

from app.services.observability import silences, stores

ANNOTATIONS = ("summary", "description", "value", "runbook_url")
LABELS = ("alertname", "severity", "cnp_slo", "slo_window")


def _alert(raw: dict) -> dict[str, Any]:
    labels = raw.get("labels") or {}
    annotations = raw.get("annotations") or {}
    status = raw.get("status") or {}
    return {
        "fingerprint": raw.get("fingerprint", ""),
        "state": status.get("state", "active"),
        "silenced_by": status.get("silencedBy") or [],
        "starts_at": datetime.fromisoformat(
            raw["startsAt"].replace("Z", "+00:00")
        ),
        "labels": {k: labels[k] for k in LABELS if k in labels},
        **{k: annotations.get(k) for k in ANNOTATIONS},
    }


def covers(silence: dict, app: str) -> bool:
    """A project silence with no app matcher, or one on this app."""
    apps = {
        m.get("value")
        for m in silence.get("matchers") or []
        if m.get("name") == "cnp_app"
    }
    return not apps or app in apps


async def app_alerts(project: str, app: str) -> dict[str, Any]:
    alerts = [_alert(a) for a in await stores.firing_alerts(project, app)]
    alerts.sort(key=lambda a: a["starts_at"], reverse=True)
    try:
        found = await silences.list_for(project)
    except silences.AlertmanagerError as exc:
        raise stores.StoreError(f"silences: {exc}") from exc
    return {
        "alerts": alerts,
        "silences": [s for s in found if covers(s, app)],
    }
