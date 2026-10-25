import asyncio

import pytest

from app.models.cluster import Cluster, ClusterState
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.services.clusters import ops, state
from tests.test_app_security_data import session_factory  # noqa: F401

AWS = TargetCloud.AWS
LOCK = (
    "Error: Error acquiring the state lock\n"
    "  ID:        abc\n  Operation: OperationTypeApply\n"
    "  Who:       bob@host\n  Created:   2026-10-25 10:00:00\n"
)


class Fake:
    """Replaces ops._stream: records commands, scripts stdout/stderr per command prefix."""

    def __init__(self, outputs=None, fail=None, errors=None):
        self.cmds, self.outputs, self.fail = [], outputs or {}, fail or {}
        self.errors = errors or {}

    async def __call__(self, cmd, cwd, env, log, on_line, on_err=None):
        self.cmds.append(cmd)
        key = " ".join(cmd[:2])
        for lines, callback in ((self.outputs.get(key, []), on_line), (self.errors.get(key, []), on_err)):
            for ln in lines:
                if log:
                    log.write(ln + "\n")
                if callback:
                    callback(ln)
        return self.fail.get(key, 0)


@pytest.fixture
def env(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "SessionLocal", session_factory)
    monkeypatch.setattr(ops.settings, "CLUSTER_TF_ROOT", str(tmp_path))
    monkeypatch.setattr(ops, "POLL_INTERVAL", 0)
    for key in ("CLUSTER_OPS_VAULT_TOKEN", "CLUSTER_OPS_AWS_ACCESS_KEY_ID", "CLUSTER_OPS_AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setattr(ops.settings, key, "x")
    steps = []
    real = state.set_step
    monkeypatch.setattr(state, "set_step", lambda db, c, s, d=0, t=0: (steps.append(s), real(db, c, s, d, t)))
    return session_factory, tmp_path, steps


def _begin(factory, tmp_path, op, current=None):
    with factory() as db:
        if current:
            db.add(Cluster(cloud=AWS, state=current))
            db.commit()
        row = state.begin(db, AWS, op, "alice")
        row.log_path = str(tmp_path / "logs" / "op.log")
        db.commit()


def _row(factory):
    with factory() as db:
        return db.get(Cluster, AWS)


def test_up_runs_steps_in_order_and_ends_up(env, monkeypatch):
    factory, tmp_path, steps = env
    fake = Fake({
        "terraform output": ["cluster-endpoint.eks.amazonaws.com"],
        "kubectl -n": ["Running"],
        "aws ssm": ["apiVersion: v1"],
    })
    monkeypatch.setattr(ops, "_stream", fake)
    monkeypatch.setattr(ops, "_argocd_server", lambda cloud: "https://cluster-endpoint.eks.amazonaws.com")
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    assert steps == list(ops.UP_STEPS)
    assert _row(factory).state == ClusterState.UP
    assert ["terraform", "apply", "-no-color", "-auto-approve", "-input=false"] in fake.cmds
    assert all("-no-color" in c for c in fake.cmds if c[0] == "terraform")


def test_up_waits_for_matching_argocd_server(env, monkeypatch):
    factory, tmp_path, _ = env
    seen = iter([None, "https://old", "https://1.2.3.4"])
    monkeypatch.setattr(ops, "_stream", Fake({"terraform output": ["1.2.3.4"], "kubectl -n": ["Running"]}))
    monkeypatch.setattr(ops, "_argocd_server", lambda cloud: next(seen))
    monkeypatch.setattr(ops, "UP_STEPS", ("argocd",))
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    assert _row(factory).state == ClusterState.UP


def test_apply_failure_sets_error_and_keeps_log(env, monkeypatch):
    factory, tmp_path, _ = env
    monkeypatch.setattr(ops, "_stream", Fake({"terraform apply": ["boom: quota exceeded"]}, {"terraform apply": 1}))
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    row = _row(factory)
    assert row.state == ClusterState.ERROR and "apply" in row.last_error
    assert "boom: quota exceeded" in (tmp_path / "logs" / "op.log").read_text()


def test_down_refused_with_loadbalancer(env, monkeypatch):
    factory, tmp_path, _ = env
    fake = Fake({"kubectl get": ["my-lb"], "aws ssm": ["cfg"]})
    monkeypatch.setattr(ops, "_stream", fake)

    async def exists(cloud):
        return True

    monkeypatch.setattr(ops, "cluster_exists", exists)
    _begin(factory, tmp_path, "down", ClusterState.UP)
    asyncio.run(ops.run_operation(AWS, "down"))
    row = _row(factory)
    assert row.state == ClusterState.ERROR and "my-lb" in row.last_error
    assert not any(c[:2] == ["terraform", "destroy"] for c in fake.cmds)


def test_down_skips_checks_when_cluster_gone(env, monkeypatch):
    factory, tmp_path, steps = env
    fake = Fake()
    monkeypatch.setattr(ops, "_stream", fake)

    async def gone(cloud):
        return False

    monkeypatch.setattr(ops, "cluster_exists", gone)
    _begin(factory, tmp_path, "down", ClusterState.UP)
    asyncio.run(ops.run_operation(AWS, "down"))
    assert steps == ["checks", "destroy"] and _row(factory).state == ClusterState.DOWN
    verbs = [c[1] for c in fake.cmds if c[0] == "terraform"]
    assert verbs.index("init") < verbs.index("destroy")
    assert not any(c[0] == "kubectl" for c in fake.cmds)


def test_lock_error_message(env, monkeypatch):
    factory, tmp_path, _ = env
    monkeypatch.setattr(ops, "_stream", Fake(fail={"terraform init": 1}, errors={"terraform init": LOCK.splitlines()}))
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    assert "Another operation is already running" in _row(factory).last_error


def test_apply_progress_counts_resources(env, monkeypatch):
    factory, tmp_path, _ = env
    monkeypatch.setattr(ops, "UP_STEPS", ("apply",))
    lines = ["Plan: 2 to add, 0 to change, 0 to destroy.", "a: Creation complete after 1s", "b: Creation complete after 1s"]
    monkeypatch.setattr(ops, "_stream", Fake({"terraform apply": lines}))
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    row = _row(factory)
    assert (row.done, row.total) == (2, 2)


def test_affected_apps(session_factory):
    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="a", target_cloud=AWS))
        db.add(Project(project_name="other", owner_username="a", target_cloud=TargetCloud.GCP))
        db.add_all([
            Deployment(name="shop", template_id="t", project_id="demo"),
            Deployment(name="gone", template_id="t", project_id="demo", status=DeploymentStatus.DELETED),
            Deployment(name="x", template_id="t", project_id="other"),
        ])
        db.commit()
        apps = ops.affected_apps(db, AWS)
    assert apps == [{"project": "demo", "app": "shop", "deployment_id": 1}]


def test_gcp_env_has_aws_credentials_and_gcloud_auth(monkeypatch):
    monkeypatch.setattr(ops.settings, "CLUSTER_OPS_AWS_ACCESS_KEY_ID", "AK")
    monkeypatch.setattr(ops.settings, "CLUSTER_OPS_GCP_SA_KEY_PATH", "/k.json")
    env = ops._env(TargetCloud.GCP, "/op")
    assert env["AWS_ACCESS_KEY_ID"] == "AK" and env["AWS_REGION"] == "eu-west-3"
    assert env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] == "/k.json"
    assert env["CLOUDSDK_CONFIG"] == "/op/gcloud" and env["HOME"] == "/op"
    assert env["KUBECONFIG"] == "/op/kubeconfig"


def test_missing_credentials_fail_clearly(env, monkeypatch):
    factory, tmp_path, _ = env
    monkeypatch.setattr(ops.settings, "CLUSTER_OPS_VAULT_TOKEN", "")
    fake = Fake()
    monkeypatch.setattr(ops, "_stream", fake)
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    row = _row(factory)
    assert row.state == ClusterState.ERROR and "credentials are not configured" in row.last_error
    assert fake.cmds == []


def test_stream_keeps_stderr_out_of_parsed_output(tmp_path):
    out, err = [], []
    log = (tmp_path / "op.log").open("w")
    rc = asyncio.run(ops._stream(["sh", "-c", "echo out; echo warn >&2"], tmp_path, {}, log, out.append, err.append))
    log.close()
    assert (rc, out, err) == (0, ["out"], ["warn"])
    assert sorted((tmp_path / "op.log").read_text().split()) == ["out", "warn"]


def test_gcp_cluster_absent_despite_stderr_notice(monkeypatch):
    fake = Fake(errors={"gcloud container": ["Listed 0 items."]})
    monkeypatch.setattr(ops, "_stream", fake)
    assert asyncio.run(ops.cluster_exists(TargetCloud.GCP)) is False


def test_wireguard_running_despite_kubectl_warning(env, monkeypatch):
    factory, tmp_path, _ = env
    warning = "E1025 memcache.go:265] couldn't get current server API group list"
    monkeypatch.setattr(ops, "_stream", Fake({"kubectl -n": ["Running"]}, errors={"kubectl -n": [warning]}))
    monkeypatch.setattr(ops, "UP_STEPS", ("wireguard",))
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    assert _row(factory).state == ClusterState.UP


def test_polls_retry_transient_errors(env, monkeypatch):
    factory, tmp_path, _ = env
    seen = iter([ops.KubeUnavailableError("blip"), "https://1.2.3.4"])

    def server(cloud):
        value = next(seen)
        if isinstance(value, Exception):
            raise value
        return value

    fake = Fake({"terraform output": ["1.2.3.4"], "kubectl -n": ["Running"]}, {"kubectl -n": 1})

    async def kubectl_fails_once(cmd, *args):
        rc = await fake(cmd, *args)
        if cmd[0] == "kubectl":
            fake.fail = {}
        return rc

    monkeypatch.setattr(ops, "_stream", kubectl_fails_once)
    monkeypatch.setattr(ops, "_argocd_server", server)
    monkeypatch.setattr(ops, "UP_STEPS", ("argocd", "wireguard"))
    _begin(factory, tmp_path, "up")
    asyncio.run(ops.run_operation(AWS, "up"))
    assert _row(factory).state == ClusterState.UP
    assert sum(c[0] == "kubectl" for c in fake.cmds) == 2
    assert "Waiting for ArgoCD" in (tmp_path / "logs" / "op.log").read_text()


@pytest.mark.parametrize("out,rc,expected", [(["cnp-gke"], 0, True), ([], 0, False), (["denied"], 1, None)])
def test_gcp_cluster_exists(monkeypatch, out, rc, expected):
    monkeypatch.setattr(ops, "_stream", Fake({"gcloud container": out}, {"gcloud container": rc}))
    assert asyncio.run(ops.cluster_exists(TargetCloud.GCP)) is expected


def test_aws_cluster_exists_not_found(monkeypatch):
    fake = Fake(fail={"aws eks": 254}, errors={"aws eks": ["An error occurred (ResourceNotFoundException)"]})
    monkeypatch.setattr(ops, "_stream", fake)
    assert asyncio.run(ops.cluster_exists(AWS)) is False


def test_cluster_exists_times_out_to_none(monkeypatch):
    async def hang(*args):
        await asyncio.sleep(60)

    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(ops, "_stream", hang)
    monkeypatch.setattr(ops.asyncio, "wait_for", lambda coro, timeout: real_wait_for(coro, 0.05))
    assert asyncio.run(ops.cluster_exists(AWS)) is None
