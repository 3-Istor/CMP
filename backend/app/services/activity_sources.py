"""
Audit sources of the Activity tab that live in Loki: the Kubernetes API audit,
Vault and Keycloak. Every query is built here, scoped to one project; the
browser never sends LogQL, or a member could read another project's logs.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.services.kube_client import kube_list

PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_KEYCLOAK_PAIR = re.compile(r'(\w+)="([^"]*)"')
_SHELL = {"exec", "attach", "portforward"}
_READS = {"get", "list", "watch"}
_RBAC = "rbac.authorization.k8s.io"
# The operator syncing VaultSecrets reads project secrets all day.
_VAULT_MACHINES = ("kubernetes-vault-secrets-operator",)


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
    project: str
    app: str | None
    target: str
    outcome: str
    status_code: int | None
    source_ip: str | None
    details: dict[str, Any] = field(default_factory=dict)


def _ns(when: datetime) -> str:
    return str(int(when.timestamp() * 1e9))


async def loki_lines(
    query: str, start: datetime, end: datetime, limit: int
) -> list[tuple[datetime, dict[str, str], str]]:
    """Log lines matching ``query``, newest first."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.LOKI_URL,
            auth=(settings.LOKI_USERNAME, settings.LOKI_PASSWORD),
            headers={"X-Scope-OrgID": settings.LOKI_TENANT},
            timeout=30,
        ) as client:
            response = await client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": _ns(start),
                    "end": _ns(end),
                    "limit": limit,
                    "direction": "backward",
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


def project_namespaces(project: str) -> set[str]:
    items = kube_list(
        "/api/v1/namespaces",
        {"labelSelector": f"cnp.3istor.com/project={project}"},
    )
    return {item["metadata"]["name"] for item in items}


def _event_id(source: str, when: datetime, line: str) -> str:
    digest = hashlib.sha1(f"{when.isoformat()}{line}".encode()).hexdigest()
    return f"{source}-{digest[:16]}"


def _app_of(namespace: str, project: str) -> str | None:
    rest = namespace.removeprefix(f"{project}-")
    return None if rest == "system" else rest


def kubernetes_event(
    project: str,
    namespaces: set[str],
    when: datetime,
    line: str,
    include_reads: bool,
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
    if namespace not in namespaces:
        return None
    shell = sub in _SHELL
    machine = user.startswith("system:")
    if machine and not shell:
        return None
    secret = resource == "secrets"
    if verb in _READS and not (shell or secret or include_reads):
        return None
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
    )


def vault_event(project: str, when: datetime, line: str) -> Event | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    request = raw.get("request") or {}
    path = request.get("path", "")
    if not path.startswith(f"project-{project}/"):
        return None
    actor = (raw.get("auth") or {}).get("display_name", "")
    if actor.startswith(_VAULT_MACHINES):
        return None
    operation = request.get("operation", "")
    error = raw.get("error")
    secret = path.removeprefix(f"project-{project}/").removeprefix("data/")
    return Event(
        id=_event_id("vault", when, line),
        time=when,
        source="vault",
        actor=actor,
        action=f"vault.{operation}",
        notable=True,
        project=project,
        app=secret.split("/", 1)[0] or None,
        target=secret,
        outcome="failure" if error else "success",
        status_code=None,
        source_ip=request.get("remote_address"),
        details={"path": path, "error": error},
    )


def keycloak_event(project: str, when: datetime, line: str) -> Event | None:
    pairs = dict(_KEYCLOAK_PAIR.findall(line))
    if pairs.get("realmName") != project or "type" not in pairs:
        return None
    kind = pairs["type"]
    failed = kind.endswith("_ERROR")
    return Event(
        id=_event_id("keycloak", when, line),
        time=when,
        source="keycloak",
        actor=pairs.get("username") or pairs.get("userId", ""),
        action=f"keycloak.{kind.lower()}",
        notable=False,
        project=project,
        app=None,
        target=pairs.get("clientId", ""),
        outcome="failure" if failed else "success",
        status_code=None,
        source_ip=pairs.get("ipAddress"),
        details={"error": pairs.get("error"), "type": kind},
    )


async def kubernetes_events(
    project: str,
    start: datetime,
    end: datetime,
    limit: int,
    include_reads: bool,
) -> list[Event]:
    namespaces = await run_in_threadpool(project_namespaces, project)
    if not namespaces:
        return []
    alternatives = "|".join(sorted(re.escape(n) for n in namespaces))
    query = f'{{job="k8s-audit"}} |~ `"namespace":"({alternatives})"`'
    # Machine lines are dropped after the query, so read more than shown.
    lines = await loki_lines(query, start, end, min(limit * 10, 5000))
    events = (
        kubernetes_event(project, namespaces, when, line, include_reads)
        for when, _, line in lines
    )
    return [e for e in events if e][:limit]


async def vault_events(
    project: str, start: datetime, end: datetime, limit: int
) -> list[Event]:
    query = (
        '{namespace="vault", container="vault"} '
        f'|= `"type":"response"` |= `"path":"project-{project}/`'
    )
    lines = await loki_lines(query, start, end, min(limit * 10, 5000))
    events = (vault_event(project, when, line) for when, _, line in lines)
    return [e for e in events if e][:limit]


async def keycloak_events(
    project: str, start: datetime, end: datetime, limit: int
) -> list[Event]:
    query = (
        '{namespace="keycloak"} |= `org.keycloak.events` '
        f'|= `realmName="{project}",`'
    )
    lines = await loki_lines(query, start, end, limit)
    events = (keycloak_event(project, when, line) for when, _, line in lines)
    return [e for e in events if e]


def deployment_events(
    project: str, start: datetime, end: datetime
) -> list[Event]:
    """Argo CD syncs of the project's apps, from each Application's history."""
    apps = kube_list(
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications",
        {"labelSelector": f"cnp.3istor.com/project={project}"},
    )
    events = []
    for application in apps:
        name = application["metadata"]["name"]
        app = (
            application["metadata"].get("labels", {}).get("cnp.3istor.com/app")
        )
        spec = application.get("spec", {})
        repos = [
            s.get("repoURL")
            for s in spec.get("sources") or [spec.get("source", {})]
        ]
        for item in application.get("status", {}).get("history", []):
            when = datetime.fromisoformat(
                item["deployedAt"].replace("Z", "+00:00")
            )
            if not start <= when < end:
                continue
            initiated = item.get("initiatedBy") or {}
            revisions = item.get("revisions") or [item.get("revision")]
            events.append(
                Event(
                    id=f"deploy-{name}-{item.get('id')}",
                    time=when,
                    source="deployment",
                    actor=initiated.get("username") or "Argo CD",
                    action="deployment.sync",
                    notable=False,
                    project=project,
                    app=app,
                    target=name,
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
        async with httpx.AsyncClient(
            base_url=settings.LOKI_URL,
            auth=(settings.LOKI_USERNAME, settings.LOKI_PASSWORD),
            headers={"X-Scope-OrgID": settings.LOKI_TENANT},
            timeout=30,
        ) as client:
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
