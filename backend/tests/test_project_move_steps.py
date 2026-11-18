import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.models.project_move import STEP_NAMES, ProjectMove
from app.services import project_registry
from app.services.kube_client import ClusterNotConnectedError
from app.services.moves import steps
from app.services.project_registry import RegistryError
from app.services.security import restore_commit

AWS, GCP, ONPREM = TargetCloud.AWS, TargetCloud.GCP, TargetCloud.ONPREM
ARGOCD = "/apis/argoproj.io/v1alpha1/namespaces/argocd"
NAMESPACES = "/api/v1/namespaces"
CNPG = "/apis/postgresql.cnpg.io/v1/namespaces"
LABEL = "cnp.3istor.com/project"
PLUGIN = "barman-cloud.cloudnative-pg.io"
VALUES = {"db": {"enabled": True, "backup": {"enabled": True}}}
T0 = "2026-11-18T10:00:00+00:00"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(
            Project(
                project_name="alpha", owner_username="u1", target_cloud=AWS
            )
        )
        session.commit()
        yield session


def _app_row(db, name, template="k3s-gitops-app", app_type="fullstack"):
    db.add(
        Deployment(
            name=name,
            template_id=template,
            provider_type=ProviderType.KUBERNETES,
            project_id="alpha",
            status=DeploymentStatus.RUNNING,
            app_config=json.dumps({"app_type": app_type}),
        )
    )
    db.commit()


def _ns(name, project="alpha", deleting=False):
    labels = {LABEL: project} if project else {}
    meta = {"name": name, "labels": labels}
    if deleting:
        meta["deletionTimestamp"] = T0
    return {"metadata": meta}


def _workload(name, replicas):
    return {
        "metadata": {"name": name},
        "spec": {"replicas": replicas},
        "status": {"readyReplicas": replicas},
    }


def _cluster(name, backups=True):
    plugins = [{"name": PLUGIN}] if backups else []
    return {"metadata": {"name": name}, "spec": {"plugins": plugins}}


def _app(name, dest, sync="Synced", health="Healthy"):
    compared = {"destination": {"name": dest}}
    return {
        "metadata": {"name": name},
        "spec": {"destination": {"name": dest}},
        "status": {
            "sync": {"status": sync, "comparedTo": compared},
            "health": {"status": health},
        },
    }


def _appproject(name, *clouds):
    return {
        "metadata": {"name": name},
        "spec": {
            "destinations": [{"name": c, "namespace": "x"} for c in clouds]
        },
    }


def _deployments(ns):
    return f"/apis/apps/v1/namespaces/{ns}/deployments"


class Kube:
    """Clusters in memory by (cloud, collection). Label selectors are ignored."""

    def __init__(self, log):
        self.items = {}
        self.log = log

    def add(self, cloud, collection, *objs):
        self.items.setdefault((cloud, collection), []).extend(objs)

    def list(self, path, params=None, cloud=ONPREM):
        return self.items.get((cloud, path), [])

    def get(self, path, params=None, cloud=ONPREM):
        collection, name = path.rsplit("/", 1)
        return next(
            (
                o
                for o in self.items.get((cloud, collection), [])
                if o["metadata"]["name"] == name
            ),
            None,
        )

    def create(self, path, body, cloud=ONPREM):
        self.log.append(
            ("create", cloud, f"{path}/{body['metadata']['name']}")
        )
        self.add(cloud, path, body)
        return body

    def merge_patch(self, path, body, cloud=ONPREM):
        self.log.append(("patch", cloud, path))
        self.get(path, cloud=cloud)["spec"].update(body["spec"])
        return {}

    def delete(self, path, cloud=ONPREM):
        self.log.append(("delete", cloud, path))


@pytest.fixture
def world(db):
    """alpha on AWS: one fullstack app with a backed-up database."""
    log = []
    kube = Kube(log)
    kube.add(
        ONPREM,
        f"{ARGOCD}/appprojects",
        _appproject("alpha-platform", "aws", "gcp"),
        _appproject("alpha", "aws", "gcp"),
    )
    kube.add(
        AWS,
        NAMESPACES,
        _ns("alpha-system"),
        _ns("alpha-shop"),
        _ns("other-web", project="other"),
        _ns("kube-system", project=None),
    )
    kube.add(AWS, _deployments("alpha-system"), _workload("keycloak", 1))
    kube.add(AWS, _deployments("alpha-shop"), _workload("shop-backend", 2))
    kube.add(AWS, _deployments("other-web"), _workload("web", 1))
    kube.add(AWS, _deployments("kube-system"), _workload("coredns", 2))
    kube.add(
        AWS, f"{CNPG}/alpha-shop/clusters", _cluster("alpha-shop-backend-db")
    )
    kube.add(
        ONPREM,
        f"{ARGOCD}/applications",
        _app("alpha-appproject", "onprem"),
        _app("alpha-image-updaters", "in-cluster"),
        _app("alpha-bootstrap", "aws"),
        _app("alpha-shop-backend", "aws"),
    )
    _app_row(db, "shop")

    def logged(name):
        return mock.AsyncMock(
            side_effect=lambda *a, **_k: log.append((name, *a)) or "sha"
        )

    fakes = SimpleNamespace(
        kube=kube,
        log=log,
        set_move=logged("set_move"),
        switch_cloud=logged("switch_cloud"),
        commit=logged("commit"),
        read_values=mock.AsyncMock(return_value=VALUES),
        event=mock.Mock(side_effect=lambda *a, **_k: log.append(("event",))),
        notify=mock.Mock(),
    )
    with (
        mock.patch.multiple(
            steps,
            kube_list=kube.list,
            kube_get=kube.get,
            kube_create=kube.create,
            kube_merge_patch=kube.merge_patch,
            kube_delete=kube.delete,
            record_system_event=fakes.event,
            notify_project=fakes.notify,
        ),
        mock.patch.object(project_registry, "set_move", fakes.set_move),
        mock.patch.object(
            project_registry, "switch_cloud", fakes.switch_cloud
        ),
        mock.patch.object(restore_commit, "read_values", fakes.read_values),
        mock.patch.object(restore_commit, "commit_values_patch", fakes.commit),
    ):
        yield fakes


def _row(db, done=(), restores=(), status="running"):
    move = steps.new_move(db, db.query(Project).one(), "gcp", "hugo")
    move.steps = [
        (
            {**s, "status": "done", "started_at": T0, "finished_at": T0}
            if s["name"] in done
            else s
        )
        for s in move.steps
    ]
    move.restores = list(restores)
    move.status = status
    db.commit()
    return move


def _before(name):
    return STEP_NAMES[: STEP_NAMES.index(name)]


def _step(move, name):
    return next(s for s in move.steps if s["name"] == name)


def advance(db, move):
    return asyncio.run(steps.advance(db, move))


def _apps_on(kube, cloud, compared=True):
    """The ApplicationSet moved the apps; *compared*: Argo CD refreshed them."""
    for app in kube.items[(ONPREM, f"{ARGOCD}/applications")]:
        if app["spec"]["destination"]["name"] == "aws":
            app["spec"]["destination"]["name"] = cloud
            if compared:
                app["status"]["sync"]["comparedTo"]["destination"][
                    "name"
                ] = cloud


# ── start_checks ────────────────────────────────────────────────────────────


def _checks(db, states=None, confirm=False):
    states = states or {AWS: "up", GCP: "up"}
    with mock.patch.object(steps, "fresh_state", lambda c: states[c]):
        return steps.start_checks(db, db.query(Project).one(), confirm)


def test_start_refuses_on_prem(db):
    db.query(Project).one().target_cloud = ONPREM
    db.commit()

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db)

    assert exc.value.status_code == 409
    assert exc.value.detail == "Moves to or from on-prem are not supported yet"


@pytest.mark.parametrize(
    "states", [{AWS: "up", GCP: "down"}, {AWS: "starting", GCP: "up"}]
)
def test_start_refuses_unless_both_clouds_are_up(db, states):
    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, states)

    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["running", "failed"])
def test_start_refuses_a_second_move(db, status):
    _row(db, status=status)

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db)

    assert exc.value.status_code == 409


def test_imported_data_needs_confirmation(db):
    _app_row(db, "cache", template="import-backing")
    _app_row(db, "legacy", template="import-service")
    _app_row(db, "shop")

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db)

    assert exc.value.status_code == 409
    assert "cache" in exc.value.detail and "legacy" in exc.value.detail
    assert "shop" not in exc.value.detail
    assert _checks(db, confirm=True) == "gcp"


def test_new_move_has_the_eight_steps_pending(db):
    move = steps.new_move(db, db.query(Project).one(), "gcp", "hugo")

    assert (move.source, move.target, move.status) == ("aws", "gcp", "running")
    assert [s["name"] for s in move.steps] == list(STEP_NAMES)
    assert {s["status"] for s in move.steps} == {"pending"}


# ── advance ─────────────────────────────────────────────────────────────────


def test_a_move_runs_its_steps_in_order(db, world):
    kube = world.kube
    move = _row(db)

    advance(db, move)  # prepare, then stop_source waits
    assert _step(move, "prepare")["status"] == "done"
    assert _step(move, "stop_source")["status"] == "running"
    assert "shop-backend" in _step(move, "stop_source")["detail"]

    kube.get(f"{_deployments('alpha-shop')}/shop-backend", cloud=AWS)[
        "status"
    ]["readyReplicas"] = 0
    advance(db, move)  # final_backup waits for its Backup
    backup = kube.get(
        f"{CNPG}/alpha-shop/backups/alpha-shop-backend-db-move-{move.id}",
        cloud=AWS,
    )
    assert backup["spec"]["method"] == "plugin"

    backup["status"] = {"phase": "completed"}
    advance(db, move)  # switch, then wait_target waits
    assert db.query(Project).one().target_cloud == GCP
    assert _step(move, "wait_target")["status"] == "running"

    _apps_on(kube, "gcp")
    advance(db, move)  # persist_restores, then cleanup_source waits
    assert _step(move, "cleanup_source")["status"] == "running"

    kube.items[(AWS, NAMESPACES)] = [_ns("kube-system", project=None)]
    advance(db, move)
    assert move.status == "done"
    assert all(s["started_at"] and s["finished_at"] for s in move.steps)

    names = [e[0] for e in world.log]
    order = ["set_move", "patch", "create", "switch_cloud", "commit", "delete"]
    assert [names.index(n) for n in order] == sorted(
        names.index(n) for n in order
    )
    assert world.log[-2][0] == "set_move" and world.log[-2][2] is None
    assert world.log[-1] == ("event",)
    restore = {
        "app": "shop",
        "env": "prod",
        "generation": 1,
        "sourceServer": "alpha-shop-backend-db",
    }
    assert world.switch_cloud.await_args.args[1]["restores"] == [restore]
    assert world.switch_cloud.await_args.args[1]["step"] == "switch"
    _, label, make_patch, actor = world.commit.await_args.args
    assert (label, actor) == ("move to gcp", "hugo")
    assert make_patch({"db": {"restore": {"generation": 7}}})["db"][
        "restore"
    ] == {
        "generation": 1,
        "sourceServer": "alpha-shop-backend-db",
        "backupID": "",
        "targetTime": "",
    }


def test_a_move_resumes_after_switch(db, world):
    restore = {
        "app": "shop",
        "env": "prod",
        "generation": 3,
        "sourceServer": "alpha-shop-backend-db-r2",
    }
    move = _row(db, done=_before("wait_target"), restores=[restore])
    _apps_on(world.kube, "gcp")

    advance(db, move)

    world.switch_cloud.assert_not_awaited()
    world.read_values.assert_not_awaited()
    world.set_move.assert_not_awaited()
    assert _step(move, "persist_restores")["status"] == "done"
    patch = world.commit.await_args.args[2]({})
    assert patch["db"]["restore"]["generation"] == 3


def test_switch_computes_restores_once(db, world):
    move = _row(db, done=_before("switch"))
    world.switch_cloud.side_effect = [RegistryError("boom"), "sha"]
    persisted = {"db": {**VALUES["db"], "restore": {"generation": 1}}}
    world.read_values.side_effect = [VALUES, persisted]

    advance(db, move)
    assert move.status == "failed"
    assert "boom" in _step(move, "switch")["detail"]
    assert move.restores[0]["generation"] == 1
    assert db.query(Project).one().target_cloud == AWS

    steps.retry(db, move)
    assert move.status == "running"
    assert _step(move, "switch")["status"] == "pending"
    advance(db, move)

    assert _step(move, "switch")["status"] == "done"
    assert world.read_values.await_count == 1
    first, second = world.switch_cloud.await_args_list
    assert first.args[1]["restores"] == second.args[1]["restores"]
    assert second.args[1]["restores"][0]["generation"] == 1


def test_stop_source_skips_the_system_and_foreign_namespaces(db, world):
    move = _row(db, done=("prepare",))

    advance(db, move)

    patched = [e[2] for e in world.log if e[0] == "patch"]
    assert patched == [f"{_deployments('alpha-shop')}/shop-backend"]
    assert all(e[1] == AWS for e in world.log if e[0] == "patch")


def test_kube_errors_keep_the_step_waiting(db, world):
    move = _row(db, done=("prepare",))

    def down(*_a, **_k):
        raise ClusterNotConnectedError(AWS)

    with mock.patch.object(steps, "kube_list", down):
        advance(db, move)

    assert move.status == "running"
    assert _step(move, "stop_source")["status"] == "running"
    assert "not connected" in _step(move, "stop_source")["detail"]


def test_a_failed_backup_fails_the_move(db, world):
    move = _row(db, done=_before("final_backup"))
    world.kube.add(
        AWS,
        f"{CNPG}/alpha-shop/backups",
        {
            "metadata": {"name": f"alpha-shop-backend-db-move-{move.id}"},
            "status": {"phase": "failed", "error": "bucket gone"},
        },
    )

    advance(db, move)

    assert move.status == "failed"
    assert "bucket gone" in _step(move, "final_backup")["detail"]
    world.switch_cloud.assert_not_awaited()


def test_cleanup_deletes_only_labelled_namespaces_on_the_source(db, world):
    kube = world.kube
    kube.add(AWS, NAMESPACES, _ns("alpha-old", deleting=True))
    kube.add(GCP, NAMESPACES, _ns("alpha-system"), _ns("alpha-shop"))
    _apps_on(kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    advance(db, move)

    deleted = [(e[1], e[2]) for e in world.log if e[0] == "delete"]
    assert deleted == [
        (AWS, f"{NAMESPACES}/alpha-system"),
        (AWS, f"{NAMESPACES}/alpha-shop"),
    ]
    assert _step(move, "cleanup_source")["status"] == "running"


def test_cleanup_never_runs_before_the_target_is_healthy(db, world):
    done = [n for n in _before("cleanup_source") if n != "wait_target"]
    move = _row(db, done=done)

    advance(db, move)  # wait_target still waits: apps are on aws

    assert not [e for e in world.log if e[0] == "delete"]
    step = _step(move, "cleanup_source")
    with pytest.raises(steps.StepFailed):
        steps.cleanup_source(db, move, step, move.steps)
    assert not [e for e in world.log if e[0] == "delete"]


def test_wait_target_counts_only_public_cloud_applications(db, world):
    kube = world.kube
    apps = kube.items[(ONPREM, f"{ARGOCD}/applications")]
    move = _row(db, done=_before("wait_target"))

    # Only on-prem Applications: nothing runs on the target yet.
    kube.items[(ONPREM, f"{ARGOCD}/applications")] = apps[:2]
    advance(db, move)
    assert _step(move, "wait_target")["status"] == "running"
    assert "gcp" in _step(move, "wait_target")["detail"]

    kube.items[(ONPREM, f"{ARGOCD}/applications")] = apps
    _apps_on(kube, "gcp")
    apps[3]["status"]["health"]["status"] = "Progressing"
    advance(db, move)
    assert "alpha-shop-backend" in _step(move, "wait_target")["detail"]
    assert "alpha-bootstrap" not in _step(move, "wait_target")["detail"]

    apps[3]["status"]["health"]["status"] = "Healthy"
    advance(db, move)
    assert _step(move, "wait_target")["status"] == "done"


def test_wait_target_waits_for_argo_to_compare_against_the_target(db, world):
    move = _row(db, done=_before("wait_target"))
    # Moved by the ApplicationSet, statuses still those of the stopped source.
    _apps_on(world.kube, "gcp", compared=False)

    advance(db, move)

    step = _step(move, "wait_target")
    assert step["status"] == "running"
    assert "alpha-bootstrap" in step["detail"]
    assert "alpha-shop-backend" in step["detail"]

    for app in world.kube.items[(ONPREM, f"{ARGOCD}/applications")]:
        app["status"]["sync"]["comparedTo"] = {
            "destination": dict(app["spec"]["destination"])
        }
    advance(db, move)
    assert _step(move, "wait_target")["status"] == "done"


def test_cleanup_checks_the_target_again_before_deleting(db, world):
    _apps_on(world.kube, "gcp")
    apps = world.kube.items[(ONPREM, f"{ARGOCD}/applications")]
    apps[3]["status"]["health"]["status"] = "Degraded"
    move = _row(db, done=_before("cleanup_source"))

    advance(db, move)

    assert not [e for e in world.log if e[0] == "delete"]
    step = _step(move, "cleanup_source")
    assert step["status"] == "running"
    assert "alpha-shop-backend" in step["detail"]


def test_prepare_waits_for_the_appprojects(db, world):
    kube = world.kube
    projects = (ONPREM, f"{ARGOCD}/appprojects")
    kube.items[projects] = [
        _appproject("alpha-platform", "aws"),
        _appproject("alpha", "aws", "gcp"),
    ]
    move = _row(db)

    advance(db, move)
    assert _step(move, "prepare")["status"] == "running"
    assert "alpha-platform" in _step(move, "prepare")["detail"]

    # A project without apps: its tenant AppProject has no destination.
    kube.items[projects] = [
        _appproject("alpha-platform", "aws", "gcp"),
        _appproject("alpha"),
    ]
    advance(db, move)
    assert _step(move, "prepare")["status"] == "done"
    assert world.set_move.await_count == 1
    assert world.set_move.await_args.args[1] == {
        "id": move.id,
        "source": "aws",
        "target": "gcp",
        "step": "prepare",
    }


def test_cut_over_seconds_in_the_system_event(db, world):
    move = _row(db, done=_before("finish"))
    move.steps = [
        {
            **s,
            **(
                {"started_at": "2026-11-18T10:00:00+00:00"}
                if s["name"] == "stop_source"
                else {}
            ),
            **(
                {"finished_at": "2026-11-18T10:01:30+00:00"}
                if s["name"] == "wait_target"
                else {}
            ),
        }
        for s in move.steps
    ]
    db.commit()

    advance(db, move)

    assert move.status == "done"
    action, target, details = world.event.call_args.args
    assert (action, target) == ("project.move", "alpha")
    assert details == {"source": "aws", "target": "gcp", "cutover_seconds": 90}
    assert world.event.call_args.kwargs["project"] == "alpha"
    world.notify.assert_called_once_with(
        "alpha", kind="move", title_key="moveDone", params={"target": "gcp"}
    )


def test_a_project_without_backed_up_data_moves(db, world):
    kube = world.kube
    kube.items[(AWS, f"{CNPG}/alpha-shop/clusters")] = [
        _cluster("alpha-shop-backend-db", backups=False)
    ]
    kube.items[(AWS, _deployments("alpha-shop"))] = [_workload("site", 0)]
    move = _row(db)

    advance(db, move)  # switch, then wait_target waits for Argo CD
    assert _step(move, "wait_target")["status"] == "running"
    _apps_on(kube, "gcp")
    advance(db, move)  # cleanup_source waits for the deletions
    kube.items[(AWS, NAMESPACES)] = []
    advance(db, move)

    assert move.status == "done"
    assert not [e for e in world.log if e[0] == "create"]
    assert move.restores == []
    assert world.switch_cloud.await_args.args[1]["restores"] == []
    world.read_values.assert_not_awaited()
    world.commit.assert_not_awaited()


# ── cancel ──────────────────────────────────────────────────────────────────


def test_cancel_before_the_source_stops(db, world):
    move = _row(db, done=("prepare",))

    asyncio.run(steps.cancel(db, move))

    assert move.status == "cancelled"
    assert world.set_move.await_args.args == ("alpha", None)
    with pytest.raises(steps.MoveRefused):
        asyncio.run(steps.cancel(db, move))
    assert world.set_move.await_count == 1


@pytest.mark.parametrize("status", ["running", "done", "failed"])
def test_cancel_refused_once_the_source_stops(db, world, status):
    move = _row(db, done=("prepare",))
    move.steps = [
        {**s, "status": status} if s["name"] == "stop_source" else s
        for s in move.steps
    ]
    db.commit()

    with pytest.raises(steps.MoveRefused) as exc:
        asyncio.run(steps.cancel(db, move))

    assert exc.value.status_code == 409
    assert exc.value.detail == "The source is already stopped: retry instead"
    world.set_move.assert_not_awaited()
    assert db.get(ProjectMove, move.id).status == "running"
