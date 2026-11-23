import pytest

from app.services import github_service
from app.services.ai import service as ai_service


@pytest.fixture(autouse=True)
def _no_cache_across_tests():
    github_service.forget_installation_tokens()
    ai_service.forget_backend_status()
    yield
    github_service.forget_installation_tokens()
    ai_service.forget_backend_status()
