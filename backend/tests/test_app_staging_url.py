from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.environment import AppEnvironment
from app.routers import deployments as deployments_router
from app.routers import projects as projects_router
from app.services.environments import runs
from app.services.keycloak_service import get_current_user
from tests.test_links import (  # noqa: F401
    USER_TOKENS,
    keycloak,
    session_factory,
)

STAGING = "web-shop-staging.3istor.com"


def _staging(factory, state: str) -> None:
    with factory() as db:
        db.add(
            AppEnvironment(
                deployment_id=1,
                name="staging",
                state=state,
                hostname=STAGING,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


def test_ready_staging_gives_its_url(session_factory):  # noqa: F811
    _staging(session_factory, "ready")

    with session_factory() as db:
        urls = runs.staging_urls(db, [1, 2])

    assert urls == {1: f"https://{STAGING}/"}


def test_provisioning_staging_has_no_url(session_factory):  # noqa: F811
    _staging(session_factory, "provisioning")

    with session_factory() as db:
        urls = runs.staging_urls(db, [1, 2])

    assert urls == {}


def test_app_lists_carry_the_staging_url(
    session_factory, keycloak  # noqa: F811
):
    _staging(session_factory, "ready")
    app = FastAPI()
    app.include_router(projects_router.router, prefix="/api")
    app.include_router(deployments_router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS["viewer"]
    client = TestClient(app)

    listed = client.get("/api/projects/shop/apps").json()
    one = client.get("/api/deployments/1").json()

    web = next(row for row in listed if row["id"] == 1)
    assert (web["staging_url"], one["staging_url"]) == (
        f"https://{STAGING}/",
        f"https://{STAGING}/",
    )
