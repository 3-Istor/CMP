"""
Where a project's alerts go (06-alerting.md): the app's Discord webhook,
else the project's, else the platform's. One VMAlertmanagerConfig per
project, matching only its own alerts on cnp_project, and one Secret holding
the webhooks it uses: they are secrets (anyone holding one can post), so
they live in Vault and are copied only where Alertmanager reads them.

Every message has the same format: title, severity, app, value, runbook,
portal and mute links, and @here when critical. Info alerts are grouped into
one message a day.
"""

import base64
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.security import alerts as targets_store

GROUP_BY = ["alertname", "cluster", "cnp_project", "cnp_app", "severity"]
PROJECT_KEY = "project"
PLATFORM_KEY = "platform"

TITLE = (
    '{{ if eq .Status "firing" }}[{{ .CommonLabels.severity | toUpper }}]'
    "{{ else }}[RÉSOLU]{{ end }} {{ .CommonLabels.alertname }} · "
    "{{ .CommonLabels.cnp_project }}"
    "{{ if .CommonLabels.cnp_app }}/{{ .CommonLabels.cnp_app }}{{ end }}"
)
MESSAGE = (
    "{{ range .Alerts }}**{{ .Annotations.summary }}**\n"
    "{{ if .Annotations.value }}Valeur : {{ .Annotations.value }}\n{{ end }}"
    "Gravité : {{ .Labels.severity }} · app : {{ .Labels.cnp_app }}"
    "{{ if .Labels.cluster }} · cluster : {{ .Labels.cluster }}{{ end }}\n"
    "{{ if .Annotations.runbook_url }}"
    "[Runbook]({{ .Annotations.runbook_url }}) {{ end }}"
    "{{ if .Annotations.portal_url }}"
    "[Portail]({{ .Annotations.portal_url }}) {{ end }}"
    "{{ if .Annotations.silence_url }}"
    "[Mettre en sourdine]({{ .Annotations.silence_url }}){{ end }}\n"
    "{{ end }}"
)
CONTENT = (
    '{{ if and (eq .Status "firing") (eq .CommonLabels.severity "critical") }}'
    "@here{{ end }}"
)


@dataclass
class ProjectRouting:
    project: str
    apps: list[str]
    # Vault's targets: "project" and "app:<name>".
    targets: dict[str, str] = field(default_factory=dict)
    platform: str = ""


def config_name(project: str) -> str:
    return f"alerts-{project}"


def secret_name(project: str) -> str:
    return f"alert-webhooks-{project}"


def _app_key(app: str) -> str:
    return f"app-{app}"


def webhooks(routing: ProjectRouting) -> dict[str, str]:
    """Secret key -> webhook, only the ones the routes use."""
    valid = {
        k: v
        for k, v in routing.targets.items()
        if v and targets_store.is_discord_webhook(v)
    }
    keys: dict[str, str] = {}
    for app in sorted(routing.apps):
        if valid.get(f"app:{app}"):
            keys[_app_key(app)] = valid[f"app:{app}"]
    if valid.get(targets_store.PROJECT_KEY):
        keys[PROJECT_KEY] = valid[targets_store.PROJECT_KEY]
    elif routing.platform and targets_store.is_discord_webhook(
        routing.platform
    ):
        keys[PLATFORM_KEY] = routing.platform
    return keys


def _labels(project: str) -> dict[str, str]:
    return {
        "cnp.3istor.com/owner": "cmp",
        "cnp.3istor.com/project": project,
    }


def _discord(project: str, key: str) -> dict:
    return {
        "send_resolved": True,
        "webhook_url_secret": {"name": secret_name(project), "key": key},
        "title": TITLE,
        "message": MESSAGE,
        "content": CONTENT,
    }


def _digest(receiver: str) -> dict:
    return {
        "receiver": receiver,
        "matchers": ['severity="info"'],
        "group_by": ["cnp_project"],
        "group_wait": "1h",
        "group_interval": "24h",
        "repeat_interval": "24h",
    }


def _inhibit(project: str, source: str, targets: str) -> dict:
    return {
        "source_matchers": [
            f'cnp_project="{project}"',
            f'severity="{source}"',
        ],
        "target_matchers": [
            f'cnp_project="{project}"',
            f'severity=~"{targets}"',
        ],
        "equal": ["alertname", "cluster", "cnp_app", "cnp_slo"],
    }


def render_config(routing: ProjectRouting) -> dict | None:
    """None when no webhook is set anywhere: Alertmanager's root decides."""
    keys = webhooks(routing)
    if not keys:
        return None
    default = PROJECT_KEY if PROJECT_KEY in keys else PLATFORM_KEY
    project = routing.project
    app_routes = [
        {
            "receiver": key,
            "matchers": [f'cnp_app="{key.removeprefix("app-")}"'],
            "routes": [_digest(key)],
        }
        for key in keys
        if key.startswith("app-")
    ]
    return {
        "apiVersion": "operator.victoriametrics.com/v1beta1",
        "kind": "VMAlertmanagerConfig",
        "metadata": {
            "name": config_name(project),
            "namespace": settings.OBS_RULES_NAMESPACE,
            "labels": _labels(project),
        },
        "spec": {
            "route": {
                "receiver": default,
                "matchers": [f'cnp_project="{project}"'],
                "group_by": GROUP_BY,
                "group_wait": "30s",
                "group_interval": "5m",
                "repeat_interval": "12h",
                "continue": False,
                "routes": app_routes + [_digest(default)],
            },
            "receivers": [
                {"name": key, "discord_configs": [_discord(project, key)]}
                for key in keys
            ],
            "inhibit_rules": [
                _inhibit(project, "critical", "warning|info"),
                _inhibit(project, "warning", "info"),
            ],
        },
    }


def render_secret(routing: ProjectRouting) -> dict | None:
    keys = webhooks(routing)
    if not keys:
        return None
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": secret_name(routing.project),
            "namespace": settings.OBS_RULES_NAMESPACE,
            "labels": _labels(routing.project),
        },
        "data": {
            key: base64.b64encode(url.encode()).decode()
            for key, url in keys.items()
        },
    }
