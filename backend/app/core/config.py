import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    APP_NAME: str = "CMP API"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "sqlite:///./arcl.db"

    # S3 Backend for Terraform State (optional - separate AWS account)
    TF_BACKEND_S3_ENABLED: bool = False
    TF_BACKEND_AWS_ACCESS_KEY_ID: str = ""
    TF_BACKEND_AWS_SECRET_ACCESS_KEY: str = ""
    TF_BACKEND_AWS_REGION: str = "eu-west-3"
    TF_BACKEND_S3_BUCKET: str = ""
    TF_BACKEND_S3_KEY_PREFIX: str = "deployments/"
    TF_BACKEND_S3_DYNAMODB_TABLE: str = (
        ""  # Optional: leave empty to disable locking
    )

    # OpenStack - loaded from environment (required for deployments)
    OS_AUTH_URL: str = ""
    OS_USERNAME: str = ""
    OS_PASSWORD: str = ""
    OS_PROJECT_NAME: str = "3-istor-cloud"
    OS_USER_DOMAIN_NAME: str = "Default"
    OS_PROJECT_DOMAIN_NAME: str = "Default"
    OS_ENDPOINT_TYPE: str = "internalURL"  # Use internal endpoints for better performance
    OS_IDENTITY_API_VERSION: str = "3"
    OS_REGION_NAME: str = "RegionOne"

    # AWS Credentials (for monitoring and deployments)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "eu-west-3"

    # User Avatars S3 Storage (self-managed S3-compatible storage)
    AVATARS_S3_ENDPOINT: str = "https://s3.3istor.com"
    AVATARS_S3_BUCKET: str = "user-avatars"
    AVATARS_S3_ACCESS_KEY_ID: str = ""
    AVATARS_S3_SECRET_ACCESS_KEY: str = ""
    AVATARS_S3_REGION: str = "eu-west-3"
    AVATARS_PUBLIC_URL_BASE: str = "https://avatars-s3.3istor.com"

    # Keycloak (for user profile management)
    KEYCLOAK_URL: str = "https://auth.3istor.com"
    KEYCLOAK_CLIENT_ID: str = "3-istor-openid"
    KEYCLOAK_CLIENT_SECRET: str = ""

    # Cloudflare (for dynamic DNS in Terraform templates)
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ZONE_ID: str = ""

    # Discord Alerting (optional - for health monitoring alerts)
    DISCORD_WEBHOOK_URL: str = ""


settings = Settings()

# Ensure OpenStack environment variables are set in os.environ
# openstacksdk reads directly from os.environ, not from Pydantic settings
if settings.OS_ENDPOINT_TYPE:
    os.environ["OS_ENDPOINT_TYPE"] = settings.OS_ENDPOINT_TYPE
if settings.OS_IDENTITY_API_VERSION:
    os.environ["OS_IDENTITY_API_VERSION"] = settings.OS_IDENTITY_API_VERSION
if settings.OS_REGION_NAME:
    os.environ["OS_REGION_NAME"] = settings.OS_REGION_NAME
