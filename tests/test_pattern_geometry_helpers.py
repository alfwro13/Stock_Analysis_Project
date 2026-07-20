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
