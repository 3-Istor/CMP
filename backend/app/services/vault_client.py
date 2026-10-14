"""Minimal Vault KV v2 client, with the CMP's own token."""

from typing import Any

import httpx

from app.core.config import settings

REQUEST_TIMEOUT = 10.0


class VaultError(Exception):
    pass


def _url(mount: str, path: str) -> str:
    if not settings.VAULT_URL or not settings.VAULT_TOKEN:
        raise VaultError("Vault is not configured on the CMP.")
    return f"{settings.VAULT_URL}/v1/{mount}/data/{path}"


async def read_kv(mount: str, path: str) -> dict[str, Any]:
    """The secret's data, or an empty dict when it does not exist yet."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                _url(mount, path),
                headers={"X-Vault-Token": settings.VAULT_TOKEN},
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
                headers={"X-Vault-Token": settings.VAULT_TOKEN},
                json={"data": data},
            )
    except httpx.HTTPError as exc:
        raise VaultError(f"Vault unreachable: {exc}") from exc
    if response.status_code not in (200, 204):
        raise VaultError(f"Vault write failed ({response.status_code})")
