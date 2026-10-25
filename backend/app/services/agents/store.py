"""
OAuth state of the assistants connected through the MCP server.

Tokens are opaque random strings; only their SHA-256 is stored, so a leaked
database does not leak usable tokens, and revoking a grant kills its tokens
on the next call without any key to rotate.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from urllib.parse import urlparse

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.agent import (
    AgentAccessToken,
    AgentAuthCode,
    AgentAuthRequest,
    AgentClient,
    AgentGrant,
)

ACCESS_PREFIX = "cnp_at_"
REFRESH_PREFIX = "cnp_rt_"
CODE_PREFIX = "cnp_ac_"

READ_SCOPE = "cnp:read"
WRITE_SCOPE = "cnp:write"
SCOPES = [READ_SCOPE, WRITE_SCOPE]

AUTH_REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)

# Identity claims copied from the approving user's Keycloak token. Roles are
# left out on purpose: project roles are resolved live from Keycloak.
IDENTITY_CLAIMS = (
    "sub",
    "preferred_username",
    "name",
    "given_name",
    "family_name",
    "email",
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite hands back naive datetimes even for timezone=True columns.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def redirect_uri_allowed(uri: str) -> bool:
    """
    Loopback redirects are always allowed (RFC 8252: native apps such as
    Claude Code or Codex pick a random port). Anything else must match one
    of the configured patterns, so a random site cannot register itself
    and phish a consent.
    """
    parsed = urlparse(uri)
    if parsed.scheme == "http" and parsed.hostname in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        return True
    patterns = [
        p.strip()
        for p in settings.AGENT_REDIRECT_URI_PATTERNS.split(",")
        if p.strip()
    ]
    return any(fnmatch(uri, pattern) for pattern in patterns)


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def save_client(client_id: str, client_name: str, info: dict) -> None:
    with SessionLocal() as db:
        db.merge(
            AgentClient(
                client_id=client_id, client_name=client_name, info=info
            )
        )
        db.commit()


def load_client(client_id: str) -> dict | None:
    with SessionLocal() as db:
        row = db.get(AgentClient, client_id)
        return dict(row.info) if row else None


# ---------------------------------------------------------------------------
# Authorization requests and codes
# ---------------------------------------------------------------------------


def save_auth_request(client_id: str, params: dict) -> str:
    request_id = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        db.execute(
            delete(AgentAuthRequest).where(AgentAuthRequest.expires_at < now())
        )
        db.add(
            AgentAuthRequest(
                id=request_id,
                client_id=client_id,
                params=params,
                expires_at=now() + AUTH_REQUEST_TTL,
            )
        )
        db.commit()
    return request_id


@dataclass
class PendingRequest:
    id: str
    client_id: str
    client_name: str
    params: dict


def load_auth_request(request_id: str) -> PendingRequest | None:
    with SessionLocal() as db:
        row = db.get(AgentAuthRequest, request_id)
        if row is None or _aware(row.expires_at) < now():
            return None
        client = db.get(AgentClient, row.client_id)
        return PendingRequest(
            id=row.id,
            client_id=row.client_id,
            client_name=client.client_name if client else row.client_id,
            params=dict(row.params),
        )


def consume_auth_request(request_id: str) -> None:
    with SessionLocal() as db:
        db.execute(
            delete(AgentAuthRequest).where(AgentAuthRequest.id == request_id)
        )
        db.commit()


def save_auth_code(
    request: PendingRequest, claims: dict, scopes: list[str]
) -> str:
    code = new_token(CODE_PREFIX)
    params = request.params
    with SessionLocal() as db:
        db.execute(
            delete(AgentAuthCode).where(AgentAuthCode.expires_at < now())
        )
        db.add(
            AgentAuthCode(
                code_hash=digest(code),
                client_id=request.client_id,
                claims={k: claims[k] for k in IDENTITY_CLAIMS if k in claims},
                scopes=scopes,
                code_challenge=params["code_challenge"],
                redirect_uri=params["redirect_uri"],
                redirect_uri_provided_explicitly=int(
                    params["redirect_uri_provided_explicitly"]
                ),
                resource=params.get("resource"),
                expires_at=now() + CODE_TTL,
            )
        )
        db.execute(
            delete(AgentAuthRequest).where(AgentAuthRequest.id == request.id)
        )
        db.commit()
    return code


@dataclass
class StoredCode:
    client_id: str
    claims: dict
    scopes: list[str]
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    resource: str | None
    expires_at: datetime


def load_auth_code(code: str) -> StoredCode | None:
    with SessionLocal() as db:
        row = db.get(AgentAuthCode, digest(code))
        if row is None or _aware(row.expires_at) < now():
            return None
        return StoredCode(
            client_id=row.client_id,
            claims=dict(row.claims),
            scopes=list(row.scopes),
            code_challenge=row.code_challenge,
            redirect_uri=row.redirect_uri,
            redirect_uri_provided_explicitly=bool(
                row.redirect_uri_provided_explicitly
            ),
            resource=row.resource,
            expires_at=_aware(row.expires_at),
        )


# ---------------------------------------------------------------------------
# Grants and tokens
# ---------------------------------------------------------------------------


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: list[str]


def _issue_access_token(db, grant: AgentGrant) -> str:
    token = new_token(ACCESS_PREFIX)
    db.execute(
        delete(AgentAccessToken).where(AgentAccessToken.expires_at < now())
    )
    db.add(
        AgentAccessToken(
            token_hash=digest(token),
            grant_id=grant.id,
            expires_at=now()
            + timedelta(seconds=settings.AGENT_ACCESS_TOKEN_TTL_SECONDS),
        )
    )
    return token


def exchange_code(code: str, client_id: str) -> IssuedTokens | None:
    """Single use: the code row is deleted in the same transaction."""
    with SessionLocal() as db:
        row = db.get(AgentAuthCode, digest(code))
        if (
            row is None
            or row.client_id != client_id
            or _aware(row.expires_at) < now()
        ):
            return None
        client = db.get(AgentClient, client_id)
        refresh = new_token(REFRESH_PREFIX)
        grant = AgentGrant(
            client_id=client_id,
            client_name=client.client_name if client else client_id,
            subject=row.claims["sub"],
            username=row.claims.get("preferred_username", ""),
            claims=dict(row.claims),
            scopes=list(row.scopes),
            refresh_token_hash=digest(refresh),
            last_used_at=now(),
            expires_at=now() + timedelta(days=settings.AGENT_REFRESH_TTL_DAYS),
        )
        db.add(grant)
        db.delete(row)
        db.flush()
        access = _issue_access_token(db, grant)
        db.commit()
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.AGENT_ACCESS_TOKEN_TTL_SECONDS,
            scopes=list(grant.scopes),
        )


def _active_grant_by_refresh(db, refresh_token: str) -> AgentGrant | None:
    grant = db.scalars(
        select(AgentGrant).where(
            AgentGrant.refresh_token_hash == digest(refresh_token)
        )
    ).first()
    if (
        grant is None
        or grant.revoked_at is not None
        or _aware(grant.expires_at) < now()
    ):
        return None
    return grant


@dataclass
class GrantView:
    id: int
    client_id: str
    client_name: str
    subject: str
    username: str
    claims: dict
    scopes: list[str]
    expires_at: datetime


def _view(grant: AgentGrant) -> GrantView:
    return GrantView(
        id=grant.id,
        client_id=grant.client_id,
        client_name=grant.client_name,
        subject=grant.subject,
        username=grant.username,
        claims=dict(grant.claims),
        scopes=list(grant.scopes),
        expires_at=_aware(grant.expires_at),
    )


def load_refresh(refresh_token: str) -> GrantView | None:
    with SessionLocal() as db:
        grant = _active_grant_by_refresh(db, refresh_token)
        return _view(grant) if grant else None


def rotate_refresh(
    refresh_token: str, client_id: str, scopes: list[str]
) -> IssuedTokens | None:
    """
    Rotation: the old refresh token stops working as soon as the new one is
    issued, so a stolen one is only good until its owner refreshes. The
    sliding expiry keeps an assistant in daily use connected.
    """
    with SessionLocal() as db:
        grant = _active_grant_by_refresh(db, refresh_token)
        if grant is None or grant.client_id != client_id:
            return None
        if scopes:
            grant.scopes = [s for s in grant.scopes if s in scopes]
        refresh = new_token(REFRESH_PREFIX)
        grant.refresh_token_hash = digest(refresh)
        grant.last_used_at = now()
        grant.expires_at = now() + timedelta(
            days=settings.AGENT_REFRESH_TTL_DAYS
        )
        access = _issue_access_token(db, grant)
        db.commit()
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.AGENT_ACCESS_TOKEN_TTL_SECONDS,
            scopes=list(grant.scopes),
        )


@dataclass
class AccessView:
    grant: GrantView
    expires_at: datetime


def load_access(token: str) -> AccessView | None:
    if not token.startswith(ACCESS_PREFIX):
        return None
    with SessionLocal() as db:
        row = db.get(AgentAccessToken, digest(token))
        if row is None or _aware(row.expires_at) < now():
            return None
        grant = db.get(AgentGrant, row.grant_id)
        if grant is None or grant.revoked_at is not None:
            return None
        return AccessView(
            grant=_view(grant), expires_at=_aware(row.expires_at)
        )


def touch_grant(grant_id: int) -> None:
    with SessionLocal() as db:
        grant = db.get(AgentGrant, grant_id)
        if grant is not None:
            grant.last_used_at = now()
            db.commit()


def revoke_by_token(token: str) -> None:
    with SessionLocal() as db:
        grant = None
        if token.startswith(ACCESS_PREFIX):
            row = db.get(AgentAccessToken, digest(token))
            grant = db.get(AgentGrant, row.grant_id) if row else None
        elif token.startswith(REFRESH_PREFIX):
            grant = _active_grant_by_refresh(db, token)
        if grant is not None:
            _revoke(db, grant)
            db.commit()


def _revoke(db, grant: AgentGrant) -> None:
    grant.revoked_at = now()
    grant.refresh_token_hash = None
    db.execute(
        delete(AgentAccessToken).where(AgentAccessToken.grant_id == grant.id)
    )


def revoke_grant(grant_id: int, username: str) -> bool:
    """Revoke one of ``username``'s own grants; False when it is not theirs."""
    with SessionLocal() as db:
        grant = db.get(AgentGrant, grant_id)
        if grant is None or grant.username != username:
            return False
        if grant.revoked_at is None:
            _revoke(db, grant)
            db.commit()
        return True


@dataclass
class GrantSummary:
    id: int
    client_name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime


def list_grants(username: str) -> list[GrantSummary]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(AgentGrant)
            .where(
                AgentGrant.username == username,
                AgentGrant.revoked_at.is_(None),
                AgentGrant.expires_at > now(),
            )
            .order_by(AgentGrant.last_used_at.desc())
        ).all()
        return [
            GrantSummary(
                id=g.id,
                client_name=g.client_name,
                scopes=list(g.scopes),
                created_at=_aware(g.created_at),
                last_used_at=(
                    _aware(g.last_used_at) if g.last_used_at else None
                ),
                expires_at=_aware(g.expires_at),
            )
            for g in rows
        ]


def token_claims(token: str) -> dict | None:
    """
    The claims the rest of the CMP sees for an assistant's token: the user's
    identity, plus who acts for them. ``None`` when the token is unknown.
    """
    access = load_access(token)
    if access is None:
        return None
    grant = access.grant
    return {
        **grant.claims,
        "azp": grant.client_name,
        "exp": int(access.expires_at.timestamp()),
        "cnp_agent": {
            "grant_id": grant.id,
            "client": grant.client_name,
            "scopes": grant.scopes,
        },
    }
