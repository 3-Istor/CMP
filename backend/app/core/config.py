from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    APP_NAME: str = "ARCL CMP API"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "sqlite:///./arcl.db"

    # AWS — loaded from environment, never hardcoded
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "eu-west-3"
    # Budget constraint: only t3.micro / t4g.nano allowed
    AWS_INSTANCE_TYPE: str = "t3.micro"
    AWS_VPC_CIDR: str = "10.1.0.0/16"
    AWS_SUBNET_CIDR: str = "10.1.1.0/24"

    # OpenStack — loaded from environment
    OS_AUTH_URL: str = ""
    OS_USERNAME: str = ""
    OS_PASSWORD: str = ""
    OS_PROJECT_NAME: str = "3-istor-cloud"
    OS_USER_DOMAIN_NAME: str = "Default"
    OS_PROJECT_DOMAIN_NAME: str = "Default"
    # Network CIDRs (DO NOT CHANGE — see ARCL_AI_CONTEXT.md)
    OS_EXTERNAL_NETWORK: str = "192.168.1.0/24"
    OS_INTERNAL_NETWORK: str = "172.16.0.0/24"
    VPN_NETWORK: str = "10.0.0.0/24"


settings = Settings()
