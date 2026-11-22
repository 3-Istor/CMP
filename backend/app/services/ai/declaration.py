"""
An agent's two levels of configuration, checked against each other.

Rights live in the project record (``spec.ai.agents[]``), written by the CMP
only; the declaration lives in the app repository
(``deploy/values-<agent>.yaml``, key ``agent:``). The declaration can never
exceed the rights: the gateway policy is generated from the rights alone, so
a declaration asking for more is refused here first, then by the chart's CI
validator, then by the gateway.

Pure functions: no I/O, so the routes, the MCP tools and the tests share
exactly the same rules.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

NAME = re.compile(r"^[a-z]([a-z0-9-]{0,38}[a-z0-9])?$")
# Kubernetes namespaces (<project>-<agent>) stop at 63 characters.
MAX_NAMESPACE = 63
TRIGGERS = ("chat", "event", "schedule", "a2a")
CRON = re.compile(r"^[0-9*,/\- ]{9,64}$")
EFFORTS = ("low", "medium", "high")
ISOLATIONS = ("runc", "gvisor", "kata")
STATUSES = ("active", "suspended")
MAX_STEPS = 20
MAX_INSTRUCTIONS = 20_000
CALLER_KINDS = ("app", "agent", "user")
USER_CALLERS = ("members", "admins")

BADGES = (
    "untrusted_content",
    "private_data",
    "external_send",
    "writes",
    "executes_code",
)
# The three risks of the Rule of Two: an agent may hold at most two.
RULE_OF_TWO = ("private_data", "untrusted_content", "external_send")


class DeclarationError(ValueError):
    """The request breaks a rule; ``errors`` says which, one line each."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def models() -> list[str]:
    return [m.strip() for m in settings.AI_MODELS.split(",") if m.strip()]


def client_id(project: str, agent: str) -> str:
    return f"agent-{project}-{agent}"


def app_client_id(project: str, app: str) -> str:
    return f"app-{project}-{app}"


def values_path(agent: str) -> str:
    return f"deploy/values-{agent}.yaml"


def check_name(project: str, name: str) -> list[str]:
    if not NAME.match(name or ""):
        return [
            f"Agent name '{name}': lowercase letters, digits and dashes, "
            "starting with a letter, at most 40 characters."
        ]
    if len(project) + 1 + len(name) > MAX_NAMESPACE:
        return [
            f"Agent name '{name}' is too long for project '{project}': "
            f"'{project}-{name}' must fit in {MAX_NAMESPACE} characters."
        ]
    return []


def default_effort(model: str) -> str:
    return "low" if "haiku" in model else "medium"


@dataclass
class Risk:
    badges: set[str] = field(default_factory=set)

    @property
    def count(self) -> int:
        return sum(1 for risk in RULE_OF_TWO if risk in self.badges)

    @property
    def blocked(self) -> bool:
        return self.count == len(RULE_OF_TWO)

    @property
    def isolation(self) -> str:
        return "gvisor" if "executes_code" in self.badges else "runc"

    def view(self) -> dict[str, Any]:
        return {
            "private_data": "private_data" in self.badges,
            "untrusted_content": "untrusted_content" in self.badges,
            "external_send": "external_send" in self.badges,
            "executes_code": "executes_code" in self.badges,
            "count": self.count,
            "verdict": "blocked" if self.blocked else "ok",
            "isolation": self.isolation,
        }


def risk_of(tools: list[str], catalogue: dict[str, list[str]]) -> Risk:
    badges: set[str] = set()
    for tool in tools:
        badges.update(catalogue.get(tool, []))
    return Risk(badges)


def capabilities(tools: list[str], catalogue: dict[str, list[str]]) -> dict:
    """What the agent reads, does, sends out and runs, tool by tool."""
    view: dict[str, list[str]] = {
        "reads": [],
        "acts": [],
        "sends": [],
        "runs_code": [],
    }
    for tool in tools:
        badges = catalogue.get(tool, [])
        if "untrusted_content" in badges or "private_data" in badges:
            view["reads"].append(tool)
        if "writes" in badges:
            view["acts"].append(tool)
        if "external_send" in badges:
            view["sends"].append(tool)
        if "executes_code" in badges:
            view["runs_code"].append(tool)
    return view


def normalize_caller(caller: str) -> str:
    kind, sep, name = str(caller).partition(":")
    return f"{kind}:{name}" if sep else f"agent:{kind}"


def _isolation(wanted: str | None, minimum: str, errors: list[str]) -> str:
    if wanted is None:
        return minimum
    if wanted not in ISOLATIONS:
        errors.append(f"Isolation '{wanted}': one of {', '.join(ISOLATIONS)}.")
        return minimum
    if ISOLATIONS.index(wanted) < ISOLATIONS.index(minimum):
        errors.append(
            f"Isolation '{wanted}' is weaker than '{minimum}', which its "
            "tools require."
        )
        return minimum
    return wanted


def _trigger(raw: Any, errors: list[str]) -> dict:
    trigger = dict(raw) if isinstance(raw, dict) else {"type": raw or "chat"}
    kind = trigger.get("type")
    if kind not in TRIGGERS:
        errors.append(f"Trigger '{kind}': one of {', '.join(TRIGGERS)}.")
        return {"type": "chat"}
    if kind == "schedule" and not CRON.match(str(trigger.get("cron", ""))):
        errors.append(
            "A schedule trigger needs a cron, e.g. {type: schedule, "
            'cron: "0 8 * * *"}.'
        )
    return {
        k: v for k, v in trigger.items() if k in ("type", "cron", "source")
    }


@dataclass
class ProjectAi:
    """``spec.ai`` of a record, with the defaults a missing key means."""

    enabled: bool
    models: list[str]
    web_search: bool
    daily_token_budget: int
    apps: list[dict]
    agents: list[dict]

    @classmethod
    def of(cls, record: dict) -> "ProjectAi":
        ai = (record.get("spec") or {}).get("ai") or {}
        return cls(
            enabled=ai.get("enabled") is True,
            models=list(ai.get("models") or []),
            web_search=ai.get("webSearch") is True,
            daily_token_budget=int(ai.get("dailyTokenBudget") or 0),
            apps=[dict(a) for a in ai.get("apps") or []],
            agents=[dict(a) for a in ai.get("agents") or []],
        )

    def agent(self, name: str) -> dict | None:
        return next((a for a in self.agents if a.get("name") == name), None)

    def app(self, name: str) -> dict | None:
        return next((a for a in self.apps if a.get("name") == name), None)


def record_apps(record: dict) -> list[dict]:
    return list((record.get("spec") or {}).get("apps") or [])


@dataclass
class AgentSpec:
    """One agent as asked for: its declaration and its rights entry."""

    name: str
    declaration: dict
    rights: dict


def build(  # pylint: disable=too-many-arguments,too-many-locals
    project: str,
    name: str,
    fields: dict,
    project_ai: ProjectAi,
    catalogue: dict[str, list[str]],
    apps: list[dict],
    owner: str,
    known_agents: set[str],
) -> AgentSpec:
    """
    Check *fields* (snake_case, as the API takes them, defaults already
    merged) and turn them into the declaration and the rights entry.

    Raises:
        DeclarationError: Every rule the request breaks.
    """
    errors = check_name(project, name)
    if not project_ai.enabled:
        errors.append(
            f"AI is not enabled on project '{project}': enable it first."
        )
    model = fields.get("model") or settings.AI_DEFAULT_MODEL
    allowed_models = project_ai.models or models()
    wanted_models = list(fields.get("models") or [model])
    if model not in wanted_models:
        wanted_models.insert(0, model)
    for item in wanted_models:
        if item not in allowed_models:
            errors.append(
                f"Model '{item}' is not allowed on the project "
                f"({', '.join(allowed_models)})."
            )
    effort = fields.get("effort") or default_effort(model)
    if effort not in EFFORTS:
        errors.append(f"Effort '{effort}': one of {', '.join(EFFORTS)}.")
    max_steps = fields.get("max_steps") or 8
    if not isinstance(max_steps, int) or not 1 <= max_steps <= MAX_STEPS:
        errors.append(f"max_steps must be between 1 and {MAX_STEPS}.")
    instructions = str(fields.get("instructions") or "").strip()
    if not instructions:
        errors.append("Instructions are required.")
    elif len(instructions) > MAX_INSTRUCTIONS:
        errors.append(
            f"Instructions are limited to {MAX_INSTRUCTIONS} characters."
        )
    trigger = _trigger(fields.get("trigger"), errors)

    tools = list(dict.fromkeys(fields.get("tools") or []))
    for tool in tools:
        if tool not in catalogue:
            errors.append(
                f"Tool '{tool}' is not available in project '{project}'."
            )
    if "web_search" in tools and not project_ai.web_search:
        errors.append("web_search needs web search enabled on the project.")
    risk = risk_of(tools, catalogue)
    if risk.blocked:
        errors.append(
            "These tools combine private data, untrusted content and "
            "sending out (Rule of Two): split the task into two agents, a "
            "reader without write and an actor with one narrow tool "
            "(template app-bot)."
        )
    isolation = _isolation(fields.get("isolation"), risk.isolation, errors)

    budget = fields.get("daily_token_budget")
    budget = 100_000 if budget is None else budget
    if not isinstance(budget, int) or budget < 0:
        errors.append("daily_token_budget must be a positive integer.")
    elif (
        project_ai.daily_token_budget
        and budget > project_ai.daily_token_budget
    ):
        errors.append(
            f"daily_token_budget {budget} is above the project's "
            f"{project_ai.daily_token_budget}."
        )

    app_names = {a.get("name") for a in apps}
    callers = [normalize_caller(c) for c in fields.get("callers") or []]
    for caller in callers:
        kind, _, target = caller.partition(":")
        if kind not in CALLER_KINDS:
            errors.append(f"Caller '{caller}': app:, agent: or user:.")
        elif kind == "user" and target not in USER_CALLERS:
            errors.append(f"Caller '{caller}': user:members or user:admins.")
        elif kind == "app" and target not in app_names:
            errors.append(f"Caller '{caller}': no such app in the project.")
        elif kind == "agent" and target not in known_agents:
            errors.append(f"Caller '{caller}': no such agent in the project.")
    calls = list(dict.fromkeys(fields.get("calls") or []))
    for callee in calls:
        if callee not in known_agents or callee == name:
            errors.append(f"Call '{callee}': no such other agent here.")
    if trigger["type"] == "a2a" and not any(
        c.startswith("agent:") for c in callers
    ):
        errors.append("An a2a agent needs at least one agent: caller.")

    output = fields.get("output")
    if output is not None and not (
        isinstance(output, dict)
        and isinstance(output.get("name"), str)
        and isinstance(output.get("schema", {}), dict)
    ):
        errors.append("output is {name: <string>, schema: <JSON schema>}.")
    description = str(fields.get("description") or "").strip()[:300]

    if errors:
        raise DeclarationError(errors)

    declaration: dict[str, Any] = {
        "template": fields.get("template") or "assistant",
        "description": description,
        "model": model,
        "effort": effort,
        "maxSteps": max_steps,
        "trigger": trigger,
        "instructions": instructions,
        "tools": tools,
    }
    if output:
        declaration["output"] = output
    if calls:
        declaration["calls"] = calls
    rights = {
        "name": name,
        "owner": fields.get("owner") or owner,
        "models": wanted_models,
        "dailyTokenBudget": budget,
        "tools": tools,
        "isolation": isolation,
        "callers": callers,
        "calls": calls,
        "status": "active",
    }
    return AgentSpec(name, declaration, rights)


def fields_of(declaration: dict, rights: dict) -> dict:
    """The snake_case fields of an existing agent, to apply a PATCH on."""
    return {
        "template": declaration.get("template"),
        "description": declaration.get("description"),
        "model": declaration.get("model")
        or (rights.get("models") or [None])[0],
        "models": rights.get("models"),
        "effort": declaration.get("effort"),
        "max_steps": declaration.get("maxSteps"),
        "trigger": declaration.get("trigger"),
        "instructions": declaration.get("instructions"),
        "tools": rights.get("tools") or declaration.get("tools"),
        "output": declaration.get("output"),
        "isolation": rights.get("isolation"),
        "callers": rights.get("callers"),
        "calls": rights.get("calls") or declaration.get("calls"),
        "daily_token_budget": rights.get("dailyTokenBudget"),
        "owner": rights.get("owner"),
    }
