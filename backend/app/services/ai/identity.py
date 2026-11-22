"""
The Keycloak identities that call the AI gateway, and their tokens.

Each agent (``agent-<project>-<agent>``) and each AI-enabled app
(``app-<project>-<app>``) is a confidential client of the platform realm,
with the gateway audience and a ``project`` claim. The CMP manages them with
its provisioner client (master realm) and keeps a copy of each secret in the
app's Vault path, where the chart's VaultSecret syncs it.

Test chats run as the user: the user's CMP token is exchanged, first by the
CMP's own client for a token whose audience is the agent, then by the agent
for a gateway token. The gateway then sees ``azp`` = the agent (its budget
and policy) and ``sub`` = the user. Keycloak's standard token exchange only
lets a client exchange a token it is the audience or the ``azp`` of, hence
the two hops and the per-agent audience scope offered to the CMP client.

Blocking (requests): call through a worker thread.
"""

import logging
import time
from typing import Any
from urllib.parse import quote

import requests

from app.core.config import settings
from app.services import keycloak_admin

logger = logging.getLogger(__name__)

TIMEOUT = 10
GATEWAY_AUDIENCE = "ai-gateway"
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
TOKEN_LIFESPAN = "300"
# Realm scope of the gateway (audience, project and agent claims), and the
# group whose attribute names the project of a service account.
BASE_SCOPES = ("basic", "ai-gateway")
PROJECTS_GROUP = "ai-projects"
PROJECT_ATTRIBUTE = "cnp_ai_project"
# Realm scopes the gateway policy may also check; attached when they exist.
WRITE_SCOPE = "mcp-write"
READ_SCOPE = "mcp-read"
WEB_SEARCH_SCOPE = "web-search"

_admin_cache: dict[str, Any] = {"token": None, "until": 0.0}
_client_tokens: dict[str, tuple[str, float]] = {}


class IdentityError(RuntimeError):
    """Keycloak refused or could not be reached; ``status`` is HTTP."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _admin_token() -> str:
    if _admin_cache["token"] and time.monotonic() < _admin_cache["until"]:
        return _admin_cache["token"]
    try:
        response = requests.post(
            keycloak_admin.master_token_url(),
            data=keycloak_admin.master_token_form(),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise IdentityError(502, f"Keycloak unreachable: {exc}") from exc
    if response.status_code >= 400:
        raise IdentityError(
            response.status_code, "Keycloak admin login failed"
        )
    data = response.json()
    _admin_cache["token"] = data["access_token"]
    _admin_cache["until"] = time.monotonic() + max(
        int(data.get("expires_in", 60)) - 15, 0
    )
    return data["access_token"]


def _admin(method: str, path: str, json: Any = None) -> Any:
    url = (
        f"{settings.KEYCLOAK_URL}/admin/realms/"
        f"{quote(settings.KEYCLOAK_REALM, safe='')}{path}"
    )
    try:
        response = requests.request(
            method,
            url,
            json=json,
            headers={"Authorization": f"Bearer {_admin_token()}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise IdentityError(502, f"{method} {path}: {exc}") from exc
    if response.status_code >= 400:
        raise IdentityError(
            response.status_code,
            f"{method} {path}: {response.status_code} {response.text[:200]}",
        )
    return response.json() if response.content else None


def _find(client_id: str) -> dict | None:
    found = _admin("GET", f"/clients?clientId={quote(client_id, safe='')}")
    return next((c for c in found or [] if c["clientId"] == client_id), None)


def _scope_ids() -> dict[str, str]:
    return {s["name"]: s["id"] for s in _admin("GET", "/client-scopes") or []}


def _claim(name: str, value: str) -> dict:
    return {
        "name": name,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-hardcoded-claim-mapper",
        "config": {
            "claim.name": name,
            "claim.value": value,
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }


def _mappers(client_id: str, project: str) -> list[dict]:
    """
    The realm scope ``ai-gateway`` derives ``project`` and ``agent`` from
    the service account's group: right for client credentials, empty for a
    test chat token, whose subject is the user. The same values hardcoded on
    the client keep both kinds of token alike.
    """
    mappers = [
        {
            "name": "aud-ai-gateway",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {
                "included.client.audience": GATEWAY_AUDIENCE,
                "access.token.claim": "true",
                "id.token.claim": "false",
            },
        },
        _claim("project", project),
    ]
    if client_id.startswith("agent-"):
        mappers.append(_claim("agent", f"service-account-{client_id}"))
    return mappers


def scopes_for(tools: list[str], catalogue: dict[str, list[str]]) -> list[str]:
    wanted = []
    if any(t != "web_search" for t in tools):
        wanted.append(READ_SCOPE)
    if any("writes" in catalogue.get(t, []) for t in tools):
        wanted.append(WRITE_SCOPE)
    if "web_search" in tools:
        wanted.append(WEB_SEARCH_SCOPE)
    return wanted


def ensure_client(
    client_id: str, project: str, description: str, scopes: list[str]
) -> str:
    """
    Create the confidential client if missing, enabled, with the gateway
    audience, the project claim and *scopes* (those the realm has). Returns
    Keycloak's internal id.
    """
    existing = _find(client_id)
    if existing is None:
        _admin(
            "POST",
            "/clients",
            {
                "clientId": client_id,
                "name": client_id,
                "description": description[:255],
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "attributes": {
                    "standard.token.exchange.enabled": "true",
                    "access.token.lifespan": TOKEN_LIFESPAN,
                },
                "protocolMappers": _mappers(client_id, project),
            },
        )
        existing = _find(client_id)
        if existing is None:
            raise IdentityError(502, f"Client {client_id} was not created")
    elif not existing.get("enabled", True):
        set_enabled(client_id, True)
    available = _scope_ids()
    wanted = [*BASE_SCOPES, *scopes]
    missing = [s for s in wanted if s not in available]
    if missing:
        logger.warning(
            "Realm scopes %s missing: %s gets the others only",
            missing,
            client_id,
        )
    for scope in wanted:
        if scope in available:
            _admin(
                "PUT",
                f"/clients/{existing['id']}/default-client-scopes/"
                f"{available[scope]}",
            )
    _join_project_group(existing["id"], project)
    return existing["id"]


def _group_or_create() -> dict:
    try:
        return _admin("GET", f"/group-by-path/{PROJECTS_GROUP}")
    except IdentityError as exc:
        if exc.status != 404:
            raise
    _admin("POST", "/groups", {"name": PROJECTS_GROUP})
    return _admin("GET", f"/group-by-path/{PROJECTS_GROUP}")


def _join_project_group(internal_id: str, project: str) -> None:
    """The service account in ``/ai-projects/<project>``, whose attribute
    the gateway's ``project`` claim reads."""
    try:
        group = _admin("GET", f"/group-by-path/{PROJECTS_GROUP}/{project}")
    except IdentityError as exc:
        if exc.status != 404:
            raise
        parent = _group_or_create()
        _admin(
            "POST",
            f"/groups/{parent['id']}/children",
            {"name": project, "attributes": {PROJECT_ATTRIBUTE: [project]}},
        )
        group = _admin("GET", f"/group-by-path/{PROJECTS_GROUP}/{project}")
    user = _admin("GET", f"/clients/{internal_id}/service-account-user")
    _admin("PUT", f"/users/{user['id']}/groups/{group['id']}")


def client_secret(client_id: str) -> str:
    found = _find(client_id)
    if found is None:
        raise IdentityError(404, f"Client {client_id} does not exist")
    return _admin("GET", f"/clients/{found['id']}/client-secret")["value"]


def is_enabled(client_id: str) -> bool | None:
    found = _find(client_id)
    return None if found is None else bool(found.get("enabled", True))


def set_enabled(client_id: str, enabled: bool) -> None:
    """Disabling stops new tokens at once; issued ones last 5 minutes."""
    found = _find(client_id)
    if found is None:
        raise IdentityError(404, f"Client {client_id} does not exist")
    _admin("PUT", f"/clients/{found['id']}", {**found, "enabled": enabled})
    _client_tokens.pop(client_id, None)


def delete_client(client_id: str) -> None:
    found = _find(client_id)
    if found is not None:
        _admin("DELETE", f"/clients/{found['id']}")
    _client_tokens.pop(client_id, None)


# ---------------------------------------------------------------------------
# Test chat audience: the CMP client may ask for a token meant for the agent
# ---------------------------------------------------------------------------


def test_scope(agent_client: str) -> str:
    return f"aud-{agent_client}"


def ensure_test_audience(agent_client: str) -> None:
    """An optional scope of the CMP client carrying the agent's audience."""
    name = test_scope(agent_client)
    scopes = _scope_ids()
    if name not in scopes:
        _admin(
            "POST",
            "/client-scopes",
            {
                "name": name,
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "false"},
                "protocolMappers": [
                    {
                        "name": "aud",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": agent_client,
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ],
            },
        )
        scopes = _scope_ids()
    cmp = _find(settings.KEYCLOAK_CLIENT_ID)
    if cmp is None:
        raise IdentityError(
            404, f"CMP client {settings.KEYCLOAK_CLIENT_ID} not found"
        )
    _admin(
        "PUT",
        f"/clients/{cmp['id']}/optional-client-scopes/{scopes[name]}",
    )


def drop_test_audience(agent_client: str) -> None:
    scope = _scope_ids().get(test_scope(agent_client))
    if scope is None:
        return
    cmp = _find(settings.KEYCLOAK_CLIENT_ID)
    if cmp is not None:
        try:
            _admin(
                "DELETE",
                f"/clients/{cmp['id']}/optional-client-scopes/{scope}",
            )
        except IdentityError as exc:
            if exc.status != 404:
                raise
    _admin("DELETE", f"/client-scopes/{scope}")


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _token_url() -> str:
    return (
        f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}"
        "/protocol/openid-connect/token"
    )


def _grant(form: dict[str, str]) -> dict:
    try:
        response = requests.post(_token_url(), data=form, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise IdentityError(502, f"Keycloak unreachable: {exc}") from exc
    if response.status_code >= 400:
        try:
            reason = response.json().get("error_description") or ""
        except ValueError:
            reason = response.text[:200]
        status = 403 if response.status_code in (400, 401, 403) else 502
        raise IdentityError(status, f"Token refused by Keycloak: {reason}")
    return response.json()


def exchange_for_agent(user_token: str, agent_client: str) -> str:
    """A gateway token with azp = the agent and sub = the user."""
    first = _grant(
        {
            "grant_type": TOKEN_EXCHANGE,
            "client_id": settings.KEYCLOAK_CLIENT_ID,
            "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
            "subject_token": user_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "audience": agent_client,
            "scope": test_scope(agent_client),
        }
    )
    second = _grant(
        {
            "grant_type": TOKEN_EXCHANGE,
            "client_id": agent_client,
            "client_secret": client_secret(agent_client),
            "subject_token": first["access_token"],
            "subject_token_type": ACCESS_TOKEN_TYPE,
            # No audience: the agent's own ones (the gateway, and the
            # sandbox runner when it has run_python), as its tokens carry.
        }
    )
    return second["access_token"]


def cmp_gateway_token() -> str:
    """The CMP's own gateway client (``cmp-ai``, created with the gateway),
    its secret from Vault through the CMP's environment."""
    if not settings.AI_CMP_CLIENT_SECRET:
        raise IdentityError(
            503, "The CMP's AI gateway client is not configured."
        )
    cached = _client_tokens.get(settings.AI_CMP_CLIENT_ID)
    if cached and time.monotonic() < cached[1]:
        return cached[0]
    data = _grant(
        {
            "grant_type": "client_credentials",
            "client_id": settings.AI_CMP_CLIENT_ID,
            "client_secret": settings.AI_CMP_CLIENT_SECRET,
        }
    )
    _client_tokens[settings.AI_CMP_CLIENT_ID] = (
        data["access_token"],
        time.monotonic() + max(int(data.get("expires_in", 60)) - 30, 0),
    )
    return data["access_token"]
