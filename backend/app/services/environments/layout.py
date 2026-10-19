"""Names and files of an app's environments. Must match K3s and cnp-project-base."""

from typing import Any

ENVIRONMENTS = ("prod", "staging")
SUFFIX = {"prod": "", "staging": "-stg"}
HOSTNAME_DOMAIN = "3istor.com"

# offhours-guard crons: asleep from 19:00 on weekdays until 08:00, and all weekend.
DEFAULT_STAGING_OFFHOURS: dict[str, Any] = {
    "enabled": True,
    "sleepAt": "0 19 * * 1-5",
    "wakeAt": "0 8 * * 1-5",
    "timezone": "Europe/Paris",
}


def components(app_type: str) -> list[str]:
    return ["frontend", "backend"] if app_type == "fullstack" else ["app"]


def base_values_file(app_type: str, component: str) -> str:
    if app_type == "fullstack":
        return f"deploy/values-{component}.yaml"
    return "deploy/values.yaml"


def env_values_file(environment: str, component: str) -> str:
    return f"deploy/env/{environment}/{component}.yaml"


def namespace(project: str, app: str, environment: str) -> str:
    return f"{project}-{app}{SUFFIX[environment]}"


def application(
    project: str, app: str, component: str, environment: str
) -> str:
    """The Argo CD Application, which is also the Helm release name."""
    return f"{project}-{app}-{component}{SUFFIX[environment]}"


def staging_hostname(project: str, app: str) -> str:
    return f"{app}-{project}-staging.{HOSTNAME_DOMAIN}"


def image_of(values: dict[str, Any]) -> dict[str, str]:
    image = values.get("image") or {}
    return {
        "repository": str(image.get("repository") or ""),
        "tag": str(image.get("tag") or ""),
    }


def pinned(repository: str, tag: str, digest: str) -> dict[str, str]:
    """image values that name a version for people and a digest for the runtime."""
    return {
        "repository": repository,
        "tag": f"{tag}@{digest}" if tag else digest,
    }


def chart_fullname(project: str, app: str, component: str) -> str:
    """The prod object names the chart derives (cnp-generic-app.fullname)."""
    return f"{application(project, app, component, 'prod')}-cnp-generic-app"[
        :63
    ].rstrip("-")


def staging_values(
    image: dict[str, str],
    project: str,
    app: str,
    component: str,
    hostname: str,
    vault_path: str,
    vault_role: str,
    oidc_client_id: str,
    offhours: dict[str, Any],
    exposed: bool,
    has_auth: bool,
) -> dict[str, Any]:
    """
    The staging values file of one component, written when staging is turned on.

    Everything that must not be shared with prod is set here, whatever the
    shared file says: secrets, identity, hostname, backups.
    """
    values: dict[str, Any] = {
        "environment": "staging",
        # The prod names, in the staging namespace: the frontend reaches its
        # backend by the Service's short name, which would not exist otherwise.
        "fullnameOverride": chart_fullname(project, app, component),
        "image": dict(image),
        "replicaCount": 1,
        "secrets": {"vaultPath": vault_path, "vaultRole": vault_role},
        "db": {"instances": 1, "backup": {"enabled": False}},
        "offhours": dict(offhours),
    }
    if exposed:
        values["ingress"] = {
            "hostname": hostname,
            "oidc": {"clientID": oidc_client_id},
        }
    if has_auth:
        values["auth"] = {"clientId": oidc_client_id}
    return values


def version_of(tag: str) -> str:
    """1.4.0-rc.2 from 1.4.0-rc.2@sha256:..., the tag alone otherwise."""
    return tag.split("@", 1)[0]
