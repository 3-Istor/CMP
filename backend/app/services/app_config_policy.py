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
    ("db", "enabled"),
    ("db", "name"),
    ("db", "storage"),
    ("db", "metrics"),
    ("db", "pooler"),
    ("bucket", "enabled"),
    ("migrations", "timeout"),
    ("migrations", "resources"),
    ("monitoring",),
    ("metrics",),
    ("tracing",),
    ("offhours",),
    ("configMap",),
    ("env",),
    ("secrets", "keys"),
    ("network", "allowFrom"),
    ("livenessProbe",),
    ("readinessProbe",),
    ("startupProbe",),
    ("podAnnotations",),
    ("podLabels",),
    ("ingress", "enabled"),
    ("ingress", "sso_protected"),
    ("ingress", "allowedGroups"),
)

# Values with an endpoint of their own, which checks what a plain merge
# cannot (a canary in progress, the chart's release rules, the backup bucket
# of a public cloud, a restore's source): the generic form would skip those
# checks. The repository can still change them, under the chart's schema.
ELSEWHERE: dict[tuple[str, ...], str] = {
    ("release",): "the Release tab",
    ("migrations", "enabled"): "the Release tab",
    ("migrations", "command"): "the Release tab",
    ("migrations", "backupBefore"): "the database backups",
    ("db", "backup"): "the Security tab (database backups)",
    ("db", "restore"): "the restore dialog of the Security tab",
}

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
    """Dotted paths *updates* would change outside EDITABLE_PREFIXES, each
    with the place that changes it when there is one."""
    refused = []
    for path in changed_paths(current, updates):
        if is_editable(path):
            continue
        where = next(
            (
                place
                for prefix, place in ELSEWHERE.items()
                if path[: len(prefix)] == prefix
            ),
            None,
        )
        dotted = ".".join(path)
        refused.append(f"{dotted} (use {where})" if where else dotted)
    return sorted(refused)
