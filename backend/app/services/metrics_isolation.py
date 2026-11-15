"""
Per-project isolation of the signals a project reads through vmauth.

All metrics live in VictoriaMetrics tenant 0. Each project's Grafana org reads
them through vmauth as its own user, whose every query vmselect filters to the
project's namespaces (extra_filters): a query naming another namespace returns
nothing. The CMP writes vmauth's config, and keeps it in step with the
projects' namespaces.

Once a new store is configured (CNP-185, D6, D7, D10), each user only reaches
read paths, listed per store; anything else gets no route:
- metrics: the Prometheus read API, with both filter spellings pinned;
- logs: LogsQL reads, with every filter spelling pinned and the single
  tenant forced;
- traces: the Jaeger read API, with the project's own tenant forced. The
  traces APIs ignore extra_filters, so the tenant header is the isolation.
vmauth drops a client query arg that clashes with one of url_prefix, and
replaces a client header with a configured one.
"""

import base64
import hashlib
import hmac
import json
import logging
from urllib.parse import quote

from app.core.config import settings
from app.services.alerts_catalog import namespace_matcher
from app.services.kube_client import kube_get, kube_merge_patch

logger = logging.getLogger(__name__)

SECRET_PATH = "/api/v1/namespaces/observability/secrets/vmauth-projects"
VMSELECT = (
    "http://vm-victoria-metrics-cluster-vmselect.observability.svc.cluster.local"
    ":8481/select/0/prometheus"
)
# The clouds write to the same VictoriaMetrics since CNP-110. Series written
# before 2026-10-27 carry no cluster label, hence the empty alternative.
ONPREM_FILTER = '{cluster=~"onprem|"}'


def cluster_filter(cluster: str) -> str:
    """vmselect ``extra_filters[]`` value for one of ``inputs.CLUSTERS``."""
    return ONPREM_FILTER if cluster == "onprem" else f'{{cluster="{cluster}"}}'


def username(project: str) -> str:
    return f"project-{project}"


def password(project: str) -> str:
    """Derived, so it is stored nowhere but in vmauth and the datasource."""
    if not settings.METRICS_PROJECT_KEY:
        raise RuntimeError("METRICS_PROJECT_KEY is not set")
    return hmac.new(
        settings.METRICS_PROJECT_KEY.encode(),
        f"vmauth:{project}".encode(),
        hashlib.sha256,
    ).hexdigest()


def url_prefix(namespaces: list[str], base: str = VMSELECT) -> str:
    # vmauth drops a client query arg only when url_prefix sets the same name,
    # and vmselect ORs every extra_filters it receives, with or without "[]".
    # Setting one name only lets a client widen the filter with the other.
    selector = quote("{" + namespace_matcher(namespaces) + "}", safe="")
    return f"{base}?extra_filters[]={selector}&extra_filters={selector}"


METRICS_READ_PATHS = [
    "/api/v1/query",
    "/api/v1/query_range",
    "/api/v1/query_exemplars",
    "/api/v1/series",
    "/api/v1/labels",
    "/api/v1/label/[a-zA-Z0-9_.]+/values",
    "/api/v1/metadata",
    "/api/v1/status/buildinfo",
]
LOGS_READ_PATHS = [
    "/select/logsql/(query|hits|facets|field_names|field_values"
    "|stream_field_names|stream_field_values|streams|stream_ids"
    "|stats_query|stats_query_range|tail)",
    "/select/buildinfo",
]
TRACES_READ_PATHS = [
    "/select/jaeger/api/(services|services/[^/]+/operations|traces"
    "|traces/[0-9a-fA-F]+|dependencies)",
]
LOGS_TENANT = ["AccountID: 0", "ProjectID: 0"]


def logs_url_prefix(namespaces: list[str]) -> str:
    # VictoriaLogs documents extra_filters and extra_stream_filters only;
    # the "[]" spellings are pinned too, so a future alias cannot widen
    # anything (the lesson of the vmselect filter).
    names = ",".join(json.dumps(n) for n in sorted(namespaces))
    fields = quote(f"namespace:in({names})", safe="")
    streams = quote(f"{{namespace in ({names})}}", safe="")
    pinned = [
        ("extra_filters", fields),
        ("extra_filters[]", fields),
        ("extra_stream_filters", streams),
        ("extra_stream_filters[]", streams),
    ]
    return f"{settings.OBS_VLOGS_URL}/?" + "&".join(
        f"{k}={v}" for k, v in pinned
    )


def stores_configured() -> bool:
    return bool(
        settings.OBS_VMSINGLE_URL
        or settings.OBS_VLOGS_URL
        or settings.OBS_VTRACES_URL
    )


def url_map(namespaces: list[str], account: int | None) -> list[dict]:
    entries = [
        {
            "src_paths": METRICS_READ_PATHS,
            "url_prefix": url_prefix(
                namespaces, settings.OBS_VMSINGLE_URL or VMSELECT
            ),
        }
    ]
    if settings.OBS_VLOGS_URL:
        entries.append(
            {
                "src_paths": LOGS_READ_PATHS,
                "url_prefix": logs_url_prefix(namespaces),
                "headers": LOGS_TENANT,
            }
        )
    if settings.OBS_VTRACES_URL and account is not None:
        entries.append(
            {
                "src_paths": TRACES_READ_PATHS,
                "url_prefix": settings.OBS_VTRACES_URL,
                "headers": [f"AccountID: {account}", "ProjectID: 0"],
            }
        )
    return entries


def render(
    ns_projects: dict[str, str], accounts: dict[str, int] | None = None
) -> str:
    by_project: dict[str, list[str]] = {}
    for namespace, project in ns_projects.items():
        by_project.setdefault(project, []).append(namespace)
    users = []
    for project, namespaces in sorted(by_project.items()):
        user = {"username": username(project), "password": password(project)}
        if stores_configured():
            user["url_map"] = url_map(
                namespaces, (accounts or {}).get(project)
            )
        else:
            user["url_prefix"] = url_prefix(namespaces)
        users.append(user)
    # JSON is valid YAML for vmauth. No unauthorized_user: without a
    # project's credentials, nothing gets through.
    return json.dumps({"users": users}, indent=2) + "\n"


def sync(
    ns_projects: dict[str, str], accounts: dict[str, int] | None = None
) -> bool:
    """Write vmauth's config when it changed; True when it did."""
    wanted = render(ns_projects, accounts)
    secret = kube_get(SECRET_PATH) or {}
    current = base64.b64decode(
        (secret.get("data") or {}).get("auth.yml", "")
    ).decode()
    if current == wanted:
        return False
    kube_merge_patch(
        SECRET_PATH,
        {"data": {"auth.yml": base64.b64encode(wanted.encode()).decode()}},
    )
    logger.info(
        "vmauth config written for %d projects",
        len(json.loads(wanted)["users"]),
    )
    return True
