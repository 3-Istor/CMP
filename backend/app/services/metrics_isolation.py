"""
Per-project metrics isolation.

All metrics live in VictoriaMetrics tenant 0. Each project's Grafana org reads
them through vmauth as its own user, whose every query vmselect filters to the
project's namespaces (extra_filters): a query naming another namespace returns
nothing. The CMP writes vmauth's config, and keeps it in step with the
projects' namespaces.
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


def render(ns_projects: dict[str, str]) -> str:
    by_project: dict[str, list[str]] = {}
    for namespace, project in ns_projects.items():
        by_project.setdefault(project, []).append(namespace)
    users = [
        {
            "username": username(project),
            "password": password(project),
            "url_prefix": (
                f"{VMSELECT}?extra_filters[]="
                + quote("{" + namespace_matcher(namespaces) + "}", safe="")
            ),
        }
        for project, namespaces in sorted(by_project.items())
    ]
    # JSON is valid YAML for vmauth. No unauthorized_user: without a
    # project's credentials, nothing gets through.
    return json.dumps({"users": users}, indent=2) + "\n"


def sync(ns_projects: dict[str, str]) -> bool:
    """Write vmauth's config when it changed; True when it did."""
    wanted = render(ns_projects)
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
