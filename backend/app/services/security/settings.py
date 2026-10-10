"""
Security settings of an app, and the project policy that can lock them.

An app's settings live in ``deploy/security.yaml`` in its repository, which
the security workflow reads. A CNP admin can set a project default, and lock
it: apps then inherit the value and cannot change it. The lock lives in the
project's registry record, where developers do not write. A developer can
still edit their own file by hand, so the collector compares the two and
raises a core finding when a locked value was changed.

Only the settings in LOCKABLE accept a lock.
"""

from enum import Enum
from io import StringIO
from typing import Any

from pydantic import BaseModel, ConfigDict
from ruamel.yaml import YAML

SETTINGS_FILE = "deploy/security.yaml"
WORKFLOW_FILE = ".github/workflows/security.yml"


class FailOn(str, Enum):
    NONE = "none"
    CRITICAL = "critical"


PLATFORM_DEFAULTS: dict[str, str] = {"ci.failOn": FailOn.NONE.value}
LOCKABLE = frozenset(PLATFORM_DEFAULTS)


class PolicyValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: FailOn
    locked: bool = False


class ProjectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ci_fail_on: PolicyValue | None = None


class EffectiveSetting(BaseModel):
    value: str
    source: str
    locked: bool


def _get(tree: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(tree, dict):
            return None
        tree = tree.get(key)
    return tree


def policy_from_record(record: dict) -> ProjectPolicy:
    raw = ((record.get("spec") or {}).get("securityPolicy") or {}).get("ci")
    fail_on = (raw or {}).get("failOn")
    if not isinstance(fail_on, dict) or "value" not in fail_on:
        return ProjectPolicy()
    return ProjectPolicy(
        ci_fail_on=PolicyValue(
            value=fail_on["value"], locked=bool(fail_on.get("locked"))
        )
    )


def policy_to_record(record: dict, policy: ProjectPolicy) -> dict:
    spec = record.setdefault("spec", {})
    if policy.ci_fail_on is None:
        spec.pop("securityPolicy", None)
        return record
    spec["securityPolicy"] = {
        "ci": {
            "failOn": {
                "value": policy.ci_fail_on.value.value,
                "locked": policy.ci_fail_on.locked,
            }
        }
    }
    return record


def effective_fail_on(
    policy: ProjectPolicy, app_settings: dict | None
) -> EffectiveSetting:
    """Locked project value, then the app's, then the project default, then the platform's."""
    project = policy.ci_fail_on
    if project and project.locked:
        return EffectiveSetting(
            value=project.value.value, source="project", locked=True
        )
    app_value = _get(app_settings or {}, "ci.failOn")
    if app_value in {f.value for f in FailOn}:
        return EffectiveSetting(value=app_value, source="app", locked=False)
    if project:
        return EffectiveSetting(
            value=project.value.value, source="project", locked=False
        )
    return EffectiveSetting(
        value=PLATFORM_DEFAULTS["ci.failOn"], source="platform", locked=False
    )


def locked_drift(policy: ProjectPolicy, app_settings: dict | None) -> bool:
    """True when the repository says otherwise than a locked project value."""
    project = policy.ci_fail_on
    if not project or not project.locked:
        return False
    return _get(app_settings or {}, "ci.failOn") != project.value.value


def render_settings(fail_on: str) -> str:
    out = StringIO()
    out.write(
        "# Security settings of this app, written by the CMP. A value locked\n"
        "# by the project admin is restored here on every change.\n"
    )
    YAML().dump({"ci": {"failOn": fail_on}}, out)
    return out.getvalue()


def parse_settings(raw: str | None) -> dict | None:
    if raw is None:
        return None
    parsed = YAML(typ="safe").load(raw)
    return parsed if isinstance(parsed, dict) else None
