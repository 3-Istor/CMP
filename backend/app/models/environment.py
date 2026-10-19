from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AppEnvironment(Base):
    """
    A non-prod environment of an app, and where its provisioning stands.

    Prod is implicit: every app has it. The registry record says which
    environments exist; this row tracks what the CMP did to get there.
    """

    __tablename__ = "app_environments"

    deployment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    # provisioning | ready | failed | removing
    state: Mapped[str] = mapped_column(String(16))
    hostname: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PromotionPolicy(Base):
    """
    How an app with a staging environment reaches prod.

    Lot 1 keeps the policy here, set from the CMP. The platform's own steps
    (the smoke check, the digest check before the commit) are added on top
    and cannot be removed from it.
    """

    __tablename__ = "promotion_policies"

    deployment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # [{"name": "e2e", "kind": "external", "workflow": "cnp-verify.yml"},
    #  {"name": "errors", "kind": "metrics", ...}]
    suites: Mapped[list] = mapped_column(JSON, default=list)
    soak_minutes: Mapped[int] = mapped_column(Integer, default=0)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_promote: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class VerificationRun(Base):
    """One run of a suite against one environment, for the digests it found there."""

    __tablename__ = "verification_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(Integer, index=True)
    environment: Mapped[str] = mapped_column(String(16))
    suite: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    # {"backend": "ghcr.io/o/app/backend@sha256:..."}: what was really running.
    digests: Mapped[dict] = mapped_column(JSON)
    version: Mapped[str | None] = mapped_column(String(128))
    # running | passed | failed | inconclusive | cancelled | error
    status: Mapped[str] = mapped_column(String(16), default="running")
    summary: Mapped[str | None] = mapped_column(Text)
    details_url: Mapped[str | None] = mapped_column(String(512))
    measurements: Mapped[dict | None] = mapped_column(JSON)
    # sha256 of the one-time callback token of an external run.
    token_hash: Mapped[str | None] = mapped_column(String(64))
    promotion_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class Promotion(Base):
    """
    Taking the release that ran in staging to prod, step by step.

    The digests are fixed when the promotion starts: a release candidate that
    lands in staging meanwhile does not change what this promotion ships.
    """

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(Integer, index=True)
    source_env: Mapped[str] = mapped_column(String(16))
    target_env: Mapped[str] = mapped_column(String(16))
    version: Mapped[str | None] = mapped_column(String(128))
    digests: Mapped[dict] = mapped_column(JSON)
    # running | waiting_approval | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(24), default="running")
    # [{"name", "kind", "status", "detail", "runId"}]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    # Set for "go back to a version prod already ran": no staging checks.
    rollback_of: Mapped[int | None] = mapped_column(Integer)
    approvals: Mapped[list] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
