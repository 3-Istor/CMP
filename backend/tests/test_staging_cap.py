import asyncio
from io import StringIO
from unittest import mock

import pytest
from ruamel.yaml import YAML

from app.services import project_registry
from app.services.project_registry import RegistryError

RECORD = """\
metadata:
  name: alpha
spec:
  targetCloud: onprem
{environments}  apps:
  - name: web
    type: static
  - name: api
    type: static
{api_staging}"""


def enable_staging(environments="", api_staging=""):
    """Turn staging on for web; return the spec written to the registry."""
    record = YAML().load(
        RECORD.format(environments=environments, api_staging=api_staging)
    )
    with (
        mock.patch.object(
            project_registry,
            "read_record",
            new=mock.AsyncMock(return_value=(record, "sha1")),
        ),
        mock.patch.object(
            project_registry,
            "list_records",
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(
            project_registry,
            "_installation_token",
            new=mock.AsyncMock(return_value="t"),
        ),
        mock.patch.object(
            project_registry, "put_file_content", new=mock.AsyncMock()
        ) as put_file,
    ):
        asyncio.run(
            project_registry.set_app_environments(
                "alpha", "web", ["prod", "staging"], "web-alpha-stg.3istor.com"
            )
        )
    return YAML(typ="safe").load(StringIO(put_file.call_args.args[3]))["spec"]


API_STAGING = "    environments: [prod, staging]\n"


def test_a_project_without_a_cap_runs_one_app_in_staging():
    spec = enable_staging()

    web = next(app for app in spec["apps"] if app["name"] == "web")
    assert web["environments"] == ["prod", "staging"]


def test_a_second_staging_app_without_a_cap_is_refused():
    with pytest.raises(RegistryError, match="A platform admin can raise it"):
        enable_staging(api_staging=API_STAGING)


def test_a_platform_admin_cap_allows_more_apps():
    environments = "  environments:\n  - name: staging\n    maxApps: 2\n"

    spec = enable_staging(environments, API_STAGING)

    assert (
        sum("staging" in app.get("environments", []) for app in spec["apps"])
        == 2
    )


def test_a_zero_cap_forbids_staging():
    environments = "  environments:\n  - name: staging\n    maxApps: 0\n"

    with pytest.raises(RegistryError, match="its limit is 0"):
        enable_staging(environments)


def test_a_zero_cap_is_written_to_the_registry():
    record = YAML().load(RECORD.format(environments="", api_staging=""))
    with (
        mock.patch.object(
            project_registry,
            "read_record",
            new=mock.AsyncMock(return_value=(record, "sha1")),
        ),
        mock.patch.object(
            project_registry,
            "_installation_token",
            new=mock.AsyncMock(return_value="t"),
        ),
        mock.patch.object(
            project_registry, "put_file_content", new=mock.AsyncMock()
        ) as put_file,
    ):
        asyncio.run(project_registry.set_project_staging("alpha", 0))

    spec = YAML(typ="safe").load(StringIO(put_file.call_args.args[3]))["spec"]
    staging = [env for env in spec["environments"] if env["name"] == "staging"]
    assert staging and staging[0]["maxApps"] == 0
