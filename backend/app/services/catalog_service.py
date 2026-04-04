from app.schemas.catalog import CatalogField, CatalogTemplate

# Static catalog of deployable app templates.
# Each template defines what VMs will be provisioned (2 OpenStack + 2 AWS).
CATALOG: list[CatalogTemplate] = [
    CatalogTemplate(
        id="wordpress",
        name="WordPress",
        description="WordPress CMS with MySQL on OpenStack and Nginx on AWS ASG.",
        icon="📝",
        category="CMS",
        fields=[
            CatalogField(
                name="db_password",
                label="Database Password",
                type="text",
                default="changeme",
            ),
            CatalogField(
                name="wp_admin_email",
                label="Admin Email",
                type="text",
                default="admin@example.com",
            ),
        ],
    ),
    CatalogTemplate(
        id="nextcloud",
        name="Nextcloud",
        description="Self-hosted file storage with DB on OpenStack, app on AWS.",
        icon="☁️",
        category="Storage",
        fields=[
            CatalogField(
                name="admin_password",
                label="Admin Password",
                type="text",
                default="changeme",
            ),
            CatalogField(
                name="storage_gb",
                label="Storage (GB)",
                type="number",
                default=50,
            ),
        ],
    ),
    CatalogTemplate(
        id="gitlab",
        name="GitLab CE",
        description="Self-hosted Git with DB on OpenStack, web on AWS ASG.",
        icon="🦊",
        category="DevOps",
        fields=[
            CatalogField(
                name="root_password",
                label="Root Password",
                type="text",
                default="changeme",
            ),
            CatalogField(
                name="external_url",
                label="External URL",
                type="text",
                default="http://gitlab.example.com",
            ),
        ],
    ),
    CatalogTemplate(
        id="grafana",
        name="Grafana + Prometheus",
        description="Monitoring stack with Prometheus on OpenStack, Grafana on AWS.",
        icon="📊",
        category="Monitoring",
        fields=[
            CatalogField(
                name="admin_password",
                label="Grafana Admin Password",
                type="text",
                default="changeme",
            ),
        ],
    ),
]


def get_all_templates() -> list[CatalogTemplate]:
    return CATALOG


def get_template_by_id(template_id: str) -> CatalogTemplate | None:
    return next((t for t in CATALOG if t.id == template_id), None)
