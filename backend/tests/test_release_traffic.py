import asyncio
import json
from unittest import mock

import httpx
import pytest

from app.services.release import traffic

APP = "https://shop-web.3istor.com/"
AUTH = "https://auth.3istor.com/realms/shop/protocol/openid-connect/auth"
ACTION = "https://auth.3istor.com/realms/shop/login-actions/authenticate?session_code=x"


def _fake_sso(seen_logins):
    """Keycloak admin + login page + Envoy OIDC callback, like the real flow."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/realms/master/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin"})
        if "/admin/realms/shop/users" in url and request.method == "GET":
            return httpx.Response(200, json=[{"id": "u1"}])
        if "/admin/realms/shop/groups" in url:
            return httpx.Response(
                200, json=[{"id": "g1", "name": "project-members"}]
            )
        if "/admin/realms/shop/" in url:
            return httpx.Response(204)
        if url == APP and "AccessToken" not in request.headers.get(
            "cookie", ""
        ):
            return httpx.Response(302, headers={"location": AUTH})
        if url == AUTH:
            page = (
                "<script>const kcContext = {"
                + json.dumps({"url": {"loginAction": ACTION}})[1:-1]
                + "}</script>"
            )
            return httpx.Response(200, text=page)
        if url == ACTION:
            seen_logins.append(request.content.decode())
            return httpx.Response(
                302,
                headers={
                    "location": "https://shop-web.3istor.com/oauth2/callback?code=c"
                },
            )
        if "/oauth2/callback" in url:
            return httpx.Response(
                302,
                headers=[
                    ("location", APP),
                    (
                        "set-cookie",
                        "AccessToken-1=a; Path=/; Domain=shop-web.3istor.com",
                    ),
                    (
                        "set-cookie",
                        "OauthHMAC-1=h; Path=/; Domain=shop-web.3istor.com",
                    ),
                ],
            )
        return httpx.Response(
            200, headers={"set-cookie": "cnp-track=stable; Path=/"}
        )

    return handler


def _client_with(handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return mock.patch.object(traffic.httpx, "AsyncClient", side_effect=factory)


def test_logs_in_through_the_realm_form_and_keeps_only_the_sso_cookies():
    logins = []
    with _client_with(_fake_sso(logins)):
        cookie = asyncio.run(traffic._login(APP, "shop"))

    assert "username=cnp-traffic" in logins[0]
    assert sorted(part.split("=")[0] for part in cookie.split("; ")) == [
        "AccessToken-1",
        "OauthHMAC-1",
    ]


def test_refuses_an_app_without_sso_login_page():
    def handler(request):
        if "/admin/realms/" in str(request.url) or "/token" in str(
            request.url
        ):
            return _fake_sso([])(request)
        return httpx.Response(200, text="<html>no sso</html>")

    with _client_with(handler), pytest.raises(traffic.TrafficError):
        asyncio.run(traffic._login(APP, "shop"))


def _session():
    return traffic.TrafficSession(
        deployment_id=1, app_url=APP, rps=5, until=0, started_by="dev"
    )


@pytest.mark.parametrize(
    "response, version",
    [
        (
            httpx.Response(
                200, headers={"set-cookie": "cnp-track=canary; Path=/"}
            ),
            "canary",
        ),
        (
            httpx.Response(
                200, headers={"set-cookie": "cnp-track=stable; Path=/"}
            ),
            "stable",
        ),
        (httpx.Response(200), "stable"),
        (
            httpx.Response(
                503, headers={"set-cookie": "cnp-track=canary; Path=/"}
            ),
            "error",
        ),
    ],
)
def test_classifies_each_answer_by_the_version_that_served_it(
    response, version
):
    session = _session()

    async def visit():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: response)
        ) as c:
            return await traffic._visit(c, session, "AccessToken-1=a")

    asyncio.run(visit())

    assert session.events[-1]["version"] == version


def test_sends_each_request_as_a_new_visitor_without_the_version_cookie():
    sent = []
    session = _session()

    def handler(request):
        sent.append(request.headers.get("cookie"))
        return httpx.Response(
            200, headers={"set-cookie": "cnp-track=canary; Path=/"}
        )

    async def visits():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            await traffic._visit(c, session, "AccessToken-1=a")
            await traffic._visit(c, session, "AccessToken-1=a")

    asyncio.run(visits())

    assert sent == ["AccessToken-1=a", "AccessToken-1=a"]


def test_asks_for_a_new_login_when_the_sso_session_expired():
    session = _session()

    async def visit():
        transport = httpx.MockTransport(
            lambda r: httpx.Response(302, headers={"location": AUTH})
        )
        async with httpx.AsyncClient(transport=transport) as c:
            return await traffic._visit(c, session, "AccessToken-1=a")

    assert asyncio.run(visit()) is False


def test_runs_one_traffic_session_per_app():
    async def scenario():
        with mock.patch.object(traffic, "_run", mock.AsyncMock()):
            await traffic.start(9, APP, "shop", 5, 1, "dev")
            with pytest.raises(traffic.TrafficError):
                await traffic.start(9, APP, "shop", 5, 1, "dev")
        traffic._sessions.clear()

    asyncio.run(scenario())


def test_caps_the_request_rate():
    async def scenario():
        with mock.patch.object(traffic, "_run", mock.AsyncMock()):
            session = await traffic.start(8, APP, "shop", 500, 1, "dev")
        traffic._sessions.clear()
        return session.rps

    assert asyncio.run(scenario()) == traffic.MAX_RPS


from app.models.deployment import Deployment  # noqa: E402
from tests.test_release_api import (
    FakeSource,
    _client,
    _paused,
    keycloak,
    session_factory,
)  # noqa: E402,F401


def _with_hostname(session_factory, hostname):
    with session_factory() as db:
        db.get(Deployment, 7).terraform_outputs = json.dumps(
            {"hostname": hostname} if hostname else {}
        )
        db.commit()


def test_a_guest_cannot_start_demo_traffic(session_factory, keycloak):
    _with_hostname(session_factory, "shop-web.3istor.com")
    client = _client(session_factory, FakeSource(_paused()), "guest")

    with mock.patch.object(traffic, "start", mock.AsyncMock()) as start:
        response = client.post("/api/deployments/7/release/traffic", json={})

    assert (response.status_code, start.called) == (403, False)


def test_an_app_without_hostname_gets_no_traffic(session_factory, keycloak):
    _with_hostname(session_factory, None)
    client = _client(session_factory, FakeSource(_paused()))

    response = client.post("/api/deployments/7/release/traffic", json={})

    assert response.status_code == 409


def test_a_member_starts_traffic_on_the_app_url_in_its_project_realm(
    session_factory, keycloak
):
    _with_hostname(session_factory, "shop-web.3istor.com")
    client = _client(session_factory, FakeSource(_paused()))
    session = _session()

    with mock.patch.object(
        traffic, "start", mock.AsyncMock(return_value=session)
    ) as start:
        response = client.post(
            "/api/deployments/7/release/traffic", json={"rps": 4, "minutes": 2}
        )

    assert response.status_code == 200
    assert start.call_args.args[:5] == (
        7,
        "https://shop-web.3istor.com/",
        "shop",
        4,
        2,
    )
