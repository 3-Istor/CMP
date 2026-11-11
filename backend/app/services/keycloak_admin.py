"""
The CMP's identity in the Keycloak master realm: the ``cmp-provisioner``
confidential client, client credentials grant. Never the master admin user.
"""

from app.core.config import settings


def is_configured() -> bool:
    return bool(
        settings.KEYCLOAK_PROVISIONER_CLIENT_ID
        and settings.KEYCLOAK_PROVISIONER_CLIENT_SECRET
    )


def describe() -> str:
    if is_configured():
        return (
            f"client '{settings.KEYCLOAK_PROVISIONER_CLIENT_ID}' "
            "(client credentials)"
        )
    return "not configured"


def master_token_url() -> str:
    return (
        f"{settings.KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    )


def master_token_form() -> dict[str, str]:
    return {
        "grant_type": "client_credentials",
        "client_id": settings.KEYCLOAK_PROVISIONER_CLIENT_ID,
        "client_secret": settings.KEYCLOAK_PROVISIONER_CLIENT_SECRET,
    }


def terraform_env() -> dict[str, str]:
    """
    Provider credentials for the app-templates modules, which pick the client
    credentials whenever a secret is given.
    """
    env: dict[str, str] = {}
    if settings.KEYCLOAK_URL:
        env["TF_VAR_keycloak_url"] = settings.KEYCLOAK_URL
    if is_configured():
        env["TF_VAR_keycloak_client_id"] = (
            settings.KEYCLOAK_PROVISIONER_CLIENT_ID
        )
        env["TF_VAR_keycloak_client_secret"] = (
            settings.KEYCLOAK_PROVISIONER_CLIENT_SECRET
        )
    return env
