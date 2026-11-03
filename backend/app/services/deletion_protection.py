"""
Per-project deletion protection.

The registry record's ``spec.deletionProtection`` is the switch. Argo CD copies
it onto the project's Applications, AppProjects and prod namespaces as the
``cnp.3istor.com/deletion-protected`` label, and an admission policy refuses to
delete an object while that label is ``"true"``. Lifting the protection takes
Argo CD about a minute to reach the cluster, so a deletion also waits for the
labels to be gone, or the cluster would refuse it halfway through.
"""

import logging

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.models.project import TargetCloud
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
)
from app.services.project_registry import RegistryError, is_deletion_protected

logger = logging.getLogger(__name__)

LABEL = "cnp.3istor.com/deletion-protected"

_ARGOCD = "/apis/argoproj.io/v1alpha1/namespaces/argocd"


def still_labelled(
    project_name: str, cloud: TargetCloud, app_name: str | None = None
) -> bool:
    """
    Whether a cluster object of the project, or of one of its apps, still
    carries ``LABEL=true``.

    Applications and AppProjects live on the on-prem cluster, namespaces on
    *cloud*. AppProjects are only checked for a whole project: they go with
    the record, not with one app.
    """
    selector = f"cnp.3istor.com/project={project_name},{LABEL}=true"
    app_selector = (
        f"{selector},cnp.3istor.com/app={app_name}" if app_name else selector
    )
    # ponytail: fail open, the registry flag is the guard; an unreachable
    # cluster skips the label check (ceiling: the policy then refuses late).
    try:
        if kube_list(
            f"{_ARGOCD}/applications", {"labelSelector": app_selector}
        ):
            return True
        if not app_name and kube_list(
            f"{_ARGOCD}/appprojects", {"labelSelector": selector}
        ):
            return True
        namespaces = kube_list(
            "/api/v1/namespaces", {"labelSelector": selector}, cloud=cloud
        )
    except (KubeUnavailableError, ClusterNotConnectedError) as exc:
        logger.warning(
            "Could not check the deletion protection labels of '%s': %s",
            project_name,
            exc,
        )
        return False
    if app_name:
        namespaces = [
            ns
            for ns in namespaces
            if ns["metadata"]["name"] == f"{project_name}-{app_name}"
        ]
    return bool(namespaces)


async def assert_deletable(
    project_name: str, cloud: TargetCloud, app_name: str | None = None
) -> None:
    """
    Raise unless the project, or one of its apps, may be deleted now.

    Raises:
        HTTPException: 409 while protection is on or its labels are still on
            the cluster, 502 if the registry cannot be read: an unknown flag
            never lets a deletion through.
    """
    try:
        protected = await is_deletion_protected(project_name)
    except RegistryError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not check deletion protection: {exc}",
        ) from exc
    if protected:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Deletion protection is on for project '{project_name}'. "
                "A project admin can turn it off in the CMP."
            ),
        )
    if await run_in_threadpool(still_labelled, project_name, cloud, app_name):
        raise HTTPException(
            status_code=409,
            detail=(
                "Deletion protection is being turned off on the cluster. "
                "Try again in a minute."
            ),
        )
