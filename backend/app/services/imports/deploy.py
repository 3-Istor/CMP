"""
Deployment of a published build, and removal of an import (spec E).

Every step is idempotent, so a run left ``running`` when the CMP stopped is
deployed again from the start on the next boot.
"""

import asyncio
import base64
import json
import logging
import secrets

import httpx
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.imports import (
    ImportRun,
    ImportService,
    ImportSource,
    RunStatus,
)
from app.services import kube_client, topology_cache, vault_client
from app.services.imports import dns, generate
from app.services.imports.generate import AppSpec, PlanError
from app.services.imports.registry_commit import commit_record_change
from app.services.imports.service import (
    ImportRefused,
    _active_run,
    _spawn,
    _system_event,
    image_name,
    redact,
)
from app.services.project_registry import list_records
from app.services.registry_names import check_app, project_name_of

logger = logging.getLogger(__name__)

TEMPLATE_KIND = {"service": "import-service", "backing": "import-backing"}
BACKING_TITLE = {
    "mongodb": "MongoDB",
    "redis": "Redis",
    "neo4j": "Neo4j",
    "elasticsearch": "Elasticsearch",
}


# ── Deployment after publish ────────────────────────────────────────────────


async def _ensure_pull_secret(project: str) -> None:
    mount = f"project-{project}"
    if await vault_client.read_kv(mount, "system/registry"):
        return
    user = settings.GITHUB_REGISTRY_USERNAME
    token = settings.GITHUB_REGISTRY_TOKEN
    if not (user and token):
        raise RuntimeError("GITHUB_REGISTRY_USERNAME/TOKEN are not set")
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    await vault_client.write_kv(
        mount,
        "system/registry",
        {
            ".dockerconfigjson": json.dumps(
                {
                    "auths": {
                        host: {
                            "username": user,
                            "password": token,
                            "auth": auth,
                        }
                        for host in ("ghcr.io", "https://ghcr.io")
                    }
                }
            )
        },
    )


async def _ensure_credentials(project: str, specs: list[AppSpec]) -> None:
    """Generate each credential once; consumers get a copy of theirs."""
    mount = f"project-{project}"
    passwords: dict[str, str] = {}
    for spec in specs:
        if spec.backing_kind != "neo4j":
            continue
        path = generate.secret_path(spec.name)
        current = await vault_client.read_kv(mount, path)
        auth = str(current.get("NEO4J_AUTH") or "")
        if not auth.startswith("neo4j/"):
            auth = f"neo4j/{secrets.token_urlsafe(24)}"
            await vault_client.write_kv(mount, path, {"NEO4J_AUTH": auth})
        passwords[spec.name] = auth.split("/", 1)[1]
    for spec in specs:
        if not spec.secret_env:
            continue
        data = {}
        for item in spec.secret_env:
            if item["key"] == "url":
                data[item["env"]] = item["value"]
            elif item["key"] == "username":
                data[item["env"]] = "neo4j"
            elif item["backing"] in passwords:
                data[item["env"]] = passwords[item["backing"]]
        await vault_client.write_kv(
            mount, generate.secret_path(spec.name), data
        )


def _upsert_deployment(
    db: Session, project: str, service: ImportService
) -> Deployment:
    row = (
        db.get(Deployment, service.deployment_id)
        if service.deployment_id
        else None
    )
    if row is None or row.status == DeploymentStatus.DELETED:
        backing = service.spec.get("backing_kind") or ""
        row = Deployment(
            name=service.app_name,
            template_id=TEMPLATE_KIND[service.kind],
            template_name=BACKING_TITLE.get(backing, "Imported service"),
            template_category="import",
            provider_type=ProviderType.KUBERNETES,
            project_id=project,
            k8s_namespace=f"{project}-{service.app_name}",
            argocd_app_name=f"{project}-{service.app_name}-app",
            status=DeploymentStatus.DEPLOYING,
            step_message="Waiting for Argo CD",
        )
        db.add(row)
        db.flush()
        service.deployment_id = row.id
    else:
        row.status = DeploymentStatus.DEPLOYING
        row.step_message = "Waiting for Argo CD"
    return row


def _argo_state(app_name: str, image: str) -> str:
    """synced, failed or pending for one Application."""
    body = kube_client.kube_get(
        f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/{app_name}"
    )
    if not body:
        return "pending"
    status = body.get("status") or {}
    health = (status.get("health") or {}).get("status")
    sync = (status.get("sync") or {}).get("status")
    images = (status.get("summary") or {}).get("images") or []
    if health == "Degraded" and image in images:
        return "failed"
    if sync == "Synced" and health == "Healthy" and image in images:
        return "synced"
    return "pending"


async def _wait_for_argo(
    project: str, apps: dict[str, str], timeout: float
) -> dict[str, str]:
    """Final state of each app (name -> expected image ref)."""
    deadline = asyncio.get_running_loop().time() + timeout
    states = {app: "pending" for app in apps}
    while True:
        for app, image in apps.items():
            if states[app] != "pending":
                continue
            try:
                states[app] = await run_in_threadpool(
                    _argo_state, f"{project}-{app}-app", image
                )
            except kube_client.KubeUnavailableError as exc:
                logger.warning("Argo CD read for %s failed: %s", app, exc)
        if all(s != "pending" for s in states.values()):
            return states
        if asyncio.get_running_loop().time() > deadline:
            return {
                a: ("timeout" if s == "pending" else s)
                for a, s in states.items()
            }
        await asyncio.sleep(10)


async def _smoke(url: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as client:
        for _ in range(12):
            try:
                response = await client.get(url)
                if response.status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(10)
    return False


async def deploy(run_id: str) -> None:
    """Idempotent: safe to run again for a run left in ``running``."""
    with SessionLocal() as db:
        run = db.get(ImportRun, run_id)
        if run is None or run.status != RunStatus.RUNNING.value:
            return
        try:
            await _deploy(db, run)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Import deploy of run %s failed", run_id)
            db.rollback()
            run = db.get(ImportRun, run_id)
            run.status = RunStatus.FAILED.value
            run.report = {
                **(run.report or {}),
                "stage": "deploy",
                "error": redact(str(exc)),
            }
            if isinstance(exc, PlanError):
                _refuse_services(run, exc)
            db.commit()
            _system_event(run, "import.deploy", False, error=redact(str(exc)))


def _refuse_services(run: ImportRun, exc: PlanError) -> None:
    """A running app keeps its last version; the others never started."""
    message = redact(f"Not deployed, the registry refused it: {exc}")
    for row in run.source.services:
        row.message = message
        if row.status != "running":
            row.status = "failed"


async def _check_names_at_head(
    project: str, record: dict, head: str, specs: list[AppSpec]
) -> None:
    """
    The names checked at approval, checked again against the registry as
    it is when the commit is made: another project may have taken one since.

    Raises:
        PlanError: On any collision; the commit is not made.
    """
    records = [
        r for r in await list_records(head) if project_name_of(r) != project
    ]
    records.append(record)
    problems: list[str] = []
    for spec in specs:
        entry: dict = {"name": spec.name, "type": spec.kind}
        if spec.public:
            entry["hostnames"] = {
                "prod": generate.hostname(project, spec.name)
            }
        problems += [
            f"app '{spec.name}': {p}"
            for p in check_app(project, entry, records)
        ]
    if problems:
        raise PlanError(sorted(set(problems)))


async def _deploy(db: Session, run: ImportRun) -> None:
    source = run.source
    project = source.project_name
    images = (run.report or {}).get("images") or {}
    rows = {s.app_name: s for s in source.services}
    specs = [AppSpec.from_dict(r.spec) for r in rows.values()]
    digests = {app: item["digest"] for app, item in images.items()}
    for app, row in rows.items():
        if row.kind == "service" and app not in digests and row.last_digest:
            digests[app] = row.last_digest

    await _ensure_pull_secret(project)
    await _ensure_credentials(project, specs)

    async def change(record: dict, head: str) -> dict[str, str | None]:
        await _check_names_at_head(
            project,
            record,
            head,
            [s for s in specs if s.kind == "backing" or s.name in digests],
        )
        return generate.apply_to_record(
            record,
            project,
            specs,
            digests,
            source.repo_full_name,
            run.sha,
            settings.IMPORT_IMAGE_PREFIX,
        )

    commit = await commit_record_change(
        project,
        change,
        f"feat(registry): import {source.repo_full_name}@{run.sha[:12]} "
        f"into {project}",
    )

    expected: dict[str, str] = {}
    for app, row in rows.items():
        spec = AppSpec.from_dict(row.spec)
        if row.kind == "service":
            if app not in digests:
                continue
            row.last_digest = digests[app]
            row.last_sha = run.sha
            expected[app] = f"{image_name(project, app)}@{digests[app]}"
        else:
            preset = generate.load_preset(spec.backing_kind)["image"]
            expected[app] = f"{preset['repository']}@{preset['digest']}"
        row.status = "deploying"
        _upsert_deployment(db, project, row)
    run.report = {**(run.report or {}), "commit": commit}
    topology_cache.invalidate(project)
    # Committed before DNS: a failure from here on must still let remove()
    # recognise these apps as the import's and clean them up.
    db.commit()

    for spec in specs:
        if spec.public:
            await dns.ensure_cname(
                project, spec.name, generate.hostname(project, spec.name)
            )

    states = await _wait_for_argo(
        project, expected, settings.IMPORT_ARGO_TIMEOUT_SECONDS
    )
    failed = []
    for app, state in states.items():
        row = rows[app]
        deployment = db.get(Deployment, row.deployment_id)
        ok = state == "synced"
        if ok and row.spec.get("public"):
            ok = await _smoke(f"https://{generate.hostname(project, app)}/")
            state = "synced" if ok else "unreachable"
        row.status = "running" if ok else "failed"
        row.message = "" if ok else f"Argo CD: {state}"
        deployment.status = (
            DeploymentStatus.RUNNING if ok else DeploymentStatus.DEGRADED
        )
        deployment.step_message = "Running" if ok else f"Argo CD: {state}"
        if not ok:
            failed.append(app)
    run.status = (
        RunStatus.FAILED.value if failed else RunStatus.SUCCEEDED.value
    )
    run.report = {**(run.report or {}), "states": states}
    db.commit()
    topology_cache.invalidate(project)
    _system_event(
        run, "import.deploy", not failed, commit=commit, failed=failed
    )


def resume_interrupted() -> None:
    """Restart the deploy of runs left running when the CMP stopped."""
    with SessionLocal() as db:
        ids = [
            r.id
            for r in db.query(ImportRun)
            .filter(ImportRun.status == RunStatus.RUNNING.value)
            .all()
        ]
    for run_id in ids:
        _spawn(deploy(run_id))


# ── Removal ─────────────────────────────────────────────────────────────────


async def remove(db: Session, user: str, source: ImportSource) -> None:
    """
    Take the import's apps out of the registry (one commit), then delete
    the namespaces, volumes included, of the apps that commit took out (D17:
    the user confirmed with step-up). An app of the record under the same name that
    this import did not put there stays, with its namespace.
    """
    if _active_run(source) is not None:
        raise ImportRefused("A run of this import is in progress")
    project = source.project_name
    services = {s.app_name for s in source.services if s.kind == "service"}
    deployed = {s.app_name for s in source.services if s.deployment_id}
    removed: set[str] = set()
    if services or deployed:

        async def change(record: dict, head: str) -> dict[str, str | None]:
            del head
            removed.clear()
            removed.update(
                generate.owned_by_import(
                    record,
                    source.repo_full_name,
                    services,
                    {
                        s.app_name
                        for s in source.services
                        if s.kind == "backing" and s.deployment_id
                    },
                )
            )
            return generate.remove_from_record(record, project, removed)

        await commit_record_change(
            project,
            change,
            f"feat(registry): remove import {source.repo_full_name} "
            f"from {project}",
        )
        topology_cache.invalidate(project)
    for service in source.services:
        if service.app_name in removed and service.spec.get("public"):
            app = service.app_name
            try:
                await dns.remove_cname(
                    project, app, generate.hostname(project, app)
                )
            except dns.DnsError as exc:
                logger.warning("DNS cleanup of %s failed: %s", app, exc)
    for service in source.services:
        if service.deployment_id:
            row = db.get(Deployment, service.deployment_id)
            if row is not None:
                row.status = DeploymentStatus.DELETING
                row.step_message = "Removing"
    db.commit()
    _spawn(_cleanup_namespaces(project, sorted(removed), source.id))
    logger.info(
        "%s removes import %s (registry entries: %s)",
        user,
        source.repo_full_name,
        sorted(removed),
    )


async def _cleanup_namespaces(
    project: str, apps: list[str], source_id: str
) -> None:
    """Delete each namespace once Argo CD dropped its Application."""
    deadline = asyncio.get_running_loop().time() + 600
    pending = list(apps)
    while pending and asyncio.get_running_loop().time() < deadline:
        for app in list(pending):
            try:
                gone = (
                    await run_in_threadpool(
                        kube_client.kube_get,
                        "/apis/argoproj.io/v1alpha1/namespaces/argocd/"
                        f"applications/{project}-{app}-app",
                    )
                    is None
                )
                if gone:
                    await run_in_threadpool(
                        kube_client.kube_delete,
                        f"/api/v1/namespaces/{project}-{app}",
                    )
                    pending.remove(app)
            except kube_client.KubeUnavailableError as exc:
                logger.warning("Namespace cleanup of %s: %s", app, exc)
        if pending:
            await asyncio.sleep(10)
    with SessionLocal() as db:
        source = db.get(ImportSource, source_id)
        for service in source.services if source else []:
            if service.deployment_id:
                row = db.get(Deployment, service.deployment_id)
                if row is not None:
                    row.status = (
                        DeploymentStatus.DELETED
                        if service.app_name not in pending
                        else DeploymentStatus.FAILED
                    )
        if source is not None and not pending:
            db.delete(source)
        db.commit()
    if pending:
        logger.error("Namespaces left behind for %s: %s", project, pending)
