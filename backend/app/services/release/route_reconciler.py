"""
Re-shape an app's HTTPRoute after it switches release strategy.

Tenant apps sync with RespectIgnoreDifferences so a canary's traffic weights
survive syncs. Side effect: the first sync after switching rolling <-> canary
keeps the live route rules, so the canary never gets traffic. One sync without
that option applies the route from Git; outside a canary nothing else moves
the weights, so it changes nothing else.
"""

import logging

from app.services.kube_client import kube_get, kube_list, kube_merge_patch

logger = logging.getLogger(__name__)

APPLICATIONS_PATH = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"
HTTPROUTES_API = "/apis/gateway.networking.k8s.io/v1"

# Seen stuck on one pass: a sync may simply still be in flight. Synced on the next.
_stuck_once: set[str] = set()
# Re-synced and still out of sync: the diff is one a sync cannot clear (the
# route's generation moves on every server-side dry run), so resyncing again
# would only loop. Forgotten once the route is seen synced or a canary moves.
_resynced: set[str] = set()


def _sync_in_flight(application: dict) -> bool:
    status = application.get("status", {})
    return (
        "operation" in application
        or status.get("operationState", {}).get("phase") == "Running"
    )


def _routes(application: dict) -> list[dict]:
    return [
        resource
        for resource in application.get("status", {}).get("resources", [])
        if resource.get("kind") == "HTTPRoute"
    ]


def _route_out_of_sync(application: dict) -> bool:
    return any(
        route.get("status") == "OutOfSync" for route in _routes(application)
    )


def _route_lacks_canary(application: dict) -> bool:
    """A route still in its rolling shape: the Rollout cannot set weights."""
    for resource in _routes(application):
        route = kube_get(
            f"{HTTPROUTES_API}/namespaces/{resource.get('namespace')}"
            f"/httproutes/{resource.get('name')}"
        )
        if route is None:
            continue
        backends = [
            ref.get("name", "")
            for rule in route.get("spec", {}).get("rules", [])
            for ref in rule.get("backendRefs", [])
        ]
        if not any(name.endswith("-canary") for name in backends):
            return True
    return False


def _needs_resync(application: dict, canary_moving: bool) -> bool:
    if _sync_in_flight(application):
        return False
    # Under a moving canary a resync would reset the weights it set, unless
    # the route never got its canary backend: then there are none to keep and
    # the Rollout is stuck at its first step until the route is reshaped.
    if canary_moving:
        return _route_lacks_canary(application)
    return _route_out_of_sync(application)


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
        if not _needs_resync(application, canary_moving):
            _stuck_once.discard(name)
            _resynced.discard(name)
            continue
        if name in _resynced:
            continue
        if name not in _stuck_once:
            _stuck_once.add(name)
            continue
        _stuck_once.discard(name)
        _resynced.add(name)
        kube_merge_patch(
            f"{APPLICATIONS_PATH}/{name}",
            {
                "operation": {
                    "initiatedBy": {"username": "cmp-release-recorder"},
                    "sync": {"syncOptions": ["ServerSideApply=true"]},
                }
            },
        )
        logger.info("Re-synced %s: its HTTPRoute kept a stale shape", name)
        synced.append(name)
    return synced
