import asyncio
from datetime import date, datetime
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.finops import ProjectBudget, RecommendationState
from app.models.project import Project
from app.models.security import SecurityScan
from app.routers import deployments as deployments_router
from app.routers import scorecard as scorecard_router
from app.services import keycloak_service, monitoring_service
from app.services import scorecard as sc
from app.services.github_service import FileNotInRepoError, GitHubAppError
from app.services.keycloak_service import get_current_user
from app.services.security import store
from app.services.security.model import Audience, Category, Draft, Source, Tier
from tests.test_app_security_data import (  # noqa: F401
    add_deployment,
    session_factory,
)

MID_MONTH = date(2026, 11, 15)
LONG_README = "\n".join(f"line {i}" for i in range(21))
SHORT_README = "\n".join(f"line {i}" for i in range(20))


@pytest.fixture(autouse=True)
def fresh_cache():
    sc.clear_cache()
    yield
    sc.clear_cache()


@pytest.mark.parametrize(
    "critical,high,expected",
    [
        (0, 0, 20),
        (1, 0, 10),
        (2, 0, 0),
        (3, 0, 0),
        (0, 1, 16),
        (0, 5, 0),
        (0, 6, 0),
        (1, 2, 2),
        (1, 3, 0),
    ],
)
def test_security_loses_ten_per_critical_and_four_per_high_down_to_zero(
    critical, high, expected
):
    result = sc.security_criterion(sc.SecurityInput(True, critical, high))

    assert result.points == expected


def test_security_is_zero_with_no_scan_reason_when_nothing_was_scanned():
    result = sc.security_criterion(sc.SecurityInput(False, 0, 0))

    assert (result.points, result.reasons) == (0, ["no_scan"])


def test_security_names_the_actions_for_each_open_severity():
    result = sc.security_criterion(sc.SecurityInput(True, 1, 1))

    assert result.actions == ["fix_critical", "fix_high"]


def test_cost_is_full_when_within_the_budget_pace_boundary():
    pace = sc.budget_pace_eur(300, MID_MONTH)

    result = sc.cost_criterion(sc.CostInput(pace, 300, 0), MID_MONTH)

    assert result.points == 20


def test_cost_is_zero_just_over_the_budget_pace():
    pace = sc.budget_pace_eur(300, MID_MONTH)

    result = sc.cost_criterion(sc.CostInput(pace + 0.01, 300, 0), MID_MONTH)

    assert (result.points, result.reasons) == (0, ["over_budget_pace"])


def test_budget_pace_is_the_share_of_the_month_elapsed():
    assert sc.budget_pace_eur(300, date(2026, 11, 15)) == 150


def test_cost_is_ten_with_no_budget_reason_when_no_budget_is_set():
    result = sc.cost_criterion(sc.CostInput(999, None, 0), MID_MONTH)

    assert (result.points, result.reasons, result.actions) == (
        10,
        ["no_budget"],
        ["set_budget"],
    )


@pytest.mark.parametrize(
    "budget,recommendations,expected",
    [(100, 0, 20), (100, 1, 15), (100, 3, 5), (100, 4, 0), (100, 9, 0)]
    + [(None, 0, 10), (None, 1, 5), (None, 2, 0), (None, 5, 0)],
)
def test_cost_loses_five_per_open_recommendation_down_to_zero(
    budget, recommendations, expected
):
    result = sc.cost_criterion(
        sc.CostInput(0, budget, recommendations), MID_MONTH
    )

    assert result.points == expected


@pytest.mark.parametrize(
    "status,expected,reasons",
    [
        ("healthy", 20, []),
        ("asleep", 15, ["asleep"]),
        ("degraded", 10, ["degraded"]),
        ("down", 0, ["down"]),
        ("unknown", 10, ["unknown"]),
        ("not_connected", 10, ["not_connected"]),
    ],
)
def test_reliability_follows_the_health_state(status, expected, reasons):
    result = sc.reliability_criterion(sc.HealthInput(status, 0))

    assert (result.points, result.reasons) == (expected, reasons)


@pytest.mark.parametrize(
    "restarts,expected", [(0, 20), (5, 20), (6, 15), (40, 15)]
)
def test_reliability_loses_five_above_five_restarts_in_fifteen_minutes(
    restarts, expected
):
    result = sc.reliability_criterion(sc.HealthInput("healthy", restarts))

    assert result.points == expected


def test_reliability_never_goes_below_zero_when_down_and_restarting():
    result = sc.reliability_criterion(sc.HealthInput("down", 10))

    assert result.points == 0


@pytest.mark.parametrize(
    "readme,expected",
    [(None, 0), (SHORT_README, 10), (LONG_README, 20), ("", 10)],
)
def test_documentation_gives_ten_for_a_readme_and_ten_above_twenty_lines(
    readme, expected
):
    assert sc.documentation_criterion(readme).points == expected


def test_documentation_asks_for_a_readme_when_there_is_none():
    result = sc.documentation_criterion(None)

    assert (result.reasons, result.actions) == (["no_readme"], ["add_readme"])


def test_owner_is_ten_with_project_owner_reason():
    result = sc.owner_criterion("alice")

    assert (result.points, result.reasons) == (10, ["project_owner"])


@pytest.mark.parametrize(
    "score,letter",
    [
        (100, "A"),
        (85, "A"),
        (84, "B"),
        (70, "B"),
        (69, "C"),
        (50, "C"),
        (49, "D"),
        (30, "D"),
        (29, "E"),
        (0, "E"),
    ],
)
def test_grade_thresholds(score, letter):
    assert sc.grade_of(score) == letter


def test_assemble_sums_the_criteria_out_of_one_hundred():
    criteria = [
        sc.Criterion(key=k, points=p, reasons=[], actions=[])
        for k, p in zip("abcde", (20, 10, 15, 5, 10))
    ]

    card = sc.assemble(criteria)

    assert (card.score, card.max, card.grade) == (60, 100, "C")


def _scan(db, project="demo", app="shop"):
    db.add(
        SecurityScan(
            project=project,
            app=app,
            source="ci",
            last_run_at=datetime(2026, 11, 1),
        )
    )
    db.commit()


def _finding(db, fingerprint, tier, app="shop"):
    store.upsert_findings(
        db,
        "demo",
        [
            Draft(
                fingerprint=fingerprint,
                app=app,
                source=Source.CI,
                category=Category.DEPENDENCIES,
                tier=tier,
                audience=Audience.DEVELOPER,
                rule="CVE",
                title="t",
            )
        ],
        (Source.CI,),
        None,
        datetime(2026, 11, 1),
    )


def test_security_reader_counts_open_core_as_critical_and_important_as_high(
    session_factory,
):
    deployment_id = add_deployment(session_factory, "static")
    with session_factory() as db:
        _scan(db)
        _finding(db, "a", Tier.CORE)
        db.commit()
        deployment = db.get(sc.Deployment, deployment_id)

        data = sc._read_security(db, deployment)

    assert (data.scanned, data.critical, data.high) == (True, 1, 0)


def test_security_reader_ignores_other_apps_and_unscanned_state(
    session_factory,
):
    deployment_id = add_deployment(session_factory, "static")
    with session_factory() as db:
        _scan(db, app="other")
        deployment = db.get(sc.Deployment, deployment_id)

        data = sc._read_security(db, deployment)

    assert data.scanned is False


def healthy_card_patches():
    return [
        mock.patch.object(sc, "_read_costs", lambda deployment: (0.0, [])),
        mock.patch.object(
            sc.app_health,
            "get_kubernetes_app_health",
            mock.AsyncMock(
                return_value=monitoring_service.AppHealthResponse(
                    deployment_name="shop",
                    status="healthy",
                    aws_frontend=None,
                    openstack_backend=None,
                    components=[
                        monitoring_service.ComponentHealth(
                            name="web", state="healthy", restarts_15m=0
                        )
                    ],
                )
            ),
        ),
    ]


@pytest.fixture
def client_factory(session_factory):
    started = []

    def build(role, readme=LONG_README, with_project=True):
        deployment_id = add_deployment(session_factory, "static")
        with session_factory() as db:
            _scan(db)
            if with_project:
                db.add(Project(project_name="demo", owner_username="alice"))
                db.commit()
        app = FastAPI()
        app.include_router(scorecard_router.router, prefix="/api")

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: {"sub": "user-1"}
        if isinstance(readme, Exception):
            get_file = mock.AsyncMock(side_effect=readme)
        else:
            get_file = mock.AsyncMock(return_value=(readme, "sha"))
        patches = [
            mock.patch.object(scorecard_router, "get_file_content", get_file),
            mock.patch.object(
                deployments_router,
                "get_installation_token",
                mock.AsyncMock(return_value="tok"),
            ),
            mock.patch.object(
                keycloak_service, "get_project_role", lambda u, p: role
            ),
            *healthy_card_patches(),
        ]
        for patch in patches:
            patch.start()
        started.extend(patches)
        return TestClient(app), deployment_id, get_file

    yield build
    for patch in started:
        patch.stop()


def scorecard_url(deployment_id: int) -> str:
    return f"/api/deployments/{deployment_id}/scorecard"


def by_key(body):
    return {c["key"]: c for c in body["criteria"]}


def test_scorecard_sums_the_five_criteria_for_a_member(client_factory):
    client, deployment_id, _ = client_factory("member")

    body = client.get(scorecard_url(deployment_id)).json()

    assert (body["score"], body["max"], body["grade"]) == (80, 100, "B")


def test_scorecard_lists_every_criterion_with_its_keys(client_factory):
    client, deployment_id, _ = client_factory("member")

    body = client.get(scorecard_url(deployment_id)).json()

    assert list(by_key(body)) == [
        "security",
        "cost",
        "reliability",
        "documentation",
        "owner",
    ]


@pytest.mark.parametrize("role", ["member", "admin", "owner"])
def test_scorecard_is_allowed_for_members_and_up(client_factory, role):
    client, deployment_id, _ = client_factory(role)

    assert client.get(scorecard_url(deployment_id)).status_code == 200


@pytest.mark.parametrize("role", ["guest", None])
def test_scorecard_is_refused_below_member(client_factory, role):
    client, deployment_id, _ = client_factory(role)

    assert client.get(scorecard_url(deployment_id)).status_code == 403


def test_scorecard_returns_404_for_an_unknown_app(client_factory):
    client, _, _ = client_factory("member")

    assert client.get(scorecard_url(9999)).status_code == 404


def test_a_missing_readme_scores_the_documentation_zero(client_factory):
    client, deployment_id, _ = client_factory(
        "member", readme=FileNotInRepoError("nope")
    )

    body = client.get(scorecard_url(deployment_id)).json()

    assert by_key(body)["documentation"]["reasons"] == ["no_readme"]


def test_a_failing_github_isolates_the_documentation_criterion(
    client_factory,
):
    client, deployment_id, _ = client_factory(
        "member", readme=GitHubAppError("boom")
    )

    body = client.get(scorecard_url(deployment_id)).json()

    assert (
        by_key(body)["documentation"]["reasons"] == ["source_unavailable"]
        and by_key(body)["security"]["points"] == 20
        and body["score"] == 60
    )


def test_a_failing_cost_source_isolates_the_cost_criterion(client_factory):
    client, deployment_id, _ = client_factory("member")

    def boom(deployment):
        raise RuntimeError("victoria down")

    with mock.patch.object(sc, "_read_costs", boom):
        body = client.get(scorecard_url(deployment_id)).json()

    assert (
        by_key(body)["cost"]["reasons"] == ["source_unavailable"]
        and by_key(body)["reliability"]["points"] == 20
    )


def test_a_failing_health_source_isolates_the_reliability_criterion(
    client_factory,
):
    client, deployment_id, _ = client_factory("member")
    failing = mock.AsyncMock(side_effect=RuntimeError("kube down"))

    with mock.patch.object(
        sc.app_health, "get_kubernetes_app_health", failing
    ):
        body = client.get(scorecard_url(deployment_id)).json()

    assert (
        by_key(body)["reliability"]["reasons"] == ["source_unavailable"]
        and by_key(body)["cost"]["points"] == 10
    )


def test_a_failing_security_source_isolates_the_security_criterion(
    client_factory,
):
    client, deployment_id, _ = client_factory("member")

    def boom(db, deployment):
        raise RuntimeError("db down")

    with mock.patch.object(sc, "_read_security", boom):
        body = client.get(scorecard_url(deployment_id)).json()

    assert (
        by_key(body)["security"]["reasons"] == ["source_unavailable"]
        and by_key(body)["owner"]["points"] == 10
    )


def test_a_second_read_within_five_minutes_is_served_from_the_cache(
    client_factory,
):
    client, deployment_id, get_file = client_factory("member")

    client.get(scorecard_url(deployment_id))
    client.get(scorecard_url(deployment_id))

    assert get_file.await_count == 1


def test_the_cache_expires_after_five_minutes(client_factory, monkeypatch):
    client, deployment_id, get_file = client_factory("member")
    now = [1000.0]
    monkeypatch.setattr(sc, "_now", lambda: now[0])

    client.get(scorecard_url(deployment_id))
    now[0] += sc.CACHE_TTL_SECONDS + 1
    client.get(scorecard_url(deployment_id))

    assert get_file.await_count == 2


def test_a_degraded_scorecard_is_not_cached(client_factory):
    client, deployment_id, get_file = client_factory(
        "member", readme=GitHubAppError("boom")
    )

    client.get(scorecard_url(deployment_id))
    client.get(scorecard_url(deployment_id))

    assert get_file.await_count == 2


def test_the_cache_does_not_bypass_the_role_check(client_factory, monkeypatch):
    client, deployment_id, _ = client_factory("member")
    client.get(scorecard_url(deployment_id))
    monkeypatch.setattr(
        keycloak_service, "get_project_role", lambda u, p: None
    )

    assert client.get(scorecard_url(deployment_id)).status_code == 403


def test_open_recommendations_exclude_applied_and_ignored_ones(
    session_factory,
):
    deployment_id = add_deployment(session_factory, "static")
    with session_factory() as db:
        db.add_all(
            [
                RecommendationState(
                    rec_id=f"{deployment_id}:a",
                    project_name="demo",
                    app_id=deployment_id,
                    rec_type="a",
                    status="ignored",
                ),
                RecommendationState(
                    rec_id=f"{deployment_id}:b",
                    project_name="demo",
                    app_id=deployment_id,
                    rec_type="b",
                    status="notified",
                ),
            ]
        )
        db.commit()
        deployment = db.get(sc.Deployment, deployment_id)
        ids = [f"{deployment_id}:{n}" for n in "abc"]

        with mock.patch.object(sc, "_read_costs", lambda d: (0.0, ids)):
            data = _run(sc._read_cost(db, deployment))

    assert data.open_recommendations == 2


def _run(coro):
    return asyncio.run(coro)


def test_a_zero_budget_counts_as_no_budget(session_factory):
    deployment_id = add_deployment(session_factory, "static")
    with session_factory() as db:
        db.add(ProjectBudget(project_name="demo", monthly_amount_eur=0.0))
        db.commit()
        deployment = db.get(sc.Deployment, deployment_id)

        with mock.patch.object(sc, "_read_costs", lambda d: (5.0, [])):
            data = _run(sc._read_cost(db, deployment))

    assert data.budget_eur is None
