"""
Release settings of an app: the ``release`` block of its values file(s).

Git stays the source of truth: the CMP reads and commits that block, exactly
what a developer would edit by hand. Defaults mirror the cnp-generic-app chart.
"""

from typing import Annotated, Any, Literal

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


class ReleaseSettingsUpdate(ReleaseSettings):
    # Set by the "enable migrations" answer to a db_without_migrations blocker.
    migrations_command: (
        list[Annotated[str, Field(min_length=1, max_length=200)]] | None
    ) = Field(default=None, min_length=1, max_length=10)


BlockerCode = Literal["no_route", "no_service", "db_without_migrations"]


class CanaryBlocker(_Camel):
    code: BlockerCode
    message: str


class ReleaseSettingsRead(_Camel):
    settings: ReleaseSettings
    files: list[str]
    can_edit: bool
    # Absent from Git: the values shown are the chart's defaults.
    declared: bool
    canary_blocker: CanaryBlocker | None = None
    # The image's migration entrypoint, when Git already names one.
    migrations_command: list[str] | None = None


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

    smoke_path = _lookup(block, "smoke", "path") or _lookup(
        values, "monitoring", "path"
    )

    raw = {
        "strategy": pick("strategy"),
        "preset": pick("preset"),
        "stickiness": pick("stickiness"),
        # The health path the platform already probes beats the chart's
        # /healthz, which a plain nginx answers with a 404.
        "smoke_path": smoke_path or pick("smoke", "path"),
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


def refusal(
    values: dict[str, Any], settings: ReleaseSettings
) -> CanaryBlocker | None:
    """Why the chart would refuse to render *settings* on *values*, if it would."""
    if settings.strategy != "canary":
        return None
    if not _lookup(values, "ingress", "enabled"):
        return CanaryBlocker(
            code="no_route",
            message=(
                "A canary shifts traffic through the app's HTTP route: expose "
                "the app first (ingress.enabled)."
            ),
        )
    # The chart enables the Service unless told otherwise.
    if _lookup(values, "service", "enabled") is False:
        return CanaryBlocker(
            code="no_service",
            message="A canary needs the app's Service (service.enabled).",
        )
    if _lookup(values, "db", "enabled") and not _lookup(
        values, "migrations", "enabled"
    ):
        return CanaryBlocker(
            code="db_without_migrations",
            message=(
                "Both versions share the database during a canary: enable "
                "migrations (migrations.enabled) so the schema is migrated "
                "once, before the new version starts."
            ),
        )
    return None


def enable_migrations(values: dict[str, Any], command: list[str]) -> None:
    """Turn on the platform's pre-sync migration Job for an app with a database."""
    if not _lookup(values, "db", "enabled"):
        return
    block = values.setdefault("migrations", {})
    block["enabled"] = True
    block["command"] = command
