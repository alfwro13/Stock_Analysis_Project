"""
tests/test_triangle_engine.py — Ascending / Descending / Symmetrical Triangle detection math
(the Pattern Detection "triangle" family). Orchestration (ticker scans, DB save/dedup, scheduler
wiring, chart API) is generic across every family and is covered by
tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from triangle_engine import (
    _detect_and_build,
    _detect_symmetrical,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_WINDOW_DAYS = 40


def _make_ascending_df(confirmed: bool = False) -> pd.DataFrame:
    """Mild prior uptrend -> 40-day window with a flat ~100 resistance and a support line
    rising from 90 to 98.5, oscillating between the two; optional breakout above resistance."""
    pre = 80.0 + np.arange(20) * 0.75 + 0.4 * np.sin(np.arange(20) * 1.1)
    resistance = 100.0
    support_start, support_end = 90.0, 98.5
    t = np.linspace(0, 1, _WINDOW_DAYS)
    support_line = support_start + (support_end - support_start) * t
    window = support_line + (resistance - support_line) * (0.5 + 0.5 * np.sin(t * 6 * np.pi))
    window = np.clip(window, support_line, resistance - 0.1)
    if confirmed:
        window = window.copy()
        window[-1] = resistance * 1.03
    prices = np.concatenate([pre, window])
    volume = np.concatenate([
        np.full(len(pre), 1_000_000.0),
        np.linspace(1_200_000, 500_000, _WINDOW_DAYS - 1),
        [2_000_000.0 if confirmed else 500_000.0],
    ])
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.3, "Low": prices - 0.3,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _make_descending_df(confirmed: bool = False) -> pd.DataFrame:
    """Mirror image of the Ascending Triangle fixture: a flat ~100 support and a resistance
    line falling from 110 to 101.5, optional breakout below support."""
    pre = 120.0 - np.arange(20) * 0.75 + 0.4 * np.sin(np.arange(20) * 1.1)
    support = 100.0
    resistance_start, resistance_end = 110.0, 101.5
    t = np.linspace(0, 1, _WINDOW_DAYS)
    resistance_line = resistance_start + (resistance_end - resistance_start) * t
    window = resistance_line - (resistance_line - support) * (0.5 + 0.5 * np.sin(t * 6 * np.pi))
    window = np.clip(window, support + 0.1, resistance_line)
    if confirmed:
        window = window.copy()
        window[-1] = support * 0.97
    prices = np.concatenate([pre, window])
    volume = np.concatenate([
        np.full(len(pre), 1_000_000.0),
        np.linspace(1_200_000, 500_000, _WINDOW_DAYS - 1),
        [2_000_000.0 if confirmed else 500_000.0],
    ])
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.3, "Low": prices - 0.3,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _make_symmetrical_df(breakout: str = None) -> pd.DataFrame:
    """40-day window where resistance falls from 110 to 101 and support rises from 90 to 99,
    oscillating between the two converging lines; optional breakout ('bullish'/'bearish') on
    the final bar past the corresponding line's current (extrapolated) value."""
    pre = 95.0 + 0.3 * np.sin(np.arange(20) * 1.1)
    resistance_start, resistance_end = 110.0, 101.0
    support_start, support_end = 90.0, 99.0
    t = np.linspace(0, 1, _WINDOW_DAYS)
    resistance_line = resistance_start + (resistance_end - resistance_start) * t
    support_line = support_start + (support_end - support_start) * t
    window = support_line + (resistance_line - support_line) * (0.5 + 0.5 * np.sin(t * 6 * np.pi))
    window = np.clip(window, support_line + 0.1, resistance_line - 0.1)
    if breakout == "bullish":
        window = window.copy()
        window[-1] = resistance_end * 1.03
    elif breakout == "bearish":
        window = window.copy()
        window[-1] = support_end * 0.97
    prices = np.concatenate([pre, window])
    volume = np.concatenate([
        np.full(len(pre), 1_000_000.0),
        np.linspace(1_200_000, 500_000, _WINDOW_DAYS - 1),
        [2_000_000.0 if breakout else 500_000.0],
    ])
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.3, "Low": prices - 0.3,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _make_no_triangle_df() -> pd.DataFrame:
    """A smooth, unbroken uptrend with no flat side — must be rejected by both directions."""
    prices = 90.0 + np.arange(60) * 0.5 + 0.4 * np.sin(np.arange(60) * 1.1)
    volume = np.full(len(prices), 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.3, "Low": prices - 0.3,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _detect(df: pd.DataFrame, is_descending: bool):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    return _detect_and_build(
        close, volume, rsi_series, is_descending,
        window_days=_WINDOW_DAYS, flat_slope_epsilon_pct=0.15, min_slope_pct=0.15,
    )


def _detect_sym(df: pd.DataFrame, bullish_enabled: bool = True, bearish_enabled: bool = True):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    return _detect_symmetrical(
        close, volume, rsi_series,
        window_days=_WINDOW_DAYS, min_slope_pct=0.15,
        bullish_enabled=bullish_enabled, bearish_enabled=bearish_enabled,
    )


class TestDetectAndBuildAscending:
    def test_forming_phase_below_resistance(self):
        result = _detect(_make_ascending_df(confirmed=False), is_descending=False)
        assert result is not None
        assert result["pattern_type"] == "ascending"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_above_resistance(self):
        result = _detect(_make_ascending_df(confirmed=True), is_descending=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_measured_target_above_resistance(self):
        result = _detect(_make_ascending_df(confirmed=True), is_descending=False)
        assert result["measured_target"] > result["flat_level"]

    def test_rejects_when_no_triangle(self):
        assert _detect(_make_no_triangle_df(), is_descending=False) is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_ascending_df(confirmed=False), is_descending=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildDescending:
    def test_forming_phase_above_support(self):
        result = _detect(_make_descending_df(confirmed=False), is_descending=True)
        assert result is not None
        assert result["pattern_type"] == "descending"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_below_support(self):
        result = _detect(_make_descending_df(confirmed=True), is_descending=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_below_support(self):
        result = _detect(_make_descending_df(confirmed=True), is_descending=True)
        assert result["measured_target"] < result["flat_level"]

    def test_ascending_shape_not_detected_as_descending(self):
        assert _detect(_make_ascending_df(confirmed=False), is_descending=True) is None


class TestDetectSymmetrical:
    def test_forming_phase_between_lines(self):
        result = _detect_sym(_make_symmetrical_df())
        assert result is not None
        assert result["pattern_type"] == "symmetrical_triangle"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_bullish_breakout(self):
        result = _detect_sym(_make_symmetrical_df(breakout="bullish"))
        assert result is not None
        assert result["pattern_type"] == "symmetrical_triangle_bullish"
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_confirmed_bearish_breakout(self):
        result = _detect_sym(_make_symmetrical_df(breakout="bearish"))
        assert result is not None
        assert result["pattern_type"] == "symmetrical_triangle_bearish"
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_above_close_when_bullish(self):
        result = _detect_sym(_make_symmetrical_df(breakout="bullish"))
        assert result["measured_target"] > result["close_price"]

    def test_measured_target_below_close_when_bearish(self):
        result = _detect_sym(_make_symmetrical_df(breakout="bearish"))
        assert result["measured_target"] < result["close_price"]

    def test_rejects_ascending_shape(self):
        assert _detect_sym(_make_ascending_df(confirmed=False)) is None

    def test_rejects_descending_shape(self):
        assert _detect_sym(_make_descending_df(confirmed=False)) is None

    def test_rejects_when_no_triangle(self):
        assert _detect_sym(_make_no_triangle_df()) is None

    def test_bullish_toggle_suppresses_confirmed_bullish(self):
        result = _detect_sym(_make_symmetrical_df(breakout="bullish"), bullish_enabled=False)
        assert result is None

    def test_bearish_toggle_suppresses_confirmed_bearish(self):
        result = _detect_sym(_make_symmetrical_df(breakout="bearish"), bearish_enabled=False)
        assert result is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect_sym(_make_symmetrical_df())
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestPhaseLabel:
    def test_ascending_forming(self):
        assert phase_label("ascending", "FORMING") == "Ascending Triangle (Forming)"

    def test_descending_confirmed(self):
        assert phase_label("descending", "CONFIRMED") == "Descending Triangle (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"

    def test_symmetrical_bullish_confirmed(self):
        assert phase_label("symmetrical_triangle_bullish", "CONFIRMED") == "Symmetrical Triangle (Bullish) (Confirmed)"

    def test_symmetrical_forming(self):
        assert phase_label("symmetrical_triangle", "FORMING") == "Symmetrical Triangle (Forming)"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "triangle"
        assert PATTERN_TYPES == {
            "ascending": "up",
            "descending": "down",
            "symmetrical_triangle_bullish": "up",
            "symmetrical_triangle_bearish": "down",
        }

    def test_detect_returns_generic_shape(self):
        df = _make_ascending_df(confirmed=True)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert len(result["points"]) >= 4
        assert len(result["lines"]) == 1
        assert result["lines"][0]["label"] == "Resistance"

    def test_detect_returns_symmetrical_shape(self):
        df = _make_symmetrical_df(breakout="bullish")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["pattern_type"] == "symmetrical_triangle_bullish"
        assert result["phase"] == "CONFIRMED"
        assert len(result["lines"]) == 2
        assert {line["label"] for line in result["lines"]} == {"Resistance (Falling)", "Support (Rising)"}

    def test_detect_respects_family_toggles(self):
        df = _make_ascending_df(confirmed=False)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"TRIANGLE": {"ASCENDING_ENABLED": False, "DESCENDING_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None

    def test_detect_respects_symmetrical_direction_toggles(self):
        df = _make_symmetrical_df(breakout="bullish")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"TRIANGLE": {"BULLISH_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
