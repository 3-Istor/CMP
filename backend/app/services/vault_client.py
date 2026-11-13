"""Minimal Vault KV v2 client, with the CMP's own token."""

import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.services import vault_auth

REQUEST_TIMEOUT = 10.0


class VaultError(Exception):
    pass


def _url(mount: str, path: str) -> str:
    if not vault_auth.is_configured():
        raise VaultError("Vault is not configured on the CMP.")
    return f"{settings.VAULT_URL}/v1/{mount}/data/{path}"


async def _headers() -> dict[str, str]:
    try:
        token = await asyncio.to_thread(vault_auth.token)
    except vault_auth.VaultAuthError as exc:
        raise VaultError(str(exc)) from exc
    return {"X-Vault-Token": token}


async def read_kv(mount: str, path: str) -> dict[str, Any]:
    """The secret's data, or an empty dict when it does not exist yet."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                _url(mount, path),
                headers=await _headers(),
            )
    except httpx.HTTPError as exc:
        raise VaultError(f"Vault unreachable: {exc}") from exc
    if response.status_code == 404:
        return {}
    if response.status_code != 200:
        raise VaultError(f"Vault read failed ({response.status_code})")
    return ((response.json().get("data") or {}).get("data")) or {}


async def write_kv(mount: str, path: str, data: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                _url(mount, path),
                headers=await _headers(),
                json={"data": data},
            )
    except httpx.HTTPError as exc:
        raise VaultError(f"Vault unreachable: {exc}") from exc
    if response.status_code not in (200, 204):
        raise VaultError(f"Vault write failed ({response.status_code})")


async def read_key_names(mount: str, path: str) -> list[str]:
    """
    The names of the keys of a secret, never its values.

    KV v2 has no endpoint listing a secret's keys without its data, so the
    response is read here and reduced to ``sorted(data.keys())`` before
    anything leaves this function. Nothing from the response body is logged,
    cached or put in an exception message.
    """
    names: list[str] | None = None
    failure: str | None = None
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                _url(mount, path),
                headers=await _headers(),
            )
        if response.status_code == 404:
            names = []
        elif response.status_code != 200:
            failure = f"Vault read failed ({response.status_code})"
        else:
            data = (response.json().get("data") or {}).get("data") or {}
            names = sorted(str(key) for key in data.keys())
    except httpx.HTTPError:
        failure = "Vault unreachable"
    except (ValueError, AttributeError, TypeError):
        failure = "Vault answered with an unexpected response"
    if names is None:
        raise VaultError(failure or "Vault read failed")
    return names


async def list_paths(mount: str, prefix: str = "") -> list[str]:
    """Every secret path under *prefix* of a KV v2 mount, recursively."""
    if not vault_auth.is_configured():
        raise VaultError("Vault is not configured on the CMP.")
    headers = await _headers()
    paths: list[str] = []
    pending = [prefix]
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        while pending:
            folder = pending.pop()
            try:
                response = await client.request(
                    "LIST",
                    f"{settings.VAULT_URL}/v1/{mount}/metadata/{folder}",
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise VaultError(f"Vault unreachable: {exc}") from exc
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise VaultError(f"Vault list failed ({response.status_code})")
            for key in (response.json().get("data") or {}).get("keys") or []:
                if key.endswith("/"):
                    pending.append(f"{folder}{key}")
                else:
                    paths.append(f"{folder}{key}")
    return sorted(paths)
