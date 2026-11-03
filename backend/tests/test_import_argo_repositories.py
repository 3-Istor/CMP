"""Authorized external imports alone may create project-scoped Argo access."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import repository_imports as service


@pytest.fixture
def job():
    return SimpleNamespace(
        installation_id="321",
        project_name="joetest",
        repository_url="https://github.com/developer/private-repo",
        status="deploying",
        source_sha="a" * 40,
    )


def response(job, code=200, **changes):
    return httpx.Response(
        code,
        request=httpx.Request(
            "GET", "https://argo.example/api/v1/repositories"
        ),
        json={
            "repo": job.repository_url,
            "project": job.project_name,
            "githubAppID": service.GITHUB_APP_ID,
            "githubAppInstallationID": int(job.installation_id),
            "connectionState": {"status": "Successful"},
            **changes,
        },
    )


def listing(job, **changes):
    item = response(job, **changes).json()
    return httpx.Response(
        200,
        request=httpx.Request(
            "GET", "https://argo.example/api/v1/repositories"
        ),
        json={"items": [item]},
    )


def empty_listing():
    return httpx.Response(
        200,
        request=httpx.Request(
            "GET", "https://argo.example/api/v1/repositories"
        ),
        json={"items": []},
    )


@pytest.mark.parametrize("control", ["user", "project", "repository"])
def test_refused_portal_permission_prevents_every_argo_request(job, control):
    db = Mock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = job
    with patch.object(
        service,
        "authorize",
        AsyncMock(side_effect=HTTPException(403, control)),
    ), patch.object(service.httpx, "get") as get, patch.object(
        service.httpx, "post"
    ) as post:
        with pytest.raises(HTTPException) as error:
            service.register_argo_repository(db, SimpleNamespace(id=1))
    assert error.value.status_code == 403
    get.assert_not_called()
    post.assert_not_called()


def test_changed_source_prevents_argo_registration(job):
    db = Mock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = job
    with patch.object(
        service, "authorize", AsyncMock(return_value="read-token")
    ), patch.object(
        service, "head", AsyncMock(return_value="b" * 40)
    ), patch.object(
        service.httpx, "get"
    ) as get:
        with pytest.raises(HTTPException):
            service.register_argo_repository(db, SimpleNamespace(id=1))
    get.assert_not_called()


@pytest.mark.parametrize("owner", ["3-Istor", "3-istor", "3-ISTOR"])
def test_platform_repos_use_existing_owner_credentials_without_declaration(
    job, owner
):
    job.repository_url = f"https://github.com/{owner}/private-repo"
    with patch.object(
        service, "deployment_gate", return_value=job
    ) as gate, patch.object(service.httpx, "get") as get, patch.object(
        service.httpx, "post"
    ) as post:
        assert service.register_argo_repository(Mock(), Mock()) is job
    gate.assert_called_once()
    get.assert_not_called()
    post.assert_not_called()


def test_external_private_repo_uses_job_installation_and_project(job):
    with patch.object(
        service, "deployment_gate", return_value=job
    ) as gate, patch.object(
        settings, "GITHUB_APP_PRIVATE_KEY", "test-only-private-key"
    ), patch.object(
        service.httpx, "get", side_effect=[empty_listing(), response(job)]
    ) as get, patch.object(
        service.httpx,
        "post",
        return_value=httpx.Response(
            200,
            request=httpx.Request(
                "POST", "https://argo.example/api/v1/repositories"
            ),
            json={"repo": job.repository_url, "type": "git"},
        ),
    ) as post:
        assert service.register_argo_repository(Mock(), Mock()) is job
    assert gate.call_count == 2
    assert get.call_args.kwargs["params"] == {
        "appProject": "joetest",
        "forceRefresh": "true",
    }
    body = post.call_args.kwargs["json"]
    assert body["project"] == "joetest"
    assert body["repo"] == job.repository_url
    assert body["githubAppInstallationID"] == 321
    assert body["githubAppPrivateKey"] == "test-only-private-key"
    assert "upsert" not in post.call_args.kwargs


def test_revocation_after_argo_lookup_prevents_the_create(job):
    with patch.object(
        service,
        "deployment_gate",
        side_effect=[job, HTTPException(403, "revoked")],
    ), patch.object(
        service.httpx, "get", return_value=empty_listing()
    ), patch.object(
        service.httpx, "post"
    ) as post:
        with pytest.raises(HTTPException):
            service.register_argo_repository(Mock(), Mock())
    post.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"githubAppInstallationID": 123},
        {"githubAppID": 1},
        {"connectionState": {"status": "Failed"}},
    ],
)
def test_existing_credentials_must_match_without_overwriting_them(
    job, changes
):
    with patch.object(
        service, "deployment_gate", return_value=job
    ), patch.object(
        service.httpx,
        "get",
        side_effect=[listing(job, **changes), response(job, **changes)],
    ), patch.object(
        service.httpx, "post"
    ) as post:
        with pytest.raises(HTTPException):
            service.register_argo_repository(Mock(), Mock())
    post.assert_not_called()


def test_existing_verified_external_repo_is_idempotent(job):
    with patch.object(
        service, "deployment_gate", return_value=job
    ), patch.object(
        service.httpx, "get", side_effect=[listing(job), response(job)]
    ), patch.object(
        service.httpx, "post"
    ) as post:
        assert service.register_argo_repository(Mock(), Mock()) is job
    post.assert_not_called()


def test_argo_failure_is_redacted_and_has_no_secret_exception_chain(job):
    with patch.object(
        service, "deployment_gate", return_value=job
    ), patch.object(
        service.httpx,
        "get",
        side_effect=httpx.ReadTimeout("private-upstream-payload"),
    ):
        with pytest.raises(HTTPException) as error:
            service.register_argo_repository(Mock(), Mock())
    assert error.value.status_code == 502
    assert "private-upstream-payload" not in str(error.value.detail)
    assert error.value.__suppress_context__


def test_same_external_repo_in_other_project_gets_separate_credentials(job):
    with patch.object(
        service, "deployment_gate", return_value=job
    ), patch.object(
        service.httpx,
        "get",
        side_effect=[listing(job, project="other-project"), response(job)],
    ), patch.object(
        service.httpx, "post", return_value=response(job)
    ) as post:
        assert service.register_argo_repository(Mock(), Mock()) is job
    assert post.call_args.kwargs["json"]["project"] == "joetest"


def test_failed_connection_after_minimal_create_response_blocks_apply(job):
    with patch.object(
        service, "deployment_gate", return_value=job
    ), patch.object(
        service.httpx,
        "get",
        side_effect=[
            empty_listing(),
            response(job, connectionState={"status": "Failed"}),
        ],
    ), patch.object(
        service.httpx,
        "post",
        return_value=httpx.Response(
            200,
            request=httpx.Request(
                "POST", "https://argo.example/api/v1/repositories"
            ),
            json={"repo": job.repository_url},
        ),
    ):
        with pytest.raises(HTTPException) as error:
            service.register_argo_repository(Mock(), Mock())
    assert error.value.status_code == 502


def test_stale_success_on_existing_repo_requires_fresh_connection_check(job):
    with patch.object(
        service, "deployment_gate", return_value=job
    ), patch.object(
        service.httpx,
        "get",
        side_effect=[
            listing(job),
            response(job, connectionState={"status": "Failed"}),
        ],
    ) as get, patch.object(
        service.httpx, "post"
    ) as post:
        with pytest.raises(HTTPException):
            service.register_argo_repository(Mock(), Mock())
    assert get.call_args.kwargs["params"]["forceRefresh"] == "true"
    post.assert_not_called()
