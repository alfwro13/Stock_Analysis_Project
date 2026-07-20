"""
tests/test_flag_engine.py — Bull Flag / Bear Flag detection math (the Pattern Detection
"flag" family). Orchestration (ticker scans, DB save/dedup, scheduler wiring, chart API) is
generic across every family and is covered by tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from flag_engine import (
    _detect_and_build,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_CONSOLIDATION_SHAPE = np.array([
    120.0, 119.3, 118.6, 117.9, 118.4, 118.9, 119.3, 118.7, 118.0,
    117.3, 117.7, 118.1, 117.6, 117.0, 116.4,
])


def _make_bull_flag_df(confirmed: bool = False) -> pd.DataFrame:
    """Quiet 40-day period -> a +20% flagpole over 10 days -> a 15-day mildly-downward-sloped
    consolidation channel, optional breakout above the upper channel line on the last bar."""
    pre = 100.0 + 0.3 * np.sin(np.arange(40) * 0.9)
    pole = np.linspace(pre[-1], pre[-1] * 1.20, 10)
    consolidation = _CONSOLIDATION_SHAPE * (pole[-1] / 120.0)
    if confirmed:
        consolidation = consolidation.copy()
        consolidation[-1] = consolidation[0] * 1.06
    prices = np.concatenate([pre, pole[1:], consolidation])
    volume = np.concatenate([
        np.full(40, 1_000_000.0), np.full(9, 1_500_000.0),
        np.linspace(1_400_000, 700_000, len(consolidation) - 1),
        [2_000_000.0 if confirmed else 700_000.0],
    ])
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _make_bear_flag_df(confirmed: bool = False) -> pd.DataFrame:
    """Mirror image of the Bull Flag fixture: a -20% flagpole followed by a mildly-upward
    consolidation channel, optional breakout below the lower channel line."""
    pre = 100.0 + 0.3 * np.sin(np.arange(40) * 0.9)
    pole = np.linspace(pre[-1], pre[-1] * 0.80, 10)
    consolidation = (200.0 - _CONSOLIDATION_SHAPE) * (pole[-1] / 80.0)
    if confirmed:
        consolidation = consolidation.copy()
        consolidation[-1] = consolidation[0] * 0.94
    prices = np.concatenate([pre, pole[1:], consolidation])
    volume = np.concatenate([
        np.full(40, 1_000_000.0), np.full(9, 1_500_000.0),
        np.linspace(1_400_000, 700_000, len(consolidation) - 1),
        [2_000_000.0 if confirmed else 700_000.0],
    ])
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _make_no_flagpole_df() -> pd.DataFrame:
    """Flat, low-volatility price series with no sharp N-day move — must be rejected."""
    prices = 100.0 + 0.3 * np.sin(np.arange(65) * 0.9)
    volume = np.full(len(prices), 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    }, index=idx)


_BEAR_TOLERANT_ALERT_CFG = {"PARALLEL_TOLERANCE_PCT": 0.2}


def _detect(df: pd.DataFrame, is_bear: bool, parallel_tolerance_pct: float = 0.15):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    return _detect_and_build(
        close, volume, rsi_series, is_bear,
        sigma_multiplier=1.5, flagpole_days=10, sigma_window_days=20,
        min_consolidation_days=7, max_consolidation_days=15,
        max_channel_slope_pct=0.75, parallel_tolerance_pct=parallel_tolerance_pct,
    )


class TestDetectAndBuildBullFlag:
    def test_forming_phase_inside_channel(self):
        result = _detect(_make_bull_flag_df(confirmed=False), is_bear=False)
        assert result is not None
        assert result["pattern_type"] == "bull_flag"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_above_upper_channel(self):
        result = _detect(_make_bull_flag_df(confirmed=True), is_bear=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_measured_target_above_breakout(self):
        result = _detect(_make_bull_flag_df(confirmed=True), is_bear=False)
        assert result["measured_target"] > result["breakout_price"]

    def test_channel_lines_slope_down(self):
        result = _detect(_make_bull_flag_df(confirmed=False), is_bear=False)
        assert result["upper_today"] < result["upper_start"]
        assert result["lower_today"] < result["lower_start"]

    def test_rejects_when_no_flagpole(self):
        assert _detect(_make_no_flagpole_df(), is_bear=False) is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_bull_flag_df(confirmed=False), is_bear=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildBearFlag:
    def test_forming_phase_inside_channel(self):
        result = _detect(_make_bear_flag_df(confirmed=False), is_bear=True, parallel_tolerance_pct=0.2)
        assert result is not None
        assert result["pattern_type"] == "bear_flag"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_below_lower_channel(self):
        result = _detect(_make_bear_flag_df(confirmed=True), is_bear=True, parallel_tolerance_pct=0.2)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_below_breakout(self):
        result = _detect(_make_bear_flag_df(confirmed=True), is_bear=True, parallel_tolerance_pct=0.2)
        assert result["measured_target"] < result["breakout_price"]

    def test_bull_shape_not_detected_as_bear(self):
        assert _detect(_make_bull_flag_df(confirmed=False), is_bear=True) is None


class TestPhaseLabel:
    def test_bull_forming(self):
        assert phase_label("bull_flag", "FORMING") == "Bull Flag (Forming)"

    def test_bear_confirmed(self):
        assert phase_label("bear_flag", "CONFIRMED") == "Bear Flag (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "flag"
        assert PATTERN_TYPES == {"bull_flag": "up", "bear_flag": "down"}

    def test_detect_returns_generic_shape(self):
        df = _make_bull_flag_df(confirmed=True)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert [p["label"] for p in result["points"]] == ["Pole Start", "Pole End"]
        assert len(result["lines"]) == 2
        assert {l["label"] for l in result["lines"]} == {"Upper Channel", "Lower Channel"}

    def test_detect_respects_family_toggles(self):
        df = _make_bull_flag_df(confirmed=False)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"FLAG": {"BULL_ENABLED": False, "BEAR_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
