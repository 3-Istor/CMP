from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.security import SecurityException
from app.services.security import alerts, store
from app.services.security.model import Audience, Category, Draft, Source, Tier

NOW = datetime(2026, 10, 9, 12, 0)
WEBHOOK = "https://discord.com/api/webhooks/1234567890/abc-DEF_123"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def no_platform_default(monkeypatch):
    monkeypatch.setattr(alerts.settings, "SECURITY_DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(alerts.settings, "DISCORD_WEBHOOK_URL", "")


def _fresh(db, tier=Tier.CORE, fp="f1", source=Source.CI, now=NOW):
    draft = Draft(
        fingerprint=fp,
        app="api",
        source=source,
        category=Category.DEPENDENCIES,
        tier=tier,
        audience=Audience.DEVELOPER,
        rule="CVE-2026-1",
        title="t",
    )
    return store.upsert_findings(db, "shop", [draft], (source,), {"api"}, now)


@pytest.mark.parametrize(
    "url",
    [
        WEBHOOK,
        "https://discordapp.com/api/webhooks/1/x",
        "https://ptb.discord.com/api/webhooks/1/x",
    ],
)
def test_discord_webhooks_are_accepted(url):
    assert alerts.is_discord_webhook(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/1/x",
        "https://discord.com.evil.io/api/webhooks/1/x",
        "https://169.254.169.254/api/webhooks/1/x",
        "https://hooks.slack.com/services/x",
    ],
)
def test_other_urls_are_refused(url):
    assert not alerts.is_discord_webhook(url)


def test_hint_does_not_contain_the_token():
    assert "abc-DEF_123" not in alerts.hint(WEBHOOK)


def test_developer_findings_go_to_the_app_webhook_first(no_platform_default):
    targets = {"project": "p", "app:api": "a"}

    assert alerts.resolve(targets, "api", Audience.DEVELOPER) == ("a", "app")


def test_developer_findings_fall_back_to_the_project_webhook(
    no_platform_default,
):
    assert alerts.resolve({"project": "p"}, "api", Audience.DEVELOPER) == (
        "p",
        "project",
    )


def test_platform_findings_skip_team_webhooks(monkeypatch):
    monkeypatch.setattr(alerts.settings, "SECURITY_DISCORD_WEBHOOK_URL", "d")

    target = alerts.resolve({"project": "p"}, "api", Audience.PLATFORM)

    assert target == ("d", "platform")


def test_nothing_is_sent_without_any_webhook(no_platform_default):
    assert alerts.resolve({}, "api", Audience.DEVELOPER) is None


def test_only_core_findings_are_notified(db):
    fresh = _fresh(db, tier=Tier.IMPORTANT)

    assert alerts.to_notify(db, "shop", fresh, NOW) == []


def test_a_finding_seen_by_two_sources_is_notified_once(db):
    fresh = _fresh(db) + _fresh(db, source=Source.TRIVY_OPERATOR)

    notified = alerts.to_notify(db, "shop", fresh, NOW)

    assert len(notified) == 1


def test_a_finding_back_after_a_rescan_is_not_notified_again(db):
    alerts.to_notify(db, "shop", _fresh(db), NOW)
    store.upsert_findings(db, "shop", [], (Source.CI,), {"api"}, NOW)

    later = NOW + timedelta(hours=1)
    again = alerts.to_notify(db, "shop", _fresh(db, now=later), later)

    assert again == []


def test_an_ignored_finding_is_not_notified(db):
    db.add(
        SecurityException(
            project="shop",
            app="api",
            fingerprint="f1",
            rule="CVE-2026-1",
            kind="vulnerability",
            status="not_affected",
            statement="never called",
            expires_on=(NOW + timedelta(days=30)).date(),
            author="alice",
            approved_by="bob",
            approved_at=NOW,
        )
    )
    db.commit()

    assert alerts.to_notify(db, "shop", _fresh(db), NOW) == []
