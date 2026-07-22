"""
tests/test_wedge_engine.py — Rising / Falling Wedge detection math (the Pattern Detection
"wedge" family). Orchestration (ticker scans, DB save/dedup, scheduler wiring, chart API) is
generic across every family and is covered by tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from wedge_engine import (
    _detect_and_build,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_WINDOW_DAYS = 40


def _make_rising_wedge_df(confirmed: bool = False) -> pd.DataFrame:
    """40-day window where both resistance (100->108) and support (90->104) rise, support
    rising faster so the lines converge; optional breakdown below support on the final bar."""
    pre = 80.0 + np.arange(20) * 0.4 + 0.3 * np.sin(np.arange(20) * 1.1)
    resistance_start, resistance_end = 100.0, 108.0
    support_start, support_end = 90.0, 104.0
    t = np.linspace(0, 1, _WINDOW_DAYS)
    resistance_line = resistance_start + (resistance_end - resistance_start) * t
    support_line = support_start + (support_end - support_start) * t
    window = support_line + (resistance_line - support_line) * (0.5 + 0.5 * np.sin(t * 6 * np.pi))
    window = np.clip(window, support_line + 0.1, resistance_line - 0.1)
    if confirmed:
        window = window.copy()
        window[-1] = support_end * 0.97
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


def _make_falling_wedge_df(confirmed: bool = False) -> pd.DataFrame:
    """Mirror image: both resistance (110->96) and support (100->92) fall, resistance falling
    faster so the lines converge; optional breakout above resistance on the final bar."""
    pre = 130.0 - np.arange(20) * 0.4 + 0.3 * np.sin(np.arange(20) * 1.1)
    resistance_start, resistance_end = 110.0, 96.0
    support_start, support_end = 100.0, 92.0
    t = np.linspace(0, 1, _WINDOW_DAYS)
    resistance_line = resistance_start + (resistance_end - resistance_start) * t
    support_line = support_start + (support_end - support_start) * t
    window = support_line + (resistance_line - support_line) * (0.5 + 0.5 * np.sin(t * 6 * np.pi))
    window = np.clip(window, support_line + 0.1, resistance_line - 0.1)
    if confirmed:
        window = window.copy()
        window[-1] = resistance_end * 1.03
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


def _make_no_wedge_df() -> pd.DataFrame:
    """A flat resistance / rising support shape (an Ascending Triangle) — must be rejected by
    both wedge directions since resistance isn't sloped past the minimum in either direction."""
    pre = 80.0 + np.arange(20) * 0.5 + 0.3 * np.sin(np.arange(20) * 1.1)
    resistance = 100.0
    support_start, support_end = 90.0, 98.5
    t = np.linspace(0, 1, _WINDOW_DAYS)
    support_line = support_start + (support_end - support_start) * t
    window = support_line + (resistance - support_line) * (0.5 + 0.5 * np.sin(t * 6 * np.pi))
    window = np.clip(window, support_line, resistance - 0.1)
    prices = np.concatenate([pre, window])
    volume = np.full(len(prices), 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.3, "Low": prices - 0.3,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _detect(df: pd.DataFrame, is_falling: bool):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    return _detect_and_build(
        close, volume, rsi_series, is_falling,
        window_days=_WINDOW_DAYS, min_slope_pct=0.15, min_convergence_diff_pct=0.1,
    )


class TestDetectAndBuildRisingWedge:
    def test_forming_phase_between_lines(self):
        result = _detect(_make_rising_wedge_df(confirmed=False), is_falling=False)
        assert result is not None
        assert result["pattern_type"] == "rising_wedge"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_below_support(self):
        result = _detect(_make_rising_wedge_df(confirmed=True), is_falling=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_measured_target_below_breakout(self):
        result = _detect(_make_rising_wedge_df(confirmed=True), is_falling=False)
        assert result["measured_target"] < result["breakout_price"]

    def test_rejects_when_not_converging(self):
        assert _detect(_make_no_wedge_df(), is_falling=False) is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_rising_wedge_df(confirmed=False), is_falling=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildFallingWedge:
    def test_forming_phase_between_lines(self):
        result = _detect(_make_falling_wedge_df(confirmed=False), is_falling=True)
        assert result is not None
        assert result["pattern_type"] == "falling_wedge"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_above_resistance(self):
        result = _detect(_make_falling_wedge_df(confirmed=True), is_falling=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_above_breakout(self):
        result = _detect(_make_falling_wedge_df(confirmed=True), is_falling=True)
        assert result["measured_target"] > result["breakout_price"]

    def test_rising_shape_not_detected_as_falling(self):
        assert _detect(_make_rising_wedge_df(confirmed=False), is_falling=True) is None


class TestPhaseLabel:
    def test_rising_forming(self):
        assert phase_label("rising_wedge", "FORMING") == "Rising Wedge (Forming)"

    def test_falling_confirmed(self):
        assert phase_label("falling_wedge", "CONFIRMED") == "Falling Wedge (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "wedge"
        assert PATTERN_TYPES == {"rising_wedge": "down", "falling_wedge": "up"}

    def test_detect_returns_generic_shape(self):
        df = _make_rising_wedge_df(confirmed=True)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert len(result["lines"]) == 2
        assert {line["label"] for line in result["lines"]} == {"Resistance", "Support"}

    def test_detect_respects_family_toggles(self):
        df = _make_rising_wedge_df(confirmed=False)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"WEDGE": {"RISING_ENABLED": False, "FALLING_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
