"""Direct GitHub App OAuth, separate from CNP's existing authentication."""

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, or_, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_github import (
    GitHubOAuthCredential,
    GitHubOAuthState,
    UserGitHubInstallation,
)
from app.services import github_service, keycloak_service

LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
OAUTH_URL = "https://github.com/login/oauth/access_token"


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fail(message="La connexion GitHub doit être renouvelée", code=403):
    raise HTTPException(code, message)


def portal_user(claims=Depends(keycloak_service.get_current_user)):
    """Keep the signed CNP browser token and its existing client/audience."""
    audience = claims.get("aud", [])
    if isinstance(audience, str):
        audience = [audience]
    if (
        not isinstance(audience, list)
        or settings.IMPORT_USER_AUDIENCE not in audience
        or claims.get("azp") != settings.KEYCLOAK_CLIENT_ID
        or not claims.get("preferred_username")
        or not claims.get("exp")
        or not claims.get("iat")
    ):
        fail("Connectez-vous avec votre compte CNP", 401)
    return claims


def cnp_user_id(claims):
    """Resolve the existing active CNP account, without any GitHub federation."""
    try:
        user = keycloak_service._find_user_by_username(
            claims["preferred_username"], keycloak_service._get_admin_token()
        )
        if not user or not user.get("enabled"):
            fail("Le compte CNP n’est plus autorisé")
        if claims.get("sub") and claims["sub"] != user["id"]:
            fail("L’identité CNP ne correspond plus à la session", 401)
        return user["id"]
    except requests.RequestException as error:
        raise HTTPException(
            503, "La vérification du compte CNP est indisponible"
        ) from error


def check_oauth_configuration():
    try:
        uri = urlsplit(settings.GITHUB_OAUTH_REDIRECT_URI)
        local = uri.scheme == "http" and uri.hostname in {
            "localhost",
            "127.0.0.1",
        }
    except ValueError:
        uri, local = urlsplit(""), False
    try:
        Fernet(settings.GITHUB_OAUTH_TOKEN_KEY.encode())
        key_valid = True
    except (ValueError, TypeError):
        key_valid = False
    return {
        "GITHUB_OAUTH_CLIENT_ID": bool(settings.GITHUB_OAUTH_CLIENT_ID),
        "GITHUB_OAUTH_CLIENT_SECRET": bool(
            settings.GITHUB_OAUTH_CLIENT_SECRET
        ),
        "GITHUB_OAUTH_REDIRECT_URI": bool(
            (uri.scheme == "https" or local)
            and uri.hostname
            and uri.path == "/api/auth/github/callback"
            and not uri.query
            and not uri.fragment
            and not uri.username
            and not uri.password
        ),
        "GITHUB_OAUTH_TOKEN_KEY": key_valid,
    }


def cipher():
    if not all(check_oauth_configuration().values()):
        fail("OAuth GitHub n’est pas encore configuré dans CMP", 503)
    return Fernet(settings.GITHUB_OAUTH_TOKEN_KEY.encode())


def encrypt(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def decrypt(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, TypeError, AttributeError) as error:
        raise HTTPException(
            403, "La connexion GitHub doit être renouvelée"
        ) from error


def session_hash(claims, user_sub, binding):
    if not isinstance(binding, str) or not 32 <= len(binding) <= 256:
        fail("Session OAuth invalide", 422)
    session = claims.get("sid") or claims.get("session_state") or ""
    return hashlib.sha256(
        json.dumps([user_sub, session, binding]).encode()
    ).hexdigest()


async def begin(db, claims, binding, mode="authorize"):
    cipher()
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    state = secrets.token_urlsafe(48)
    verifier = secrets.token_urlsafe(64)
    db.add(
        GitHubOAuthState(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            user_sub=user_sub,
            session_hash=session_hash(claims, user_sub, binding),
            verifier_ciphertext=encrypt({"verifier": verifier, "mode": mode}),
            expires_at=now() + timedelta(minutes=10),
        )
    )
    db.commit()
    if mode == "install":
        app = await app_info()
        if not LOGIN.fullmatch(app.get("slug", "")):
            fail("L’App GitHub n’a pas de nom valide", 503)
        return {
            "authorization_url": f'https://github.com/apps/{app["slug"]}/installations/new?'
            + urlencode({"state": state})
        }
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return {
        "authorization_url": "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "offline_access",
            }
        )
    }


async def exchange(parameters):
    cipher()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                OAUTH_URL,
                data={
                    "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                    "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
                    **parameters,
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code in {400, 401, 403, 404}:
            fail()
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            fail("Réponse OAuth GitHub invalide", 503)
        if data.get("error"):
            fail()
        if (
            not isinstance(data.get("access_token"), str)
            or not data["access_token"]
        ):
            fail("Réponse OAuth GitHub invalide", 503)
        # Refuse unbounded credentials. Request offline_access and require the
        # documented expiring-token contract instead of guessing a token lifetime.
        if (
            data.get("token_type", "").lower() != "bearer"
            or not isinstance(data.get("refresh_token"), str)
            or not data["refresh_token"]
            or type(data.get("expires_in")) is not int
            or data["expires_in"] <= 60
            or type(data.get("refresh_token_expires_in")) is not int
            or data["refresh_token_expires_in"] <= 60
        ):
            fail(
                "Activez les jetons utilisateur GitHub expirants avant de lier le compte",
                503,
            )
        return data
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise HTTPException(
            503, "Le service OAuth GitHub est indisponible"
        ) from error


async def api(path, token, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.github.com" + path,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                **kwargs,
            )
        if response.status_code in {401, 403, 404}:
            fail(
                "L’accès GitHub est absent ou révoqué ; reconnectez le compte"
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            fail("Réponse GitHub invalide", 503)
        return data
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(
            503, "La vérification GitHub est indisponible"
        ) from error


def identity(data):
    if (
        not isinstance(data, dict)
        or type(data.get("id")) is not int
        or data["id"] <= 0
        or not isinstance(data.get("login"), str)
        or not LOGIN.fullmatch(data["login"])
    ):
        fail("L’identité GitHub retournée est invalide")
    return {"id": str(data["id"]), "login": data["login"]}


async def callback(db, claims, state, code, binding):
    cipher()
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    digest = hashlib.sha256(state.encode()).hexdigest()
    row = db.get(GitHubOAuthState, digest)
    bound = session_hash(claims, user_sub, binding)
    if not row or row.user_sub != user_sub or row.session_hash != bound:
        fail("Le retour OAuth ne correspond pas à cette session CNP")
    pending = decrypt(row.verifier_ciphertext)
    verifier = pending["verifier"]
    result = db.execute(
        update(GitHubOAuthState)
        .where(
            GitHubOAuthState.state_hash == digest,
            GitHubOAuthState.user_sub == user_sub,
            GitHubOAuthState.session_hash == bound,
            GitHubOAuthState.consumed_at.is_(None),
            GitHubOAuthState.expires_at > now(),
        )
        .values(consumed_at=now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        fail("Ce retour OAuth a expiré ou a déjà été utilisé")
    db.commit()  # Consume before exchange: concurrent/replayed callbacks never exchange twice.
    if pending.get("mode") == "install":
        # Installation callbacks (including their unsigned installation_id/code)
        # are not identity proof. Start a fresh PKCE web authorization instead.
        return await begin(db, claims, binding)
    if not code:
        fail("Le code OAuth GitHub est absent", 422)
    data = await exchange(
        {
            "code": code,
            "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
            "code_verifier": verifier,
        }
    )
    user = identity(await api("/user", data["access_token"]))
    row = db.get(GitHubOAuthCredential, user_sub)
    if row is None:
        row = GitHubOAuthCredential(user_sub=user_sub, version=0)
    row.github_user_id, row.github_login = user["id"], user["login"]
    row.credentials_ciphertext = encrypt(
        {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    )
    row.access_expires_at = now() + timedelta(seconds=data["expires_in"])
    row.refresh_expires_at = now() + timedelta(
        seconds=data["refresh_token_expires_in"]
    )
    row.refreshing_until, row.version = None, row.version + 1
    db.add(row)
    # OAuth never silently adopts a platform or previously selected installation.
    db.execute(
        delete(UserGitHubInstallation).where(
            UserGitHubInstallation.user_sub.in_(
                [user_sub, claims["preferred_username"]]
            )
        )
    )
    db.commit()
    return {"github_login": user["login"], "identity_linked": True}


async def fresh_credentials(db, user_sub):
    """Refresh in an isolated transaction: never flush or commit a caller's job."""
    cipher()
    with Session(bind=db.get_bind(), expire_on_commit=False) as isolated:
        row = isolated.get(GitHubOAuthCredential, user_sub)
        if not row:
            fail("Liez votre compte GitHub dans CMP avant de continuer")
        data = decrypt(row.credentials_ciphertext)
        if row.access_expires_at > now() + timedelta(seconds=60):
            return {
                "id": row.github_user_id,
                "login": row.github_login,
                "token": data["access_token"],
            }
        if row.refresh_expires_at <= now() + timedelta(seconds=60):
            fail()
        version, lease = row.version, now() + timedelta(seconds=90)
        result = isolated.execute(
            update(GitHubOAuthCredential)
            .where(
                GitHubOAuthCredential.user_sub == user_sub,
                GitHubOAuthCredential.version == version,
                or_(
                    GitHubOAuthCredential.refreshing_until.is_(None),
                    GitHubOAuthCredential.refreshing_until < now(),
                ),
            )
            .values(refreshing_until=lease)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            isolated.rollback()
            fail("Le jeton GitHub est en renouvellement ; réessayez", 503)
        isolated.commit()
        try:
            refreshed = await exchange(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                }
            )
            user = identity(await api("/user", refreshed["access_token"]))
            if user["id"] != row.github_user_id:
                fail("L’identité GitHub a changé ; reconnectez le compte")
            result = isolated.execute(
                update(GitHubOAuthCredential)
                .where(
                    GitHubOAuthCredential.user_sub == user_sub,
                    GitHubOAuthCredential.version == version,
                    GitHubOAuthCredential.refreshing_until == lease,
                )
                .values(
                    github_login=user["login"],
                    version=version + 1,
                    refreshing_until=None,
                    credentials_ciphertext=encrypt(
                        {
                            "access_token": refreshed["access_token"],
                            "refresh_token": refreshed["refresh_token"],
                        }
                    ),
                    access_expires_at=now()
                    + timedelta(seconds=refreshed["expires_in"]),
                    refresh_expires_at=now()
                    + timedelta(seconds=refreshed["refresh_token_expires_in"]),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                isolated.rollback()
                fail(
                    "La connexion GitHub a été remplacée pendant son renouvellement"
                )
            isolated.commit()
            return {**user, "token": refreshed["access_token"]}
        except Exception:
            isolated.rollback()
            # Clear only our lease. A newer OAuth callback/revision wins.
            isolated.execute(
                update(GitHubOAuthCredential)
                .where(
                    GitHubOAuthCredential.user_sub == user_sub,
                    GitHubOAuthCredential.version == version,
                    GitHubOAuthCredential.refreshing_until == lease,
                )
                .values(refreshing_until=None)
            )
            isolated.commit()
            raise


async def pages(path, token, field):
    values = []
    for page in range(1, 1001):
        response = await api(
            path, token, params={"per_page": 100, "page": page}
        )
        batch = response.get(field)
        if not isinstance(batch, list) or any(
            not isinstance(item, dict) for item in batch
        ):
            fail("La liste des accès GitHub ne peut pas être vérifiée", 503)
        values.extend(batch)
        if len(batch) < 100:
            return values
    fail("La liste des accès GitHub dépasse la limite de vérification", 503)


async def app_info():
    try:
        app = await api("/app", github_service.generate_jwt())
    except github_service.GitHubAppError as error:
        raise HTTPException(
            503, "L’App GitHub doit être configurée dans CMP"
        ) from error
    if type(app.get("id")) is not int or str(app["id"]) != str(
        github_service.GITHUB_APP_ID
    ):
        fail("L’App GitHub retournée ne correspond pas à CMP")
    return app


async def installations(token, app):
    values = await pages("/user/installations", token, "installations")
    return [
        item
        for item in values
        if item.get("app_id") == app["id"] and not item.get("suspended_at")
    ]


async def repository_identity(db, user_sub, installation_id):
    proof = await fresh_credentials(db, user_sub)
    current = identity(await api("/user", proof["token"]))
    if current["id"] != proof["id"]:
        fail("L’identité GitHub liée a changé")
    app = await app_info()
    available = await installations(proof["token"], app)
    selected = next(
        (
            item
            for item in available
            if str(item.get("id")) == str(installation_id)
        ),
        None,
    )
    if selected is None:
        fail("Cette installation n’est pas accessible à votre compte GitHub")
    installed = await api(
        "/app/installations/" + str(installation_id),
        github_service.generate_jwt(),
    )
    if (
        installed.get("id") != selected.get("id")
        or installed.get("app_id") != app["id"]
        or installed.get("suspended_at")
        or type(installed.get("account", {}).get("id")) is not int
        or installed["account"]["id"] <= 0
        or installed.get("account", {}).get("id")
        != selected.get("account", {}).get("id")
    ):
        fail("Cette installation GitHub n’est plus autorisée")
    return {**current, "token": proof["token"]}


async def repository_access(
    db, user_sub, installation_id, full_name, write=False
):
    proof = await repository_identity(db, user_sub, installation_id)
    repos = await pages(
        f"/user/installations/{installation_id}/repositories",
        proof["token"],
        "repositories",
    )
    repo = next(
        (
            item
            for item in repos
            if str(item.get("full_name", "")).lower() == full_name.lower()
        ),
        None,
    )
    permission = (repo or {}).get("permissions", {})
    if not isinstance(permission, dict):
        fail("Les permissions GitHub ne peuvent pas être vérifiées", 503)
    allowed = (
        permission.get("admin") is True
        or permission.get("push" if write else "pull") is True
    )
    if not repo or not allowed or repo.get("archived") or repo.get("disabled"):
        fail("Vos droits sur ce dépôt GitHub sont absents ou insuffisants")
    return {**proof, "repository": repo}


def records(db, user_sub, username):
    return (
        db.query(UserGitHubInstallation)
        .filter(UserGitHubInstallation.user_sub.in_([user_sub, username]))
        .all()
    )


async def connection_status(db, claims):
    configured = all(check_oauth_configuration().values())
    result = {
        "github_installation_id": None,
        "identity_linked": False,
        "github_login": None,
        "oauth_configured": configured,
        "installations": [],
        "installation_url": None,
    }
    if not configured:
        return result
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    with db.no_autoflush:
        exists = db.get(GitHubOAuthCredential, user_sub)
    app = await app_info()
    if not LOGIN.fullmatch(app.get("slug", "")):
        fail("L’App GitHub n’a pas de nom valide", 503)
    result["installation_url"] = (
        f'https://github.com/apps/{app["slug"]}/installations/new'
    )
    if not exists:
        return result
    try:
        proof = await fresh_credentials(db, user_sub)
        current = identity(await api("/user", proof["token"]))
        if current["id"] != proof["id"]:
            fail("L’identité GitHub liée a changé")
        available = await installations(proof["token"], app)
    except HTTPException as error:
        if error.status_code != 403:
            raise
        return result
    result.update(
        identity_linked=True,
        github_login=current["login"],
        installations=[
            {
                "id": str(item["id"]),
                "account": item.get("account", {}).get("login", ""),
                "account_type": item.get("account", {}).get("type", ""),
            }
            for item in available
        ],
    )
    saved = records(db, user_sub, claims["preferred_username"])
    ids = {row.installation_id for row in saved}
    selected = next(iter(ids)) if len(ids) == 1 else None
    if selected and any(
        item["id"] == selected for item in result["installations"]
    ):
        await repository_identity(db, user_sub, selected)
        result["github_installation_id"] = selected
    return result


async def connect(db, claims, installation_id):
    if not isinstance(installation_id, str) or not re.fullmatch(
        r"[1-9][0-9]{0,19}", installation_id
    ):
        fail(
            "Choisissez une installation GitHub accessible à votre compte", 422
        )
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    proof = await repository_identity(db, user_sub, installation_id)
    app = await app_info()
    available = await installations(proof["token"], app)
    selected = next(
        (item for item in available if str(item.get("id")) == installation_id),
        None,
    )
    if selected is None:
        fail("Cette installation n’est plus accessible à votre compte GitHub")
    saved = records(db, user_sub, claims["preferred_username"])
    row = next(
        (item for item in saved if item.user_sub == user_sub), None
    ) or (saved[0] if saved else UserGitHubInstallation(user_sub=user_sub))
    for duplicate in saved:
        if duplicate is not row:
            db.delete(duplicate)
    row.user_sub, row.installation_id = user_sub, installation_id
    db.add(row)
    db.commit()
    return {
        "message": "Installation GitHub vérifiée et sélectionnée",
        "installation_id": installation_id,
        "github_login": proof["login"],
        "account": selected.get("account", {}).get("login", ""),
    }
