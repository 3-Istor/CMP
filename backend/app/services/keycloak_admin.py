"""
The CMP's identity in the Keycloak master realm.

The ``cmp-provisioner`` confidential client (client credentials grant) is used
when configured; the master admin user (password grant on ``admin-cli``) is the
fallback until it is removed.
"""

from app.core.config import settings


def uses_provisioner() -> bool:
    return bool(
        settings.KEYCLOAK_PROVISIONER_CLIENT_ID
        and settings.KEYCLOAK_PROVISIONER_CLIENT_SECRET
    )


def is_configured() -> bool:
    return uses_provisioner() or bool(
        settings.KEYCLOAK_ADMIN_USERNAME and settings.KEYCLOAK_ADMIN_PASSWORD
    )


def describe() -> str:
    if uses_provisioner():
        return (
            f"client '{settings.KEYCLOAK_PROVISIONER_CLIENT_ID}' "
            "(client credentials)"
        )
    if is_configured():
        return f"admin user '{settings.KEYCLOAK_ADMIN_USERNAME}' (admin-cli)"
    return "not configured"


def master_token_url() -> str:
    return (
        f"{settings.KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    )


def master_token_form() -> dict[str, str]:
    if uses_provisioner():
        return {
            "grant_type": "client_credentials",
            "client_id": settings.KEYCLOAK_PROVISIONER_CLIENT_ID,
            "client_secret": settings.KEYCLOAK_PROVISIONER_CLIENT_SECRET,
        }
    return {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": settings.KEYCLOAK_ADMIN_USERNAME,
        "password": settings.KEYCLOAK_ADMIN_PASSWORD,
    }


def terraform_env() -> dict[str, str]:
    """
    Provider credentials for the app-templates modules.

    The admin password is not handed to Terraform once the client is set: the
    modules pick the client credentials whenever a secret is given.
    """
    env: dict[str, str] = {}
    if settings.KEYCLOAK_URL:
        env["TF_VAR_keycloak_url"] = settings.KEYCLOAK_URL
    if uses_provisioner():
        env["TF_VAR_keycloak_client_id"] = (
            settings.KEYCLOAK_PROVISIONER_CLIENT_ID
        )
        env["TF_VAR_keycloak_client_secret"] = (
            settings.KEYCLOAK_PROVISIONER_CLIENT_SECRET
        )
        return env
    if settings.KEYCLOAK_ADMIN_USERNAME:
        env["TF_VAR_keycloak_admin_username"] = (
            settings.KEYCLOAK_ADMIN_USERNAME
        )
    if settings.KEYCLOAK_ADMIN_PASSWORD:
        env["TF_VAR_keycloak_admin_password"] = (
            settings.KEYCLOAK_ADMIN_PASSWORD
        )
    return env
