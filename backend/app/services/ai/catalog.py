"""
What an agent can be built from: the templates of app-templates and the
tools of the platform and of the project's apps.

Both are read from Git and cached a few minutes. A project app exposes MCP
tools by declaring them in its values (``mcp: {enabled: true, tools: [...]}``);
the gateway registers them on ``/mcp/<project>`` as ``<app>__<tool>``.
"""

import asyncio
import logging
import time
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from app.core.config import settings
from app.services.ai import declaration
from app.services.app_security_data import (
    BACKEND_VALUES_FILE,
    SINGLE_VALUES_FILE,
)
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    get_file_content,
    get_org_installation_token,
    list_directory,
)

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300
PLATFORM_TOOLS: dict[str, dict[str, Any]] = {
    "web_search": {
        "description": "Recherche web (outil serveur d'Anthropic).",
        "badges": ["untrusted_content", "external_send"],
    },
    "run_python": {
        "description": "Exécute du Python dans un bac à sable gVisor, avec "
        "les outils de l'agent importables (code mode).",
        "badges": ["executes_code"],
    },
}
WRITE_VERBS = (
    "reply",
    "post",
    "create",
    "update",
    "delete",
    "write",
    "send",
    "publish",
    "set",
    "add",
    "remove",
)

_cache: dict[str, tuple[float, Any]] = {}
_lock = asyncio.Lock()


def _cached(key: str) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    return None


def _load_yaml(text: str) -> Any:
    try:
        return YAML(typ="safe").load(text)
    except YAMLError:
        return None


def github_repo(repo_url: str | None) -> str | None:
    """``owner/name`` of a repository in the platform org, else None."""
    prefix = f"https://github.com/{settings.GITHUB_ORG}/".lower()
    url = (repo_url or "").strip().rstrip("/").removesuffix(".git")
    if not url.lower().startswith(prefix):
        return None
    name = url[len(prefix) :]
    if not name or "/" in name:
        return None
    return f"{settings.GITHUB_ORG}/{name}"


def values_file_of(app: dict) -> str:
    kind = app.get("type")
    if kind == "static":
        return SINGLE_VALUES_FILE
    if kind == "fullstack":
        return BACKEND_VALUES_FILE
    return declaration.values_path(app["name"])


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _with_recommended(defaults: Any, recommended: Any) -> dict:
    """An agent: block, plus the rights the template recommends."""
    block = dict(defaults or {})
    recommended = recommended if isinstance(recommended, dict) else {}
    for key in ("models", "isolation", "dailyTokenBudget"):
        if key in recommended:
            block[key] = recommended[key]
    return block


def _template(raw: Any) -> dict | None:
    """app-templates agents/<id>.yaml (format: agents/README.md there). In a
    pair the reader is the template itself and the actor has its own block."""
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    defaults = _with_recommended(raw.get("defaults"), raw.get("recommended"))
    pair = raw.get("pair")
    actor = (pair.get("actor") or {}) if isinstance(pair, dict) else {}
    return {
        "id": str(raw["id"]),
        "title": str(raw.get("title") or raw["id"]),
        "description": str(raw.get("description") or ""),
        "icon": str(raw.get("icon") or "bot"),
        "recommended": dict(raw.get("recommended") or {}),
        "defaults": defaults,
        "suggested_tools": list(raw.get("suggestedTools") or []),
        "risk_notes": list(raw.get("riskNotes") or []),
        "pair": (
            {
                "reader": defaults,
                "actor": _with_recommended(
                    actor.get("defaults"), actor.get("recommended")
                ),
                "actor_suggested_tools": list(
                    actor.get("suggestedTools") or []
                ),
            }
            if isinstance(pair, dict)
            else None
        ),
    }


async def templates() -> list[dict]:
    """
    The agent templates, ordered by id. Empty while app-templates has none.

    Raises:
        GitHubAppError: GitHub could not be read.
    """
    cached = _cached("templates")
    if cached is not None:
        return cached
    async with _lock:
        token = await get_org_installation_token()
        repo = f"{settings.GITHUB_ORG}/{settings.AI_TEMPLATES_REPO}"
        path = settings.AI_TEMPLATES_PATH.strip("/")
        found = []
        for name in await list_directory(token, repo, path):
            if not name.endswith((".yaml", ".yml")):
                continue
            text, _ = await get_file_content(token, repo, f"{path}/{name}")
            item = _template(_load_yaml(text))
            if item is None:
                logger.warning("Skipping invalid agent template %s", name)
                continue
            found.append(item)
        found.sort(key=lambda t: t["id"])
        _cache["templates"] = (time.monotonic(), found)
        return found


async def template(template_id: str) -> dict | None:
    return next((t for t in await templates() if t["id"] == template_id), None)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _badges(tool: Any, private: bool) -> tuple[str, str, list[str]]:
    if isinstance(tool, dict):
        name = str(tool.get("name") or "")
        description = str(tool.get("description") or "")
        given = tool.get("badges")
        if isinstance(given, list):
            return (
                name,
                description,
                [b for b in given if b in declaration.BADGES],
            )
    else:
        name, description = str(tool), ""
    if name.split("_", 1)[0].lower() in WRITE_VERBS:
        return name, description, ["writes", "external_send"]
    return (
        name,
        description,
        ["private_data" if private else "untrusted_content"],
    )


def mcp_tools(app_name: str, values: Any) -> list[dict]:
    """The MCP tools an app's values declare, as the gateway names them."""
    block = values.get("mcp") if isinstance(values, dict) else None
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return []
    private = block.get("data") == "private"
    tools = []
    for raw in block.get("tools") or []:
        name, description, badges = _badges(raw, private)
        if name:
            tools.append(
                {
                    "name": f"{app_name}__{name}",
                    "source": app_name,
                    "description": description,
                    "badges": badges,
                }
            )
    return tools


async def _app_values(token: str, project: str, app: dict) -> Any:
    """The app's values: in its repository, or in cnp-projects while it
    has none (imported apps before their switch, mcp apps)."""
    repo = github_repo(app.get("repoURL"))
    path = values_file_of(app)
    if repo is None:
        repo = settings.CNP_REGISTRY_REPO
        path = f"apps/{project}/{app['name']}/values.yaml"
    key = f"values:{repo}:{path}"
    cached = _cached(key)
    if cached is not None:
        return cached
    try:
        text, _ = await get_file_content(token, repo, path)
    except FileNotInRepoError:
        text = ""
    except GitHubAppError as exc:
        logger.warning("Values of %s unreadable: %s", app.get("name"), exc)
        return None
    values = _load_yaml(text) or {}
    _cache[key] = (time.monotonic(), values)
    return values


async def tools(record: dict) -> list[dict]:
    """Every tool an agent of the project may be given, with its badges."""
    found = [
        {"name": name, "source": "platform", **about}
        for name, about in PLATFORM_TOOLS.items()
    ]
    apps = [
        a for a in declaration.record_apps(record) if a.get("type") != "agent"
    ]
    if not apps:
        return found
    token = await get_org_installation_token()
    project = (record.get("metadata") or {}).get("name", "")
    for app in apps:
        found.extend(
            mcp_tools(app["name"], await _app_values(token, project, app))
        )
    return found


def badges_by_name(items: list[dict]) -> dict[str, list[str]]:
    return {t["name"]: list(t["badges"]) for t in items}
