import asyncio
import json
from datetime import date, timedelta
from unittest import mock

import pytest
from fastapi import HTTPException

from app.models.deployment import Deployment, DeploymentStatus
from app.models.security import SecurityException
from app.routers import activity, deployments, security
from app.services import keycloak_service
from app.services.security import store
from app.services.security.model import Tier
from tests.test_platform_only_endpoints import session_factory  # noqa: F401

AUDITOR = {
    "sub": "a",
    "preferred_username": "aud",
    "groups": ["security-auditor"],
}
FINOPS = {"sub": "f", "preferred_username": "fin", "groups": ["finops"]}
PROJECT_ADMIN = {"sub": "p", "preferred_username": "padmin", "groups": []}
TODAY = date(2026, 11, 7)


def _exception(**overrides) -> SecurityException:
    values = {
        "project": "shop",
        "app": "web",
        "fingerprint": "f1",
        "rule": "CVE-1",
        "kind": "vuln",
        "status": "not_affected",
        "statement": "s",
        "expires_on": TODAY + timedelta(days=30),
        "author": "dev",
    }
    values.update(overrides)
    return SecurityException(**values)


@pytest.fixture
def no_project_groups():
    with mock.patch.object(
        keycloak_service, "get_project_role", lambda u, p: None
    ):
        yield


class TestCriticalExceptions:
    def test_a_critical_finding_is_covered_only_by_a_reviewer_approval(self):
        project_admin_approval = _exception(
            approved_at=TODAY, approved_by_reviewer=False
        )

        assert (
            store.exception_applies(project_admin_approval, Tier.CORE, TODAY)
            is False
        )

    def test_a_reviewer_approval_covers_a_critical_finding(self):
        reviewer_approval = _exception(
            approved_at=TODAY, approved_by_reviewer=True
        )

        assert (
            store.exception_applies(reviewer_approval, Tier.CORE, TODAY)
            is True
        )

    def _approve(self, factory, token, tier):
        with factory() as db:
            db.add(_exception())
            db.commit()
            row_id = db.query(SecurityException).one().id
        with (
            factory() as db,
            mock.patch.object(
                security, "_exception_tier", lambda db, row: tier
            ),
            mock.patch.object(security, "_require_admin", mock.AsyncMock()),
            mock.patch.object(
                security,
                "_sync_exceptions_file",
                mock.AsyncMock(return_value=None),
            ),
        ):
            asyncio.run(security.approve_exception(row_id, token, db))
            return db.get(SecurityException, row_id)

    def test_a_project_admin_cannot_approve_a_critical_exception(
        self, session_factory
    ):
        with pytest.raises(HTTPException) as exc:
            self._approve(session_factory, PROJECT_ADMIN, Tier.CORE)

        assert exc.value.status_code == 403

    def test_an_unknown_tier_is_treated_as_critical(self, session_factory):
        with pytest.raises(HTTPException) as exc:
            self._approve(session_factory, PROJECT_ADMIN, None)

        assert exc.value.status_code == 403

    def test_the_auditor_approves_a_critical_exception(self, session_factory):
        row = self._approve(session_factory, AUDITOR, Tier.CORE)

        assert row.approved_by_reviewer is True


class TestAuditorReadsOnly:
    def test_auditor_cannot_write_on_a_project_it_is_not_member_of(
        self, session_factory, no_project_groups
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                security._require_member(
                    AUDITOR, "shop", "developer", write=True
                )
            )

        assert exc.value.status_code == 403

    def test_auditor_reads_activity_everywhere(self, no_project_groups):
        assert asyncio.run(activity._role(AUDITOR, "shop")) == "platform"

    def test_auditor_cannot_read_application_logs(
        self, session_factory, no_project_groups
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(activity._role(AUDITOR, "shop", app_logs=True))

        assert exc.value.status_code == 403


class TestDeploymentListing:
    @pytest.fixture
    def apps(self, session_factory):
        with session_factory() as db:
            db.add(
                Deployment(
                    name="shop-db",
                    template_id="k3s-gitops-app",
                    project_id="shop",
                    status=DeploymentStatus.RUNNING,
                    terraform_outputs=json.dumps({"url": "https://shop"}),
                )
            )
            db.commit()
        with mock.patch.object(
            deployments, "_user_project_names", lambda db, token: []
        ):
            yield session_factory

    def test_auditor_lists_every_app_without_its_outputs(self, apps):
        with apps() as db:
            listed = deployments.list_deployments(AUDITOR, db)

        assert [(d.name, d.terraform_outputs) for d in listed] == [
            ("shop-db", None)
        ]

    def test_finops_sees_no_app_outside_its_projects(self, apps):
        with apps() as db:
            listed = deployments.list_deployments(FINOPS, db)

        assert listed == []

    def test_finops_holds_no_role_on_a_project(
        self, session_factory, no_project_groups
    ):
        from app.services import authz

        assert authz.project_role_or_none(FINOPS, "shop") is None
