import time
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.config import settings
from app.routers import platform_roles
from app.services import audit
from app.services.access import groups
from app.services.access_review import directory
from app.services.keycloak_service import get_current_user

TOKENS = {
    "alice": {"preferred_username": "alice", "groups": ["/infra"]},
    "eve": {"preferred_username": "eve", "groups": ["member"]},
    "sam": {"preferred_username": "sam", "groups": ["security-auditor"]},
    "fay": {"preferred_username": "fay", "groups": ["finops"]},
    "alice-assistant": {
        "preferred_username": "alice",
        "groups": ["infra"],
        "cnp_agent": {"grant_id": 1, "client": "claude", "scopes": []},
    },
}
MEMBERS = {"infra": ["alice"], "security-auditor": ["sam"], "finops": ["fay"]}


@pytest.fixture
def keycloak():
    with (
        mock.patch.object(groups, "add_member") as add,
        mock.patch.object(groups, "remove_member") as remove,
        mock.patch.object(
            directory, "group_members", lambda g: list(MEMBERS[g])
        ),
    ):
        yield mock.Mock(add=add, remove=remove)


@pytest.fixture
def noted():
    with mock.patch.object(platform_roles, "note") as fake:
        yield fake


@pytest.fixture
def client(keycloak, noted):
    app = FastAPI()
    app.include_router(platform_roles.router, prefix="/api")

    def user(request: Request):
        return TOKENS[request.headers["x-user"]]

    app.dependency_overrides[get_current_user] = user
    return TestClient(app)


def call(client, method, user, path=""):
    return client.request(
        method, f"/api/platform-roles{path}", headers={"x-user": user}
    )


@pytest.mark.parametrize("user", ["eve", "sam", "fay"])
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", ""),
        ("PUT", "/finops/members/x"),
        ("DELETE", "/finops/members/x"),
    ],
)
def test_refuses_everyone_outside_infra(client, keycloak, user, method, path):
    response = call(client, method, user, path)

    assert response.status_code == 403
    keycloak.add.assert_not_called()
    keycloak.remove.assert_not_called()


def test_lists_the_three_groups_with_infra_unmanaged(client):
    response = call(client, "GET", "alice")

    assert response.json() == [
        {"group": "infra", "managed": False, "members": ["alice"]},
        {"group": "security-auditor", "managed": True, "members": ["sam"]},
        {"group": "finops", "managed": True, "members": ["fay"]},
    ]


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_asks_for_a_passkey_before_a_change(client, keycloak, method):
    with (
        mock.patch.object(settings, "STEP_UP_ENABLED", True),
        mock.patch.object(settings, "STEP_UP_ACR", "gold"),
    ):
        response = call(client, method, "alice", "/finops/members/x")

    assert response.status_code == 401
    keycloak.add.assert_not_called()
    keycloak.remove.assert_not_called()


def test_refuses_an_assistant_token(client, keycloak):
    response = call(client, "PUT", "alice-assistant", "/finops/members/x")

    assert response.status_code == 403
    keycloak.add.assert_not_called()


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_refuses_to_write_infra_even_stepped_up(client, keycloak, method):
    stepped_up = {"acr": "gold", "auth_time": time.time() - 10}
    with (
        mock.patch.object(settings, "STEP_UP_ENABLED", True),
        mock.patch.object(settings, "STEP_UP_ACR", "gold"),
        mock.patch.dict(TOKENS["alice"], stepped_up),
    ):
        response = call(client, method, "alice", "/infra/members/x")

    assert response.status_code == 403
    assert response.json()["detail"] == "'infra' is managed in Terraform."
    keycloak.add.assert_not_called()
    keycloak.remove.assert_not_called()


def test_grants_finops_and_journals_it(client, keycloak, noted):
    response = call(client, "PUT", "alice", "/finops/members/x")

    assert response.status_code == 204
    keycloak.add.assert_called_once_with("x", "finops")
    noted.assert_called_once()
    assert noted.call_args.kwargs == {
        "action": "platform_role.add",
        "group": "finops",
        "target": "x",
    }


def test_removes_an_auditor_and_journals_it(client, keycloak, noted):
    response = call(client, "DELETE", "alice", "/security-auditor/members/sam")

    assert response.status_code == 204
    keycloak.remove.assert_called_once_with("sam", "security-auditor")
    assert noted.call_args.kwargs["action"] == "platform_role.remove"


def test_reports_a_keycloak_failure_as_502(client, keycloak):
    keycloak.add.side_effect = groups.GroupError("keycloak down")

    response = call(client, "PUT", "alice", "/finops/members/x")

    assert response.status_code == 502


def test_group_membership_code_accepts_finops_and_auditor_only():
    # pylint: disable=protected-access
    groups._require_managed("finops")
    groups._require_managed("security-auditor")
    for group in ("infra", "member"):
        with pytest.raises(groups.GroupError):
            groups._require_managed(group)


def test_changes_are_journaled_as_notable():
    action, _ = audit.match_action(
        "DELETE", "/api/platform-roles/finops/members/x"
    )

    assert action == "platform_role.remove"
    assert action in audit.NOTABLE
