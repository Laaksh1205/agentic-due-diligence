"""
Temporal weight calculation — Task 2.4 (design doc Section 7).

Risk signals lose relevance over time: a remediated 2019 data breach matters less
than one under active investigation in 2025. ``temporal_weight`` captures this as
a decay factor applied to a signal's effective severity downstream.

Formula (design doc Section 7):

    temporal_weight = max(0.3, 1.0 - years_since_event * 0.15)

    this year   -> 1.00
    1 year ago  -> 0.85
    3 years ago -> 0.55
    5+ years ago-> 0.30   (floor — a risk is never weighted to zero)
    unknown date-> 0.70   (assume "somewhat recent")
"""

from __future__ import annotations

from datetime import date
from typing import Optional

DEFAULT_WEIGHT = 0.7   # data_date is None
MIN_WEIGHT = 0.3       # floor — old signals never fully ignored
MAX_WEIGHT = 1.0
DECAY_PER_YEAR = 0.15
_DAYS_PER_YEAR = 365.25


def calculate_temporal_weight(
    data_date: Optional[date], *, today: Optional[date] = None
) -> float:
    """Return the temporal decay weight in [0.3, 1.0] for an event on *data_date*.

    Args:
        data_date: when the underlying event occurred, or None if unknown.
        today: reference "now" (defaults to ``date.today()``); injectable so the
            calculation is deterministic in tests.

    Returns:
        ``DEFAULT_WEIGHT`` (0.7) when *data_date* is None; otherwise
        ``max(0.3, 1 - years*0.15)``, clamped to a maximum of 1.0 (future-dated
        events are treated as current rather than over-weighted).
    """
    if data_date is None:
        return DEFAULT_WEIGHT
    today = today or date.today()
    years = (today - data_date).days / _DAYS_PER_YEAR
    if years < 0:  # event dated in the future -> treat as current
        years = 0.0
    return max(MIN_WEIGHT, min(MAX_WEIGHT, 1.0 - years * DECAY_PER_YEAR))


__all__ = [
    "calculate_temporal_weight",
    "DEFAULT_WEIGHT",
    "MIN_WEIGHT",
    "MAX_WEIGHT",
    "DECAY_PER_YEAR",
]
