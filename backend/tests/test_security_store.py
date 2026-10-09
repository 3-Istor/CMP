from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.security import SecurityException, SecuritySnapshot
from app.services.security import exceptions as rules
from app.services.security import scoring, store
from app.services.security.model import Audience, Category, Draft, Source, Tier

NOW = datetime(2026, 10, 9, 12, 0)
TODAY = NOW.date()


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _draft(fp="f1", app="api", source=Source.CI, tier=Tier.IMPORTANT):
    return Draft(
        fingerprint=fp,
        app=app,
        source=source,
        category=Category.DEPENDENCIES,
        tier=tier,
        audience=Audience.DEVELOPER,
        rule="CVE-2026-1",
        title="t",
    )


def test_a_finding_no_longer_seen_is_resolved(db):
    # Arrange
    store.upsert_findings(db, "shop", [_draft()], (Source.CI,), {"api"}, NOW)

    # Act
    store.upsert_findings(db, "shop", [], (Source.CI,), {"api"}, NOW)

    # Assert
    assert store.merged_findings(db, "shop", TODAY) == []


def test_a_pass_only_resolves_the_apps_it_collected(db):
    # Arrange
    store.upsert_findings(
        db,
        "shop",
        [_draft(app="api"), _draft("f2", app="web")],
        (Source.CI,),
        {"api", "web"},
        NOW,
    )

    # Act
    store.upsert_findings(db, "shop", [], (Source.CI,), {"api"}, NOW)

    # Assert
    assert [m.app for m in store.merged_findings(db, "shop", TODAY)] == ["web"]


def test_a_pass_only_resolves_its_own_sources(db):
    # Arrange
    store.upsert_findings(
        db,
        "shop",
        [_draft(source=Source.TRIVY_OPERATOR)],
        (Source.TRIVY_OPERATOR,),
        store.ALL_APPS,
        NOW,
    )

    # Act
    store.upsert_findings(db, "shop", [], (Source.CI,), store.ALL_APPS, NOW)

    # Assert
    assert len(store.merged_findings(db, "shop", TODAY)) == 1


def test_a_reappearing_finding_is_reported_as_new_again(db):
    # Arrange
    store.upsert_findings(db, "shop", [_draft()], (Source.CI,), {"api"}, NOW)
    store.upsert_findings(db, "shop", [], (Source.CI,), {"api"}, NOW)

    # Act
    fresh = store.upsert_findings(
        db, "shop", [_draft()], (Source.CI,), {"api"}, NOW + timedelta(days=1)
    )

    # Assert
    assert [f.fingerprint for f in fresh] == ["f1"]


def test_two_sources_of_one_problem_merge_into_the_worst_tier(db):
    # Arrange
    store.upsert_findings(
        db,
        "shop",
        [_draft(source=Source.CI, tier=Tier.IMPORTANT)],
        (Source.CI,),
        {"api"},
        NOW,
    )
    store.upsert_findings(
        db,
        "shop",
        [_draft(source=Source.TRIVY_OPERATOR, tier=Tier.CORE)],
        (Source.TRIVY_OPERATOR,),
        store.ALL_APPS,
        NOW,
    )

    # Act
    merged = store.merged_findings(db, "shop", TODAY)

    # Assert
    assert [(m.tier, m.sources) for m in merged] == [
        (Tier.CORE, ["ci", "trivy-operator"])
    ]


def _exception(db, status="not_affected", approved=False, expires=None):
    row = SecurityException(
        project="shop",
        app="api",
        fingerprint="f1",
        rule="CVE-2026-1",
        kind="vulnerability",
        status=status,
        statement="never called in our code",
        expires_on=expires or TODAY + timedelta(days=30),
        author="alice",
        approved_by="bob" if approved else None,
        approved_at=NOW if approved else None,
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize(
    "tier, status, approved, expected",
    [
        (Tier.IMPORTANT, "not_affected", False, True),
        (Tier.CORE, "not_affected", False, False),
        (Tier.CORE, "not_affected", True, True),
        (Tier.IMPORTANT, "accepted_risk", False, False),
        (Tier.IMPORTANT, "accepted_risk", True, True),
    ],
)
def test_core_findings_and_accepted_risks_need_an_approval(
    db, tier, status, approved, expected
):
    # Arrange
    store.upsert_findings(
        db, "shop", [_draft(tier=tier)], (Source.CI,), {"api"}, NOW
    )
    _exception(db, status=status, approved=approved)

    # Act
    merged = store.merged_findings(db, "shop", TODAY)

    # Assert
    assert merged[0].excepted is expected


def test_an_expired_exception_no_longer_hides_the_finding(db):
    # Arrange
    store.upsert_findings(db, "shop", [_draft()], (Source.CI,), {"api"}, NOW)
    _exception(db, expires=TODAY - timedelta(days=1))

    # Act
    merged = store.merged_findings(db, "shop", TODAY)

    # Assert
    assert merged[0].excepted is False


def test_snapshot_counts_new_major_findings_of_the_day(db):
    # Arrange
    store.upsert_findings(
        db,
        "shop",
        [_draft(tier=Tier.CORE), _draft("f2", tier=Tier.RECOMMENDED)],
        (Source.CI,),
        {"api"},
        NOW,
    )

    # Act
    store.write_snapshots(db, "shop", ["api"], TODAY)

    # Assert
    row = db.query(SecuritySnapshot).filter_by(app="api").one()
    assert (row.score, row.grade, row.new_major) == (73, "D", 1)


# ── Scoring ──────────────────────────────────────────────────────────────────


def _scored(tier, excepted=False, category=Category.DEPENDENCIES):
    return scoring.Scored("api", category, tier, excepted)


def test_one_core_finding_caps_the_grade_at_d():
    # Arrange
    findings = [_scored(Tier.CORE)]

    # Act
    value = scoring.score(findings)

    # Assert
    assert (value, scoring.grade(value, scoring.has_core(findings))) == (
        75,
        "D",
    )


def test_ignored_findings_cost_nothing():
    assert scoring.score([_scored(Tier.CORE, excepted=True)]) == 100


def test_score_never_goes_below_zero():
    assert scoring.score([_scored(Tier.CORE)] * 5) == 0


def test_segment_takes_the_worst_tier_of_its_category():
    # Arrange
    findings = [
        _scored(Tier.RECOMMENDED, category=Category.ACCESS),
        _scored(Tier.IMPORTANT, category=Category.ACCESS),
    ]

    # Act
    access = next(
        s for s in scoring.segments(findings) if s.category is Category.ACCESS
    )

    # Assert
    assert (access.worst, access.points_lost) == (Tier.IMPORTANT, 10)


# ── Exception rules ──────────────────────────────────────────────────────────


def _request(status, days=None, justification=None):
    return rules.ExceptionRequest(
        project="shop",
        status=status,
        justification=justification,
        statement="explained in more than ten characters",
        expires_on=TODAY + timedelta(days=days) if days else None,
    )


def test_a_committed_secret_cannot_be_accepted():
    with pytest.raises(ValueError):
        rules.validate(
            _request(rules.Status.ACCEPTED_RISK), "secret", Tier.CORE, TODAY
        )


def test_an_accepted_risk_expires_within_90_days():
    with pytest.raises(ValueError):
        rules.validate(
            _request(rules.Status.ACCEPTED_RISK, days=120),
            "vulnerability",
            Tier.IMPORTANT,
            TODAY,
        )


def test_not_affected_defaults_to_a_yearly_review_without_approval():
    # Arrange
    request = _request(
        rules.Status.NOT_AFFECTED,
        justification=rules.Justification.VULNERABLE_CODE_NOT_IN_EXECUTE_PATH,
    )

    # Act
    expires_on, needs_approval = rules.validate(
        request, "vulnerability", Tier.IMPORTANT, TODAY
    )

    # Assert
    assert (expires_on, needs_approval) == (TODAY + timedelta(days=365), False)


def test_not_affected_requires_a_vex_justification():
    with pytest.raises(ValueError):
        _request(rules.Status.NOT_AFFECTED)


def test_repository_copy_lists_only_vulnerabilities_and_secrets_in_force():
    # Arrange
    rows = [
        SecurityException(
            rule="CVE-1",
            kind="vulnerability",
            status="not_affected",
            statement="s",
            expires_on=date(2099, 1, 1),
            author="a",
            justification="component_not_present",
        ),
        SecurityException(
            rule="kyverno",
            kind="other",
            status="false_positive",
            statement="s",
            expires_on=date(2099, 1, 1),
            author="a",
        ),
        SecurityException(
            rule="CVE-2",
            kind="vulnerability",
            status="not_affected",
            statement="s",
            expires_on=date(2020, 1, 1),
            author="a",
        ),
    ]

    # Act
    content = rules.render_file(rows, TODAY)

    # Assert
    assert ("CVE-1" in content, "kyverno" in content, "CVE-2" in content) == (
        True,
        False,
        False,
    )
