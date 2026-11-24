from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.security import SecurityScan
from app.routers import security
from app.services import scorecard


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
            Deployment(
                id=1,
                name="web",
                template_id=security.K8S_TEMPLATE,
                project_id="shop",
                provider_type=ProviderType.KUBERNETES,
                status=DeploymentStatus.RUNNING,
            )
        )
        session.commit()
        yield session


def _scan(db, source, status="ok", last_run_at=None):
    db.add(
        SecurityScan(
            project="shop",
            app="web",
            source=source,
            status=status,
            message="",
            last_run_at=last_run_at,
            collected_at=datetime(2026, 11, 24),
        )
    )
    db.commit()


def test_an_app_never_scanned_has_no_score_and_says_why(db):
    # Arrange: backups are collected, the CI workflow never ran
    _scan(db, "cnpg")
    _scan(db, "ci", status="missing")

    # Act
    summary = security._summary(db, "shop", "web", "developer")

    # Assert
    assert (
        summary.scan_state,
        summary.score,
        summary.grade,
        summary.not_scanned_reason,
    ) == ("not_scanned", None, None, "workflow_not_run")


def test_the_scorecard_agrees_with_the_security_tab_on_an_unscanned_app(db):
    # Arrange
    _scan(db, "cnpg")

    # Act
    data = scorecard._read_security(db, db.get(Deployment, 1))

    # Assert
    assert data.scanned is False


def test_a_scanned_app_without_findings_scores_full_marks(db):
    # Arrange
    _scan(db, "trivy-operator", last_run_at=datetime(2026, 11, 24))

    # Act
    summary = security._summary(db, "shop", "web", "developer")

    # Assert
    assert (summary.scan_state, summary.score, summary.grade) == (
        "scanned",
        100,
        "A",
    )
