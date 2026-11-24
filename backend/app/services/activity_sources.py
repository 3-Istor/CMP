"""
Sources of the Activity tab.

The audit sources (Kubernetes API, Vault, Keycloak, Argo CD syncs) are read
across all projects by the activity collector, a minute at a time, and kept in
the CMP database: querying the log store over days on every page load took
longer than the gateway's timeout. Pod logs are still read live from
VictoriaLogs, one project at a time. Every LogsQL query is built here; the
browser never sends one.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, ProjectStatus, TargetCloud
from app.services.environments import layout
from app.services.kube_client import kube_list

PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_KEYCLOAK_PAIR = re.compile(r'(\w+)="([^"]*)"')
_SHELL = {"exec", "attach", "portforward"}
_READS = {"get", "list", "watch"}
_RBAC = "rbac.authorization.k8s.io"
# The operator syncing VaultSecrets reads project secrets all day.
_VAULT_MACHINES = ("kubernetes-vault-secrets-operator",)
# Vault's Kubernetes auth names a service account kubernetes-<ns>-<sa>: the
# CMP and the platform controllers reading what they run on, never a person.
_VAULT_SERVICE_ACCOUNT = "kubernetes-"

_REGEX_SPECIAL = set(r"\.+*?()|[]{}^$")


def quoted(text: str) -> str:
    """A LogsQL quoted string; JSON escaping is LogsQL's string escaping."""
    return json.dumps(text, ensure_ascii=False)


def contains(text: str) -> str:
    """A filter on _msg containing ``text`` as is, like LogQL's ``|=``."""
    return "~" + quoted(
        "".join(f"\\{c}" if c in _REGEX_SPECIAL else c for c in text)
    )


# Every audit source keeps its raw line in _msg (Fluent Bit's "log" field).
KUBERNETES_QUERY = (
    '{job="k8s-audit"}'
    " | unpack_json fields (user.username, objectRef.subresource)"
    # system:admin is the admin kubeconfig: a person, not a controller.
    ' | filter user.username:!~"^system:" or user.username:="system:admin"'
    ' or objectRef.subresource:~"^(exec|attach|portforward)$"'
)
VAULT_QUERY = (
    '{namespace="vault", container="vault"} '
    + contains('"type":"response"')
    + "".join(f" -{contains(m)}" for m in _VAULT_MACHINES)
)
KEYCLOAK_QUERY = '{namespace=~"keycloak|.+-system"} ' + contains(
    "org.keycloak.events"
)
NETWORK_QUERY = (
    '{namespace="kube-system", container="cilium-agent"} '
    + contains('"POLICY_DENIED"')
)
STREAM_FIELDS = ("cluster", "namespace", "pod", "container", "project", "app")
# One tenant for every log (D6): the stream fields are the isolation.
LOGS_TENANT = {"AccountID": "0", "ProjectID": "0"}


class LogsUnavailableError(Exception):
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


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def time_filter(start: datetime, end: datetime) -> str:
    return f"_time:[{stamp(start)}, {stamp(end)})"


def project_streams(project: str) -> str:
    """The project's own streams, ANDed with any query that carries it."""
    return "{project=" + quoted(project) + "}"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.OBS_VLOGS_URL, headers=LOGS_TENANT, timeout=30
    )


async def _post(path: str, data: dict[str, Any]) -> httpx.Response:
    if not settings.OBS_VLOGS_URL:
        raise LogsUnavailableError("OBS_VLOGS_URL is not set")
    try:
        async with _client() as client:
            response = await client.post(path, data=data)
    except httpx.HTTPError as exc:
        raise LogsUnavailableError(str(exc)) from exc
    if response.status_code != 200:
        raise LogsUnavailableError(
            f"VictoriaLogs answered {response.status_code}: "
            f"{response.text[:200]}"
        )
    return response


def _when(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


async def log_lines(
    query: str,
    start: datetime,
    end: datetime,
    limit: int,
    oldest_first: bool = False,
    streams: str | None = None,
    fields: tuple[str, ...] = (),
) -> list[tuple[datetime, dict[str, str], str]]:
    """
    The first ``limit`` lines matching ``query`` in [start, end), newest
    first whichever end they were read from, with their stream fields and
    ``fields``. ``streams`` is a stream filter VictoriaLogs ANDs with the
    whole query.
    """
    order = "" if oldest_first else " desc"
    wanted = STREAM_FIELDS + fields
    data = {
        "query": f"{time_filter(start, end)} {query}"
        f" | sort by (_time{order}) | limit {limit}"
        f" | fields _time, _msg, {', '.join(wanted)}",
    }
    if streams:
        data["extra_stream_filters"] = streams
    response = await _post("/select/logsql/query", data)
    lines = []
    for raw in response.text.splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        labels = {f: row[f] for f in wanted if row.get(f)}
        lines.append((_when(row["_time"]), labels, row.get("_msg", "")))
    lines.sort(key=lambda item: item[0], reverse=True)
    return lines


def namespace_projects() -> dict[str, str]:
    """Every project namespace and the project it belongs to."""
    items = kube_list(
        "/api/v1/namespaces", {"labelSelector": "cnp.3istor.com/project"}
    )
    result = {
        item["metadata"]["name"]: item["metadata"]["labels"][
            "cnp.3istor.com/project"
        ]
        for item in items
    }
    # Cloud namespaces live in EKS/GKE, so they are derived from the DB
    # (cnp-project-base: <project>-system plus each app's environments).
    with SessionLocal() as db:
        rows = (
            db.query(Project.project_name, Deployment.name)
            .outerjoin(
                Deployment,
                (Deployment.project_id == Project.project_name)
                & (Deployment.provider_type == ProviderType.KUBERNETES)
                & (Deployment.status != DeploymentStatus.DELETED),
            )
            .filter(
                Project.target_cloud != TargetCloud.ONPREM,
                Project.status != ProjectStatus.DECOMMISSIONING,
            )
            .all()
        )
    for project, app in rows:
        names = [f"{project}-system"] + (
            [layout.namespace(project, app, e) for e in layout.ENVIRONMENTS]
            if app
            else []
        )
        # namespace_matcher refuses the whole list over one bad name.
        result.update({n: project for n in names if PROJECT_NAME.match(n)})
    return result


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
    reading = operation in ("read", "list")
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
        and operation in ("read", "create", "update", "delete")
        and not (reading and actor.startswith(_VAULT_SERVICE_ACCOUNT)),
        project=project,
        app=(secret.split("/", 1)[0] or None) if project else None,
        target=secret,
        outcome="failure" if error else "success",
        status_code=None,
        source_ip=request.get("remote_address"),
        details={"path": path, "error": error},
        is_read=reading,
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


# A JSON line's level is in json.level, a plain line's in its words.
LEVELS = {
    "error": (
        "error|err|fatal|panic|exception",
        "error|err|fatal|panic|critical",
    ),
    "warn": ("warn|warning", "warn|warning"),
}


def level_filter(level: str) -> str:
    words, names = LEVELS[level]
    by_words = quoted(rf"(?i)\b({words})\b")
    by_field = quoted(f"(?i)^({names})$")
    return f"(_msg:~{by_words} or json.level:~{by_field})"


def logs_query(
    project: str,
    namespace: str | None,
    pod: str | None,
    container: str | None,
    search: str | None,
    level: str | None,
) -> str:
    selector = [f"project={quoted(project)}"]
    for label, value in (
        ("namespace", namespace),
        ("pod", pod),
        ("container", container),
    ):
        if value:
            selector.append(f"{label}={quoted(value)}")
    query = "{" + ", ".join(selector) + "}"
    if search:
        query += f" {contains(search)}"
    if level in LEVELS:
        query += f" {level_filter(level)}"
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
    lines = await log_lines(
        query, start, end, limit, streams=project_streams(project)
    )
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
    response = await _post(
        "/select/logsql/stream_field_values",
        {
            "query": f"{time_filter(start, end)} {project_streams(project)}",
            "field": label,
            "extra_stream_filters": project_streams(project),
        },
    )
    return sorted(v["value"] for v in response.json().get("values") or [])


async def log_volume(
    project: str,
    namespace: str | None,
    start: datetime,
    end: datetime,
    step: int,
) -> list[tuple[dict[str, str], list[tuple[datetime, float]]]]:
    """Lines and bytes per namespace and step, one series per name."""
    selector = f"project={quoted(project)}"
    if namespace:
        selector += f", namespace={quoted(namespace)}"
    response = await _post(
        "/select/logsql/stats_query_range",
        {
            # The same counts as the cnp_log_* recording rules.
            "query": "{" + selector + "}"
            " | stats by (namespace) count() lines,"
            " sum_len(_msg, json.*) bytes",
            "start": stamp(start),
            "end": stamp(end),
            "step": f"{step}s",
            "extra_stream_filters": project_streams(project),
        },
    )
    return [
        (
            series["metric"],
            [
                (datetime.fromtimestamp(float(ts), tz=timezone.utc), float(v))
                for ts, v in series["values"]
            ],
        )
        for series in response.json()["data"]["result"]
    ]
