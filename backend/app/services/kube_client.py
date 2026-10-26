"""
Access to the Kubernetes API of the cluster the CMP runs in, or of a public
cloud's cluster.

Uses the pod's service account (cmp-security-reader in K3s): reads, plus the
few writes behind the security page's buttons. Outside a cluster there is
nothing to read, and callers report the data as not measured rather than
failing. The EKS/GKE clusters are reached with Argo CD's cluster secrets.
"""

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from app.models.project import TargetCloud

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
REQUEST_TIMEOUT = 10
CLUSTER_SECRET = "/api/v1/namespaces/argocd/secrets/cluster-{}"
CREDENTIALS_TTL = 60

# cloud -> (read at, server, bearer token, CA file)
_credentials: dict[TargetCloud, tuple[float, str, str, str]] = {}

# Kept alive across calls: a fresh TLS handshake per read was the bulk of the
# release poller's CPU.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_maxsize=16))


class KubeUnavailableError(Exception):
    """Raised when the Kubernetes API cannot be reached or refuses the read."""


class ClusterNotConnectedError(Exception):
    """A public cloud's cluster is not up: nothing was sent to it."""

    def __init__(self, cloud: TargetCloud):
        super().__init__(f"the {cloud.value} cluster is not connected")
        self.cloud = cloud.value


OUTSIDE_CLUSTER_NOTICE = (
    "not running inside a Kubernetes cluster: cluster data is not collected. "
    "Expected when the backend runs locally."
)


def in_cluster() -> bool:
    """True when the pod's service account is mounted (never in local dev)."""
    return (
        bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
        and (SERVICE_ACCOUNT_DIR / "token").exists()
    )


def _api_base() -> str:
    if not in_cluster():
        raise KubeUnavailableError("not running inside a Kubernetes cluster")
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    return f"https://{host}:{port}"


def _cloud_credentials(cloud: TargetCloud) -> tuple[str, str, str]:
    secret = kube_get(CLUSTER_SECRET.format(cloud.value))
    if secret is None:
        raise KubeUnavailableError(
            f"no Argo CD cluster secret for {cloud.value}"
        )
    try:
        data = secret["data"]
        config = json.loads(base64.b64decode(data["config"]))
        server = base64.b64decode(data["server"]).decode()
        ca = base64.b64decode(config["tlsClientConfig"]["caData"])
        token = config["bearerToken"]
    except (KeyError, ValueError) as exc:
        raise KubeUnavailableError(
            f"malformed Argo CD cluster secret for {cloud.value}"
        ) from exc
    # Replaced atomically: requests in flight may still read the old file.
    ca_path = Path(tempfile.gettempdir()) / f"cluster-{cloud.value}-ca.crt"
    fd, tmp = tempfile.mkstemp(dir=ca_path.parent)
    with os.fdopen(fd, "wb") as f:
        f.write(ca)
    os.replace(tmp, ca_path)
    return server, token, str(ca_path)


def _target(cloud: TargetCloud, refresh: bool = False) -> tuple[str, str, str]:
    """Base URL, bearer token and CA file of the cluster to call."""
    if cloud == TargetCloud.ONPREM:
        base = _api_base()
        token = (SERVICE_ACCOUNT_DIR / "token").read_text().strip()
        return base, token, str(SERVICE_ACCOUNT_DIR / "ca.crt")
    # Imported here: state imports this module.
    from app.services.clusters.state import cached_state

    if cached_state(cloud) != "up":
        raise ClusterNotConnectedError(cloud)
    hit = _credentials.get(cloud)
    if hit and not refresh and time.monotonic() - hit[0] < CREDENTIALS_TTL:
        return hit[1:]
    target = _cloud_credentials(cloud)
    _credentials[cloud] = (time.monotonic(), *target)
    return target


def _request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    cloud: TargetCloud = TargetCloud.ONPREM,
    **kwargs: Any,
) -> requests.Response:
    for refresh in (False, True):
        base, token, ca = _target(cloud, refresh)
        try:
            response = _session.request(
                method,
                f"{base}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    **(headers or {}),
                },
                verify=ca,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise KubeUnavailableError(f"{method} {path}: {exc}") from exc
        # A cloud token may have rotated: re-read the secret once.
        if response.status_code != 401 or cloud == TargetCloud.ONPREM:
            break
    if not response.ok and response.status_code != 404:
        raise KubeUnavailableError(
            f"{method} {path}: HTTP {response.status_code}"
        )
    return response


def kube_get(
    path: str,
    params: dict[str, str] | None = None,
    cloud: TargetCloud = TargetCloud.ONPREM,
) -> dict[str, Any] | None:
    """
    GET an API path (e.g. ``/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/x``).

    Returns None on 404.

    Raises:
        KubeUnavailableError: Not in a cluster, network error or non-404 failure.
        ClusterNotConnectedError: *cloud* is a public cloud whose cluster is not up.
    """
    response = _request("GET", path, params=params, cloud=cloud)
    if response.status_code == 404:
        return None
    return response.json()


def kube_list(
    path: str,
    params: dict[str, str] | None = None,
    cloud: TargetCloud = TargetCloud.ONPREM,
) -> list[dict[str, Any]]:
    """GET a collection path and return its items (empty when the path is unknown)."""
    body = kube_get(path, params, cloud=cloud)
    return body.get("items", []) if body else []


def kube_create(
    path: str,
    body: dict[str, Any],
    cloud: TargetCloud = TargetCloud.ONPREM,
) -> dict[str, Any]:
    """
    POST *body* to a collection path.

    Raises:
        KubeUnavailableError: Not in a cluster, refused, or the path is unknown.
    """
    response = _request("POST", path, json=body, cloud=cloud)
    if response.status_code == 404:
        raise KubeUnavailableError(f"POST {path}: HTTP 404")
    return response.json()


def kube_delete_collection(
    path: str,
    params: dict[str, str] | None = None,
    cloud: TargetCloud = TargetCloud.ONPREM,
) -> None:
    """DELETE every object of a collection path; an unknown path is a no-op."""
    _request("DELETE", path, params=params, cloud=cloud)


def kube_merge_patch(
    path: str,
    body: dict[str, Any],
    cloud: TargetCloud = TargetCloud.ONPREM,
) -> dict[str, Any]:
    """
    JSON merge-patch an object.

    Raises:
        KubeUnavailableError: Not in a cluster, refused, or the object is gone.
    """
    response = _request(
        "PATCH",
        path,
        headers={"Content-Type": "application/merge-patch+json"},
        json=body,
        cloud=cloud,
    )
    if response.status_code == 404:
        raise KubeUnavailableError(f"PATCH {path}: HTTP 404")
    return response.json()
