import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RETENTION_PATTERN = r"^[1-9][0-9]*[dwm]$"

PROJECT_MEMBERS_GROUPS = ["project-members", "project-admins"]
PROJECT_ADMINS_GROUPS = ["project-admins"]

SINGLE_VALUES_FILE = "deploy/values.yaml"
FRONTEND_VALUES_FILE = "deploy/values-frontend.yaml"
BACKEND_VALUES_FILE = "deploy/values-backend.yaml"


class ExposurePreset(str, Enum):
    PUBLIC = "public"
    PROJECT_USERS = "project_users"
    PROJECT_MEMBERS = "project_members"
    PROJECT_ADMINS = "project_admins"


ExposureState = Literal[
    "public",
    "project_users",
    "project_members",
    "project_admins",
    "custom",
]

_PRESET_VALUES: dict[ExposurePreset, tuple[bool, list[str]]] = {
    ExposurePreset.PUBLIC: (False, []),
    ExposurePreset.PROJECT_USERS: (True, []),
    ExposurePreset.PROJECT_MEMBERS: (True, PROJECT_MEMBERS_GROUPS),
    ExposurePreset.PROJECT_ADMINS: (True, PROJECT_ADMINS_GROUPS),
}

_PRESET_LABELS: dict[ExposurePreset, str] = {
    ExposurePreset.PUBLIC: "public",
    ExposurePreset.PROJECT_USERS: "project users",
    ExposurePreset.PROJECT_MEMBERS: "project members",
    ExposurePreset.PROJECT_ADMINS: "project admins",
}


class BackupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    keep_after_delete: bool | None = None
    retention_policy: str | None = Field(
        default=None, pattern=RETENTION_PATTERN
    )


class SecurityDataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exposure: ExposurePreset | None = None
    backup: BackupUpdate | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "SecurityDataUpdate":
        backup_changes = (
            self.backup.model_dump(exclude_none=True) if self.backup else {}
        )
        if self.exposure is None and not backup_changes:
            raise ValueError("Nothing to update.")
        return self


class BackupState(BaseModel):
    enabled: bool
    keep_after_delete: bool
    retention_policy: str | None


class DatabaseState(BaseModel):
    backup: BackupState


class SecurityDataRead(BaseModel):
    repo: str
    can_edit: bool
    exposure: ExposureState | None
    database: DatabaseState | None


def app_type_of(app_config: str | None, terraform_outputs: str | None) -> str:
    """
    The k3s-gitops-app module decides the type and outputs it; the create
    payload never carries it, so app_config alone says "static" for all.
    """
    for raw in (terraform_outputs, app_config):
        try:
            app_type = json.loads(raw or "{}").get("app_type")
        except ValueError:
            continue
        if app_type in ("static", "fullstack"):
            return app_type
    return "static"


def values_files_for(app_type: str) -> tuple[str, str]:
    """Return the (exposure file, database file) of an app type."""
    if app_type == "fullstack":
        return FRONTEND_VALUES_FILE, BACKEND_VALUES_FILE
    return SINGLE_VALUES_FILE, SINGLE_VALUES_FILE


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def has_ingress(values: dict) -> bool:
    return _mapping(values.get("ingress")).get("enabled") is True


def has_database(values: dict) -> bool:
    return _mapping(values.get("db")).get("enabled") is True


def read_exposure(values: dict) -> ExposureState:
    ingress = _mapping(values.get("ingress"))
    sso_protected = ingress.get("sso_protected", False)
    groups = ingress.get("allowedGroups") or []
    if not isinstance(sso_protected, bool) or not isinstance(groups, list):
        return "custom"
    for preset, (preset_sso, preset_groups) in _PRESET_VALUES.items():
        if sso_protected == preset_sso and set(groups) == set(preset_groups):
            return preset.value
    return "custom"


def read_backup(values: dict) -> BackupState:
    backup = _mapping(_mapping(values.get("db")).get("backup"))
    retention = backup.get("retentionPolicy")
    return BackupState(
        enabled=backup.get("enabled") is True,
        keep_after_delete=backup.get("keepAfterDelete") is True,
        retention_policy=retention if isinstance(retention, str) else None,
    )


def exposure_patch(preset: ExposurePreset) -> dict:
    sso_protected, groups = _PRESET_VALUES[preset]
    return {
        "ingress": {
            "sso_protected": sso_protected,
            "allowedGroups": list(groups),
        }
    }


def exposure_commit_label(preset: ExposurePreset) -> str:
    return f"set exposure to {_PRESET_LABELS[preset]}"


def backup_patch(update: BackupUpdate) -> dict:
    backup: dict[str, Any] = {}
    if update.enabled is not None:
        backup["enabled"] = update.enabled
    if update.keep_after_delete is not None:
        backup["keepAfterDelete"] = update.keep_after_delete
    if update.retention_policy is not None:
        backup["retentionPolicy"] = update.retention_policy
    return {"db": {"backup": backup}}


def backup_commit_labels(update: BackupUpdate) -> list[str]:
    labels = []
    if update.enabled is not None:
        labels.append(
            "enable database backups"
            if update.enabled
            else "disable database backups"
        )
    if update.keep_after_delete is not None:
        labels.append(
            "keep backups after app deletion"
            if update.keep_after_delete
            else "delete backups with the app"
        )
    if update.retention_policy is not None:
        labels.append(f"set backup retention to {update.retention_policy}")
    return labels
