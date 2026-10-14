"""
Shapes of the Release tab API.

The view is camelCase on the wire, as the frontend reads it straight into its
types. The history rows keep the column names of ``release_events``.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

StepKind = Literal["analysis", "setWeight", "pause", "scale", "other"]
StepStatus = Literal[
    "done", "active", "running", "pending", "failed", "skipped"
]
Phase = Literal["Progressing", "Paused", "Healthy", "Degraded", "Aborted"]
Outcome = Literal[
    "running", "promoted", "aborted_auto", "aborted_manual", "superseded"
]


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ReleaseMetric(_Camel):
    name: str
    value: float | None
    threshold: float | None
    comparator: Literal["<=", ">="] | None = None
    unit: str | None
    phase: str | None


class ReleaseStep(_Camel):
    index: int
    kind: StepKind
    label: str
    status: StepStatus
    weight: int | None = None
    duration_seconds: int | None = None
    metrics: list[ReleaseMetric] = []
    message: str | None = None


class ReleaseRevision(_Camel):
    hash: str
    role: Literal["stable", "canary"]
    image: str | None
    ready: int
    desired: int


class ReleaseWeights(_Camel):
    stable: int
    canary: int


class ReleaseActions(_Camel):
    promote: bool = False
    promote_full: bool = False
    skip: bool = False
    abort: bool = False
    retry: bool = False


class ReleaseView(_Camel):
    enabled: bool
    strategy: str = "rolling"
    preset: str | None = None
    phase: Phase | None = None
    revision: int | None = None
    stable_hash: str | None = None
    canary_hash: str | None = None
    stable_image: str | None = None
    canary_image: str | None = None
    current_step_index: int | None = None
    weights: ReleaseWeights | None = None
    forced: bool = False
    allow_force: bool = True
    started_at: datetime | None = None
    eta_seconds: int | None = None
    steps: list[ReleaseStep] = []
    revisions: list[ReleaseRevision] = []
    actions: ReleaseActions = ReleaseActions()

    @property
    def in_progress(self) -> bool:
        return self.canary_hash is not None


def _blank_to_none(value: str | None) -> str | None:
    if value is not None:
        value = value.strip()
    return value or None


class PromoteRequest(BaseModel):
    mode: Literal["next", "full"] = "next"
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class SkipRequest(BaseModel):
    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a reason is required to skip a step")
        return value.strip()


class AbortRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class ReleaseActor(BaseModel):
    user: str
    action: str
    reason: str | None
    at: datetime
    step: int | None = None


class ReleaseEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    deployment_id: int
    revision: int
    image: str | None
    strategy: str
    started_at: datetime
    finished_at: datetime | None
    outcome: Outcome
    forced: bool
    reached_step: int | None
    steps: list[dict[str, Any]] | None
    actors: list[ReleaseActor] | None
