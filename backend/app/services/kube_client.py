"""
Access to the Kubernetes API of the cluster the CMP runs in.

Uses the pod's service account (cmp-security-reader in K3s): reads, plus the
few writes behind the security page's buttons. Outside a cluster there is
nothing to read, and callers report the data as not measured rather than
failing.
"""

import os
from pathlib import Path
from typing import Any

import requests

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
REQUEST_TIMEOUT = 10


class KubeUnavailableError(Exception):
    """Raised when the Kubernetes API cannot be reached or refuses the read."""


def _api_base() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host or not (SERVICE_ACCOUNT_DIR / "token").exists():
        raise KubeUnavailableError("not running inside a Kubernetes cluster")
    return f"https://{host}:{port}"


def kube_get(
    path: str, params: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """
    GET an API path (e.g. ``/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/x``).

    Returns None on 404.

    Raises:
        KubeUnavailableError: Not in a cluster, network error or non-404 failure.
    """
    base = _api_base()
    token = (SERVICE_ACCOUNT_DIR / "token").read_text().strip()
    try:
        response = requests.get(
            f"{base}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            verify=str(SERVICE_ACCOUNT_DIR / "ca.crt"),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise KubeUnavailableError(f"GET {path}: {exc}") from exc
    if response.status_code == 404:
        return None
    if not response.ok:
        raise KubeUnavailableError(f"GET {path}: HTTP {response.status_code}")
    return response.json()


def kube_list(
    path: str, params: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """GET a collection path and return its items (empty when the path is unknown)."""
    body = kube_get(path, params)
    return body.get("items", []) if body else []


def _request(method: str, path: str, **kwargs: Any) -> requests.Response:
    base = _api_base()
    token = (SERVICE_ACCOUNT_DIR / "token").read_text().strip()
    try:
        response = requests.request(
            method,
            f"{base}{path}",
            headers={"Authorization": f"Bearer {token}"},
            verify=str(SERVICE_ACCOUNT_DIR / "ca.crt"),
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise KubeUnavailableError(f"{method} {path}: {exc}") from exc
    if not response.ok and response.status_code != 404:
        raise KubeUnavailableError(
            f"{method} {path}: HTTP {response.status_code}"
        )
    return response


def kube_create(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """
    POST *body* to a collection path.

    Raises:
        KubeUnavailableError: Not in a cluster, refused, or the path is unknown.
    """
    response = _request("POST", path, json=body)
    if response.status_code == 404:
        raise KubeUnavailableError(f"POST {path}: HTTP 404")
    return response.json()


def kube_delete_collection(
    path: str, params: dict[str, str] | None = None
) -> None:
    """DELETE every object of a collection path; an unknown path is a no-op."""
    _request("DELETE", path, params=params)
