import asyncio
import logging
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from app.models.environment import AppEnvironment
from app.schemas.project import ProjectCreate
from app.scripts import move_staging_realm
from app.scripts.move_staging_realm import with_realm
from app.services import project_bootstrap
from app.services.environments import staging_realm
from tests.test_environments import db  # noqa: F401
from tests.test_project_status import (  # noqa: F401
    add_project,
    session_factory,
    stubbed_terraform,
)


def test_the_realm_is_named_after_the_project():
    assert staging_realm.realm_name("shop") == "shop-staging"


def test_ensure_applies_the_realm_module_and_returns_its_name():
    executor = mock.Mock()
    executor.apply.return_value = {"realm": "shop-staging"}
    with (
        mock.patch.object(staging_realm, "get_repository") as repository,
        mock.patch.object(
            staging_realm, "create_executor", return_value=executor
        ) as create,
    ):
        repository.return_value.get_template_path.return_value = Path("/t")

        realm = staging_realm.ensure("shop")

    assert realm == "shop-staging"
    assert create.call_args.args == (
        Path("/t/staging-realm"),
        "shop-_staging-realm",
        "cnp/projects/shop/_staging-realm",
    )
    executor.init.assert_called_once()
    executor.apply.assert_called_once_with({"project_name": "shop"})


def test_destroy_uses_the_same_state():
    executor = mock.Mock()
    with (
        mock.patch.object(staging_realm, "get_repository") as repository,
        mock.patch.object(
            staging_realm, "create_executor", return_value=executor
        ),
    ):
        repository.return_value.get_template_path.return_value = Path("/t")

        staging_realm.destroy("shop")

    executor.destroy.assert_called_once_with({"project_name": "shop"})


def test_a_project_name_ending_in_staging_is_refused():
    with pytest.raises(ValidationError, match="cannot end in -staging"):
        ProjectCreate(project_name="shop-staging")


def test_teardown_destroys_the_staging_realm(
    session_factory, stubbed_terraform  # noqa: F811
):
    add_project(session_factory, "demo")
    with mock.patch.object(staging_realm, "destroy") as destroy:
        project_bootstrap.run_project_teardown("demo", "aws")

    destroy.assert_called_once_with("demo")


def test_a_failed_realm_destroy_is_logged_and_the_teardown_goes_on(
    session_factory, stubbed_terraform, caplog  # noqa: F811
):
    add_project(session_factory, "demo")
    _, run = stubbed_terraform
    with (
        mock.patch.object(
            staging_realm, "destroy", side_effect=RuntimeError("boom")
        ),
        caplog.at_level(logging.ERROR),
    ):
        project_bootstrap.run_project_teardown("demo", "aws")

    assert "staging realm" in caplog.text
    assert run.call_args.args[0][:2] == ["terraform", "destroy"]


class TestMoveRealm:
    def test_only_the_realm_keys_of_existing_blocks_change(self):
        values = {
            "image": {"tag": "1"},
            "ingress": {"hostname": "h", "realm": "shop"},
            "auth": {"clientId": "c"},
        }

        assert with_realm(values, "shop-staging") is True
        assert values == {
            "image": {"tag": "1"},
            "ingress": {"hostname": "h", "realm": "shop-staging"},
            "auth": {"clientId": "c", "realm": "shop-staging"},
        }

    def test_a_component_without_those_blocks_is_left_alone(self):
        values = {"image": {"tag": "1"}}

        assert with_realm(values, "shop-staging") is False
        assert values == {"image": {"tag": "1"}}

    @pytest.mark.parametrize("state", ["provisioning", "failed", None])
    def test_nothing_moves_unless_staging_is_ready(
        self, db, state
    ):  # noqa: F811
        if state is None:
            db.delete(db.get(AppEnvironment, (7, "staging")))
        else:
            db.get(AppEnvironment, (7, "staging")).state = state
        db.commit()
        with (
            mock.patch.object(
                move_staging_realm, "SessionLocal", lambda: nullcontext(db)
            ),
            mock.patch.object(staging_realm, "ensure") as ensure,
            mock.patch.object(
                move_staging_realm.activation, "_executor"
            ) as executor,
            pytest.raises(SystemExit) as refused,
        ):
            asyncio.run(move_staging_realm.move(7))

        assert "not ready" in str(refused.value)
        ensure.assert_not_called()
        executor.assert_not_called()
