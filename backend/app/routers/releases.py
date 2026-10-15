"""
Release tab: follow and steer an app's progressive release (Argo Rollouts).

Reading needs any role on the app's project, acting needs member, like the
``release.view`` / ``release.operate`` capabilities of the frontend.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment
from app.models.release import ReleaseEvent
from app.routers.deployments import _get_app_or_404
from app.schemas.release import (
    AbortRequest,
    PromoteRequest,
    ReleaseEventRead,
    ReleaseView,
    SkipRequest,
)
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError
from app.services.release import history, sync
from app.services.release.source import (
    KubeReleaseSource,
    ReleaseSource,
    RolloutSnapshot,
)
from app.services.release.view import (
    ABORT_PATCH,
    RETRY_PATCH,
    promote_patch,
    skip_patch,
)

router = APIRouter(prefix="/deployments", tags=["Releases"])

CurrentUser = Annotated[dict, Depends(get_current_user)]

POLL_SECONDS = 2.0
KEEPALIVE_SECONDS = 15.0

_source = KubeReleaseSource()


def get_release_source() -> ReleaseSource:
    return _source


Source = Annotated[ReleaseSource, Depends(get_release_source)]


def _compute(
    db: Session, deployment: Deployment, source: ReleaseSource
) -> tuple[ReleaseView, RolloutSnapshot | None, ReleaseEvent | None]:
    try:
        return sync.compute(db, deployment, source)
    except KubeUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"Cluster unreachable: {exc}"
        ) from exc


def _actor(token: dict) -> str:
    # User tokens carry no ``sub`` claim in this realm.
    return token.get("preferred_username") or token.get("sub") or "unknown"


@router.get("/{deployment_id}/release", response_model=ReleaseView)
def get_release(
    deployment_id: int,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
):
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.GUEST)
    return _compute(db, deployment, source)[0]


@router.get("/{deployment_id}/releases", response_model=list[ReleaseEventRead])
def list_releases(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    _get_app_or_404(db, token, deployment_id, ProjectRole.GUEST)
    return history.list_events(db, deployment_id)


def _poll_frame(deployment_id: int, source: ReleaseSource) -> str | None:
    """One SSE frame with the current view, a comment on a cluster error."""
    with SessionLocal() as db:
        deployment = db.get(Deployment, deployment_id)
        if deployment is None:
            return None
        try:
            view = _compute(db, deployment, source)[0]
        except HTTPException as exc:
            # A comment: the client parses every data line as a view.
            return f": {exc.detail}\n\n"
    return f"data: {view.model_dump_json(by_alias=True)}\n\n"


@dataclass
class _Watch:
    """The latest frame of one app, shared by every stream open on it."""

    listeners: int = 0
    frame: str | None = None
    version: int = 0
    gone: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    poller: asyncio.Task | None = None


_watches: dict[int, _Watch] = {}


async def _poll_watch(
    deployment_id: int, watch: _Watch, source: ReleaseSource
) -> None:
    try:
        while watch.listeners:
            frame = await run_in_threadpool(_poll_frame, deployment_id, source)
            watch.gone = frame is None
            if watch.gone or frame != watch.frame:
                watch.frame = frame
                watch.version += 1
                watch.changed.set()
                watch.changed = asyncio.Event()
            if watch.gone:
                return
            await asyncio.sleep(POLL_SECONDS)
    finally:
        if _watches.get(deployment_id) is watch:
            del _watches[deployment_id]


def _join(deployment_id: int, source: ReleaseSource) -> _Watch:
    watch = _watches.get(deployment_id)
    if watch is None:
        watch = _watches[deployment_id] = _Watch()
        watch.listeners = 1
        watch.poller = asyncio.create_task(
            _poll_watch(deployment_id, watch, source)
        )
    else:
        watch.listeners += 1
    return watch


async def view_stream(
    deployment_id: int,
    source: ReleaseSource,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """
    Yield a frame each time the app's view changes.

    One poller per app however many streams are open: it stops when the
    last stream leaves, so the cluster load does not grow with open tabs.
    """
    watch = _join(deployment_id, source)
    seen = 0
    try:
        while not await is_disconnected():
            if watch.gone:
                return
            if watch.version != seen:
                seen = watch.version
                yield watch.frame or ""
                continue
            try:
                await asyncio.wait_for(
                    watch.changed.wait(), timeout=KEEPALIVE_SECONDS
                )
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        watch.listeners -= 1


@router.get("/{deployment_id}/release/stream")
async def stream_release(
    deployment_id: int,
    request: Request,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
):
    await run_in_threadpool(
        _get_app_or_404, db, token, deployment_id, ProjectRole.GUEST
    )
    return StreamingResponse(
        view_stream(deployment_id, source, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _act(
    db: Session,
    token: dict,
    deployment_id: int,
    source: ReleaseSource,
    decide: Callable[
        [ReleaseView, dict[str, Any]], tuple[dict[str, Any], str, bool]
    ],
    reason: str | None,
) -> ReleaseView:
    """
    Authorize, let *decide* validate and pick the patch, apply, record.

    *decide* returns ``(patch, action, forced)`` or raises.
    """
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    view, snapshot, event = _compute(db, deployment, source)
    if snapshot is None or event is None:
        raise HTTPException(
            status_code=409, detail="No progressive release for this app."
        )
    patch, action, forced = decide(view, snapshot.rollout)
    rollout = snapshot.rollout["metadata"]
    try:
        source.patch_status(rollout["namespace"], rollout["name"], patch)
    except KubeUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail=f"Rollout not updated: {exc}"
        ) from exc
    history.record_action(
        db,
        event,
        _actor(token),
        action,
        reason,
        view.current_step_index,
        forced,
    )
    return _compute(db, deployment, source)[0]


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _forbid_force(view: ReleaseView) -> None:
    if not view.allow_force:
        raise HTTPException(
            status_code=403,
            detail="Forcing a release is disabled for this app.",
        )


@router.post("/{deployment_id}/release/promote", response_model=ReleaseView)
def promote_release(
    deployment_id: int,
    payload: PromoteRequest,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
):
    """
    ``next`` releases the step waiting for a decision; ``full`` jumps to
    100 % and is recorded as forced, so it needs a reason.
    """

    def decide(view: ReleaseView, rollout: dict) -> tuple[dict, str, bool]:
        if payload.mode == "next":
            if not view.actions.promote:
                raise _conflict("No step is waiting for a decision.")
            return promote_patch(rollout, "next"), "promote", False
        if not view.in_progress or view.phase == "Aborted":
            raise _conflict("No release in progress.")
        _forbid_force(view)
        if payload.reason is None:
            raise HTTPException(
                status_code=422,
                detail="A reason is required to promote before the end.",
            )
        return promote_patch(rollout, "full"), "promote_full", True

    return _act(db, token, deployment_id, source, decide, payload.reason)


@router.post("/{deployment_id}/release/skip", response_model=ReleaseView)
def skip_release_step(
    deployment_id: int,
    payload: SkipRequest,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
):
    """Skip the running analysis step; recorded as forced."""

    def decide(view: ReleaseView, rollout: dict) -> tuple[dict, str, bool]:
        if not view.actions.skip:
            _forbid_force(view)
            raise _conflict("No analysis step is running.")
        return skip_patch(rollout), "skip", True

    return _act(db, token, deployment_id, source, decide, payload.reason)


@router.post("/{deployment_id}/release/abort", response_model=ReleaseView)
def abort_release(
    deployment_id: int,
    payload: AbortRequest,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
):
    def decide(view: ReleaseView, rollout: dict) -> tuple[dict, str, bool]:
        if not view.actions.abort:
            raise _conflict("No release in progress.")
        return ABORT_PATCH, "abort", False

    return _act(db, token, deployment_id, source, decide, payload.reason)


@router.post("/{deployment_id}/release/retry", response_model=ReleaseView)
def retry_release(
    deployment_id: int,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
):
    """Restart an aborted release of the same revision from its first step."""

    def decide(view: ReleaseView, rollout: dict) -> tuple[dict, str, bool]:
        if not view.actions.retry:
            raise _conflict("Only an aborted release can be retried.")
        return RETRY_PATCH, "retry", False

    return _act(db, token, deployment_id, source, decide, None)
