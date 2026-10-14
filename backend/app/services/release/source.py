"""
Where the Release tab reads an app's Argo Rollout from.

``ReleaseSource`` is the seam: the in-cluster implementation below talks to
the Kubernetes API with the CMP's service account. A remote cluster will get
an implementation over the Argo CD API instead, without touching the view.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.services.kube_client import kube_get, kube_list, kube_merge_patch

logger = logging.getLogger(__name__)

ROLLOUTS_API = "/apis/argoproj.io/v1alpha1"
HASH_LABEL = "rollouts-pod-template-hash"


@dataclass
class RolloutSnapshot:
    """Everything the view is computed from, as the API returned it."""

    rollout: dict[str, Any]
    analysis_runs: list[dict[str, Any]] = field(default_factory=list)
    replica_sets: list[dict[str, Any]] = field(default_factory=list)
    # Keyed by (cluster_scope, template_name).
    templates: dict[tuple[bool, str], dict[str, Any]] = field(
        default_factory=dict
    )


class ReleaseSource(Protocol):
    def fetch(self, namespace: str, app_name: str) -> RolloutSnapshot | None:
        """The app's Rollout and its children, or None if it has none."""

    def patch_status(
        self, namespace: str, rollout_name: str, body: dict[str, Any]
    ) -> None:
        """Merge-patch the Rollout's status subresource."""


def _owned_by(obj: dict[str, Any], uid: str) -> bool:
    return any(
        ref.get("uid") == uid
        for ref in obj.get("metadata", {}).get("ownerReferences", [])
    )


def pick_rollout(
    rollouts: list[dict[str, Any]], app_name: str
) -> dict[str, Any] | None:
    """
    The Rollout of the app: the one named after it, else the only one.

    A fullstack app renders one Rollout per component; Lot 1 shows the first
    by name.
    """
    if not rollouts:
        return None
    for rollout in rollouts:
        if rollout["metadata"]["name"] == app_name:
            return rollout
    ordered = sorted(rollouts, key=lambda r: r["metadata"]["name"])
    if len(ordered) > 1:
        logger.info(
            "Several Rollouts for app %s, showing %s",
            app_name,
            ordered[0]["metadata"]["name"],
        )
    return ordered[0]


def analysis_templates(rollout: dict[str, Any]) -> set[tuple[bool, str]]:
    steps = (
        rollout.get("spec", {})
        .get("strategy", {})
        .get("canary", {})
        .get("steps", [])
    )
    refs: set[tuple[bool, str]] = set()
    for step in steps:
        for template in (step.get("analysis") or {}).get("templates", []):
            refs.add(
                (bool(template.get("clusterScope")), template["templateName"])
            )
    return refs


class KubeReleaseSource:
    """Reads the Rollout through the CMP's own service account."""

    def fetch(self, namespace: str, app_name: str) -> RolloutSnapshot | None:
        rollout = pick_rollout(
            kube_list(f"{ROLLOUTS_API}/namespaces/{namespace}/rollouts"),
            app_name,
        )
        if rollout is None:
            return None
        uid = rollout["metadata"]["uid"]
        status = rollout.get("status", {})
        hashes = {
            h
            for h in (status.get("stableRS"), status.get("currentPodHash"))
            if h
        }

        snapshot = RolloutSnapshot(rollout=rollout)
        if not hashes:
            return snapshot

        selector = f"{HASH_LABEL} in ({','.join(sorted(hashes))})"
        snapshot.replica_sets = [
            rs
            for rs in kube_list(
                f"/apis/apps/v1/namespaces/{namespace}/replicasets",
                {"labelSelector": selector},
            )
            if _owned_by(rs, uid)
        ]
        if status.get("currentPodHash"):
            runs = [
                run
                for run in kube_list(
                    f"{ROLLOUTS_API}/namespaces/{namespace}/analysisruns",
                    {
                        "labelSelector": (
                            f"{HASH_LABEL}={status['currentPodHash']}"
                        )
                    },
                )
                if _owned_by(run, uid)
            ]
            snapshot.analysis_runs = sorted(
                runs, key=lambda r: r["metadata"].get("creationTimestamp", "")
            )
        for cluster_scope, name in analysis_templates(rollout):
            path = (
                f"{ROLLOUTS_API}/clusteranalysistemplates/{name}"
                if cluster_scope
                else f"{ROLLOUTS_API}/namespaces/{namespace}"
                f"/analysistemplates/{name}"
            )
            template = kube_get(path)
            if template is not None:
                snapshot.templates[(cluster_scope, name)] = template
        return snapshot

    def patch_status(
        self, namespace: str, rollout_name: str, body: dict[str, Any]
    ) -> None:
        kube_merge_patch(
            f"{ROLLOUTS_API}/namespaces/{namespace}/rollouts/"
            f"{rollout_name}/status",
            body,
        )
