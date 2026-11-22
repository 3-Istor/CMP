"""
The platform side of hosted agents: their traces at the gateway and the
registry of MCP servers.

Traces: the gateway exports one trace per call to the platform tenant, with
``cnp.project`` and ``cnp.agent`` attributes (message bodies dropped).

MCP servers: the platform's own (``run-python``) and every project app whose
values declare ``mcp: {enabled: true}``. Approving or retiring one is a pull
request on ``registry/mcp-servers.yaml`` of cnp-projects, reviewed by a
person.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from app.core.config import settings
from app.services import project_registry
from app.services.ai import catalog, gitops
from app.services.grafana_service import GRAFANA_BASE_URL
from app.services.observability import app_traces, stores

TRACE_WINDOW = timedelta(days=7)
MCP_REGISTRY = "registry/mcp-servers.yaml"
PLATFORM_SERVERS = {"run-python": ["run_python"]}
STATUSES = ("approved", "pending", "retired")


def grafana_url(project: str, agent: str, trace_id: str | None = None) -> str:
    query: dict[str, Any] = {
        "refId": "A",
        "datasource": {"uid": settings.AI_GRAFANA_TRACES_DATASOURCE},
    }
    if trace_id:
        query.update(queryType="traceql", query=trace_id)
    else:
        query.update(
            queryType="search",
            service=settings.AI_TRACES_SERVICE,
            tags=f"cnp.project={project} cnp.agent={agent}",
        )
    pane = {
        "datasource": settings.AI_GRAFANA_TRACES_DATASOURCE,
        "queries": [query],
        "range": {"from": "now-24h", "to": "now"},
    }
    return f"{GRAFANA_BASE_URL}/explore?schemaVersion=1&panes=" + quote(
        json.dumps({"ai": pane}, separators=(",", ":"))
    )


async def agent_traces(project: str, agent: str, limit: int) -> dict:
    now = datetime.now(timezone.utc)
    params = {
        "service": settings.AI_TRACES_SERVICE,
        "tags": json.dumps({"cnp.project": project, "cnp.agent": agent}),
        "start": int((now - TRACE_WINDOW).timestamp() * 1e6),
        "end": int(now.timestamp() * 1e6),
        "limit": limit,
    }
    try:
        body = await stores.jaeger(
            settings.AI_TRACES_ACCOUNT, "traces", params
        )
    except stores.NotAvailableError:
        return {
            "available": False,
            "items": [],
            "grafana_url": grafana_url(project, agent),
        }
    rows = [
        {
            **app_traces.summary(trace),
            "grafana_url": grafana_url(project, agent, trace.get("traceID")),
        }
        for trace in (body or {}).get("data") or []
        if trace.get("spans")
    ]
    rows.sort(key=lambda r: r["start"], reverse=True)
    return {
        "available": True,
        "items": rows[:limit],
        "grafana_url": grafana_url(project, agent),
    }


async def _decisions() -> dict[tuple[str | None, str], str]:
    values = await gitops.read_values(settings.CNP_REGISTRY_REPO, MCP_REGISTRY)
    found = {}
    for entry in (values or {}).get("servers") or []:
        if isinstance(entry, dict) and entry.get("status") in STATUSES:
            found[(entry.get("project"), str(entry.get("name")))] = entry[
                "status"
            ]
    return found


async def mcp_servers() -> list[dict]:
    decisions = await _decisions()
    servers = [
        {
            "name": name,
            "project": None,
            "source": "platform",
            "tools": [
                {"name": t, "badges": catalog.PLATFORM_TOOLS[t]["badges"]}
                for t in tools
            ],
            "status": decisions.get((None, name), "approved"),
        }
        for name, tools in PLATFORM_SERVERS.items()
    ]
    for record in await project_registry.list_records():
        project = (record.get("metadata") or {}).get("name")
        by_app: dict[str, list[dict]] = {}
        for tool in await catalog.tools(record):
            if tool["source"] != "platform":
                by_app.setdefault(tool["source"], []).append(
                    {"name": tool["name"], "badges": tool["badges"]}
                )
        servers.extend(
            {
                "name": app,
                "project": project,
                "source": "app",
                "tools": tools,
                "status": decisions.get((project, app), "pending"),
            }
            for app, tools in sorted(by_app.items())
        )
    return servers


async def decide_server(
    name: str, project: str | None, status: str, username: str
) -> dict:
    """Open the pull request that records the decision on a server."""
    repo = settings.CNP_REGISTRY_REPO
    current = await gitops.read_values(repo, MCP_REGISTRY) or {}
    servers = [
        s
        for s in current.get("servers") or []
        if not (s.get("name") == name and s.get("project") == project)
    ]
    servers.append(
        {
            "name": name,
            "project": project,
            "status": status,
            "decidedBy": username,
        }
    )
    servers.sort(key=lambda s: (s.get("project") or "", s["name"]))
    text = gitops.values_with(current, "servers", servers)
    return await gitops.open_pr(
        repo,
        {MCP_REGISTRY: text},
        f"mcp-{name}",
        f"feat(registry): {status} MCP server {name}",
        f"{username} marks the MCP server `{name}`"
        f"{f' of project `{project}`' if project else ''} as {status}.",
    )
