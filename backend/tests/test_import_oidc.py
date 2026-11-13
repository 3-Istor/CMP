import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings
from app.services.imports import oidc
from app.services.imports.oidc import WorkerTokenError, verify_worker_token

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WORKFLOW_SHA = "a" * 40
WORKFLOW_REF = (
    "3-Istor/extern-project-analyzer/.github/workflows/import.yml"
    "@refs/heads/main"
)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_WORKFLOW_REPO_ID", "1001")
    monkeypatch.setattr(settings, "IMPORT_WORKFLOW_OWNER_ID", "2002")
    monkeypatch.setattr(settings, "IMPORT_WORKFLOW_SHAS", f"{WORKFLOW_SHA}")
    monkeypatch.setattr(oidc, "_signing_key", lambda token: KEY.public_key())


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": oidc.ISSUER,
        "aud": "cmp-import",
        "sub": "repo:3-Istor/extern-project-analyzer:ref:refs/heads/main",
        "iat": now,
        "exp": now + 300,
        "repository": "3-Istor/extern-project-analyzer",
        "repository_id": "1001",
        "repository_owner_id": "2002",
        "workflow_ref": WORKFLOW_REF,
        "job_workflow_ref": WORKFLOW_REF,
        "workflow_sha": WORKFLOW_SHA,
        "job_workflow_sha": WORKFLOW_SHA,
        "ref": "refs/heads/main",
        "event_name": "workflow_dispatch",
        "run_id": "987654321",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _token(key=KEY, **overrides) -> str:
    return jwt.encode(_claims(**overrides), key, algorithm="RS256")


def test_token_of_the_pinned_workflow_is_accepted():
    claims = verify_worker_token(_token())

    assert claims["run_id"] == "987654321"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-audience"},
        {"iss": "https://token.actions.githubusercontent.com.evil"},
        {"exp": int(time.time()) - 600},
        {"repository": "3-Istor/K3s"},
        {"repository_id": "666"},
        {"repository_owner_id": "666"},
        {"workflow_sha": "b" * 40},
        {"job_workflow_sha": "b" * 40},
        {"ref": "refs/heads/feature"},
        {"job_workflow_ref": WORKFLOW_REF.replace("import.yml", "evil.yml")},
        {"workflow_ref": WORKFLOW_REF.replace("main", "dev")},
        {"event_name": "push"},
        {"run_id": "not-a-number"},
        {"run_id": None},
        {"sub": None},
    ],
)
def test_token_with_a_claim_off_the_pin_is_refused(overrides):
    with pytest.raises(WorkerTokenError):
        verify_worker_token(_token(**overrides))


def test_token_signed_by_another_key_is_refused():
    with pytest.raises(WorkerTokenError):
        verify_worker_token(_token(key=OTHER_KEY))


def test_unset_pins_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_WORKFLOW_REPO_ID", "")

    with pytest.raises(WorkerTokenError):
        verify_worker_token(_token(repository_id=""))


def test_empty_sha_allow_list_refuses_every_workflow(monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_WORKFLOW_SHAS", "")

    with pytest.raises(WorkerTokenError):
        verify_worker_token(_token())
