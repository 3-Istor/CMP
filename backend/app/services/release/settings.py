"""
Release settings of an app: the ``release`` block of its values file(s).

Git stays the source of truth: the CMP reads and commits that block, exactly
what a developer would edit by hand. Defaults mirror the cnp-generic-app chart.
"""

from typing import Any, Literal

from pydantic import Field, ValidationError
from pydantic.alias_generators import to_camel

from app.schemas.release import _Camel

Strategy = Literal["rolling", "canary"]
Preset = Literal["safe", "fast", "demo", "manual"]
Stickiness = Literal["none", "cookie"]

DEFAULTS: dict[str, Any] = {
    "strategy": "rolling",
    "preset": "safe",
    "stickiness": "cookie",
    "smoke": {"path": "/healthz"},
    "analysis": {"errorRateMax": 1, "minRequests": 50},
    "allowForce": True,
}


class ReleaseSettings(_Camel):
    strategy: Strategy
    preset: Preset
    stickiness: Stickiness
    smoke_path: str = Field(pattern=r"^/\S*$", max_length=200)
    error_rate_max: float = Field(gt=0, le=100)
    min_requests: int = Field(ge=1, le=100_000)
    allow_force: bool


class ReleaseSettingsRead(_Camel):
    settings: ReleaseSettings
    files: list[str]
    can_edit: bool
    # Absent from Git: the values shown are the chart's defaults.
    declared: bool


def _lookup(block: Any, *keys: str) -> Any:
    value: Any = block
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def read(values: dict[str, Any]) -> ReleaseSettings:
    """The settings of a parsed values file, chart defaults filling gaps."""
    block = values.get("release")

    def pick(*keys: str) -> Any:
        found = _lookup(block, *keys)
        return _lookup(DEFAULTS, *keys) if found is None else found

    raw = {
        "strategy": pick("strategy"),
        "preset": pick("preset"),
        "stickiness": pick("stickiness"),
        "smoke_path": pick("smoke", "path"),
        "error_rate_max": pick("analysis", "errorRateMax"),
        "min_requests": pick("analysis", "minRequests"),
        "allow_force": pick("allowForce"),
    }
    try:
        return ReleaseSettings(**raw)
    except ValidationError as exc:
        # A hand-edited value the chart refuses (bluegreen, a typo) breaks
        # the render: show the default, which a save writes back.
        bad = {str(error["loc"][0]) for error in exc.errors()}
        fallback = read({})
        return ReleaseSettings(
            **{
                key: (
                    getattr(fallback, key)
                    if key in bad or to_camel(key) in bad
                    else value
                )
                for key, value in raw.items()
            }
        )


def apply(values: dict[str, Any], settings: ReleaseSettings) -> None:
    """Write *settings* into *values* in place, keeping comments and keys."""
    block = values.setdefault("release", {})
    block["strategy"] = settings.strategy
    block["preset"] = settings.preset
    block["stickiness"] = settings.stickiness
    block.setdefault("smoke", {})["path"] = settings.smoke_path
    analysis = block.setdefault("analysis", {})
    analysis["errorRateMax"] = _number(settings.error_rate_max)
    analysis["minRequests"] = settings.min_requests
    block["allowForce"] = settings.allow_force


def _number(value: float) -> int | float:
    return int(value) if value == int(value) else value


def refusal(values: dict[str, Any], settings: ReleaseSettings) -> str | None:
    """Why the chart would refuse to render *settings* on *values*, if it would."""
    if settings.strategy != "canary":
        return None
    if not _lookup(values, "ingress", "enabled"):
        return (
            "A canary shifts traffic through the app's HTTP route: expose "
            "the app first (ingress.enabled)."
        )
    # The chart enables the Service unless told otherwise.
    if _lookup(values, "service", "enabled") is False:
        return "A canary needs the app's Service (service.enabled)."
    if _lookup(values, "db", "enabled") and not _lookup(
        values, "migrations", "enabled"
    ):
        return (
            "Both versions share the database during a canary: enable "
            "migrations (migrations.enabled) so the schema is migrated once, "
            "before the new version starts."
        )
    return None
