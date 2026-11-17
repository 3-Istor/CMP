"""
Open Renovate / Dependabot upgrade PRs, so a CVE can link to its fix.

The portal never opens a PR itself: a link shows only when a bot already did.
"""

import json
import re

from app.services.security import store

_BOT_REFS = ("renovate/", "dependabot/")
_ONBOARDING_REF = "renovate/configure"
_BOT_LOGINS = ("renovate[bot]", "dependabot[bot]")

# ponytail: in-process, refilled by the hourly GitHub pass; one backend replica
# on SQLite. Move to a table if the backend ever runs several replicas.
_prs: dict[tuple[str, str], list[dict]] = {}


def _is_bot(pull: dict) -> bool:
    ref = (pull.get("head") or {}).get("ref") or ""
    login = (pull.get("user") or {}).get("login") or ""
    if ref == _ONBOARDING_REF:  # Renovate's setup PR, not an upgrade
        return False
    return ref.startswith(_BOT_REFS) or login in _BOT_LOGINS


def remember(project: str, app: str, pulls: list[dict]) -> None:
    _prs[(project, app)] = [p for p in pulls if _is_bot(p)]


def matches(pull: dict, package: str) -> bool:
    """Title names the package as a whole token, or the body links to it."""
    name = re.escape(package)
    # A package name never touches these characters, so they bound a token.
    title = re.search(
        rf"(?<![\w@/.-]){name}(?![\w@/.-])", pull.get("title") or "", re.I
    )
    return (
        bool(title)
        or f"[{package}](".lower() in (pull.get("body") or "").lower()
    )


def for_finding(project: str, finding: store.Merged) -> str | None:
    fix = (json.loads(finding.i18n) if finding.i18n else {}).get("fix") or {}
    package = (fix.get("params") or {}).get("package")
    if fix.get("key") != "vuln.fixBump" or not package:
        return None
    for pull in _prs.get((project, finding.app or ""), []):
        if matches(pull, package):
            return pull.get("html_url")
    return None
