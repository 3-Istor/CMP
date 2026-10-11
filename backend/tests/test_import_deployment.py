"""An accepted import must still stop on deployment permission/readiness failures."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.services import repository_imports as service
from app.services import terraform_orchestrator as orchestrator
from app.services.project_registry import RegistryError


class DeploymentGatesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.deployment = Deployment(
            name="web",
            project_id="sandbox",
            template_id="k3s-gitops-app",
            provider_type=ProviderType.KUBERNETES,
            status=DeploymentStatus.PENDING,
            app_config=json.dumps(
                {
                    "repository_mode": "existing",
                    "existing_repository_url": "https://github.com/example/original",
                    "github_owner": "example",
                    "github_installation_id": "11",
                }
            ),
        )
        self.db.add(self.deployment)
        self.db.commit()
        self.job = SimpleNamespace(
            project_name="sandbox",
            app_name="web",
            source_sha="a" * 40,
            repository_url="https://github.com/example/original",
            configuration={
                "chart_sha": "b" * 40,
                "target_cloud": "onprem",
                "image": "ghcr.io/example/web:import-test",
            },
            report={"digest": "sha256:" + "c" * 64},
        )
        self.directory = tempfile.TemporaryDirectory()
        self.template = Path(self.directory.name)
        (self.template / "variables.tf").write_text(
            'variable "repository_mode" {}'
        )
        self.executor = Mock(state_dir=self.template)
        self.executor.plan.return_value = "plan"
        self.executor.apply.return_value = {
            "hostname": "web.example.org",
            "app_url": "https://web.example.org",
        }
        self.executor.get_state_summary.return_value = {"resource_count": 3}

    def tearDown(self):
        self.directory.cleanup()
        self.db.close()
        self.engine.dispose()

    def run_pipeline(self, gate=None, registry_error=None, ready=True):
        registry = AsyncMock(side_effect=registry_error)
        with patch.object(
            orchestrator, "SessionLocal", return_value=self.db
        ), patch.object(orchestrator, "get_repository") as repo, patch.object(
            orchestrator, "create_executor", return_value=self.executor
        ), patch.object(
            service, "deployment_gate", gate or Mock(return_value=self.job)
        ), patch.object(
            service, "register_argo_repository"
        ), patch.object(
            service, "wait_for_argo"
        ) as argo, patch.object(
            service, "scoped_token", AsyncMock(return_value="read-only")
        ), patch(
            "app.services.project_registry.register_app", registry
        ), patch.object(
            orchestrator, "_wait_for_url_ready", return_value=ready
        ) as http, patch.object(
            orchestrator, "logger"
        ):
            repo.return_value.get_template_by_id.return_value = {
                "name": "Import",
                "_template_path": str(self.template),
            }
            orchestrator.run_deployment(self.deployment.id)
        return registry, argo, http

    def test_permission_revoked_before_apply_prevents_mutations(self):
        gate = Mock(
            side_effect=[self.job, self.job, HTTPException(403, "revoked")]
        )
        registry, argo, http = self.run_pipeline(gate=gate)
        self.executor.apply.assert_not_called()
        registry.assert_not_called()
        argo.assert_not_called()
        http.assert_not_called()
        self.assertEqual(self.deployment.status, DeploymentStatus.FAILED)

    def test_registry_failure_prevents_readiness_and_running_status(self):
        registry, argo, http = self.run_pipeline(
            registry_error=RegistryError("registry unavailable")
        )
        self.executor.apply.assert_called_once()
        registry.assert_awaited_once()
        argo.assert_not_called()
        http.assert_not_called()
        self.assertEqual(self.deployment.status, DeploymentStatus.FAILED)

    def test_availability_timeout_never_marks_running(self):
        registry, argo, http = self.run_pipeline(ready=False)
        self.assertEqual(self.deployment.status, DeploymentStatus.FAILED)
        self.assertEqual(
            registry.call_args.kwargs["source_revision"], "a" * 40
        )
        self.assertEqual(registry.call_args.kwargs["chart_revision"], "b" * 40)
        argo.assert_called_once()
        http.assert_called_once()

    def test_argo_requires_both_actual_revisions_and_correct_destination(self):
        app = {
            "metadata": {"name": "sandbox-web-app"},
            "spec": {
                "project": "sandbox",
                "destination": {"name": "onprem", "namespace": "sandbox-web"},
                "sources": [
                    {
                        "targetRevision": "b" * 40,
                        "helm": {
                            "values": 'image: {tag: "import-test@sha256:'
                            + "c" * 64
                            + '"}'
                        },
                    },
                    {
                        "repoURL": self.job.repository_url,
                        "targetRevision": "a" * 40,
                    },
                ],
            },
            "status": {
                "health": {"status": "Healthy"},
                "sync": {
                    "status": "Synced",
                    "revisions": ["b" * 40, "a" * 40],
                },
            },
        }
        response = Mock()
        response.json.return_value = {"items": [app]}
        with patch.object(service.httpx, "get", return_value=response):
            service.wait_for_argo(self.job, timeout=1)
            app["status"]["sync"]["revisions"][0] = "c" * 40
            with patch("time.monotonic", side_effect=[0, 0, 2]), patch(
                "time.sleep"
            ):
                with self.assertRaises(HTTPException):
                    service.wait_for_argo(self.job, timeout=1)
            app["status"]["sync"]["revisions"][0] = "b" * 40
            app["spec"]["destination"]["namespace"] = "other-project"
            with patch("time.monotonic", side_effect=[0, 0, 2]), patch(
                "time.sleep"
            ):
                with self.assertRaises(HTTPException):
                    service.wait_for_argo(self.job, timeout=1)
