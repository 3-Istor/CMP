"""
The platform chart's schema, applied by the CMP before it commits an app's
values file.

The app repository's CI and Argo CD render that file with infra-templates,
whose values.schema.json is closed: a value it refuses would be committed,
then fail the app's CI and leave Argo CD unable to sync. The CMP and the
repository write the same file under the same rules, read from the chart
Argo CD deploys with (main of infra-templates).

What the schema cannot see (the image of another app, the namespace quota)
is checked by the chart at render time, in CI and in Argo CD.
"""

import json
import time
from typing import Any

from jsonschema import Draft7Validator
from ruamel.yaml import YAML

from app.core.config import settings
from app.services.github_service import get_file_content

CACHE_SECONDS = 300

_cache: dict[str, Any] = {}


class ChartUnavailableError(Exception):
    """The chart's schema or defaults could not be read."""


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def fetch_chart(token: str) -> tuple[dict, dict]:
    """values.schema.json and values.yaml of the chart, cached a few minutes."""
    cached = _cache.get("chart")
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1], cached[2]
    repo = f"{settings.GITHUB_ORG}/infra-templates"
    try:
        schema_text, _ = await get_file_content(
            token, repo, "values.schema.json"
        )
        defaults_text, _ = await get_file_content(token, repo, "values.yaml")
        schema = json.loads(schema_text)
        defaults = _plain(YAML(typ="safe").load(defaults_text) or {})
    except Exception as exc:
        raise ChartUnavailableError(
            f"The platform chart could not be read to check the values: {exc}"
        ) from exc
    _cache["chart"] = (time.monotonic(), schema, defaults)
    return schema, defaults


def coalesce(defaults: Any, values: Any) -> Any:
    """Helm's merge of a values file over the chart's defaults: nested maps
    merge, anything else replaces, null removes the key."""
    if not (isinstance(defaults, dict) and isinstance(values, dict)):
        return values
    merged = dict(defaults)
    for key, value in values.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = coalesce(defaults.get(key), value)
    return merged


def schema_errors(schema: dict, defaults: dict, values: Any) -> list[str]:
    merged = coalesce(defaults, _plain(values))
    errors = Draft7Validator(schema).iter_errors(merged)
    return sorted(
        f"{'.'.join(str(p) for p in error.absolute_path) or '(root)'}: "
        f"{error.message}"
        for error in errors
    )


async def refusals(token: str, values: Any) -> list[str]:
    """What the chart's schema refuses in *values*, empty when it fits.

    Raises:
        ChartUnavailableError: The chart could not be read.
    """
    schema, defaults = await fetch_chart(token)
    return schema_errors(schema, defaults, values)
