import json
from datetime import datetime

import pytest

from app.services.security import store, upgrade_prs
from app.services.security.model import Audience, Category, Tier


def _finding(key="vuln.fixBump", package="lodash", app="api"):
    i18n = {"fix": {"key": key, "params": {"package": package}}}
    return store.Merged(
        fingerprint="f",
        app=app,
        category=Category.DEPENDENCIES,
        tier=Tier.IMPORTANT,
        audience=Audience.DEVELOPER,
        rule="r",
        title="t",
        detail="",
        fix="",
        location="",
        link=None,
        raw="",
        sources=[],
        first_seen=datetime(2026, 1, 1),
        last_seen=datetime(2026, 1, 1),
        resolved_at=None,
        i18n=json.dumps(i18n),
    )


def _pull(title, login="renovate[bot]", ref="renovate/lodash", body=""):
    return {
        "title": title,
        "body": body,
        "html_url": "https://github.com/o/r/pull/1",
        "head": {"ref": ref},
        "user": {"login": login},
    }


@pytest.mark.parametrize(
    "title, body, package, expected",
    [
        ("Update dependency lodash to v4.17.21", "", "lodash", True),
        ("Bump lodash from 4.17.20 to 4.17.21", "", "lodash", True),
        (
            "chore(deps): update dependency @types/node to v20",
            "",
            "@types/node",
            True,
        ),
        ("Update dependency lodash.merge to v4.6.2", "", "lodash", False),
        (
            "Bump the npm_and_yarn group with 2 updates",
            "Bumps ... [lodash](https://github.com/lodash/lodash) and [axios](...)",
            "lodash",
            True,
        ),
        (
            "Update all non-major dependencies",
            "| [Jinja2](https://...) |",
            "jinja2",
            True,
        ),
        ("Fix login", "uses lodash", "lodash", False),
    ],
)
def test_matches(title, body, package, expected):
    assert (
        upgrade_prs.matches({"title": title, "body": body}, package)
        is expected
    )


def test_only_bot_prs_are_kept():
    upgrade_prs.remember(
        "shop", "api", [_pull("Bump lodash", "alice", "fix/x")]
    )
    assert upgrade_prs.for_finding("shop", _finding()) is None
    upgrade_prs.remember("shop", "api", [_pull("Bump lodash")])
    assert (
        upgrade_prs.for_finding("shop", _finding())
        == "https://github.com/o/r/pull/1"
    )


@pytest.mark.parametrize("key", ["vuln.fixBase", "vuln.noFix"])
def test_a_finding_without_a_bump_gets_no_pr(key):
    upgrade_prs.remember("shop", "api", [_pull("Bump lodash")])
    assert upgrade_prs.for_finding("shop", _finding(key)) is None
