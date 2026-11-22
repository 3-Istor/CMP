import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.models.project import TargetCloud
from app.services import kube_client
from app.services.finops.ingest.inputs import NodeRates
from app.services.imports import estimate, generate

PLAN = json.loads(
    (
        Path(__file__).parent / "fixtures" / "imports" / "epitweet-plan.json"
    ).read_text()
)
# 0.01 EUR per core-hour and per GiB-hour.
RATE = NodeRates(0.01, 0.01)


@pytest.fixture(autouse=True)
def rates(monkeypatch):
    monkeypatch.setattr(
        estimate.inputs, "cluster_rates", lambda usd: {"onprem": RATE}
    )


def _estimate(edits=None, deployed=None, dismissed=frozenset(), plan=PLAN):
    return estimate.estimate(
        plan,
        edits,
        TargetCloud.ONPREM,
        None,
        0,
        deployed or {},
        set(dismissed),
        None,
    )


def test_the_epitweet_plan_shows_its_total_ram_and_monthly_cost():
    result = _estimate()

    total = result["total"]
    specs = generate.resolve(PLAN)
    assert total["requests"]["mem_mib"] == sum(
        s.requests_mem_mib for s in specs
    )
    expected = sum(
        RATE.hourly_eur(
            generate.millicpu(s.cpu) / 1000, s.requests_mem_mib / 1024
        )
        * 730
        for s in specs
    )
    assert total["monthly_eur"] == pytest.approx(expected, abs=0.1)


def test_each_app_shows_its_requests_and_limits():
    rows = {r["app"]: r for r in _estimate()["apps"]}

    redis = rows["redis"]

    assert (redis["requests"], redis["limits"], redis["estimated"]) == (
        {"cpu_m": 50, "mem_mib": 64},
        {"cpu_m": 250, "mem_mib": 256},
        False,
    )
    assert rows["user-service"]["limits"]["cpu_m"] == 1000


def test_a_plan_over_the_project_quota_is_flagged_before_apply(monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_MAX_PROJECT_REQUESTS_MIB", 512)

    result = _estimate()

    assert any(
        "the project may use 512 MiB" in p for p in result["quota"]["problems"]
    )


def test_without_finops_prices_the_cost_is_unknown(monkeypatch):
    def missing(usd):
        raise FileNotFoundError("onprem-tco.yaml")

    monkeypatch.setattr(estimate.inputs, "cluster_rates", missing)

    result = _estimate()

    assert (result["priced"], result["total"]["monthly_eur"]) == (False, None)


def test_a_reimport_without_change_has_an_empty_diff():
    deployed = {s.name: s.to_dict() for s in generate.resolve(PLAN)}

    result = _estimate(deployed=deployed)

    assert result["diff"] == {"added": [], "removed": [], "changed": []}


def test_a_reimport_shows_added_removed_and_changed_apps():
    deployed = {s.name: s.to_dict() for s in generate.resolve(PLAN)}
    deployed["legacy"] = deployed.pop("search-service")

    result = _estimate(
        {
            "memory": {
                "user-service": {
                    "requests_mem_mib": 256,
                    "limits_mem_mib": 512,
                }
            }
        },
        deployed=deployed,
    )

    assert result["diff"]["added"] == ["search-service"]
    assert result["diff"]["removed"] == ["legacy"]
    assert {"app": "user-service", "field": "requests_mem_mib"}.items() <= (
        result["diff"]["changed"][0].items()
    )


def test_a_first_import_has_no_diff():
    assert _estimate()["diff"] is None


def test_a_plan_with_an_unsupported_backing_is_estimated_without_it():
    plan = json.loads(json.dumps(PLAN))
    plan["backing"][0] = {**plan["backing"][0], "supported": False}

    result = _estimate(plan=plan)

    assert plan["backing"][0]["name"] not in {r["app"] for r in result["apps"]}
    assert result["plan_problems"]


def test_the_gain_of_the_recommendations_not_ignored_is_shown():
    plan = {
        **PLAN,
        "recommendations": [
            {"id": "a", "gain_mem_mib": 1024},
            {"id": "b", "gain_mem_mib": 512},
            {"id": "c", "gain_mem_mib": None},
        ],
    }

    result = _estimate(plan=plan, dismissed={"b"})

    assert result["recommendations_gain_mib"] == 1024


def test_free_cluster_capacity_is_allocatable_minus_requests(monkeypatch):
    estimate._cluster_cache.clear()

    def kube_list(path, params=None, cloud=None):
        if path == "/api/v1/nodes":
            return [
                {"status": {"allocatable": {"cpu": "4", "memory": "8Gi"}}},
                {
                    "spec": {"unschedulable": True},
                    "status": {"allocatable": {"cpu": "4", "memory": "8Gi"}},
                },
            ]
        return [
            {
                "spec": {
                    "containers": [
                        {
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "1Gi"}
                            }
                        },
                        {"resources": {}},
                    ]
                }
            }
        ]

    monkeypatch.setattr(kube_client, "kube_list", kube_list)

    found = estimate.cluster_capacity(TargetCloud.ONPREM)

    assert found["free"] == {"cpu_m": 3500, "mem_mib": 7168}


def test_an_unreachable_cluster_leaves_the_capacity_out(monkeypatch):
    estimate._cluster_cache.clear()

    def down(*args, **kwargs):
        raise kube_client.KubeUnavailableError("down")

    monkeypatch.setattr(kube_client, "kube_list", down)

    assert estimate.cluster_capacity(TargetCloud.AWS) is None
