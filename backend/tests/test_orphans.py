"""
CNP-306: deleting a template app leaves nothing behind and can be run again;
the orphan check lists what is left and never deletes.
"""

import asyncio
import json
from unittest import mock

import pytest

from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project
from app.services import aws_tfstate, kube_client, orphans, project_registry
from app.services import terraform_orchestrator as orchestrator
from app.services import vault_client
from app.services.environments import activation
from app.services.orphans import Alive
from tests.test_app_inputs import MANIFEST_VARIABLES
from tests.test_app_security_data import session_factory  # noqa: F401

SHOP = Alive({"shop": "onprem", "shop-eu": "aws"}, {"shop": {"api"}})


def _names(found):
    return sorted(o.name for o in found)


# --- what each check reports --------------------------------------------


def test_registry_entries_without_a_live_app_or_project_are_orphans():
    records = [
        {"metadata": {"name": "shop"}, "spec": {"apps": [{"name": "api"}]}},
        {"metadata": {"name": "shop"}, "spec": {"apps": [{"name": "old"}]}},
        {"metadata": {"name": "gone"}, "spec": {"apps": [{"name": "x"}]}},
    ]

    found = orphans.check_registry(SHOP, records)

    assert _names(found) == ["gone", "shop/old"]


def test_namespaces_of_live_apps_staging_and_system_are_not_orphans():
    namespaces = [
        _namespace("shop-api", "shop"),
        _namespace("shop-api-stg", "shop"),
        _namespace("shop-system", "shop"),
        _namespace("shop-old", "shop"),
        _namespace("gone-api", "gone"),
        _namespace("kube-system", None),
    ]

    found = orphans.check_namespaces(SHOP, "onprem", namespaces)

    assert _names(found) == ["gone-api", "shop-old"]


def test_vault_secrets_outside_live_apps_are_orphans():
    paths = ["api", "api/registry", "system/discord", "old", "old/registry"]

    found = orphans.check_kv(SHOP, "shop", paths)

    assert _names(found) == ["project-shop/old"]


@pytest.mark.parametrize(
    "name,orphan",
    [
        ("shop-api-role", False),
        ("shop-api-staging-role", False),
        ("project-shop-system-role", False),
        ("shop-old-role", True),
        ("shop-old-staging-role", True),
        ("gone-api-role", True),
        ("project-gone-system-role", True),
        ("vault-secrets-operator-role", False),
    ],
)
def test_vault_roles_are_matched_to_their_app(name, orphan):
    found = orphans.check_vault_names(
        SHOP, "vault_role", "role", [name], {"shop", "shop-eu", "gone"}
    )

    assert bool(found) is orphan


@pytest.mark.parametrize(
    "name,orphan",
    [
        ("project-shop-app-api-policy", False),
        ("project-shop-dev-policy", False),
        ("project-shop-api-staging-policy", False),
        ("project-shop-app-old-policy", True),
        ("project-shop-unknown-policy", False),
        ("project-gone-read-policy", True),
        ("cmp-backend", False),
    ],
)
def test_vault_policies_are_matched_to_their_app(name, orphan):
    found = orphans.check_vault_names(
        SHOP, "vault_policy", "policy", [name], {"shop", "gone"}
    )

    assert bool(found) is orphan


def test_a_name_belongs_to_the_longest_matching_project():
    found = orphans.check_vault_names(
        SHOP, "vault_role", "role", ["shop-eu-web-role"], {"shop", "shop-eu"}
    )

    assert [(o.project, o.app) for o in found] == [("shop-eu", "web")]


def test_states_of_deleted_apps_are_orphans_once_per_app():
    keys = [
        "cnp/projects/shop/api/terraform.tfstate",
        "cnp/projects/shop/_staging-realm/terraform.tfstate",
        "cnp/projects/shop/old/terraform.tfstate",
        "cnp/projects/shop/old/staging/terraform.tfstate",
    ]

    found = orphans.check_states(SHOP, keys)

    assert _names(found) == ["cnp/projects/shop/old/"]


# --- the whole check --------------------------------------------------------


@pytest.fixture
def platform(monkeypatch):
    """Every source the check reads, as it is once 'old' was deleted, and
    every way to delete something wired to fail the test."""
    sources = {
        "records": [
            {"metadata": {"name": "shop"}, "spec": {"apps": [{"name": "api"}]}}
        ],
        "namespaces": [_namespace("shop-api", "shop")],
        "kv": ["api", "system/discord"],
        "roles": ["shop-api-role", "project-shop-system-role"],
        "policies": ["project-shop-app-api-policy", "project-shop-dev-policy"],
        "states": ["cnp/projects/shop/api/terraform.tfstate"],
    }
    monkeypatch.setattr(
        project_registry,
        "list_records",
        mock.AsyncMock(side_effect=lambda: sources["records"]),
    )
    monkeypatch.setattr(
        kube_client, "kube_list", lambda *a, **k: sources["namespaces"]
    )
    monkeypatch.setattr(
        vault_client,
        "list_paths",
        mock.AsyncMock(side_effect=lambda mount: sources["kv"]),
    )

    async def list_names(path):
        return sources["policies" if path.startswith("sys/") else "roles"]

    monkeypatch.setattr(vault_client, "list_names", list_names)
    monkeypatch.setattr(
        aws_tfstate, "state_keys", lambda prefix: sources["states"]
    )
    monkeypatch.setattr(settings, "TF_BACKEND_S3_BUCKET", "states")
    for module, name in (
        (kube_client, "kube_delete"),
        (kube_client, "kube_delete_collection"),
        (vault_client, "delete_app_role"),
        (vault_client, "write_kv"),
        (aws_tfstate, "delete_states"),
        (project_registry, "unregister_app"),
        (project_registry, "remove_record"),
    ):
        monkeypatch.setattr(
            module, name, mock.Mock(side_effect=AssertionError("deleted"))
        )
    return sources


def test_after_an_app_is_deleted_the_check_finds_nothing_of_it(platform):
    alive = Alive({"shop": "onprem"}, {"shop": {"api"}})

    result = asyncio.run(orphans.find(alive))

    assert result == {"orphans": [], "errors": []}


def test_the_check_lists_leftovers_and_deletes_nothing(platform):
    platform["namespaces"].append(_namespace("shop-old", "shop"))
    platform["roles"].append("shop-old-role")
    platform["states"].append("cnp/projects/shop/old/terraform.tfstate")
    alive = Alive({"shop": "onprem"}, {"shop": {"api"}})

    result = asyncio.run(orphans.find(alive))

    assert sorted(o["kind"] for o in result["orphans"]) == [
        "namespace",
        "tfstate",
        "vault_role",
    ]


def test_a_source_that_cannot_be_read_is_reported_not_passed(
    platform, monkeypatch
):
    monkeypatch.setattr(
        vault_client,
        "list_names",
        mock.AsyncMock(side_effect=vault_client.VaultError("403")),
    )
    alive = Alive({"shop": "onprem"}, {"shop": {"api"}})

    result = asyncio.run(orphans.find(alive))

    assert result["errors"] == [
        "vault roles (kubernetes): 403",
        "vault policies: 403",
    ]


# --- template app deletion ---------------------------------------------------


def _deleting(factory, monkeypatch, tmp_path, unregister):
    monkeypatch.chdir(tmp_path)
    with factory() as db:
        db.add(Project(project_name="shop", owner_username="alice"))
        db.add(
            Deployment(
                id=7,
                name="old",
                template_id="k3s-gitops-app",
                provider_type=ProviderType.KUBERNETES,
                project_id="shop",
                status=DeploymentStatus.FAILED,
                app_config=json.dumps({"template_repo_name": "t"}),
            )
        )
        db.commit()
    repo = mock.Mock()
    repo.get_template_by_id.return_value = {
        "variables": MANIFEST_VARIABLES,
        "_template_path": str(tmp_path),
    }
    states = mock.Mock(return_value=[])
    patches = [
        mock.patch.object(orchestrator, "SessionLocal", factory),
        mock.patch.object(orchestrator, "get_repository", lambda: repo),
        mock.patch.object(orchestrator, "create_executor"),
        mock.patch.object(
            orchestrator,
            "get_org_installation_token",
            mock.AsyncMock(return_value="ghs"),
        ),
        mock.patch.object(orchestrator, "_notify"),
        mock.patch.object(orchestrator.obs_reconcile, "request_sync"),
        mock.patch.object(activation, "destroy_with_app"),
        mock.patch.object(project_registry, "unregister_app", unregister),
        mock.patch.object(aws_tfstate, "delete_states", states),
        mock.patch.object(settings, "TF_BACKEND_S3_BUCKET", "states"),
    ]
    for patch in patches:
        patch.start()
    orchestrator.run_deletion(7)
    for patch in patches:
        patch.stop()
    with factory() as db:
        return db.get(Deployment, 7), states


def test_a_registry_failure_leaves_the_deletion_to_run_again(
    session_factory, monkeypatch, tmp_path  # noqa: F811
):
    unregister = mock.AsyncMock(
        side_effect=project_registry.RegistryError("GitHub down")
    )

    row, _ = _deleting(session_factory, monkeypatch, tmp_path, unregister)

    assert row.status == DeploymentStatus.FAILED


def test_a_deletion_run_again_after_a_failure_finishes(
    session_factory, monkeypatch, tmp_path  # noqa: F811
):
    row, states = _deleting(
        session_factory, monkeypatch, tmp_path, mock.AsyncMock()
    )

    assert (row.status, states.call_args.args) == (
        DeploymentStatus.DELETED,
        ("cnp/projects/shop/old/",),
    )


def _namespace(name, project):
    labels = {project_registry.PROJECT_LABEL: project} if project else {}
    return {"metadata": {"name": name, "labels": labels}}
