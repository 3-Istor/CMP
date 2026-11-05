"""
Release tags the "New release" button proposes: ``vX.Y.Z``, or
``vX.Y.Z-rc.N`` for an app with a staging environment (release candidates
deploy to staging). Any other tag is ignored.
"""

import re
from dataclasses import dataclass
from typing import Literal

_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?", re.ASCII)


@dataclass(frozen=True)
class ReleaseChoice:
    kind: Literal["candidate", "patch", "minor", "major"]
    tag: str


def release_choices(tags: list[str], staged: bool) -> list[ReleaseChoice]:
    finals: set[tuple[int, int, int]] = set()
    candidates: dict[tuple[int, int, int], int] = {}  # version -> highest rc
    for name in tags:
        if not (match := _TAG.fullmatch(name)):
            continue
        version = tuple(int(part) for part in match.groups()[:3])
        if match[4] is None:
            finals.add(version)
        else:
            candidates[version] = max(
                candidates.get(version, 0), int(match[4])
            )
    base = max(finals | candidates.keys(), default=None)

    def choice(kind, version):
        tag = "v" + ".".join(map(str, version))
        if staged:
            tag += f"-rc.{candidates.get(version, 0) + 1}"
        return ReleaseChoice(kind, tag)

    choices = []
    # Only the newest version counts: a candidate below a final is abandoned.
    if staged and base in candidates and base not in finals:
        choices.append(choice("candidate", base))
    if base is None:
        bumps = {"patch": (0, 1, 0), "minor": (0, 1, 0), "major": (1, 0, 0)}
    else:
        major, minor, patch = base
        bumps = {
            "patch": (major, minor, patch + 1),
            "minor": (major, minor + 1, 0),
            "major": (major + 1, 0, 0),
        }
    choices += [choice(kind, version) for kind, version in bumps.items()]

    unique: dict[str, ReleaseChoice] = {}
    for item in choices:
        unique.setdefault(item.tag, item)
    return list(unique.values())
