"""Portal-authorized import protocol. Every external gate fails closed."""

import asyncio
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import jwt
import requests
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, ProjectStatus
from app.models.repository_import import RepositoryImport
from app.models.user_github import UserGitHubInstallation
from app.services import keycloak_service, project_registry
from app.services.clusters.state import ensure_cluster_up
from app.services.github_account import linked_identity
from app.services.github_service import generate_jwt, get_installation_token

TERMINAL = {"completed", "failed", "cancelled"}
SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(
    r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def stop(message, code=409):
    raise HTTPException(code, message)


def enabled():
    if not settings.IMPORT_ENABLED:
        stop("L’import de repos n’est pas activé", 503)
    if not (
        settings.IMPORT_DISPATCH_WORKFLOW
        in {"docker-image.yml", "import-project.yml"}
        and SHA.fullmatch(settings.IMPORT_WORKFLOW_SHA)
        and SHA.fullmatch(settings.IMPORT_CHART_SHA)
        and (
            settings.CNP_REGISTRY_BRANCH == "main"
            or settings.IMPORT_ALLOWED_PROJECT
        )
        and settings.IMPORT_OIDC_AUDIENCE
        and settings.IMPORT_IMAGE_PREFIX.startswith("ghcr.io/")
        and settings.IMPORT_CALLBACK_URL.startswith("https://")
        and settings.IMPORT_CALLBACK_URL.rstrip("/").endswith("/api")
        and settings.IMPORT_ARGOCD_URL.startswith("https://")
        and settings.IMPORT_ARGOCD_TOKEN
        and settings.GITHUB_APP_PRIVATE_KEY
        and settings.GITHUB_INSTALLATION_ID
        and settings.GITHUB_REGISTRY_TOKEN
    ):
        stop("Configuration de l’import incomplète", 503)


@lru_cache(maxsize=4)
def jwks(url):
    return jwt.PyJWKClient(
        url, timeout=10, headers={"User-Agent": "cnp-backend"}
    )


def verify_token(token, worker=False):
    enabled()
    issuer = (
        "https://token.actions.githubusercontent.com"
        if worker
        else f"{settings.KEYCLOAK_URL.rstrip('/')}/realms/{settings.KEYCLOAK_REALM}"
    )
    uri = issuer + (
        "/.well-known/jwks" if worker else "/protocol/openid-connect/certs"
    )
    try:
        key = jwks(uri).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=(
                settings.IMPORT_OIDC_AUDIENCE
                if worker
                else settings.IMPORT_USER_AUDIENCE
            ),
            options={
                "require": ["exp", "iat", "iss", "aud"]
                + (["sub"] if worker else [])
            },
        )
    except Exception:
        stop("Authentification invalide", 401)
    if worker:
        prefix = f"{settings.IMPORT_WORKFLOW_REPO}/.github/workflows/"
        suffix = f"@refs/heads/{settings.IMPORT_WORKFLOW_REF}"
        expected = prefix + settings.IMPORT_DISPATCH_WORKFLOW + suffix
        reusable = prefix + "import-project.yml" + suffix
        if (
            claims.get("repository") != settings.IMPORT_WORKFLOW_REPO
            or claims.get("workflow_ref") != expected
            or claims.get("workflow_sha") != settings.IMPORT_WORKFLOW_SHA
            or claims.get("sha") != settings.IMPORT_WORKFLOW_SHA
            or claims.get("ref")
            != f"refs/heads/{settings.IMPORT_WORKFLOW_REF}"
            or (
                settings.IMPORT_DISPATCH_WORKFLOW != "import-project.yml"
                and (
                    claims.get("job_workflow_ref") != reusable
                    or claims.get("job_workflow_sha")
                    != settings.IMPORT_WORKFLOW_SHA
                )
            )
            or claims.get("event_name") != "workflow_dispatch"
            or str(claims.get("run_attempt")) != "1"
            or not str(claims.get("run_id", "")).isdigit()
        ):
            stop("Cette CI n’est pas autorisée", 403)
    else:
        if (
            not claims.get("preferred_username")
            or claims.get("azp") != settings.KEYCLOAK_CLIENT_ID
        ):
            stop("Identité ou client du portail invalide", 401)
        # The platform realm can omit sub. Resolve it only after verifying the
        # signature, expiry, issuer, audience and authorized portal client.
        if not claims.get("sub"):
            claims["sub"] = keycloak_service.get_user_id_from_token(claims)
    return claims


async def github(method, path, token, **kwargs):
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(
            method,
            "https://api.github.com" + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            **kwargs,
        )
    if not response.is_success:
        stop(f"GitHub a refusé l’opération ({response.status_code})", 502)
    return response.json() if response.content else {}


async def scoped_token(job, write=False):
    name = REPOSITORY.fullmatch(job.repository_url).group(2)
    permissions = {
        "contents": "write" if write else "read",
        "pull_requests": "write" if write else "read",
    }
    data = await github(
        "POST",
        f"/app/installations/{job.installation_id}/access_tokens",
        generate_jwt(),
        json={"repositories": [name], "permissions": permissions},
    )
    if not data.get("token"):
        stop("Autorisation GitHub manquante", 403)
    return data["token"]


async def platform_chart_token():
    data = await github(
        "POST",
        f"/app/installations/{settings.GITHUB_INSTALLATION_ID}/access_tokens",
        generate_jwt(),
        json={
            "repositories": ["infra-templates"],
            "permissions": {"contents": "read"},
        },
    )
    if not data.get("token"):
        stop("Le chart approuvé n’est pas accessible", 403)
    return data["token"]


async def require_gitops_consumer():
    """Refuse imports before the reviewed ApplicationSet update is actually live."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            settings.IMPORT_ARGOCD_URL.rstrip("/")
            + "/api/v1/applicationsets/cnp-apps",
            headers={
                "Authorization": f"Bearer {settings.IMPORT_ARGOCD_TOKEN}"
            },
        )
    response.raise_for_status()
    spec = response.json().get("spec", {})
    sources = spec.get("template", {}).get("spec", {}).get("sources", [])
    generators = json.dumps(spec.get("generators", []))
    if (
        len(sources) != 2
        or sources[0].get("repoURL")
        != "https://github.com/3-Istor/infra-templates.git"
        or sources[1].get("repoURL") != "{{ .repoURL }}"
        or sources[1].get("ref") != "values"
        or sources[0].get("targetRevision") != "{{ .chartRevision }}"
        or sources[1].get("targetRevision") != "{{ .sourceRevision }}"
        or "sourceRevision" not in generators
        or "chartRevision" not in generators
        or "imageDigest" not in generators
        or "imageDigest" not in sources[0].get("helm", {}).get("values", "")
    ):
        stop(
            "Déployez la branche K3s qui fige les révisions avant d’activer l’import",
            503,
        )

    if settings.CNP_REGISTRY_BRANCH != "main":
        annotations = (
            response.json().get("metadata", {}).get("annotations", {})
        )
        if (
            annotations.get("cnp.3istor.com/import-test-project")
            != settings.IMPORT_ALLOWED_PROJECT
            or annotations.get("cnp.3istor.com/import-test-registry")
            != settings.CNP_REGISTRY_BRANCH
        ):
            stop(
                "L’ApplicationSet n’est pas configuré pour ce projet et ce registre de recette",
                503,
            )
    await require_registry_contract()
    await require_image_updater_consumer()


async def require_registry_contract():
    token = await get_installation_token(settings.GITHUB_INSTALLATION_ID)
    result = await github(
        "GET",
        f"/repos/{settings.CNP_REGISTRY_REPO}/contents/registry/schema.json",
        token,
        params={"ref": settings.CNP_REGISTRY_BRANCH},
    )
    try:
        schema = json.loads(base64.b64decode(result["content"]))
        properties = schema["properties"]["spec"]["properties"]["apps"][
            "items"
        ]["properties"]
    except (KeyError, TypeError, ValueError):
        stop("Le schéma du registre d’import ne peut pas être vérifié", 503)
    if (
        not isinstance(properties, dict)
        or not {"sourceRevision", "chartRevision", "imageTag", "imageDigest"}
        <= properties.keys()
    ):
        stop(
            "Le registre doit accepter les révisions et le digest avant d’activer l’import",
            503,
        )


async def require_image_updater_consumer():
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            settings.IMPORT_ARGOCD_URL.rstrip("/")
            + "/api/v1/applicationsets/cnp-image-updaters",
            headers={
                "Authorization": f"Bearer {settings.IMPORT_ARGOCD_TOKEN}"
            },
        )
    response.raise_for_status()
    data = response.json()
    source = (
        data.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("source", {})
    )
    revision = source.get("targetRevision", "HEAD")
    if settings.CNP_REGISTRY_BRANCH != "main":
        annotations = data.get("metadata", {}).get("annotations", {})
        revision = annotations.get(
            "cnp.3istor.com/import-test-k3s-revision", ""
        )
        expected = (
            '{{ if eq .metadata.name "'
            + settings.IMPORT_ALLOWED_PROJECT
            + '" }}'
            + revision
            + "{{ else }}HEAD{{ end }}"
        )
        if (
            not SHA.fullmatch(revision)
            or source.get("targetRevision") != expected
        ):
            stop(
                "Le contrôleur d’images de recette n’utilise pas la révision K3s approuvée",
                503,
            )
    if (
        source.get("repoURL", "").removesuffix(".git")
        != "https://github.com/3-Istor/K3s"
        or source.get("path") != "k8s/helm/cnp-image-updaters"
    ):
        stop(
            "Le contrôleur d’images ne correspond pas au contrat de plateforme",
            503,
        )
    token = await get_installation_token(settings.GITHUB_INSTALLATION_ID)
    result = await github(
        "GET",
        "/repos/3-Istor/K3s/contents/k8s/helm/cnp-image-updaters/templates/imageupdaters.yaml",
        token,
        params={"ref": revision},
    )
    try:
        template = base64.b64decode(result["content"]).decode()
    except (KeyError, TypeError, ValueError):
        stop("La protection des repos importés ne peut pas être vérifiée", 503)
    if 'if not (hasKey $app "sourceRevision")' not in template:
        stop(
            "Déployez la protection ImageUpdater des imports avant de continuer",
            503,
        )


def fresh_project(db, project_name, sub, username):
    if (
        settings.IMPORT_ALLOWED_PROJECT
        and project_name != settings.IMPORT_ALLOWED_PROJECT
    ):
        stop("Cet environnement de recette est limité au projet autorisé", 403)
    project = (
        db.query(Project).filter_by(project_name=project_name).one_or_none()
    )
    if not project or project.status != ProjectStatus.ACTIVE:
        stop("Le projet existant doit être actif", 403)
    # Fail closed even for the owner when Keycloak is unavailable or the account disabled.
    admin = keycloak_service._get_admin_token()
    user = keycloak_service._find_user_by_username(username, admin)
    if not user or user.get("id") != sub or not user.get("enabled", False):
        stop("L’identité du portail n’est plus autorisée", 403)
    response = requests.get(
        f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{sub}/groups",
        headers={"Authorization": f"Bearer {admin}"},
        timeout=10,
    )
    response.raise_for_status()
    groups = {g["name"] for g in response.json()}
    if (
        username != project.owner_username
        and f"project-{project_name}-admins" not in groups
    ):
        stop("Seul un propriétaire ou administrateur peut importer", 403)
    return project


async def repository_user_permission(job, token, write=False):
    """An installation ID is not proof that the portal user owns its repositories."""
    identity = await run_in_threadpool(linked_identity, job.user_sub)
    if identity is None:
        stop(
            "Liez votre identité GitHub à votre compte CNP dans les comptes liés Keycloak",
            403,
        )
    login = identity["login"]
    user = await github("GET", f"/users/{login}", token)
    if str(user.get("id")) != identity["id"]:
        stop("L’identité GitHub liée a changé", 403)
    permission = await github(
        "GET",
        "/repos/"
        + job.repository_url.removeprefix("https://github.com/")
        + f"/collaborators/{login}/permission",
        token,
    )
    allowed = {"admin", "write"} if write else {"admin", "write", "read"}
    if permission.get("permission") not in allowed:
        stop(
            "Vos droits GitHub sur ce repo sont insuffisants pour cette étape",
            403,
        )


async def require_runtime_secrets(job):
    keys = job.configuration.get("runtime_secret_keys", [])
    if not keys:
        return
    if not settings.VAULT_URL or not settings.VAULT_TOKEN:
        stop("Vault est requis pour les secrets de l’application", 503)
    path = f"project-{job.project_name}/data/{job.app_name}/runtime"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            settings.VAULT_URL.rstrip("/") + "/v1/" + path,
            headers={"X-Vault-Token": settings.VAULT_TOKEN},
        )
    if response.status_code == 404:
        stop(
            "Configurez les secrets requis dans Vault avant de lancer l’import",
            422,
        )
    response.raise_for_status()
    data = response.json().get("data", {}).get("data", {})
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key] for key in keys
    ):
        stop(
            "Des secrets requis de l’application sont absents ou vides dans Vault",
            422,
        )


async def authorize(db, job, write=False):
    enabled()
    if job.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        stop("L’autorisation de ce parcours a expiré", 403)
    project = await run_in_threadpool(
        fresh_project, db, job.project_name, job.user_sub, job.username
    )
    link = (
        db.query(UserGitHubInstallation)
        .filter(
            or_(
                UserGitHubInstallation.user_sub == job.user_sub,
                UserGitHubInstallation.user_sub == job.username,
            )
        )
        .one_or_none()
    )
    if not link or link.installation_id != job.installation_id:
        stop("L’autorisation GitHub du portail a changé", 403)
    record = await project_registry.read_record(job.project_name)
    if (
        not record
        or record[0].get("spec", {}).get("status") != "active"
        or record[0]["spec"].get("targetCloud")
        != job.configuration["target_cloud"]
    ):
        stop("Le registre du projet n’est pas validé", 403)
    if project.target_cloud.value != job.configuration["target_cloud"]:
        stop("Le cloud cible a changé", 403)
    if job.configuration["chart_sha"] != settings.IMPORT_CHART_SHA:
        stop("La version approuvée du chart a changé", 403)
    await ensure_cluster_up(project.target_cloud)
    await require_gitops_consumer()
    await require_runtime_secrets(job)
    await project_registry.ensure_app_names_free(
        job.project_name, job.app_name, "static", job.configuration["hostname"]
    )
    token = await scoped_token(job)
    await repository_user_permission(job, token, write=write)
    repo = await github(
        "GET",
        "/repos/" + job.repository_url.removeprefix("https://github.com/"),
        token,
    )
    if (
        repo.get("html_url", "").lower() != job.repository_url.lower()
        or repo.get("archived")
        or repo.get("disabled")
    ):
        stop("Le repo demandé n’est plus accessible", 403)
    existing_apps = record[0]["spec"].get("apps", [])
    for app in existing_apps:
        if app.get("name") == job.app_name and (
            not job.deployment_id
            or app.get("repoURL", "").lower() != job.repository_url.lower()
        ):
            stop("Cette application existe déjà dans le registre", 409)
    repository_id = repo.get("id")
    if not isinstance(repository_id, int) or repository_id <= 0:
        stop("Identifiant de repo GitHub manquant", 502)
    if job.configuration.get("repository_id", repository_id) != repository_id:
        stop("L’identité du repo GitHub a changé", 403)
    job.configuration = {**job.configuration, "repository_id": repository_id}
    return token


async def head(job, token):
    data = await github(
        "GET",
        "/repos/"
        + job.repository_url.removeprefix("https://github.com/")
        + "/commits/"
        + quote(job.configuration.get("source_branch", "main"), safe=""),
        token,
    )
    if not SHA.fullmatch(data.get("sha", "")):
        stop("Révision GitHub invalide", 502)
    return data["sha"]


def transition(db, job, expected, status, **changes):
    result = db.execute(
        update(RepositoryImport)
        .where(
            RepositoryImport.id == job.id,
            RepositoryImport.status == expected,
            RepositoryImport.run_id == job.run_id,
        )
        .values(status=status, **changes)
    )
    if result.rowcount != 1:
        db.rollback()
        stop("Le parcours a changé ou a déjà été exécuté")
    db.commit()
    db.refresh(job)


async def require_workflow_configuration():
    token = await get_installation_token(settings.GITHUB_INSTALLATION_ID)
    revision = await github(
        "GET",
        f"/repos/{settings.IMPORT_WORKFLOW_REPO}/commits/{settings.IMPORT_WORKFLOW_REF}",
        token,
    )
    if revision.get("sha") != settings.IMPORT_WORKFLOW_SHA:
        stop("La CI centrale ne correspond plus à la version approuvée", 503)
    for name, expected in (
        ("CMP_IMPORT_API_URL", settings.IMPORT_CALLBACK_URL.rstrip("/")),
        ("CMP_IMPORT_OIDC_AUDIENCE", settings.IMPORT_OIDC_AUDIENCE),
    ):
        variable = await github(
            "GET",
            f"/repos/{settings.IMPORT_WORKFLOW_REPO}/actions/variables/{name}",
            token,
        )
        if variable.get("value", "").rstrip("/") != expected:
            stop(
                "La CI ne cible pas ce CMP ou son audience ; corrigez les variables GitHub avant de lancer l’import",
                503,
            )
    return token


async def dispatch(job):
    token = await require_workflow_configuration()
    await github(
        "POST",
        f"/repos/{settings.IMPORT_WORKFLOW_REPO}/actions/workflows/{settings.IMPORT_DISPATCH_WORKFLOW}/dispatches",
        token,
        json={
            "ref": settings.IMPORT_WORKFLOW_REF,
            "inputs": {"import_id": job.id},
        },
    )


def public(job):
    return {
        key: getattr(job, key)
        for key in (
            "id",
            "project_name",
            "app_name",
            "repository_url",
            "status",
            "message",
            "source_sha",
            "pull_request",
            "deployment_id",
            "report",
        )
    }


async def create(db, claims, payload):
    project = await run_in_threadpool(
        fresh_project,
        db,
        payload.project_name,
        claims["sub"],
        claims["preferred_username"],
    )
    if not payload.allow_changes:
        stop(
            "Validez l’autorisation de proposer les fichiers par PR avant de démarrer",
            422,
        )
    match = REPOSITORY.fullmatch(payload.repository_url.strip())
    if not match or match.group(2) in {".", ".."}:
        stop("URL GitHub invalide", 422)
    if (
        payload.app_name == "system"
        or len(f"{payload.project_name}-{payload.app_name}-app") > 63
    ):
        stop(
            "Le nom d’application est réservé ou trop long pour ce projet", 422
        )
    if ".." in Path(
        payload.project_path
    ).parts or payload.project_path.startswith("/"):
        stop("Le dossier doit rester dans le repo", 422)
    if db.query(Deployment).filter_by(name=payload.app_name).first():
        stop("Ce nom d’application est déjà utilisé")
    link = (
        db.query(UserGitHubInstallation)
        .filter(
            or_(
                UserGitHubInstallation.user_sub == claims["sub"],
                UserGitHubInstallation.user_sub
                == claims["preferred_username"],
            )
        )
        .one_or_none()
    )
    if not link:
        stop("Reliez d’abord votre compte GitHub dans le portail", 403)
    import_id = str(uuid4())
    job = RepositoryImport(
        id=import_id,
        project_name=payload.project_name,
        app_name=payload.app_name,
        repository_url=f"https://github.com/{match.group(1).lower()}/{match.group(2).lower()}",
        user_sub=claims["sub"],
        username=claims["preferred_username"],
        installation_id=link.installation_id,
        status="queued",
        report={},
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),
        configuration={
            "source_branch": getattr(payload, "source_branch", "main"),
            "project_path": payload.project_path,
            "port": payload.port,
            "health_path": payload.health_path,
            "stack": payload.stack,
            "runtime_environment": getattr(payload, "runtime_environment", {}),
            "smoke_environment": getattr(payload, "smoke_environment", {}),
            "runtime_secret_keys": getattr(payload, "runtime_secret_keys", []),
            "target_cloud": project.target_cloud.value,
            "chart_sha": settings.IMPORT_CHART_SHA,
            "image": f"{settings.IMPORT_IMAGE_PREFIX.lower()}/{payload.app_name}:import-{import_id}",
            "hostname": f"{payload.app_name}-{payload.project_name}.{settings.IMPORT_DOMAIN}",
        },
    )
    token = await authorize(
        db, job
    )  # No clone, commit or dispatch before permission checks.
    await head(job, token)  # Refuse missing branches before dispatch.
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        stop(
            "Ce repo ou ce nom d’application possède déjà un parcours d’import"
        )
    try:
        await dispatch(job)
    except Exception:
        job.status, job.message = "failed", "La CI n’a pas pu être démarrée"
        db.commit()
        raise
    return public(job)


async def approved_pr(job, token):
    path = "/repos/" + job.repository_url.removeprefix("https://github.com/")
    pr = await github("GET", path + f"/pulls/{job.pull_request}", token)
    if (
        not pr.get("merged")
        or pr.get("base", {}).get("ref")
        != job.configuration.get("source_branch", "main")
        or pr.get("head", {}).get("ref") != f"cnp-import/{job.id}"
    ):
        stop("La PR doit être validée et fusionnée avant de reprendre")
    # Fetch every page: a later dismissal/change request must supersede an approval.
    latest = {}
    page = 1
    while True:
        reviews = await github(
            "GET",
            path + f"/pulls/{job.pull_request}/reviews",
            token,
            params={"per_page": 100, "page": page},
        )
        for review in reviews:
            if review.get("state") != "COMMENTED":
                latest[review.get("user", {}).get("id")] = review
        if len(reviews) < 100:
            break
        page += 1
    author = pr.get("user", {}).get("id")
    approvals = [
        review
        for actor, review in latest.items()
        if actor
        and actor != author
        and review.get("state") == "APPROVED"
        and review.get("commit_id") == pr["head"].get("sha")
    ]
    if not approvals or any(
        review.get("state") == "CHANGES_REQUESTED"
        for review in latest.values()
    ):
        stop(
            "Une approbation indépendante sur la dernière révision de la PR est requise"
        )


async def resume(db, job):
    if job.status != "waiting_pr" or not job.pull_request:
        stop("Le parcours n’attend pas de PR")
    job.expires_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) + timedelta(hours=1)
    token = await authorize(db, job)
    await approved_pr(job, token)
    transition(
        db,
        job,
        "waiting_pr",
        "queued",
        run_id=None,
        message="PR fusionnée : nouvelle validation complète",
    )
    try:
        await dispatch(job)
    except Exception:
        job.status, job.message = "failed", "La reprise de la CI a échoué"
        db.commit()
        raise
    return public(job)


async def retry(db, job):
    """Only an explicit portal action can restart a failed pre-deployment run."""
    if job.status not in {"failed", "cancelled"} or job.deployment_id:
        stop(
            "Seuls les parcours arrêtés avant déploiement peuvent être relancés"
        )
    job.expires_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) + timedelta(hours=1)
    token = await authorize(db, job)
    path = "/repos/" + job.repository_url.removeprefix("https://github.com/")
    owner = REPOSITORY.fullmatch(job.repository_url).group(1)
    prs = await github(
        "GET",
        path + "/pulls",
        token,
        params={
            "state": "all",
            "head": f"{owner}:cnp-import/{job.id}",
            "base": job.configuration.get("source_branch", "main"),
        },
    )
    if prs:
        if len(prs) != 1 or (
            prs[0].get("state") != "open" and not prs[0].get("merged_at")
        ):
            stop(
                "La PR précédente a été refusée ; corrigez ce parcours avant de le relancer"
            )
        transition(
            db,
            job,
            job.status,
            "waiting_pr",
            run_id=None,
            pull_request=prs[0]["number"],
            message="PR existante retrouvée : validez sa dernière révision et reprenez ici",
        )
        return public(job)
    refs = await github(
        "GET", path + f"/git/matching-refs/heads/cnp-import/{job.id}", token
    )
    if refs:
        stop(
            "Une branche de préparation existe sans PR ; ouvrez sa PR avant de relancer"
        )
    transition(
        db,
        job,
        job.status,
        "queued",
        run_id=None,
        source_sha=None,
        pull_request=None,
        message="Nouvelle autorisation : reprise complète des contrôles",
    )
    try:
        await dispatch(job)
    except Exception:
        job.status, job.message = "failed", "La relance de la CI a échoué"
        db.commit()
        raise
    return public(job)


async def worker(db, job, claims, action, payload, tasks):
    run_id = str(claims["run_id"])
    if (
        action == "fail"
        and job.run_id == run_id
        and job.status in {"failed", "cancelled", "waiting_pr"}
    ):
        if job.status == "failed" and payload.get("report"):
            job.report = {**job.report, **payload["report"]}
            db.commit()
        return {"status": job.status}
    if job.status in TERMINAL or job.status in {"waiting_pr", "deploying"}:
        stop("Le parcours est arrêté")
    if action == "claim":
        if job.status != "queued" or job.run_id:
            stop("Le parcours est déjà pris en charge")
    elif job.run_id != run_id:
        stop("Cette exécution ne possède pas le parcours", 403)
    if action == "fail":
        transition(
            db,
            job,
            job.status,
            "failed",
            report=payload.get("report", {}),
            message="La CI a échoué ou a été annulée ; aucune suite autorisée",
        )
        return {"status": "failed"}
    try:
        token = await authorize(db, job, write=action == "write")
        if action == "claim":
            if job.pull_request:
                await approved_pr(job, token)
            sha = await head(job, token)
            transition(
                db,
                job,
                "queued",
                "preparing",
                run_id=run_id,
                source_sha=sha,
                message="Analyse et préparation",
            )
            return {
                "repository_url": job.repository_url,
                "source_sha": sha,
                "token": token,
                **job.configuration,
                "project_name": job.project_name,
                "app_name": job.app_name,
                "has_merged_pr": bool(job.pull_request),
                "chart_token": await platform_chart_token(),
            }
        if action == "write" and job.status == "preparing":
            if await head(job, token) != job.source_sha:
                stop("Le repo a changé pendant l’analyse")
            return {"token": await scoped_token(job, write=True)}
        if action == "prepared" and job.status == "preparing":
            if await head(job, token) != job.source_sha:
                stop("Le repo a changé avant la validation de la PR")
            number = payload.get("pull_request")
            if not isinstance(number, int) or number <= 0:
                stop("PR manquante", 422)
            pr = await github(
                "GET",
                "/repos/"
                + job.repository_url.removeprefix("https://github.com/")
                + f"/pulls/{number}",
                token,
            )
            if (
                pr.get("head", {}).get("ref") != f"cnp-import/{job.id}"
                or pr.get("base", {}).get("ref")
                != job.configuration.get("source_branch", "main")
                or pr.get("state") != "open"
            ):
                stop("La PR ne correspond pas à ce parcours", 403)
            transition(
                db,
                job,
                "preparing",
                "waiting_pr",
                pull_request=number,
                report=payload.get("report", {}),
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(days=7),
                message="Arrêt : validez et fusionnez la PR puis reprenez ici",
            )
            return {"status": "waiting_pr"}
        if action == "validated" and job.status == "preparing":
            if (
                payload.get("source_sha") != job.source_sha
                or await head(job, token) != job.source_sha
            ):
                stop("La révision analysée a changé")
            report = payload.get("report", {})
            if (
                report.get("security", {}).get("blocked") is not False
                or report.get("deployment", {}).get("blocked") is not False
                or report.get("rendered_deployment", {}).get("blocked")
                is not False
            ):
                stop("Scans bloquants ou manquants", 422)
            transition(
                db,
                job,
                "preparing",
                "building",
                report=report,
                message="Scans validés : construction de l’image",
            )
            return {"image": job.configuration["image"]}
        if action == "publish" and job.status == "building":
            if (
                payload.get("image") != job.configuration["image"]
                or payload.get("source_sha") != job.source_sha
            ):
                stop("Image ou révision incorrecte", 422)
            size = payload.get("image_size")
            if (
                not isinstance(size, int)
                or size <= 0
                or size > settings.IMPORT_MAX_IMAGE_BYTES
            ):
                stop("Taille de l’image refusée", 422)
            if (
                payload.get("image_scan_passed") is not True
                or payload.get("http_check_passed") is not True
            ):
                stop("Scan des dépendances de l’image manquant", 422)
            if await head(job, token) != job.source_sha:
                stop("Le repo a changé avant publication")
            transition(
                db,
                job,
                "building",
                "publishing",
                message="Publication de l’image validée",
            )
            return {"image": job.configuration["image"]}
        if action == "complete" and job.status == "publishing":
            if (
                not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", payload.get("digest", "")
                )
                or payload.get("image") != job.configuration["image"]
            ):
                stop("Preuve de publication invalide", 422)
            if await head(job, token) != job.source_sha:
                stop("Le repo a changé avant déploiement")
            if db.query(Deployment).filter_by(name=job.app_name).first():
                stop("Une application existe déjà avec ce nom")
            deployment = Deployment(
                name=job.app_name,
                template_id="k3s-gitops-app",
                template_name="Repository import",
                project_id=job.project_name,
                provider_type=ProviderType.KUBERNETES,
                github_repo_url=job.repository_url,
                status=DeploymentStatus.PENDING,
                step_message="Import validé : déploiement du repo existant",
                app_config=json.dumps(
                    {
                        "repository_mode": "existing",
                        "existing_repository_url": job.repository_url,
                        "github_owner": REPOSITORY.fullmatch(
                            job.repository_url
                        ).group(1),
                        "target_cloud": job.configuration["target_cloud"],
                        "github_installation_id": job.installation_id,
                        "project_name": job.project_name,
                        "domain": settings.IMPORT_DOMAIN,
                        "runtime_secret_keys": job.configuration.get(
                            "runtime_secret_keys", []
                        ),
                    }
                ),
            )
            # Atomic single-consumer transition and deployment creation in one transaction.
            result = db.execute(
                update(RepositoryImport)
                .where(
                    RepositoryImport.id == job.id,
                    RepositoryImport.status == "publishing",
                    RepositoryImport.run_id == run_id,
                )
                .values(
                    status="deploying",
                    message="Déploiement dans le projet existant",
                )
            )
            if result.rowcount != 1:
                db.rollback()
                stop("Déploiement déjà déclenché")
            db.add(deployment)
            db.flush()
            db.refresh(job)
            job.deployment_id = deployment.id
            job.report = {
                **job.report,
                "image": job.configuration["image"],
                "digest": payload["digest"],
            }
            db.commit()
            from app.services.terraform_orchestrator import run_deployment

            tasks.add_task(run_deployment, deployment.id)
            return {"status": "deploying", "deployment_id": deployment.id}
        stop("Étape invalide ou validation précédente manquante")
    except Exception:
        db.rollback()
        db.refresh(job)
        if job.status not in TERMINAL | {"waiting_pr", "deploying"}:
            job.status, job.message = (
                "failed",
                "Un contrôle a échoué ; parcours arrêté",
            )
            db.commit()
        raise


def deployment_gate(db, deployment):
    """Recheck the portal grant and immutable source before each deploy mutation."""
    job = (
        db.query(RepositoryImport)
        .filter_by(deployment_id=deployment.id)
        .one_or_none()
    )
    if not job or job.status != "deploying":
        stop("Le déploiement ne possède pas d’import validé", 403)

    async def check():
        token = await authorize(db, job)
        if await head(job, token) != job.source_sha:
            stop("Le repo a changé après les scans")

    asyncio.run(check())
    return job


def register_argo_repository(job):
    """Argo renews its own read credentials using the existing App installation."""
    headers = {"Authorization": f"Bearer {settings.IMPORT_ARGOCD_TOKEN}"}
    base = settings.IMPORT_ARGOCD_URL.rstrip("/") + "/api/v1/repositories"
    response = httpx.get(
        base + "/" + quote(job.repository_url, safe=""),
        headers=headers,
        timeout=20,
    )
    if response.status_code == 404:
        response = httpx.post(
            base,
            headers=headers,
            timeout=20,
            json={
                "repo": job.repository_url,
                "type": "git",
                "githubAppID": 3836905,
                "githubAppInstallationID": int(job.installation_id),
                "githubAppPrivateKey": settings.GITHUB_APP_PRIVATE_KEY,
            },
        )
    response.raise_for_status()
    if (
        response.json().get("connectionState", {}).get("status")
        != "Successful"
    ):
        stop("Argo CD ne peut pas lire le repo importé", 502)


def wait_for_argo(job, timeout=300):
    """Require the actual source revision to be Synced and Healthy, not an SSO redirect."""
    import time

    from ruamel.yaml import YAML

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = httpx.get(
            settings.IMPORT_ARGOCD_URL.rstrip("/") + "/api/v1/applications",
            headers={
                "Authorization": f"Bearer {settings.IMPORT_ARGOCD_TOKEN}"
            },
            timeout=20,
        )
        response.raise_for_status()
        matches = []
        for item in response.json().get("items", []):
            spec = item.get("spec", {})
            sources = spec.get("sources", [spec.get("source", {})])
            inline = (
                YAML(typ="safe").load(
                    sources[0].get("helm", {}).get("values", "{}")
                )
                if sources
                else {}
            )
            if (
                item.get("metadata", {}).get("name")
                == f"{job.project_name}-{job.app_name}-app"
                and spec.get("project") == job.project_name
                and spec.get("destination", {}).get("namespace")
                == f"{job.project_name}-{job.app_name}"
                and spec.get("destination", {}).get("name")
                == job.configuration["target_cloud"]
                and len(sources) == 2
                and sources[0].get("targetRevision")
                == job.configuration["chart_sha"]
                and sources[1].get("targetRevision") == job.source_sha
                and isinstance(inline, dict)
                and inline.get("image", {}).get("tag")
                == job.configuration["image"].rsplit(":", 1)[1]
                + "@"
                + job.report["digest"]
                and any(
                    source.get("repoURL", "").removesuffix(".git").lower()
                    == job.repository_url.lower()
                    for source in sources
                )
            ):
                matches.append(item)
        if matches:
            for item in matches:
                # Missing is normal during initial reconciliation; degraded is an immediate refusal.
                if (
                    item.get("status", {}).get("health", {}).get("status")
                    == "Degraded"
                ):
                    stop("Argo CD signale une application dégradée", 502)
            if all(
                item.get("status", {}).get("sync", {}).get("status")
                == "Synced"
                and item.get("status", {}).get("health", {}).get("status")
                == "Healthy"
                and item.get("status", {}).get("sync", {}).get("revisions")
                == [job.configuration["chart_sha"], job.source_sha]
                for item in matches
            ):
                return
        time.sleep(5)
    stop("Délai de synchronisation Argo CD dépassé", 504)
