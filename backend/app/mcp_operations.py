"""
MCP tools for running apps and projects after they exist: power, config,
staging and promotions, CI runs, canary traffic, members, costs, security,
database backups and alerts.

Every tool calls the CMP API with the user's token, so the API keeps checking
roles; ``_gate`` adds the stake policy on top (policy.yaml). Approving an
assistant or a promotion, deletion protection, customer access and finance
admin stay in the CMP on purpose (CNP-233).
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp_server import (
    PENDING,
    PUBLIC_URL,
    READ_ONLY,
    Caller,
    _bounded,
    _gate,
    mcp,
    tool_errors,
)

# Values keys with a tool and a rule of their own (a restore is a db.restore
# patch, release is release.settings), or that switch off a safety net.
CONFIG_KEYS_ELSEWHERE = {
    "db": "list_backups, database_backup, database_restore",
    "database": "the CMP",
    "release": "update_release_settings",
    "migrations": "update_release_settings",
    "image": "cut_release",
    "security": "the CMP",
}
CRON = r"^[0-9*,/\- ]{9,64}$"

WRITE = ToolAnnotations(destructiveHint=False, idempotentHint=False)
DESTRUCTIVE = ToolAnnotations(destructiveHint=True, idempotentHint=False)
FINDINGS_SHOWN = 50


async def _app_gate(
    caller: Caller,
    app_id: int,
    action: str,
    *,
    tool: str,
    arguments: dict,
    summary: str,
    user_confirmed: bool,
    confirmation_id: str | None = None,
) -> tuple[dict, dict | None]:
    """The app, and the answer to return instead when the policy stops it.
    ``{app}`` in ``summary`` becomes the app's name."""
    caller.require_write()
    app = await caller.api("GET", f"/api/deployments/{app_id}")
    verdict, answer = await _gate(
        caller,
        action,
        app["project_id"],
        tool=tool,
        arguments=arguments,
        summary=summary.replace("{app}", app["name"]),
        deployment_id=app_id,
        namespace=app.get("k8s_namespace"),
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    return app, answer if verdict == "stop" else None


async def _project_gate(
    caller: Caller,
    project: str,
    action: str,
    *,
    tool: str,
    arguments: dict,
    summary: str,
    user_confirmed: bool,
    confirmation_id: str | None = None,
) -> dict | None:
    caller.require_write()
    verdict, answer = await _gate(
        caller,
        action,
        project,
        tool=tool,
        arguments=arguments,
        summary=summary,
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    return answer if verdict == "stop" else None


async def _optional(caller: Caller, path: str, **kwargs) -> Any:
    """A part of a combined read: its error instead of failing the whole."""
    try:
        return await caller.api("GET", path, **kwargs)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"unavailable": getattr(exc, "detail", str(exc))}


# ---------------------------------------------------------------------------
# Running app
# ---------------------------------------------------------------------------


@mcp.tool(annotations=WRITE)
@tool_errors
async def app_power(
    app_id: int,
    action: Annotated[
        Literal["restart", "sleep", "wake"],
        Field(
            description="restart: roll the pods (same version). sleep: stop "
            "it until woken, it costs nothing meanwhile. wake: start it again."
        ),
    ],
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Restart, put to sleep or wake an app."""
    caller = Caller()
    _, stop = await _app_gate(
        caller,
        app_id,
        "app.restart" if action == "restart" else "app.sleep",
        tool="app_power",
        arguments={"app_id": app_id, "action": action},
        summary=f"{action.capitalize()} the app '{{app}}'?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST", f"/api/deployments/{app_id}/{action}", json={}
    )
    return {"ok": True, "app_id": app_id, "action": action, "result": result}


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_app_config(
    app_id: int,
    component: Annotated[
        str | None,
        Field(description="Fullstack apps: backend or frontend"),
    ] = None,
) -> dict:
    """The app's deployment settings (its values file in Git): replicas,
    resources, database, and the rest."""
    caller = Caller()
    params = {"component": component} if component else None
    view = await caller.api(
        "GET", f"/api/deployments/{app_id}/config", params=params
    )
    return {
        "file": view.get("file_path"),
        "components": view.get("components", []),
        "config": view.get("config", {}),
    }


@mcp.tool(annotations=WRITE)
@tool_errors
async def update_app_config(
    app_id: int,
    changes: Annotated[
        dict[str, Any],
        Field(
            description="Keys to merge into the values file, nested objects "
            'allowed, e.g. {"replicaCount": 2}. Keys not given are kept. '
            "Read get_app_config first."
        ),
    ],
    component: str | None = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Change the app's deployment settings: one commit on main, which the
    platform deploys."""
    if not changes or any(str(key).startswith("_") for key in changes):
        raise ValueError(
            "Give at least one setting; keys cannot start with _."
        )
    elsewhere = sorted(set(changes) & set(CONFIG_KEYS_ELSEWHERE))
    if elsewhere:
        raise ValueError(
            "Not through update_app_config: "
            + "; ".join(
                f"{k} -> {CONFIG_KEYS_ELSEWHERE[k]}" for k in elsewhere
            )
        )
    caller = Caller()
    _, stop = await _app_gate(
        caller,
        app_id,
        "app.config",
        tool="update_app_config",
        arguments={
            "app_id": app_id,
            "changes": changes,
            "component": component,
        },
        summary=f"Change the settings of '{{app}}': {changes}?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    params = {"component": component} if component else None
    view = await caller.api(
        "GET", f"/api/deployments/{app_id}/config", params=params
    )
    result = await caller.api(
        "PATCH",
        f"/api/deployments/{app_id}/config",
        params=params,
        json={"_sha": view["_sha"], **changes},
    )
    return {"ok": True, "app_id": app_id, "result": result}


# ---------------------------------------------------------------------------
# Staging, promotions, CI
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_environments(app_id: int) -> dict:
    """Prod and staging of an app: versions, staging schedule, the promotion
    policy and recent promotions."""
    caller = Caller()
    base = f"/api/deployments/{app_id}"
    return {
        "environments": await _optional(caller, f"{base}/environments"),
        "staging_offhours": await _optional(
            caller, f"{base}/environments/staging/offhours"
        ),
        "promotion_policy": await _optional(
            caller, f"{base}/promotion-policy"
        ),
        "promotions": await _optional(caller, f"{base}/promotions"),
    }


@mcp.tool(annotations=DESTRUCTIVE)
@tool_errors
async def staging(
    app_id: int,
    action: Annotated[
        Literal["enable", "disable", "wake", "schedule"],
        Field(
            description="enable: add a staging environment. disable: remove "
            "it and everything it runs. wake: start it outside its hours. "
            "schedule: change when it sleeps and wakes."
        ),
    ],
    sleep_at: Annotated[
        str | None,
        Field(
            pattern=CRON,
            description='Cron, e.g. "0 19 * * 1-5" (19:00 on weekdays)',
        ),
    ] = None,
    wake_at: Annotated[
        str | None,
        Field(pattern=CRON, description='Cron, e.g. "0 8 * * 1-5"'),
    ] = None,
    schedule_enabled: Annotated[
        bool | None, Field(description="false: staging never sleeps")
    ] = None,
    timezone: str | None = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Add, remove, wake or schedule the staging environment of an app."""
    caller = Caller()
    offhours = {
        key: value
        for key, value in (
            ("enabled", schedule_enabled),
            ("sleepAt", sleep_at),
            ("wakeAt", wake_at),
            ("timezone", timezone),
        )
        if value is not None
    }
    if action == "schedule" and not offhours:
        raise ValueError("Give the hours (cron) or schedule_enabled.")
    arguments = {"app_id": app_id, "action": action}
    if action in ("enable", "schedule"):
        arguments["offhours"] = offhours
    _, stop = await _app_gate(
        caller,
        app_id,
        "staging.manage",
        tool="staging",
        arguments=arguments,
        summary={
            "enable": "Add a staging environment to '{app}'?",
            "disable": "Remove the staging environment of '{app}' and "
            "everything it runs?",
            "wake": "Wake the staging environment of '{app}'?",
            "schedule": f"Set the staging hours of '{{app}}' to {offhours}?",
        }[action],
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    path = f"/api/deployments/{app_id}/environments/staging"
    if action == "enable":
        result = await caller.api("POST", path, json={"offhours": offhours})
    elif action == "disable":
        result = await caller.api("DELETE", path)
    elif action == "wake":
        result = await caller.api("POST", f"{path}/wake", json={})
    else:
        # The PUT replaces the whole schedule: keep what was not given.
        current = await caller.api("GET", f"{path}/offhours")
        kept = {
            key: current[key]
            for key in ("enabled", "sleepAt", "wakeAt", "timezone")
            if key in current
        }
        result = await caller.api(
            "PUT", f"{path}/offhours", json={**kept, **offhours}
        )
    return {"ok": True, "app_id": app_id, "action": action, "result": result}


@mcp.tool(annotations=DESTRUCTIVE)
@tool_errors
async def promotion(
    app_id: int,
    action: Annotated[
        Literal["start", "accept_step", "cancel"],
        Field(
            description="start: promote what runs in staging to prod. "
            "accept_step: accept a step waiting for a decision. cancel: stop "
            "a promotion in progress."
        ),
    ],
    promotion_id: int | None = None,
    step_index: int | None = None,
    emergency: Annotated[
        bool, Field(description="Skip the soak time; needs a reason")
    ] = False,
    reason: Annotated[str | None, Field(max_length=500)] = None,
    rollback_to: Annotated[
        int | None,
        Field(description="Promote this earlier revision instead (rollback)"),
    ] = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Promote staging to prod, accept a step, or cancel. A promotion that
    needs an approval is approved by a person in the CMP."""
    if action != "start" and promotion_id is None:
        raise ValueError("promotion_id is required; see get_environments.")
    if action == "accept_step" and step_index is None:
        raise ValueError("step_index is required to accept a step.")
    if emergency and not reason:
        raise ValueError("An emergency promotion needs a reason.")
    caller = Caller()
    arguments = {
        "app_id": app_id,
        "action": action,
        "promotion_id": promotion_id,
        "step_index": step_index,
        "emergency": emergency,
        "reason": reason,
        "rollback_to": rollback_to,
    }
    _, stop = await _app_gate(
        caller,
        app_id,
        "release.abort" if action == "cancel" else "release.promote",
        tool="promotion",
        arguments=arguments,
        summary={
            "start": (
                f"Roll prod of '{{app}}' back to revision {rollback_to}"
                if rollback_to is not None
                else "Promote staging of '{app}' to prod"
            )
            + (" now, skipping the soak time" if emergency else "")
            + (f" ({reason})" if reason else "")
            + "?",
            "accept_step": f"Accept step {step_index} of the promotion of "
            "'{app}'?",
            "cancel": "Cancel the promotion of '{app}'?",
        }[action],
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    base = f"/api/deployments/{app_id}/promotions"
    if action == "start":
        result = await caller.api(
            "POST",
            base,
            json={
                "emergency": emergency,
                "reason": reason,
                "rollback_to": rollback_to,
            },
        )
    elif action == "accept_step":
        result = await caller.api(
            "POST",
            f"{base}/{promotion_id}/steps/{step_index}/accept",
            json={},
        )
    else:
        result = await caller.api(
            "POST", f"{base}/{promotion_id}/cancel", json={}
        )
    return {"ok": True, "app_id": app_id, "promotion": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def set_promotion_policy(
    app_id: int,
    soak_minutes: Annotated[
        int | None,
        Field(ge=0, le=7 * 24 * 60, description="Time staging must run first"),
    ] = None,
    require_approval: bool | None = None,
    auto_promote: bool | None = None,
    suites: Annotated[
        list[dict] | None,
        Field(description="Test suites run on staging; see get_environments"),
    ] = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Change how staging is promoted to prod. Fields not given are kept."""
    caller = Caller()
    changes = {
        key: value
        for key, value in (
            ("soakMinutes", soak_minutes),
            ("requireApproval", require_approval),
            ("autoPromote", auto_promote),
            ("suites", suites),
        )
        if value is not None
    }
    if not changes:
        raise ValueError("Give at least one field to change.")
    _, stop = await _app_gate(
        caller,
        app_id,
        "promotion.policy",
        tool="set_promotion_policy",
        arguments={"app_id": app_id, **changes},
        summary=f"Change the promotion policy of '{{app}}': {changes}?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    path = f"/api/deployments/{app_id}/promotion-policy"
    current = await caller.api("GET", path)
    body = {
        key: current.get(key)
        for key in ("suites", "soakMinutes", "requireApproval", "autoPromote")
    }
    result = await caller.api("PUT", path, json={**body, **changes})
    return {"ok": True, "app_id": app_id, "policy": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def ci_run(
    app_id: int,
    action: Literal["start", "cancel"],
    environment: Literal["prod", "staging"] = "staging",
    suite: Annotated[
        str | None, Field(description="Suite to run, from the policy")
    ] = None,
    run_id: int | None = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Run a test suite against an environment, or cancel a run."""
    if action == "start" and not suite:
        raise ValueError("suite is required; see get_environments.")
    if action == "cancel" and run_id is None:
        raise ValueError("run_id is required to cancel a run.")
    caller = Caller()
    _, stop = await _app_gate(
        caller,
        app_id,
        "ci.run",
        tool="ci_run",
        arguments={
            "app_id": app_id,
            "action": action,
            "environment": environment,
            "suite": suite,
            "run_id": run_id,
        },
        summary=(
            f"Run the suite '{suite}' on {environment} of '{{app}}'?"
            if action == "start"
            else f"Cancel run {run_id} of '{{app}}'?"
        ),
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    if action == "start":
        result = await caller.api(
            "POST",
            f"/api/deployments/{app_id}/environments/{environment}/runs",
            json={"suite": suite},
        )
    else:
        result = await caller.api(
            "POST", f"/api/deployments/{app_id}/runs/{run_id}/cancel", json={}
        )
    return {"ok": True, "app_id": app_id, "run": result}


# ---------------------------------------------------------------------------
# Release: canary traffic and settings
# ---------------------------------------------------------------------------


@mcp.tool(annotations=WRITE)
@tool_errors
async def release_traffic(
    app_id: int,
    action: Annotated[
        Literal["start", "stop"],
        Field(
            description="start: send synthetic requests so a canary with "
            "no real users gets enough traffic to be judged. stop: end it."
        ),
    ],
    rps: Annotated[int, Field(ge=1, le=20)] = 10,
    minutes: Annotated[int, Field(ge=1, le=15)] = 5,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Generate or stop test traffic on the app during a rollout."""
    caller = Caller()
    _, stop = await _app_gate(
        caller,
        app_id,
        "release.traffic",
        tool="release_traffic",
        arguments={
            "app_id": app_id,
            "action": action,
            "rps": rps,
            "minutes": minutes,
        },
        summary=(
            f"Send {rps} requests per second to '{{app}}' for {minutes} min?"
            if action == "start"
            else "Stop the test traffic on '{app}'?"
        ),
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    path = f"/api/deployments/{app_id}/release/traffic"
    if action == "start":
        result = await caller.api(
            "POST", path, json={"rps": rps, "minutes": minutes}
        )
    else:
        result = await caller.api("DELETE", path)
    return {"ok": True, "app_id": app_id, "traffic": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def update_release_settings(
    app_id: int,
    changes: Annotated[
        dict[str, Any],
        Field(
            description="Fields of the release block to change, e.g. "
            '{"strategy": "canary", "errorRateMax": 2}. Read get_release '
            "first; fields not given are kept."
        ),
    ],
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Change how the app is released (strategy, canary checks): one commit,
    refused while a canary is running."""
    if not changes:
        raise ValueError("Give at least one field to change.")
    caller = Caller()
    _, stop = await _app_gate(
        caller,
        app_id,
        "release.settings",
        tool="update_release_settings",
        arguments={"app_id": app_id, "changes": changes},
        summary=f"Change the release settings of '{{app}}': {changes}?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    path = f"/api/deployments/{app_id}/release/settings"
    view = await caller.api("GET", path)
    result = await caller.api(
        "PUT", path, json={**view.get("settings", {}), **changes}
    )
    return {"ok": True, "app_id": app_id, "result": result}


# ---------------------------------------------------------------------------
# Project: members and deletion
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def list_members(project: str) -> dict:
    """Who is in a project, with their role."""
    caller = Caller()
    members = await caller.api("GET", f"/api/projects/{project}/members")
    return {"project": project, "members": members}


@mcp.tool(annotations=WRITE)
@tool_errors
async def project_member(
    project: str,
    action: Literal["add", "remove"],
    username: Annotated[str, Field(min_length=1, description="Company login")],
    role: Literal["admin", "member", "guest"] = "member",
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Add someone to a project with a role, or remove them. The owner role
    is never given by an assistant."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "project.members",
        tool="project_member",
        arguments={
            "project": project,
            "action": action,
            "username": username,
            "role": role,
        },
        summary=(
            f"Add {username} to '{project}' as {role}?"
            if action == "add"
            else f"Remove {username} from '{project}'?"
        ),
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    path = f"/api/projects/{project}/members"
    if action == "add":
        result = await caller.api(
            "POST", path, json={"username": username, "role": role}
        )
    else:
        result = await caller.api("DELETE", f"{path}/{username}")
    return {"ok": True, "project": project, "result": result}


@mcp.tool(
    annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True)
)
@tool_errors
async def delete_project(
    project: str,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Delete an empty project (delete its apps first with delete_app). Only
    sandbox and demo projects; elsewhere a person does it in the CMP."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "project.delete",
        tool="delete_project",
        arguments={"project": project},
        summary=f"Delete the project '{project}'? This cannot be undone.",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    result = await _bounded(caller.api("DELETE", f"/api/projects/{project}"))
    return {
        "ok": True,
        "project": project,
        "status": "deleting" if result is PENDING else "deleted",
        "next": "Call whoami: the project disappears once deleted.",
    }


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_costs(project: str) -> dict:
    """What a project costs: this month so far, the month-end forecast, the
    budget, and recommendations to spend less."""
    caller = Caller()
    params = {"project": project}
    overview = await _optional(caller, "/api/finops/overview", params=params)
    return {
        "summary": overview.get("summary", overview),
        "by_cloud": overview.get("by_cloud"),
        "apps": overview.get("apps"),
        "forecast": await _optional(
            caller, "/api/finops/forecast", params=params
        ),
        "budget": await _optional(caller, f"/api/finops/budgets/{project}"),
        "recommendations": await _optional(
            caller, "/api/finops/recommendations", params=params
        ),
    }


@mcp.tool(annotations=WRITE)
@tool_errors
async def set_budget(
    project: str,
    monthly_amount_eur: Annotated[float, Field(ge=0)],
    threshold_warn: Annotated[int, Field(ge=1, le=100)] = 70,
    threshold_critical: Annotated[int, Field(ge=1, le=100)] = 90,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Set the monthly budget of a project and when it alerts (percent)."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "finops.budget",
        tool="set_budget",
        arguments={
            "project": project,
            "monthly_amount_eur": monthly_amount_eur,
            "threshold_warn": threshold_warn,
            "threshold_critical": threshold_critical,
        },
        summary=f"Set the budget of '{project}' to {monthly_amount_eur} EUR "
        f"a month (alerts at {threshold_warn} % and {threshold_critical} %)?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    result = await caller.api(
        "PUT",
        f"/api/finops/budgets/{project}",
        json={
            "monthly_amount_eur": monthly_amount_eur,
            "threshold_warn": threshold_warn,
            "threshold_critical": threshold_critical,
        },
    )
    return {"ok": True, "project": project, "budget": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def cost_recommendation(
    project: str,
    recommendation_id: str,
    action: Annotated[
        Literal["apply", "ignore", "notify"],
        Field(
            description="apply: change the app as recommended (a commit). "
            "ignore: hide it. notify: send it to the project's admins."
        ),
    ],
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Act on a cost recommendation from get_costs."""
    caller = Caller()
    recommendations = await caller.api(
        "GET", "/api/finops/recommendations", params={"project": project}
    )
    found = next(
        (r for r in recommendations if r.get("id") == recommendation_id), None
    )
    if found is None:
        raise ValueError(
            f"No recommendation {recommendation_id!r} in '{project}'; see "
            "get_costs."
        )
    stop = await _project_gate(
        caller,
        project,
        "finops.apply" if action == "apply" else "finops.triage",
        tool="cost_recommendation",
        arguments={
            "project": project,
            "recommendation_id": recommendation_id,
            "action": action,
        },
        summary=f"{action.capitalize()} the recommendation "
        f"{found.get('title')!r} on {found.get('app_name')!r} (saves "
        f"{found.get('monthly_saving_eur')} EUR a month)?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST",
        f"/api/finops/recommendations/{recommendation_id}/{action}",
        json={},
    )
    return {
        "ok": True,
        "recommendation_id": recommendation_id,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_security(project: str, app: str | None = None) -> dict:
    """Security state of a project or app: summary, open findings (the most
    severe first) and exceptions."""
    caller = Caller()
    params = {"project": project} | ({"app": app} if app else {})
    findings = await _optional(
        caller,
        "/api/security/findings",
        params={**params, "state": "open"},
    )
    shown = findings
    if isinstance(findings, list) and len(findings) > FINDINGS_SHOWN:
        shown = findings[:FINDINGS_SHOWN]
    return {
        "summary": await _optional(
            caller, "/api/security/summary", params=params
        ),
        "open_findings": shown,
        "open_findings_total": (
            len(findings) if isinstance(findings, list) else None
        ),
        "exceptions": await _optional(
            caller, "/api/security/exceptions", params=params
        ),
    }


@mcp.tool(annotations=WRITE)
@tool_errors
async def security_scan(
    project: str,
    app: str,
    source: Annotated[
        Literal["ci", "trivy-operator"],
        Field(
            description="trivy-operator: rescan what runs. ci: rerun the "
            "scan of the app's pipeline."
        ),
    ] = "trivy-operator",
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Ask for a new security scan of an app."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "security.scan",
        tool="security_scan",
        arguments={"project": project, "app": app, "source": source},
        summary=f"Scan '{app}' in '{project}' again ({source})?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST",
        "/api/security/scans",
        json={"project": project, "app": app, "source": source},
    )
    return {"ok": True, "scan": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def request_security_exception(
    project: str,
    fingerprint: Annotated[
        str, Field(description="The finding's fingerprint, from get_security")
    ],
    status: Literal["not_affected", "false_positive", "accepted_risk"],
    statement: Annotated[
        str,
        Field(
            min_length=10,
            max_length=2000,
            description="Why, in words a reviewer can check",
        ),
    ],
    justification: Annotated[
        Literal[
            "component_not_present",
            "vulnerable_code_not_present",
            "vulnerable_code_not_in_execute_path",
            "vulnerable_code_cannot_be_controlled_by_adversary",
            "inline_mitigations_already_exist",
        ]
        | None,
        Field(description="Required for not_affected (VEX)"),
    ] = None,
    expires_on: date | None = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Except a finding. Some exceptions apply at once and silence the
    finding in CI too, so standard and critical projects need a person's
    approval in the CMP before the call goes through."""
    if (status == "not_affected") != (justification is not None):
        raise ValueError(
            "A justification goes with not_affected, and only with it."
        )
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "security.exception",
        tool="request_security_exception",
        arguments={
            "project": project,
            "fingerprint": fingerprint,
            "status": status,
            "statement": statement,
            "justification": justification,
            "expires_on": expires_on.isoformat() if expires_on else None,
        },
        summary=f"Mark the finding {fingerprint} as {status} in "
        f"'{project}' (it may stop failing CI): {statement!r}",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    body = {"project": project, "status": status, "statement": statement}
    if justification:
        body["justification"] = justification
    if expires_on:
        body["expires_on"] = expires_on.isoformat()
    result = await caller.api(
        "POST", f"/api/security/findings/{fingerprint}/exception", json=body
    )
    return {
        "ok": True,
        "exception": result,
        "next": "Its status says whether it applies now or waits for an "
        f"admin's approval: {PUBLIC_URL}/projects/{project}",
    }


# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def list_backups(project: str, app: str) -> dict:
    """The app's database and its backups, newest first."""
    caller = Caller()
    params = {"project": project, "app": app}
    return {
        "databases": await _optional(
            caller, "/api/security/databases", params=params
        ),
        "backups": await _optional(
            caller, "/api/security/backups", params=params
        ),
    }


@mcp.tool(annotations=WRITE)
@tool_errors
async def database_backup(
    project: str,
    app: str,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Take a backup of the app's database now."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "db.backup",
        tool="database_backup",
        arguments={"project": project, "app": app},
        summary=f"Back up the database of '{app}' now?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST", "/api/security/backups", json={"project": project, "app": app}
    )
    return {"ok": True, "backup": result}


@mcp.tool(annotations=DESTRUCTIVE)
@tool_errors
async def database_restore(
    project: str,
    app: str,
    backup: Annotated[
        str, Field(description="Backup name, from list_backups")
    ],
    target_time: Annotated[
        datetime | None,
        Field(description="Point in time to restore to, within the backup"),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(
            description="true: only check that the restore is possible and "
            "say what it would do. Always do this first."
        ),
    ] = True,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Restore the app's database from a backup. It replaces the current
    data: run it with dry_run=true first."""
    caller = Caller()
    body = {"project": project, "app": app, "backup": backup}
    if target_time:
        body["target_time"] = target_time.isoformat()
    if not dry_run:
        stop = await _project_gate(
            caller,
            project,
            "db.restore",
            tool="database_restore",
            arguments={**body, "dry_run": False},
            summary=f"Replace the database of '{app}' with the backup "
            f"{backup}"
            + (f" at {target_time.isoformat()}" if target_time else "")
            + "? Data written since is lost.",
            user_confirmed=user_confirmed,
            confirmation_id=confirmation_id,
        )
        if stop:
            return stop
    else:
        caller.require_write()
    result = await caller.api(
        "POST",
        "/api/security/restores",
        params={"dry_run": str(dry_run).lower()},
        json=body,
    )
    return {"ok": True, "dry_run": dry_run, "restore": result}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def list_alerts(project: str, app: str | None = None) -> dict:
    """The alerts a project or app can have, whether each is on, and its
    thresholds."""
    caller = Caller()
    params = {"project": project} | ({"app": app} if app else {})
    return await caller.api("GET", "/api/alerting", params=params)


@mcp.tool(annotations=WRITE)
@tool_errors
async def update_alert(
    project: str,
    alert_id: Annotated[str, Field(description="From list_alerts")],
    enabled: bool,
    params: Annotated[
        dict[str, int] | None,
        Field(description="Thresholds to change, names from list_alerts"),
    ] = None,
    app: str | None = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
) -> dict:
    """Switch an alert on or off, or change its thresholds."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "alert.update",
        tool="update_alert",
        arguments={
            "project": project,
            "alert_id": alert_id,
            "enabled": enabled,
            "params": params or {},
            "app": app,
        },
        summary=f"{'Enable' if enabled else 'Disable'} the alert {alert_id} "
        f"in '{project}'"
        + (f" for '{app}'" if app else "")
        + (f" with {params}" if params else "")
        + "?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
    )
    if stop:
        return stop
    query = {"project": project} | ({"app": app} if app else {})
    result = await caller.api(
        "PUT",
        f"/api/alerting/{alert_id}",
        params=query,
        json={"enabled": enabled, "params": params or {}},
    )
    return {"ok": True, "alert": result}
