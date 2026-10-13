"""
Vocabulary shared by the security collector, store and API.

A finding is one problem on one app, identified by a fingerprint that stays
the same from one scan to the next: that is what tells a new problem from a
known, fixed or ignored one. The same problem seen by two sources (a CVE in
the CI image scan and in Trivy Operator) shares its fingerprint and is shown
once.
"""

import hashlib
from enum import Enum

from pydantic import BaseModel


class Category(str, Enum):
    """The questions a developer asks, one per segment of the ring."""

    LEAKS = "leaks"
    DEPENDENCIES = "dependencies"
    CONTAINER = "container"
    ACCESS = "access"
    DATA = "data"
    JOURNAL = "journal"


class Tier(str, Enum):
    CORE = "core"
    IMPORTANT = "important"
    RECOMMENDED = "recommended"
    INFO = "info"


class Audience(str, Enum):
    """Who can fix it: the app's developers, or the platform team."""

    DEVELOPER = "developer"
    PLATFORM = "platform"


class Source(str, Enum):
    KYVERNO = "kyverno"
    TRIVY_OPERATOR = "trivy-operator"
    CI = "ci"
    CNPG = "cnpg"
    EXPOSURE = "exposure"
    CILIUM = "cilium"
    REPOSITORY = "repository"


CLUSTER_SOURCES = (
    Source.KYVERNO,
    Source.TRIVY_OPERATOR,
    Source.CNPG,
    Source.CILIUM,
)
GITHUB_SOURCES = (Source.CI, Source.EXPOSURE, Source.REPOSITORY)

PENALTY = {
    Tier.CORE: 25,
    Tier.IMPORTANT: 8,
    Tier.RECOMMENDED: 2,
    Tier.INFO: 0,
}

TIER_RANK = {
    Tier.INFO: 0,
    Tier.RECOMMENDED: 1,
    Tier.IMPORTANT: 2,
    Tier.CORE: 3,
}

CATEGORY_LABELS = {
    Category.LEAKS: "Fuites",
    Category.DEPENDENCIES: "Dépendances",
    Category.CONTAINER: "Conteneur",
    Category.ACCESS: "Accès",
    Category.DATA: "Données",
    Category.JOURNAL: "Journal",
}


class Draft(BaseModel):
    """A finding as a source reports it, before it is stored."""

    fingerprint: str
    app: str | None
    source: Source
    category: Category
    tier: Tier
    audience: Audience
    rule: str
    title: str
    detail: str = ""
    fix: str = ""
    location: str = ""
    link: str | None = None
    raw: str = ""


def fingerprint(*parts: object) -> str:
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]
