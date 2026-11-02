import re
from typing import Any

from pydantic import BaseModel

SECRET_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class ExpectedSecret(BaseModel):
    name: str
    set: bool


class SecretPresence(BaseModel):
    expected: list[ExpectedSecret]
    extra: list[str]
    vault_path: str | None = None


def expected_names(values_files: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for values in values_files:
        secrets = values.get("secrets")
        if not isinstance(secrets, dict):
            continue
        keys = secrets.get("keys")
        if not isinstance(keys, list):
            continue
        names.update(
            key
            for key in keys
            if isinstance(key, str) and SECRET_NAME_PATTERN.match(key)
        )
    return sorted(names)


def vault_location(
    values_files: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """(mount, path) read by the chart's VaultSecret, from secrets.vaultPath."""
    for values in values_files:
        secrets = values.get("secrets")
        if not isinstance(secrets, dict) or not secrets.get("enabled"):
            continue
        vault_path = secrets.get("vaultPath")
        if not isinstance(vault_path, str):
            continue
        mount, _, path = vault_path.strip("/").partition("/")
        if mount and path:
            return mount, path
    return None


def compute(
    expected: list[str],
    present: list[str],
    location: tuple[str, str] | None = None,
) -> SecretPresence:
    present_set = set(present)
    return SecretPresence(
        expected=[
            ExpectedSecret(name=name, set=name in present_set)
            for name in expected
        ],
        extra=sorted(present_set - set(expected)),
        vault_path=f"{location[0]}/{location[1]}" if location else None,
    )
