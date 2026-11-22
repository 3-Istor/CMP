import asyncio

import pytest
from ruamel.yaml import YAML

from app.models.project import TargetCloud
from app.services import project_registry
from app.services.imports import registry_commit
from tests.test_import_registry_commit import FakeGitHub

RECORD = """apiVersion: cnp.3istor.com/v1alpha1
kind: ProjectRecord
metadata:
  name: shop
spec:
  targetCloud: aws
  apps: []
"""
MOVE = {
    "id": "m1",
    "source": "aws",
    "target": "gcp",
    "step": "switch",
    "restores": [
        {"app": "api", "env": "prod", "generation": 2, "sourceServer": "s"}
    ],
}


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub([])
    fake.record = RECORD

    async def content(token, repo, path, ref):
        return fake.record, "blob"

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(
        registry_commit, "get_org_installation_token", fake.token
    )
    monkeypatch.setattr(registry_commit, "get_branch_head", fake.head)
    monkeypatch.setattr(registry_commit, "get_file_content", content)
    monkeypatch.setattr(registry_commit, "commit_files", fake.commit)
    monkeypatch.setattr(registry_commit.asyncio, "sleep", no_sleep)
    return fake


def _written(fake):
    assert len(fake.commits) == 1
    _, files, message = fake.commits[0]
    return files["registry/projects/shop.yaml"], message


def test_set_move_writes_the_dict_and_keeps_spec(github):
    asyncio.run(project_registry.set_move("shop", MOVE))

    text, message = _written(github)
    assert text.startswith(RECORD)
    assert YAML(typ="safe").load(text)["status"] == {"move": MOVE}
    assert message == "feat(registry): set move of shop to gcp [skip ci]"


def test_clearing_the_move_drops_status(github):
    github.record = RECORD + "status:\n  move:\n    id: m1\n    target: gcp\n"

    asyncio.run(project_registry.set_move("shop", None))

    text, _ = _written(github)
    assert "status" not in YAML(typ="safe").load(text)


def test_switch_cloud_is_one_commit(github):
    asyncio.run(project_registry.switch_cloud("shop", MOVE))

    text, _ = _written(github)
    record = YAML(typ="safe").load(text)
    assert record["spec"]["targetCloud"] == "gcp"
    assert record["spec"]["cloudClass"] == "public"
    assert record["status"]["move"] == MOVE


def test_new_records_carry_their_cloud_class():
    aws = YAML(typ="safe").load(
        project_registry.build_record("p", "o", TargetCloud.AWS)
    )
    onprem = YAML(typ="safe").load(
        project_registry.build_record("p", "o", TargetCloud.ONPREM)
    )
    assert aws["spec"]["cloudClass"] == "public"
    assert onprem["spec"]["cloudClass"] == "onprem"
