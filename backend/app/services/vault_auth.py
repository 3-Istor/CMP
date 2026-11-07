"""
The CMP's Vault token.

With ``VAULT_AUTH=kubernetes`` the backend logs in to the Kubernetes auth
method with its ServiceAccount JWT, keeps the token it gets in memory and
renews it before it expires. Otherwise the static ``VAULT_TOKEN`` is used, as
before.
"""

import logging
import threading
import time
from pathlib import Path

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
# Renewed once less than this share of the lease is left.
RENEW_FRACTION = 1 / 3
# A Terraform run must not outlive its token: the provider uses it directly
# (TERRAFORM_VAULT_SKIP_CHILD_TOKEN) and cannot renew it.
TERRAFORM_MIN_TTL = 45 * 60


class VaultAuthError(RuntimeError):
    """The CMP could not get a Vault token. Never carries a token."""


class _Lease:
    def __init__(self, token: str, ttl: float, expires_at: float) -> None:
        self.token = token
        self.ttl = ttl
        self.expires_at = expires_at


_lock = threading.Lock()
_lease: _Lease | None = None


def uses_kubernetes() -> bool:
    return settings.VAULT_AUTH.strip().lower() == "kubernetes"


def is_configured() -> bool:
    if not settings.VAULT_URL:
        return False
    return uses_kubernetes() or bool(settings.VAULT_TOKEN)


def token(min_ttl: float = 0) -> str:
    """A Vault token still valid for at least ``min_ttl`` seconds."""
    if not uses_kubernetes():
        if not settings.VAULT_TOKEN:
            raise VaultAuthError("VAULT_TOKEN is not set")
        return settings.VAULT_TOKEN

    global _lease  # pylint: disable=global-statement
    with _lock:
        now = time.monotonic()
        if _lease is not None and now < _lease.expires_at:
            threshold = max(min_ttl, _lease.ttl * RENEW_FRACTION)
            if _lease.expires_at - now > threshold:
                return _lease.token
            duration = _renew(_lease.token)
            if duration is not None and duration > threshold:
                _lease.expires_at = now + duration
                return _lease.token
        _lease = _login(now)
        if _lease.ttl < min_ttl:
            raise VaultAuthError(
                f"Vault role '{settings.VAULT_KUBERNETES_ROLE}' issues "
                f"{int(_lease.ttl)}s tokens, {int(min_ttl)}s are needed"
            )
        return _lease.token


def describe() -> str:
    if not settings.VAULT_URL:
        return "not configured"
    if uses_kubernetes():
        return (
            f"kubernetes auth, role '{settings.VAULT_KUBERNETES_ROLE}' "
            f"on mount '{settings.VAULT_KUBERNETES_MOUNT}'"
        )
    return "static VAULT_TOKEN" if settings.VAULT_TOKEN else "no token"


def reset() -> None:
    global _lease  # pylint: disable=global-statement
    with _lock:
        _lease = None


def _post(path: str, **kwargs) -> requests.Response:
    if not settings.VAULT_URL:
        raise VaultAuthError("VAULT_URL is not set")
    try:
        return requests.post(
            f"{settings.VAULT_URL}/v1/{path}",
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise VaultAuthError(f"Vault unreachable: {exc}") from exc


def _login(now: float) -> _Lease:
    try:
        jwt = (
            Path(settings.VAULT_KUBERNETES_JWT_PATH)
            .read_text(encoding="utf-8")
            .strip()
        )
    except OSError as exc:
        raise VaultAuthError(
            f"cannot read the ServiceAccount token: {exc.strerror}"
        ) from exc
    response = _post(
        f"auth/{settings.VAULT_KUBERNETES_MOUNT}/login",
        json={"role": settings.VAULT_KUBERNETES_ROLE, "jwt": jwt},
    )
    if response.status_code != 200:
        raise VaultAuthError(
            f"Vault Kubernetes login as role "
            f"'{settings.VAULT_KUBERNETES_ROLE}' failed "
            f"({response.status_code})"
        )
    auth = response.json().get("auth") or {}
    ttl = float(auth.get("lease_duration") or 0)
    if not auth.get("client_token") or ttl <= 0:
        raise VaultAuthError("Vault Kubernetes login returned no token")
    if ttl < TERRAFORM_MIN_TTL:
        logger.warning(
            "Vault role '%s' issues %ds tokens: Terraform runs need %ds and "
            "will fail",
            settings.VAULT_KUBERNETES_ROLE,
            ttl,
            TERRAFORM_MIN_TTL,
        )
    return _Lease(auth["client_token"], ttl, now + ttl)


def _renew(current: str) -> float | None:
    """The new lease duration, or None when the token could not be renewed."""
    try:
        response = _post(
            "auth/token/renew-self", headers={"X-Vault-Token": current}
        )
    except VaultAuthError:
        return None
    if response.status_code != 200:
        return None
    duration = (response.json().get("auth") or {}).get("lease_duration")
    return float(duration) if duration else None


def terraform_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if settings.VAULT_URL:
        env["TF_VAR_vault_url"] = settings.VAULT_URL
        env["VAULT_ADDR"] = settings.VAULT_URL
    if not is_configured():
        return env
    vault_token = token(min_ttl=TERRAFORM_MIN_TTL)
    env["TF_VAR_vault_token"] = vault_token
    env["VAULT_TOKEN"] = vault_token
    if uses_kubernetes():
        # The provider would otherwise mint a child token, which needs
        # auth/token/create: the cmp-backend policy does not grant it.
        env["TERRAFORM_VAULT_SKIP_CHILD_TOKEN"] = "true"
    return env
