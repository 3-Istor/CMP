"""
Step-up authentication for destructive actions (CNP-124).

A stepped-up token has ``acr == STEP_UP_ACR`` (Keycloak LoA 2, a passkey) and
an ``auth_time`` at most *max_age* seconds old. Otherwise the API answers 401
with an RFC 9470 challenge (``WWW-Authenticate: Bearer
error="insufficient_user_authentication", acr_values=..., max_age=...``) and
``detail.code == "step_up_required"``; the frontend logs in again with those
``acr_values`` and ``max_age`` (``frontend/src/lib/stepUp.ts``).
"""

import time
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.services.keycloak_service import get_current_user

STEP_UP_REQUIRED = "step_up_required"
STEP_UP_UNAVAILABLE_FOR_AGENTS = "step_up_unavailable_for_agents"


def _challenge(max_age: int) -> HTTPException:
    acr = settings.STEP_UP_ACR
    message = "This action needs a fresh login with a passkey."
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": STEP_UP_REQUIRED,
            "acr_values": acr,
            "max_age": max_age,
            "message": message,
        },
        headers={
            "WWW-Authenticate": (
                'Bearer error="insufficient_user_authentication", '
                f'error_description="{message}", '
                f'acr_values="{acr}", max_age={max_age}'
            )
        },
    )


def is_stepped_up(token: dict, max_age: int, now: float | None = None) -> bool:
    """Whether *token* proves a passkey login at most *max_age* seconds old."""
    if token.get("acr") != settings.STEP_UP_ACR:
        return False
    auth_time = token.get("auth_time")
    if not isinstance(auth_time, (int, float)) or isinstance(auth_time, bool):
        return False
    current = time.time() if now is None else now
    # A future auth_time means a clock problem, not a fresh login.
    return 0 <= current - auth_time <= max_age


def assert_step_up(
    token: dict,
    max_age: int | None = None,
    *,
    agents_allowed: bool = False,
) -> None:
    """
    Raise unless *token* is stepped up. *max_age* defaults to
    ``STEP_UP_MAX_AGE``.

    Raises:
        HTTPException 401: step-up needed, with the challenge described above.
        HTTPException 403: an assistant token on a route that refuses them.
    """
    if not settings.STEP_UP_ENABLED:
        return
    if "cnp_agent" in token:
        if agents_allowed:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": STEP_UP_UNAVAILABLE_FOR_AGENTS,
                "message": "This action needs a passkey: do it in the CMP.",
            },
        )
    limit = settings.STEP_UP_MAX_AGE if max_age is None else max_age
    if not is_stepped_up(token, limit):
        raise _challenge(limit)


def require_step_up(
    max_age: int | None = None, *, agents_allowed: bool = False
) -> Callable[..., dict]:
    """
    FastAPI dependency factory: the verified claims, once stepped up.

        @router.post("/x", dependencies=[Depends(require_step_up(120))])
    """

    def _dep(token: Annotated[dict, Depends(get_current_user)]) -> dict:
        assert_step_up(token, max_age, agents_allowed=agents_allowed)
        return token

    return _dep
