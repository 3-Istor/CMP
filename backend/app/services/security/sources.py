"""
Turn what each scanner produced into findings.

Pure functions over the raw Kubernetes objects and CI report, so they can be
tested without a cluster. Wording is written for a developer who does not
know Kubernetes: what is wrong, and what to change.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.security.model import (
    Audience,
    Category,
    Draft,
    Source,
    Tier,
    fingerprint,
)

BARMAN_PLUGIN = "barman-cloud.cloudnative-pg.io"
BACKUP_MAX_AGE = timedelta(hours=26)


@dataclass(frozen=True)
class AppRef:
    name: str
    namespace: str
    repo: str | None

    def owns_image(self, repository: str) -> bool:
        """Images the app's CI builds: ``<owner>/<repo>`` or ``<owner>/<repo>/<component>``."""
        if not self.repo:
            return False
        repo = self.repo.lower()
        repository = repository.lower()
        return repository == repo or repository.startswith(f"{repo}/")


def app_for_namespace(apps: list[AppRef], namespace: str) -> AppRef | None:
    return next((a for a in apps if a.namespace == namespace), None)


# ── Kyverno ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KyvernoRule:
    key: str
    audience: Audience
    tier: Tier
    category: Category
    title: str
    fix: str


# Matched on the policy message: the policies report no rule name. Only
# running as root depends on the app's own Dockerfile; every other rule is set
# by the platform chart.
_KYVERNO_RULES: list[tuple[str, KyvernoRule]] = [
    (
        "runAsNonRoot",
        KyvernoRule(
            "run-as-root",
            Audience.DEVELOPER,
            Tier.IMPORTANT,
            Category.CONTAINER,
            "Le conteneur tourne en root",
            "Ajoute un utilisateur non root à la fin du Dockerfile, "
            "par exemple `USER 10001`.",
        ),
    ),
    (
        "UID 0",
        KyvernoRule(
            "run-as-root",
            Audience.DEVELOPER,
            Tier.IMPORTANT,
            Category.CONTAINER,
            "Le conteneur tourne en root",
            "Ajoute un utilisateur non root à la fin du Dockerfile, "
            "par exemple `USER 10001`.",
        ),
    ),
    (
        "drop ALL",
        KyvernoRule(
            "capabilities",
            Audience.PLATFORM,
            Tier.IMPORTANT,
            Category.CONTAINER,
            "Capabilities Linux non retirées",
            "Ajouter `capabilities.drop: [ALL]` au securityContext du chart.",
        ),
    ),
    (
        "allowPrivilegeEscalation",
        KyvernoRule(
            "privilege-escalation",
            Audience.PLATFORM,
            Tier.IMPORTANT,
            Category.CONTAINER,
            "Escalade de privilèges autorisée",
            "Poser `allowPrivilegeEscalation: false` dans le chart.",
        ),
    ),
    (
        "seccompProfile",
        KyvernoRule(
            "seccomp",
            Audience.PLATFORM,
            Tier.RECOMMENDED,
            Category.CONTAINER,
            "Profil seccomp absent",
            "Poser `seccompProfile.type: RuntimeDefault` dans le chart.",
        ),
    ),
    (
        ":latest",
        KyvernoRule(
            "image-latest",
            Audience.PLATFORM,
            Tier.RECOMMENDED,
            Category.CONTAINER,
            "Image déployée en :latest",
            "Déployer l'image par tag de version ou par digest.",
        ),
    ),
    (
        "explicit tag",
        KyvernoRule(
            "image-latest",
            Audience.PLATFORM,
            Tier.RECOMMENDED,
            Category.CONTAINER,
            "Image déployée sans tag",
            "Déployer l'image par tag de version ou par digest.",
        ),
    ),
    (
        "Memory requests",
        KyvernoRule(
            "resources",
            Audience.PLATFORM,
            Tier.RECOMMENDED,
            Category.CONTAINER,
            "Requests et limits absentes",
            "Poser des requests CPU et mémoire et une limite mémoire "
            "égale à la demande dans le chart.",
        ),
    ),
]

_KYVERNO_OTHER = KyvernoRule(
    "pod-security",
    Audience.PLATFORM,
    Tier.IMPORTANT,
    Category.CONTAINER,
    "Réglage de sécurité du pod non conforme",
    "Voir le message Kyverno et corriger le chart.",
)


def kyverno_rule(message: str) -> KyvernoRule:
    return next(
        (rule for needle, rule in _KYVERNO_RULES if needle in message),
        _KYVERNO_OTHER,
    )


def kyverno_drafts(
    project: str, apps: list[AppRef], policy_reports: list[dict]
) -> list[Draft]:
    """
    One finding per app and rule, listing the pods it hits. Only Pod reports
    are read: their Deployment and ReplicaSet reports would count each failure
    three times.
    """
    hits: dict[tuple[str | None, KyvernoRule], dict] = {}
    for report in policy_reports:
        scope = report.get("scope") or {}
        if scope.get("kind") != "Pod":
            continue
        namespace = scope.get("namespace") or report["metadata"].get(
            "namespace", ""
        )
        app = app_for_namespace(apps, namespace)
        for result in report.get("results", []):
            if result.get("result") not in ("fail", "error"):
                continue
            message = result.get("message", "")
            rule = kyverno_rule(message)
            entry = hits.setdefault(
                (app.name if app else None, rule),
                {"pods": set(), "messages": set()},
            )
            entry["pods"].add(scope.get("name", ""))
            entry["messages"].add(message)

    drafts = []
    for (app_name, rule), entry in hits.items():
        pods = sorted(entry["pods"])
        drafts.append(
            Draft(
                fingerprint=fingerprint(
                    project, app_name, Source.KYVERNO.value, rule.key
                ),
                app=app_name,
                source=Source.KYVERNO,
                category=rule.category,
                tier=rule.tier,
                audience=(
                    rule.audience
                    if app_name is not None
                    else Audience.PLATFORM
                ),
                rule=rule.key,
                title=rule.title,
                detail=f"{len(pods)} pod(s) concerné(s)",
                fix=rule.fix,
                location=", ".join(pods),
                raw="\n".join(sorted(entry["messages"])),
            )
        )
    return drafts


# ── Vulnerabilities (Trivy Operator and CI) ──────────────────────────────────


def vulnerability_tier(
    severity: str, fixed: str | None, running: bool
) -> Tier:
    """
    Only fixable CRITICAL and HIGH count. A fixable CRITICAL is core once it
    runs; in a CI build that may never be deployed, it is important.
    """
    if not fixed:
        return Tier.INFO
    if severity == "CRITICAL":
        return Tier.CORE if running else Tier.IMPORTANT
    if severity == "HIGH":
        return Tier.IMPORTANT
    return Tier.INFO


def _vulnerability_draft(
    project: str,
    app: str | None,
    source: Source,
    audience: Audience,
    running: bool,
    vuln_id: str,
    severity: str,
    package: str,
    installed: str,
    fixed: str | None,
    title: str | None,
    location: str,
    link: str | None,
    os_package: bool,
) -> Draft:
    if fixed and os_package:
        fix = (
            f"Mets à jour l'image de base du Dockerfile : "
            f"{package} est corrigé en {fixed}."
        )
    elif fixed:
        fix = f"Passe {package} de {installed} à {fixed}."
    else:
        fix = "Aucun correctif publié pour l'instant."
    return Draft(
        fingerprint=fingerprint(project, app, vuln_id, package, installed),
        app=app,
        source=source,
        category=Category.DEPENDENCIES,
        tier=vulnerability_tier(severity, fixed, running),
        audience=audience,
        rule=vuln_id,
        title=f"{vuln_id} dans {package} {installed}",
        detail=f"{severity.capitalize()}. {title or ''}".strip(),
        fix=fix,
        location=location,
        link=link or f"https://avd.aquasec.com/nvd/{vuln_id.lower()}",
    )


def _report_owner(
    apps: list[AppRef], report: dict
) -> tuple[AppRef | None, str, str]:
    namespace = report["metadata"].get("namespace", "")
    labels = report["metadata"].get("labels") or {}
    body = report.get("report") or {}
    artifact = body.get("artifact") or {}
    repository = artifact.get("repository", "")
    image = (
        f"{(body.get('registry') or {}).get('server', '')}/{repository}"
        f":{artifact.get('tag', '')}"
    ).lstrip("/")
    workload = labels.get("trivy-operator.resource.name", "")
    return (
        app_for_namespace(apps, namespace),
        repository,
        f"{image} ({workload})",
    )


def trivy_operator_drafts(
    project: str,
    apps: list[AppRef],
    vulnerability_reports: list[dict],
    secret_reports: list[dict],
) -> list[Draft]:
    drafts: dict[str, Draft] = {}
    for report in vulnerability_reports:
        app, repository, location = _report_owner(apps, report)
        audience = (
            Audience.DEVELOPER
            if app and app.owns_image(repository)
            else Audience.PLATFORM
        )
        for vuln in (report.get("report") or {}).get("vulnerabilities", []):
            draft = _vulnerability_draft(
                project,
                app.name if app else None,
                Source.TRIVY_OPERATOR,
                audience,
                True,
                vuln.get("vulnerabilityID", ""),
                vuln.get("severity", ""),
                vuln.get("resource", ""),
                vuln.get("installedVersion", ""),
                vuln.get("fixedVersion") or None,
                vuln.get("title"),
                location,
                vuln.get("primaryLink"),
                vuln.get("class") == "os-pkgs",
            )
            drafts[draft.fingerprint] = draft

    for report in secret_reports:
        app, repository, location = _report_owner(apps, report)
        audience = (
            Audience.DEVELOPER
            if app and app.owns_image(repository)
            else Audience.PLATFORM
        )
        for secret in (report.get("report") or {}).get("secrets", []):
            target = secret.get("target", "")
            rule_id = secret.get("ruleID", "")
            app_name = app.name if app else None
            draft = Draft(
                fingerprint=fingerprint(
                    project, app_name, "image-secret", rule_id, target
                ),
                app=app_name,
                source=Source.TRIVY_OPERATOR,
                category=Category.LEAKS,
                tier=Tier.CORE,
                audience=audience,
                rule=rule_id,
                title=f"Secret embarqué dans l'image : {secret.get('title', rule_id)}",
                detail=f"Fichier {target} de l'image",
                fix=(
                    "Change ce secret, puis retire le fichier de l'image : "
                    "un secret se passe par Vault, jamais dans l'image."
                ),
                location=location,
            )
            drafts[draft.fingerprint] = draft
    return list(drafts.values())


def ci_drafts(project: str, app: AppRef, report: dict) -> list[Draft]:
    """Findings of a ``cnp-security-report``; schema 1 reports carry no detail."""
    scanners = report.get("scanners") or {}
    run_url = report.get("run_url")
    drafts: dict[str, Draft] = {}

    for leak in (scanners.get("gitleaks") or {}).get("leaks", []):
        leak_fingerprint = leak.get("fingerprint") or (
            f"{leak.get('file')}:{leak.get('rule_id')}:{leak.get('line')}"
        )
        draft = Draft(
            fingerprint=fingerprint(
                project, app.name, "leak", leak_fingerprint
            ),
            app=app.name,
            source=Source.CI,
            category=Category.LEAKS,
            tier=Tier.CORE,
            audience=Audience.DEVELOPER,
            rule=leak_fingerprint,
            title="Secret commité dans le repo",
            detail=" · ".join(
                part
                for part in (
                    f"Règle {leak.get('rule_id')}",
                    (
                        f"commit {str(leak.get('commit'))[:7]}"
                        if leak.get("commit")
                        else ""
                    ),
                )
                if part
            ),
            fix=(
                "Change ce secret tout de suite, puis retire-le du code. "
                "Le supprimer de l'historique ne suffit pas : il a pu être copié."
            ),
            location=f"{leak.get('file')} ligne {leak.get('line')}",
            link=run_url,
        )
        drafts[draft.fingerprint] = draft

    for scanner in ("trivy_fs", "trivy_image"):
        for vuln in (scanners.get(scanner) or {}).get("vulnerabilities", []):
            target = vuln.get("target", "")
            image = vuln.get("image")
            draft = _vulnerability_draft(
                project,
                app.name,
                Source.CI,
                Audience.DEVELOPER,
                False,
                vuln.get("id", ""),
                vuln.get("severity", ""),
                vuln.get("package", ""),
                vuln.get("installed", ""),
                vuln.get("fixed"),
                vuln.get("title"),
                f"image {image} · {target}" if image else target,
                run_url,
                vuln.get("class") == "os-pkgs",
            )
            drafts[draft.fingerprint] = draft
    return list(drafts.values())


# ── Backups ──────────────────────────────────────────────────────────────────


def _backup_draft(
    project: str,
    app: AppRef,
    cluster: str,
    rule: str,
    tier: Tier,
    title: str,
    detail: str,
    fix: str,
) -> Draft:
    return Draft(
        fingerprint=fingerprint(project, app.name, "backup", cluster),
        app=app.name,
        source=Source.CNPG,
        category=Category.DATA,
        tier=tier,
        audience=Audience.DEVELOPER,
        rule=rule,
        title=title,
        detail=detail,
        fix=fix,
        location=cluster,
    )


def backup_drafts(
    project: str,
    apps: list[AppRef],
    clusters: list[dict],
    backups: list[dict],
    now: datetime,
) -> list[Draft]:
    completed: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for backup in backups:
        status = backup.get("status") or {}
        if status.get("phase") != "completed" or not status.get("stoppedAt"):
            continue
        cluster_name = ((backup.get("spec") or {}).get("cluster") or {}).get(
            "name", ""
        )
        completed[(backup["metadata"]["namespace"], cluster_name)].append(
            datetime.fromisoformat(status["stoppedAt"].replace("Z", "+00:00"))
        )

    drafts = []
    for cluster in clusters:
        namespace = cluster["metadata"]["namespace"]
        name = cluster["metadata"]["name"]
        app = app_for_namespace(apps, namespace)
        if app is None:
            continue

        plugins = (cluster.get("spec") or {}).get("plugins") or []
        if not any(p.get("name") == BARMAN_PLUGIN for p in plugins):
            drafts.append(
                _backup_draft(
                    project,
                    app,
                    name,
                    "backup-disabled",
                    Tier.IMPORTANT,
                    "Base sans sauvegarde",
                    "Une suppression ou une erreur serait définitive.",
                    "Active la sauvegarde dans Données › Sauvegardes.",
                )
            )
            continue

        conditions = (cluster.get("status") or {}).get("conditions") or []
        archiving = next(
            (c for c in conditions if c.get("type") == "ContinuousArchiving"),
            None,
        )
        if archiving and archiving.get("status") == "False":
            drafts.append(
                _backup_draft(
                    project,
                    app,
                    name,
                    "backup-failing",
                    Tier.CORE,
                    "Sauvegarde activée mais en échec",
                    f"Archivage en échec : {archiving.get('message', '')}",
                    "Contacte l'équipe plateforme : le stockage des "
                    "sauvegardes ne répond pas.",
                )
            )
            continue

        created = datetime.fromisoformat(
            cluster["metadata"]["creationTimestamp"].replace("Z", "+00:00")
        )
        last = max(completed.get((namespace, name), []), default=None)
        if last is None and now - created > BACKUP_MAX_AGE:
            drafts.append(
                _backup_draft(
                    project,
                    app,
                    name,
                    "backup-missing",
                    Tier.CORE,
                    "Sauvegarde activée mais jamais réussie",
                    "Aucune sauvegarde réussie depuis l'activation.",
                    "Lance une sauvegarde manuelle pour voir l'erreur, ou "
                    "contacte l'équipe plateforme.",
                )
            )
        elif last is not None and now - last > BACKUP_MAX_AGE:
            drafts.append(
                _backup_draft(
                    project,
                    app,
                    name,
                    "backup-stale",
                    Tier.CORE,
                    "Sauvegarde en retard",
                    f"Dernière sauvegarde réussie : {last:%Y-%m-%d %H:%M} UTC",
                    "Lance une sauvegarde manuelle pour voir l'erreur, ou "
                    "contacte l'équipe plateforme.",
                )
            )
    return drafts


# ── Exposure and network isolation ───────────────────────────────────────────


def exposure_drafts(
    project: str, app: AppRef, preset: str | None
) -> list[Draft]:
    if preset not in ("public", "custom"):
        return []
    public = preset == "public"
    return [
        Draft(
            fingerprint=fingerprint(project, app.name, "exposure", preset),
            app=app.name,
            source=Source.EXPOSURE,
            category=Category.ACCESS,
            tier=Tier.RECOMMENDED if public else Tier.INFO,
            audience=Audience.DEVELOPER,
            rule=f"exposure-{preset}",
            title=(
                "App publique, accessible sans connexion"
                if public
                else "Exposition réglée à la main dans le code"
            ),
            detail=(
                "N'importe qui sur Internet peut l'ouvrir."
                if public
                else "La CMP ne reconnaît pas ce réglage : vérifie qui y a accès."
            ),
            fix=(
                "Si ce n'est pas voulu, réserve-la aux membres du projet. "
                "Sinon, ignore cette alerte en indiquant que c'est voulu."
                if public
                else "Choisis un des réglages proposés dans Accès et exposition."
            ),
        )
    ]


def isolation_drafts(project: str, policy: dict | None) -> list[Draft]:
    if policy:
        return []
    return [
        Draft(
            fingerprint=fingerprint(project, None, "isolation"),
            app=None,
            source=Source.CILIUM,
            category=Category.ACCESS,
            tier=Tier.CORE,
            audience=Audience.PLATFORM,
            rule="network-isolation",
            title="Projet non isolé des autres",
            detail=f"La policy cnp-project-{project}-ingress est absente.",
            fix="Vérifier le rendu de cnp-project-base pour ce projet.",
        )
    ]


# ── Repository guard rails ───────────────────────────────────────────────────


def repository_drafts(
    project: str, app: AppRef, workflow_present: bool, locked_drift: bool
) -> list[Draft]:
    drafts = []
    if not workflow_present:
        drafts.append(
            Draft(
                fingerprint=fingerprint(project, app.name, "workflow-missing"),
                app=app.name,
                source=Source.REPOSITORY,
                category=Category.LEAKS,
                tier=Tier.CORE,
                audience=Audience.DEVELOPER,
                rule="workflow-missing",
                title="Workflow de sécurité absent du repo",
                detail="Plus aucun scan des secrets ni des dépendances ne tourne.",
                fix="Restaure .github/workflows/security.yml depuis le modèle.",
            )
        )
    if locked_drift:
        drafts.append(
            Draft(
                fingerprint=fingerprint(project, app.name, "locked-drift"),
                app=app.name,
                source=Source.REPOSITORY,
                category=Category.LEAKS,
                tier=Tier.CORE,
                audience=Audience.DEVELOPER,
                rule="locked-setting-changed",
                title="Réglage imposé par l'admin modifié dans le repo",
                detail="deploy/security.yaml ne suit plus la politique du projet.",
                fix="Remets la valeur imposée, ou demande à l'admin du projet de lever le verrou.",
            )
        )
    return drafts
