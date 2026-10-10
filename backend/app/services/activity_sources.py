"""
Sources of the Activity tab.

The audit sources (Kubernetes API, Vault, Keycloak, Argo CD syncs) are read
across all projects by the activity collector, a minute at a time, and kept in
the CMP database: querying Loki over days on every page load took longer than
the gateway's timeout. Pod logs are still read live, one project at a time.
Every LogQL query is built here; the browser never sends one.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.services.kube_client import kube_list

PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_KEYCLOAK_PAIR = re.compile(r'(\w+)="([^"]*)"')
_SHELL = {"exec", "attach", "portforward"}
_READS = {"get", "list", "watch"}
_RBAC = "rbac.authorization.k8s.io"
# The operator syncing VaultSecrets reads project secrets all day.
_VAULT_MACHINES = ("kubernetes-vault-secrets-operator",)

KUBERNETES_QUERY = (
    '{job="k8s-audit"} '
    '| json user="user.username", sub="objectRef.subresource" '
    # system:admin is the admin kubeconfig: a person, not a controller.
    '| user !~ "system:.*" or user = "system:admin" '
    'or sub =~ "exec|attach|portforward"'
)
VAULT_QUERY = (
    '{namespace="vault", container="vault"} |= `"type":"response"`'
    + "".join(f" != `{m}`" for m in _VAULT_MACHINES)
)
KEYCLOAK_QUERY = '{namespace=~"keycloak|.+-system"} |= `org.keycloak.events`'
NETWORK_QUERY = (
    '{namespace="kube-system", container="cilium-agent"} |= `"POLICY_DENIED"`'
)


class LokiUnavailableError(Exception):
    pass


@dataclass
class Event:
    id: str
    time: datetime
    source: str
    actor: str
    action: str
    notable: bool
    project: str | None
    app: str | None
    target: str
    outcome: str
    status_code: int | None
    source_ip: str | None
    details: dict[str, Any] = field(default_factory=dict)
    is_read: bool = False


def _ns(when: datetime) -> str:
    return str(int(when.timestamp() * 1e9))


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.LOKI_URL,
        auth=(settings.LOKI_USERNAME, settings.LOKI_PASSWORD),
        headers={"X-Scope-OrgID": settings.LOKI_TENANT},
        timeout=30,
    )


async def loki_lines(
    query: str,
    start: datetime,
    end: datetime,
    limit: int,
    direction: str = "backward",
) -> list[tuple[datetime, dict[str, str], str]]:
    """Log lines matching ``query``, newest first."""
    try:
        async with _client() as client:
            response = await client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": _ns(start),
                    "end": _ns(end),
                    "limit": limit,
                    "direction": direction,
                },
            )
    except httpx.HTTPError as exc:
        raise LokiUnavailableError(str(exc)) from exc
    if response.status_code != 200:
        raise LokiUnavailableError(
            f"Loki answered {response.status_code}: {response.text[:200]}"
        )
    lines = []
    for stream in response.json()["data"]["result"]:
        for ts, line in stream["values"]:
            when = datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)
            lines.append((when, stream["stream"], line))
    lines.sort(key=lambda item: item[0], reverse=True)
    return lines[:limit]


def namespace_projects() -> dict[str, str]:
    """Every project namespace and the project it belongs to."""
    items = kube_list(
        "/api/v1/namespaces", {"labelSelector": "cnp.3istor.com/project"}
    )
    return {
        item["metadata"]["name"]: item["metadata"]["labels"][
            "cnp.3istor.com/project"
        ]
        for item in items
    }


def _event_id(source: str, when: datetime, line: str) -> str:
    digest = hashlib.sha1(f"{when.isoformat()}{line}".encode()).hexdigest()
    return f"{source}-{digest[:16]}"


def _app_of(namespace: str, project: str | None) -> str | None:
    if not project:
        return None
    rest = namespace.removeprefix(f"{project}-")
    return None if rest == "system" else rest


def kubernetes_event(
    ns_projects: dict[str, str], when: datetime, line: str
) -> Event | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    ref = raw.get("objectRef") or {}
    namespace = ref.get("namespace", "")
    user = (raw.get("user") or {}).get("username", "")
    verb = raw.get("verb", "")
    sub = ref.get("subresource") or ""
    resource = ref.get("resource", "")
    shell = sub in _SHELL
    machine = user.startswith("system:") and user != "system:admin"
    if not user or machine and not shell:
        return None
    secret = resource == "secrets"
    project = ns_projects.get(namespace)
    code = (raw.get("responseStatus") or {}).get("code")
    name = ref.get("name") or ""
    return Event(
        id=_event_id("kubernetes", when, line),
        time=when,
        source="kubernetes",
        actor=user,
        action=f"kubernetes.{sub or verb}",
        notable=shell
        or secret
        or ref.get("apiGroup") == _RBAC
        and verb not in _READS,
        project=project,
        app=_app_of(namespace, project),
        target="/".join(p for p in (resource, name, sub) if p),
        outcome="success" if code is None or code < 400 else "failure",
        status_code=code,
        source_ip=(raw.get("sourceIPs") or [None])[0],
        details={
            "request_uri": raw.get("requestURI"),
            "user_agent": raw.get("userAgent"),
            "groups": (raw.get("user") or {}).get("groups"),
            "namespace": namespace,
        },
        is_read=verb in _READS and not (shell or secret),
    )


def _project_of_path(path: str, projects: set[str]) -> str | None:
    if not path.startswith("project-"):
        return None
    head = path.removeprefix("project-").split("/", 1)[0]
    return head if head in projects else None


def vault_event(projects: set[str], when: datetime, line: str) -> Event | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    request = raw.get("request") or {}
    path = request.get("path", "")
    actor = (raw.get("auth") or {}).get("display_name", "")
    if actor.startswith(_VAULT_MACHINES):
        return None
    project = _project_of_path(path, projects)
    operation = request.get("operation", "")
    error = raw.get("error")
    secret = (
        path.removeprefix(f"project-{project}/").removeprefix("data/")
        if project
        else path
    )
    return Event(
        id=_event_id("vault", when, line),
        time=when,
        source="vault",
        actor=actor,
        action=f"vault.{operation}",
        notable=bool(project)
        and operation in ("read", "create", "update", "delete"),
        project=project,
        app=(secret.split("/", 1)[0] or None) if project else None,
        target=secret,
        outcome="failure" if error else "success",
        status_code=None,
        source_ip=request.get("remote_address"),
        details={"path": path, "error": error},
        is_read=operation == "list",
    )


def keycloak_event(
    projects: set[str], when: datetime, line: str
) -> Event | None:
    pairs = dict(_KEYCLOAK_PAIR.findall(line))
    if "type" not in pairs:
        return None
    realm = pairs.get("realmName", "")
    kind = pairs["type"]
    failed = kind.endswith("_ERROR")
    return Event(
        id=_event_id("keycloak", when, line),
        time=when,
        source="keycloak",
        actor=pairs.get("username") or pairs.get("userId", ""),
        action=f"keycloak.{kind.lower()}",
        notable=False,
        project=realm if realm in projects else None,
        app=None,
        target=pairs.get("clientId", ""),
        outcome="failure" if failed else "success",
        status_code=None,
        source_ip=pairs.get("ipAddress"),
        details={"error": pairs.get("error"), "type": kind, "realm": realm},
    )


def _workload(pod: str) -> str:
    """A pod name without the suffixes its controller generated."""
    return re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$|-[a-z0-9]{5}$", "", pod)


def network_event(
    ns_projects: dict[str, str], when: datetime, line: str
) -> Event | None:
    """A flow a network policy dropped, kept on the side that dropped it."""
    try:
        flow = json.loads(line)["flow"]
    except (ValueError, KeyError, TypeError):
        return None
    src_side = flow.get("source") or {}
    dst_side = flow.get("destination") or {}
    ingress = flow.get("traffic_direction") == "INGRESS"
    local = dst_side if ingress else src_side
    project = ns_projects.get(local.get("namespace", ""))
    if not project:
        return None
    l4 = flow.get("l4") or {}
    proto = next(iter(l4), "")
    port = (l4.get(proto) or {}).get("destination_port")
    ips = flow.get("IP") or {}

    def side(s: dict, ip: str | None) -> str:
        if s.get("namespace"):
            return f"{s['namespace']}/{_workload(s.get('pod_name', ''))}"
        return ip or "world"

    return Event(
        id=_event_id("network", when, line),
        time=when,
        source="network",
        actor=side(src_side, ips.get("source")),
        action="network.denied",
        notable=False,
        project=project,
        app=_app_of(local.get("namespace", ""), project),
        target=side(dst_side, ips.get("destination")),
        outcome="failure",
        status_code=None,
        source_ip=ips.get("source"),
        details={
            "direction": "ingress" if ingress else "egress",
            "protocol": proto,
            "port": port,
        },
    )


def deployment_events() -> list[Event]:
    """Argo CD syncs of every project app, from each Application's history."""
    apps = kube_list(
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications",
        {"labelSelector": "cnp.3istor.com/project"},
    )
    events = []
    for application in apps:
        meta = application["metadata"]
        labels = meta.get("labels", {})
        spec = application.get("spec", {})
        repos = [
            s.get("repoURL")
            for s in spec.get("sources") or [spec.get("source", {})]
        ]
        for item in application.get("status", {}).get("history", []):
            initiated = item.get("initiatedBy") or {}
            revisions = item.get("revisions") or [item.get("revision")]
            events.append(
                Event(
                    id=f"deploy-{meta['name']}-{item.get('id')}",
                    time=datetime.fromisoformat(
                        item["deployedAt"].replace("Z", "+00:00")
                    ),
                    source="deployment",
                    actor=initiated.get("username") or "Argo CD",
                    action="deployment.sync",
                    notable=False,
                    project=labels.get("cnp.3istor.com/project"),
                    app=labels.get("cnp.3istor.com/app"),
                    target=meta["name"],
                    outcome="success",
                    status_code=None,
                    source_ip=None,
                    details={
                        "revisions": dict(zip(repos, revisions)),
                        "automated": bool(initiated.get("automated")),
                    },
                )
            )
    return events


# ── Pod logs, read live ──────────────────────────────────────────────────────


@dataclass
class LogLine:
    time: datetime
    namespace: str
    pod: str
    container: str
    line: str


_LEVELS = {
    "error": r"(?i)\b(error|err|fatal|panic|exception)\b",
    "warn": r"(?i)\b(warn|warning)\b",
}


def logs_query(
    project: str,
    namespace: str | None,
    pod: str | None,
    container: str | None,
    search: str | None,
    level: str | None,
) -> str:
    selector = [f'project="{project}"']
    for label, value in (
        ("namespace", namespace),
        ("pod", pod),
        ("container", container),
    ):
        if value:
            selector.append(f'{label}="{value}"')
    query = "{" + ", ".join(selector) + "}"
    if search:
        query += f" |= `{search}`"
    if level in _LEVELS:
        query += f" |~ `{_LEVELS[level]}`"
    return query


async def app_logs(
    project: str,
    namespace: str | None,
    pod: str | None,
    container: str | None,
    search: str | None,
    level: str | None,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[LogLine]:
    query = logs_query(project, namespace, pod, container, search, level)
    lines = await loki_lines(query, start, end, limit)
    return [
        LogLine(
            time=when,
            namespace=labels.get("namespace", ""),
            pod=labels.get("pod", ""),
            container=labels.get("container", ""),
            line=line,
        )
        for when, labels, line in lines
    ]


async def log_label_values(
    project: str, label: str, start: datetime, end: datetime
) -> list[str]:
    try:
        async with _client() as client:
            response = await client.get(
                f"/loki/api/v1/label/{label}/values",
                params={
                    "query": f'{{project="{project}"}}',
                    "start": _ns(start),
                    "end": _ns(end),
                },
            )
    except httpx.HTTPError as exc:
        raise LokiUnavailableError(str(exc)) from exc
    if response.status_code != 200:
        raise LokiUnavailableError(f"Loki answered {response.status_code}")
    return sorted(response.json().get("data") or [])
