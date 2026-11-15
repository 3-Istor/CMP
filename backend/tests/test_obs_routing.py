import base64
import copy
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.services.observability import routing, silences

APP_HOOK = "https://discord.com/api/webhooks/111/app-token"
PROJECT_HOOK = "https://discord.com/api/webhooks/222/project-token"
PLATFORM_HOOK = "https://discord.com/api/webhooks/333/platform-token"


def plan(targets: dict[str, str], apps=("web", "api")):
    return routing.ProjectRouting(
        project="shop",
        apps=list(apps),
        targets=targets,
        platform=PLATFORM_HOOK,
    )


def test_an_app_webhook_gets_its_own_route_before_the_project_one():
    config = routing.render_config(
        plan({"app:web": APP_HOOK, "project": PROJECT_HOOK})
    )

    route = config["spec"]["route"]
    assert (route["receiver"], route["routes"][0]["matchers"]) == (
        "project",
        ['cnp_app="web"'],
    )


def test_without_any_team_webhook_alerts_go_to_the_platform():
    config = routing.render_config(plan({}))

    assert config["spec"]["route"]["receiver"] == "platform"


def test_the_route_only_matches_the_projects_alerts_and_does_not_continue():
    route = routing.render_config(plan({}))["spec"]["route"]

    assert (route["matchers"], route["continue"]) == (
        ['cnp_project="shop"'],
        False,
    )


def test_alerts_are_grouped_by_cluster():
    route = routing.render_config(plan({}))["spec"]["route"]

    assert "cluster" in route["group_by"]


def test_nothing_is_rendered_without_any_webhook():
    empty = routing.ProjectRouting(project="shop", apps=["web"])

    assert (routing.render_config(empty), routing.render_secret(empty)) == (
        None,
        None,
    )


def test_a_deleted_apps_webhook_is_not_copied():
    secret = routing.render_secret(
        plan({"app:gone": APP_HOOK, "project": PROJECT_HOOK})
    )

    assert set(secret["data"]) == {"project"}


def test_a_value_that_is_not_a_discord_webhook_is_never_used():
    secret = routing.render_secret(
        plan({"app:web": "http://attacker.example/hook"})
    )

    assert set(secret["data"]) == {"platform"}


def test_webhooks_live_in_the_secret_not_in_the_config():
    targets = {"app:web": APP_HOOK, "project": PROJECT_HOOK}

    config = json.dumps(routing.render_config(plan(targets)))
    secret = routing.render_secret(plan(targets))

    assert (
        APP_HOOK in config,
        base64.b64decode(secret["data"]["app-web"]),
    ) == (
        False,
        APP_HOOK.encode(),
    )


def test_inhibitions_never_reach_another_project():
    rules = routing.render_config(plan({}))["spec"]["inhibit_rules"]

    assert all(
        'cnp_project="shop"' in r["source_matchers"]
        and 'cnp_project="shop"' in r["target_matchers"]
        for r in rules
    )


# ---- end to end, against a real Alertmanager ----------------------------

ALERTMANAGER = os.environ.get("ALERTMANAGER") or shutil.which("alertmanager")


class _Discord(BaseHTTPRequestHandler):
    received: list[tuple[str, dict]] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        _Discord.received.append(
            (self.path, json.loads(self.rfile.read(length)))
        )
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _as_alertmanager_config(spec: dict, hook_base: str) -> dict:
    """What the operator builds from a VMAlertmanagerConfig, roughly."""
    spec = copy.deepcopy(spec)

    def fast(route):
        route["group_wait"] = "1s"
        route["group_interval"] = "1s"
        for child in route.get("routes", []):
            fast(child)

    fast(spec["route"])
    for receiver in spec["receivers"]:
        for discord in receiver["discord_configs"]:
            key = discord.pop("webhook_url_secret")["key"]
            discord["webhook_url"] = f"{hook_base}/{key}"
    return {
        "route": {"receiver": "root", "routes": [spec["route"]]},
        "receivers": [{"name": "root"}] + spec["receivers"],
        "inhibit_rules": spec["inhibit_rules"],
    }


@pytest.fixture
def alertmanager(tmp_path):
    if not ALERTMANAGER:
        pytest.skip("alertmanager is not installed")
    _Discord.received = []
    discord = ThreadingHTTPServer(("127.0.0.1", 0), _Discord)
    threading.Thread(target=discord.serve_forever, daemon=True).start()
    spec = routing.render_config(plan({"app:web": APP_HOOK}))["spec"]
    config = _as_alertmanager_config(
        spec, f"http://127.0.0.1:{discord.server_port}"
    )
    (tmp_path / "am.json").write_text(json.dumps(config))
    port = _free_port()
    process = subprocess.Popen(
        [
            ALERTMANAGER,
            f"--config.file={tmp_path / 'am.json'}",
            f"--storage.path={tmp_path / 'data'}",
            f"--web.listen-address=127.0.0.1:{port}",
            "--cluster.listen-address=",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/-/ready").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        process.kill()
        pytest.fail(process.stderr.read().decode()[-2000:])
    yield url
    process.kill()
    discord.shutdown()


def _fire(url: str, app: str, severity: str, project: str = "shop") -> None:
    httpx.post(
        f"{url}/api/v2/alerts",
        json=[
            {
                "labels": {
                    "alertname": "SloAvailabilityBurnRate",
                    "cnp_project": project,
                    "cnp_app": app,
                    "cnp_slo": "availability",
                    "cluster": "onprem",
                    "severity": severity,
                },
                "annotations": {
                    "summary": "SLO de disponibilité de web",
                    "value": "8% des requêtes hors objectif sur 1h",
                    "runbook_url": "https://runbooks.test/slo",
                    "portal_url": "https://cmp.test/projects/shop/apps/1",
                    "silence_url": "https://cmp.test/projects/shop/alerts",
                },
            }
        ],
    ).raise_for_status()


def _wait_for(count: int) -> list[tuple[str, dict]]:
    for _ in range(100):
        if len(_Discord.received) >= count:
            break
        time.sleep(0.1)
    return _Discord.received


def test_a_critical_app_alert_reaches_the_app_webhook_with_here(alertmanager):
    _fire(alertmanager, "web", "critical")

    path, body = _wait_for(1)[0]

    assert (path, body["content"]) == ("/app-web", "@here")


def test_the_message_carries_value_runbook_and_portal_links(alertmanager):
    _fire(alertmanager, "web", "warning")

    _, body = _wait_for(1)[0]
    description = body["embeds"][0]["description"]

    assert all(
        part in description
        for part in (
            "8% des requêtes",
            "(https://runbooks.test/slo)",
            "(https://cmp.test/projects/shop/apps/1)",
            "Mettre en sourdine",
        )
    ) and not body.get("content")


def test_an_app_without_webhook_falls_back_to_the_platform(alertmanager):
    _fire(alertmanager, "api", "critical")

    path, _ = _wait_for(1)[0]

    assert path == "/platform"


def test_another_projects_alert_does_not_use_these_webhooks(alertmanager):
    _fire(alertmanager, "web", "critical", project="shopping")
    time.sleep(3)

    assert _Discord.received == []


def test_a_portal_silence_mutes_only_its_project(alertmanager):
    body = silences.build(
        "shop",
        {"cnp_app": "web"},
        timedelta(hours=1),
        "alice",
        "",
        datetime.now(timezone.utc),
    )
    httpx.post(f"{alertmanager}/api/v2/silences", json=body).raise_for_status()

    _fire(alertmanager, "web", "critical")
    _fire(alertmanager, "api", "critical")
    time.sleep(3)

    assert [path for path, _ in _Discord.received] == ["/platform"]
