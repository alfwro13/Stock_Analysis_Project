"""
tests/test_double_top_bottom_engine.py — Double Top / Double Bottom detection math (the
Pattern Detection "double_top_bottom" family).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from double_top_bottom_engine import (
    _detect_and_build,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_BALANCE_TOL = 3.0
_MIN_SEP = 3.0


def _make_double_top_df(confirmed: bool = False) -> pd.DataFrame:
    """Prior uptrend -> peak1(25, 110) -> trough(35, 95) -> peak2(50, 109) -> pull-back,
    optional breakdown below the 95 support level."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 109, 16)[1:]
    seg4 = np.linspace(109, 100, 6)[1:]
    parts = [seg1, seg2, seg3, seg4]
    if confirmed:
        parts.append(np.linspace(100, 85, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_double_bottom_df(confirmed: bool = False) -> pd.DataFrame:
    """Prior downtrend -> trough1(25, 90) -> peak(35, 105) -> trough2(50, 91) -> bounce,
    optional breakout above the 105 resistance level."""
    seg1 = np.linspace(110, 90, 26)
    seg2 = np.linspace(90, 105, 11)[1:]
    seg3 = np.linspace(105, 91, 16)[1:]
    seg4 = np.linspace(91, 100, 6)[1:]
    parts = [seg1, seg2, seg3, seg4]
    if confirmed:
        parts.append(np.linspace(100, 115, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_unbalanced_top_df() -> pd.DataFrame:
    """Second peak far below the first — must be rejected by the balance rule."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 101, 16)[1:]  # second peak only 101, vs. first peak 110 (>3% off)
    seg4 = np.linspace(101, 96, 6)[1:]
    prices = np.concatenate([seg1, seg2, seg3, seg4])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_no_separation_top_df() -> pd.DataFrame:
    """Trough sits too close to the two peaks' average — no real pull-back."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 108, 11)[1:]  # trough barely dips to 108
    seg3 = np.linspace(108, 110, 16)[1:]
    seg4 = np.linspace(110, 105, 6)[1:]
    prices = np.concatenate([seg1, seg2, seg3, seg4])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_no_trend_top_df() -> pd.DataFrame:
    """Same double-top geometry with no genuine prior uptrend before the first peak."""
    seg0 = 108.0 + np.sin(np.linspace(0, 4 * np.pi, 25)) * 0.5
    peak1_bump = np.array([110.0])
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 109, 16)[1:]
    seg4 = np.linspace(109, 100, 6)[1:]
    prices = np.concatenate([seg0, peak1_bump, seg2, seg3, seg4])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _with_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.date_range("2024-01-01", periods=len(df), freq="B")
    return df


def _detect(df: pd.DataFrame, is_bottom: bool, prior_trend_min_pct: float = 8.0, volume_confirm_multiplier: float = 1.5):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return _detect_and_build(close, volume, rsi_series, vol_sma, is_bottom, prior_trend_min_pct, volume_confirm_multiplier, _BALANCE_TOL, _MIN_SEP)


class TestDetectAndBuildDoubleTop:
    def test_forming_phase_above_support(self):
        result = _detect(_make_double_top_df(confirmed=False), is_bottom=False)
        assert result is not None
        assert result["pattern_type"] == "double_top"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_below_support(self):
        result = _detect(_make_double_top_df(confirmed=True), is_bottom=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_measured_target_below_support(self):
        result = _detect(_make_double_top_df(confirmed=True), is_bottom=False)
        assert result["measured_target"] < result["neck_value"]

    def test_peaks_roughly_balanced(self):
        result = _detect(_make_double_top_df(confirmed=False), is_bottom=False)
        assert result["ext1_price"] == pytest.approx(result["ext2_price"], rel=0.03)

    def test_rejects_when_unbalanced(self):
        assert _detect(_make_unbalanced_top_df(), is_bottom=False) is None

    def test_rejects_when_no_separation(self):
        assert _detect(_make_no_separation_top_df(), is_bottom=False) is None

    def test_rejects_when_no_prior_trend(self):
        assert _detect(_make_no_trend_top_df(), is_bottom=False) is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_double_top_df(confirmed=False), is_bottom=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildDoubleBottom:
    def test_forming_phase_below_resistance(self):
        result = _detect(_make_double_bottom_df(confirmed=False), is_bottom=True)
        assert result is not None
        assert result["pattern_type"] == "double_bottom"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_above_resistance(self):
        result = _detect(_make_double_bottom_df(confirmed=True), is_bottom=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_above_resistance(self):
        result = _detect(_make_double_bottom_df(confirmed=True), is_bottom=True)
        assert result["measured_target"] > result["neck_value"]

    def test_top_shape_not_detected_as_bottom(self):
        assert _detect(_make_double_top_df(confirmed=False), is_bottom=True) is None


class TestPhaseLabel:
    def test_top_forming(self):
        assert phase_label("double_top", "FORMING") == "Double Top (Forming)"

    def test_top_confirmed(self):
        assert phase_label("double_top", "CONFIRMED") == "Double Top (Confirmed)"

    def test_bottom_confirmed(self):
        assert phase_label("double_bottom", "CONFIRMED") == "Double Bottom (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "double_top_bottom"
        assert PATTERN_TYPES == {"double_top": "down", "double_bottom": "up"}

    def test_detect_returns_generic_shape(self):
        df = _with_datetime_index(_make_double_top_df(confirmed=True))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert len(result["points"]) == 3
        assert [p["label"] for p in result["points"]] == ["Peak 1", "Trough", "Peak 2"]
        assert len(result["lines"]) == 1
        assert result["lines"][0]["label"] == "Support"

    def test_detect_respects_family_toggles(self):
        df = _with_datetime_index(_make_double_top_df(confirmed=False))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"DOUBLE_TOP_BOTTOM": {"TOP_ENABLED": False, "BOTTOM_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
