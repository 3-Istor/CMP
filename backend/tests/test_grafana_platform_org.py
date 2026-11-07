import asyncio
from unittest import mock

import httpx

from app.services import grafana_service, keycloak_service

ORG_USERS = [
    {
        "login": "admin",
        "email": "admin@localhost",
        "userId": 1,
        "role": "Admin",
    },
    {
        "login": "brian.perret@epita.fr",
        "email": "brian.perret@epita.fr",
        "userId": 2,
        "role": "Viewer",
    },
    {
        "login": "joe.bejjani@epita.fr",
        "email": "joe.bejjani@epita.fr",
        "userId": 3,
        "role": "Viewer",
    },
    {
        "login": "demo-auditor@3istor.com",
        "email": "demo-auditor@3istor.com",
        "userId": 4,
        "role": "Viewer",
    },
]
# list_group_usernames returns each member's username and email.
GROUPS = {
    "infra": {
        "brian.perret",
        "brian.perret@epita.fr",
        "hugo.guillet",
        "hugo.guillet@epita.fr",
    },
    "security-auditor": {"demo-auditor", "demo-auditor@3istor.com"},
}


def _run(org_users):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.method == "GET":
            return httpx.Response(200, json=org_users)
        if request.method == "POST":
            return httpx.Response(404, json={"message": "user not found"})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with (
        mock.patch.object(grafana_service, "_get_http_client", lambda: client),
        mock.patch.object(
            keycloak_service, "list_group_usernames", lambda g: GROUPS[g]
        ),
    ):
        asyncio.run(grafana_service.sync_platform_org())
    return calls


def test_platform_admins_become_admin_and_others_lose_their_role():
    calls = _run(ORG_USERS)

    patches = {path: body for method, path, body in calls if method == "PATCH"}
    assert patches == {
        "/api/orgs/1/users/2": b'{"role":"Admin"}',
        "/api/orgs/1/users/3": b'{"role":"None"}',
    }


def test_never_touches_the_local_grafana_admin():
    calls = _run(ORG_USERS)

    assert all(not path.endswith("/users/1") for _, path, _ in calls[1:])


def test_adds_a_platform_admin_missing_from_the_org():
    calls = _run(ORG_USERS)

    posts = [body for method, _, body in calls if method == "POST"]
    assert posts == [
        b'{"loginOrEmail":"hugo.guillet@epita.fr","role":"Admin"}'
    ]
