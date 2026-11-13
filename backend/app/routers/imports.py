"""
Repository import API (CNP-238, CNP-241).

Users: /api/projects/{project_name}/import... (authz.py roles, step-up on
apply and delete). The central workflow: /api/import/worker/{run_id}/...,
authenticated by its GitHub OIDC token only.
"""

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services import audit
from app.services.authz import RequireAdmin, RequireMember
from app.services.github_service import GitHubAppError
from app.services.imports import access, deploy, service
from app.services.imports.oidc import WorkerTokenError, verify_worker_token
from app.services.imports.service import ImportRefused
from app.services.project_registry import RegistryError
from app.services.step_up import assert_step_up

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Imports"])
worker = APIRouter(prefix="/import/worker", tags=["Import worker"])

DbSession = Annotated[Session, Depends(get_db)]


def _refused(exc: ImportRefused) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _enabled() -> None:
    try:
        service.ensure_enabled()
    except ImportRefused as exc:
        raise _refused(exc) from exc


class ImportRequest(BaseModel):
    repo_full_name: str = Field(
        pattern=r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}$"
    )
    branch: str | None = Field(default=None, max_length=255)


class MemoryEdit(BaseModel):
    requests_mem_mib: int = Field(ge=16, le=8192)
    limits_mem_mib: int = Field(ge=16, le=8192)


class ProbeEdit(BaseModel):
    kind: Literal["tcp", "http"]
    path: str | None = Field(default=None, max_length=200)
    port: int = Field(ge=1, le=65535)


class LinkRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from", max_length=40)
    to: str = Field(max_length=40)


class PlanEdits(BaseModel):
    exclude: list[str] = Field(default_factory=list, max_length=100)
    rename: dict[str, str] = Field(default_factory=dict)
    public: dict[str, bool] = Field(default_factory=dict)
    memory: dict[str, MemoryEdit] = Field(default_factory=dict)
    port: dict[str, int] = Field(default_factory=dict)
    probe: dict[str, ProbeEdit] = Field(default_factory=dict)
    drop_links: list[LinkRef] = Field(default_factory=list, max_length=200)


async def _recheck_repository(db: Session, claims: dict, source) -> None:
    """
    The caller must still be able to import the bound repository, the same
    one by id, before a build mints a token on it.
    """
    try:
        repo = await access.resolve_repository(
            db, claims, source.repo_full_name, repo_id=source.repo_id
        )
    except access.RepoAccessError as exc:
        raise HTTPException(
            403,
            f"You can no longer import {source.repo_full_name}: {exc}",
        ) from exc
    except GitHubAppError as exc:
        raise HTTPException(502, f"GitHub could not be read: {exc}") from exc
    if source.installation_id != repo.installation_id:
        source.installation_id = repo.installation_id
        db.commit()


@router.get("/{project_name}/import/repos")
async def list_importable_repos(ctx: RequireAdmin, db: DbSession):
    _enabled()
    try:
        repos = await access.importable_repositories(db, ctx.token)
    except GitHubAppError as exc:
        raise HTTPException(502, f"GitHub could not be read: {exc}") from exc
    return {"repositories": [r.public() for r in repos]}


@router.post("/{project_name}/imports", status_code=202)
async def create_import(
    body: ImportRequest, ctx: RequireAdmin, db: DbSession, request: Request
):
    _enabled()
    try:
        repo = await access.resolve_repository(
            db, ctx.token, body.repo_full_name
        )
    except access.RepoAccessError as exc:
        raise HTTPException(403, str(exc)) from exc
    except GitHubAppError as exc:
        raise HTTPException(502, f"GitHub could not be read: {exc}") from exc
    try:
        run = await service.start_analysis(
            db, ctx.project_name, ctx.username, repo, body.branch
        )
    except ImportRefused as exc:
        raise _refused(exc) from exc
    audit.note(request, target=repo.full_name)
    return {"run": service.run_view(run)}


@router.get("/{project_name}/imports")
async def list_imports(ctx: RequireMember, db: DbSession):
    _enabled()
    service.expire_stale(db)
    sources = (
        db.query(service.ImportSource)
        .filter_by(project_name=ctx.project_name)
        .order_by(service.ImportSource.created_at)
        .all()
    )
    return {"imports": [service.source_view(s) for s in sources]}


@router.get("/{project_name}/imports/runs/{run_id}")
async def get_import_run(run_id: str, ctx: RequireMember, db: DbSession):
    _enabled()
    try:
        run = service.get_run(db, ctx.project_name, run_id)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    return {
        **service.run_view(run),
        "capacity": service.capacity_view(db, ctx.project_name, run.source),
        "services": [service.service_view(s) for s in run.source.services],
    }


@router.post("/{project_name}/imports/{source_id}/apply", status_code=202)
async def apply_import(
    source_id: str,
    edits: PlanEdits,
    ctx: RequireAdmin,
    db: DbSession,
    request: Request,
):
    _enabled()
    assert_step_up(ctx.token)
    try:
        source = service.get_source(db, ctx.project_name, source_id)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    await _recheck_repository(db, ctx.token, source)
    try:
        run = await service.approve(
            db,
            ctx.project_name,
            ctx.username,
            source,
            edits.model_dump(mode="json", by_alias=True),
        )
    except ImportRefused as exc:
        raise _refused(exc) from exc
    audit.note(request, target=source.repo_full_name)
    return {"run": service.run_view(run)}


@router.post("/{project_name}/imports/{source_id}/rebuild", status_code=202)
async def rebuild_import(
    source_id: str, ctx: RequireAdmin, db: DbSession, request: Request
):
    _enabled()
    assert_step_up(ctx.token)
    try:
        source = service.get_source(db, ctx.project_name, source_id)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    await _recheck_repository(db, ctx.token, source)
    try:
        run = await service.rebuild(db, ctx.username, source)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    audit.note(request, target=source.repo_full_name)
    return {"run": service.run_view(run)}


@router.delete("/{project_name}/imports/{source_id}", status_code=202)
async def delete_import(
    source_id: str, ctx: RequireAdmin, db: DbSession, request: Request
):
    _enabled()
    assert_step_up(ctx.token)
    try:
        source = service.get_source(db, ctx.project_name, source_id)
        target = source.repo_full_name
        await deploy.remove(db, ctx.username, source)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    except RegistryError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit.note(request, target=target)
    return {"message": "Removal started"}


# ── Worker ──────────────────────────────────────────────────────────────────


class WorkerCall(BaseModel):
    attempt_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class PlanCall(WorkerCall):
    plan: dict[str, Any]


class PublishedImage(BaseModel):
    app: str
    image: str
    digest: str
    sbom_summary: dict[str, Any] = Field(default_factory=dict)
    vulns: dict[str, int] = Field(default_factory=dict)


class PublishCall(WorkerCall):
    services: list[PublishedImage] = Field(max_length=100)


class FailCall(WorkerCall):
    stage: str = Field(max_length=32)
    message: str = Field(default="", max_length=20000)


async def worker_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """The workflow's OIDC claims; anything else is a bare 401."""
    _enabled()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Unauthorized")
    try:
        return await run_in_threadpool(verify_worker_token, token)
    except WorkerTokenError as exc:
        logger.warning("Import worker token refused: %s", exc)
        raise HTTPException(401, "Unauthorized") from exc


WorkerClaims = Annotated[dict, Depends(worker_claims)]


async def _read_body(request: Request, limit: int) -> bytes:
    """
    The raw body, refused past *limit* bytes whatever Content-Length says.
    Worker endpoints declare no body parameter: FastAPI would read and parse
    it before the OIDC dependency ran.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise HTTPException(400, "Malformed Content-Length")
        if int(declared) > limit:
            raise HTTPException(413, "Request body too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, "Request body too large")
    return bytes(body)


async def _worker_body(request: Request, model: type[BaseModel], limit: int):
    raw = await _read_body(request, limit)
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(422, "Malformed worker call") from exc


def _run_for(db: Session, run_id: str, attempt_id: str, claims: dict):
    try:
        return service.worker_run(db, run_id, attempt_id, claims)
    except ImportRefused as exc:
        raise _refused(exc) from exc


@worker.post("/{run_id}/claim")
async def worker_claim(
    run_id: str, request: Request, claims: WorkerClaims, db: DbSession
):
    body = await _worker_body(
        request, WorkerCall, settings.IMPORT_WORKER_MAX_BODY_BYTES
    )
    run = _run_for(db, run_id, body.attempt_id, claims)
    try:
        return await service.claim(db, run, claims)
    except ImportRefused as exc:
        raise _refused(exc) from exc


@worker.post("/{run_id}/plan", status_code=204)
async def worker_plan(
    run_id: str, request: Request, claims: WorkerClaims, db: DbSession
):
    body = await _worker_body(request, PlanCall, service.MAX_PLAN_BYTES)
    run = _run_for(db, run_id, body.attempt_id, claims)
    try:
        service.receive_plan(db, run, body.plan)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    return Response(status_code=204)


@worker.post("/{run_id}/publish", status_code=202)
async def worker_publish(
    run_id: str, request: Request, claims: WorkerClaims, db: DbSession
):
    body = await _worker_body(
        request, PublishCall, settings.IMPORT_WORKER_MAX_BODY_BYTES
    )
    run = _run_for(db, run_id, body.attempt_id, claims)
    try:
        service.receive_publish(
            db, run, [s.model_dump() for s in body.services]
        )
    except ImportRefused as exc:
        raise _refused(exc) from exc
    return {"status": "deploying"}


@worker.post("/{run_id}/fail", status_code=204)
async def worker_fail(
    run_id: str, request: Request, claims: WorkerClaims, db: DbSession
):
    body = await _worker_body(
        request, FailCall, settings.IMPORT_WORKER_MAX_BODY_BYTES
    )
    run = _run_for(db, run_id, body.attempt_id, claims)
    try:
        service.receive_failure(db, run, body.stage, body.message)
    except ImportRefused as exc:
        raise _refused(exc) from exc
    return Response(status_code=204)
