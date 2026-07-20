"""
tests/test_head_shoulders_engine.py — Head & Shoulders detection math (the Pattern Detection
"head_shoulders" family). Orchestration (ticker scans, DB save/dedup, scheduler wiring, chart
API) is generic across every family and is covered by tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from head_shoulders_engine import (
    _detect_and_build,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)


def _make_regular_df(confirmed: bool = False) -> pd.DataFrame:
    """Regular (topping) H&S: prior uptrend -> l_shoulder(25,110) -> l_armpit(35,95) ->
    head(50,120) -> r_armpit(60,96) -> r_shoulder(75,108) -> optional breakdown."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 16)[1:]
    seg4 = np.linspace(120, 96, 11)[1:]
    seg5 = np.linspace(96, 108, 16)[1:]
    parts = [seg1, seg2, seg3, seg4, seg5]
    if confirmed:
        parts.append(np.linspace(108, 90, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_regular_df_double_top(confirmed: bool = False) -> pd.DataFrame:
    """Regular H&S whose head is a double top (two independent, unmerged swing highs 6 bars
    apart, 120 then 119.5, with no qualifying swing low between them) — modeled on SMGB.L's
    real 2026-06-22/2026-06-30 twin peaks, which went undetected before the pivot-merge fix
    because the un-merged pair breaks the strict top/bottom/top/bottom alternation check."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 11)[1:]
    dip = np.concatenate([np.linspace(120, 118, 4)[1:], np.linspace(118, 119, 3)[1:]])
    peak2 = np.array([119.5])
    seg4 = np.linspace(119.5, 96, 11)[1:]
    seg5 = np.linspace(96, 108, 16)[1:]
    parts = [seg1, seg2, seg3, dip, peak2, seg4, seg5]
    if confirmed:
        parts.append(np.linspace(108, 90, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_inverse_df(confirmed: bool = False) -> pd.DataFrame:
    """Inverse (bottoming) H&S: prior downtrend -> l_shoulder(25,90) -> l_armpit(35,105) ->
    head(50,80) -> r_armpit(60,104) -> r_shoulder(75,92) -> optional breakout."""
    seg1 = np.linspace(110, 90, 26)
    seg2 = np.linspace(90, 105, 11)[1:]
    seg3 = np.linspace(105, 80, 16)[1:]
    seg4 = np.linspace(80, 104, 11)[1:]
    seg5 = np.linspace(104, 92, 16)[1:]
    parts = [seg1, seg2, seg3, seg4, seg5]
    if confirmed:
        parts.append(np.linspace(92, 110, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_no_trend_df() -> pd.DataFrame:
    """Same H&S geometry but with no prior directional move — sideways oscillation before the
    left shoulder — must be rejected by the prior-trend gate. l_shoulder is still a valid local
    top (spikes just above the oscillation band), but the 20-bar lookback shows no real trend."""
    seg0 = 108.0 + np.sin(np.linspace(0, 4 * np.pi, 25)) * 0.5
    l_shoulder_bump = np.array([110.0])
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 16)[1:]
    seg4 = np.linspace(120, 96, 11)[1:]
    seg5 = np.linspace(96, 108, 16)[1:]
    prices = np.concatenate([seg0, l_shoulder_bump, seg2, seg3, seg4, seg5])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_unbalanced_df() -> pd.DataFrame:
    """H&S geometry where the right shoulder/armpit sit far below the left side's midpoint —
    badly unbalanced, must be rejected by the balance rule."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 16)[1:]
    seg4 = np.linspace(120, 40, 11)[1:]  # right armpit collapses to 40, not 96
    seg5 = np.linspace(40, 45, 16)[1:]   # right shoulder barely recovers to 45
    prices = np.concatenate([seg1, seg2, seg3, seg4, seg5])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _with_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.date_range("2024-01-01", periods=len(df), freq="B")
    return df


def _detect(df: pd.DataFrame, inverted: bool, prior_trend_min_pct: float = 8.0, volume_confirm_multiplier: float = 1.5):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return _detect_and_build(close, volume, rsi_series, vol_sma, inverted, prior_trend_min_pct, volume_confirm_multiplier)


class TestDetectAndBuildRegular:
    def test_forming_phase_when_above_neckline(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=False)
        assert result is not None
        assert result["pattern_type"] == "regular"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_when_below_neckline(self):
        result = _detect(_make_regular_df(confirmed=True), inverted=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None
        assert result["breakout_price"] is not None

    def test_measured_target_below_neckline_for_regular(self):
        result = _detect(_make_regular_df(confirmed=True), inverted=False)
        assert result["measured_target"] < result["neck_value"]

    def test_head_taller_than_shoulders(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=False)
        assert result["head_price"] > result["l_shoulder_price"]
        assert result["head_price"] > result["r_shoulder_price"]

    def test_rejects_when_no_prior_trend(self):
        result = _detect(_make_no_trend_df(), inverted=False)
        assert result is None

    def test_double_top_head_detected_after_merge(self):
        result = _detect(_make_regular_df_double_top(confirmed=False), inverted=False)
        assert result is not None
        assert result["pattern_type"] == "regular"
        assert result["head_price"] == pytest.approx(120.0)

    def test_rejects_when_unbalanced(self):
        result = _detect(_make_unbalanced_df(), inverted=False)
        assert result is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildInverse:
    def test_forming_phase_when_below_neckline(self):
        result = _detect(_make_inverse_df(confirmed=False), inverted=True)
        assert result is not None
        assert result["pattern_type"] == "inverse"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_when_above_neckline(self):
        result = _detect(_make_inverse_df(confirmed=True), inverted=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_above_neckline_for_inverse(self):
        result = _detect(_make_inverse_df(confirmed=True), inverted=True)
        assert result["measured_target"] > result["neck_value"]

    def test_head_lower_than_shoulders(self):
        result = _detect(_make_inverse_df(confirmed=False), inverted=True)
        assert result["head_price"] < result["l_shoulder_price"]
        assert result["head_price"] < result["r_shoulder_price"]

    def test_regular_shape_not_detected_as_inverse(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=True)
        assert result is None


class TestPhaseLabel:
    def test_regular_forming(self):
        assert phase_label("regular", "FORMING") == "Head & Shoulders (Forming)"

    def test_regular_confirmed(self):
        assert phase_label("regular", "CONFIRMED") == "Head & Shoulders (Confirmed)"

    def test_inverse_confirmed(self):
        assert phase_label("inverse", "CONFIRMED") == "Inverse Head & Shoulders (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "head_shoulders"
        assert PATTERN_TYPES == {"regular": "down", "inverse": "up"}

    def test_detect_returns_generic_shape(self):
        df = _with_datetime_index(_make_regular_df(confirmed=True))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert len(result["points"]) == 5
        assert [p["label"] for p in result["points"]] == ["L Shoulder", "L Armpit", "Head", "R Armpit", "R Shoulder"]
        assert isinstance(result["points"][0]["date"], str)
        assert len(result["lines"]) == 1
        assert result["lines"][0]["label"] == "Neckline"
        assert result["breakout_date"] is not None
        assert "l_shoulder_idx" not in result

    def test_detect_respects_family_toggles(self):
        df = _with_datetime_index(_make_regular_df(confirmed=False))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"HEAD_SHOULDERS": {"REGULAR_ENABLED": False, "INVERSE_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None

    def test_detect_returns_none_below_min_bars(self):
        df = _with_datetime_index(_make_regular_df(confirmed=False).head(30))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
