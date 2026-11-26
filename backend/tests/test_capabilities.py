"""Capability table: project role first, then platform-group grants."""

import re
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException

from app.core import roles
from app.services import authz, keycloak_service

FRONTEND = Path(__file__).parents[2] / "frontend/src/lib/permissions.ts"


def _check(groups, project_role, cap, owner=False):
    token = {"sub": "x", "preferred_username": "u", "groups": groups}
    db = mock.MagicMock()
    # The owner is recognised from the projects row, not a Keycloak group.
    row = mock.MagicMock(owner_username="u") if owner else None
    db.query.return_value.filter.return_value.first.return_value = row
    with mock.patch.object(
        keycloak_service, "get_project_role", lambda u, p: project_role
    ):
        try:
            authz.assert_capability(db, token, "shop", cap)
        except HTTPException as exc:
            assert exc.status_code == 403
            return False
    return True


@pytest.mark.parametrize(
    ("groups", "project_role", "cap", "allowed"),
    [
        (["security-auditor"], None, "app.viewConfig", True),
        (["security-auditor"], None, "app.viewLogs", False),
        (["security-auditor"], None, "app.operate", False),
        (["finops"], None, "finops.view", True),
        (["finops"], None, "release.view", False),
        (["infra"], None, "app.deploy", True),
        ([], "guest", "app.viewObservability", True),
        ([], "guest", "app.viewLogs", False),
        (["security-auditor"], "member", "app.operate", True),
        (["/security-auditor"], None, "members.view", True),
        ([], None, "app.view", False),
    ],
)
def test_capability_table(groups, project_role, cap, allowed):
    assert _check(groups, project_role, cap) is allowed


@pytest.mark.parametrize(
    ("project_role", "owner", "cap", "allowed"),
    [
        (None, True, "project.export", True),
        (None, True, "app.deploy", True),
        ("admin", False, "project.export", False),
        ("admin", False, "app.deploy", True),
        ("member", False, "app.viewLogs", True),
        ("member", False, "app.deploy", False),
    ],
)
def test_project_roles_alone(project_role, owner, cap, allowed):
    assert _check([], project_role, cap, owner=owner) is allowed


def test_platform_cap_sets_stay_within_the_table():
    for cap in ("app.viewLogs", "app.viewTraces", "app.viewSecretsLink"):
        assert cap not in roles.AUDITOR_CAPS
    assert roles.AUDITOR_CAPS <= roles.MIN_ROLE.keys()
    assert roles.FINOPS_CAPS <= roles.MIN_ROLE.keys()


def test_require_cap_is_memoised():
    assert authz.RequireCap("app.view") is authz.RequireCap("app.view")


def test_min_role_matches_frontend():
    src = FRONTEND.read_text()
    block = src.split("const MIN_ROLE", 1)[1].split("};", 1)[0]
    front = dict(re.findall(r'"([\w.]+)": "(\w+)"', block))
    assert front == {k: v.value for k, v in roles.MIN_ROLE.items()}


@pytest.mark.parametrize(
    ("name", "caps"),
    [
        ("AUDITOR_CAPABILITIES", roles.AUDITOR_CAPS),
        ("FINOPS_CAPABILITIES", roles.FINOPS_CAPS),
    ],
)
def test_platform_caps_match_frontend(name, caps):
    block = FRONTEND.read_text().split(f"const {name}", 1)[1].split(";", 1)[0]
    assert set(re.findall(r'"([\w.]+)"', block)) == caps
