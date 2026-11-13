"""
GitHub Actions OIDC tokens of the central import workflow (D10, D14).

Only one workflow may call the worker API: import.yml of the analyzer
repository, on its main branch, at a commit listed in IMPORT_WORKFLOW_SHAS,
started by workflow_dispatch. The repository is also pinned by its numeric
ids, which a deleted and recreated repository of the same name does not keep.
"""

from functools import lru_cache

import jwt

from app.core.config import settings

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"


class WorkerTokenError(Exception):
    """The token is not one of the import workflow's. Never says which check."""


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        JWKS_URL, timeout=10, headers={"User-Agent": "cnp-cmp-import"}
    )


def _signing_key(token: str):
    return _jwks_client().get_signing_key_from_jwt(token).key


def allowed_workflow_shas() -> frozenset[str]:
    return frozenset(
        sha.strip().lower()
        for sha in settings.IMPORT_WORKFLOW_SHAS.split(",")
        if sha.strip()
    )


def expected_workflow_ref() -> str:
    return (
        f"{settings.IMPORT_WORKFLOW_REPO}/.github/workflows/"
        f"{settings.IMPORT_WORKFLOW_FILE}@{settings.IMPORT_WORKFLOW_REF}"
    )


def check_claims(claims: dict) -> None:
    """
    Raises:
        WorkerTokenError: If any claim differs from the pinned workflow.
    """
    shas = allowed_workflow_shas()
    expected = {
        "repository": settings.IMPORT_WORKFLOW_REPO,
        "repository_id": settings.IMPORT_WORKFLOW_REPO_ID,
        "repository_owner_id": settings.IMPORT_WORKFLOW_OWNER_ID,
        "job_workflow_ref": expected_workflow_ref(),
        "workflow_ref": expected_workflow_ref(),
        "ref": settings.IMPORT_WORKFLOW_REF,
        "event_name": "workflow_dispatch",
    }
    # Unset pins fail closed: an empty setting never matches a claim.
    for claim, value in expected.items():
        if not value or str(claims.get(claim, "")) != value:
            raise WorkerTokenError(f"claim {claim}")
    for claim in ("job_workflow_sha", "workflow_sha"):
        if str(claims.get(claim, "")).lower() not in shas:
            raise WorkerTokenError(f"claim {claim}")
    if not str(claims.get("run_id", "")).isdigit():
        raise WorkerTokenError("claim run_id")


def verify_worker_token(token: str) -> dict:
    """
    The verified claims of a GitHub Actions OIDC token of import.yml.

    Raises:
        WorkerTokenError: Bad signature, issuer, audience, expiry or claims.
    """
    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=settings.IMPORT_OIDC_AUDIENCE,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=30,
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise WorkerTokenError("signature or registered claims") from exc
    check_claims(claims)
    return claims
