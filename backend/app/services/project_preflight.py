"""
Project leftovers preflight

A project that was torn down badly can leave Cloudflare DNS records, a Keycloak
realm or client, Keycloak groups and a Vault mount behind. Terraform then fails
halfway through creating a new project of the same name. This lists what is
still there before anything is written or applied.
"""

import logging

import requests

from app.core.config import settings
from app.services.registry_names import HOSTNAME_DOMAIN

logger = logging.getLogger(__name__)

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
PROJECT_REALM = "3istor"
REQUEST_TIMEOUT = 10

# Cloudflare sits in front of the Keycloak URL and blocks the default
# python-requests user agent.
_HEADERS = {"User-Agent": "cmp-backend-preflight/1.0"}


class PreflightUnavailableError(Exception):
    """Raised when a leftover check could not be performed."""


def find_leftovers(project_name: str) -> list[str]:
    """
    Return a description of every resource that already exists for the project.

    Raises:
        PreflightUnavailableError: If a service could not be queried. A check
            that cannot run is not a pass.
    """
    leftovers: list[str] = []
    try:
        leftovers += _cloudflare_leftovers(project_name)
        leftovers += _keycloak_leftovers(project_name)
        leftovers += _vault_leftovers(project_name)
    except requests.RequestException as exc:
        raise PreflightUnavailableError(
            f"Could not check for leftovers of '{project_name}': {exc}"
        ) from exc
    return leftovers


def _cloudflare_leftovers(project_name: str) -> list[str]:
    if not (settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_ZONE_ID):
        logger.warning(
            "Cloudflare is not configured: skipping the DNS leftovers check"
        )
        return []

    found: list[str] = []
    for prefix in ("status", "offhours", "auth"):
        hostname = f"{prefix}-{project_name}.{HOSTNAME_DOMAIN}"
        response = requests.get(
            f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/dns_records",
            headers={
                **_HEADERS,
                "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
            },
            params={"name": hostname},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        if response.json().get("result"):
            found.append(f"Cloudflare DNS record '{hostname}'")
    return found


def _keycloak_admin_token() -> str:
    response = requests.post(
        f"{settings.KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        headers=_HEADERS,
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": settings.KEYCLOAK_ADMIN_USERNAME,
            "password": settings.KEYCLOAK_ADMIN_PASSWORD,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _keycloak_leftovers(project_name: str) -> list[str]:
    headers = {
        **_HEADERS,
        "Authorization": f"Bearer {_keycloak_admin_token()}",
    }
    admin = f"{settings.KEYCLOAK_URL}/admin/realms"
    found: list[str] = []

    realm = requests.get(
        f"{admin}/{project_name}", headers=headers, timeout=REQUEST_TIMEOUT
    )
    if realm.status_code != 404:
        realm.raise_for_status()
        found.append(f"Keycloak realm '{project_name}'")

    client_id = f"broker-{project_name}"
    clients = requests.get(
        f"{admin}/{PROJECT_REALM}/clients",
        headers=headers,
        params={"clientId": client_id},
        timeout=REQUEST_TIMEOUT,
    )
    clients.raise_for_status()
    if any(c.get("clientId") == client_id for c in clients.json()):
        found.append(
            f"Keycloak client '{client_id}' in realm '{PROJECT_REALM}'"
        )

    for suffix in ("admins", "members"):
        group_name = f"project-{project_name}-{suffix}"
        groups = requests.get(
            f"{admin}/{PROJECT_REALM}/groups",
            headers=headers,
            params={"search": group_name, "exact": "true"},
            timeout=REQUEST_TIMEOUT,
        )
        groups.raise_for_status()
        if any(g.get("name") == group_name for g in groups.json()):
            found.append(
                f"Keycloak group '{group_name}' in realm '{PROJECT_REALM}'"
            )
    return found


def _vault_leftovers(project_name: str) -> list[str]:
    response = requests.get(
        f"{settings.VAULT_URL}/v1/sys/mounts",
        headers={**_HEADERS, "X-Vault-Token": settings.VAULT_TOKEN},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    mounts = body.get("data", body)
    mount = f"project-{project_name}/"
    return [f"Vault mount '{mount}'"] if mount in mounts else []
