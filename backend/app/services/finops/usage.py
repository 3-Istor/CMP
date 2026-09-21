"""
Deterministic simulated resource usage.

There is no real metrics backend, so we synthesise a plausible usage signal that
is **stable** (same inputs → same output) yet varied across apps, resources and
time. This single signal drives both the timeline graph and the recommendation
engine (an app whose average usage sits far below its quota is over-provisioned).
"""

from __future__ import annotations

import hashlib
import math
from datetime import date


def _seed(app_id: int, resource: str, day: date) -> float:
    """A stable float in [0, 1) derived from (app, resource, day)."""
    raw = f"{app_id}:{resource}:{day.isoformat()}".encode()
    digest = hashlib.sha256(raw).digest()
    # Use 4 bytes → integer → normalise.
    n = int.from_bytes(digest[:4], "big")
    return n / 0xFFFFFFFF


def _base_load(app_id: int, resource: str) -> float:
    """
    Per-app, per-resource baseline utilisation (0..1).

    Some apps are deliberately over-provisioned (low base load) so the
    recommendation engine always has material to work with.
    """
    raw = f"base:{app_id}:{resource}".encode()
    n = int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") / 0xFFFFFFFF
    # Spread baseline between 15% and 85%.
    return 0.15 + 0.70 * n


def simulated_usage_pct(app_id: int, resource: str, day: date) -> float:
    """
    Simulated utilisation ratio (0..1) of ``resource`` for app ``app_id`` on
    ``day``: how much of the allocated quota is actually used.

    Combines a per-app baseline with daily and weekly sinusoidal variation plus
    a little deterministic noise. Never exactly 0 or 1.
    """
    base = _base_load(app_id, resource)

    # Weekly rhythm (busier mid-week) + intra-period wobble.
    weekly = 0.10 * math.sin((day.toordinal() % 7) / 7.0 * 2 * math.pi)
    monthly = 0.06 * math.sin((day.day / 30.0) * 2 * math.pi)
    noise = (_seed(app_id, resource, day) - 0.5) * 0.12

    value = base + weekly + monthly + noise
    return max(0.02, min(0.99, value))


def has_spike(app_id: int, resource: str, day: date) -> bool:
    """
    Deterministic 'anomalous consumption' marker: true on rare days where usage
    jumps far above the app's recent average (> 150%). Used for spike alerts.
    """
    # ~1 day in 20, stable per (app, resource, day).
    return _seed(app_id, f"spike:{resource}", day) > 0.95


def is_inactive(app_id: int, day: date, window_days: int = 7) -> bool:
    """
    True when simulated network traffic has been ~nil for ``window_days`` up to
    and including ``day`` — used to flag prolonged inactivity.
    """
    from datetime import timedelta

    for offset in range(window_days):
        d = day - timedelta(days=offset)
        if simulated_usage_pct(app_id, "network", d) > 0.08:
            return False
    return True
