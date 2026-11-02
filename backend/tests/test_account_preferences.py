import importlib.util
from pathlib import Path
from unittest import mock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.user_preference import UserPreference
from app.routers import account as account_router
from app.schemas.account import UserPreferences

MIGRATION = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "a1c5e8f2b7d3_add_user_preferences.py"
)


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


def _claims(username="alice"):
    return {"sub": "uuid-1", "preferred_username": username}


class TestPreferencesSchema:
    @pytest.mark.parametrize("locale", ["fr", "en", None])
    def test_accepts_supported_locales_and_null(self, locale):
        assert UserPreferences(locale=locale).locale == locale

    @pytest.mark.parametrize("locale", ["de", "FR", "fr-FR", ""])
    def test_rejects_other_locales(self, locale):
        with pytest.raises(ValidationError):
            UserPreferences(locale=locale)


class TestPreferencesEndpoints:
    def test_get_returns_null_locale_when_nothing_is_stored(self, db):
        result = account_router.get_preferences(_claims(), db)

        assert result.locale is None

    def test_put_then_get_returns_the_stored_locale(self, db):
        account_router.update_preferences(
            UserPreferences(locale="en"), _claims(), db
        )

        assert account_router.get_preferences(_claims(), db).locale == "en"

    def test_put_replaces_the_previous_locale_without_a_second_row(self, db):
        account_router.update_preferences(
            UserPreferences(locale="en"), _claims(), db
        )
        account_router.update_preferences(
            UserPreferences(locale="fr"), _claims(), db
        )

        assert db.query(UserPreference).count() == 1
        assert account_router.get_preferences(_claims(), db).locale == "fr"

    def test_put_null_clears_the_preference(self, db):
        account_router.update_preferences(
            UserPreferences(locale="en"), _claims(), db
        )
        account_router.update_preferences(
            UserPreferences(locale=None), _claims(), db
        )

        assert account_router.get_preferences(_claims(), db).locale is None

    def test_preferences_are_per_user(self, db):
        account_router.update_preferences(
            UserPreferences(locale="en"), _claims("alice"), db
        )

        assert (
            account_router.get_preferences(_claims("bob"), db).locale is None
        )

    def test_put_without_user_identifier_is_refused(self, db):
        with pytest.raises(account_router.HTTPException) as exc:
            account_router.update_preferences(
                UserPreferences(locale="en"), {}, db
            )

        assert exc.value.status_code == 400


class TestProfileLocale:
    def test_profile_exposes_the_stored_locale(self, db):
        account_router.update_preferences(
            UserPreferences(locale="en"), _claims(), db
        )

        with mock.patch.object(
            account_router, "_get_admin_token", side_effect=RuntimeError
        ):
            profile = account_router.get_user_profile(
                {**_claims(), "email": "a@example.com"}, db
            )

        assert profile.locale == "en"


class TestProfileAdminFlag:
    @pytest.mark.parametrize(
        ("roles", "expected"), [(["cnp-admin"], True), ([], False)]
    )
    def test_profile_reports_the_platform_admin_role(
        self, db, roles, expected
    ):
        claims = {
            **_claims(),
            "email": "a@example.com",
            "realm_access": {"roles": roles},
        }

        with mock.patch.object(
            account_router, "_get_admin_token", side_effect=RuntimeError
        ):
            profile = account_router.get_user_profile(claims, db)

        assert profile.cnp_admin is expected


class TestMigration:
    def _load(self):
        spec = importlib.util.spec_from_file_location("mig", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_upgrade_creates_the_table_on_sqlite_and_downgrade_drops_it(self):
        engine = create_engine("sqlite://")
        module = self._load()

        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            columns = {
                c["name"]: c
                for c in inspect(conn).get_columns("user_preferences")
            }
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
            dropped = not inspect(conn).has_table("user_preferences")

        assert columns["locale"]["nullable"] is True
        assert dropped

    def test_upgrade_is_a_no_op_when_create_all_already_made_the_table(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        module = self._load()

        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            exists = inspect(conn).has_table("user_preferences")

        assert exists
