"""Terraform output parsing for cluster operation progress."""

import re

_PLAN = re.compile(r"Plan: (\d+) to add, \d+ to change, (\d+) to destroy")
_DONE = {"up": "Creation complete", "down": "Destruction complete"}


def plan_total(line: str) -> int | None:
    m = _PLAN.search(line)
    return (int(m[1]) or int(m[2])) if m else None


def is_resource_done(line: str, operation: str) -> bool:
    return _DONE[operation] in line
