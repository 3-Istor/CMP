import asyncio

import pytest
from ruamel.yaml import YAML

from app.services.github_service import GitHubAppError, StaleBranchError
from app.services.imports import registry_commit
from app.services.project_registry import RegistryError

RECORD = """apiVersion: cnp.3istor.com/v1alpha1
kind: ProjectRecord
metadata:
  name: shop
spec:
  targetCloud: onprem
  apps: []
"""


class FakeGitHub:
    def __init__(self, failures):
        self.failures = list(failures)
        self.heads = iter(["h1", "h2", "h3", "h4", "h5", "h6"])
        self.commits = []

    async def token(self):
        return "ghs_org"

    async def head(self, token, repo, branch):
        return next(self.heads)

    async def content(self, token, repo, path, ref):
        assert path == "registry/projects/shop.yaml"
        return RECORD, "blob"

    async def commit(self, token, repo, files, message, parent_sha, branch):
        if self.failures:
            raise self.failures.pop(0)
        self.commits.append((parent_sha, files, message))
        return "c0ffee"


@pytest.fixture
def github(monkeypatch):
    def install(*failures):
        fake = FakeGitHub(failures)
        monkeypatch.setattr(
            registry_commit, "get_org_installation_token", fake.token
        )
        monkeypatch.setattr(registry_commit, "get_branch_head", fake.head)
        monkeypatch.setattr(registry_commit, "get_file_content", fake.content)
        monkeypatch.setattr(registry_commit, "commit_files", fake.commit)
        monkeypatch.setattr(registry_commit.asyncio, "sleep", _no_sleep)
        return fake

    return install


async def _no_sleep(seconds):
    return None


async def _add_api(record, head):
    record["spec"]["apps"].append({"name": "api", "type": "backing"})
    return {"apps/shop/api/values.yaml": "image: {}\n"}


def test_record_and_values_land_in_one_commit(github):
    fake = github()

    sha = asyncio.run(
        registry_commit.commit_record_change("shop", _add_api, "feat: x")
    )

    assert sha == "c0ffee"
    parent, files, message = fake.commits[0]
    assert sorted(files) == [
        "apps/shop/api/values.yaml",
        "registry/projects/shop.yaml",
    ]
    record = YAML(typ="safe").load(files["registry/projects/shop.yaml"])
    assert record["spec"]["apps"] == [{"name": "api", "type": "backing"}]
    assert message.endswith("[skip ci]")


@pytest.mark.parametrize(
    "failure",
    [
        StaleBranchError("main moved"),
        GitHubAppError("Failed to commit: HTTP 409: Conflict"),
    ],
)
def test_a_lost_race_is_recomputed_on_the_new_head(github, failure):
    fake = github(failure, failure)

    asyncio.run(
        registry_commit.commit_record_change("shop", _add_api, "feat: x")
    )

    assert [c[0] for c in fake.commits] == ["h3"]
    record = YAML(typ="safe").load(
        fake.commits[0][1]["registry/projects/shop.yaml"]
    )
    assert len(record["spec"]["apps"]) == 1


def test_other_github_errors_are_not_retried(github):
    fake = github(GitHubAppError("HTTP 403: Resource not accessible"))

    with pytest.raises(RegistryError):
        asyncio.run(
            registry_commit.commit_record_change("shop", _add_api, "x")
        )

    assert fake.commits == []


def test_gives_up_after_the_last_attempt(github):
    github(*[StaleBranchError("moved")] * registry_commit.MAX_ATTEMPTS)

    with pytest.raises(RegistryError, match="gave up"):
        asyncio.run(
            registry_commit.commit_record_change("shop", _add_api, "x")
        )
