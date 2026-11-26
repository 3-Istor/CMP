from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.deployment import Deployment
from app.models.finops import CostLineItem
from app.models.project import Project, TargetCloud
from app.models.project_move import ProjectMove
from app.services.finops.ingest import presence, split
from app.services.finops.ingest.projects import IDLE
from app.services.finops.ingest.store import upsert
from tests.test_finops_cloud_billing import (  # noqa: F401  pylint: disable=unused-import
    _node,
    factory,
)

DAY = date(2026, 11, 22)


def test_project_without_moves_stays_on_its_cloud():
    stays = presence.stays([("todo", "aws", datetime(2026, 11, 21, 9))], [])

    assert presence.hours_present(stays, "aws", DAY) == {
        "todo": set(range(24))
    }
    assert not presence.hours_present(stays, "gcp", DAY)


def test_move_counts_on_both_clouds_while_running():
    moves = [
        (
            "todo",
            "aws",
            "gcp",
            "done",
            datetime(2026, 11, 22, 13, 14, 39),
            datetime(2026, 11, 22, 13, 23),
        )
    ]
    stays = presence.stays([("todo", "gcp", datetime(2026, 11, 20))], moves)

    assert presence.hours_present(stays, "aws", DAY) == {
        "todo": set(range(14))
    }
    assert presence.hours_present(stays, "gcp", DAY) == {
        "todo": set(range(13, 24))
    }


def test_cancelled_move_changes_nothing():
    moves = [
        (
            "todo",
            "aws",
            "gcp",
            "cancelled",
            datetime(2026, 11, 22, 13),
            datetime(2026, 11, 22, 14),
        )
    ]
    stays = presence.stays([("todo", "aws", datetime(2026, 11, 20))], moves)

    assert presence.hours_present(stays, "aws", DAY) == {
        "todo": set(range(24))
    }
    assert not presence.hours_present(stays, "gcp", DAY)


def test_shares_sum_to_total_and_idle_takes_empty_hours():
    apps = {"todo": [("api", "todo-api"), ("web", "todo-web")]}

    result = presence.shares(10.0, 10, {"todo": {1, 2, 3, 4}}, apps)

    assert result[("todo", "api", "todo-api")] == pytest.approx(2.0)
    assert result[("todo", "web", "todo-web")] == pytest.approx(2.0)
    assert result[(IDLE, None, IDLE)] == pytest.approx(6.0)
    assert sum(result.values()) == 10.0


def test_no_presence_all_idle():
    assert presence.shares(5.0, 24, {}, {}) == {(IDLE, None, IDLE): 5.0}


def test_presence_beyond_cluster_hours_is_capped():
    apps = {"todo": [(None, "todo-system")]}

    result = presence.shares(3.0, 2, {"todo": {0, 1, 2, 3}}, apps)

    assert result[("todo", None, "todo-system")] == pytest.approx(3.0)
    assert result.get((IDLE, None, IDLE), 0.0) == pytest.approx(0.0)


def test_split_day_falls_back_to_presence(factory):  # noqa: F811
    with factory() as db:
        upsert(db, [_node("aws", 6.0, day=DAY)])
        db.add(
            Project(
                project_name="todo-move",
                owner_username="hugo",
                target_cloud=TargetCloud.AWS,
                created_at=datetime(2026, 11, 20),
            )
        )
        db.add(
            Deployment(
                name="api",
                template_id="static",
                project_id="todo-move",
                k8s_namespace="todo-move-api",
            )
        )
        db.add(
            ProjectMove(
                project="todo-move",
                source="aws",
                target="gcp",
                status="cancelled",
                actor="hugo",
                created_at=datetime(2026, 11, 22, 10),
            )
        )
        db.commit()

        written = split.split_day(db, "aws", DAY)
        rows = db.scalars(
            select(CostLineItem).where(CostLineItem.x_source == "aws_split")
        ).all()

    assert written == 2
    assert sum(r.x_cost_eur for r in rows) == pytest.approx(6.0, abs=1e-12)
    assert {(r.project, r.app, r.resource_name) for r in rows} == {
        ("todo-move", "api", "todo-move-api"),
        ("todo-move", None, "todo-move-system"),
    }
    assert all(r.x_allocation_method == "presence" for r in rows)
    assert all(r.x_is_estimate for r in rows)


def test_split_day_idle_takes_cluster_hours_no_project_covers(
    factory,
):  # noqa: F811
    fee = _node("aws", 2.4, day=DAY, rid="eks-fee")
    fee.project, fee.extra_tags = None, {}
    fee.service_name = split.EKS_SERVICE
    fee.consumed_quantity, fee.consumed_unit = 12.0, "Hrs"
    with factory() as db:
        upsert(db, [_node("aws", 6.0, day=DAY), fee])
        db.add(
            Project(
                project_name="todo-move",
                owner_username="hugo",
                target_cloud=TargetCloud.AWS,
                created_at=datetime(2026, 11, 22, 18, 30),
            )
        )
        db.commit()

        split.split_day(db, "aws", DAY)
        rows = db.scalars(
            select(CostLineItem).where(CostLineItem.x_source == "aws_split")
        ).all()

    by_project = {r.project: r.x_cost_eur for r in rows}
    assert by_project["todo-move"] == pytest.approx(3.0)
    assert by_project[IDLE] == pytest.approx(3.0)
