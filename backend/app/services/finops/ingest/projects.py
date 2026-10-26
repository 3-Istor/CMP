"""Which project and app a namespace's cost belongs to."""

from dataclasses import dataclass

from app.core.config import settings

PLATFORM = "platform"
IDLE = "__idle__"


@dataclass(frozen=True)
class Owner:
    project: str
    app: str | None
    method: str


def legacy_namespaces() -> set[str]:
    return {
        ns.strip()
        for ns in settings.FINOPS_LEGACY_NAMESPACES.split(",")
        if ns.strip()
    }


def owner_of(namespace: str, ns_projects: dict[str, str]) -> Owner:
    """
    Labelled project namespaces are named ``<project>-<app>`` (or
    ``<project>-system``). Namespaces that predate CMP projects are their own
    project. Anything else runs the platform and is shared later (D-F2).
    """
    if namespace == IDLE:
        return Owner(project=IDLE, app=None, method="opencost")
    project = ns_projects.get(namespace)
    if project:
        rest = namespace.removeprefix(f"{project}-")
        app = None if rest in ("system", namespace) else rest
        return Owner(project=project, app=app, method="opencost")
    if namespace in legacy_namespaces():
        return Owner(project=namespace, app=namespace, method="opencost")
    return Owner(project=PLATFORM, app=namespace, method="opencost")
