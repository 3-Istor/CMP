from datetime import datetime

from app.models.deployment import DeploymentStatus
from app.schemas.deployment import DeploymentRead


def _read(step_message: str) -> DeploymentRead:
    return DeploymentRead.model_validate(
        {
            "id": 1,
            "name": "my-app",
            "template_id": "k3s-gitops-app",
            "template_name": None,
            "template_icon": None,
            "template_category": None,
            "status": DeploymentStatus.RUNNING,
            "step_message": step_message,
            "provider_type": "kubernetes",
            "project_id": "self-runner",
            "github_repo_url": None,
            "argocd_app_name": None,
            "k8s_namespace": None,
            "terraform_outputs": None,
            "resource_count": None,
            "created_at": datetime(2026, 11, 2),
            "updated_at": datetime(2026, 11, 2),
        }
    )


def test_stored_step_messages_lose_their_emojis():
    assert (
        _read("✅ Running - app_url: x").step_message == "Running - app_url: x"
    )


def test_plain_step_messages_are_unchanged():
    assert _read("Deploying").step_message == "Deploying"
