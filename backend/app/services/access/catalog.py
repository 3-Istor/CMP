"""
What can be elevated to, and through which Keycloak group.

Each elevation is one Keycloak group created by the K3s Terraform, bound
there to the real permission (a Vault policy through an external group
alias, a ClusterRoleBinding, later a cloud admin role through the Keycloak
federation). The CMP only moves people in and out of the group: adding an
entry here without its Terraform side grants nothing.
"""

from dataclasses import dataclass

from app.core.config import settings

# Hard ceiling: the Vault OIDC token TTL and the Keycloak token lifespans in
# the K3s Terraform are sized for grants of at most this.
ABSOLUTE_MAX_MINUTES = 240
MIN_MINUTES = 15


@dataclass(frozen=True)
class Elevation:
    key: str
    group: str
    # Where the permission lives, for the UI and the alerts: vault, k3s,
    # aws, gcp.
    system: str


ELEVATIONS: dict[str, Elevation] = {
    e.key: e
    for e in (
        Elevation("vault-data", "elevated-vault-data", "vault"),
        Elevation("k3s-admin", "elevated-k3s-admin", "k3s"),
    )
}


def get(key: str) -> Elevation | None:
    return ELEVATIONS.get(key)


def managed_groups() -> set[str]:
    return {e.group for e in ELEVATIONS.values()}


def max_minutes() -> int:
    return max(
        MIN_MINUTES, min(settings.ACCESS_MAX_MINUTES, ABSOLUTE_MAX_MINUTES)
    )


def default_minutes() -> int:
    return max(
        MIN_MINUTES, min(settings.ACCESS_DEFAULT_MINUTES, max_minutes())
    )
