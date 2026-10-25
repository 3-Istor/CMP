"""Run the equivalent of Cloud/terraform/public/{aws/EKS,gcp/GKE}/cnp.sh up/down."""

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cluster import Cluster
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.services.clusters import state
from app.services.clusters.progress import is_resource_done, plan_total
from app.services.kube_client import kube_get
from app.services.state_lock import format_state_lock_message, parse_state_lock

UP_STEPS = ("init", "apply", "kubeconfig", "nodes", "argocd", "wireguard")
DOWN_STEPS = ("checks", "destroy")
POLL_INTERVAL = 10
POLL_TIMEOUT = 600

_DIRS = {TargetCloud.AWS: "aws/EKS", TargetCloud.GCP: "gcp/GKE"}
_CLUSTER_NAMES = {TargetCloud.AWS: "cnp-eks", TargetCloud.GCP: "cnp-gke"}
_LB_JSONPATH = '{.items[?(@.spec.type=="LoadBalancer")].metadata.name}'


class _StepError(Exception):
    pass


@dataclass
class _Ctx:
    cloud: TargetCloud
    operation: str
    cwd: Path
    env: dict
    log: object
    tail: deque = field(default_factory=lambda: deque(maxlen=500))
    step: str = ""
    done: int = 0

    @property
    def kubeconfig(self) -> Path:
        return Path(self.env["KUBECONFIG"])


async def _stream(cmd: list[str], cwd: Path, env: dict, log, on_line) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip("\n")
        if log:
            log.write(line + "\n")
            log.flush()
        on_line(line)
    return await proc.wait()


def _env(cloud: TargetCloud, kubeconfig: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TF_IN_AUTOMATION": "1",
        "TF_PLUGIN_CACHE_DIR": "/app/cloud/.plugin-cache",
        "KUBECONFIG": kubeconfig,
        "VAULT_ADDR": settings.VAULT_URL,
        "VAULT_TOKEN": settings.CLUSTER_OPS_VAULT_TOKEN,
    }
    if cloud == TargetCloud.AWS:
        env |= {
            "AWS_ACCESS_KEY_ID": settings.CLUSTER_OPS_AWS_ACCESS_KEY_ID,
            "AWS_SECRET_ACCESS_KEY": settings.CLUSTER_OPS_AWS_SECRET_ACCESS_KEY,
            "AWS_REGION": "eu-west-3",
            "AWS_PAGER": "",
        }
        tf_vars = settings.CLUSTER_TF_VARS_AWS
    else:
        env |= {
            "GOOGLE_APPLICATION_CREDENTIALS": settings.CLUSTER_OPS_GCP_SA_KEY_PATH,
            "CLOUDSDK_CORE_PROJECT": settings.CLUSTER_OPS_GCP_PROJECT,
        }
        tf_vars = settings.CLUSTER_TF_VARS_GCP
    for k, v in json.loads(tf_vars).items():
        env[f"TF_VAR_{k}"] = v if isinstance(v, str) else json.dumps(v)
    return env


async def cluster_exists(cloud: TargetCloud) -> bool | None:
    """None when the describe call itself failed (not a "not found")."""
    name = _CLUSTER_NAMES[cloud]
    cmd = (
        ["aws", "eks", "describe-cluster", "--name", name]
        if cloud == TargetCloud.AWS
        else ["gcloud", "container", "clusters", "describe", name,
              "--zone", "europe-west9-a"]
    )
    out: list[str] = []
    rc = await _stream(cmd, Path(settings.CLUSTER_TF_ROOT), _env(cloud, ""), None, out.append)
    if rc == 0:
        return True
    text = "\n".join(out)
    return False if ("ResourceNotFoundException" in text or "was not found" in text) else None


def affected_apps(db, cloud: TargetCloud) -> list[dict]:
    rows = (
        db.query(Project.project_name, Deployment.name, Deployment.id)
        .join(Deployment, Deployment.project_id == Project.project_name)
        .filter(Project.target_cloud == cloud, Deployment.status != DeploymentStatus.DELETED)
        .all()
    )
    return [{"project": p, "app": a, "deployment_id": d} for p, a, d in rows]


def _set_step(ctx: _Ctx, step: str, done: int = 0, total: int = 0) -> None:
    ctx.step, ctx.done = step, done
    with SessionLocal() as db:
        state.set_step(db, ctx.cloud, step, done, total)


async def _run(ctx: _Ctx, cmd: list[str], cwd: Path | None = None, on_line=None, log=True) -> str:
    """Run a command, return its output; raise _StepError on non-zero exit."""
    lines: list[str] = []

    def collect(line: str) -> None:
        lines.append(line)
        ctx.tail.append(line)
        if on_line:
            on_line(line)

    rc = await _stream(cmd, cwd or ctx.cwd, ctx.env, ctx.log if log else None, collect)
    if rc:
        lock = parse_state_lock("\n".join(lines))
        raise _StepError(
            format_state_lock_message(lock) if lock
            else f"{ctx.step} failed (exit {rc}): {' '.join(cmd[:2])}"
        )
    return "\n".join(lines)


async def _terraform(ctx: _Ctx, verb: str) -> None:
    total = 0

    def on_line(line: str) -> None:
        nonlocal total
        total = plan_total(line) or total
        if is_resource_done(line, ctx.operation):
            _set_step(ctx, ctx.step, ctx.done + 1, total)
        elif plan_total(line):
            _set_step(ctx, ctx.step, 0, total)

    args = ["-auto-approve", "-input=false"]
    await _run(ctx, ["terraform", verb, *args], on_line=on_line)


async def _fetch_kubeconfig(ctx: _Ctx) -> None:
    if ctx.cloud == TargetCloud.AWS:
        cmd = ["aws", "ssm", "get-parameter", "--name", "/cnp/eks/kubeconfig",
               "--query", "Parameter.Value", "--output", "text"]
        # Not logged: it holds credentials.
        out = await _run(ctx, cmd, log=False)
        ctx.kubeconfig.write_text(out + "\n")
        ctx.kubeconfig.chmod(0o600)
    else:
        await _run(ctx, ["gcloud", "container", "clusters", "get-credentials",
                         "cnp-gke", "--zone", "europe-west9-a"])


async def _poll(what: str, check) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(POLL_INTERVAL)
    raise _StepError(f"Timed out waiting for {what}")


def _argocd_server(cloud: TargetCloud) -> str | None:
    secret = kube_get(f"/api/v1/namespaces/argocd/secrets/cluster-{cloud.value}")
    server = ((secret or {}).get("data") or {}).get("server")
    return base64.b64decode(server).decode() if server else None


async def _wait_argocd(ctx: _Ctx) -> None:
    endpoint = (await _run(ctx, ["terraform", "output", "-raw", "cluster_endpoint"])).strip()
    if not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint  # GKE output has no scheme

    async def registered() -> bool:
        return await asyncio.to_thread(_argocd_server, ctx.cloud) == endpoint

    await _poll("ArgoCD to register the cluster", registered)


async def _wait_wireguard(ctx: _Ctx) -> None:
    cmd = ["kubectl", "-n", "cnp-vpn", "get", "pod", "-l", "app=wireguard",
           "-o", "jsonpath={.items[*].status.phase}"]

    async def running() -> bool:
        phases = (await _run(ctx, cmd, log=False)).split()
        return bool(phases) and all(p == "Running" for p in phases)

    await _poll("the WireGuard pod", running)


async def _up(ctx: _Ctx) -> None:
    for step in UP_STEPS:
        _set_step(ctx, step)
        if step == "init":
            await _run(ctx, ["terraform", "init", "-input=false"])
        elif step == "apply":
            await _terraform(ctx, "apply")
        elif step == "kubeconfig":
            await _fetch_kubeconfig(ctx)
        elif step == "nodes":
            await _run(ctx, ["kubectl", "wait", "--for=condition=Ready",
                             "nodes", "--all", "--timeout=10m"])
        elif step == "argocd":
            await _wait_argocd(ctx)
        else:
            await _wait_wireguard(ctx)


async def _down(ctx: _Ctx) -> None:
    _set_step(ctx, "checks")
    if await cluster_exists(ctx.cloud) is not False:
        await _fetch_kubeconfig(ctx)
        out = await _run(ctx, ["kubectl", "get", "svc", "-A", "-o", f"jsonpath={_LB_JSONPATH}"])
        if out.strip():
            raise _StepError(f"Delete LoadBalancer Services first: {out.strip()}")
    _set_step(ctx, "destroy")
    await _terraform(ctx, "destroy")


async def run_operation(cloud: TargetCloud, operation: str) -> None:
    """Never raises: any failure ends in finish(ok=False)."""
    tmp = tempfile.mkdtemp(prefix="cluster-op-")
    log = None
    try:
        with SessionLocal() as db:
            log_path = Path(db.get(Cluster, cloud).log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a")
        ctx = _Ctx(cloud, operation, Path(settings.CLUSTER_TF_ROOT) / _DIRS[cloud],
                   _env(cloud, f"{tmp}/kubeconfig"), log)
        if cloud == TargetCloud.GCP:
            await _run(ctx, ["gcloud", "auth", "activate-service-account",
                             "--key-file", settings.CLUSTER_OPS_GCP_SA_KEY_PATH])
        await (_up if operation == "up" else _down)(ctx)
        error = None
    except _StepError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - the state must always leave "busy"
        error = f"Unexpected error: {exc}"
    finally:
        if log:
            log.close()
        shutil.rmtree(tmp, ignore_errors=True)
    with SessionLocal() as db:
        state.finish(db, cloud, ok=error is None, error=error)
