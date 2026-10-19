"""
The platform's skills, served to assistants over MCP.

The single source is the cnp-agent-kit repository, also installed as a
Claude Code plugin. Assistants without the plugin (claude.ai, ChatGPT) get
the same guides here: names and descriptions up front, the body only when a
task needs it (progressive disclosure).
"""

import asyncio
import logging
import posixpath
import time
from dataclasses import dataclass

from ruamel.yaml import YAML

from app.core.config import settings
from app.services.agents import repo as repo_ops
from app.services.github_service import (
    GitHubAppError,
    get_file_content,
    get_installation_token,
)

logger = logging.getLogger(__name__)

CACHE_SECONDS = 600


@dataclass
class Skill:
    name: str
    description: str
    files: list[str]


_cache: dict = {"at": 0.0, "skills": {}, "files": {}}
_lock = asyncio.Lock()


def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return YAML(typ="safe").load(text[3:end]) or {}


async def _refresh() -> None:
    token = await get_installation_token(settings.GITHUB_INSTALLATION_ID)
    root = settings.AGENT_KIT_SKILLS_PATH.strip("/")
    paths = await repo_ops.list_files(
        token, settings.AGENT_KIT_REPO, settings.AGENT_KIT_BRANCH
    )
    skills: dict[str, Skill] = {}
    files: dict[str, str] = {}
    for path in paths:
        if not path.startswith(root + "/") or not path.endswith(".md"):
            continue
        relative = path[len(root) + 1 :]
        skill_dir = relative.split("/", 1)[0]
        content, _ = await get_file_content(
            token,
            settings.AGENT_KIT_REPO,
            path,
            ref=settings.AGENT_KIT_BRANCH,
        )
        files[relative] = content
        if posixpath.basename(relative) == "SKILL.md" and "/" in relative:
            meta = _front_matter(content)
            skills[skill_dir] = Skill(
                name=meta.get("name", skill_dir),
                description=" ".join(str(meta.get("description", "")).split()),
                files=[],
            )
    for relative in files:
        skill_dir = relative.split("/", 1)[0]
        if skill_dir in skills:
            skills[skill_dir].files.append(relative.split("/", 1)[1])
    _cache.update(at=time.monotonic(), skills=skills, files=files)


async def _ensure_fresh() -> None:
    if time.monotonic() - _cache["at"] < CACHE_SECONDS and _cache["skills"]:
        return
    async with _lock:
        if (
            time.monotonic() - _cache["at"] < CACHE_SECONDS
            and _cache["skills"]
        ):
            return
        try:
            await _refresh()
        except (GitHubAppError, OSError, ValueError) as exc:
            # Keep serving what we had: a GitHub hiccup must not take the
            # guides away mid-conversation.
            logger.warning("Agent kit skills not refreshed: %s", exc)
            _cache["at"] = time.monotonic() - CACHE_SECONDS + 60


async def list_skills() -> list[Skill]:
    await _ensure_fresh()
    return sorted(_cache["skills"].values(), key=lambda s: s.name)


async def read(skill: str, file: str = "SKILL.md") -> str | None:
    await _ensure_fresh()
    clean = posixpath.normpath(f"{skill}/{file}")
    if clean.startswith(".."):
        return None
    return _cache["files"].get(clean)
