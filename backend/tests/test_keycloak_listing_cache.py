from unittest import mock

import pytest

from app.services import keycloak_service as kc

GROUPS = [{"name": "project-demo-members"}]


@pytest.fixture(autouse=True)
def _clean():
    kc._listing_cache.clear()
    yield
    kc._listing_cache.clear()


def test_listing_is_served_from_cache_within_the_ttl():
    with mock.patch.object(kc, "_fetch_user_groups", return_value=GROUPS) as f:
        first = kc.fetch_user_projects_from_keycloak("u1")
        second = kc.fetch_user_projects_from_keycloak("u1")

    assert first == second == [{"name": "demo", "role": "member"}]
    assert f.call_count == 1


def test_listing_refetches_after_the_ttl():
    with mock.patch.object(kc, "_fetch_user_groups", return_value=GROUPS) as f:
        with mock.patch.object(kc.time, "monotonic", return_value=1000.0):
            kc.fetch_user_projects_from_keycloak("u1")
        with mock.patch.object(
            kc.time, "monotonic", return_value=1000.0 + kc._LISTING_TTL + 1
        ):
            kc.fetch_user_projects_from_keycloak("u1")

    assert f.call_count == 2


@pytest.mark.parametrize("change", ["add", "remove"])
def test_membership_change_clears_the_listing(change):
    with mock.patch.object(kc, "_fetch_user_groups", return_value=GROUPS) as f:
        kc.fetch_user_projects_from_keycloak("u1")
        with (
            mock.patch.object(kc, "_get_admin_token", return_value="t"),
            mock.patch.object(
                kc, "_find_user_by_username", return_value={"id": "u1"}
            ),
            mock.patch.object(kc, "_find_group_by_name", return_value=None),
            mock.patch.object(kc.requests, "put"),
        ):
            if change == "remove":
                kc.remove_user_from_project("bob", "demo")
            else:
                kc._find_group_by_name.return_value = {"id": "g1"}
                kc.add_user_to_project("bob", "demo", "member")
        kc.fetch_user_projects_from_keycloak("u1")

    assert f.call_count == 2


def test_keycloak_failure_is_not_cached():
    with mock.patch.object(kc, "_fetch_user_groups", return_value=[]) as f:
        kc.fetch_user_projects_from_keycloak("u1")
        kc.fetch_user_projects_from_keycloak("u1")

    assert f.call_count == 2


def test_role_lookup_is_never_cached():
    with mock.patch.object(
        kc, "_fetch_user_groups_or_raise", return_value=GROUPS
    ) as f:
        kc.get_user_project_role("u1", "demo")
        kc.get_user_project_role("u1", "demo")

    assert f.call_count == 2
