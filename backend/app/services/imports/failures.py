"""
Why an import run failed and what its users can do about it (CNP-268).

The workflow reports a stage, a message and, since CNP-268, details: the
apps concerned, the vulnerabilities that blocked them and a log excerpt.
The CMP classifies the failure into a cause and derives the actions it
offers from it. Users cannot open the workflow's repository: its Actions
URLs are taken out of everything they see and kept for the audit journal.
"""

import re
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.models.imports import ImportRun, RunKind, RunStatus

LOG_EXCERPT_MAX = 8000
FIELD_MAX = 200
VULNS_MAX = 200
APPS_MAX = 100
ANALYSIS_STALE = timedelta(days=30)
TOKEN_LIKE = re.compile(
    r"(gh[opsur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"
    r"|[Bb]earer\s+\S+)"
)
REASONS = frozenset(
    {
        "scan_blocked",
        "build_failed",
        "dockerfile_refused",
        "plan_mismatch",
        "fetch_failed",
        "analyzer_failed",
    }
)

RETRY = "retry"
FIX_IN_REPO = "fix_in_repo"
REANALYZE = "reanalyze"
CONTACT_PLATFORM = "contact_platform"

ACTIONS = {
    "scan_blocked": [FIX_IN_REPO, RETRY],
    "build_failed": [FIX_IN_REPO, RETRY],
    "dockerfile_refused": [FIX_IN_REPO, RETRY],
    "plan_mismatch": [REANALYZE],
    "fetch_failed": [RETRY, REANALYZE],
    "analysis_failed": [RETRY, CONTACT_PLATFORM],
    "timeout": [RETRY],
    "workflow_stopped": [RETRY],
    "workflow_not_started": [RETRY, CONTACT_PLATFORM],
    "dispatch_failed": [RETRY, CONTACT_PLATFORM],
    "deploy_failed": [RETRY, CONTACT_PLATFORM],
    "internal": [RETRY, CONTACT_PLATFORM],
}
STAGE_CAUSES = {
    "scan": "scan_blocked",
    "build": "build_failed",
    "fetch": "fetch_failed",
    "analyze": "analysis_failed",
    "timeout": "timeout",
    "expired": "timeout",
    "workflow": "workflow_stopped",
    "dispatch": "dispatch_failed",
    "deploy": "deploy_failed",
}
REASON_CAUSES = {"analyzer_failed": "analysis_failed"}


def _workflow_url() -> re.Pattern:
    repo = re.escape(settings.IMPORT_WORKFLOW_REPO)
    return re.compile(
        rf"[,;]?\s*(?:see\s+)?https?://github\.com/{repo}(?:/\S*)?",
        re.IGNORECASE,
    )


def public_text(text: str) -> str:
    """*text* without token-like strings nor a link to the workflow's runs."""
    return _workflow_url().sub("", TOKEN_LIKE.sub("[redacted]", text)).strip()


def workflow_run_url(workflow_run_id: str | int | None) -> str | None:
    """For the audit journal and the logs only: users cannot open it."""
    if not workflow_run_id:
        return None
    return (
        f"https://github.com/{settings.IMPORT_WORKFLOW_REPO}"
        f"/actions/runs/{workflow_run_id}"
    )


def _text(value, limit: int = FIELD_MAX) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return public_text(value)[:limit] or None


def clean_details(details: dict | None, known_apps: set[str]) -> dict:
    """
    What is kept of the worker's details. Tolerant: a malformed field is
    dropped, never the report, so a failure is always recorded.
    """
    if not isinstance(details, dict):
        return {}
    kept: dict = {}
    if details.get("reason") in REASONS:
        kept["reason"] = details["reason"]
    apps = details.get("apps")
    if isinstance(apps, list):
        named = [
            a
            for a in dict.fromkeys(x for x in apps if isinstance(x, str))
            if a in known_apps
        ]
        if named:
            kept["apps"] = named[:APPS_MAX]
    vulnerabilities = []
    for item in details.get("vulnerabilities") or []:
        if not isinstance(item, dict) or item.get("app") not in known_apps:
            continue
        fields = {
            key: _text(item.get(key))
            for key in ("id", "package", "installed", "fixed", "severity")
        }
        if all(fields.values()):
            vulnerabilities.append({"app": item["app"], **fields})
    if vulnerabilities:
        kept["vulnerabilities"] = vulnerabilities[:VULNS_MAX]
    excerpt = details.get("log_excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        kept["log_excerpt"] = public_text(excerpt)[-LOG_EXCERPT_MAX:]
    return kept


def known_apps(run: ImportRun) -> set[str]:
    return {s.app_name for s in run.source.services}


def cause(run: ImportRun) -> str:
    report = run.report or {}
    failure = report.get("failure") or {}
    if failure.get("cause") in ACTIONS:
        return failure["cause"]
    if run.status == RunStatus.CANCELLED.value:
        return "workflow_stopped"
    reason = failure.get("reason")
    if failure.get("vulnerabilities"):
        return "scan_blocked"
    if reason in REASONS:
        return REASON_CAUSES.get(reason, reason)
    stage = report.get("stage")
    if stage is None and report.get("error") == "expired":
        return "timeout"
    return STAGE_CAUSES.get(stage, "internal")


def upgrades(vulnerabilities: list[dict]) -> list[dict]:
    """One line per package version to change, with the apps it blocks."""
    lines: dict[tuple[str, str], dict] = {}
    for vuln in vulnerabilities:
        line = lines.setdefault(
            (vuln["package"], vuln["installed"]),
            {
                "package": vuln["package"],
                "installed": vuln["installed"],
                "fixed": vuln["fixed"],
                "apps": [],
                "ids": [],
            },
        )
        if vuln["app"] not in line["apps"]:
            line["apps"].append(vuln["app"])
        if vuln["id"] not in line["ids"]:
            line["ids"].append(vuln["id"])
    return sorted(lines.values(), key=lambda line: line["package"])


def _analysis_is_stale(run: ImportRun) -> bool:
    """The build's analysis no longer describes the import's branch."""
    source = run.source
    analyses = [
        r
        for r in source.runs
        if r.kind == RunKind.ANALYZE.value
        and r.status == RunStatus.SUCCEEDED.value
    ]
    if not analyses:
        return True
    analysis = analyses[-1]
    branch = ((analysis.plan or {}).get("source") or {}).get("branch")
    if branch and branch != source.branch:
        return True
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return analysis.created_at is not None and (
        now - analysis.created_at > ANALYSIS_STALE
    )


def failure_view(run: ImportRun) -> dict | None:
    """What failed, for whom, and the actions offered; None unless failed."""
    if run.status not in (RunStatus.FAILED.value, RunStatus.CANCELLED.value):
        return None
    report = run.report or {}
    details = report.get("failure") or {}
    found = cause(run)
    actions = list(ACTIONS[found])
    if (
        run.kind == RunKind.BUILD.value
        and REANALYZE not in actions
        and _analysis_is_stale(run)
    ):
        actions.append(REANALYZE)
    if run.kind == RunKind.ANALYZE.value and REANALYZE in actions:
        actions.remove(REANALYZE)
        if RETRY not in actions:
            actions.insert(0, RETRY)
    if run.kind == RunKind.ANALYZE.value and FIX_IN_REPO in actions:
        actions.remove(FIX_IN_REPO)
    vulnerabilities = details.get("vulnerabilities") or []
    return {
        "cause": found,
        "stage": report.get("stage"),
        "message": public_text(report.get("error") or ""),
        "apps": details.get("apps") or [],
        "vulnerabilities": vulnerabilities,
        "upgrades": upgrades(vulnerabilities),
        "log_excerpt": details.get("log_excerpt"),
        "actions": actions,
        "rebuilds_on_push": bool(run.source.auto_rebuild),
    }
