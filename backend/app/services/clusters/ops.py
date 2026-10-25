"""Run the equivalent of Cloud/terraform/public/{aws/EKS,gcp/GKE}/cnp.sh up/down."""

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cluster import Cluster
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.services.clusters import state
from app.services.clusters.progress import is_resource_done, plan_total
from app.services.kube_client import KubeUnavailableError, kube_get
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
    step: str = ""
    done: int = 0

    @property
    def kubeconfig(self) -> Path:
        return Path(self.env["KUBECONFIG"])


async def _stream(cmd: list[str], cwd: Path, env: dict, log, on_line, on_err=None) -> int:
    """stdout goes to on_line, stderr to on_err: CLIs print warnings on stderr. Both are logged."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def pump(stream, callback) -> None:
        async for raw in stream:
            line = raw.decode(errors="replace").rstrip("\n")
            if log:
                log.write(line + "\n")
                log.flush()
            if callback:
                callback(line)

    try:
        await asyncio.gather(pump(proc.stdout, on_line), pump(proc.stderr, on_err))
        return await proc.wait()
    except BaseException:  # incl. cancellation (timeouts): do not orphan the process
        if proc.returncode is None:
            proc.kill()
        raise


def _env(cloud: TargetCloud, tmp: str) -> dict:
    """tmp: a writable per-operation dir; the pod's uid has no home and gcloud needs a config dir."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": tmp,
        "CLOUDSDK_CONFIG": f"{tmp}/gcloud",
        "TF_IN_AUTOMATION": "1",
        "TF_PLUGIN_CACHE_DIR": "/app/cloud/.plugin-cache",
        "KUBECONFIG": f"{tmp}/kubeconfig",
        "VAULT_ADDR": settings.VAULT_URL,
        "VAULT_TOKEN": settings.CLUSTER_OPS_VAULT_TOKEN,
    }
    # The GKE root also uses the AWS provider (S3 endpoint registry): AWS creds for both.
    env |= {
        "AWS_ACCESS_KEY_ID": settings.CLUSTER_OPS_AWS_ACCESS_KEY_ID,
        "AWS_SECRET_ACCESS_KEY": settings.CLUSTER_OPS_AWS_SECRET_ACCESS_KEY,
        "AWS_REGION": "eu-west-3",
        "AWS_PAGER": "",
    }
    if cloud == TargetCloud.AWS:
        tf_vars = settings.CLUSTER_TF_VARS_AWS
    else:
        env |= {
            "GOOGLE_APPLICATION_CREDENTIALS": settings.CLUSTER_OPS_GCP_SA_KEY_PATH,
            # gcloud ignores GOOGLE_APPLICATION_CREDENTIALS; this authenticates it without activation.
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": settings.CLUSTER_OPS_GCP_SA_KEY_PATH,
            "CLOUDSDK_CORE_PROJECT": settings.CLUSTER_OPS_GCP_PROJECT,
        }
        tf_vars = settings.CLUSTER_TF_VARS_GCP
    for k, v in json.loads(tf_vars).items():
        env[f"TF_VAR_{k}"] = v if isinstance(v, str) else json.dumps(v)
    return env


async def cluster_exists(cloud: TargetCloud) -> bool | None:
    """None when the lookup itself failed (not a "not found")."""
    name = _CLUSTER_NAMES[cloud]
    cmd = (
        ["aws", "eks", "describe-cluster", "--name", name]
        if cloud == TargetCloud.AWS
        else ["gcloud", "container", "clusters", "list",
              f"--filter=name={name}", "--format=value(name)"]
    )
    out: list[str] = []
    err: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="cluster-exists-") as tmp:
            rc = await asyncio.wait_for(
                _stream(cmd, Path(settings.CLUSTER_TF_ROOT), _env(cloud, tmp), None,
                        out.append, err.append),
                timeout=30,
            )
    except asyncio.TimeoutError:
        return None
    if cloud == TargetCloud.GCP:  # list exits 0 with empty stdout when absent
        return None if rc else bool("".join(out).strip())
    if rc == 0:
        return True
    return False if "ResourceNotFoundException" in "\n".join(err) else None


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
    """Run a command, return its stdout; raise _StepError on non-zero exit."""
    lines: list[str] = []
    err: list[str] = []

    def collect(line: str) -> None:
        lines.append(line)
        if on_line:
            on_line(line)

    rc = await _stream(cmd, cwd or ctx.cwd, ctx.env, ctx.log if log else None, collect, err.append)
    if rc:
        lock = parse_state_lock("\n".join(lines + err))
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

    args = ["-no-color", "-auto-approve", "-input=false"]
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


async def _poll(ctx: _Ctx, what: str, check) -> None:
    """Transient failures (API blip, kubectl exit) are logged and retried until the deadline."""
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if await check():
                return
        except (KubeUnavailableError, _StepError) as exc:
            if ctx.log:
                ctx.log.write(f"Waiting for {what}: {exc}\n")
                ctx.log.flush()
        await asyncio.sleep(POLL_INTERVAL)
    raise _StepError(f"Timed out waiting for {what}")


def _argocd_server(cloud: TargetCloud) -> str | None:
    secret = kube_get(f"/api/v1/namespaces/argocd/secrets/cluster-{cloud.value}")
    server = ((secret or {}).get("data") or {}).get("server")
    return base64.b64decode(server).decode() if server else None


async def _wait_argocd(ctx: _Ctx) -> None:
    endpoint = (await _run(ctx, ["terraform", "output", "-no-color", "-raw", "cluster_endpoint"])).strip()
    if not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint  # GKE output has no scheme

    async def registered() -> bool:
        return await asyncio.to_thread(_argocd_server, ctx.cloud) == endpoint

    await _poll(ctx, "ArgoCD to register the cluster", registered)


async def _wait_wireguard(ctx: _Ctx) -> None:
    async def running() -> bool:
        phases = (await _run(ctx, _WG_CMD, log=False)).split()
        return bool(phases) and all(p == "Running" for p in phases)

    await _poll(ctx, "the WireGuard pod", running)


_WG_CMD = ["kubectl", "-n", "cnp-vpn", "get", "pod", "-l", "app=wireguard",
           "-o", "jsonpath={.items[*].status.phase}"]
_wg_cache: dict = {}


async def wireguard_running(cloud: TargetCloud) -> bool | None:
    """Standalone WireGuard pod check (fetches a throwaway kubeconfig); stores the result in the cache."""
    tmp = tempfile.mkdtemp(prefix="cluster-wg-")
    try:
        ctx = _Ctx(cloud, "up", Path(settings.CLUSTER_TF_ROOT) / _DIRS[cloud],
                   _env(cloud, tmp), None, step="wireguard")
        await asyncio.wait_for(_fetch_kubeconfig(ctx), timeout=20)
        phases = (await asyncio.wait_for(_run(ctx, _WG_CMD, log=False), timeout=20)).split()
        result: bool | None = bool(phases) and all(p == "Running" for p in phases)
    except Exception:  # noqa: BLE001 - health must not depend on it
        result = None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _wg_cache[cloud] = result
    return result


def wireguard_cached(cloud: TargetCloud) -> bool | None:
    return _wg_cache.get(cloud)


async def _up(ctx: _Ctx) -> None:
    for step in UP_STEPS:
        _set_step(ctx, step)
        if step == "init":
            await _run(ctx, ["terraform", "init", "-no-color", "-input=false"])
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
    await _run(ctx, ["terraform", "init", "-no-color", "-input=false"])  # .terraform is ephemeral
    if await cluster_exists(ctx.cloud) is not False:
        await _fetch_kubeconfig(ctx)
        out = await _run(ctx, ["kubectl", "get", "svc", "-A", "-o", f"jsonpath={_LB_JSONPATH}"])
        if out.strip():
            raise _StepError(f"Delete LoadBalancer Services first: {out.strip()}")
    _set_step(ctx, "destroy")
    await _terraform(ctx, "destroy")


def _check_credentials(cloud: TargetCloud) -> None:
    # The arcl-cmp-cluster-ops secret is optional in the chart: the pod starts without it.
    missing = not (settings.CLUSTER_OPS_VAULT_TOKEN and settings.CLUSTER_OPS_AWS_ACCESS_KEY_ID
                   and settings.CLUSTER_OPS_AWS_SECRET_ACCESS_KEY)
    if cloud == TargetCloud.GCP and not Path(settings.CLUSTER_OPS_GCP_SA_KEY_PATH).is_file():
        missing = True
    if missing:
        raise _StepError("Cluster credentials are not configured (secret arcl-cmp-cluster-ops)")


async def run_operation(cloud: TargetCloud, operation: str) -> None:
    """Never raises: any failure ends in finish(ok=False)."""
    tmp = tempfile.mkdtemp(prefix="cluster-op-")
    log = None
    try:
        with SessionLocal() as db:
            log_path = Path(db.get(Cluster, cloud).log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a")
        _check_credentials(cloud)
        ctx = _Ctx(cloud, operation, Path(settings.CLUSTER_TF_ROOT) / _DIRS[cloud],
                   _env(cloud, tmp), log)
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
