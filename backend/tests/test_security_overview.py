from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.security import SecurityException, SecurityScan
from app.routers import security
from app.services.security import store
from app.services.security.model import Audience, Category, Draft, Source, Tier

NOW = datetime.utcnow()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _project(db, name, tiers, excepted=()):
    db.add(
        Deployment(
            name="web",
            template_id=security.K8S_TEMPLATE,
            project_id=name,
            provider_type=ProviderType.KUBERNETES,
            status=DeploymentStatus.RUNNING,
        )
    )
    db.add(
        SecurityScan(
            project=name,
            app="web",
            source=Source.CI.value,
            status="ok",
            message="",
            last_run_at=NOW,
        )
    )
    drafts = [
        Draft(
            fingerprint=f"{name}-{i}",
            app="web" if i % 2 == 0 else None,
            source=Source.CI,
            category=Category.DEPENDENCIES,
            tier=tier,
            audience=Audience.DEVELOPER,
            rule="r",
            title="t",
        )
        for i, tier in enumerate(tiers)
    ]
    store.upsert_findings(db, name, drafts, (Source.CI,), {"web"}, NOW)
    for i in excepted:
        db.add(
            SecurityException(
                project=name,
                app="web",
                fingerprint=f"{name}-{i}",
                rule="r",
                kind="finding",
                status="accepted_risk",
                justification="j",
                statement="s",
                expires_on=(NOW + timedelta(days=5)).date(),
                author="a",
                created_at=NOW,
                approved_by="b",
                approved_at=NOW,
                approved_by_reviewer=True,
            )
        )
    db.commit()


def _setup(db, n):
    tiers = [Tier.CORE, Tier.IMPORTANT, Tier.RECOMMENDED, Tier.IMPORTANT]
    for i in range(n):
        _project(db, f"p{i}", tiers[: i % 4 + 1], excepted=[1] if i else [])
    return [f"p{i}" for i in range(n)]


def test_overview_matches_summary(db):
    names = _setup(db, 4)
    db.add(  # deployed, never scanned: no score
        Deployment(
            name="api",
            template_id=security.K8S_TEMPLATE,
            project_id="fresh",
            provider_type=ProviderType.KUBERNETES,
            status=DeploymentStatus.RUNNING,
        )
    )
    db.commit()
    names.append("fresh")

    result = security._overview(db, names)

    assert len(result) == 5
    for o in result:
        s = security._summary(db, o.project, None, "developer")
        assert (o.score, o.grade, o.actions, o.core) == (
            s.score,
            s.grade,
            s.actions,
            s.core,
        )


def test_overview_query_count_is_constant(db):
    def count(names):
        n = []
        engine = db.get_bind()

        def hook(*_):
            n.append(1)

        event.listen(engine, "before_cursor_execute", hook)
        security._overview(db, names)
        event.remove(engine, "before_cursor_execute", hook)
        return len(n)

    names = _setup(db, 6)
    assert count(names[:2]) == count(names)
    assert count(names) <= 6
