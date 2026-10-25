"""
Promotion of a release from staging to prod, as an ordered list of steps.

Lot 1 sequence: the platform's smoke check, the suites the app chose, a soak
time counted while staging is awake, an approval when required, then the
commit of the digests into deploy/env/prod/. The digests are fixed when the
promotion starts, so a release candidate that lands in staging meanwhile
neither changes nor blocks what this promotion ships.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.deployment import Deployment
from app.models.environment import Promotion, PromotionPolicy, VerificationRun
from app.services.environments import gitops, layout, observe, runs, suites
from app.services.github_service import GitHubAppError, create_release

logger = logging.getLogger(__name__)

ACTIVE = ("running", "waiting_approval")
DONE_STEP = ("passed", "skipped", "accepted")


class PromotionRefused(Exception):
    """The promotion cannot start; the message says why, for people."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def policy_of(db: Session, deployment_id: int) -> PromotionPolicy:
    policy = db.get(PromotionPolicy, deployment_id)
    if policy is None:
        policy = PromotionPolicy(
            deployment_id=deployment_id,
            suites=[],
            soak_minutes=0,
            require_approval=False,
            auto_promote=False,
        )
    return policy


def active_promotion(db: Session, deployment_id: int) -> Promotion | None:
    return (
        db.query(Promotion)
        .filter_by(deployment_id=deployment_id)
        .filter(Promotion.status.in_(ACTIVE))
        .first()
    )


def _step(name: str, kind: str, **extra) -> dict:
    return {
        "name": name,
        "kind": kind,
        "status": "pending",
        "detail": None,
        **extra,
    }


def build_steps(
    policy: PromotionPolicy,
    needs_approval: bool,
    emergency: bool,
    rollback: bool,
) -> list[dict]:
    steps: list[dict] = []
    if not rollback:
        steps.append(_step(suites.SMOKE, "run", suite=dict(suites.SMOKE_SPEC)))
        for spec in policy.suites or []:
            steps.append(_step(spec["name"], "run", suite=spec))
        if policy.soak_minutes:
            steps.append(
                _step(
                    "soak", "soak", minutes=policy.soak_minutes, awakeSeconds=0
                )
            )
    if needs_approval:
        steps.append(_step("approval", "approve"))
    steps.append(_step("prod", "promote"))
    if emergency:
        for step in steps:
            if step["kind"] in ("soak", "approve"):
                step["status"] = "skipped"
                step["detail"] = "Emergency promotion"
    return steps


async def start(
    db: Session,
    deployment: Deployment,
    actor: str,
    actor_is_admin: bool,
    emergency: bool = False,
    reason: str | None = None,
    rollback_of: Promotion | None = None,
) -> Promotion:
    """
    Raises:
        PromotionRefused: Another promotion runs, staging is not up, or it
            runs what prod already runs.
    """
    if active_promotion(db, deployment.id):
        raise PromotionRefused(
            "A promotion is already in progress for this app."
        )
    if emergency and not reason:
        raise PromotionRefused("An emergency promotion needs a reason.")

    policy = policy_of(db, deployment.id)
    if rollback_of is not None:
        digests, version = rollback_of.digests, rollback_of.version
    else:
        staging = await asyncio.to_thread(observe.of, deployment, "staging")
        if not staging.converged:
            raise PromotionRefused(
                "Staging is not up with a single version (asleep, rolling out or failing): "
                "nothing can be promoted from it now."
            )
        digests, version = staging.digests, staging.version
    prod = await asyncio.to_thread(observe.of, deployment, "prod")
    if prod.digests == digests:
        raise PromotionRefused("Prod already runs this version.")

    # Without the admin role, starting a promotion is asking for one.
    needs_approval = policy.require_approval or not actor_is_admin
    promotion = Promotion(
        deployment_id=deployment.id,
        source_env="prod" if rollback_of else "staging",
        target_env="prod",
        version=version,
        digests=digests,
        status="running",
        steps=build_steps(
            policy, needs_approval, emergency, rollback_of is not None
        ),
        emergency=emergency,
        reason=reason,
        rollback_of=rollback_of.id if rollback_of else None,
        approvals=[],
        actor=actor,
        created_at=now(),
    )
    db.add(promotion)
    db.commit()
    db.refresh(promotion)
    return promotion


def _save(db: Session, promotion: Promotion, steps: list[dict]) -> None:
    # A new list, so SQLAlchemy sees the JSON column change.
    promotion.steps = [dict(step) for step in steps]
    db.commit()


def _fail(
    db: Session, promotion: Promotion, steps: list[dict], detail: str
) -> None:
    promotion.status = "failed"
    promotion.detail = detail
    promotion.finished_at = now()
    _save(db, promotion, steps)


async def advance(
    db: Session, promotion: Promotion, tick_seconds: float = 0
) -> Promotion:
    """
    Move a promotion as far as it can go now. Safe to call often: each step
    is idempotent, and a step that has to wait leaves the rest pending.
    """
    if promotion.status not in ACTIVE:
        return promotion
    deployment = db.get(Deployment, promotion.deployment_id)
    steps = [dict(step) for step in promotion.steps]
    for step in steps:
        if step["status"] in DONE_STEP:
            continue
        if step["status"] == "failed":
            _fail(
                db,
                promotion,
                steps,
                step.get("detail") or f"{step['name']} failed",
            )
            return promotion
        await _advance_step(db, deployment, promotion, step, tick_seconds)
        if step["status"] == "failed":
            _fail(
                db,
                promotion,
                steps,
                step.get("detail") or f"{step['name']} failed",
            )
            return promotion
        if step["status"] not in DONE_STEP:
            promotion.status = (
                "waiting_approval"
                if step["kind"] == "approve" or step["status"] == "waiting"
                else "running"
            )
            _save(db, promotion, steps)
            return promotion
    promotion.status = "done"
    promotion.finished_at = now()
    _save(db, promotion, steps)
    return promotion


async def _advance_step(db, deployment, promotion, step, tick_seconds) -> bool:
    kind = step["kind"]
    if kind == "run":
        return await _advance_run(db, deployment, promotion, step)
    if kind == "soak":
        return await _advance_soak(deployment, promotion, step, tick_seconds)
    if kind == "approve":
        if promotion.approvals:
            step["status"] = "passed"
            step["detail"] = f"Approved by {promotion.approvals[-1]['by']}"
            return True
        step["status"] = "waiting"
        return False
    if kind == "promote":
        await _commit(deployment, promotion, step)
        return True
    step["status"] = "failed"
    step["detail"] = f"Unknown step kind {kind}"
    return False


async def _advance_run(db, deployment, promotion, step) -> bool:
    spec = step["suite"]
    run = db.get(VerificationRun, step["runId"]) if step.get("runId") else None
    if run is None:
        run = runs.fresh_pass(
            db, deployment.id, "staging", spec["name"], promotion.digests
        )
        if run is None:
            try:
                run = await runs.start(
                    db,
                    deployment,
                    "staging",
                    spec,
                    promotion.actor,
                    promotion_id=promotion.id,
                    expected_digests=promotion.digests,
                )
            except runs.DigestsMoved as exc:
                step["status"] = "failed"
                step["detail"] = (
                    f"{exc} Its evidence is gone: start a promotion of the new release."
                )
                return False
            except runs.RunRefused as exc:
                step["detail"] = str(exc)
                return False
        step["runId"] = run.id
    db.refresh(run)
    step["detail"] = run.summary
    if run.status == "running":
        step["status"] = "running"
        return False
    if run.status == "passed":
        step["status"] = "passed"
        return True
    if run.status == "inconclusive":
        rule = spec.get("onInconclusive", "ask")
        if rule == "pass":
            step["status"] = "passed"
            step["detail"] = f"Inconclusive, passed by policy: {run.summary}"
            return True
        if rule == "ask":
            step["status"] = "waiting"
            step["detail"] = (
                f"Inconclusive, a project admin must accept it: {run.summary}"
            )
            return False
    step["status"] = "failed"
    step["detail"] = f"{spec['name']}: {run.summary or run.status}"
    return False


async def _advance_soak(deployment, promotion, step, tick_seconds) -> bool:
    """Count time only while staging is up and runs the promoted digests."""
    state = await asyncio.to_thread(observe.of, deployment, "staging")
    if state.converged and state.digests != promotion.digests:
        step["status"] = "failed"
        step["detail"] = (
            "Staging moved on to another release before the soak time ended."
        )
        return False
    if state.converged:
        step["awakeSeconds"] = step.get("awakeSeconds", 0) + tick_seconds
    needed = step["minutes"] * 60
    done = step["awakeSeconds"] >= needed
    step["status"] = "passed" if done else "running"
    step["detail"] = (
        f"{int(step['awakeSeconds'] // 60)} / {step['minutes']} min awake"
    )
    return done


def _trailers(promotion: Promotion) -> str:
    lines = [
        f"CNP-Promotion: {promotion.id}",
        f"CNP-Promoted-By: {promotion.actor}",
    ]
    lines += [f"CNP-Approved-By: {a['by']}" for a in promotion.approvals]
    if promotion.emergency:
        lines.append(f"CNP-Emergency: {promotion.reason}")
    if promotion.rollback_of:
        lines.append(f"CNP-Rollback-Of: {promotion.rollback_of}")
    return "\n".join(lines)


async def _commit(
    deployment: Deployment, promotion: Promotion, step: dict
) -> None:
    token = await gitops.token_for(deployment)
    repo = gitops.repo_of(deployment)
    version = promotion.version or ""

    async def build(head: str) -> dict[str, str]:
        files = {}
        for component, digest in promotion.digests.items():
            path = layout.env_values_file("prod", component)
            values = await gitops.read_values(token, repo, path, head) or {}
            repository, sha = digest.split("@", 1)
            image = values.setdefault("image", {})
            image["repository"] = repository
            image["tag"] = f"{version}@{sha}" if version else sha
            files[path] = gitops.dump(values)
        return files

    message = f"chore(release): promote {deployment.name} {version} to prod\n\n{_trailers(promotion)}"
    try:
        sha = await gitops.commit(token, repo, build, message)
    except gitops.GitOpsError as exc:
        step["status"] = "failed"
        step["detail"] = f"Commit refused: {exc}"
        return
    promotion.commit_sha = sha
    step["status"] = "passed"
    step["detail"] = (
        f"Committed {sha[:8]}; Argo CD rolls it out with the prod release strategy"
    )
    if not promotion.rollback_of and version:
        core = layout.version_of(version).split("-", 1)[0]
        try:
            await create_release(
                token,
                repo,
                # The full version: several candidates of one X.Y.Z can reach
                # prod, and a tag already taken refuses the release.
                f"release/{layout.version_of(version)}",
                sha,
                f"{core} ({version})",
                f"Promoted from staging by {promotion.actor}.\n\n{_trailers(promotion)}",
            )
        except GitHubAppError as exc:
            # The release page is for people; prod already moved.
            logger.warning(
                "promotion %s: GitHub release not published: %s",
                promotion.id,
                exc,
            )


def approve(
    promotion: Promotion, approver: str, approver_is_platform_admin: bool
) -> None:
    """
    Raises:
        PromotionRefused: Nothing waits for an approval, or the requester
            approves their own promotion.
    """
    waiting = [
        s
        for s in promotion.steps
        if s["kind"] == "approve" and s["status"] != "passed"
    ]
    if not waiting:
        raise PromotionRefused("This promotion does not wait for an approval.")
    if approver == promotion.actor and not approver_is_platform_admin:
        raise PromotionRefused(
            "Someone else has to approve a promotion you started."
        )
    promotion.approvals = [
        *promotion.approvals,
        {"by": approver, "at": now().isoformat()},
    ]


def accept_step(promotion: Promotion, index: int, approver: str) -> None:
    """Accept an inconclusive verification, as its suite's policy asks."""
    steps = [dict(step) for step in promotion.steps]
    if (
        index >= len(steps)
        or steps[index]["status"] != "waiting"
        or steps[index]["kind"] != "run"
    ):
        raise PromotionRefused("This step does not wait for a decision.")
    steps[index]["status"] = "accepted"
    steps[index][
        "detail"
    ] = f"{steps[index].get('detail') or ''} Accepted by {approver}.".strip()
    promotion.steps = steps


def cancel(promotion: Promotion, actor: str) -> None:
    if promotion.status not in ACTIVE:
        raise PromotionRefused("This promotion is already over.")
    if any(
        s["kind"] == "promote" and s["status"] == "passed"
        for s in promotion.steps
    ):
        raise PromotionRefused("Prod already moved: use 'Go back' instead.")
    promotion.status = "cancelled"
    promotion.detail = f"Cancelled by {actor}"
    promotion.finished_at = now()
