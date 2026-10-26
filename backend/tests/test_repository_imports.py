"""Import protocol tests: real DB and JWT signatures, mocked external systems."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.deployment import Deployment
from app.models.project import Project
from app.models.repository_import import RepositoryImport
from app.services import repository_imports as service
from app.services.terraform_executor import TerraformExecutor, create_executor


class ImportProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.job = RepositoryImport(
            id="12345678-1234-1234-1234-123456789012",
            project_name="sandbox",
            app_name="web",
            repository_url="https://github.com/example/original",
            user_sub="user-id",
            username="joe",
            installation_id="11",
            status="preparing",
            run_id="123",
            source_sha="a" * 40,
            report={},
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(hours=1),
            configuration={
                "target_cloud": "onprem",
                "image": "ghcr.io/example/imports/web:import-123",
                "chart_sha": "b" * 40,
            },
        )
        self.db.add(self.job)
        self.db.commit()
        self.tasks = BackgroundTasks()
        self.claims = {"run_id": "123"}

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def invoke(self, action, payload=None):
        with patch.object(
            service, "authorize", AsyncMock(return_value="read-token")
        ), patch.object(service, "head", AsyncMock(return_value="a" * 40)):
            return await service.worker(
                self.db,
                self.job,
                self.claims,
                action,
                payload or {},
                self.tasks,
            )

    async def test_missing_scan_prevents_build_and_deployment(self):
        with self.assertRaises(HTTPException):
            await self.invoke(
                "validated",
                {
                    "source_sha": "a" * 40,
                    "report": {"security": {"blocked": False}},
                },
            )
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.db.query(Deployment).count(), 0)
        self.assertEqual(len(self.tasks.tasks), 0)

    async def test_failures_and_permission_revocation_stop_next_step(self):
        with patch.object(
            service,
            "authorize",
            AsyncMock(side_effect=HTTPException(403, "revoked")),
        ):
            with self.assertRaises(HTTPException):
                await service.worker(
                    self.db, self.job, self.claims, "write", {}, self.tasks
                )
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(len(self.tasks.tasks), 0)

    async def test_wrong_runner_cannot_take_over(self):
        self.claims = {"run_id": "999"}
        with self.assertRaises(HTTPException):
            await self.invoke("write")
        self.assertEqual(self.job.status, "preparing")

    async def test_waiting_pr_and_cancelled_jobs_cannot_continue(self):
        for status in ("waiting_pr", "cancelled", "failed"):
            self.job.status = status
            self.db.commit()
            with self.assertRaises(HTTPException):
                await self.invoke(
                    "complete",
                    {
                        "digest": "sha256:" + "b" * 64,
                        "image": self.job.configuration["image"],
                    },
                )
        self.assertEqual(self.db.query(Deployment).count(), 0)

    async def test_out_of_order_deployment_stops_the_pipeline(self):
        with self.assertRaises(HTTPException):
            await self.invoke(
                "complete",
                {
                    "digest": "sha256:" + "b" * 64,
                    "image": self.job.configuration["image"],
                },
            )
        self.assertEqual(self.job.status, "failed")

    async def test_final_source_change_prevents_deployment(self):
        self.job.status = "publishing"
        self.db.commit()
        with patch.object(
            service, "authorize", AsyncMock(return_value="token")
        ), patch.object(service, "head", AsyncMock(return_value="b" * 40)):
            with self.assertRaises(HTTPException):
                await service.worker(
                    self.db,
                    self.job,
                    self.claims,
                    "complete",
                    {
                        "digest": "sha256:" + "b" * 64,
                        "image": self.job.configuration["image"],
                    },
                    self.tasks,
                )
        self.assertEqual(self.db.query(Deployment).count(), 0)

    async def test_valid_completion_creates_one_existing_repo_deployment(self):
        self.job.status = "publishing"
        self.db.commit()
        payload = {
            "digest": "sha256:" + "b" * 64,
            "image": self.job.configuration["image"],
        }
        await self.invoke("complete", payload)
        deployment = self.db.query(Deployment).one()
        config = json.loads(deployment.app_config)
        self.assertEqual(config["repository_mode"], "existing")
        self.assertEqual(deployment.github_repo_url, self.job.repository_url)
        self.assertEqual(deployment.project_id, "sandbox")
        with self.assertRaises(HTTPException):
            await self.invoke("complete", payload)
        self.assertEqual(self.db.query(Deployment).count(), 1)
        self.assertEqual(len(self.tasks.tasks), 1)

    async def test_branch_recipe_runs_pr_resume_scan_publish_and_single_deployment(
        self,
    ):
        self.db.add(Project(project_name="sandbox", owner_username="joe"))
        self.job.status, self.job.run_id = "queued", None
        self.job.configuration = {
            **self.job.configuration,
            "source_branch": "import-fichier-joe-raph",
            "runtime_environment": {"APP_ENV": "test", "AUTH_MODE": "demo"},
            "runtime_secret_keys": [],
        }
        self.db.commit()
        pr = {
            "head": {"ref": f"cnp-import/{self.job.id}", "sha": "c" * 40},
            "base": {"ref": "import-fichier-joe-raph"},
            "state": "open",
            "user": {"id": 1},
        }
        reviews = [
            {"user": {"id": 2}, "state": "APPROVED", "commit_id": "c" * 40}
        ]
        with patch.object(
            service, "authorize", AsyncMock(return_value="test")
        ), patch.object(
            service, "head", AsyncMock(return_value="a" * 40)
        ) as head, patch.object(
            service,
            "platform_chart_token",
            AsyncMock(return_value="test-chart"),
        ), patch.object(
            service, "scoped_token", AsyncMock(return_value="test-write")
        ), patch.object(
            service,
            "github",
            AsyncMock(
                side_effect=[
                    pr,
                    {**pr, "merged": True},
                    reviews,
                    {**pr, "merged": True},
                    reviews,
                ]
            ),
        ), patch.object(
            service, "dispatch", AsyncMock()
        ) as dispatch:

            async def step(action, payload=None):
                return await service.worker(
                    self.db,
                    self.job,
                    self.claims,
                    action,
                    payload or {},
                    self.tasks,
                )

            contract = await step("claim")
            self.assertEqual(
                contract["source_branch"], "import-fichier-joe-raph"
            )
            self.assertEqual(
                contract["runtime_environment"]["AUTH_MODE"], "demo"
            )
            await step("write")
            await step("prepared", {"pull_request": 7, "report": {}})
            self.assertEqual(self.job.status, "waiting_pr")
            self.assertEqual(self.db.query(Deployment).count(), 0)
            self.assertEqual(len(self.tasks.tasks), 0)
            await service.resume(self.db, self.job)
            dispatch.assert_awaited_once()
            self.assertEqual(self.job.status, "queued")
            self.claims = {"run_id": "456"}
            head.return_value = "c" * 40
            await step("claim")
            await step(
                "validated",
                {
                    "source_sha": "c" * 40,
                    "report": {
                        "security": {"blocked": False},
                        "deployment": {"blocked": False},
                        "rendered_deployment": {"blocked": False},
                    },
                },
            )
            self.assertEqual(self.job.status, "building")
            await step(
                "publish",
                {
                    "image": self.job.configuration["image"],
                    "source_sha": "c" * 40,
                    "image_size": 1234,
                    "image_scan_passed": True,
                    "http_check_passed": True,
                },
            )
            payload = {
                "digest": "sha256:" + "d" * 64,
                "image": self.job.configuration["image"],
            }
            await step("complete", payload)
            with self.assertRaises(HTTPException):
                await step("complete", payload)
        self.assertEqual(self.job.status, "deploying")
        self.assertEqual(self.job.source_sha, "c" * 40)
        self.assertEqual(self.db.query(Project).count(), 1)
        deployment = self.db.query(Deployment).one()
        self.assertEqual(deployment.project_id, "sandbox")
        self.assertEqual(deployment.github_repo_url, self.job.repository_url)
        self.assertEqual(len(self.tasks.tasks), 1)

    async def test_unmerged_pr_prevents_resume_and_dispatch(self):
        self.job.status = "waiting_pr"
        self.job.pull_request = 7
        self.db.commit()
        with patch.object(
            service, "authorize", AsyncMock(return_value="token")
        ), patch.object(
            service, "github", AsyncMock(return_value={"merged": False})
        ), patch.object(
            service, "dispatch", AsyncMock()
        ) as dispatch:
            with self.assertRaises(HTTPException):
                await service.resume(self.db, self.job)
            dispatch.assert_not_called()
        self.assertEqual(self.job.status, "waiting_pr")

    async def test_publish_rejects_missing_scan_or_oversize_image(self):
        self.job.status = "building"
        self.db.commit()
        with self.assertRaises(HTTPException):
            await self.invoke(
                "publish",
                {
                    "image": self.job.configuration["image"],
                    "source_sha": self.job.source_sha,
                    "image_size": 2**40,
                    "image_scan_passed": True,
                },
            )
        self.assertEqual(self.job.status, "failed")

    async def test_latest_pr_revision_requires_independent_approval(self):
        self.job.status, self.job.pull_request = "waiting_pr", 7
        self.db.commit()
        pr = {
            "merged": True,
            "base": {"ref": "main"},
            "user": {"id": 1},
            "head": {"ref": f"cnp-import/{self.job.id}", "sha": "a" * 40},
        }
        approval = {
            "user": {"id": 2},
            "state": "APPROVED",
            "commit_id": "a" * 40,
        }
        for reviews in (
            [],
            [{**approval, "commit_id": "c" * 40}],
            [approval, {**approval, "state": "DISMISSED"}],
            [
                approval,
                {**approval, "user": {"id": 3}, "state": "CHANGES_REQUESTED"},
            ],
        ):
            with patch.object(
                service, "github", AsyncMock(side_effect=[pr, reviews])
            ), patch.object(service, "dispatch", AsyncMock()) as dispatch:
                with self.assertRaises(HTTPException):
                    await service.approved_pr(self.job, "read")
                dispatch.assert_not_called()
        with patch.object(
            service, "authorize", AsyncMock(return_value="read")
        ), patch.object(
            service, "github", AsyncMock(side_effect=[pr, [approval]])
        ), patch.object(
            service, "dispatch", AsyncMock()
        ) as dispatch:
            await service.resume(self.db, self.job)
            dispatch.assert_awaited_once()
        self.assertEqual(self.job.status, "queued")
        self.assertIsNone(self.job.run_id)

    async def test_retry_recovers_existing_pr_without_duplicate_dispatch(self):
        self.job.status = "failed"
        self.db.commit()
        with patch.object(
            service, "authorize", AsyncMock(return_value="read")
        ), patch.object(
            service,
            "github",
            AsyncMock(return_value=[{"number": 7, "state": "open"}]),
        ), patch.object(
            service, "dispatch", AsyncMock()
        ) as dispatch:
            await service.retry(self.db, self.job)
        dispatch.assert_not_called()
        self.assertEqual(self.job.status, "waiting_pr")
        self.assertEqual(self.job.pull_request, 7)

    async def test_retry_cannot_duplicate_existing_deployment(self):
        self.job.status = "publishing"
        self.db.commit()
        await self.invoke(
            "complete",
            {
                "image": self.job.configuration["image"],
                "digest": "sha256:" + "b" * 64,
            },
        )
        self.job.status = "failed"
        self.db.commit()
        with self.assertRaises(HTTPException):
            await service.retry(self.db, self.job)
        self.assertEqual(self.db.query(Deployment).count(), 1)

    async def test_legacy_config_write_cannot_bypass_import_scans(self):
        from app.routers.deployments import update_deployment_config

        self.job.status = "publishing"
        self.db.commit()
        await self.invoke(
            "complete",
            {
                "image": self.job.configuration["image"],
                "digest": "sha256:" + "b" * 64,
            },
        )
        deployment = self.db.query(Deployment).one()
        deployment.status = service.DeploymentStatus.RUNNING
        self.db.commit()
        with patch(
            "app.routers.deployments.imported_portal_guard",
            return_value=self.job,
        ), patch.object(
            service.keycloak_service, "get_project_role", return_value="admin"
        ), patch(
            "app.routers.deployments.put_file_content", AsyncMock()
        ) as commit:
            with self.assertRaises(HTTPException) as error:
                await update_deployment_config(
                    deployment_id=deployment.id,
                    request=Mock(),
                    payload={"_sha": "old", "replicaCount": 3},
                    token={"sub": "user-id"},
                    component=None,
                    db=self.db,
                    credentials=None,
                )
        self.assertEqual(error.exception.status_code, 409)
        commit.assert_not_called()

    async def test_new_security_panel_cannot_write_imported_source(self):
        from app.routers.deployments import update_security_data
        from app.services.app_security_data import SecurityDataUpdate

        self.job.status = "publishing"
        self.db.commit()
        await self.invoke(
            "complete",
            {
                "image": self.job.configuration["image"],
                "digest": "sha256:" + "b" * 64,
            },
        )
        deployment = self.db.query(Deployment).one()
        with patch.object(
            service.keycloak_service, "get_project_role", return_value="admin"
        ), patch(
            "app.routers.deployments._get_github_token_for_deployment",
            AsyncMock(),
        ) as token, patch(
            "app.routers.deployments.put_file_content", AsyncMock()
        ) as commit:
            with self.assertRaises(HTTPException) as error:
                await update_security_data(
                    deployment_id=deployment.id,
                    payload=SecurityDataUpdate(exposure="public"),
                    request=Mock(),
                    token_payload={"sub": "user-id"},
                    db=self.db,
                )
        self.assertEqual(error.exception.status_code, 409)
        token.assert_not_called()
        commit.assert_not_called()

    async def test_finops_cannot_write_imported_source(self):
        from app.routers.finops import _apply_patch_to_gitops

        self.job.status = "publishing"
        self.db.commit()
        await self.invoke(
            "complete",
            {
                "image": self.job.configuration["image"],
                "digest": "sha256:" + "b" * 64,
            },
        )
        deployment = self.db.query(Deployment).one()
        with patch(
            "app.services.github_service.put_file_content", AsyncMock()
        ) as commit:
            with self.assertRaises(HTTPException) as error:
                await _apply_patch_to_gitops(
                    deployment, {"replicaCount": 2}, self.db
                )
        self.assertEqual(error.exception.status_code, 409)
        commit.assert_not_called()

    async def test_security_settings_and_exception_copy_preserve_imported_source(
        self,
    ):
        from app.routers.security import (
            _sync_exceptions_file,
            _write_app_settings,
        )

        self.job.status = "publishing"
        self.db.commit()
        await self.invoke(
            "complete",
            {
                "image": self.job.configuration["image"],
                "digest": "sha256:" + "b" * 64,
            },
        )
        deployment = self.db.query(Deployment).one()
        with patch(
            "app.routers.security._github_token", AsyncMock()
        ) as token, patch(
            "app.routers.security.put_file_content", AsyncMock()
        ) as commit:
            with self.assertRaises(HTTPException) as error:
                await _write_app_settings(
                    deployment, "never", "joe", False, self.db
                )
            self.assertEqual(error.exception.status_code, 409)
            result = await _sync_exceptions_file(
                self.db, "sandbox", "web", "joe"
            )
        self.assertIsNone(result)
        token.assert_not_called()
        commit.assert_not_called()

    async def _completed_deployment(self):
        self.job.status = "publishing"
        self.db.commit()
        await self.invoke(
            "complete",
            {
                "image": self.job.configuration["image"],
                "digest": "sha256:" + "b" * 64,
            },
        )
        return self.db.query(Deployment).one()

    async def test_release_operations_cannot_bypass_the_import(self):
        from app.routers import releases

        deployment = await self._completed_deployment()
        source, decide = Mock(), Mock()
        with patch.object(
            releases, "_get_app_or_404", return_value=deployment
        ):
            with self.assertRaises(HTTPException) as error:
                releases._act(self.db, {}, deployment.id, source, decide, None)
        self.assertEqual(error.exception.status_code, 409)
        decide.assert_not_called()
        source.patch_status.assert_not_called()

    async def test_release_settings_stop_before_read_or_write(self):
        from app.routers import releases

        deployment = await self._completed_deployment()
        payload = releases.release_settings.ReleaseSettingsUpdate(
            **releases.release_settings.read({}).model_dump()
        )
        with patch.object(
            releases,
            "_get_kubernetes_deployment_or_404",
            return_value=deployment,
        ), patch.object(releases, "_load_all", AsyncMock()) as files:
            with self.assertRaises(HTTPException) as error:
                await releases.update_release_settings(
                    deployment.id, payload, Mock(), {}, Mock(), self.db
                )
        self.assertEqual(error.exception.status_code, 409)
        files.assert_not_called()

    async def test_staging_stops_before_creating_resources(self):
        from app.routers import environments

        deployment = await self._completed_deployment()
        with patch.object(
            environments,
            "_get_kubernetes_deployment_or_404",
            return_value=deployment,
        ), patch.object(environments.activation, "start_enable") as create:
            with self.assertRaises(HTTPException) as error:
                await environments.enable_staging(
                    deployment.id, environments.EnableRequest(), {}, self.db
                )
        self.assertEqual(error.exception.status_code, 409)
        create.assert_not_called()

    async def test_mcp_source_write_gate_preserves_the_import(self):
        from app import mcp_server

        deployment = await self._completed_deployment()
        factory = sessionmaker(bind=self.engine)
        with patch.object(mcp_server, "SessionLocal", factory):
            with self.assertRaises(HTTPException) as error:
                mcp_server._require_mutable_import(deployment.id)
        self.assertEqual(error.exception.status_code, 409)


class SignedIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )

    def test_unsigned_expired_and_other_workflow_tokens_are_rejected(self):
        now = datetime.now(timezone.utc)
        claims = {
            "sub": "worker",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": "https://token.actions.githubusercontent.com",
            "aud": settings.IMPORT_OIDC_AUDIENCE,
            "repository": settings.IMPORT_WORKFLOW_REPO,
            "workflow_ref": f"{settings.IMPORT_WORKFLOW_REPO}/.github/workflows/{settings.IMPORT_DISPATCH_WORKFLOW}@refs/heads/main",
            "job_workflow_ref": f"{settings.IMPORT_WORKFLOW_REPO}/.github/workflows/import-project.yml@refs/heads/main",
            "job_workflow_sha": "a" * 40,
            "sha": "a" * 40,
            "ref": "refs/heads/main",
            "workflow_sha": "a" * 40,
            "event_name": "workflow_dispatch",
            "run_attempt": "1",
            "run_id": "123",
        }
        client = Mock()
        client.get_signing_key_from_jwt.return_value = SimpleNamespace(
            key=self.key.public_key()
        )
        with patch.object(service, "enabled"), patch.object(
            service, "jwks", return_value=client
        ), patch.object(settings, "IMPORT_WORKFLOW_SHA", "a" * 40):
            valid = jwt.encode(claims, self.key, algorithm="RS256")
            self.assertEqual(
                service.verify_token(valid, worker=True)["run_id"], "123"
            )
            for changes in (
                {"workflow_ref": "attacker/workflow"},
                {"workflow_sha": "b" * 40},
                {"job_workflow_ref": "attacker/workflow"},
                {"job_workflow_sha": "b" * 40},
                {"sha": "b" * 40},
                {"ref": "refs/heads/another-branch"},
                {"exp": now - timedelta(seconds=1)},
                {"run_attempt": "2"},
            ):
                with self.assertRaises(HTTPException):
                    service.verify_token(
                        jwt.encode(
                            {**claims, **changes}, self.key, algorithm="RS256"
                        ),
                        worker=True,
                    )
            with self.assertRaises(HTTPException):
                service.verify_token(
                    jwt.encode(claims, "", algorithm="none"), worker=True
                )

    def test_live_keycloak_refusal_blocks_owner_too(self):
        db = Mock()
        db.query.return_value.filter_by.return_value.one_or_none.return_value = Project(
            project_name="sandbox",
            owner_username="joe",
            status=service.ProjectStatus.ACTIVE,
        )
        with patch.object(
            service.keycloak_service, "_get_admin_token", return_value="token"
        ), patch.object(
            service.keycloak_service,
            "_find_user_by_username",
            return_value={"id": "user-id", "enabled": False},
        ):
            with self.assertRaises(HTTPException):
                service.fresh_project(db, "sandbox", "user-id", "joe")

    def test_tfvars_contain_no_token_and_keep_typed_values(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = TerraformExecutor.__new__(TerraformExecutor)
            executor.working_dir = Path(directory)
            args = executor._variable_args(
                {
                    "repository_mode": "existing",
                    "existing_repository_url": "https://github.com/example/original",
                    "github_token": "private-token",
                }
            )
            self.assertEqual(args, [])
            text = (Path(directory) / "import.auto.tfvars.json").read_text()
            self.assertNotIn("private-token", text)
            self.assertEqual(
                executor._import_sensitive["TF_VAR_github_token"],
                "private-token",
            )

    def test_import_tfvars_are_isolated_from_catalog_and_other_apps(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                template = Path(directory) / "catalog-template"
                template.mkdir()
                (template / "main.tf").write_text('variable "project_name" {}')
                first = create_executor(template, "first", isolated=True)
                second = create_executor(template, "second", isolated=True)
                first._variable_args(
                    {"repository_mode": "existing", "project_name": "one"}
                )
                second._variable_args(
                    {"repository_mode": "existing", "project_name": "two"}
                )
                self.assertNotEqual(first.working_dir, second.working_dir)
                self.assertFalse(
                    (template / "import.auto.tfvars.json").exists()
                )
                self.assertEqual(
                    json.loads(
                        (
                            first.working_dir / "import.auto.tfvars.json"
                        ).read_text()
                    )["project_name"],
                    "one",
                )
                reused = create_executor(template, "first", isolated=True)
                self.assertEqual(reused.working_dir, first.working_dir)
            finally:
                os.chdir(previous)


class RepositoryPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_installation_id_alone_never_grants_repository_access(self):
        job = SimpleNamespace(
            user_sub="user-id",
            repository_url="https://github.com/example/private",
        )
        response = Mock()
        response.json.return_value = []
        with patch.object(
            service.keycloak_service, "_get_admin_token", return_value="admin"
        ), patch.object(
            service.requests, "get", return_value=response
        ), patch.object(
            service, "github", AsyncMock()
        ) as github:
            with self.assertRaises(HTTPException):
                await service.repository_user_permission(job, "read")
        github.assert_not_called()

    async def test_user_permission_is_checked_again_before_writes(self):
        job = SimpleNamespace(
            user_sub="user-id",
            repository_url="https://github.com/example/private",
        )
        response = Mock()
        response.json.return_value = [
            {"identityProvider": "github", "userName": "joe", "userId": "42"}
        ]
        with patch.object(
            service.keycloak_service, "_get_admin_token", return_value="admin"
        ), patch.object(
            service.requests, "get", return_value=response
        ), patch.object(
            service,
            "github",
            AsyncMock(
                side_effect=[
                    {"id": 42},
                    {"permission": "read"},
                    {"id": 42},
                    {"permission": "read"},
                ]
            ),
        ):
            await service.repository_user_permission(job, "read")
            with self.assertRaises(HTTPException):
                await service.repository_user_permission(
                    job, "read", write=True
                )


if __name__ == "__main__":
    unittest.main()
