"""
GitHub account linking (CNP-241): user OAuth of the CNP-Portal app.

The user's GitHub token is the proof of what they may import: GitHub lists
only the installations, and the repositories in them, its owner can reach.
An installation id sent by a browser proves nothing on its own, so one is
recorded only once the user's token shows it.

Flow: PKCE S256 with a hashed, single-use state bound to the Keycloak
session and to a random value held in an httpOnly cookie by the frontend
bridge (frontend/src/app/api/auth/github). Tokens are stored encrypted with
MultiFernet (GITHUB_TOKEN_ENCRYPTION_KEYS, newest first) and refreshed under
a lease, since a GitHub refresh token is single-use.
"""

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
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
INSTALLATION_ID = re.compile(r"^[1-9][0-9]{0,19}$")
GITHUB_API = "https://api.github.com"
OAUTH_URL = "https://github.com/login/oauth/access_token"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
CALLBACK_PATH = "/api/auth/github/callback"
STATE_TTL = timedelta(minutes=10)
REFRESH_LEASE = timedelta(seconds=90)
EARLY_REFRESH = timedelta(seconds=60)
MAX_PAGES = 20


class LinkError(Exception):
    """A linking step refused; ``status_code`` is what the API answers."""

    def __init__(self, message: str, status_code: int = 403) -> None:
        self.status_code = status_code
        super().__init__(message)


RELINK = "Your GitHub link must be renewed: link GitHub again."


def _is_int(value) -> bool:
    # bool is an int subclass: True must not pass for an id or a lifetime.
    return isinstance(value, int) and not isinstance(value, bool)


def _positive(value, floor: int = 0) -> bool:
    return _is_int(value) and value > floor


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cnp_user_id(claims: dict) -> str:
    """The caller's Keycloak id: tokens of this realm often carry no sub."""
    return keycloak_service.get_user_id_from_token(claims)


def _keys() -> list[Fernet]:
    keys = []
    for raw in settings.GITHUB_TOKEN_ENCRYPTION_KEYS.split(","):
        if raw.strip():
            try:
                keys.append(Fernet(raw.strip().encode()))
            except (ValueError, TypeError):
                return []
    return keys


def _redirect_uri_valid() -> bool:
    try:
        uri = urlsplit(settings.GITHUB_OAUTH_REDIRECT_URI)
        local = uri.scheme == "http" and uri.hostname in {
            "localhost",
            "127.0.0.1",
        }
    except ValueError:
        return False
    return bool(
        (uri.scheme == "https" or local)
        and uri.hostname
        and uri.path == CALLBACK_PATH
        and not uri.query
        and not uri.fragment
        and not uri.username
        and not uri.password
    )


def configuration() -> dict[str, bool]:
    """Which settings are usable, never their values."""
    return {
        "GITHUB_OAUTH_CLIENT_ID": bool(settings.GITHUB_OAUTH_CLIENT_ID),
        "GITHUB_OAUTH_CLIENT_SECRET": bool(
            settings.GITHUB_OAUTH_CLIENT_SECRET
        ),
        "GITHUB_OAUTH_REDIRECT_URI": _redirect_uri_valid(),
        "GITHUB_TOKEN_ENCRYPTION_KEYS": bool(_keys()),
        "GITHUB_APP_SLUG": bool(LOGIN.fullmatch(settings.GITHUB_APP_SLUG)),
    }


def is_configured() -> bool:
    return all(configuration().values())


def cipher() -> MultiFernet:
    if not is_configured():
        raise LinkError("GitHub linking is not configured on the CMP", 503)
    return MultiFernet(_keys())


def encrypt(value: dict) -> str:
    return cipher().encrypt(json.dumps(value).encode()).decode()


def decrypt(value: str) -> dict:
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, TypeError, AttributeError) as exc:
        raise LinkError(RELINK) from exc


def session_hash(claims: dict, user_sub: str, binding: str) -> str:
    if not isinstance(binding, str) or not 32 <= len(binding) <= 256:
        raise LinkError("Invalid OAuth session", 422)
    session = claims.get("sid") or claims.get("session_state") or ""
    return hashlib.sha256(
        json.dumps([user_sub, session, binding]).encode()
    ).hexdigest()


def install_url() -> str:
    return (
        f"https://github.com/apps/{settings.GITHUB_APP_SLUG}"
        "/installations/new"
    )


async def begin(
    db: Session, claims: dict, binding: str, mode: str = "authorize"
) -> dict:
    """Start an authorization (or an installation, then an authorization)."""
    cipher()
    if mode not in ("authorize", "install"):
        raise LinkError("Invalid GitHub flow", 422)
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    state = secrets.token_urlsafe(48)
    verifier = secrets.token_urlsafe(64)
    db.add(
        GitHubOAuthState(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            user_sub=user_sub,
            session_hash=session_hash(claims, user_sub, binding),
            verifier_ciphertext=encrypt({"verifier": verifier, "mode": mode}),
            expires_at=now() + STATE_TTL,
        )
    )
    db.commit()
    if mode == "install":
        return {
            "authorization_url": install_url()
            + "?"
            + urlencode({"state": state})
        }
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return {
        "authorization_url": AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    }


async def exchange(parameters: dict) -> dict:
    """
    Code or refresh token to user tokens. Only expiring tokens are
    accepted: the app must have "Expire user authorization tokens" on.
    """
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
            raise LinkError(RELINK)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LinkError("GitHub OAuth is unavailable", 503) from exc
    if not isinstance(data, dict) or data.get("error"):
        raise LinkError(RELINK)
    expiring = (
        _nonempty(data.get("access_token"))
        and _nonempty(data.get("refresh_token"))
        and str(data.get("token_type", "")).lower() == "bearer"
        and _positive(data.get("expires_in"), 60)
        and _positive(data.get("refresh_token_expires_in"), 60)
    )
    if not expiring:
        raise LinkError(
            "GitHub returned a token that does not expire: turn on expiring "
            "user tokens in the CNP-Portal app settings",
            503,
        )
    return data


async def api(path: str, token: str, **kwargs) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                GITHUB_API + path,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                **kwargs,
            )
        if response.status_code in {401, 403, 404}:
            raise LinkError(
                "GitHub access is missing or revoked: link GitHub again"
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LinkError("GitHub is unavailable", 503) from exc
    if not isinstance(data, dict):
        raise LinkError("GitHub answered with an unexpected response", 503)
    return data


def identity(data: dict) -> dict:
    if (
        not isinstance(data, dict)
        or not _positive(data.get("id"))
        or not isinstance(data.get("login"), str)
        or not LOGIN.fullmatch(data["login"])
    ):
        raise LinkError("GitHub returned an invalid identity")
    return {"id": str(data["id"]), "login": data["login"]}


async def callback(
    db: Session, claims: dict, state: str, code: str | None, binding: str
) -> dict:
    cipher()
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    digest = hashlib.sha256(state.encode()).hexdigest()
    bound = session_hash(claims, user_sub, binding)
    row = db.get(GitHubOAuthState, digest)
    if not row or row.user_sub != user_sub or row.session_hash != bound:
        raise LinkError("This GitHub answer does not belong to this session")
    pending = decrypt(row.verifier_ciphertext)
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
        raise LinkError("This GitHub answer expired or was already used")
    # Consumed before the exchange: a replayed callback never exchanges.
    db.commit()
    if pending.get("mode") == "install":
        # The installation redirect (its installation_id, its code) is not
        # proof of identity: continue with a fresh PKCE authorization.
        return await begin(db, claims, binding)
    if not code:
        raise LinkError("GitHub sent no authorization code", 422)
    data = await exchange(
        {
            "code": code,
            "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
            "code_verifier": pending["verifier"],
        }
    )
    user = identity(await api("/user", data["access_token"]))
    credential = db.get(GitHubOAuthCredential, user_sub)
    if credential is None:
        credential = GitHubOAuthCredential(user_sub=user_sub, version=0)
    credential.github_user_id = user["id"]
    credential.github_login = user["login"]
    credential.credentials_ciphertext = encrypt(
        {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    )
    credential.access_expires_at = now() + timedelta(
        seconds=data["expires_in"]
    )
    credential.refresh_expires_at = now() + timedelta(
        seconds=data["refresh_token_expires_in"]
    )
    credential.refreshing_until = None
    credential.version = (credential.version or 0) + 1
    db.add(credential)
    # A new GitHub identity never inherits an installation chosen before.
    db.execute(
        delete(UserGitHubInstallation).where(
            UserGitHubInstallation.user_sub.in_(
                [user_sub, claims.get("preferred_username") or user_sub]
            )
        )
    )
    db.commit()
    return {"github_login": user["login"], "identity_linked": True}


async def fresh_credentials(db: Session, user_sub: str) -> dict:
    """
    A usable access token of the user, refreshed if needed. The refresh
    runs in its own transaction so it never commits the caller's work.
    """
    cipher()
    with Session(bind=db.get_bind(), expire_on_commit=False) as isolated:
        row = isolated.get(GitHubOAuthCredential, user_sub)
        if not row:
            raise LinkError("Link GitHub from your account page first")
        data = decrypt(row.credentials_ciphertext)
        if row.access_expires_at > now() + EARLY_REFRESH:
            return {
                "id": row.github_user_id,
                "login": row.github_login,
                "token": data["access_token"],
            }
        if row.refresh_expires_at <= now() + EARLY_REFRESH:
            raise LinkError(RELINK)
        version, lease = row.version, now() + REFRESH_LEASE
        taken = isolated.execute(
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
        if taken.rowcount != 1:
            isolated.rollback()
            raise LinkError(
                "Your GitHub token is being renewed: try again", 503
            )
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
                raise LinkError(
                    "Your GitHub identity changed: link GitHub again"
                )
            saved = isolated.execute(
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
            if saved.rowcount != 1:
                isolated.rollback()
                raise LinkError(
                    "Your GitHub link was replaced while it was renewed"
                )
            isolated.commit()
            return {**user, "token": refreshed["access_token"]}
        except Exception:
            isolated.rollback()
            # Release only our lease: a newer link wins.
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


async def pages(path: str, token: str, field: str) -> list[dict]:
    values: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        response = await api(
            path, token, params={"per_page": 100, "page": page}
        )
        batch = response.get(field)
        if not isinstance(batch, list) or any(
            not isinstance(item, dict) for item in batch
        ):
            raise LinkError("GitHub answered with an unexpected list", 503)
        values.extend(batch)
        if len(batch) < 100:
            return values
    raise LinkError("Too many GitHub results to verify", 503)


async def installations(token: str) -> list[dict]:
    """The user's reachable, unsuspended installations of CNP-Portal."""
    app_id = int(github_service.GITHUB_APP_ID)
    return [
        item
        for item in await pages("/user/installations", token, "installations")
        if item.get("app_id") == app_id
        and not item.get("suspended_at")
        and _is_int(item.get("id"))
    ]


def records(db: Session, user_sub: str, username: str | None) -> list:
    keys = [user_sub] + ([username] if username else [])
    return (
        db.query(UserGitHubInstallation)
        .filter(UserGitHubInstallation.user_sub.in_(keys))
        .all()
    )


async def connection_status(db: Session, claims: dict) -> dict:
    result = {
        "github_installation_id": None,
        "identity_linked": False,
        "github_login": None,
        "oauth_configured": is_configured(),
        "installations": [],
        "installation_url": None,
    }
    if not result["oauth_configured"]:
        return result
    result["installation_url"] = install_url()
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    with db.no_autoflush:
        linked = db.get(GitHubOAuthCredential, user_sub)
    if not linked:
        return result
    try:
        proof = await fresh_credentials(db, user_sub)
        current = identity(await api("/user", proof["token"]))
        if current["id"] != proof["id"]:
            raise LinkError("Your GitHub identity changed: link it again")
        available = await installations(proof["token"])
    except LinkError as exc:
        if exc.status_code != 403:
            raise
        return result
    result.update(
        identity_linked=True,
        github_login=current["login"],
        installations=[
            {
                "id": str(item["id"]),
                "account": (item.get("account") or {}).get("login", ""),
                "account_type": (item.get("account") or {}).get("type", ""),
            }
            for item in available
        ],
    )
    reachable = {item["id"] for item in result["installations"]}
    saved = records(db, user_sub, claims.get("preferred_username"))
    selected = next(
        (r.installation_id for r in saved if r.installation_id in reachable),
        None,
    )
    result["github_installation_id"] = selected
    return result


async def connect(db: Session, claims: dict, installation_id: str) -> dict:
    """Record an installation once the user's own token shows it."""
    if not isinstance(installation_id, str) or not INSTALLATION_ID.fullmatch(
        installation_id
    ):
        raise LinkError("Pick a GitHub installation your account reaches", 422)
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    proof = await fresh_credentials(db, user_sub)
    selected = next(
        (
            item
            for item in await installations(proof["token"])
            if str(item["id"]) == installation_id
        ),
        None,
    )
    if selected is None:
        raise LinkError("Your GitHub account cannot reach this installation")
    saved = records(db, user_sub, claims.get("preferred_username"))
    row = next((r for r in saved if r.user_sub == user_sub), None)
    for duplicate in saved:
        if duplicate is not row:
            db.delete(duplicate)
    if row is None:
        row = UserGitHubInstallation(user_sub=user_sub)
    row.installation_id = installation_id
    db.add(row)
    db.commit()
    return {
        "message": "GitHub installation verified and selected",
        "installation_id": installation_id,
        "github_login": proof["login"],
        "account": (selected.get("account") or {}).get("login", ""),
    }


async def unlink(db: Session, claims: dict) -> None:
    """Forget the user's GitHub token and installation; revoke the grant."""
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    row = db.get(GitHubOAuthCredential, user_sub)
    token = None
    if row is not None and _keys():
        try:
            token = decrypt(row.credentials_ciphertext).get("access_token")
        except LinkError:
            token = None
    db.execute(
        delete(GitHubOAuthCredential).where(
            GitHubOAuthCredential.user_sub == user_sub
        )
    )
    for record in records(db, user_sub, claims.get("preferred_username")):
        db.delete(record)
    db.commit()
    if token and settings.GITHUB_OAUTH_CLIENT_ID:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.request(
                    "DELETE",
                    f"{GITHUB_API}/applications/"
                    f"{settings.GITHUB_OAUTH_CLIENT_ID}/grant",
                    auth=(
                        settings.GITHUB_OAUTH_CLIENT_ID,
                        settings.GITHUB_OAUTH_CLIENT_SECRET,
                    ),
                    headers={"Accept": "application/vnd.github+json"},
                    json={"access_token": token},
                )
        except httpx.HTTPError:
            # The local link is gone either way; the grant lapses with the
            # token's expiry.
            pass


async def importable_repositories(db: Session, claims: dict):
    """
    Repositories the user may import, proven by their GitHub token: every
    non-archived repository of each reachable CNP-Portal installation on
    which GitHub says they can push or administer.

    Returns None when the user has not linked GitHub (or linking is off, or
    the link must be renewed): the caller then offers linking.

    Raises:
        GitHubAppError: GitHub could not be reached.
    """
    # Imported here: access imports this module lazily the other way round.
    from app.services.imports.access import RepoRef

    if not is_configured():
        return None
    user_sub = await run_in_threadpool(cnp_user_id, claims)
    if db.get(GitHubOAuthCredential, user_sub) is None:
        return None
    try:
        proof = await fresh_credentials(db, user_sub)
        found: list = []
        for item in await installations(proof["token"]):
            repos = await pages(
                f"/user/installations/{item['id']}/repositories",
                proof["token"],
                "repositories",
            )
            for repo in repos:
                permissions = repo.get("permissions") or {}
                if (
                    repo.get("archived")
                    or repo.get("disabled")
                    or not (
                        permissions.get("push") is True
                        or permissions.get("admin") is True
                    )
                    or not _is_int(repo.get("id"))
                    or not isinstance(repo.get("full_name"), str)
                ):
                    continue
                found.append(
                    RepoRef(
                        full_name=repo["full_name"],
                        repo_id=repo["id"],
                        installation_id=item["id"],
                        default_branch=repo.get("default_branch") or "main",
                        private=bool(repo.get("private")),
                    )
                )
        return found
    except LinkError as exc:
        if exc.status_code == 403:
            return None
        raise github_service.GitHubAppError(str(exc)) from exc
