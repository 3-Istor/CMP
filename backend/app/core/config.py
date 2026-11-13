import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    APP_NAME: str = "CMP API"
    DEBUG: bool = True

    # Post last month's summary to the platform Discord on the 5th.
    FINOPS_MONTHLY_REPORT_ENABLED: bool = False

    # Database
    DATABASE_URL: str = "sqlite:///./arcl.db"

    # S3 Backend for Terraform State (optional - separate AWS account)
    TF_BACKEND_S3_ENABLED: bool = False
    TF_BACKEND_AWS_ACCESS_KEY_ID: str = ""
    TF_BACKEND_AWS_SECRET_ACCESS_KEY: str = ""
    TF_BACKEND_AWS_REGION: str = "eu-west-3"
    TF_BACKEND_S3_BUCKET: str = ""
    TF_BACKEND_S3_KEY_PREFIX: str = "deployments/"
    # Role assumed with a token of the Keycloak client cnp-tfstate instead of
    # the static key above (Cloud repo, terraform/public/aws/cmp-iam). Empty
    # keeps the static key: that is the rollback switch.
    TF_BACKEND_AWS_ROLE_ARN: str = ""
    TF_BACKEND_OIDC_TOKEN_URL: str = (
        "https://auth.3istor.com/realms/3istor/protocol/openid-connect/token"
    )
    TF_BACKEND_OIDC_CLIENT_ID: str = "cnp-tfstate"
    TF_BACKEND_OIDC_CLIENT_SECRET: str = ""

    # Project registry, the Git source of truth for project placement (D-01)
    CNP_REGISTRY_REPO: str = "3-Istor/cnp-projects"
    CNP_REGISTRY_BRANCH: str = "main"
    CNP_REGISTRY_PATH_PREFIX: str = "registry/projects"

    # OpenStack - loaded from environment (required for deployments)
    OS_AUTH_URL: str = ""
    OS_USERNAME: str = ""
    OS_PASSWORD: str = ""
    OS_PROJECT_NAME: str = "3-istor-cloud"
    OS_USER_DOMAIN_NAME: str = "Default"
    OS_PROJECT_DOMAIN_NAME: str = "Default"
    OS_ENDPOINT_TYPE: str = (
        "internalURL"  # Use internal endpoints for better performance
    )
    OS_IDENTITY_API_VERSION: str = "3"
    OS_REGION_NAME: str = "RegionOne"

    # AWS Credentials (for monitoring and deployments)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "eu-west-3"
    AWS_INSTANCE_TYPE: str = "t3.micro"  # Budget-safe instance type

    # User Avatars S3 Storage (self-managed S3-compatible storage)
    AVATARS_S3_ENDPOINT: str = "https://s3.3istor.com"
    AVATARS_S3_BUCKET: str = "user-avatars"
    AVATARS_S3_ACCESS_KEY_ID: str = ""
    AVATARS_S3_SECRET_ACCESS_KEY: str = ""
    AVATARS_S3_REGION: str = "eu-west-3"
    AVATARS_PUBLIC_URL_BASE: str = "https://avatars-s3.3istor.com"

    # App icons: private Garage bucket, served through the CMP only.
    # Unset keys leave the upload disabled and the built-in icons working.
    ICONS_S3_ENDPOINT: str = ""
    ICONS_S3_BUCKET: str = "cnp-app-icons"
    ICONS_S3_ACCESS_KEY_ID: str = ""
    ICONS_S3_SECRET_ACCESS_KEY: str = ""
    ICONS_S3_REGION: str = "garage"

    # Keycloak (for user profile management and project group resolution)
    KEYCLOAK_URL: str = "https://auth.3istor.com"
    KEYCLOAK_REALM: str = "3istor"
    KEYCLOAK_CLIENT_ID: str = "3-istor-openid"
    KEYCLOAK_CLIENT_SECRET: str = ""
    # Clients whose access tokens the API accepts (the token's ``azp``).
    # Comma separated, so a client switch can accept both for a while.
    # Empty means KEYCLOAK_CLIENT_ID alone.
    KEYCLOAK_ALLOWED_CLIENTS: str = ""
    # Step-up before destructive actions (app.services.step_up). Off until the
    # Keycloak flow that issues the acr is live: with it on and no such flow,
    # every destructive action would loop on the login page.
    STEP_UP_ENABLED: bool = False
    # acr of Keycloak LoA 2 (a passkey), per the clients' acr.loa.map.
    STEP_UP_ACR: str = "gold"
    # Must be at least the LoA 2 max age of the Keycloak flow (300 s).
    STEP_UP_MAX_AGE: int = 300
    # Master realm client (cmp-provisioner) for realm provisioning and
    # Terraform.
    KEYCLOAK_PROVISIONER_CLIENT_ID: str = ""
    KEYCLOAK_PROVISIONER_CLIENT_SECRET: str = ""

    # Just-in-time access (CNP-124). Durations in minutes; the max is capped
    # at 240 whatever is configured. A request nobody decides on lapses.
    ACCESS_DEFAULT_MINUTES: int = 60
    ACCESS_MAX_MINUTES: int = 60
    ACCESS_REQUEST_TTL_MINUTES: int = 120
    # Members found in an elevated group without an active grant are removed
    # and reported. Off only while migrating by hand.
    ACCESS_REMOVE_UNMANAGED_MEMBERS: bool = True

    # Periodic access review (CNP-124). Infra opens a campaign by hand; with
    # ACCESS_REVIEW_AUTO_START the CMP also opens one every
    # ACCESS_REVIEW_INTERVAL_DAYS. Undecided items are escalated at the
    # deadline, never removed.
    ACCESS_REVIEW_AUTO_START: bool = False
    ACCESS_REVIEW_INTERVAL_DAYS: int = 90
    ACCESS_REVIEW_DEADLINE_DAYS: int = 14
    # Accounts without a login for INACTIVE_AFTER_DAYS are reported, then
    # disabled (enabled=false, reversible) INACTIVE_GRACE_DAYS later, only
    # when INACTIVE_DISABLE_ENABLED. Usernames starting with one of the
    # comma-separated prefixes, or listed exactly, are never touched.
    INACTIVE_AFTER_DAYS: int = 60
    INACTIVE_GRACE_DAYS: int = 14
    INACTIVE_DISABLE_ENABLED: bool = False
    INACTIVE_EXCLUDED_PREFIXES: str = "demo-,break-glass,service-account-"
    INACTIVE_EXCLUDED_USERS: str = ""

    # Vault (for secret management in Terraform modules)
    VAULT_URL: str = ""
    # "kubernetes" logs in with the pod's ServiceAccount; anything else uses
    # the static VAULT_TOKEN.
    VAULT_AUTH: str = ""
    VAULT_KUBERNETES_MOUNT: str = "kubernetes"
    VAULT_KUBERNETES_ROLE: str = "cmp-backend"
    VAULT_KUBERNETES_JWT_PATH: str = (
        "/var/run/secrets/kubernetes.io/serviceaccount/token"
    )
    VAULT_TOKEN: str = ""

    # Cloudflare (for dynamic DNS in Terraform templates)
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ZONE_ID: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""

    # Discord Alerting (optional - for health monitoring alerts)
    DISCORD_WEBHOOK_URL: str = ""
    # Default target of security alerts; a project or an app can set its own.
    # Falls back to DISCORD_WEBHOOK_URL when empty.
    SECURITY_DISCORD_WEBHOOK_URL: str = ""
    CMP_PUBLIC_URL: str = "https://cmp.3istor.com"

    # Days the journal of changes made through the CMP is kept.
    AUDIT_RETENTION_DAYS: int = 30

    # Loki, through its authenticating gateway (Activity tab).
    LOKI_URL: str = "http://loki-gateway.observability.svc.cluster.local"
    LOKI_USERNAME: str = "cmp"
    LOKI_PASSWORD: str = ""
    LOKI_TENANT: str = "default"
    # Credential of every project Grafana org's Loki datasource.
    LOKI_GRAFANA_PROJECTS_PASSWORD: str = ""
    # Key the per-project vmauth passwords are derived from.
    METRICS_PROJECT_KEY: str = ""
    # Days of audit history read from Loki the first time the collector runs.
    ACTIVITY_BACKFILL_DAYS: int = 7
    ALERTMANAGER_URL: str = "http://alertmanager.observability.svc:9093"

    # FinOps: "simulated" keeps the demo engine, "real" reads cost_line_items.
    FINOPS_PROVIDER: str = "simulated"
    FINOPS_INGEST_ENABLED: bool = False
    FINOPS_INGEST_INTERVAL_SECONDS: int = 3600
    # Days replayed on every run: OpenCost and billing exports revise them.
    FINOPS_REPLAY_DAYS: int = 3
    # First day ingested: OpenCost needs its own price metrics in
    # VictoriaMetrics, which start the day it is installed.
    FINOPS_INGEST_SINCE: str = "2026-10-27"
    # Versioned prices (K3s repo finops/), mounted from a ConfigMap.
    FINOPS_DATA_DIR: str = "/etc/cnp-finops"
    OPENCOST_URL: str = "http://opencost.opencost.svc.cluster.local:9003"
    # Cloud clusters' OpenCost over the VPN, "aws=http://...,gcp=http://...".
    # Empty until the cloud activation runbook is done.
    FINOPS_OPENCOST_CLUSTERS: str = ""
    # Namespaces that predate CMP projects and carry no project label: each is
    # its own project. Any other unlabelled namespace is platform cost.
    FINOPS_LEGACY_NAMESPACES: str = (
        "qcm,mepa-app,roadmap,linmap-bot,3istor-sessions,dev-sandbox,demo-app"
    )
    # Target capacity (10 projects x 2-3 apps): the cost of the platform
    # namespaces and idle capacity is divided by it into a fixed fee per app.
    FINOPS_PLATFORM_APP_SLOTS: int = 25
    # Token metrics of the AI gateway (OpenTelemetry GenAI conventions).
    FINOPS_AI_TOKEN_METRIC: str = "gen_ai_client_token_usage_sum"
    # AWS Data Exports "FOCUS 1.2 with AWS columns" (Parquet). Empty bucket
    # disables the source. The prefix is the export root, i.e. the folder that
    # holds metadata/ and data/ (<prefix>/<export-name>). Credentials come
    # from the standard AWS_* environment variables.
    FINOPS_AWS_EXPORT_BUCKET: str = ""
    FINOPS_AWS_EXPORT_PREFIX: str = ""
    FINOPS_AWS_REGION: str = "eu-west-3"
    # Role assumed with the Keycloak token instead of the AWS_* keys.
    FINOPS_AWS_ROLE_ARN: str = ""
    # BigQuery billing export. The FOCUS view wins over the detailed table
    # when both are set; neither set disables the source. Tables are fully
    # qualified: project.dataset.table.
    FINOPS_GCP_FOCUS_VIEW: str = ""
    FINOPS_GCP_BILLING_TABLE: str = ""
    # Project the query jobs run (and are billed) in.
    FINOPS_GCP_PROJECT: str = ""
    # Service account key; falls back to GOOGLE_APPLICATION_CREDENTIALS.
    FINOPS_GCP_CREDENTIALS_FILE: str = ""
    # Workload Identity Federation from Keycloak, preferred over the key file:
    # //iam.googleapis.com/projects/<n>/locations/global/workloadIdentityPools/
    # cnp-finops/providers/keycloak, and the reader service account email.
    FINOPS_GCP_WIF_AUDIENCE: str = ""
    FINOPS_GCP_SERVICE_ACCOUNT: str = ""
    # Keycloak client cnp-finops, whose token both clouds trust.
    FINOPS_OIDC_TOKEN_URL: str = (
        "https://auth.3istor.com/realms/3istor/protocol/openid-connect/token"
    )
    FINOPS_OIDC_CLIENT_ID: str = "cnp-finops"
    FINOPS_OIDC_CLIENT_SECRET: str = ""
    # A day is an anomaly when an app costs more than the median of its
    # previous 14 days by this share AND by this many euros: on-prem apps
    # cost cents a day, where a relative jump alone is noise.
    FINOPS_ANOMALY_PCT: float = 0.5
    FINOPS_ANOMALY_MIN_EUR: float = 0.05

    # GitHub App Integration (for Kubernetes GitOps provisioning)
    GITHUB_APP_PRIVATE_KEY: str = ""  # PEM format RSA private key
    # Installation ID of the GitHub App on the 3-Istor org, used to mint
    # installation tokens for org-level Terraform operations (e.g. project bootstrap)
    GITHUB_INSTALLATION_ID: str = ""
    # Owner of every app repository; never taken from a deployment request.
    GITHUB_ORG: str = "3-Istor"

    # GitHub Registry (GHCR) Token for pulling private container images
    # Personal Access Token (PAT) with read:packages scope
    GITHUB_REGISTRY_USERNAME: str = ""
    GITHUB_REGISTRY_TOKEN: str = ""

    GRAFANA_ADMIN_PASSWORD: str = ""

    # AI assistants (MCP server on /mcp, OAuth served by the CMP itself)
    AGENT_ACCESS_TOKEN_TTL_SECONDS: int = 900
    AGENT_REFRESH_TTL_DAYS: int = 30
    # Redirect URIs an assistant may register, comma-separated fnmatch
    # patterns. Loopback URIs (Claude Code, Codex, OpenCode) are always
    # allowed.
    AGENT_REDIRECT_URI_PATTERNS: str = (
        "https://claude.ai/api/mcp/auth_callback,"
        "https://claude.com/api/mcp/auth_callback,"
        "https://chatgpt.com/connector_platform_oauth_redirect,"
        "https://chatgpt.com/connector/oauth/*,"
        "https://vscode.dev/redirect,"
        "cursor://anysphere.cursor-mcp/*,"
        "vscode://*"
    )
    # Repository holding the skills the MCP server shares with assistants.
    AGENT_KIT_REPO: str = "3-Istor/cnp-agent-kit"
    AGENT_KIT_BRANCH: str = "main"
    AGENT_KIT_SKILLS_PATH: str = "plugins/cnp/skills"


settings = Settings()

# Ensure OpenStack environment variables are set in os.environ
# openstacksdk reads directly from os.environ, not from Pydantic settings
if settings.OS_ENDPOINT_TYPE:
    os.environ["OS_ENDPOINT_TYPE"] = settings.OS_ENDPOINT_TYPE
if settings.OS_IDENTITY_API_VERSION:
    os.environ["OS_IDENTITY_API_VERSION"] = settings.OS_IDENTITY_API_VERSION
if settings.OS_REGION_NAME:
    os.environ["OS_REGION_NAME"] = settings.OS_REGION_NAME
