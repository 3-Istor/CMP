"""
Rules for ignoring a finding, and the copy the CI reads.

"Not affected" follows the VEX justifications and is a fact, reviewed after a
year. An accepted risk is a decision: it expires within 90 days and needs a
project admin other than its author. A committed secret can never be
accepted, only marked a false positive or revoked once it has been changed.

The CMP database is the reference. The vulnerability and secret exceptions
of an app are also written to ``deploy/security-exceptions.yaml`` in its
repository, which the security workflow turns into ``.trivyignore.yaml`` and
``.gitleaksignore``.
"""

from datetime import date, timedelta
from enum import Enum
from io import StringIO

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

from app.models.security import SecurityException
from app.services.security.model import Category, Source, Tier

EXCEPTIONS_FILE = "deploy/security-exceptions.yaml"
MAX_ACCEPTED_RISK = timedelta(days=90)
MAX_REVIEW = timedelta(days=365)


class Status(str, Enum):
    NOT_AFFECTED = "not_affected"
    FALSE_POSITIVE = "false_positive"
    ACCEPTED_RISK = "accepted_risk"
    REVOKED = "revoked"


class Justification(str, Enum):
    """VEX justifications for ``not_affected``."""

    COMPONENT_NOT_PRESENT = "component_not_present"
    VULNERABLE_CODE_NOT_PRESENT = "vulnerable_code_not_present"
    VULNERABLE_CODE_NOT_IN_EXECUTE_PATH = "vulnerable_code_not_in_execute_path"
    VULNERABLE_CODE_CANNOT_BE_CONTROLLED_BY_ADVERSARY = (
        "vulnerable_code_cannot_be_controlled_by_adversary"
    )
    INLINE_MITIGATIONS_ALREADY_EXIST = "inline_mitigations_already_exist"


class ExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    status: Status
    justification: Justification | None = None
    statement: str = Field(min_length=10, max_length=2000)
    expires_on: date | None = None

    @model_validator(mode="after")
    def _justified(self) -> "ExceptionRequest":
        if (self.status is Status.NOT_AFFECTED) != (
            self.justification is not None
        ):
            raise ValueError(
                "A justification is required for not_affected, and only for it."
            )
        return self


def kind_of(category: Category, source: str) -> str:
    if category is Category.LEAKS:
        return "secret"
    if source in (Source.CI.value, Source.TRIVY_OPERATOR.value) and (
        category is Category.DEPENDENCIES
    ):
        return "vulnerability"
    return "other"


def validate(
    request: ExceptionRequest, kind: str, tier: Tier, today: date
) -> tuple[date, bool]:
    """
    Return the expiry date and whether a project admin must approve.

    Raises:
        ValueError: The request breaks a rule; the message says which.
    """
    if kind == "secret" and request.status not in (
        Status.FALSE_POSITIVE,
        Status.REVOKED,
    ):
        raise ValueError(
            "Un secret commité ne s'accepte pas : faux positif, ou révoqué "
            "après l'avoir changé."
        )
    if request.status is Status.REVOKED and kind != "secret":
        raise ValueError("« Révoqué » ne s'applique qu'à un secret.")

    limit = (
        MAX_ACCEPTED_RISK
        if request.status is Status.ACCEPTED_RISK
        else MAX_REVIEW
    )
    expires_on = request.expires_on or today + limit
    if expires_on <= today:
        raise ValueError("L'expiration doit être dans le futur.")
    if expires_on > today + limit:
        raise ValueError(
            f"Expiration au plus tard le {today + limit:%Y-%m-%d} pour ce motif."
        )
    needs_approval = (
        tier is Tier.CORE or request.status is Status.ACCEPTED_RISK
    )
    return expires_on, needs_approval


def render_file(exceptions: list[SecurityException], today: date) -> str:
    """The repository copy: approved, unexpired vulnerability and secret exceptions."""
    entries = []
    for e in sorted(exceptions, key=lambda e: (e.kind, e.rule)):
        if e.kind not in ("vulnerability", "secret"):
            continue
        if e.revoked_at is not None or e.expires_on < today:
            continue
        entry = {
            "id": e.rule,
            "kind": e.kind,
            "status": e.status,
            "statement": e.statement,
            "expires": e.expires_on.isoformat(),
            "author": e.author,
        }
        if e.justification:
            entry["justification"] = e.justification
        if e.approved_by:
            entry["approvedBy"] = e.approved_by
        entries.append(entry)

    yaml = YAML()
    out = StringIO()
    out.write(
        "# Written by the CMP from its Security page. Edits made here are\n"
        "# overwritten on the next change made in the CMP.\n"
    )
    yaml.dump({"exceptions": entries}, out)
    return out.getvalue()
