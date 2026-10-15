"""
Release tab: follow and steer an app's progressive release (Argo Rollouts).

Reading needs any role on the app's project, acting needs member, like the
``release.view`` / ``release.operate`` capabilities of the frontend.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from io import StringIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment
from app.models.release import ReleaseEvent
from app.routers.deployments import (
    _app_type_of,
    _authorize_app,
    _extract_repo_full_name,
    _get_app_or_404,
    _get_github_token_for_deployment,
    _get_kubernetes_deployment_or_404,
    _load_values_file,
    _yaml,
)
from app.schemas.release import (
    AbortRequest,
    PromoteRequest,
    ReleaseEventRead,
    ReleaseView,
    SkipRequest,
)
from app.services import app_security_data as security_data
from app.services import audit
from app.services.github_service import GitHubAppError, put_file_content
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError
from app.services.release import history
from app.services.release import settings as release_settings
from app.services.release import sync
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


def _release_files(deployment: Deployment) -> list[str]:
    return list(
        dict.fromkeys(security_data.values_files_for(_app_type_of(deployment)))
    )


def _holds(
    db: Session, token: dict, deployment: Deployment, role: ProjectRole
) -> bool:
    try:
        _authorize_app(db, token, deployment, role)
    except HTTPException:
        return False
    return True


async def _load_all(
    deployment: Deployment,
) -> tuple[str, str, list[tuple[str, Any, str]]]:
    gh_token = await _get_github_token_for_deployment(deployment)
    repo = _extract_repo_full_name(deployment.github_repo_url)
    loaded = []
    for file_path in _release_files(deployment):
        values, sha = await _load_values_file(gh_token, repo, file_path)
        loaded.append((file_path, values, sha))
    return gh_token, repo, loaded


@router.get(
    "/{deployment_id}/release/settings",
    response_model=release_settings.ReleaseSettingsRead,
)
async def get_release_settings(
    deployment_id: int,
    token: CurrentUser,
    db: Session = Depends(get_db),
):
    deployment = await run_in_threadpool(
        _get_kubernetes_deployment_or_404,
        deployment_id,
        db,
        token,
        ProjectRole.MEMBER,
    )
    can_edit = await run_in_threadpool(
        _holds, db, token, deployment, ProjectRole.ADMIN
    )
    _, _, loaded = await _load_all(deployment)
    declared = [values for _, values, _ in loaded if "release" in values]
    return release_settings.ReleaseSettingsRead(
        settings=release_settings.read(
            declared[0] if declared else loaded[0][1]
        ),
        files=[file_path for file_path, _, _ in loaded],
        can_edit=can_edit,
        declared=bool(declared),
    )


@router.put("/{deployment_id}/release/settings")
async def update_release_settings(
    deployment_id: int,
    payload: release_settings.ReleaseSettings,
    request: Request,
    token: CurrentUser,
    source: Source,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Commit the app's ``release`` block, like a developer editing it in Git.

    Refused while a canary is on its way: changing the steps under a running
    Rollout makes Argo Rollouts restart or skip them.
    """
    deployment = await run_in_threadpool(
        _get_kubernetes_deployment_or_404,
        deployment_id,
        db,
        token,
        ProjectRole.ADMIN,
    )
    view = (await run_in_threadpool(_compute, db, deployment, source))[0]
    if (
        view.phase in ("Progressing", "Paused")
        and view.canary_hash
        and view.canary_hash != view.stable_hash
    ):
        raise _conflict(
            "A release is in progress: promote or abort it before changing "
            "the release settings."
        )

    gh_token, repo, loaded = await _load_all(deployment)
    targets = []
    for file_path, values, sha in loaded:
        refused = release_settings.refusal(values, payload)
        targets.append((file_path, values, sha, refused))
    if all(refused for *_, refused in targets):
        raise HTTPException(status_code=422, detail=targets[0][3])

    commits: dict[str, str] = {}
    applied: dict[str, str] = {}
    for file_path, values, sha, refused in targets:
        # A fullstack component that cannot take a canary (the backend has no
        # route of its own) keeps rolling while the exposed one goes canary.
        wanted = (
            payload.model_copy(update={"strategy": "rolling"})
            if refused
            else payload
        )
        applied[file_path] = wanted.strategy
        if "release" in values and release_settings.read(values) == wanted:
            continue
        audit.note(
            request,
            **{
                f"{file_path}:release": wanted.model_dump(
                    by_alias=True, mode="json"
                )
            },
        )
        release_settings.apply(values, wanted)
        out = StringIO()
        _yaml.dump(values, out)
        try:
            result = await put_file_content(
                installation_token=gh_token,
                repo_full_name=repo,
                file_path=file_path,
                content=out.getvalue(),
                message=(
                    f"chore(release): {wanted.strategy}, preset "
                    f"{wanted.preset} via CMP"
                ),
                sha=sha,
            )
        except GitHubAppError as exc:
            status_code = 409 if "conflict" in str(exc).lower() else 502
            raise HTTPException(
                status_code=status_code, detail=str(exc)
            ) from exc
        commits[file_path] = result.get("commit", {}).get("sha", "")

    return {"repo": repo, "commits": commits, "strategies": applied}
