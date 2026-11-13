"""
Which Helm values a project admin may change through PATCH /config.

The config panel sends the whole values file back on save, so the check is on
what changes, not on which keys are present: a platform-controlled value
echoed unchanged passes, a changed or added one is refused. The app's identity
and its Vault access (project_name, secrets.vaultRole, ...) are set by the
platform; letting them through let a project admin name a platform Vault role.
"""

from typing import Any

EDITABLE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("replicaCount",),
    ("image", "tag"),
    ("image", "digest"),
    ("image", "pullPolicy"),
    ("service",),
    ("resources",),
    ("autoscaling",),
    ("db",),
    ("migrations",),
    ("release",),
    ("monitoring",),
    ("offhours",),
    ("configMap",),
    ("env",),
    ("livenessProbe",),
    ("readinessProbe",),
    ("startupProbe",),
    ("podAnnotations",),
    ("podLabels",),
    ("logRetention",),
    ("ingress", "enabled"),
    ("ingress", "sso_protected"),
    ("ingress", "allowedGroups"),
)

_MISSING = object()


def changed_paths(
    current: Any, updates: Any, prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    """Leaf paths of *updates* whose value differs from *current* or is new."""
    if isinstance(updates, dict) and (
        current is _MISSING or isinstance(current, dict)
    ):
        known = {} if current is _MISSING else current
        paths: list[tuple[str, ...]] = []
        for key, value in updates.items():
            paths += changed_paths(
                known.get(key, _MISSING), value, prefix + (str(key),)
            )
        return paths
    return [] if current == updates else [prefix]


def is_editable(path: tuple[str, ...]) -> bool:
    return any(
        path[: len(allowed)] == allowed for allowed in EDITABLE_PREFIXES
    )


def forbidden_changes(current: Any, updates: Any) -> list[str]:
    """Dotted paths *updates* would change outside EDITABLE_PREFIXES."""
    return sorted(
        ".".join(path)
        for path in changed_paths(current, updates)
        if not is_editable(path)
    )
