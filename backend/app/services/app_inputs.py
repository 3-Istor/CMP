"""
What a deployment request may put in ``app_config``, and the Terraform
variables the CMP builds from it.

Terraform runs with the platform's Vault token and Keycloak provisioner secret
in its environment, and ``-var`` beats ``TF_VAR_*``. A caller who could name
any variable could point vault_url or keycloak_url at their own endpoint, or
keycloak_realm_id at another project's realm. So only the variables the
template manifest lists are accepted, each value is checked against its type,
and the platform inputs are set by the CMP after the caller's.
"""

import json
import logging
import re
from typing import Any

from app.core.config import settings
from app.models.project import TargetCloud

logger = logging.getLogger(__name__)

GITHUB_INSTALLATION_ID = "github_installation_id"
# The k3s-gitops-app module derives app_type from it the same way.
FULLSTACK_TEMPLATE_REPO = "template-app-webapp-python-fastapi-react"

# Listed in a manifest for the form, but the CMP decides the value. Sent back
# unchanged by the portal and the MCP, so an equal value is accepted.
_PLATFORM_FIELDS = frozenset({"github_owner"})

# Values end up in shell commands of the templates (local-exec) and in
# resource names: no quotes, spaces, `$` or other shell metacharacters.
_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$")
_DIGITS = re.compile(r"^[0-9]{1,20}$")


class InvalidAppConfig(ValueError):
    """The request's app_config holds keys or values the CMP refuses."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _value_problem(field: dict[str, Any], value: Any) -> str | None:
    name = field["name"]
    kind = field.get("type") or "text"
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return f"{name} must be a string or a number."
    options = field.get("options") or []
    if kind == "select":
        valid = value in options
        problem = f"{name} must be one of {options}."
    elif kind == "number":
        valid = bool(_DIGITS.match(str(value)))
        problem = f"{name} must be a whole number."
    else:
        valid = bool(_TEXT.match(str(value)))
        problem = (
            f"{name} may hold letters, digits and . _ / : @ - only, "
            "up to 200 characters."
        )
    return None if valid else problem


def _user_fields(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in fields if f["name"] not in _PLATFORM_FIELDS}


def platform_values(fields: list[dict[str, Any]]) -> dict[str, str]:
    """The manifest fields the CMP sets itself, with their values."""
    declared = {f["name"] for f in fields}
    values = {"github_owner": settings.GITHUB_ORG}
    return {k: v for k, v in values.items() if k in declared}


def validate_request(
    fields: list[dict[str, Any]],
    app_config: dict[str, Any],
    project_id: str | None,
) -> dict[str, Any]:
    """
    Check a create request's app_config against the template's manifest
    fields and return what may be stored: the user fields (with their
    defaults) and the GitHub installation ID.

    ``project_name`` is accepted when it names the request's project, and a
    platform field when it holds the platform's value: the portal sends both.

    Raises:
        InvalidAppConfig: A key is not settable or a value is malformed.
    """
    user_fields = _user_fields(fields)
    platform = platform_values(fields)
    problems: list[str] = []
    accepted: dict[str, Any] = {}
    for key, value in app_config.items():
        if key == "project_name":
            if value != project_id:
                problems.append("project_name must match project_id.")
        elif key in platform:
            if value != platform[key]:
                problems.append(f"{key} is set by the platform.")
        elif key == GITHUB_INSTALLATION_ID:
            if isinstance(value, bool) or not _DIGITS.match(str(value)):
                problems.append(f"{key} must be a number.")
            else:
                accepted[key] = str(value)
        elif key in user_fields:
            if value == "" and not user_fields[key].get("required"):
                continue
            problem = _value_problem(user_fields[key], value)
            if problem:
                problems.append(problem)
            else:
                accepted[key] = value
        else:
            problems.append(f"{key} cannot be set.")
    for name, field in user_fields.items():
        if name in accepted:
            continue
        if field.get("default") not in (None, ""):
            accepted[name] = field["default"]
        elif field.get("required"):
            problems.append(f"{name} is required.")
    if problems:
        raise InvalidAppConfig(problems)
    return accepted


def stored_installation_id(app_config: str | None) -> int | None:
    value = json.loads(app_config or "{}").get(GITHUB_INSTALLATION_ID)
    if value is None or isinstance(value, bool):
        return None
    if not _DIGITS.match(str(value)):
        return None
    return int(value)


def stored_target_cloud(app_config: str | None) -> str:
    value = json.loads(app_config or "{}").get("target_cloud")
    valid = {c.value for c in TargetCloud}
    return value if value in valid else TargetCloud.ONPREM.value


def terraform_variables(
    fields: list[dict[str, Any]],
    app_config: str | None,
    *,
    app_name: str,
    project_name: str,
    gitops: bool,
) -> dict[str, Any]:
    """
    The ``-var`` set of an app: the stored user fields that are still valid,
    then the platform inputs, so a row written before the allowlist cannot
    override them. Anything else in the row is dropped.
    """
    stored = json.loads(app_config or "{}")
    user_fields = _user_fields(fields)
    variables: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in stored.items():
        field = user_fields.get(key)
        if field is not None and _value_problem(field, value) is None:
            variables[key] = value
        elif key not in (
            "project_name",
            "target_cloud",
            GITHUB_INSTALLATION_ID,
            *_PLATFORM_FIELDS,
        ):
            dropped.append(key)
    if dropped:
        logger.warning(
            "app_config keys not passed to Terraform for %s: %s",
            app_name,
            sorted(dropped),
        )
    variables.update(platform_values(fields))
    variables["app_name"] = app_name
    variables["project_name"] = project_name
    if gitops:
        variables["target_cloud"] = stored_target_cloud(app_config)
    return variables
