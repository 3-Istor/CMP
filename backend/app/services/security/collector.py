"""
Background collection of scan results into the CMP database.

The scanners run on their own schedules: Kyverno and Trivy Operator in the
cluster, the security workflow in GitHub Actions, CNPG backups at night. This
loop only reads what they produced, so opening the security page never
starts a scan nor calls GitHub or the cluster.

- Cluster pass, every 15 min: Kyverno, Trivy Operator, CNPG, Cilium.
- GitHub pass, every hour: the CI report on the default branch, exposure.
  Apps with a scan requested by hand are polled every minute until the new
  report lands.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ruamel.yaml import YAML
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.security import SecurityScan
from app.services import app_security_data as security_data
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    get_default_branch,
    get_file_content,
    get_installation_token,
    get_latest_artifact,
)
from app.services.kube_client import KubeUnavailableError, kube_get, kube_list
from app.services.project_registry import RegistryError, read_record
from app.services.security import alerts
from app.services.security import settings as app_settings
from app.services.security import sources, store
from app.services.security.model import CLUSTER_SOURCES, Source

logger = logging.getLogger(__name__)

K8S_TEMPLATE = "k3s-gitops-app"
PROJECT_LABEL = "cnp.3istor.com/project"
REPORT_ARTIFACT = "cnp-security-report"
REPORT_FILE = "cnp-security-report.json"
SECURITY_WORKFLOW = "security.yml"

CLUSTER_INTERVAL = 15 * 60
GITHUB_INTERVAL = 60 * 60
TICK = 60
REQUEST_TIMEOUT = timedelta(minutes=30)
KYVERNO_INTERVAL = timedelta(hours=1)
TRIVY_REPORT_TTL = timedelta(hours=24)

_yaml = YAML(typ="safe")


def utcnow() -> datetime:
    """Naive UTC: what SQLite stores and gives back."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_k8s_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
        tzinfo=None
    )


def next_weekly_run(after: datetime) -> datetime:
    """Next ``17 5 * * 1`` (Monday 05:17 UTC) of the security workflow."""
    candidate = after.replace(hour=5, minute=17, second=0, microsecond=0)
    candidate += timedelta(days=(0 - candidate.weekday()) % 7)
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


def next_nightly_backup(after: datetime) -> datetime:
    candidate = after.replace(hour=3, minute=0, second=0, microsecond=0)
    return candidate if candidate > after else candidate + timedelta(days=1)


@dataclass(frozen=True)
class ProjectApps:
    project: str
    apps: list[sources.AppRef]
    deployments: dict[str, Deployment]


def kubernetes_projects(db: Session) -> list[ProjectApps]:
    rows = (
        db.query(Deployment)
        .filter(
            Deployment.template_id == K8S_TEMPLATE,
            Deployment.status == DeploymentStatus.RUNNING,
            Deployment.project_id.isnot(None),
        )
        .all()
    )
    by_project: dict[str, list[Deployment]] = {}
    for row in rows:
        by_project.setdefault(row.project_id, []).append(row)
    return [
        ProjectApps(
            project=project,
            apps=[
                sources.AppRef(
                    name=d.name,
                    namespace=f"{project}-{d.name}",
                    repo=repo_full_name(d.github_repo_url),
                )
                for d in deployments
            ],
            deployments={d.name: d for d in deployments},
        )
        for project, deployments in sorted(by_project.items())
    ]


def repo_full_name(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").removesuffix(".git").split("github.com/")[-1]


# ── Cluster pass ─────────────────────────────────────────────────────────────


@dataclass
class ClusterData:
    namespaces: list[str]
    policy_reports: list[dict]
    vulnerability_reports: list[dict]
    secret_reports: list[dict]
    trivy_installed: bool
    clusters: list[dict]
    backups: list[dict]
    isolation_policy: dict | None


def _fetch_cluster(project: str) -> ClusterData:
    namespaces = [
        ns["metadata"]["name"]
        for ns in kube_list(
            "/api/v1/namespaces",
            {"labelSelector": f"{PROJECT_LABEL}={project}"},
        )
    ]
    data = ClusterData(
        namespaces=namespaces,
        policy_reports=[],
        vulnerability_reports=[],
        secret_reports=[],
        trivy_installed=kube_get("/apis/aquasecurity.github.io/v1alpha1")
        is not None,
        clusters=[],
        backups=[],
        isolation_policy=kube_get(
            "/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/"
            f"cnp-project-{project}-ingress"
        ),
    )
    for ns in namespaces:
        data.policy_reports += kube_list(
            f"/apis/wgpolicyk8s.io/v1alpha2/namespaces/{ns}/policyreports"
        )
        if data.trivy_installed:
            data.vulnerability_reports += kube_list(
                f"/apis/aquasecurity.github.io/v1alpha1/namespaces/{ns}/vulnerabilityreports"
            )
            data.secret_reports += kube_list(
                f"/apis/aquasecurity.github.io/v1alpha1/namespaces/{ns}/exposedsecretreports"
            )
        data.clusters += kube_list(
            f"/apis/postgresql.cnpg.io/v1/namespaces/{ns}/clusters"
        )
        data.backups += kube_list(
            f"/apis/postgresql.cnpg.io/v1/namespaces/{ns}/backups"
        )
    return data


def _record_cluster_scans(
    db: Session, entry: ProjectApps, data: ClusterData, now: datetime
) -> None:
    kyverno = store.scan_state(db, entry.project, None, Source.KYVERNO)
    kyverno.status, kyverno.message = "ok", ""
    kyverno.last_run_at = max(
        (
            _parse_k8s_time(r["metadata"]["creationTimestamp"])
            for r in data.policy_reports
            if r["metadata"].get("creationTimestamp")
        ),
        default=None,
    )
    kyverno.next_run_at = now + KYVERNO_INTERVAL
    kyverno.collected_at = now

    for app in entry.apps:
        trivy = store.scan_state(
            db, entry.project, app.name, Source.TRIVY_OPERATOR
        )
        trivy.collected_at = now
        if not data.trivy_installed:
            trivy.status, trivy.message = (
                "unavailable",
                "Trivy Operator absent",
            )
            continue
        created = [
            _parse_k8s_time(r["metadata"]["creationTimestamp"])
            for r in data.vulnerability_reports
            if r["metadata"].get("namespace") == app.namespace
        ]
        trivy.status, trivy.message = "ok", ""
        trivy.last_run_at = max(created, default=None)
        trivy.next_run_at = (
            min(created) + TRIVY_REPORT_TTL if created else None
        )
        if (
            trivy.requested_at
            and trivy.last_run_at
            and (trivy.last_run_at >= trivy.requested_at)
        ):
            trivy.requested_at = None

        backup = store.scan_state(db, entry.project, app.name, Source.CNPG)
        backup.collected_at = now
        done = [
            _parse_k8s_time(b["status"]["stoppedAt"])
            for b in data.backups
            if b["metadata"]["namespace"] == app.namespace
            and (b.get("status") or {}).get("stoppedAt")
        ]
        backup.status, backup.message = "ok", ""
        backup.last_run_at = max(done, default=None)
        backup.next_run_at = next_nightly_backup(now)
    db.commit()


async def collect_cluster(db: Session, entry: ProjectApps) -> list:
    now = utcnow()
    try:
        data = await asyncio.to_thread(_fetch_cluster, entry.project)
    except KubeUnavailableError as exc:
        logger.warning(
            "Security: cluster data of '%s' unavailable: %s",
            entry.project,
            exc,
        )
        state = store.scan_state(db, entry.project, None, Source.KYVERNO)
        state.status, state.message = "error", str(exc)
        db.commit()
        return []

    drafts = (
        sources.kyverno_drafts(entry.project, entry.apps, data.policy_reports)
        + sources.trivy_operator_drafts(
            entry.project,
            entry.apps,
            data.vulnerability_reports,
            data.secret_reports,
        )
        + sources.backup_drafts(
            entry.project,
            entry.apps,
            data.clusters,
            data.backups,
            datetime.now(timezone.utc),
        )
        + sources.isolation_drafts(entry.project, data.isolation_policy)
    )
    owned = tuple(
        s
        for s in CLUSTER_SOURCES
        if s is not Source.TRIVY_OPERATOR or data.trivy_installed
    )
    fresh = store.upsert_findings(
        db, entry.project, drafts, owned, store.ALL_APPS, now
    )
    _record_cluster_scans(db, entry, data, now)
    return fresh


# ── GitHub pass ──────────────────────────────────────────────────────────────


async def _read_exposure(
    token: str, repo: str, deployment: Deployment, branch: str
) -> str | None:
    app_type = json.loads(deployment.app_config or "{}").get(
        "app_type", "static"
    )
    exposure_file, _ = security_data.values_files_for(app_type)
    raw, _ = await get_file_content(
        installation_token=token,
        repo_full_name=repo,
        file_path=exposure_file,
        ref=branch,
    )
    values = _yaml.load(raw)
    if not isinstance(values, dict) or not security_data.has_ingress(values):
        return None
    return security_data.read_exposure(values)


async def _read_optional(
    token: str, repo: str, path: str, branch: str
) -> str | None:
    try:
        raw, _ = await get_file_content(
            installation_token=token,
            repo_full_name=repo,
            file_path=path,
            ref=branch,
        )
    except FileNotInRepoError:
        return None
    return raw


async def project_policy(project: str) -> app_settings.ProjectPolicy | None:
    """None when the registry cannot be read: lock checks are then skipped."""
    try:
        existing = await read_record(project)
    except RegistryError as exc:
        logger.warning("Security: policy of '%s' unreadable: %s", project, exc)
        return None
    return app_settings.policy_from_record(existing[0] if existing else {})


async def collect_github_app(
    db: Session,
    entry: ProjectApps,
    app: sources.AppRef,
    policy: app_settings.ProjectPolicy | None = None,
) -> list:
    """Collect one app's CI report and exposure; errors leave its findings open."""
    now = utcnow()
    deployment = entry.deployments[app.name]
    scan = store.scan_state(db, entry.project, app.name, Source.CI)
    scan.collected_at = now
    installation_id = json.loads(deployment.app_config or "{}").get(
        "github_installation_id"
    )
    if not app.repo or not installation_id:
        scan.status, scan.message = "unavailable", "Pas de repo GitHub lié"
        db.commit()
        return []

    try:
        token = await get_installation_token(installation_id)
        branch = await get_default_branch(token, app.repo)
        artifact = await get_latest_artifact(
            token,
            app.repo,
            REPORT_ARTIFACT,
            REPORT_FILE,
            branch,
            scan.artifact_id,
        )
        preset = await _read_exposure(token, app.repo, deployment, branch)
        workflow = await _read_optional(
            token, app.repo, app_settings.WORKFLOW_FILE, branch
        )
        settings_raw = await _read_optional(
            token, app.repo, app_settings.SETTINGS_FILE, branch
        )
    except GitHubAppError as exc:
        logger.warning(
            "Security: GitHub data of '%s' unavailable: %s", app.repo, exc
        )
        scan.status, scan.message = "error", str(exc)
        db.commit()
        return []

    fresh = store.upsert_findings(
        db,
        entry.project,
        sources.exposure_drafts(entry.project, app, preset),
        (Source.EXPOSURE,),
        {app.name},
        now,
    )
    fresh += store.upsert_findings(
        db,
        entry.project,
        sources.repository_drafts(
            entry.project,
            app,
            workflow_present=workflow is not None,
            locked_drift=policy is not None
            and app_settings.locked_drift(
                policy, app_settings.parse_settings(settings_raw)
            ),
        ),
        (Source.REPOSITORY,),
        {app.name},
        now,
    )

    scan.next_run_at = next_weekly_run(now)
    if artifact is None:
        scan.status, scan.message = (
            "missing",
            "Le workflow n'a pas encore tourné sur la branche par défaut",
        )
        fresh += store.upsert_findings(
            db, entry.project, [], (Source.CI,), {app.name}, now
        )
    else:
        scan.status, scan.message = "ok", ""
        scan.last_run_at = artifact.created_at.replace(tzinfo=None)
        if scan.requested_at and scan.last_run_at >= scan.requested_at:
            scan.requested_at = None
        if artifact.content is not None:
            scan.artifact_id = artifact.id
            fresh += store.upsert_findings(
                db,
                entry.project,
                sources.ci_drafts(entry.project, app, artifact.content),
                (Source.CI,),
                {app.name},
                now,
            )
    if scan.requested_at and now - scan.requested_at > REQUEST_TIMEOUT:
        scan.requested_at = None
    db.commit()
    return fresh


# ── Loop ─────────────────────────────────────────────────────────────────────


async def collect_project(
    db: Session, entry: ProjectApps, cluster: bool, github: bool
) -> None:
    # A project's first pass stores what was already there: alerting on all
    # of it would flood the channel with old news.
    first_pass = (
        db.query(SecurityScan.id)
        .filter(SecurityScan.project == entry.project)
        .first()
        is None
    )
    trivy_requested = any(
        store.scan_state(
            db, entry.project, app.name, Source.TRIVY_OPERATOR
        ).requested_at
        for app in entry.apps
    )
    fresh = []
    if cluster or trivy_requested:
        fresh += await collect_cluster(db, entry)
    policy = await project_policy(entry.project) if github else None
    for app in entry.apps:
        requested = store.scan_state(db, entry.project, app.name, Source.CI)
        if github or requested.requested_at is not None:
            fresh += await collect_github_app(db, entry, app, policy)
    store.write_snapshots(
        db, entry.project, [a.name for a in entry.apps], utcnow().date()
    )
    notify = alerts.to_notify(db, entry.project, fresh, utcnow())
    db.commit()
    if not first_pass:
        await alerts.send(entry.project, notify)


async def collect_once(cluster: bool, github: bool) -> None:
    db = SessionLocal()
    try:
        for entry in kubernetes_projects(db):
            try:
                await collect_project(db, entry, cluster, github)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                db.rollback()
                logger.error(
                    "Security: collection of '%s' failed: %s",
                    entry.project,
                    exc,
                    exc_info=True,
                )
    finally:
        db.close()


async def security_collector_loop() -> None:
    logger.info("Security collector started")
    last_cluster = last_github = float("-inf")
    while True:
        started = time.monotonic()
        cluster = started - last_cluster >= CLUSTER_INTERVAL
        github = started - last_github >= GITHUB_INTERVAL
        try:
            await collect_once(cluster, github)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Security collector error: %s", exc, exc_info=True)
        if cluster:
            last_cluster = started
        if github:
            last_github = started
        await asyncio.sleep(TICK)
