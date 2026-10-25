"""
Turn a Rollout snapshot into the Release tab's view, and the tab's buttons
into status patches.

Pure functions over the raw objects, so the mapping is tested against real
shapes without a cluster.
"""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.schemas.release import (
    ReleaseActions,
    ReleaseMetric,
    ReleaseRevision,
    ReleaseStep,
    ReleaseView,
    ReleaseWeights,
)
from app.services.release.source import HASH_LABEL, RolloutSnapshot

REVISION_ANNOTATION = "rollout.argoproj.io/revision"
PRESET_ANNOTATION = "cnp.3istor.com/release-preset"
ALLOW_FORCE_ANNOTATION = "cnp.3istor.com/release-allow-force"
SMOKE_TEMPLATE = "cnp-smoke"
ERROR_RATE_TEMPLATE = "cnp-error-rate"

_RUNNING_PHASES = {"", "Pending", "Running"}
_FAILED_PHASES = {"Failed", "Error"}
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
# result[0] <= {{args.max-error-rate}}, result < 0.01, ...
_THRESHOLD = re.compile(
    r"result(?:\[0\])?\s*(<=|<|>=|>)\s*"
    r"(?:\{\{\s*args\.([\w.-]+)\s*\}\}|(-?\d+(?:\.\d+)?(?:e-?\d+)?))"
)


@dataclass
class ReleaseHint:
    """What only the CMP's own history knows about the current attempt."""

    started_at: datetime | None = None
    forced: bool = False
    skipped: set[int] = field(default_factory=set)
    # A forced full promotion jumps every step from this one on.
    skipped_from: int | None = None
    reached_step: int | None = None


def parse_duration(raw: Any) -> int | None:
    """Seconds in a Go duration (``1m30s``) or a bare number of seconds."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    return int(sum(float(n) * _UNIT_SECONDS[u] for n, u in parts))


def parse_measurement(raw: Any) -> float | None:
    """A Prometheus measurement as Argo stores it: ``"[0.68]"`` or ``"0.68"``."""
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return None if number is None or math.isnan(number) else number


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _args(items: list[dict[str, Any]] | None) -> dict[str, str]:
    return {a["name"]: a["value"] for a in items or [] if "value" in a}


def metric_threshold(
    metric: dict[str, Any], args: dict[str, str]
) -> tuple[float | None, str | None]:
    """The bound a metric is judged against, and the side that passes."""
    for condition, passing in (
        (metric.get("successCondition"), True),
        (metric.get("failureCondition"), False),
    ):
        match = _THRESHOLD.search(condition or "")
        if not match:
            continue
        operator, arg, literal = match.groups()
        below = operator.startswith("<") == passing
        try:
            value = float(args[arg] if arg else literal)
        except (KeyError, ValueError):
            return None, None
        return value, "<=" if below else ">="
    return None, None


_ARG_REF = re.compile(r"^\{\{\s*args\.([\w-]+)\s*\}\}$")


def _resolve(value: Any, args: dict[str, str]) -> Any:
    """A template field, with an {{args.x}} reference replaced by its value."""
    match = _ARG_REF.match(str(value)) if isinstance(value, str) else None
    return args.get(match.group(1)) if match else value


def _template_seconds(
    template: dict[str, Any], args: dict[str, str] | None = None
) -> int | None:
    """How long a template's measurements last; None if they never stop."""
    args = args or {}
    longest = 0
    for metric in template.get("spec", {}).get("metrics", []):
        interval = parse_duration(_resolve(metric.get("interval"), args))
        count = _resolve(metric.get("count"), args)
        if count is None and interval:
            return None
        try:
            count = int(count or 1)
        except ValueError:
            return None
        delay = parse_duration(_resolve(metric.get("initialDelay"), args)) or 0
        # The first measurement comes after the delay, the next ones an
        # interval apart: count measurements span (count - 1) intervals.
        longest = max(longest, delay + (interval or 0) * max(count - 1, 0))
    return longest


def _templates(
    step: dict[str, Any], snapshot: RolloutSnapshot
) -> list[dict[str, Any] | None]:
    return [
        snapshot.templates.get(
            (bool(ref.get("clusterScope")), ref["templateName"])
        )
        for ref in step["analysis"].get("templates", [])
    ]


def _expected_analysis_seconds(
    step: dict[str, Any], snapshot: RolloutSnapshot
) -> int | None:
    """From the templates when known: a step's duration arg is only a hint
    the templates do not read (cnp-error-rate waits 60 s, then measures)."""
    args = _args(step["analysis"].get("args"))
    templates = _templates(step, snapshot)
    if templates and all(templates):
        longest = 0
        for template in templates:
            seconds = _template_seconds(template, args)
            if seconds is None:
                return None
            longest = max(longest, seconds)
        return longest
    return parse_duration(args.get("duration"))


def _human(seconds: int | None) -> str:
    if not seconds:
        return "0 s"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    if seconds > 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds} s"


def _describe(
    raw: dict[str, Any], kind: str, label: str, snapshot: RolloutSnapshot
) -> str | None:
    """What a step does, in one line a release watcher can act on."""
    if kind == "setWeight":
        return (
            f"{raw['setWeight']} % des nouveaux visiteurs vont sur la nouvelle "
            "version ; les autres restent sur la version stable."
        )
    if kind == "pause":
        seconds = parse_duration((raw["pause"] or {}).get("duration"))
        if seconds is None:
            return (
                "Attend votre validation : « Continuer » pour aller plus loin."
            )
        return f"Laisse tourner {_human(seconds)} avant l'étape suivante."
    if kind == "scale":
        if (raw.get("setCanaryScale") or {}).get("matchTrafficWeight"):
            return "Ajuste le nombre de pods de la nouvelle version à sa part de trafic."
        return (
            "Démarre la nouvelle version, sans encore lui envoyer de trafic."
        )
    if kind != "analysis":
        return None
    args = _args(raw["analysis"].get("args"))
    names = [
        t.get("templateName") for t in raw["analysis"].get("templates", [])
    ]
    if "cnp-smoke" in names:
        path = _url_path(args.get("smoke-url"))
        return (
            f"Un appel HTTP sur {path} de la nouvelle version, avant tout trafic : "
            "il doit répondre 2xx."
        )
    parts = []
    if args.get("min-requests"):
        parts.append(
            f"au moins {args['min-requests']} requêtes sur la nouvelle version"
        )
    if args.get("max-error-rate"):
        try:
            parts.append(
                f"au plus {float(args['max-error-rate']) * 100:g} % d'erreurs"
            )
        except ValueError:
            pass
    timing = ""
    for template in _templates(raw, snapshot):
        for metric in (template or {}).get("spec", {}).get("metrics", []):
            delay = parse_duration(_resolve(metric.get("initialDelay"), args))
            interval = parse_duration(_resolve(metric.get("interval"), args))
            count = _resolve(metric.get("count"), args)
            if interval:
                timing = (
                    f" Première mesure après {_human(delay)}, le temps que les "
                    f"métriques arrivent, puis toutes les {_human(interval)}"
                    + (f" ({count} mesures)." if count else ".")
                )
            break
    if not parts:
        return label + "." + timing
    return "Réussit avec " + " et ".join(parts) + "." + timing


def _url_path(url: str | None) -> str:
    return (urlsplit(url).path or "/") if url else "/"


def _kind(step: dict[str, Any]) -> str:
    for key, kind in (
        ("setWeight", "setWeight"),
        ("pause", "pause"),
        ("analysis", "analysis"),
        ("setCanaryScale", "scale"),
    ):
        if key in step:
            return kind
    return "other"


def _label(step: dict[str, Any], kind: str) -> str:
    if kind == "setWeight":
        return f"Trafic {step['setWeight']} %"
    if kind == "pause":
        duration = (step["pause"] or {}).get("duration")
        return f"Pause {duration}" if duration else "Validation manuelle"
    if kind == "scale":
        scale = step["setCanaryScale"] or {}
        if scale.get("matchTrafficWeight"):
            return "Alignement des pods"
        return "Préparation du canary"
    if kind == "analysis":
        names = {
            t["templateName"] for t in step["analysis"].get("templates", [])
        }
        if SMOKE_TEMPLATE in names:
            return "Smoke test"
        if ERROR_RATE_TEMPLATE in names:
            return "Analyse du taux d'erreur"
        return "Analyse"
    return next(iter(step), "Étape")


def _step_runs(
    snapshot: RolloutSnapshot, revision: str | None
) -> dict[int, dict[str, Any]]:
    """The latest step AnalysisRun of the current revision, per step index."""
    runs: dict[int, dict[str, Any]] = {}
    for run in snapshot.analysis_runs:
        meta = run["metadata"]
        labels = meta.get("labels", {})
        if labels.get("rollout-type") != "Step" or "step-index" not in labels:
            continue
        run_revision = meta.get("annotations", {}).get(REVISION_ANNOTATION)
        if revision and run_revision and run_revision != revision:
            continue
        runs[int(labels["step-index"])] = run
    return runs


def _metrics(run: dict[str, Any]) -> list[ReleaseMetric]:
    spec = run.get("spec", {})
    args = _args(spec.get("args"))
    specs = {m["name"]: m for m in spec.get("metrics", [])}
    results = {
        r["name"]: r for r in run.get("status", {}).get("metricResults", [])
    }
    metrics = []
    for name, metric in specs.items():
        result = results.get(name, {})
        measured = [
            m for m in result.get("measurements", []) if m.get("value")
        ]
        threshold, comparator = metric_threshold(metric, args)
        metrics.append(
            ReleaseMetric(
                name=name,
                value=(
                    parse_measurement(measured[-1]["value"])
                    if measured
                    else None
                ),
                threshold=threshold,
                comparator=comparator,
                unit="ratio" if "error-rate" in name else None,
                phase=result.get("phase") or "Pending",
            )
        )
    return metrics


def _run_message(run: dict[str, Any]) -> str | None:
    status = run.get("status", {})
    if status.get("message"):
        return status["message"]
    for result in status.get("metricResults", []):
        for measurement in reversed(result.get("measurements", [])):
            if measurement.get("message"):
                return measurement["message"]
    return None


def _seconds_between(start: str | None, end: datetime) -> int | None:
    started = _parse_time(start)
    return int((end - started).total_seconds()) if started else None


def _run_elapsed(run: dict[str, Any], now: datetime) -> int | None:
    status = run.get("status", {})
    end = _parse_time(status.get("completedAt")) or now
    return _seconds_between(
        status.get("startedAt") or run["metadata"].get("creationTimestamp"),
        end,
    )


def _is_inconclusive(rollout: dict[str, Any]) -> bool:
    status = rollout.get("status", {})
    analysis = (status.get("canary") or {}).get(
        "currentStepAnalysisRunStatus"
    ) or {}
    return analysis.get("status") == "Inconclusive" or any(
        c.get("reason") == "InconclusiveAnalysisRun"
        for c in status.get("pauseConditions") or []
    )


def _pause_started(rollout: dict[str, Any]) -> str | None:
    for condition in rollout.get("status", {}).get("pauseConditions") or []:
        if condition.get("reason") == "CanaryPauseStep":
            return condition.get("startTime")
    return None


def _image(rs: dict[str, Any] | None) -> str | None:
    if not rs:
        return None
    containers = (
        rs.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    return containers[0].get("image") if containers else None


def _revision_entry(
    rs: dict[str, Any] | None, pod_hash: str, role: str
) -> ReleaseRevision:
    return ReleaseRevision(
        hash=pod_hash,
        role=role,
        image=_image(rs),
        ready=(rs or {}).get("status", {}).get("readyReplicas") or 0,
        desired=(rs or {}).get("spec", {}).get("replicas") or 0,
    )


def _strategy(rollout: dict[str, Any]) -> str:
    strategy = rollout.get("spec", {}).get("strategy", {})
    if "canary" in strategy:
        return "canary"
    if "blueGreen" in strategy:
        return "bluegreen"
    return "rolling"


def disabled_view() -> ReleaseView:
    return ReleaseView(enabled=False)


def build_view(
    snapshot: RolloutSnapshot | None,
    now: datetime | None = None,
    hint: ReleaseHint | None = None,
) -> ReleaseView:
    if snapshot is None:
        return disabled_view()
    now = now or datetime.now(timezone.utc)
    hint = hint or ReleaseHint()
    rollout = snapshot.rollout
    meta = rollout["metadata"]
    annotations = meta.get("annotations", {})
    status = rollout.get("status", {})
    raw_steps = (
        rollout.get("spec", {})
        .get("strategy", {})
        .get("canary", {})
        .get("steps", [])
    )

    revision_text = annotations.get(REVISION_ANNOTATION)
    stable_hash = status.get("stableRS")
    current_hash = status.get("currentPodHash")
    canary_hash = (
        current_hash if current_hash and current_hash != stable_hash else None
    )
    in_progress = canary_hash is not None
    aborted = bool(status.get("abort"))
    allow_force = annotations.get(ALLOW_FORCE_ANNOTATION) != "false"
    current = int(status.get("currentStepIndex") or 0)
    runs = _step_runs(snapshot, revision_text)
    rs_by_hash = {
        rs["metadata"].get("labels", {}).get(HASH_LABEL): rs
        for rs in snapshot.replica_sets
    }

    if not in_progress:
        current = len(raw_steps)
    elif aborted:
        failed = [
            i
            for i, run in runs.items()
            if run.get("status", {}).get("phase") in _FAILED_PHASES
        ]
        # The controller resets currentStepIndex to 0 on abort.
        current = max(failed) if failed else (hint.reached_step or 0)

    steps: list[ReleaseStep] = []
    eta: int | None = 0
    for index, raw in enumerate(raw_steps):
        kind = _kind(raw)
        run = runs.get(index) if kind == "analysis" else None
        label = _label(raw, kind)
        step = ReleaseStep(
            index=index,
            kind=kind,
            label=label,
            description=_describe(raw, kind, label, snapshot),
            status="pending",
            weight=raw.get("setWeight"),
            metrics=_metrics(run) if run else [],
        )
        expected: int | None = 0
        if kind == "analysis":
            expected = _expected_analysis_seconds(raw, snapshot)
            step.expected_seconds = expected
            step.duration_seconds = _run_elapsed(run, now) if run else expected
        elif kind == "pause":
            expected = parse_duration((raw["pause"] or {}).get("duration"))
            step.duration_seconds = expected

        if index < current:
            jumped = index in hint.skipped or (
                hint.skipped_from is not None and index >= hint.skipped_from
            )
            step.status = "skipped" if jumped else "done"
        elif index == current and aborted:
            step.status = "failed"
            step.message = (run and _run_message(run)) or "Release annulée"
        elif index == current:
            step.status, remaining = _current_status(
                kind, raw, run, rollout, expected, now
            )
            if step.status in ("failed", "active") and run:
                step.message = _run_message(run)
            eta = None if eta is None or remaining is None else eta + remaining
        elif index > current:
            eta = None if eta is None or expected is None else eta + expected
        steps.append(step)

    weights = (status.get("canary") or {}).get("weights")
    if weights:
        split = ReleaseWeights(
            stable=weights.get("stable", {}).get("weight", 0),
            canary=weights.get("canary", {}).get("weight", 0),
        )
    elif not in_progress:
        split = ReleaseWeights(stable=100, canary=0)
    else:
        reached = [
            s.get("setWeight", 0)
            for s in raw_steps[:current]
            if "setWeight" in s
        ]
        canary_weight = reached[-1] if reached else 0
        split = ReleaseWeights(
            stable=100 - canary_weight, canary=canary_weight
        )

    revisions = []
    if stable_hash:
        revisions.append(
            _revision_entry(rs_by_hash.get(stable_hash), stable_hash, "stable")
        )
    if canary_hash:
        revisions.append(
            _revision_entry(rs_by_hash.get(canary_hash), canary_hash, "canary")
        )

    actions = ReleaseActions()
    if in_progress and aborted:
        actions.retry = True
    elif in_progress:
        active = steps[current] if current < len(steps) else None
        actions.abort = True
        actions.promote_full = allow_force
        actions.promote = active is not None and active.status == "active"
        actions.skip = (
            allow_force
            and active is not None
            and active.kind == "analysis"
            and active.status in ("running", "active")
        )

    started_at = hint.started_at
    if started_at is None and canary_hash in rs_by_hash:
        started_at = _parse_time(
            rs_by_hash[canary_hash]["metadata"].get("creationTimestamp")
        )

    return ReleaseView(
        enabled=True,
        strategy=_strategy(rollout),
        preset=annotations.get(PRESET_ANNOTATION),
        phase="Aborted" if aborted else status.get("phase") or "Progressing",
        revision=int(revision_text) if revision_text else None,
        stable_hash=stable_hash,
        canary_hash=canary_hash,
        stable_image=_image(rs_by_hash.get(stable_hash)),
        canary_image=_image(rs_by_hash.get(canary_hash)),
        current_step_index=current,
        weights=split,
        forced=hint.forced,
        allow_force=allow_force,
        started_at=started_at,
        eta_seconds=eta if in_progress and not aborted else None,
        steps=steps,
        revisions=revisions,
        actions=actions,
    )


def _current_status(
    kind: str,
    raw: dict[str, Any],
    run: dict[str, Any] | None,
    rollout: dict[str, Any],
    expected: int | None,
    now: datetime,
) -> tuple[str, int | None]:
    """Status of the step the Rollout is on, and the seconds it has left."""
    if kind == "analysis":
        if _is_inconclusive(rollout):
            return "active", None
        phase = (run or {}).get("status", {}).get("phase", "")
        if phase in _FAILED_PHASES:
            return "failed", None
        if phase == "Successful":
            return "done", 0
        elapsed = _run_elapsed(run, now) if run else 0
        if expected is None or elapsed is None:
            return "running", None
        return "running", max(0, expected - elapsed)
    if kind == "pause":
        if expected is None:
            return "active", None
        elapsed = _seconds_between(_pause_started(rollout), now) or 0
        return "running", max(0, expected - elapsed)
    return "running", 0


# ── Status patches ──────────────────────────────────────────────────────────
#
# Mirrors ``kubectl argo rollouts promote`` (pkg/kubectl-argo-rollouts/cmd/
# promote): an inconclusive analysis also needs controllerPause cleared and the
# step index moved past it, or the Rollout stays stuck on it.


def _next_step_index(rollout: dict[str, Any]) -> int:
    status = rollout.get("status", {})
    steps = rollout["spec"]["strategy"]["canary"].get("steps", [])
    return min(int(status.get("currentStepIndex") or 0) + 1, len(steps))


def _next_step_patch(rollout: dict[str, Any]) -> dict[str, Any]:
    # What `kubectl argo rollouts promote` sends. Clearing pauseConditions
    # alone leaves controllerPause set on an indefinite pause step, and the
    # Rollout then waits on that step forever ("waiting for all steps").
    return {
        "status": {
            "pauseConditions": None,
            "controllerPause": False,
            "currentStepIndex": _next_step_index(rollout),
        }
    }


def promote_patch(rollout: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode == "full":
        return {"status": {"promoteFull": True}}
    return _next_step_patch(rollout)


def skip_patch(rollout: dict[str, Any]) -> dict[str, Any]:
    if _is_inconclusive(rollout):
        return _next_step_patch(rollout)
    return {"status": {"currentStepIndex": _next_step_index(rollout)}}


ABORT_PATCH: dict[str, Any] = {"status": {"abort": True}}
RETRY_PATCH: dict[str, Any] = {"status": {"abort": False}}
