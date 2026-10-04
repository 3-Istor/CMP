"""
Project roles and their precedence.

A user's standing on a project is one of four roles, ordered:

    guest < member < admin < owner

Only three of them are Keycloak groups. ``owner`` is deliberately absent
from :data:`GROUP_SUFFIX`: it is the ``projects.owner_username`` column, set
once when the project is created and never moved, so there is no group to
put anybody in.

This module holds no imports beyond the stdlib on purpose — routers,
services and the authorization dependency all import it, and anything
heavier here would close an import cycle.

Keep in sync with ``frontend/src/lib/permissions.ts``, which mirrors this
ordering to decide what the UI shows.
"""

import enum


class ProjectRole(str, enum.Enum):
    """A user's role on a single project."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"


# Higher rank wins when a user lands in several groups for one project.
_RANK: dict[ProjectRole, int] = {
    ProjectRole.GUEST: 0,
    ProjectRole.MEMBER: 1,
    ProjectRole.ADMIN: 2,
    ProjectRole.OWNER: 3,
}


# Keycloak group suffix per role. ``owner`` has none — see the module
# docstring.
GROUP_SUFFIX: dict[ProjectRole, str] = {
    ProjectRole.ADMIN: "admins",
    ProjectRole.MEMBER: "members",
    ProjectRole.GUEST: "guests",
}

SUFFIX_TO_ROLE: dict[str, ProjectRole] = {
    suffix: role for role, suffix in GROUP_SUFFIX.items()
}

# The roles an admin may hand out. Owner is not among them.
ASSIGNABLE: tuple[ProjectRole, ...] = (
    ProjectRole.ADMIN,
    ProjectRole.MEMBER,
    ProjectRole.GUEST,
)

# Every group suffix a project owns, for the paths that sweep all of them
# (removing a user, listing the roster).
ALL_GROUP_SUFFIXES: tuple[str, ...] = tuple(GROUP_SUFFIX.values())


def rank(role: "ProjectRole | str | None") -> int:
    """
    Return the precedence of *role*, or ``-1`` for no role at all.

    Accepts the raw string form so callers holding a value straight off the
    wire don't have to convert first.
    """
    if role is None:
        return -1
    try:
        return _RANK[ProjectRole(role)]
    except ValueError:
        return -1


def at_least(role: "ProjectRole | str | None", minimum: ProjectRole) -> bool:
    """Return True if *role* is *minimum* or stronger."""
    return rank(role) >= rank(minimum)
