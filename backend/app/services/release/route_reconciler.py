"""
Re-shape an app's HTTPRoute after it switches release strategy.

Tenant apps sync with RespectIgnoreDifferences so a canary's traffic weights
survive syncs. Side effect: the first sync after switching rolling <-> canary
keeps the live route rules, so the canary never gets traffic. One sync without
that option applies the route from Git; outside a canary nothing else moves
the weights, so it changes nothing else.
"""

import logging

from app.services.kube_client import kube_list, kube_merge_patch

logger = logging.getLogger(__name__)

APPLICATIONS_PATH = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"

# Seen stuck on one pass: a sync may simply still be in flight. Synced on the next.
_stuck_once: set[str] = set()


def _route_out_of_sync(application: dict) -> bool:
    status = application.get("status", {})
    if "operation" in application:
        return False
    if status.get("operationState", {}).get("phase") == "Running":
        return False
    return any(
        resource.get("kind") == "HTTPRoute"
        and resource.get("status") == "OutOfSync"
        for resource in status.get("resources", [])
    )


def reconcile_route(
    project: str, app_name: str, canary_moving: bool
) -> list[str]:
    """Sync the app's Applications whose route stayed out of sync, return their names."""
    selector = (
        f"cnp.3istor.com/project={project},cnp.3istor.com/app={app_name}"
    )
    synced = []
    for application in kube_list(
        APPLICATIONS_PATH, {"labelSelector": selector}
    ):
        name = application["metadata"]["name"]
        if canary_moving or not _route_out_of_sync(application):
            _stuck_once.discard(name)
            continue
        if name not in _stuck_once:
            _stuck_once.add(name)
            continue
        _stuck_once.discard(name)
        kube_merge_patch(
            f"{APPLICATIONS_PATH}/{name}",
            {
                "operation": {
                    "initiatedBy": {"username": "cmp-release-recorder"},
                    "sync": {"syncOptions": ["ServerSideApply=true"]},
                }
            },
        )
        logger.info("Re-synced %s: its HTTPRoute stayed out of sync", name)
        synced.append(name)
    return synced
