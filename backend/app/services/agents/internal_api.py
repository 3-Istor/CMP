"""
The CMP's own REST API, called in-process with the assistant's token.

Tools go through the same endpoints as the web UI, so RBAC, validation and
the audit journal apply to assistants without being written twice.
"""

from typing import Any

import httpx
from starlette.types import ASGIApp

_app: ASGIApp | None = None


def bind(app: ASGIApp) -> None:
    global _app  # pylint: disable=global-statement
    _app = app


class ApiError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


async def call(
    token: str,
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict | None = None,
) -> Any:
    if _app is None:
        raise RuntimeError("internal_api.bind() was not called")
    transport = httpx.ASGITransport(app=_app, client=("agent", 0))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://cmp.internal",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    ) as client:
        response = await client.request(method, path, json=json, params=params)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ApiError(response.status_code, detail)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()
