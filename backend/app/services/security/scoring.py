"""
Score, grade and ring segments of an app or a project.

A score is 100 minus the penalties of the open, non-ignored findings. One
open core finding caps the grade at D whatever the score: a project with a
leaked secret must not read as healthy.
"""

from dataclasses import dataclass, field

from app.services.security.model import (
    CATEGORY_LABELS,
    PENALTY,
    TIER_RANK,
    Category,
    Tier,
)

GRADE_FLOORS = (("A", 90), ("B", 75), ("C", 60), ("D", 40))


@dataclass(frozen=True)
class Scored:
    """What scoring needs to know about a merged, open finding."""

    app: str | None
    category: Category
    tier: Tier
    excepted: bool


def score(findings: list[Scored]) -> int:
    lost = sum(PENALTY[f.tier] for f in findings if not f.excepted)
    return max(0, 100 - lost)


def grade(value: int, has_core: bool) -> str:
    letter = next((g for g, floor in GRADE_FLOORS if value >= floor), "E")
    if has_core and letter in ("A", "B", "C"):
        return "D"
    return letter


def actionable(findings: list[Scored]) -> list[Scored]:
    return [f for f in findings if not f.excepted and f.tier is not Tier.INFO]


def has_core(findings: list[Scored]) -> bool:
    return any(f.tier is Tier.CORE and not f.excepted for f in findings)


@dataclass
class Segment:
    category: Category
    label: str
    worst: Tier | None = None
    counts: dict[str, int] = field(default_factory=dict)
    points_lost: int = 0


def segments(findings: list[Scored]) -> list[Segment]:
    by_category = {
        c: Segment(category=c, label=CATEGORY_LABELS[c]) for c in Category
    }
    for f in actionable(findings):
        segment = by_category[f.category]
        segment.counts[f.tier.value] = segment.counts.get(f.tier.value, 0) + 1
        segment.points_lost += PENALTY[f.tier]
        if (
            segment.worst is None
            or TIER_RANK[f.tier] > TIER_RANK[segment.worst]
        ):
            segment.worst = f.tier
    return list(by_category.values())


def project_score(app_scores: list[int], project_wide: list[Scored]) -> int:
    """Mean of the apps, minus what is wrong with the project itself."""
    base = round(sum(app_scores) / len(app_scores)) if app_scores else 100
    lost = sum(PENALTY[f.tier] for f in project_wide if not f.excepted)
    return max(0, base - lost)
