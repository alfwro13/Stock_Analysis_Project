"""
tests/test_pattern_geometry_helpers.py — shared swing-point/pivot math used by every
Pattern Detection family (head_shoulders_engine.py, double_top_bottom_engine.py, ...).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pattern_geometry_helpers import (
    rw_top,
    rw_bottom,
    find_pivots,
    merge_adjacent_pivots,
    latest_alternating_run,
    piecewise_r2,
    volume_confirms,
    rsi_divergence,
    linreg,
    slope_pct_per_day,
    candle_body_wick_metrics,
    is_bullish_engulfing,
    is_bearish_engulfing,
    is_hammer,
    is_shooting_star,
)


class TestRwExtrema:
    def test_rw_top_detects_peak(self):
        data = np.concatenate([np.linspace(0, 10, 10), np.linspace(10, 0, 10)])
        assert rw_top(data, 14, 5) is True

    def test_rw_bottom_detects_trough(self):
        data = np.concatenate([np.linspace(10, 0, 10), np.linspace(0, 10, 10)])
        assert rw_bottom(data, 14, 5) is True

    def test_rw_top_false_on_monotonic_series(self):
        data = np.linspace(0, 10, 20)
        assert not any(rw_top(data, i, 5) for i in range(len(data)))

    def test_too_early_index_returns_false(self):
        data = np.linspace(0, 10, 20)
        assert rw_top(data, 3, 5) is False
        assert rw_bottom(data, 3, 5) is False


class TestMergeAdjacentPivots:
    def test_higher_peak_wins(self):
        prices = np.array([0.0, 10.0, 0.0, 12.0, 0.0])
        raw_events = [(1, 1), (3, 1)]
        merged = merge_adjacent_pivots(raw_events, prices)
        assert merged == [(3, 1)]

    def test_lower_trough_wins(self):
        prices = np.array([10.0, 5.0, 6.0, 4.5, 8.0])
        raw_events = [(1, -1), (3, -1)]
        merged = merge_adjacent_pivots(raw_events, prices)
        assert merged == [(3, -1)]

    def test_no_merge_when_alternating(self):
        raw_events = [(10, 1), (20, -1), (30, 1), (40, -1)]
        prices = np.zeros(50)
        assert merge_adjacent_pivots(raw_events, prices) == raw_events


class TestLatestAlternatingRun:
    def test_finds_4_point_regular_run(self):
        seg1 = np.linspace(90, 110, 26)
        seg2 = np.linspace(110, 95, 11)[1:]
        seg3 = np.linspace(95, 120, 16)[1:]
        seg4 = np.linspace(120, 96, 11)[1:]
        seg5 = np.linspace(96, 108, 16)[1:]
        prices = np.concatenate([seg1, seg2, seg3, seg4, seg5])
        run = latest_alternating_run(prices, 5, 4, wanted_first=1)
        assert run == [25, 35, 50, 60]

    def test_returns_none_with_too_few_extrema(self):
        data = np.linspace(0, 10, 20)
        assert latest_alternating_run(data, 5, 4, wanted_first=1) is None

    def test_finds_3_point_double_top_run(self):
        seg1 = np.linspace(90, 110, 26)
        seg2 = np.linspace(110, 95, 11)[1:]
        seg3 = np.linspace(95, 118, 16)[1:]
        seg4 = np.linspace(118, 110, 6)[1:]  # trailing bars so the second peak is a valid pivot
        prices = np.concatenate([seg1, seg2, seg3, seg4])
        run = latest_alternating_run(prices, 5, 3, wanted_first=1)
        assert run == [25, 35, 50]


class TestPiecewiseR2:
    def test_perfect_fit_scores_near_one(self):
        pivots = [0, 10, 20]
        closes = np.zeros(21)
        closes[0:11] = np.linspace(100, 90, 11)
        closes[10:21] = np.linspace(90, 105, 11)
        r2 = piecewise_r2(closes, pivots, 20)
        assert r2 is not None
        assert r2 > 0.95

    def test_none_when_end_before_start(self):
        assert piecewise_r2(np.zeros(10), [5, 7], 3) is None


class TestVolumeConfirms:
    def test_declining_volume_confirms_forming(self):
        volume = np.array([100.0] * 60 + [50.0] * 20)
        vol_sma = pd.Series(np.full(80, 90.0))
        assert volume_confirms(volume, vol_sma, 5, 70, 79, confirmed=False, multiplier=1.5) is True

    def test_rising_volume_does_not_confirm_forming(self):
        volume = np.array([50.0] * 60 + [100.0] * 20)
        vol_sma = pd.Series(np.full(80, 90.0))
        assert volume_confirms(volume, vol_sma, 5, 70, 79, confirmed=False, multiplier=1.5) is False

    def test_confirmed_requires_breakout_surge(self):
        volume = np.array([100.0] * 60 + [50.0] * 19 + [500.0])
        vol_sma = pd.Series(np.full(80, 90.0))
        assert volume_confirms(volume, vol_sma, 5, 70, 79, confirmed=True, multiplier=1.5) is True

    def test_confirmed_without_surge_fails(self):
        volume = np.array([100.0] * 60 + [50.0] * 20)
        vol_sma = pd.Series(np.full(80, 90.0))
        assert volume_confirms(volume, vol_sma, 5, 70, 79, confirmed=True, multiplier=1.5) is False


class TestRsiDivergence:
    def test_bearish_divergence_not_inverted(self):
        rsi = pd.Series([70.0] * 100)
        rsi.iloc[10] = 60.0
        rsi.iloc[50] = 55.0
        assert rsi_divergence(rsi, 10, 50, inverted=False) is True

    def test_no_divergence_not_inverted(self):
        rsi = pd.Series([70.0] * 100)
        rsi.iloc[10] = 50.0
        rsi.iloc[50] = 60.0
        assert rsi_divergence(rsi, 10, 50, inverted=False) is False

    def test_bullish_divergence_inverted(self):
        rsi = pd.Series([30.0] * 100)
        rsi.iloc[10] = 30.0
        rsi.iloc[50] = 35.0
        assert rsi_divergence(rsi, 10, 50, inverted=True) is True

    def test_nan_returns_false(self):
        rsi = pd.Series([np.nan] * 100)
        assert rsi_divergence(rsi, 10, 50, inverted=False) is False


class TestLinreg:
    def test_perfect_line_gives_exact_slope_and_r2_one(self):
        x = np.arange(10)
        y = 2.0 * x + 5.0
        result = linreg(x, y)
        assert result is not None
        slope, intercept, r2 = result
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(5.0)
        assert r2 == pytest.approx(1.0)

    def test_none_with_fewer_than_two_points(self):
        assert linreg(np.array([1.0]), np.array([1.0])) is None

    def test_none_with_zero_x_range(self):
        assert linreg(np.array([5.0, 5.0, 5.0]), np.array([1.0, 2.0, 3.0])) is None

    def test_noisy_line_scores_below_one(self):
        x = np.arange(20)
        y = 1.5 * x + np.array([0, 1, -1, 0.5, -0.5] * 4)
        _, _, r2 = linreg(x, y)
        assert 0.0 < r2 < 1.0


class TestSlopePctPerDay:
    def test_normalizes_by_reference_price(self):
        assert slope_pct_per_day(1.0, 100.0) == pytest.approx(1.0)
        assert slope_pct_per_day(1.0, 200.0) == pytest.approx(0.5)

    def test_zero_or_negative_reference_price_returns_zero(self):
        assert slope_pct_per_day(1.0, 0.0) == 0.0
        assert slope_pct_per_day(1.0, -50.0) == 0.0


class TestCandleBodyWickMetrics:
    def test_bullish_candle_with_both_wicks(self):
        m = candle_body_wick_metrics(open_=10.0, high=12.0, low=9.0, close=11.0)
        assert m["body"] == pytest.approx(1.0)
        assert m["range"] == pytest.approx(3.0)
        assert m["upper_wick"] == pytest.approx(1.0)
        assert m["lower_wick"] == pytest.approx(1.0)
        assert m["is_bullish"] is True
        assert m["is_bearish"] is False

    def test_zero_body_and_range_are_floored(self):
        m = candle_body_wick_metrics(open_=10.0, high=10.0, low=10.0, close=10.0)
        assert m["body_safe"] >= 0.001
        assert m["range"] >= 0.001


class TestEngulfing:
    def test_bullish_engulfing_true(self):
        # Prior bearish (10 -> 8), current bullish and fully containing it (7 -> 11).
        assert is_bullish_engulfing(prev_open=10.0, prev_close=8.0, curr_open=7.0, curr_close=11.0) is True

    def test_bullish_engulfing_false_when_prior_not_bearish(self):
        # Containment inequalities alone hold, but the prior candle is bullish, not bearish.
        assert is_bullish_engulfing(prev_open=8.0, prev_close=10.0, curr_open=7.0, curr_close=11.0) is False

    def test_bullish_engulfing_false_when_containment_fails(self):
        assert is_bullish_engulfing(prev_open=10.0, prev_close=8.0, curr_open=8.5, curr_close=9.5) is False

    def test_bearish_engulfing_true(self):
        assert is_bearish_engulfing(prev_open=8.0, prev_close=10.0, curr_open=11.0, curr_close=7.0) is True

    def test_bearish_engulfing_false_when_prior_not_bullish(self):
        assert is_bearish_engulfing(prev_open=10.0, prev_close=8.0, curr_open=11.0, curr_close=7.0) is False


class TestPinBars:
    def test_hammer_true(self):
        assert is_hammer(upper_wick=0.1, lower_wick=2.5, body_safe=1.0, rng=3.0) is True

    def test_hammer_false_when_wick_too_short(self):
        assert is_hammer(upper_wick=0.1, lower_wick=1.5, body_safe=1.0, rng=3.0) is False

    def test_hammer_false_when_opposite_wick_not_negligible(self):
        assert is_hammer(upper_wick=1.0, lower_wick=2.5, body_safe=1.0, rng=3.0) is False

    def test_shooting_star_true(self):
        assert is_shooting_star(upper_wick=2.5, lower_wick=0.1, body_safe=1.0, rng=3.0) is True

    def test_shooting_star_false_when_wick_too_short(self):
        assert is_shooting_star(upper_wick=1.5, lower_wick=0.1, body_safe=1.0, rng=3.0) is False
