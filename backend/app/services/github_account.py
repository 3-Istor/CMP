"""Link a verified portal identity to an already approved GitHub installation."""

import re

import httpx
import requests
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.models.user_github import UserGitHubInstallation
from app.services import github_service, keycloak_service

LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")


def portal_user(claims=Depends(keycloak_service.get_current_user)):
    """Require the signed CNP browser token, independently of import activation."""
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
        raise HTTPException(401, "Connectez-vous avec votre compte CNP")
    return claims


def linked_identity(user_sub):
    """Only Keycloak's verified federation identifies the GitHub account."""
    response = requests.get(
        f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}"
        f"/users/{user_sub}/federated-identity",
        headers={
            "Authorization": "Bearer " + keycloak_service._get_admin_token()
        },
        timeout=10,
    )
    response.raise_for_status()
    identities = [
        item
        for item in response.json()
        if item.get("identityProvider") == "github"
    ]
    if not identities:
        return None
    if len(identities) != 1:
        raise HTTPException(403, "La liaison GitHub du compte CNP est ambiguë")
    identity = identities[0]
    if not LOGIN.fullmatch(identity.get("userName", "")) or not re.fullmatch(
        r"[0-9]+", identity.get("userId", "")
    ):
        raise HTTPException(
            403, "L’identité GitHub liée au compte CNP est invalide"
        )
    return {"login": identity["userName"], "id": identity["userId"]}


def identity_for_user(claims):
    try:
        user = keycloak_service._find_user_by_username(
            claims["preferred_username"], keycloak_service._get_admin_token()
        )
        if not user or not user.get("enabled"):
            raise HTTPException(403, "Le compte CNP n’est plus autorisé")
        if claims.get("sub") and claims["sub"] != user["id"]:
            raise HTTPException(
                401, "L’identité CNP ne correspond plus à la session"
            )
        return user["id"], linked_identity(user["id"])
    except requests.RequestException as error:
        raise HTTPException(
            503, "La vérification du compte CNP est indisponible"
        ) from error


async def api(path, token):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.github.com" + path,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if response.status_code == 404:
            raise HTTPException(
                403,
                "Cette installation ou cette organisation GitHub n’est pas accessible à votre compte",
            )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(
            503,
            "La vérification GitHub est indisponible ; aucune liaison enregistrée",
        ) from error


async def verify_installation(installation_id, identity):
    if not re.fullmatch(r"[1-9][0-9]{0,19}", installation_id):
        raise HTTPException(422, "Identifiant d’installation GitHub invalide")
    try:
        installation = await api(
            "/app/installations/" + installation_id,
            github_service.generate_jwt(),
        )
        account = installation.get("account", {})
        if (
            str(installation.get("app_id"))
            != str(github_service.GITHUB_APP_ID)
            or installation.get("id") != int(installation_id)
            or installation.get("suspended_at")
            or not LOGIN.fullmatch(account.get("login", ""))
        ):
            raise HTTPException(
                403, "Cette installation CNP n’est pas autorisée"
            )
        organization = account.get("type") == "Organization"
        token = await github_service.get_installation_token(
            installation_id,
            permissions={"members": "read"} if organization else {},
        )
        user = await api("/users/" + identity["login"], token)
        if str(user.get("id")) != identity["id"]:
            raise HTTPException(
                403,
                "L’identité GitHub liée a changé ; reconnectez-la dans CNP",
            )
        if organization:
            membership = await api(
                f'/orgs/{account["login"]}/memberships/{user["login"]}', token
            )
            if membership.get("state") != "active":
                raise HTTPException(
                    403,
                    "Votre adhésion à l’organisation GitHub doit être active",
                )
        elif account.get("type") != "User" or account.get("id") != user.get(
            "id"
        ):
            raise HTTPException(
                403, "Cette installation appartient à un autre compte GitHub"
            )
        return {"login": user["login"], "account": account["login"]}
    except github_service.GitHubAppError as error:
        raise HTTPException(
            503,
            "La configuration de l’App CNP doit être vérifiée par un administrateur",
        ) from error


def records(db, user_sub, username):
    return (
        db.query(UserGitHubInstallation)
        .filter(
            or_(
                UserGitHubInstallation.user_sub == user_sub,
                UserGitHubInstallation.user_sub == username,
            )
        )
        .all()
    )


async def connection_status(db, claims):
    user_sub, identity = await run_in_threadpool(identity_for_user, claims)
    saved = records(db, user_sub, claims["preferred_username"])
    ids = {row.installation_id for row in saved}
    selected = next(iter(ids)) if identity and len(ids) == 1 else None
    if selected:
        try:
            await verify_installation(selected, identity)
        except HTTPException as error:
            if error.status_code not in {403, 422}:
                raise
            selected = None
    return {
        "github_installation_id": selected,
        "identity_linked": identity is not None,
        "github_login": identity["login"] if identity else None,
        "account_url": settings.GITHUB_ACCOUNT_LINK_URL or None,
    }


async def connect(db, claims, installation_id=None):
    user_sub, identity = await run_in_threadpool(identity_for_user, claims)
    if not identity:
        raise HTTPException(
            409,
            "Liez d’abord GitHub dans les comptes liés CNP, puis revenez ici",
        )
    selected = str(
        installation_id or settings.GITHUB_INSTALLATION_ID or ""
    ).strip()
    if not selected:
        raise HTTPException(
            503,
            "L’installation CNP existante doit être configurée par un administrateur",
        )
    verified = await verify_installation(selected, identity)
    saved = records(db, user_sub, claims["preferred_username"])
    canonical = next((row for row in saved if row.user_sub == user_sub), None)
    row = canonical or (
        saved[0] if saved else UserGitHubInstallation(user_sub=user_sub)
    )
    for duplicate in saved:
        if duplicate is not row:
            db.delete(duplicate)
    row.user_sub, row.installation_id = user_sub, selected
    db.add(row)
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(
            503, "La liaison GitHub n’a pas pu être enregistrée"
        ) from error
    return {
        "message": "Installation CNP existante rattachée après vérification de votre compte GitHub",
        "installation_id": selected,
        "github_login": verified["login"],
        "account": verified["account"],
    }
