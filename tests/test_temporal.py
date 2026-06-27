"""
Tests for temporal weight calculation — Task 2.4.2.

Uses a fixed reference date so the decay math is deterministic.
"""

from datetime import date, timedelta

import pytest

from src.analysis.temporal import (
    DEFAULT_WEIGHT,
    MIN_WEIGHT,
    calculate_temporal_weight,
)

TODAY = date(2025, 6, 15)


def _years_ago(n: int) -> date:
    return date(TODAY.year - n, TODAY.month, TODAY.day)


def test_today_is_full_weight():
    assert calculate_temporal_weight(TODAY, today=TODAY) == pytest.approx(1.0)


def test_one_year_ago():
    assert calculate_temporal_weight(_years_ago(1), today=TODAY) == pytest.approx(0.85, abs=0.01)


def test_three_years_ago():
    assert calculate_temporal_weight(_years_ago(3), today=TODAY) == pytest.approx(0.55, abs=0.01)


def test_five_years_ago_hits_floor():
    # 1 - 5*0.15 = 0.25 -> clamped up to the 0.30 floor
    assert calculate_temporal_weight(_years_ago(5), today=TODAY) == pytest.approx(0.30, abs=0.01)


def test_ten_years_ago_stays_at_floor():
    assert calculate_temporal_weight(_years_ago(10), today=TODAY) == MIN_WEIGHT


def test_none_returns_default():
    assert calculate_temporal_weight(None) == DEFAULT_WEIGHT == 0.7


def test_future_date_clamped_to_one():
    future = TODAY + timedelta(days=400)
    assert calculate_temporal_weight(future, today=TODAY) == pytest.approx(1.0)


def test_weight_always_within_bounds():
    for n in range(0, 40):
        w = calculate_temporal_weight(_years_ago(n), today=TODAY)
        assert MIN_WEIGHT <= w <= 1.0


def test_monotonic_non_increasing_with_age():
    weights = [calculate_temporal_weight(_years_ago(n), today=TODAY) for n in range(0, 8)]
    assert weights == sorted(weights, reverse=True)
