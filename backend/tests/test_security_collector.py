import asyncio
import json
from datetime import datetime, timezone
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus
from app.services.github_service import Artifact, GitHubAppError
from app.services.security import collector, settings, sources, store

REPORT = {
    "scanners": {
        "gitleaks": {
            "leaks": [
                {
                    "rule_id": "aws",
                    "file": "a.py",
                    "line": 3,
                    "fingerprint": "x",
                }
            ]
        }
    }
}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(autoflush=False, bind=engine)()
    yield session
    session.close()


@pytest.fixture
def entry(db):
    deployment = Deployment(
        name="api",
        template_id="k3s-gitops-app",
        status=DeploymentStatus.RUNNING,
        project_id="shop",
        github_repo_url="https://github.com/3-Istor/shop-api",
        app_config=json.dumps({"github_installation_id": "1"}),
    )
    db.add(deployment)
    db.commit()
    return collector.kubernetes_projects(db)[0]


def _github(artifact=None, error=None, files=None):
    if files is None:
        files = {settings.WORKFLOW_FILE: "name: Security Scans"}
    patches = {
        "get_installation_token": mock.AsyncMock(return_value="t"),
        "get_default_branch": mock.AsyncMock(return_value="main"),
        "get_latest_artifact": mock.AsyncMock(
            return_value=artifact, side_effect=error
        ),
        "_read_exposure": mock.AsyncMock(return_value="project_members"),
        "_read_optional": mock.AsyncMock(
            side_effect=lambda token, repo, path, branch: files.get(path)
        ),
    }
    return mock.patch.multiple(collector, **patches)


@pytest.mark.parametrize(
    "after, expected",
    [
        (datetime(2026, 10, 9, 12, 0), datetime(2026, 10, 12, 5, 17)),
        (datetime(2026, 10, 12, 5, 0), datetime(2026, 10, 12, 5, 17)),
        (datetime(2026, 10, 12, 6, 0), datetime(2026, 10, 19, 5, 17)),
    ],
)
def test_next_weekly_run_is_the_next_monday_morning(after, expected):
    assert collector.next_weekly_run(after) == expected


def test_kubernetes_apps_map_to_their_project_namespace(entry):
    assert entry.apps == [
        sources.AppRef("api", "shop-api", "3-Istor/shop-api")
    ]


def test_ci_findings_come_from_the_latest_default_branch_report(db, entry):
    # Arrange
    artifact = Artifact(
        "42", datetime(2026, 10, 9, tzinfo=timezone.utc), REPORT
    )

    # Act
    with _github(artifact):
        asyncio.run(collector.collect_github_app(db, entry, entry.apps[0]))

    # Assert
    assert [
        m.title
        for m in store.merged_findings(
            db, "shop", datetime(2026, 10, 9).date()
        )
    ] == ["Secret commité dans le repo"]


def test_a_github_error_keeps_known_findings_open(db, entry):
    # Arrange
    artifact = Artifact(
        "42", datetime(2026, 10, 9, tzinfo=timezone.utc), REPORT
    )
    with _github(artifact):
        asyncio.run(collector.collect_github_app(db, entry, entry.apps[0]))

    # Act
    with _github(error=GitHubAppError("HTTP 502")):
        asyncio.run(collector.collect_github_app(db, entry, entry.apps[0]))

    # Assert
    assert (
        len(store.merged_findings(db, "shop", datetime(2026, 10, 9).date()))
        == 1
    )


def test_an_unchanged_report_is_not_downloaded_again_nor_resolved(db, entry):
    # Arrange
    artifact = Artifact(
        "42", datetime(2026, 10, 9, tzinfo=timezone.utc), REPORT
    )
    with _github(artifact):
        asyncio.run(collector.collect_github_app(db, entry, entry.apps[0]))
    known = Artifact("42", artifact.created_at, None)

    # Act
    with _github(known):
        asyncio.run(collector.collect_github_app(db, entry, entry.apps[0]))

    # Assert
    assert (
        len(store.merged_findings(db, "shop", datetime(2026, 10, 9).date()))
        == 1
    )


def _titles(db):
    return [
        m.title
        for m in store.merged_findings(
            db, "shop", datetime(2026, 10, 9).date()
        )
    ]


def test_a_deleted_security_workflow_is_a_core_finding(db, entry):
    # Act
    with _github(files={}):
        asyncio.run(collector.collect_github_app(db, entry, entry.apps[0]))

    # Assert
    assert _titles(db) == ["Workflow de sécurité absent du repo"]


def test_a_locked_value_changed_in_the_repo_is_a_core_finding(db, entry):
    # Arrange
    policy = settings.ProjectPolicy(
        ci_fail_on=settings.PolicyValue(value="critical", locked=True)
    )
    files = {
        settings.WORKFLOW_FILE: "name: Security Scans",
        settings.SETTINGS_FILE: "ci:\n  failOn: none\n",
    }

    # Act
    with _github(files=files):
        asyncio.run(
            collector.collect_github_app(db, entry, entry.apps[0], policy)
        )

    # Assert
    assert _titles(db) == ["Réglage imposé par l'admin modifié dans le repo"]


def _core_draft():
    from app.services.security.model import (  # pylint: disable=import-outside-toplevel
        Audience,
        Category,
        Draft,
        Source,
        Tier,
    )

    return Draft(
        fingerprint="leak",
        app="api",
        source=Source.CI,
        category=Category.LEAKS,
        tier=Tier.CORE,
        audience=Audience.DEVELOPER,
        rule="aws-key",
        title="Clé AWS dans le code",
    )


def _run_project_pass(db, entry):
    async def fake_github_app(db, entry, app, policy=None):
        store.scan_state(db, entry.project, app.name, collector.Source.CI)
        return store.upsert_findings(
            db,
            entry.project,
            [_core_draft()],
            (collector.Source.CI,),
            {app.name},
            collector.utcnow(),
        )

    send = mock.AsyncMock()
    with mock.patch.object(
        collector, "collect_github_app", fake_github_app
    ), mock.patch.object(
        collector, "project_policy", mock.AsyncMock(return_value=None)
    ), mock.patch.object(
        collector.alerts, "send", send
    ):
        asyncio.run(collector.collect_project(db, entry, False, True))
    return send


def test_the_first_pass_of_a_project_sends_no_alert(db, entry):
    send = _run_project_pass(db, entry)

    send.assert_not_called()


def test_a_core_finding_seen_after_the_first_pass_is_sent(db, entry):
    db.add(
        store.SecurityScan(
            project=entry.project,
            app="api",
            source="ci",
            status="ok",
            message="",
            last_run_at=datetime(2026, 10, 1),
        )
    )
    db.commit()

    send = _run_project_pass(db, entry)

    assert [f.fingerprint for f in send.call_args.args[1]] == ["leak"]
