import asyncio
from unittest import mock

import pytest

from app.models.agent import ProjectAgentSettings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.observability import SloSetting
from app.models.project import Project, TargetCloud
from app.services.kube_client import KubeUnavailableError
from app.services.observability import reconcile
from app.services.vault_client import VaultError
from tests.test_app_security_data import session_factory  # noqa: F401

HOOK = "https://discord.com/api/webhooks/1/token"


@pytest.fixture
def db(session_factory):
    with session_factory() as s:
        s.add_all(
            [
                Project(project_name="shop", owner_username="alice"),
                Project(
                    project_name="far",
                    owner_username="bob",
                    target_cloud=TargetCloud.AWS,
                ),
                ProjectAgentSettings(project_name="shop", level="demo"),
                SloSetting(
                    project="shop", app="web", availability=99.9, latency=90
                ),
            ]
        )
        for project, app, status in (
            ("shop", "web", DeploymentStatus.RUNNING),
            ("shop", "api", DeploymentStatus.RUNNING),
            ("shop", "old", DeploymentStatus.DELETED),
            ("far", "web", DeploymentStatus.RUNNING),
        ):
            s.add(
                Deployment(
                    name=app,
                    template_id="k3s-gitops-app",
                    provider_type=ProviderType.KUBERNETES,
                    project_id=project,
                    status=status,
                )
            )
        s.commit()
    with mock.patch.object(reconcile, "SessionLocal", session_factory):
        yield


def _desired(vault=None):
    read = mock.AsyncMock(side_effect=vault or (lambda p: {"project": HOOK}))
    with mock.patch.object(reconcile.targets_store, "read_targets", read):
        found = reconcile.inventory()
        return asyncio.run(
            reconcile.desired_state(found, {"shop-web", "far-web"})
        )


def test_only_routed_onprem_live_apps_get_an_slo(db):
    rules = _desired().objects[reconcile.RULES]

    assert list(rules) == ["slo-shop-web"]


def test_the_owner_target_and_the_demo_period_are_used(db):
    rule = _desired().objects[reconcile.RULES]["slo-shop-web"]
    records = {
        r.get("record"): r
        for g in rule["spec"]["groups"]
        for r in g["rules"]
        if r["labels"]["cnp_slo"] == "availability"
    }

    assert (
        "slo:sli_error:ratio_rate7d" in records,
        records["slo:objective:ratio"]["expr"],
    ) == (True, "vector(0.999)")


def test_a_vault_outage_freezes_only_that_projects_routing(db):
    def vault(project):
        if project == "shop":
            raise VaultError("down")
        return {}

    desired = _desired(vault)

    assert (desired.frozen, desired.objects[reconcile.CONFIGS]) == (
        {"shop"},
        {},
    )


def _kube(current: list[dict]):
    return {
        "kube_get": mock.Mock(return_value={"items": current}),
        "kube_create": mock.Mock(),
        "kube_replace": mock.Mock(),
        "kube_delete": mock.Mock(),
    }


def _obj(name: str, project: str = "shop", spec=None) -> dict:
    return {
        "metadata": {
            "name": name,
            "labels": {
                "cnp.3istor.com/owner": "cmp",
                "cnp.3istor.com/project": project,
            },
        },
        "spec": spec or {"a": 1},
    }


def _apply(current, wanted, frozen=frozenset()):
    calls = _kube(current)
    with mock.patch.multiple(reconcile, **calls):
        changes = reconcile.apply(reconcile.RULES, wanted, set(frozen))
    return changes, calls


def test_an_unchanged_object_is_not_written():
    wanted = _obj("slo-shop-web")
    live = reconcile._with_checksum(wanted, reconcile.RULES)
    live["metadata"]["resourceVersion"] = "7"
    live["spec"] = {"a": 1, "defaulted": "by the API server"}

    changes, _ = _apply([live], {"slo-shop-web": wanted})

    assert changes == 0


def test_a_changed_object_is_replaced_with_the_version_read():
    live = reconcile._with_checksum(_obj("slo-shop-web"), reconcile.RULES)
    live["metadata"]["resourceVersion"] = "7"

    _, calls = _apply(
        [live], {"slo-shop-web": _obj("slo-shop-web", spec={"a": 2})}
    )

    sent = calls["kube_replace"].call_args.args[1]
    assert (sent["metadata"]["resourceVersion"], sent["spec"]) == (
        "7",
        {"a": 2},
    )


def test_objects_of_removed_apps_are_deleted_unless_frozen():
    current = [_obj("slo-shop-gone"), _obj("slo-far-gone", project="far")]

    _, calls = _apply(current, {}, frozen={"far"})

    assert [
        c.args[0].rsplit("/", 1)[-1]
        for c in calls["kube_delete"].call_args_list
    ] == ["slo-shop-gone"]


def test_an_unknown_api_aborts_instead_of_deleting_everything():
    calls = _kube([])
    calls["kube_get"].return_value = None

    with mock.patch.multiple(reconcile, **calls):
        with pytest.raises(KubeUnavailableError):
            reconcile.apply(reconcile.RULES, {}, set())


def test_an_unreadable_route_list_aborts_the_sync():
    with mock.patch.object(reconcile, "kube_get", return_value=None):
        with pytest.raises(KubeUnavailableError):
            reconcile.routed_namespaces()


def test_the_flag_off_writes_nothing():
    with (
        mock.patch.object(reconcile.settings, "OBS_V3_ENABLED", False),
        mock.patch.object(reconcile, "sync") as sync,
    ):
        asyncio.run(reconcile.sync_quietly())

    assert sync.call_count == 0
