import asyncio
import contextlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.models.project_move import STEP_NAMES, ProjectMove
from app.services import project_registry
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
)
from app.services.moves import active_move, prepull, steps
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
RESTORE = {
    "app": "shop",
    "env": "prod",
    "generation": 3,
    "sourceServer": "alpha-shop-backend-db-r2",
}


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


def _pod(name, owner="ReplicaSet"):
    return {"metadata": {"name": name, "ownerReferences": [{"kind": owner}]}}


def _cluster(name, backups=True, archiving=True):
    plugins = [{"name": PLUGIN}] if backups else []
    condition = {"type": "ContinuousArchiving", "status": str(archiving)}
    return {
        "metadata": {"name": name},
        "spec": {"plugins": plugins},
        "status": {"conditions": [condition]},
    }


def _vaultsecret(role):
    return {"metadata": {"name": "s"}, "spec": {"vaultRole": role}}


def _app(name, dest, sync="Synced", health="Healthy"):
    compared = {"destination": {"name": dest}}
    return {
        "metadata": {"name": name},
        "spec": {
            "destination": {"name": dest},
            "sources": [{"repoURL": "chart"}, {"ref": "values"}],
        },
        "status": {
            "sync": {
                "status": sync,
                "comparedTo": compared,
                "revisions": ["chart-sha", "old-sha"],
            },
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


def _pods(ns):
    return f"{NAMESPACES}/{ns}/pods"


def _secrets(ns):
    return f"/apis/ricoberger.de/v1alpha1/namespaces/{ns}/vaultsecrets"


def _secrets_core(ns):
    return f"{NAMESPACES}/{ns}/secrets"


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
        obj = self.get(path, cloud=cloud)
        if obj is None:
            raise KubeUnavailableError(f"PATCH {path}: HTTP 404", 404)
        obj.setdefault("spec", {}).update(body.get("spec", {}))
        obj["metadata"].setdefault("annotations", {}).update(
            body.get("metadata", {}).get("annotations", {})
        )
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
        AWS,
        _pods("alpha-shop"),
        _pod("shop-backend-5d9f"),
        _pod("alpha-shop-backend-db-1", owner="Cluster"),
    )
    kube.add(
        AWS, f"{CNPG}/alpha-shop/clusters", _cluster("alpha-shop-backend-db")
    )
    kube.add(
        AWS,
        _secrets("alpha-system"),
        _vaultsecret("project-alpha-system-role"),
    )
    kube.add(AWS, _secrets("alpha-shop"), _vaultsecret("alpha-shop-role"))
    kube.add(
        ONPREM,
        f"{ARGOCD}/applications",
        _app("alpha-appproject", "onprem"),
        _app("alpha-image-updaters", "in-cluster"),
        _app("alpha-bootstrap", "gcp"),
        _app("alpha-shop-backend", "aws"),
    )
    # The bootstrap already runs on the target: prepare has what it waits for.
    kube.add(GCP, NAMESPACES, _ns("alpha-system"), _ns("alpha-shop"))
    kube.add(GCP, _secrets("alpha-shop"), _vaultsecret("alpha-shop-role"))
    kube.add(GCP, _secrets_core("alpha-shop"), {"metadata": {"name": "s"}})
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
        includes=mock.AsyncMock(return_value=False),
        role_exists=mock.AsyncMock(return_value=True),
        event=mock.Mock(side_effect=lambda *a, **_k: log.append(("event",))),
        notify=mock.Mock(),
        notify_users=mock.Mock(),
        prepull_pending=mock.Mock(return_value=[]),
        prepull_cleanup=mock.Mock(),
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
            notify_users=fakes.notify_users,
        ),
        mock.patch.object(prepull, "pending", fakes.prepull_pending),
        mock.patch.object(prepull, "cleanup", fakes.prepull_cleanup),
        mock.patch.object(project_registry, "set_move", fakes.set_move),
        mock.patch.object(
            project_registry, "switch_cloud", fakes.switch_cloud
        ),
        mock.patch.object(restore_commit, "read_values", fakes.read_values),
        mock.patch.object(restore_commit, "commit_values_patch", fakes.commit),
        mock.patch.object(restore_commit, "includes", fakes.includes),
        mock.patch.object(
            steps.vault_client, "app_role_exists", fakes.role_exists
        ),
    ):
        yield fakes


def _row(db, done=(), restores=None, status="running"):
    move = steps.new_move(db, db.query(Project).one(), "gcp", "hugo")
    move.steps = [
        (
            {**s, "status": "done", "started_at": T0, "finished_at": T0}
            if s["name"] in done
            else s
        )
        for s in move.steps
    ]
    move.restores = None if restores is None else list(restores)
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


def _backend(kube):
    return kube.get(f"{ARGOCD}/applications/alpha-shop-backend")


# ── start_checks ────────────────────────────────────────────────────────────


def _checks(
    db,
    states=None,
    confirm=False,
    room=None,
    target="public",
    source=None,
    record=None,
):
    states = states or {AWS: "up", GCP: "up"}
    if source:
        db.query(Project).one().target_cloud = source
        db.commit()
    with (
        mock.patch.object(steps, "fresh_state", lambda c: states[c]),
        mock.patch.object(steps, "_no_room", return_value=room),
        mock.patch.object(
            project_registry,
            "read_record",
            mock.AsyncMock(return_value=(record or {"spec": {}}, "sha")),
        ),
    ):
        return asyncio.run(
            steps.start_checks(db, db.query(Project).one(), confirm, target)
        )


def test_target_public_from_aws_is_gcp(db, world):
    assert _checks(db) == "gcp"


def test_target_onprem_from_aws(db, world):
    # fresh_state would raise KeyError for on-prem: it is never asked.
    assert _checks(db, target="onprem") == "onprem"


def test_target_public_from_onprem_picks_up_cloud(db, world):
    assert _checks(db, {AWS: "down", GCP: "up"}, source=ONPREM) == "gcp"

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, {AWS: "down", GCP: "down"}, source=ONPREM)
    assert exc.value.status_code == 409


def test_target_onprem_from_onprem_refused(db, world):
    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, target="onprem", source=ONPREM)

    assert exc.value.status_code == 409


def test_refuses_obc_in_app_namespace(db, world):
    world.kube.add(ONPREM, NAMESPACES, _ns("alpha-optispace"))
    world.kube.add(
        ONPREM,
        "/apis/objectbucket.io/v1alpha1/namespaces/alpha-optispace"
        "/objectbucketclaims",
        {"metadata": {"name": "db-backup"}},
    )

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, source=ONPREM)

    assert exc.value.status_code == 409
    assert (
        exc.value.detail
        == "Move the backups of optispace to alpha-backups first."
    )
    world.set_move.assert_not_called()


def test_refuses_keycloak_to_public(db, world):
    record = {"spec": {"features": {"keycloak": True}}}

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, source=ONPREM, record=record)

    assert exc.value.status_code == 409
    assert exc.value.detail == "Projects with their own Keycloak stay on-prem."


@pytest.mark.parametrize(
    "states", [{AWS: "up", GCP: "down"}, {AWS: "up", GCP: "starting"}]
)
def test_start_refuses_unless_the_target_is_up(db, states):
    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, states)

    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["running", "failed"])
def test_start_refuses_a_second_move(db, status):
    _row(db, status=status)

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db)

    assert exc.value.status_code == 409


def test_imported_data_needs_confirmation(db, world):
    _app_row(db, "cache", template="import-backing")
    _app_row(db, "legacy", template="import-service")
    _app_row(db, "shop")

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db)

    assert exc.value.status_code == 409
    assert "cache" in exc.value.detail and "legacy" in exc.value.detail
    assert "shop" not in exc.value.detail
    assert _checks(db, confirm=True) == "gcp"


def test_databases_without_a_restore_need_confirmation(db, world):
    kube = world.kube
    # Staging databases start empty; so does one without backups.
    kube.add(AWS, NAMESPACES, _ns("alpha-shop-stg"), _ns("alpha-blog"))
    kube.add(AWS, f"{CNPG}/alpha-shop-stg/clusters", _cluster("alpha-shop-db"))
    kube.add(
        AWS,
        f"{CNPG}/alpha-blog/clusters",
        _cluster("alpha-blog-backend-db", backups=False),
    )

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db)

    assert exc.value.detail == "Data in blog, shop (staging) will be lost."
    assert _checks(db, confirm=True) == "gcp"


def test_new_move_has_the_eight_steps_pending(db):
    move = steps.new_move(db, db.query(Project).one(), "gcp", "hugo")

    assert (move.source, move.target, move.status) == ("aws", "gcp", "running")
    assert [s["name"] for s in move.steps] == list(STEP_NAMES)
    assert {s["status"] for s in move.steps} == {"pending"}
    assert move.restores is None


# ── advance ─────────────────────────────────────────────────────────────────


def test_a_move_runs_its_steps_in_order(db, world):
    kube = world.kube
    move = _row(db)

    advance(db, move)  # prepare, then stop_source waits
    assert _step(move, "prepare")["status"] == "done"
    assert _step(move, "stop_source")["status"] == "running"
    assert "shop-backend" in _step(move, "stop_source")["detail"]

    kube.items[(AWS, _pods("alpha-shop"))] = []
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
    advance(db, move)  # persist_restores waits for Argo CD to deploy it
    assert _step(move, "persist_restores")["status"] == "running"

    _backend(kube)["status"]["sync"]["revisions"] = ["chart-sha", "sha"]
    advance(db, move)  # cleanup_source waits for the deletions
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
    world.commit.assert_awaited_once()
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
    move = _row(db, done=_before("wait_target"), restores=[RESTORE])
    _apps_on(world.kube, "gcp")
    _backend(world.kube)["status"]["sync"]["revisions"] = ["chart-sha", "sha"]

    advance(db, move)

    world.switch_cloud.assert_not_awaited()
    world.read_values.assert_not_awaited()
    world.set_move.assert_not_awaited()
    assert _step(move, "persist_restores")["status"] == "done"
    patch = world.commit.await_args.args[2]({})
    assert patch["db"]["restore"]["generation"] == 3


def test_prepare_computes_the_restores_before_the_source_stops(db, world):
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["status"] == "done"
    assert move.restores == [
        {
            "app": "shop",
            "env": "prod",
            "generation": 1,
            "sourceServer": "alpha-shop-backend-db",
        }
    ]
    world.switch_cloud.assert_not_awaited()


def test_staging_databases_are_not_restored(db, world):
    kube = world.kube
    kube.add(AWS, NAMESPACES, _ns("alpha-shop-stg"))
    kube.add(
        AWS,
        f"{CNPG}/alpha-shop-stg/clusters",
        _cluster("alpha-shop-backend-db"),
    )
    move = _row(db)

    advance(db, move)

    # Restores land in the values file prod and staging share.
    assert [r["env"] for r in move.restores] == ["prod"]


def test_switch_reuses_the_restores_after_a_failed_commit(db, world):
    restore = {**RESTORE, "generation": 1}
    move = _row(db, done=_before("switch"), restores=[restore])
    world.switch_cloud.side_effect = [RegistryError("boom"), "sha"]

    advance(db, move)
    assert move.status == "failed"
    assert "boom" in _step(move, "switch")["detail"]
    assert db.query(Project).one().target_cloud == AWS

    steps.retry(db, move)
    assert move.status == "running"
    assert _step(move, "switch")["status"] == "pending"
    advance(db, move)

    assert _step(move, "switch")["status"] == "done"
    world.read_values.assert_not_awaited()
    first, second = world.switch_cloud.await_args_list
    assert first.args[1]["restores"] == second.args[1]["restores"]
    assert second.args[1]["restores"] == [restore]


def test_prepare_fails_while_a_vault_role_is_missing_on_the_target(db, world):
    world.role_exists.side_effect = lambda _mount, name: (
        name != "alpha-shop-role"
    )
    move = _row(db)

    advance(db, move)

    assert move.status == "failed"
    detail = _step(move, "prepare")["detail"]
    assert "alpha-shop-role" in detail and "kubernetes-gcp" in detail
    assert "project-alpha-system-role" not in detail
    assert {c.args[0] for c in world.role_exists.await_args_list} == {
        "kubernetes-gcp"
    }


def test_the_owner_and_the_actor_hear_of_a_failed_move(db, world):
    move = _row(db)

    with mock.patch.object(steps, "_no_room", return_value="No room"):
        advance(db, move)

    users = world.notify_users.call_args.args[0]
    assert set(users) == {"u1", "hugo"}
    kwargs = world.notify_users.call_args.kwargs
    assert (kwargs["kind"], kwargs["title_key"]) == ("move", "move_failed")
    assert kwargs["params"] == {"project": "alpha", "cloud": "GCP"}


def test_stop_source_skips_the_system_and_foreign_namespaces(db, world):
    move = _row(db, done=("prepare",))

    advance(db, move)

    patched = [e[2] for e in world.log if e[0] == "patch"]
    assert patched == [f"{_deployments('alpha-shop')}/shop-backend"]
    assert all(e[1] == AWS for e in world.log if e[0] == "patch")


def test_stop_source_waits_for_the_pods_to_go(db, world):
    kube = world.kube
    kube.get(f"{_deployments('alpha-shop')}/shop-backend", cloud=AWS)[
        "status"
    ]["readyReplicas"] = 0
    move = _row(db, done=("prepare",))

    advance(db, move)

    # No ready replica, but a terminating pod may still write.
    step = _step(move, "stop_source")
    assert step["status"] == "running"
    assert "shop-backend-5d9f" in step["detail"]
    assert "alpha-shop-backend-db-1" not in step["detail"]

    kube.items[(AWS, _pods("alpha-shop"))] = [
        _pod("alpha-shop-backend-db-1", owner="Cluster")
    ]
    advance(db, move)
    assert _step(move, "stop_source")["status"] == "done"


def test_stop_source_ignores_finished_pods(db, world):
    kube = world.kube
    evicted = _pod("shop-backend-evicted")
    evicted["status"] = {"phase": "Failed"}
    kube.items[(AWS, _pods("alpha-shop"))] = [evicted]
    move = _row(db, done=("prepare",))

    advance(db, move)
    assert _step(move, "stop_source")["status"] == "done"

    running = _pod("shop-backend-live")
    running["status"] = {"phase": "Running"}
    kube.items[(AWS, _pods("alpha-shop"))] = [evicted, running]
    move = _row(db, done=("prepare",))
    advance(db, move)
    step = _step(move, "stop_source")
    assert step["status"] == "running"
    assert "shop-backend-live" in step["detail"]
    assert "evicted" not in step["detail"]


@pytest.mark.parametrize("status", [None, 429, 503])
def test_an_unreachable_cluster_is_a_wait(db, world, status):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    def refuse(path, cloud=ONPREM):
        raise KubeUnavailableError(f"DELETE {path}: HTTP {status}", status)

    with mock.patch.object(steps, "kube_delete", refuse):
        advance(db, move)

    assert move.status == "running"
    assert _step(move, "cleanup_source")["status"] == "running"


@pytest.mark.parametrize("status", [404, 422])
def test_a_refusing_cluster_fails_the_move(db, world, status):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    def refuse(path, cloud=ONPREM):
        raise KubeUnavailableError(f"DELETE {path}: HTTP {status}", status)

    with mock.patch.object(steps, "kube_delete", refuse):
        advance(db, move)

    assert move.status == "failed"
    assert f"HTTP {status}" in _step(move, "cleanup_source")["detail"]


@pytest.mark.parametrize("status", [401, 403])
def test_an_api_server_refusing_while_it_restarts_is_a_wait(db, world, status):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    def refuse(path, cloud=ONPREM):
        raise KubeUnavailableError(f"DELETE {path}: HTTP {status}", status)

    with mock.patch.object(steps, "kube_delete", refuse):
        advance(db, move)
        assert move.status == "running"
        assert _step(move, "cleanup_source")["status"] == "running"

        # Still refused once the grace is over: a real refusal.
        cleanup = next(s for s in move.steps if s["name"] == "cleanup_source")
        since = steps._now() - steps.REFUSAL_GRACE - timedelta(seconds=1)
        move.steps = [
            {**s, "refused_since": since.isoformat()} if s is cleanup else s
            for s in move.steps
        ]
        advance(db, move)

    assert move.status == "failed"
    assert f"HTTP {status}" in _step(move, "cleanup_source")["detail"]


def test_a_step_that_answers_again_forgets_the_refusal(db, world):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    def refuse(path, cloud=ONPREM):
        raise KubeUnavailableError(f"DELETE {path}: HTTP 403", 403)

    with mock.patch.object(steps, "kube_delete", refuse):
        advance(db, move)
    advance(db, move)

    assert "refused_since" not in _step(move, "cleanup_source")


def test_final_backup_shows_the_backup_phase(db, world):
    move = _row(db, done=_before("final_backup"))
    world.kube.add(
        AWS,
        f"{CNPG}/alpha-shop/backups",
        {
            "metadata": {"name": f"alpha-shop-backend-db-move-{move.id}"},
            "status": {"phase": "walArchivingFailing"},
        },
    )

    advance(db, move)

    assert "walArchivingFailing" in _step(move, "final_backup")["detail"]


def test_persist_waits_for_argo_to_deploy_the_commit(db, world):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("persist_restores"), restores=[RESTORE])

    advance(db, move)
    step = _step(move, "persist_restores")
    assert step["status"] == "running"
    assert "alpha-shop-backend" in step["detail"]

    _backend(world.kube)["status"]["sync"]["revisions"] = ["chart-sha", "sha"]
    advance(db, move)

    assert _step(move, "persist_restores")["status"] == "done"
    world.commit.assert_awaited_once()


def test_persist_accepts_a_later_commit_of_the_app_repo(db, world):
    _apps_on(world.kube, "gcp")
    _backend(world.kube)["status"]["sync"]["revisions"] = ["chart-sha", "c2"]
    world.includes.return_value = True
    move = _row(db, done=_before("persist_restores"), restores=[RESTORE])

    advance(db, move)

    assert _step(move, "persist_restores")["status"] == "done"
    _, revision, sha = world.includes.await_args.args
    assert (revision, sha) == ("c2", "sha")


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


def test_cleanup_keeps_backups_namespace_on_onprem_source(db, world):
    kube = world.kube
    kube.add(ONPREM, NAMESPACES, _ns("alpha-system"), _ns("alpha-shop"))
    kube.add(ONPREM, NAMESPACES, _ns("alpha-backups"))
    _apps_on(kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))
    move.source = "onprem"
    db.commit()

    advance(db, move)

    deleted = [(e[1], e[2]) for e in world.log if e[0] == "delete"]
    assert deleted == [
        (ONPREM, f"{NAMESPACES}/alpha-system"),
        (ONPREM, f"{NAMESPACES}/alpha-shop"),
    ]


def test_stop_source_ignores_backups_namespace(world):
    world.kube.add(ONPREM, NAMESPACES, _ns("alpha-shop"), _ns("alpha-backups"))

    assert steps._app_namespaces("alpha", ONPREM) == ["alpha-shop"]


def test_project_apps_excludes_appproject_only(world):
    apps = world.kube.items[(ONPREM, f"{ARGOCD}/applications")]
    apps[:] = [
        _app("alpha-appproject", "onprem"),
        _app("alpha-bootstrap", "onprem"),
        _app("alpha-shop-app", "onprem"),
    ]

    names = [a["metadata"]["name"] for a in steps._project_apps("alpha")]

    assert names == ["alpha-bootstrap", "alpha-shop-app"]


def test_precheck_reads_onprem_mount(db, world):
    for name in ("alpha-platform", "alpha"):
        world.kube.get(f"{ARGOCD}/appprojects/{name}")["spec"][
            "destinations"
        ].append({"name": "onprem", "namespace": "x"})
    move = _row(db)
    move.target = "onprem"
    db.commit()

    advance(db, move)

    assert {c.args[0] for c in world.role_exists.await_args_list} == {
        "kubernetes"
    }


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
    boot = world.kube.get(f"{ARGOCD}/applications/alpha-bootstrap")
    boot["status"]["sync"]["comparedTo"]["destination"]["name"] = "aws"

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
    _backend(world.kube)["status"]["health"]["status"] = "Degraded"
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


def _finishing(db):
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
    return move


def test_cut_over_seconds_in_the_system_event(db, world):
    move = _finishing(db)

    advance(db, move)

    assert move.status == "done"
    action, target, details = world.event.call_args.args
    assert (action, target) == ("project.move.done", "alpha")
    assert details == {"source": "aws", "target": "gcp", "cutover_seconds": 90}
    assert world.event.call_args.kwargs["project"] == "alpha"
    world.notify.assert_called_once_with(
        "alpha",
        kind="move",
        title_key="move_done",
        params={"project": "alpha", "cloud": "GCP"},
        link="/projects/alpha/settings",
    )


def test_a_lost_notification_does_not_fail_a_done_move(db, world):
    world.notify.side_effect = RuntimeError("smtp down")
    move = _finishing(db)

    advance(db, move)

    assert move.status == "done"
    assert world.set_move.await_args.args == ("alpha", None)


def test_a_project_without_backed_up_data_moves(db, world):
    kube = world.kube
    kube.items[(AWS, f"{CNPG}/alpha-shop/clusters")] = [
        _cluster("alpha-shop-backend-db", backups=False)
    ]
    kube.items[(AWS, _deployments("alpha-shop"))] = [_workload("site", 0)]
    kube.items[(AWS, _pods("alpha-shop"))] = []
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


# ── prepare: the target gets ready before the source stops ─────────────────


def _bare_target(kube):
    """The target has its namespaces but nothing of the project's secrets."""
    kube.items[(GCP, _secrets("alpha-shop"))] = []
    kube.items[(GCP, _secrets_core("alpha-shop"))] = []


def test_prepare_copies_the_vaultsecrets_to_the_target(db, world):
    kube = world.kube
    _bare_target(kube)
    source = {
        "apiVersion": "ricoberger.de/v1alpha1",
        "kind": "VaultSecret",
        "metadata": {
            "name": "s",
            "namespace": "alpha-shop",
            "uid": "x",
            "resourceVersion": "9",
            "labels": {
                "keep": "1",
                "app.kubernetes.io/instance": "alpha-shop",
            },
            "annotations": {"argocd.argoproj.io/tracking-id": "a:b"},
        },
        "spec": {"vaultRole": "alpha-shop-role", "path": "kv/x"},
    }
    kube.items[(AWS, _secrets("alpha-shop"))] = [source]
    move = _row(db)

    advance(db, move)

    copied = kube.get(f"{_secrets('alpha-shop')}/s", cloud=GCP)
    assert copied == {
        "apiVersion": "ricoberger.de/v1alpha1",
        "kind": "VaultSecret",
        "metadata": {
            "name": "s",
            "namespace": "alpha-shop",
            "labels": {"keep": "1"},
        },
        "spec": source["spec"],
    }
    # Created once; the operator has not written the Secret yet.
    assert _step(move, "prepare")["status"] == "running"
    assert "s" in _step(move, "prepare")["detail"]
    advance(db, move)
    assert len([e for e in world.log if e[0] == "create"]) == 1


def test_prepare_waits_for_the_target_namespace(db, world):
    world.kube.items[(GCP, NAMESPACES)] = [_ns("alpha-system")]
    world.kube.items[(GCP, _secrets("alpha-shop"))] = []
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["status"] == "running"
    assert "alpha-shop" in _step(move, "prepare")["detail"]
    assert not [e for e in world.log if e[0] == "create"]


def test_prepare_waits_for_the_secrets_then_ends(db, world):
    kube = world.kube
    kube.items[(GCP, _secrets_core("alpha-shop"))] = []
    move = _row(db)

    advance(db, move)
    assert _step(move, "prepare")["status"] == "running"
    assert "s" in _step(move, "prepare")["detail"]
    world.prepull_cleanup.assert_not_called()

    kube.add(GCP, _secrets_core("alpha-shop"), {"metadata": {"name": "s"}})
    advance(db, move)
    assert _step(move, "prepare")["status"] == "done"
    world.prepull_cleanup.assert_called_once_with(move)


def test_prepare_waits_for_the_bootstrap_on_the_target(db, world):
    boot = world.kube.get(f"{ARGOCD}/applications/alpha-bootstrap")
    boot["status"]["health"]["status"] = "Progressing"
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["status"] == "running"
    assert "alpha-bootstrap" in _step(move, "prepare")["detail"]


def test_prepare_waits_for_the_prepull(db, world):
    world.prepull_pending.return_value = ["pulling img on node-1"]
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["status"] == "running"
    assert _step(move, "prepare")["detail"] == "pulling img on node-1"
    world.prepull_cleanup.assert_not_called()

    world.prepull_pending.return_value = []
    advance(db, move)
    assert _step(move, "prepare")["status"] == "done"


# ── registry ApplicationSets are refreshed after each commit ────────────────


# ── cancel ──────────────────────────────────────────────────────────────────


def test_cancel_before_the_source_stops(db, world):
    move = _row(db, done=("prepare",))

    asyncio.run(steps.cancel(db, move))

    assert move.status == "cancelling"
    assert move.finished_at is None
    assert world.set_move.await_args.args == ("alpha", None)
    assert active_move(db, "alpha") is move
    with pytest.raises(steps.MoveRefused):
        asyncio.run(steps.cancel(db, move))
    assert world.set_move.await_count == 1


def _cancelling(db):
    move = _row(db, done=("prepare",))
    asyncio.run(steps.cancel(db, move))
    return move


def test_cancelling_waits_for_the_bootstrap_to_target_the_source(db, world):
    move = _cancelling(db)

    advance(db, move)

    assert move.status == "cancelling"
    assert "alpha-bootstrap" in move.detail
    assert not [e for e in world.log if e[0] == "delete"]


def test_cancelling_deletes_the_target_namespaces_then_ends(db, world):
    kube = world.kube
    kube.items[(GCP, NAMESPACES)] = [
        _ns("alpha-system"),
        _ns("alpha-shop"),
        _ns("alpha-old", deleting=True),
        _ns("alpha-backups"),
        _ns("other-web", project="other"),
    ]
    boot = kube.get(f"{ARGOCD}/applications/alpha-bootstrap")
    boot["spec"]["destination"]["name"] = "aws"
    move = _cancelling(db)

    advance(db, move)

    deleted = [e[2] for e in world.log if e[0] == "delete"]
    assert sorted(deleted) == [
        f"{NAMESPACES}/alpha-shop",
        f"{NAMESPACES}/alpha-system",
    ]
    assert all(e[1] == GCP for e in world.log if e[0] == "delete")
    assert move.status == "cancelling"

    kube.items[(GCP, NAMESPACES)] = [_ns("alpha-backups")]
    advance(db, move)
    assert move.status == "cancelled"
    assert move.finished_at is not None


def test_cancelling_waits_when_the_cluster_is_out_of_reach(db, world):
    move = _cancelling(db)
    with mock.patch.object(
        steps, "kube_get", side_effect=KubeUnavailableError("down")
    ):
        advance(db, move)

    assert move.status == "cancelling"
    assert move.detail == "down"


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


# ── a lost source ─────────────────────────────────────────


@pytest.fixture
def lost(world):
    """AWS does not answer: every call to it raises."""
    kube = world.kube

    def guard(fn):
        def call(path, *args, cloud=ONPREM, **kwargs):
            if cloud == AWS:
                raise ClusterNotConnectedError(AWS)
            return fn(path, *args, cloud=cloud, **kwargs)

        return call

    with mock.patch.multiple(
        steps,
        kube_list=guard(kube.list),
        kube_get=guard(kube.get),
        kube_create=guard(kube.create),
        kube_merge_patch=guard(kube.merge_patch),
        kube_delete=guard(kube.delete),
    ):
        yield world


def test_prepare_without_the_source_derives_the_restores_from_the_values(
    db, lost
):
    lost.read_values.return_value = {
        "db": {
            "enabled": True,
            "backup": {"enabled": True},
            "restore": {"generation": 2},
        }
    }
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["status"] == "done"
    assert move.restores == [
        {
            "app": "shop",
            "env": "prod",
            "generation": 3,
            "sourceServer": "alpha-shop-backend-db-r2",
        }
    ]
    assert "unreachable" in _step(move, "prepare")["detail"]
    lost.prepull_pending.assert_not_called()
    assert not [e for e in lost.log if e[0] == "create"]


def test_forced_restores_skip_apps_without_a_backed_up_database(db, lost):
    lost.read_values.side_effect = HTTPException(409, "no database")
    move = _row(db)

    advance(db, move)

    assert move.restores == []


def test_forced_prepare_with_an_answering_source_is_as_usual(db, world):
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["detail"] is None
    world.prepull_pending.assert_called_once()
    assert move.restores[0]["generation"] == 1


def test_forced_prepare_ignores_source_problems(db, world):
    _backend(world.kube)["status"]["health"]["status"] = "Degraded"
    move = _row(db)

    advance(db, move)

    assert _step(move, "prepare")["status"] == "done"


def test_a_lost_source_skips_stop_and_backup_and_the_move_goes_on(db, lost):
    move = _row(db, done=("prepare",), restores=[RESTORE])

    advance(db, move)

    for name in ("stop_source", "final_backup"):
        step = _step(move, name)
        assert step["status"] == "skipped"
        assert "not connected" in step["detail"]
        assert step["started_at"] and step["finished_at"]
    assert db.query(Project).one().target_cloud == GCP
    assert _step(move, "wait_target")["status"] == "running"


def test_a_failed_backup_is_skipped_on_a_forced_move(db, world):
    move = _row(db, done=("prepare", "stop_source"))
    world.kube.add(
        AWS,
        f"{CNPG}/alpha-shop/backups",
        {
            "metadata": {"name": f"alpha-shop-backend-db-move-{move.id}"},
            "status": {"phase": "failed", "error": "no WireGuard"},
        },
    )

    advance(db, move)

    step = _step(move, "final_backup")
    assert step["status"] == "skipped"
    assert "no WireGuard" in step["detail"]


def test_a_stuck_backup_is_skipped_after_the_bounded_wait(db, world):
    move = _row(db, done=("prepare", "stop_source"))

    advance(db, move)
    assert _step(move, "final_backup")["status"] == "running"

    steps_ = [dict(s) for s in move.steps]
    _named = next(s for s in steps_ if s["name"] == "final_backup")
    _named["started_at"] = (
        steps.datetime.now(steps.timezone.utc)
        - steps.SOURCE_WAIT
        - timedelta(seconds=1)
    ).isoformat()
    move.steps = steps_
    db.commit()
    advance(db, move)

    assert _step(move, "final_backup")["status"] == "skipped"
    assert "waited" in _step(move, "final_backup")["detail"]


def test_forced_cutover_counts_from_the_skipped_stop(db, lost):
    move = _row(db, done=_before("cleanup_source"), restores=[RESTORE])
    _apps_on(lost.kube, "gcp")
    for name in ("stop_source", "final_backup"):
        _step(move, name).update(status="skipped", detail="lost")

    advance(db, move)

    assert _step(move, "cleanup_source")["status"] == "skipped"
    assert _step(move, "cleanup_source")["deferred"] is True
    assert _step(move, "cleanup_source")["detail"] == "On return of AWS"
    assert move.status == "done"
    assert lost.event.call_args.args[2]["cutover_seconds"] == 0


def test_forced_cleanup_runs_when_the_source_answers(db, world):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    advance(db, move)

    assert _step(move, "cleanup_source")["status"] == "running"
    assert [e for e in world.log if e[0] == "delete"]


def test_a_forced_cleanup_waits_for_the_target_before_deferring(db, lost):
    move = _row(db, done=_before("cleanup_source"))

    advance(db, move)

    assert _step(move, "cleanup_source")["status"] == "running"
    assert "Waiting for" in _step(move, "cleanup_source")["detail"]


# ── cleanup_returned ────────────────────────────────────────────────────────


def _deferred(db, project_cloud=GCP):
    move = _row(db, done=STEP_NAMES)
    move.steps = [
        (
            {**s, "status": "skipped", "deferred": True}
            if s["name"] == "cleanup_source"
            else s
        )
        for s in move.steps
    ]
    move.status = "done"
    db.query(Project).one().target_cloud = project_cloud
    db.commit()
    return move


def _returned(db, cloud=AWS):
    with mock.patch.object(
        steps, "SessionLocal", lambda: contextlib.nullcontext(db)
    ):
        return steps.cleanup_returned(cloud)


def test_return_stops_the_tunnel_and_deletes_the_namespaces(db, world):
    kube = world.kube
    kube.add(AWS, _deployments("alpha-system"), _workload("alpha-tunnel", 2))
    move = _deferred(db)

    done = _returned(db)

    assert done
    assert kube.get(f"{_deployments('alpha-system')}/alpha-tunnel", cloud=AWS)[
        "spec"
    ] == {"replicas": 0}
    deleted = [e[2] for e in world.log if e[0] == "delete"]
    assert deleted == [
        f"{NAMESPACES}/alpha-system",
        f"{NAMESPACES}/alpha-shop",
    ]
    assert _step(move, "cleanup_source")["status"] == "skipped"

    kube.items[(AWS, NAMESPACES)] = [_ns("kube-system", project=None)]
    _returned(db)

    assert _step(move, "cleanup_source")["status"] == "done"
    assert not _step(move, "cleanup_source").get("deferred")


def test_return_of_an_empty_cluster_closes_the_step(db, world):
    world.kube.items[(AWS, NAMESPACES)] = []
    move = _deferred(db)

    _returned(db)

    assert _step(move, "cleanup_source")["status"] == "done"
    assert not [e for e in world.log if e[0] == "delete"]


def test_return_without_a_tunnel_goes_on(db, world):
    _deferred(db)

    _returned(db)  # no alpha-tunnel: the 404 is ignored

    assert [e for e in world.log if e[0] == "delete"]


def test_return_never_touches_a_project_now_on_that_cloud(db, world):
    _deferred(db, project_cloud=AWS)

    assert _returned(db) == []
    assert not world.log


def test_return_never_touches_a_project_moving_to_that_cloud(db, world):
    _deferred(db)
    again = steps.new_move(db, db.query(Project).one(), "aws", "hugo")
    assert again.status == "running"

    assert _returned(db) == []
    assert not world.log


def test_return_ignores_other_clouds_and_normal_moves(db, world):
    _deferred(db)

    assert _returned(db, GCP) == []
    assert not world.log


def test_a_down_source_does_not_refuse_the_move(db, world):
    assert _checks(db, {AWS: "down", GCP: "up"}, confirm=True) == "gcp"
    assert _checks(db, {AWS: "degraded", GCP: "up"}, confirm=True) == "gcp"


def test_the_target_must_still_be_up(db, world):
    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, {AWS: "down", GCP: "degraded"}, confirm=True)

    assert exc.value.status_code == 409


def test_a_protected_project_can_move(db, world):
    # The registry label no longer refuses a move (see cleanup below).
    assert _checks(db) == "gcp"


def test_a_target_without_room_refuses_the_move(db, world):
    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, room="Not enough room on GCP: needs 1.2 CPU")

    assert exc.value.status_code == 409
    assert "Not enough room" in exc.value.detail


def test_a_lost_source_leaves_the_data_loss_to_the_confirmation(db, lost):
    _app_row(db, "legacy", template="import-service")

    with pytest.raises(steps.MoveRefused) as exc:
        _checks(db, {AWS: "down", GCP: "up"})

    assert exc.value.detail == "Data in legacy will be lost."


def test_prepare_fails_when_the_target_has_no_room(db, world):
    move = _row(db)

    with mock.patch.object(steps, "_no_room", return_value="Not enough room"):
        advance(db, move)

    assert move.status == "failed"
    assert _step(move, "prepare")["detail"] == "Not enough room"
    world.set_move.assert_not_awaited()


def test_cleanup_marks_the_namespace_before_deleting_it(db, world):
    _apps_on(world.kube, "gcp")
    move = _row(db, done=_before("cleanup_source"))

    advance(db, move)

    ns = world.kube.get(f"{NAMESPACES}/alpha-shop", cloud=AWS)
    assert ns["metadata"]["annotations"][steps.MOVE_CLEANUP] == "true"
    kinds = [e[0] for e in world.log if e[2].endswith("/alpha-shop")]
    assert kinds == ["patch", "delete"]


def test_a_cancel_marks_the_target_namespace_before_deleting_it(db, world):
    kube = world.kube
    kube.add(ONPREM, f"{ARGOCD}/applications", _app("alpha-bootstrap", "aws"))
    kube.items[(ONPREM, f"{ARGOCD}/applications")] = [
        _app("alpha-bootstrap", "aws")
    ]
    move = _row(db, status="cancelling")

    advance(db, move)

    ns = kube.get(f"{NAMESPACES}/alpha-shop", cloud=GCP)
    assert ns["metadata"]["annotations"][steps.MOVE_CLEANUP] == "true"
    kinds = [e[0] for e in world.log if e[2].endswith("/alpha-shop")]
    assert kinds == ["patch", "delete"]
