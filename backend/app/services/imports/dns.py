"""
Public hostname of an imported app: one CNAME to the project's tunnel.

Same record the k3s-gitops-app module creates for template apps (name
<app>-<project>, proxied, to <tunnel id>.cfargotunnel.com), made with the
Cloudflare API rather than a Terraform run per app (D9). Its marker names the
app as well as the project: a record of a template app or of another import
carries another marker and is never adopted, patched or deleted from here.
The tunnel's routing table comes from the registry record's hostnames,
rendered by cnp-project-base: nothing here touches it.
"""

import httpx

from app.core.config import settings

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 15.0


class DnsError(Exception):
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
        "User-Agent": "cmp-backend-import/1.0",
    }


def _configured() -> None:
    if not (
        settings.CLOUDFLARE_API_TOKEN
        and settings.CLOUDFLARE_ZONE_ID
        and settings.CLOUDFLARE_ACCOUNT_ID
    ):
        raise DnsError("Cloudflare is not configured on the CMP")


async def _call(client: httpx.AsyncClient, method: str, path: str, **kw):
    response = await client.request(
        method, f"{CLOUDFLARE_API}{path}", headers=_headers(), **kw
    )
    if response.status_code >= 400:
        raise DnsError(f"Cloudflare {method} {path}: {response.status_code}")
    return response.json().get("result")


def marker(project: str, app: str) -> str:
    # Starts like the D-13 marker so cnp-clean finds it; at most 92
    # characters (project and app fit a 63-character namespace), under the
    # 100 Cloudflare keeps on every plan.
    return f"cnp.project={project} cnp.app={app} cnp.env=prod"


async def ensure_cname(project: str, app: str, hostname: str) -> None:
    """
    Raises:
        DnsError: Cloudflare unreachable, the project has no tunnel, or the
            name is held by a record that is not this app's.
    """
    _configured()
    owned = marker(project, app)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        tunnels = await _call(
            client,
            "GET",
            f"/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel",
            params={"name": f"cnp-{project}-tunnel", "is_deleted": "false"},
        )
        if not tunnels:
            raise DnsError(f"project '{project}' has no Cloudflare tunnel")
        target = f"{tunnels[0]['id']}.cfargotunnel.com"
        records = await _call(
            client,
            "GET",
            f"/zones/{settings.CLOUDFLARE_ZONE_ID}/dns_records",
            params={"name": hostname},
        )
        body = {
            "type": "CNAME",
            "name": f"{app}-{project}",
            "content": target,
            "proxied": True,
            "ttl": 1,
            "comment": owned,
        }
        if not records:
            await _call(
                client,
                "POST",
                f"/zones/{settings.CLOUDFLARE_ZONE_ID}/dns_records",
                json=body,
            )
            return
        if len(records) != 1 or records[0].get("comment") != owned:
            raise DnsError(
                f"{hostname} already exists and does not belong to app "
                f"'{app}' of project '{project}'"
            )
        record = records[0]
        if (
            record.get("type") != "CNAME"
            or record.get("content") != target
            or not record.get("proxied")
        ):
            await _call(
                client,
                "PATCH",
                f"/zones/{settings.CLOUDFLARE_ZONE_ID}/dns_records/"
                f"{record['id']}",
                json=body,
            )


async def remove_cname(project: str, app: str, hostname: str) -> None:
    """Delete the record if it carries this app's marker."""
    _configured()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        records = await _call(
            client,
            "GET",
            f"/zones/{settings.CLOUDFLARE_ZONE_ID}/dns_records",
            params={"name": hostname},
        )
        for record in records or []:
            if record.get("comment") == marker(project, app):
                await _call(
                    client,
                    "DELETE",
                    f"/zones/{settings.CLOUDFLARE_ZONE_ID}/dns_records/"
                    f"{record['id']}",
                )
