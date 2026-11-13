import asyncio
import json
import threading
from io import StringIO
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from ruamel.yaml import YAML
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.environment import AppEnvironment
from app.routers import projects as projects_router
from app.schemas.project import CustomerAccessUpdate
from app.services import audit, customer_identity, project_registry
from app.services import terraform_orchestrator as orchestrator
from app.services.customer_identity import CustomerIdentityError
from app.services.environments import activation
from app.services.project_registry import RecordNotFoundError, RegistryError
from tests.test_api_auth import _user, db, roles  # noqa: F401

AGENT = {
    "sub": "u1",
    "preferred_username": "u1",
    "cnp_agent": {"grant_id": "g"},
}


def put(db, token, project="alpha", mode="open"):
    return asyncio.run(
        projects_router.update_customer_access(
            project, CustomerAccessUpdate(mode=mode), token, db
        )
    )


@pytest.fixture
def seams():
    """Registry write and Keycloak apply, recording the order they ran in."""
    calls = []

    async def write(project, mode):
        calls.append(("registry", project, mode))

    def apply(project, mode):
        calls.append(("keycloak", project, mode, threading.get_ident()))

    with (
        mock.patch.object(projects_router, "set_customer_access", new=write),
        mock.patch.object(customer_identity, "apply", new=apply),
    ):
        yield calls


@pytest.mark.parametrize(
    "mode, order",
    [
        ("open", ["registry", "keycloak"]),
        ("companies", ["keycloak", "registry"]),
        ("closed", ["keycloak", "registry"]),
    ],
)
def test_an_admin_sets_customer_access(
    db, roles, seams, mode, order  # noqa: F811
):
    assert put(db, _user("u1"), mode=mode) == {"mode": mode}

    # Open: registry first, so a deploy heals Keycloak if the second step
    # fails. Otherwise Keycloak first, so a failure never leaves it open.
    # Keycloak calls are blocking, so off the event loop.
    assert [c[0] for c in seams] == order
    assert all(c[:3][1:] == ("alpha", mode) for c in seams)
    keycloak = next(c for c in seams if c[0] == "keycloak")
    assert keycloak[3] != threading.get_ident()


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValidationError):
        CustomerAccessUpdate(mode="public")
    with pytest.raises(ValidationError):
        CustomerAccessUpdate(enabled=True)


def test_a_platform_admin_may_set_customer_access(
    db, roles, seams  # noqa: F811
):
    with mock.patch.object(projects_router, "is_cnp_admin", return_value=True):
        assert put(db, _user("root")) == {"mode": "open"}

    assert len(seams) == 2


@pytest.mark.parametrize("mode", ["closed", "companies", "open"])
def test_a_member_cannot_set_customer_access(
    db, roles, seams, mode  # noqa: F811
):
    with pytest.raises(HTTPException) as exc:
        put(db, _user("u2"), mode=mode)

    assert exc.value.status_code == 403
    assert seams == []


def test_an_agent_cannot_set_customer_access(db, roles, seams):  # noqa: F811
    with pytest.raises(HTTPException) as exc:
        put(db, AGENT, mode="companies")

    assert exc.value.status_code == 403
    assert "Assistants cannot change" in exc.value.detail
    assert seams == []


def test_a_project_without_record_is_not_found(db, roles, seams):  # noqa: F811
    with mock.patch.object(
        projects_router,
        "set_customer_access",
        new=mock.AsyncMock(side_effect=RecordNotFoundError("no record")),
    ):
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u1"))

    assert exc.value.status_code == 404
    assert seams == []


def test_an_unwritable_registry_leaves_keycloak_alone(
    db, roles, seams
):  # noqa: F811
    with mock.patch.object(
        projects_router,
        "set_customer_access",
        new=mock.AsyncMock(side_effect=RegistryError("down")),
    ):
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u1"))

    assert exc.value.status_code == 502
    assert seams == []


def test_a_keycloak_failure_says_the_setting_is_saved(
    db, roles, seams
):  # noqa: F811
    with mock.patch.object(
        customer_identity,
        "apply",
        side_effect=CustomerIdentityError(403, "PUT /admin: forbidden"),
    ):
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u1"))

    assert exc.value.status_code == 502
    assert "saved" in exc.value.detail
    assert "PUT /admin: forbidden" in exc.value.detail
    assert seams == [("registry", "alpha", "open")]


@pytest.mark.parametrize("mode", ["closed", "companies"])
def test_a_keycloak_failure_keeps_the_saved_mode(
    db, roles, seams, mode  # noqa: F811
):
    with mock.patch.object(
        customer_identity,
        "apply",
        side_effect=CustomerIdentityError(502, "keycloak down"),
    ):
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u1"), mode=mode)

    assert exc.value.status_code == 502
    assert "unchanged" in exc.value.detail
    assert "keycloak down" in exc.value.detail
    assert seams == []  # the mode was never written


@pytest.mark.parametrize("mode", ["closed", "companies"])
def test_an_unwritable_registry_after_keycloak_is_a_bad_gateway(
    db, roles, seams, mode  # noqa: F811
):
    with mock.patch.object(
        projects_router,
        "set_customer_access",
        new=mock.AsyncMock(side_effect=RegistryError("down")),
    ):
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u1"), mode=mode)

    assert exc.value.status_code == 502
    assert [c[0] for c in seams] == ["keycloak"]


def test_reading_the_mode(roles):  # noqa: F811
    with mock.patch.object(
        projects_router,
        "get_customer_access",
        new=mock.AsyncMock(return_value="companies"),
    ):
        read = asyncio.run(
            projects_router.read_customer_access(
                SimpleNamespace(project_name="alpha")
            )
        )

    assert read == {"mode": "companies"}


def test_the_change_is_journaled_as_notable():
    action, params = audit.match_action(
        "PUT", "/api/projects/alpha/customer-access"
    )

    assert action == "project.customer_access.update"
    assert params == {"project_name": "alpha"}
    assert action in audit.NOTABLE
    # Entries written before the modes keep their badge.
    assert "project.customer_signup.update" in audit.NOTABLE


RECORD = """\
apiVersion: cnp.3istor.com/v1alpha1
kind: ProjectRecord
metadata:
  name: alpha
spec:
  targetCloud: onprem
{extra}"""


def registry(extra):
    record = YAML().load(RECORD.format(extra=extra))
    return (
        mock.patch.object(
            project_registry,
            "read_record",
            new=mock.AsyncMock(return_value=(record, "sha1")),
        ),
        mock.patch.object(
            project_registry,
            "_installation_token",
            new=mock.AsyncMock(return_value="t"),
        ),
    )


def written(mode, extra):
    read, token = registry(extra)
    with (
        read,
        token,
        mock.patch.object(
            project_registry, "put_file_content", new=mock.AsyncMock()
        ) as put_file,
    ):
        asyncio.run(project_registry.set_customer_access("alpha", mode))
    put_file.assert_awaited_once()
    args, kwargs = put_file.call_args
    assert kwargs["sha"] == "sha1"
    spec = YAML(typ="safe").load(StringIO(args[3]))["spec"]
    return spec, kwargs["message"]


@pytest.mark.parametrize("mode", ["companies", "open"])
def test_the_record_carries_the_mode(mode):
    spec, message = written(mode, "  deletionProtection: true\n")

    assert spec["customerAccess"] == mode
    assert spec["deletionProtection"] is True
    assert message == (
        f"feat(registry): set customer access to {mode} for alpha [skip ci]"
    )


@pytest.mark.parametrize(
    "mode, expected",
    [("closed", None), ("companies", "companies"), ("open", "open")],
)
def test_writing_a_mode_drops_the_old_flag(mode, expected):
    spec, message = written(
        mode, "  selfRegistration: true\n  customerAccess: open\n"
    )

    assert "selfRegistration" not in spec
    assert spec.get("customerAccess") == expected
    assert spec["targetCloud"] == "onprem"
    assert message == (
        f"feat(registry): set customer access to {mode} for alpha [skip ci]"
    )


@pytest.mark.parametrize(
    "extra, expected",
    [
        ("", "closed"),
        ("  customerAccess: companies\n", "companies"),
        ("  customerAccess: open\n", "open"),
        # Records written before the modes existed.
        ("  selfRegistration: true\n", "open"),
        ("  selfRegistration: false\n", "closed"),
        (
            "  customerAccess: companies\n  selfRegistration: true\n",
            "companies",
        ),
        ("  customerAccess: public\n", "closed"),
    ],
)
def test_the_mode_reads_from_the_record(extra, expected):
    read, _ = registry(extra)
    with read:
        assert (
            asyncio.run(project_registry.get_customer_access("alpha"))
            == expected
        )


def test_a_project_without_record_is_closed():
    with mock.patch.object(
        project_registry, "read_record", new=mock.AsyncMock(return_value=None)
    ):
        assert (
            asyncio.run(project_registry.get_customer_access("x")) == "closed"
        )
        with pytest.raises(RecordNotFoundError):
            asyncio.run(project_registry.set_customer_access("x", "open"))


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def seen_by_apply_if_enabled(session_factory, state_of):
    """A stand-in recording the project and the state its caller was in."""
    seen = []

    def during(project):
        with session_factory() as db:
            seen.append((project, state_of(db)))

    return seen, during


class FakeExecutor:
    working_dir = state_dir = "/tmp/x"

    def init(self):
        pass

    def plan(self, config):
        return ""

    def apply(self, config):
        return {
            "hostname": "web-demo.example.test",
            "app_type": "static",
            "app_url": "https://web-demo.example.test",
        }

    def get_state_summary(self):
        return {"resource_count": 1}


def test_a_new_app_gets_the_rights_when_customer_access_is_on(
    session_factory, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)  # the orchestrator writes logs/deployments
    with session_factory() as db:
        db.add(
            Deployment(
                id=1,
                name="web",
                template_id="k3s-gitops-app",
                project_id="demo",
                provider_type=ProviderType.KUBERNETES,
                app_config=json.dumps({}),
            )
        )
        db.commit()
    seen, during = seen_by_apply_if_enabled(
        session_factory, lambda db: db.get(Deployment, 1).status
    )
    wait = mock.Mock(side_effect=lambda *_: seen.append("url wait") or True)
    repo = mock.Mock()
    repo.get_template_by_id.return_value = {"_template_path": str(tmp_path)}

    with (
        mock.patch.object(orchestrator, "SessionLocal", session_factory),
        mock.patch.object(orchestrator, "get_repository", return_value=repo),
        mock.patch.object(
            orchestrator, "create_executor", return_value=FakeExecutor()
        ),
        mock.patch.object(orchestrator, "_notify"),
        mock.patch.object(
            orchestrator,
            "get_org_installation_token",
            new=mock.AsyncMock(return_value="t"),
        ),
        mock.patch.object(orchestrator, "_wait_for_url_ready", new=wait),
        mock.patch.object(
            project_registry, "register_app", new=mock.AsyncMock()
        ),
        mock.patch.object(
            customer_identity, "apply_if_enabled", side_effect=during
        ),
    ):
        orchestrator.run_deployment(1)

    # The client exists after apply: don't wait for the URL first, or the app
    # serves without the roles in the meantime.
    assert seen == [("demo", DeploymentStatus.DEPLOYING), "url wait"]
    with session_factory() as db:
        assert db.get(Deployment, 1).status == DeploymentStatus.RUNNING


def test_staging_gets_the_rights_before_it_is_ready(session_factory):
    with session_factory() as db:
        db.add(
            Deployment(
                id=1,
                name="web",
                template_id="k3s-gitops-app",
                project_id="demo",
                provider_type=ProviderType.KUBERNETES,
            )
        )
        db.add(
            AppEnvironment(
                deployment_id=1,
                name="staging",
                state="provisioning",
                updated_at=activation.now(),
            )
        )
        db.commit()
    seen, during = seen_by_apply_if_enabled(
        session_factory,
        lambda db: db.get(AppEnvironment, (1, "staging")).state,
    )

    with (
        mock.patch.object(activation, "SessionLocal", session_factory),
        mock.patch.object(
            activation, "_executor", return_value=FakeExecutor()
        ),
        mock.patch.object(activation.staging_realm, "ensure"),
        mock.patch.object(
            activation, "_write_env_files", new=mock.AsyncMock()
        ),
        mock.patch.object(
            project_registry, "set_app_environments", new=mock.AsyncMock()
        ),
        mock.patch.object(
            customer_identity, "apply_if_enabled", side_effect=during
        ),
    ):
        asyncio.run(activation._enable(1, {}))

    assert seen == [("demo", "provisioning")]
    with session_factory() as db:
        assert db.get(AppEnvironment, (1, "staging")).state == "ready"


def test_a_broken_customer_access_never_fails_the_staging_activation(
    session_factory,
):
    with session_factory() as db:
        db.add(
            Deployment(
                id=1,
                name="web",
                template_id="k3s-gitops-app",
                project_id="demo",
                provider_type=ProviderType.KUBERNETES,
            )
        )
        db.add(
            AppEnvironment(
                deployment_id=1,
                name="staging",
                state="provisioning",
                updated_at=activation.now(),
            )
        )
        db.commit()

    with (
        mock.patch.object(activation, "SessionLocal", session_factory),
        mock.patch.object(
            activation, "_executor", return_value=FakeExecutor()
        ),
        mock.patch.object(activation.staging_realm, "ensure"),
        mock.patch.object(
            activation, "_write_env_files", new=mock.AsyncMock()
        ),
        mock.patch.object(
            project_registry,
            "set_app_environments",
            new=mock.AsyncMock(),
        ),
        mock.patch.object(
            project_registry,
            "get_customer_access",
            new=mock.AsyncMock(side_effect=RegistryError("down")),
        ),
    ):
        asyncio.run(activation._enable(1, {}))

    with session_factory() as db:
        assert db.get(AppEnvironment, (1, "staging")).state == "ready"
