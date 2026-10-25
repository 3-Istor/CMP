"""
Verification suites and how one run of each is carried out.

Lot 1 kinds: smoke (the platform's, always there), external (a workflow of the
app's repository that posts its result back) and metrics (Envoy's numbers for
the environment's route over a window). Job and k6 suites come later.
"""

import asyncio
import hashlib
import logging
import math
import secrets
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import Field

from app.schemas.release import _Camel
from app.services.kube_client import kube_list
from app.services.metrics_isolation import VMSELECT
from app.services.release import traffic

logger = logging.getLogger(__name__)

SMOKE = "smoke"
SMOKE_PATIENCE_SECONDS = 60
EXTERNAL_TOKEN_BYTES = 32

Kind = Literal["smoke", "external", "metrics"]
OnInconclusive = Literal["fail", "pass", "ask"]


class SuiteSpec(_Camel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    kind: Literal["external", "metrics"]
    # external: the workflow file started with workflow_dispatch.
    workflow: str | None = Field(default=None, pattern=r"^[\w.-]+\.ya?ml$")
    timeout_minutes: int = Field(default=30, ge=1, le=1440)
    # Allowed against prod: it only reads.
    prod_safe: bool = False
    # metrics
    window_minutes: int = Field(default=15, ge=1, le=240)
    min_requests: int = Field(default=50, ge=1, le=1_000_000)
    error_rate_max: float = Field(default=1, gt=0, le=100)
    p95_ms: int | None = Field(default=None, ge=1, le=600_000)
    on_inconclusive: OnInconclusive = "ask"

    def problems(self) -> list[str]:
        if self.name == SMOKE:
            return ["'smoke' is the platform's own suite, pick another name"]
        if self.kind == "external" and not self.workflow:
            return [
                f"suite '{self.name}': an external suite names its workflow file"
            ]
        return []


SMOKE_SPEC = {"name": SMOKE, "kind": "smoke", "prodSafe": True}


@dataclass
class Outcome:
    status: str
    summary: str
    measurements: dict | None = None


def new_token() -> tuple[str, str]:
    """A one-time callback token and the hash kept in the database."""
    token = secrets.token_urlsafe(EXTERNAL_TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _service_urls(
    namespace: str, releases: dict[str, str], paths: dict[str, str]
) -> dict[str, str]:
    """component -> URL of its main Service (not the canary's -stable/-canary pair)."""
    urls: dict[str, str] = {}
    for component, release in releases.items():
        services = kube_list(
            f"/api/v1/namespaces/{namespace}/services",
            {"labelSelector": f"app.kubernetes.io/instance={release}"},
        )
        main = [
            s
            for s in services
            if not s["metadata"]["name"].endswith(("-canary", "-stable"))
            and s.get("spec", {}).get("ports")
        ]
        if main:
            service = main[0]
            port = service["spec"]["ports"][0]["port"]
            urls[component] = (
                f"http://{service['metadata']['name']}.{namespace}.svc.cluster.local:"
                f"{port}{paths.get(component, '/')}"
            )
    return urls


async def run_smoke(
    namespace: str, releases: dict[str, str], paths: dict[str, str]
) -> Outcome:
    """
    GET every component's Service until it answers below 400, for up to a
    minute: chart pods can be Ready before the app inside listens.
    """
    urls = await asyncio.to_thread(_service_urls, namespace, releases, paths)
    if not urls:
        return Outcome("failed", "No Service found in the environment.")
    answers: dict[str, int | str] = {}
    async with httpx.AsyncClient(
        timeout=5.0, follow_redirects=False
    ) as client:
        for component, url in urls.items():
            deadline = (
                asyncio.get_running_loop().time() + SMOKE_PATIENCE_SECONDS
            )
            while True:
                try:
                    code: int | str = (await client.get(url)).status_code
                except httpx.HTTPError as exc:
                    code = type(exc).__name__
                answers[component] = code
                if isinstance(code, int) and code < 400:
                    break
                if asyncio.get_running_loop().time() > deadline:
                    break
                await asyncio.sleep(5)
    failed = {
        c: a
        for c, a in answers.items()
        if not (isinstance(a, int) and a < 400)
    }
    summary = ", ".join(f"{c} {a}" for c, a in answers.items())
    return Outcome(
        "failed" if failed else "passed", summary, {"answers": answers}
    )


async def run_public_smoke(url: str, realm: str) -> Outcome:
    """
    GET the environment through its public hostname, logged in as the realm's
    technical user when SSO stands in front: the CMP cannot reach a Service on
    a public cloud cluster.
    """
    host = httpx.URL(url).host
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True
        ) as client:
            first = await client.get(url)
            if first.url.host == host:
                status = first.status_code
            else:
                cookie = await traffic.login(url, realm)
                status = (
                    await client.get(url, headers={"Cookie": cookie})
                ).status_code
    except (httpx.HTTPError, traffic.TrafficError) as exc:
        return Outcome("failed", f"{url}: {exc}")
    passed = status < 400
    return Outcome(
        "passed" if passed else "failed",
        f"{url} {status}",
        {"answers": {"public": status}},
    )


def _route_selector(namespace: str) -> str:
    return f'container="envoy",envoy_cluster_name=~"httproute/{namespace}/.*"'


async def _query(client: httpx.AsyncClient, promql: str) -> float | None:
    response = await client.get(
        f"{VMSELECT}/api/v1/query", params={"query": promql}
    )
    response.raise_for_status()
    result = response.json().get("data", {}).get("result") or []
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if math.isnan(value) else value


async def run_metrics(namespace: str, spec: SuiteSpec) -> Outcome:
    """
    Requests, error rate and p95 latency of the environment's route over the
    window. Below min_requests the answer is inconclusive, never a pass.
    """
    selector = _route_selector(namespace)
    window = f"{spec.window_minutes}m"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            total = await _query(
                client,
                f"sum(increase(envoy_cluster_endpoint_rq_total{{{selector}}}[{window}]))",
            )
            errors = await _query(
                client,
                f"sum(increase(envoy_cluster_endpoint_rq_error{{{selector}}}[{window}]))",
            )
            p95 = None
            if spec.p95_ms is not None:
                p95 = await _query(
                    client,
                    "histogram_quantile(0.95, sum by (le) (rate("
                    f"envoy_cluster_upstream_rq_time_bucket{{{selector}}}[{window}])))",
                )
    except httpx.HTTPError as exc:
        return Outcome("error", f"Metrics unavailable: {exc}")

    requests = total or 0.0
    rate = 100 * (errors or 0.0) / requests if requests else None
    measurements = {
        "requests": round(requests),
        "errorRate": None if rate is None else round(rate, 2),
        "p95Ms": None if p95 is None else round(p95),
        "windowMinutes": spec.window_minutes,
    }
    if requests < spec.min_requests:
        return Outcome(
            "inconclusive",
            f"{round(requests)} requests in {spec.window_minutes} min, "
            f"{spec.min_requests} needed to judge",
            measurements,
        )
    problems = []
    if rate is not None and rate > spec.error_rate_max:
        problems.append(f"error rate {rate:.2f} % > {spec.error_rate_max} %")
    if spec.p95_ms is not None and p95 is not None and p95 > spec.p95_ms:
        problems.append(f"p95 {p95:.0f} ms > {spec.p95_ms} ms")
    summary = f"{round(requests)} requests, {rate:.2f} % errors" + (
        f", p95 {p95:.0f} ms" if p95 is not None else ""
    )
    if problems:
        return Outcome("failed", "; ".join(problems), measurements)
    return Outcome("passed", summary, measurements)
