import asyncio
import base64
import json
import time
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus
from app.routers import deployments as deployments_router
from app.schemas.deployment import DeploymentCreate
from app.services import keycloak_service

ISSUER = f"{settings.KEYCLOAK_URL}/realms/3istor"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key=SIGNING_KEY, **overrides) -> str:
    claims = {
        "sub": "u1",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
        "preferred_username": "alice",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _unsigned(**claims) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")
    return (
        enc({"alg": "none", "typ": "JWT"}) + b"." + enc(claims) + b"."
    ).decode()


@pytest.fixture
def jwks():
    signing = mock.Mock(key=SIGNING_KEY.public_key())
    with mock.patch.object(
        keycloak_service._jwks_client,
        "get_signing_key_from_jwt",
        return_value=signing,
    ):
        yield


class TestDecodePlatformToken:
    def test_accepts_a_token_signed_by_the_realm(self, jwks):
        claims = keycloak_service.decode_platform_token(_token())

        assert claims["sub"] == "u1"

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param(lambda: _token(key=OTHER_KEY), id="foreign-signature"),
            pytest.param(lambda: _token(exp=int(time.time()) - 10), id="expired"),
            pytest.param(lambda: _token(iss="https://evil.test/realms/3istor"), id="wrong-issuer"),
            pytest.param(lambda: _unsigned(sub="u1", iss=ISSUER, exp=9999999999), id="alg-none"),
            pytest.param(lambda: "not-a-jwt", id="malformed"),
        ],
    )
    def test_rejects_untrusted_tokens(self, jwks, token):
        with pytest.raises(HTTPException) as exc:
            keycloak_service.decode_platform_token(token())

        assert exc.value.status_code == 401

    def test_reports_unavailable_when_signing_keys_cannot_be_fetched(self):
        with mock.patch.object(
            keycloak_service._jwks_client,
            "get_signing_key_from_jwt",
            side_effect=jwt.PyJWKClientConnectionError("down"),
        ):
            with pytest.raises(HTTPException) as exc:
                keycloak_service.decode_platform_token(_token())

        assert exc.value.status_code == 503


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        for project in ("alpha", "beta"):
            session.add(
                Deployment(
                    name=f"{project}-web",
                    template_id="k3s-gitops-app",
                    project_id=project,
                    status=DeploymentStatus.RUNNING,
                )
            )
        session.commit()
        yield session


ROLES = {("u1", "alpha"): "admin", ("u2", "alpha"): "member"}


@pytest.fixture
def roles():
    with (
        mock.patch.object(
            keycloak_service,
            "get_project_role",
            side_effect=lambda user, project: ROLES.get((user, project)),
        ),
        mock.patch.object(
            deployments_router,
            "fetch_user_projects_from_keycloak",
            side_effect=lambda user: [
                {"name": p, "role": r} for (u, p), r in ROLES.items() if u == user
            ],
        ),
    ):
        yield


def _user(sub: str) -> dict:
    return {"sub": sub, "preferred_username": sub}


def _deployment_id(db, project: str) -> int:
    return db.query(Deployment).filter_by(project_id=project).one().id


class TestDeploymentsAuthorization:
    def test_lists_only_the_users_projects(self, db, roles):
        result = asyncio.run(deployments_router.list_deployments(_user("u2"), db))

        assert [d.project_id for d in result] == ["alpha"]

    def test_member_reads_a_deployment_of_their_project(self, db, roles):
        result = asyncio.run(
            deployments_router.get_deployment(_deployment_id(db, "alpha"), _user("u2"), db)
        )

        assert result.project_id == "alpha"

    def test_refuses_a_deployment_of_another_project(self, db, roles):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                deployments_router.get_deployment(_deployment_id(db, "beta"), _user("u1"), db)
            )

        assert exc.value.status_code == 403

    def test_refuses_outputs_of_another_project(self, db, roles):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                deployments_router.get_deployment_outputs(
                    _deployment_id(db, "beta"), _user("u2"), db
                )
            )

        assert exc.value.status_code == 403

    def test_member_cannot_delete(self, db, roles):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                deployments_router.delete_deployment(
                    _deployment_id(db, "alpha"), BackgroundTasks(), _user("u2"), db
                )
            )

        assert exc.value.status_code == 403

    def test_member_cannot_create(self, db, roles):
        payload = DeploymentCreate(
            name="api", template_id="k3s-gitops-app", project_id="alpha"
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                deployments_router.create_deployment(
                    payload, BackgroundTasks(), _user("u2"), db
                )
            )

        assert exc.value.status_code == 403

    def test_admin_cannot_target_another_project_through_app_config(self, db, roles):
        payload = DeploymentCreate(
            name="api",
            template_id="k3s-gitops-app",
            project_id="alpha",
            app_config={"project_name": "beta"},
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                deployments_router.create_deployment(
                    payload, BackgroundTasks(), _user("u1"), db
                )
            )

        assert exc.value.status_code == 400

    def test_refuses_a_deployment_outside_any_project_to_non_platform_admins(self, db, roles):
        payload = DeploymentCreate(name="vm", template_id="k3s-gitops-app")

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                deployments_router.create_deployment(
                    payload, BackgroundTasks(), _user("u1"), db
                )
            )

        assert exc.value.status_code == 403
