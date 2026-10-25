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
    """Replaces ops._stream: records commands, scripts output per command prefix."""

    def __init__(self, outputs=None, fail=None):
        self.cmds, self.outputs, self.fail = [], outputs or {}, fail or {}

    async def __call__(self, cmd, cwd, env, log, on_line):
        self.cmds.append(cmd)
        key = " ".join(cmd[:2])
        lines = self.outputs.get(key, [])
        for ln in lines:
            if log:
                log.write(ln + "\n")
            on_line(ln)
        return self.fail.get(key, 0)


@pytest.fixture
def env(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "SessionLocal", session_factory)
    monkeypatch.setattr(ops.settings, "CLUSTER_TF_ROOT", str(tmp_path))
    monkeypatch.setattr(ops, "POLL_INTERVAL", 0)
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
    assert ["terraform", "apply", "-auto-approve", "-input=false"] in fake.cmds


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
    assert not any(c[0] == "kubectl" for c in fake.cmds)


def test_lock_error_message(env, monkeypatch):
    factory, tmp_path, _ = env
    monkeypatch.setattr(ops, "_stream", Fake({"terraform init": LOCK.splitlines()}, {"terraform init": 1}))
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
