"""
"Finish the import": what an imported app still lacks to match a template
app, computed from state the CMP already holds. Each task names the screens
it unlocks and the quickest way to get it done: the setup pull request, a
page of the CMP, or a hosted agent that opens the pull requests.
"""

from dataclasses import dataclass, field
from urllib.parse import quote

from app.models.imports import ImportService, ImportSource
from app.services.app_repo import is_managed_import
from app.services.imports import standard_pr
from app.services.imports.generate import hostname

DEPLOY = "deploy"
ROUTE = "route"
SETUP_PR = "setup_pr"
CODE_SCANNING = "code_scanning"
TRACING = "tracing"
# The hosted agent template that opens pull requests in an app repository.
AGENT_TEMPLATE = "pr-review"

LABELS = {
    DEPLOY: "Build and deploy from the repository",
    ROUTE: "Route public traffic ({hostname})",
    SETUP_PR: "Merge the setup PR: CI, signed images, release from tags",
    CODE_SCANNING: "Enable code scanning",
    TRACING: "Add tracing (OpenTelemetry)",
}


@dataclass(frozen=True)
class AppState:
    """What the cluster and the stores say, gathered by the caller."""

    running: bool
    route_live: bool
    code_scanned: bool
    # None: the traces store is not configured, the task cannot be done.
    has_traces: bool | None


@dataclass
class Task:
    key: str
    done: bool
    unlocks: list[str] = field(default_factory=list)
    action: dict | None = None
    params: dict[str, str] = field(default_factory=dict)

    def view(self) -> dict:
        return {
            "key": self.key,
            "label": LABELS[self.key].format(**self.params),
            "done": self.done,
            "params": self.params,
            "unlocks": self.unlocks,
            "action": self.action,
        }


def import_href(project: str) -> str:
    return f"/projects/{quote(project, safe='')}#imports"


def agent_href(project: str) -> str:
    return (
        f"/projects/{quote(project, safe='')}/ai/new"
        f"?template={AGENT_TEMPLATE}"
    )


def _setup_action(source: ImportSource) -> dict:
    record = source.standard_repo or {}
    if record.get("state") == standard_pr.OPEN and record.get("url"):
        return {"kind": "pr", "href": record["url"]}
    return {"kind": "link", "href": import_href(source.project_name)}


def compute(
    source: ImportSource, service: ImportService, state: AppState
) -> list[Task]:
    project = source.project_name
    managed = is_managed_import(source, service.app_name)
    is_service = service.kind == "service"
    public = bool((service.spec or {}).get("public"))
    agent = {"kind": "agent", "href": agent_href(project)}

    tasks = [Task(DEPLOY, state.running)]
    if is_service and public:
        tasks.append(
            Task(
                ROUTE,
                state.route_live,
                params={"hostname": hostname(project, service.app_name)},
            )
        )
    setup = Task(SETUP_PR, managed, ["delivery", "settings"])
    if not managed:
        setup.action = _setup_action(source)
        reason = standard_pr.ineligible(source)
        if reason:
            setup.params["blocked"] = reason
    tasks.append(setup)
    if is_service:
        tasks.append(
            Task(
                CODE_SCANNING,
                state.code_scanned,
                ["security.code"],
                (
                    None
                    if state.code_scanned
                    else (agent if managed else _setup_action(source))
                ),
            )
        )
        if state.has_traces is not None:
            tasks.append(
                Task(
                    TRACING,
                    state.has_traces,
                    ["traces"],
                    None if state.has_traces else agent,
                )
            )
    return tasks


def view(
    source: ImportSource, service: ImportService, tasks: list[Task]
) -> dict:
    record = source.standard_repo or {}
    return {
        "imported": True,
        "managed": is_managed_import(source, service.app_name),
        "source_id": source.id,
        "repo_full_name": source.repo_full_name,
        "source_mode": source.source_mode,
        "origin_repo_full_name": source.origin_repo_full_name,
        "setup_pr": (
            {"state": record["state"], "url": record.get("url")}
            if record.get("state")
            else None
        ),
        "import_href": import_href(source.project_name),
        "agent_href": agent_href(source.project_name),
        "tasks": [t.view() for t in tasks],
        "done": sum(t.done for t in tasks),
        "total": len(tasks),
    }


NOT_IMPORTED = {
    "imported": False,
    "managed": True,
    "source_id": None,
    "repo_full_name": None,
    "source_mode": None,
    "origin_repo_full_name": None,
    "setup_pr": None,
    "import_href": None,
    "agent_href": None,
    "tasks": [],
    "done": 0,
    "total": 0,
}
