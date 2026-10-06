"""
Project security report: one status per control, and a score.

Controls are evaluated on what the project's owners can act on, the app
namespaces. The project's system namespace runs platform components, so its
results are shown apart and never count towards the score.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from app.services.kube_client import KubeUnavailableError, kube_get, kube_list

logger = logging.getLogger(__name__)

PROJECT_LABEL = "cnp.3istor.com/project"
BARMAN_PLUGIN = "barman-cloud.cloudnative-pg.io"
BACKUP_MAX_AGE = timedelta(hours=26)
CACHE_TTL_SECONDS = 60

KYVERNO_CONTROLS = {
    "pod_security": (
        "pod-security-restricted",
        "Images non privilégiées",
        "Pod Security Standards Restricted : non-root, pas de privilège, capabilities retirées",
    ),
    "image_tags": (
        "disallow-latest-and-untagged-images",
        "Pas de :latest",
        "Chaque image porte un tag explicite ou un digest",
    ),
    "resources": (
        "require-resources-and-memory-limits",
        "Requests et limits",
        "CPU et mémoire demandés, limite mémoire égale à la demande",
    ),
}


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


_SEVERITY = {Status.UNKNOWN: 0, Status.OK: 1, Status.WARN: 2, Status.FAIL: 3}


class Finding(BaseModel):
    subject: str
    status: Status
    detail: str


class Control(BaseModel):
    id: str
    title: str
    description: str
    status: Status
    summary: str
    findings: list[Finding] = []


class SecurityReport(BaseModel):
    project: str
    generated_at: datetime
    score: int | None
    controls: list[Control]
    platform_controls: list[Control]


def worst(statuses: list[Status]) -> Status:
    measured = [s for s in statuses if s is not Status.UNKNOWN]
    if not measured:
        return Status.UNKNOWN
    return max(measured, key=_SEVERITY.__getitem__)


def score(controls: list[Control]) -> int | None:
    measured = [c for c in controls if c.status is not Status.UNKNOWN]
    if not measured:
        return None
    ok = sum(1 for c in measured if c.status is Status.OK)
    return round(100 * ok / len(measured))


def _control(
    control_id: str,
    title: str,
    description: str,
    findings: list[Finding],
    empty: str,
) -> Control:
    status = worst([f.status for f in findings])
    if status is Status.UNKNOWN:
        summary = empty
    else:
        bad = sum(
            1 for f in findings if f.status in (Status.WARN, Status.FAIL)
        )
        summary = (
            "Tout est conforme"
            if bad == 0
            else f"{bad} sur {len(findings)} à traiter"
        )
    return Control(
        id=control_id,
        title=title,
        description=description,
        status=status,
        summary=summary,
        findings=findings,
    )


def _unmeasured(
    control_id: str, title: str, description: str, why: str
) -> Control:
    return Control(
        id=control_id,
        title=title,
        description=description,
        status=Status.UNKNOWN,
        summary=why,
    )


def kyverno_findings(policy_reports: list[dict], policy: str) -> list[Finding]:
    """One finding per Pod evaluated by *policy*: its controllers' reports would count it twice."""
    findings: dict[str, Finding] = {}
    for report in policy_reports:
        scope = report.get("scope") or {}
        if scope.get("kind") != "Pod":
            continue
        subject = f"{scope.get('namespace') or report['metadata'].get('namespace')}/{scope.get('name')}"
        for result in report.get("results", []):
            if result.get("policy") != policy:
                continue
            failed = result.get("result") in ("fail", "error")
            current = findings.get(subject)
            if current and current.status is Status.FAIL:
                continue
            findings[subject] = Finding(
                subject=subject,
                status=Status.FAIL if failed else Status.OK,
                detail=result.get("message", "") if failed else "Conforme",
            )
    return sorted(findings.values(), key=lambda f: f.subject)


def backup_findings(
    clusters: list[dict], backups: list[dict], now: datetime
) -> list[Finding]:
    findings = []
    for cluster in clusters:
        namespace = cluster["metadata"]["namespace"]
        name = cluster["metadata"]["name"]
        subject = f"{namespace}/{name}"
        plugins = (cluster.get("spec") or {}).get("plugins") or []
        if not any(p.get("name") == BARMAN_PLUGIN for p in plugins):
            findings.append(
                Finding(
                    subject=subject,
                    status=Status.WARN,
                    detail="Sauvegarde désactivée (db.backup.enabled)",
                )
            )
            continue

        conditions = (cluster.get("status") or {}).get("conditions") or []
        archiving = next(
            (c for c in conditions if c.get("type") == "ContinuousArchiving"),
            None,
        )
        if archiving and archiving.get("status") == "False":
            findings.append(
                Finding(
                    subject=subject,
                    status=Status.FAIL,
                    detail=f"Archivage WAL en échec : {archiving.get('message', '')}",
                )
            )
            continue

        completed = [
            b
            for b in backups
            if b["metadata"]["namespace"] == namespace
            and ((b.get("spec") or {}).get("cluster") or {}).get("name")
            == name
            and (b.get("status") or {}).get("phase") == "completed"
            and (b.get("status") or {}).get("stoppedAt")
        ]
        if not completed:
            findings.append(
                Finding(
                    subject=subject,
                    status=Status.FAIL,
                    detail="Aucun backup réussi pour l'instant",
                )
            )
            continue
        last = max(
            datetime.fromisoformat(
                b["status"]["stoppedAt"].replace("Z", "+00:00")
            )
            for b in completed
        )
        fresh = now - last <= BACKUP_MAX_AGE
        findings.append(
            Finding(
                subject=subject,
                status=Status.OK if fresh else Status.FAIL,
                detail=f"Dernier backup réussi : {last:%Y-%m-%d %H:%M} UTC",
            )
        )
    return findings


def exposure_finding(app: str, exposure: str | None) -> Finding:
    if exposure is None:
        return Finding(
            subject=app, status=Status.OK, detail="Pas d'exposition HTTP"
        )
    if exposure == "public":
        return Finding(
            subject=app,
            status=Status.WARN,
            detail="Publique : accessible sans connexion, à vérifier",
        )
    if exposure == "custom":
        return Finding(
            subject=app,
            status=Status.WARN,
            detail="Réglage personnalisé dans le code, à vérifier",
        )
    labels = {
        "project_users": "Tout compte connecté",
        "project_members": "Membres du projet",
        "project_admins": "Admins du projet",
    }
    return Finding(
        subject=app, status=Status.OK, detail=labels.get(exposure, exposure)
    )


def ci_scan_finding(app: str, report: dict | None) -> Finding:
    if report is None:
        return Finding(
            subject=app,
            status=Status.UNKNOWN,
            detail="Aucun rapport de scan (le workflow n'a pas encore tourné)",
        )
    scanners = report.get("scanners") or {}
    failed = [
        name
        for name, s in scanners.items()
        if (s or {}).get("status") == "fail"
    ]
    commit = str(report.get("commit", ""))[:7]
    if failed:
        return Finding(
            subject=app,
            status=Status.FAIL,
            detail=f"En échec : {', '.join(sorted(failed))} (commit {commit})",
        )
    return Finding(
        subject=app,
        status=Status.OK,
        detail=f"Scans propres (commit {commit})",
    )


def _kyverno_controls(policy_reports: list[dict]) -> list[Control]:
    return [
        _control(
            control_id,
            title,
            description,
            kyverno_findings(policy_reports, policy),
            "Aucun pod évalué",
        )
        for control_id, (
            policy,
            title,
            description,
        ) in KYVERNO_CONTROLS.items()
    ]


AppValuesLoader = Callable[[Any], Awaitable[tuple[str | None, bool]]]
AppReportLoader = Callable[[Any], Awaitable[dict | None]]


async def build_report(
    project_name: str,
    deployments: list[Any],
    load_exposure: AppValuesLoader,
    load_ci_report: AppReportLoader,
) -> SecurityReport:
    """
    Build the report for *project_name*.

    *load_exposure(deployment)* returns (exposure preset or None, ok) and
    *load_ci_report(deployment)* returns the parsed cnp-security-report or
    None; both raise on failure, which marks that app as not measured.
    """
    system_ns = f"{project_name}-system"
    controls: list[Control] = []
    platform_controls: list[Control] = []

    try:
        namespaces = [
            ns["metadata"]["name"]
            for ns in await asyncio.to_thread(
                kube_list,
                "/api/v1/namespaces",
                {"labelSelector": f"{PROJECT_LABEL}={project_name}"},
            )
        ]
        app_namespaces = [ns for ns in namespaces if ns != system_ns]

        reports_by_ns = {
            ns: await asyncio.to_thread(
                kube_list,
                f"/apis/wgpolicyk8s.io/v1alpha2/namespaces/{ns}/policyreports",
            )
            for ns in namespaces
        }
        app_reports = [r for ns in app_namespaces for r in reports_by_ns[ns]]
        controls += _kyverno_controls(app_reports)
        platform_controls += _kyverno_controls(
            reports_by_ns.get(system_ns, [])
        )

        policy = await asyncio.to_thread(
            kube_get,
            f"/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/cnp-project-{project_name}-ingress",
        )
        controls.append(
            Control(
                id="network_isolation",
                title="Projet fermé par défaut",
                description="Seuls le projet, la gateway et la plateforme peuvent joindre ses pods",
                status=Status.OK if policy else Status.FAIL,
                summary=(
                    "Default deny actif"
                    if policy
                    else "Aucune policy d'isolation réseau sur le projet"
                ),
            )
        )

        clusters, backups = [], []
        for ns in app_namespaces:
            clusters += await asyncio.to_thread(
                kube_list,
                f"/apis/postgresql.cnpg.io/v1/namespaces/{ns}/clusters",
            )
            backups += await asyncio.to_thread(
                kube_list,
                f"/apis/postgresql.cnpg.io/v1/namespaces/{ns}/backups",
            )
        controls.append(
            _control(
                "backups",
                "Sauvegardes des bases",
                "Backup quotidien réussi depuis moins de 26 h pour chaque base",
                backup_findings(clusters, backups, datetime.now(timezone.utc)),
                "Aucune base de données",
            )
        )
    except KubeUnavailableError as exc:
        logger.warning(
            "Security report for '%s' without cluster data: %s",
            project_name,
            exc,
        )
        why = "Données du cluster indisponibles"
        controls += [
            _unmeasured(cid, title, desc, why)
            for cid, (_, title, desc) in KYVERNO_CONTROLS.items()
        ]
        controls.append(
            _unmeasured(
                "network_isolation",
                "Projet fermé par défaut",
                "Seuls le projet, la gateway et la plateforme peuvent joindre ses pods",
                why,
            )
        )
        controls.append(
            _unmeasured(
                "backups",
                "Sauvegardes des bases",
                "Backup quotidien réussi depuis moins de 26 h pour chaque base",
                why,
            )
        )

    exposure, scans = [], []
    for deployment in deployments:
        app = deployment.name
        try:
            preset, _ = await load_exposure(deployment)
            exposure.append(exposure_finding(app, preset))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Exposure of '%s' not readable: %s", app, exc)
            exposure.append(
                Finding(
                    subject=app,
                    status=Status.UNKNOWN,
                    detail="Values illisibles",
                )
            )
        try:
            scans.append(
                ci_scan_finding(app, await load_ci_report(deployment))
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("CI report of '%s' not readable: %s", app, exc)
            scans.append(
                Finding(
                    subject=app,
                    status=Status.UNKNOWN,
                    detail="Rapport illisible",
                )
            )

    controls.append(
        _control(
            "exposure",
            "Exposition des apps",
            "Qui peut joindre chaque app (réglable dans l'app ou son values.yaml)",
            exposure,
            "Aucune app",
        )
    )
    controls.append(
        _control(
            "ci_scans",
            "Scans CI",
            "Secrets (gitleaks) et CVE critiques corrigeables (Trivy) du dernier build",
            scans,
            "Aucune app",
        )
    )

    return SecurityReport(
        project=project_name,
        generated_at=datetime.now(timezone.utc),
        score=score(controls),
        controls=controls,
        platform_controls=platform_controls,
    )


_cache: dict[str, tuple[float, SecurityReport]] = {}


async def cached_report(
    project_name: str,
    deployments: list[Any],
    load_exposure: AppValuesLoader,
    load_ci_report: AppReportLoader,
) -> SecurityReport:
    """The report, rebuilt at most once a minute per project: it fans out to GitHub."""
    hit = _cache.get(project_name)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    report = await build_report(
        project_name, deployments, load_exposure, load_ci_report
    )
    _cache[project_name] = (time.monotonic(), report)
    return report
