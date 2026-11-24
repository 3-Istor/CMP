import pytest

from app.core import swr_cache
from app.services import github_service
from app.services.ai import service as ai_service


@pytest.fixture(autouse=True)
def _no_cache_across_tests():
    github_service.forget_installation_tokens()
    ai_service.forget_backend_status()
    swr_cache.clear_all()
    yield
    github_service.forget_installation_tokens()
    ai_service.forget_backend_status()
    swr_cache.clear_all()
