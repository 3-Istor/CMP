import asyncio

import pytest

from app.models.cluster import Cluster, ClusterState
from app.models.project import TargetCloud
from app.services.clusters import recovery
from tests.test_app_security_data import session_factory  # noqa: F401

AWS, GCP = TargetCloud.AWS, TargetCloud.GCP
OWN_LOCK = (
    "Error: Error acquiring the state lock\n"
    "  ID:        abc\n  Operation: OperationTypeApply\n"
    "  Who:       root@arcl-cmp-backend-old\n  Created:   2026-10-25 10:00:00\n"
)
FOREIGN_LOCK = OWN_LOCK.replace("root@arcl-cmp-backend-old", "hugo@laptop")


def test_own_stale_lock():
    assert recovery.is_own_stale_lock("root@arcl-cmp-backend-7f9-abc", "arcl-cmp-backend-7f9-xyz")


def test_foreign_lock():
    assert not recovery.is_own_stale_lock("hugo@laptop", "arcl-cmp-backend-7f9-xyz")


def test_same_pod_lock_not_stale():
    assert not recovery.is_own_stale_lock("root@arcl-cmp-backend-1", "arcl-cmp-backend-1")


@pytest.fixture
def env(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "SessionLocal", session_factory)
    monkeypatch.setattr(recovery.settings, "CLUSTER_TF_ROOT", str(tmp_path))
    monkeypatch.setattr(recovery.settings, "CLUSTER_OPS_VAULT_TOKEN", "")
    monkeypatch.setattr(recovery, "_last_reconcile", {})
    monkeypatch.setattr(recovery, "_last_renew", None)
    monkeypatch.setattr(recovery.socket, "gethostname", lambda: "arcl-cmp-backend-new")
    calls = {"run": [], "cmds": []}

    async def run_operation(cloud, op):
        calls["run"].append((cloud, op))

    monkeypatch.setattr(recovery, "run_operation", run_operation)

    def script(plan_output):
        async def stream(cmd, cwd, env, log, on_line):
            calls["cmds"].append(cmd)
            if cmd[1] == "plan":
                for line in plan_output.splitlines():
                    on_line(line)
            return 0

        monkeypatch.setattr(recovery, "_stream", stream)

    script("")
    return session_factory, tmp_path, calls, script


def _add(factory, tmp_path, st, attempts, cloud=AWS):
    with factory() as db:
        db.add(Cluster(cloud=cloud, state=st, operation="up", attempts=attempts,
                       log_path=str(tmp_path / "op.log")))
        db.commit()


def _row(factory, cloud=AWS):
    with factory() as db:
        return db.get(Cluster, cloud)


def _resume():
    async def go():
        await recovery.resume_interrupted()
        await asyncio.sleep(0)

    asyncio.run(go())


def test_resume_retries_once(env):
    factory, tmp, calls, _ = env
    _add(factory, tmp, ClusterState.STARTING, 1)
    _resume()
    assert _row(factory).attempts == 2
    assert calls["run"] == [(AWS, "up")]


def test_resume_releases_own_lock(env):
    factory, tmp, calls, script = env
    script(OWN_LOCK)
    _add(factory, tmp, ClusterState.STARTING, 1)
    _resume()
    assert ["terraform", "force-unlock", "-force", "abc"] in calls["cmds"]
    assert "Released stale lock abc" in (tmp / "op.log").read_text()
    assert calls["run"]


def test_resume_gives_up_after_two(env):
    factory, tmp, calls, _ = env
    _add(factory, tmp, ClusterState.STARTING, 2)
    _resume()
    row = _row(factory)
    assert (row.state, row.last_error) == (ClusterState.ERROR, "Interrupted twice")
    assert calls["run"] == []


def test_resume_foreign_lock_goes_error(env):
    factory, tmp, calls, script = env
    script(FOREIGN_LOCK)
    _add(factory, tmp, ClusterState.STARTING, 1)
    _resume()
    assert _row(factory).state == ClusterState.ERROR
    assert not any(c[1] == "force-unlock" for c in calls["cmds"])
    assert calls["run"] == []


def _reconcile(factory, exists):
    async def fake(cloud):
        return exists

    recovery.cluster_exists = fake
    with factory() as db:
        asyncio.run(recovery.reconcile(db))


def test_reconcile_skips_in_flight(env, monkeypatch):
    factory, tmp, *_ = env
    _add(factory, tmp, ClusterState.STARTING, 1)
    monkeypatch.setattr(recovery, "cluster_exists", None)
    _reconcile(factory, False)
    assert _row(factory).state == ClusterState.STARTING


def test_reconcile_fixes_drift(env, monkeypatch):
    factory, tmp, *_ = env
    monkeypatch.setattr(recovery, "cluster_exists", None)
    _add(factory, tmp, ClusterState.UP, 0)
    _reconcile(factory, False)
    assert _row(factory).state == ClusterState.DOWN
    assert _row(factory, GCP).state == ClusterState.DOWN  # missing row created


def test_reconcile_unknown_keeps_state(env, monkeypatch):
    factory, tmp, *_ = env
    monkeypatch.setattr(recovery, "cluster_exists", None)
    _add(factory, tmp, ClusterState.UP, 0)
    _reconcile(factory, None)
    assert _row(factory).state == ClusterState.UP


def test_vault_renewal_skipped_within_24h(env, monkeypatch):
    monkeypatch.setattr(recovery.settings, "CLUSTER_OPS_VAULT_TOKEN", "tok")
    posts = []

    class Resp:
        status_code = 200

    class Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers):
            posts.append((url, headers))
            return Resp()

    monkeypatch.setattr(recovery.httpx, "AsyncClient", Client)

    async def twice():
        await recovery.renew_vault_token()
        await recovery.renew_vault_token()

    asyncio.run(twice())
    assert len(posts) == 1
    assert posts[0][0].endswith("/v1/auth/token/renew-self")
    assert posts[0][1] == {"X-Vault-Token": "tok"}
