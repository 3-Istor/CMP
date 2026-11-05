import asyncio

import pytest

from app.services.agents import repo
from app.services.release.tags import release_choices


def _choices(tags, staged):
    return [(c.kind, c.tag) for c in release_choices(tags, staged)]


@pytest.mark.parametrize(
    "tags,staged,expected",
    [
        # no tag at all: patch and minor collapse into one entry
        ([], False, [("patch", "v0.1.0"), ("major", "v1.0.0")]),
        ([], True, [("patch", "v0.1.0-rc.1"), ("major", "v1.0.0-rc.1")]),
        # final tags only
        (
            ["v1.0.0", "v1.1.0"],
            False,
            [
                ("patch", "v1.1.1"),
                ("minor", "v1.2.0"),
                ("major", "v2.0.0"),
            ],
        ),
        (
            ["v1.0.0", "v1.1.0"],
            True,
            [
                ("patch", "v1.1.1-rc.1"),
                ("minor", "v1.2.0-rc.1"),
                ("major", "v2.0.0-rc.1"),
            ],
        ),
        # release candidate in progress
        (
            ["v1.2.0-rc.1", "v1.2.0-rc.2", "v1.2.0-rc.3"],
            True,
            [
                ("candidate", "v1.2.0-rc.4"),
                ("patch", "v1.2.1-rc.1"),
                ("minor", "v1.3.0-rc.1"),
                ("major", "v2.0.0-rc.1"),
            ],
        ),
        # candidate promoted to a final tag
        (
            ["v1.2.0-rc.3", "v1.2.0"],
            True,
            [
                ("patch", "v1.2.1-rc.1"),
                ("minor", "v1.3.0-rc.1"),
                ("major", "v2.0.0-rc.1"),
            ],
        ),
        # app without staging keeps its old candidates out of the way
        (
            ["v1.2.0-rc.3"],
            False,
            [
                ("patch", "v1.2.1"),
                ("minor", "v1.3.0"),
                ("major", "v2.0.0"),
            ],
        ),
        # unparsable tags are ignored
        (
            [
                "latest",
                "1.2.3",
                "v1.2",
                "v9.9.9-beta.1",
                "v9.9.9-rc.x",
                "v9.9.9-rc.1+build",
                "release-v8.0.0",
                "v1.0.0",
            ],
            True,
            [
                ("patch", "v1.0.1-rc.1"),
                ("minor", "v1.1.0-rc.1"),
                ("major", "v2.0.0-rc.1"),
            ],
        ),
        # a candidate below the latest final is not in progress
        (
            ["v1.2.0-rc.3", "v1.3.0"],
            True,
            [
                ("patch", "v1.3.1-rc.1"),
                ("minor", "v1.4.0-rc.1"),
                ("major", "v2.0.0-rc.1"),
            ],
        ),
    ],
)
def test_release_choices(tags, staged, expected):
    assert _choices(tags, staged) == expected


@pytest.fixture
def github(monkeypatch):
    sent = []

    async def fake_request(client, method, url, token, **kwargs):
        sent.append((method, url, kwargs))
        if url.endswith("/tags"):
            return [{"name": "v1.0.0"}, {"name": "v1.1.0-rc.1"}]
        if "/commits/" in url:
            return {"sha": "abc123", "commit": {"message": "fix: x\n\nbody"}}
        if "/git/ref/heads/" in url:
            return {"object": {"sha": "headsha"}}
        return {}

    monkeypatch.setattr(repo, "_request", fake_request)
    return sent


def test_list_tags_returns_the_first_hundred_names(github):
    names = asyncio.run(repo.list_tags("tok", "org/app"))

    assert names == ["v1.0.0", "v1.1.0-rc.1"]
    assert github[0][2]["params"] == {"per_page": 100}
    assert github[0][1].endswith("/repos/org/app/tags")


def test_head_commit_returns_sha_and_subject(github):
    assert asyncio.run(repo.head_commit("tok", "org/app", "dev")) == (
        "abc123",
        "fix: x",
    )
    assert github[0][1].endswith("/repos/org/app/commits/dev")


def test_create_tag_on_a_given_sha_skips_the_branch_lookup(github):
    sha = asyncio.run(repo.create_tag("tok", "org/app", "v1.0.0", sha="abc"))

    assert sha == "abc"
    assert [m for m, _, _ in github] == ["POST"]
    assert github[0][2]["json"] == {"ref": "refs/tags/v1.0.0", "sha": "abc"}


def test_create_tag_defaults_to_the_branch_head(github):
    sha = asyncio.run(repo.create_tag("tok", "org/app", "v1.0.0"))

    assert sha == "headsha"
    assert github[1][2]["json"]["sha"] == "headsha"
