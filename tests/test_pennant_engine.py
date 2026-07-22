"""
tests/test_pennant_engine.py — Bull Pennant / Bear Pennant detection math (the Pattern
Detection "pennant" family). Orchestration (ticker scans, DB save/dedup, scheduler wiring,
chart API) is generic across every family and is covered by
tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from pennant_engine import (
    _detect_and_build,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)


def _seg(a: float, b: float, n: int) -> np.ndarray:
    return np.linspace(a, b, n)[:-1]


# A hand-built converging micro-triangle: peak1(103) -> trough1(97) -> peak2(101, lower) ->
# trough2(97.5, higher) -> a short tail ending inside the range (FORMING) or breaking out.
_CONSOLIDATION_SHAPE = np.concatenate([
    _seg(100, 103, 4), _seg(103, 97, 4), _seg(97, 101, 4), _seg(101, 97.5, 4), _seg(97.5, 98.7, 4),
    np.array([98.7]),
])


def _make_bull_pennant_df(confirmed: bool = False) -> pd.DataFrame:
    pre = 100.0 + 0.3 * np.sin(np.arange(40) * 0.9)
    pole = np.linspace(pre[-1], pre[-1] * 1.20, 10)
    scale = pole[-1] / 100.0
    consolidation = _CONSOLIDATION_SHAPE * scale
    if confirmed:
        consolidation = consolidation.copy()
        consolidation[-1] = 105.0 * scale
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


def _make_bear_pennant_df(confirmed: bool = False) -> pd.DataFrame:
    pre = 100.0 + 0.3 * np.sin(np.arange(40) * 0.9)
    pole = np.linspace(pre[-1], pre[-1] * 0.80, 10)
    scale = pole[-1] / 100.0
    consolidation = (200.0 - _CONSOLIDATION_SHAPE) * scale
    if confirmed:
        consolidation = consolidation.copy()
        consolidation[-1] = 95.0 * scale
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
    prices = 100.0 + 0.3 * np.sin(np.arange(65) * 0.9)
    volume = np.full(len(prices), 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _detect(df: pd.DataFrame, is_bear: bool):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    return _detect_and_build(
        close, volume, rsi_series, is_bear,
        sigma_multiplier=1.5, flagpole_days=10, sigma_window_days=20,
        min_consolidation_days=16, max_consolidation_days=16, min_slope_pct=0.05,
    )


class TestDetectAndBuildBullPennant:
    def test_forming_phase_inside_triangle(self):
        result = _detect(_make_bull_pennant_df(confirmed=False), is_bear=False)
        assert result is not None
        assert result["pattern_type"] == "bull_pennant"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_above_resistance(self):
        result = _detect(_make_bull_pennant_df(confirmed=True), is_bear=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_measured_target_above_breakout(self):
        result = _detect(_make_bull_pennant_df(confirmed=True), is_bear=False)
        assert result["measured_target"] > result["breakout_price"]

    def test_rejects_when_no_flagpole(self):
        assert _detect(_make_no_flagpole_df(), is_bear=False) is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_bull_pennant_df(confirmed=False), is_bear=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildBearPennant:
    def test_forming_phase_inside_triangle(self):
        result = _detect(_make_bear_pennant_df(confirmed=False), is_bear=True)
        assert result is not None
        assert result["pattern_type"] == "bear_pennant"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_below_support(self):
        result = _detect(_make_bear_pennant_df(confirmed=True), is_bear=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_below_breakout(self):
        result = _detect(_make_bear_pennant_df(confirmed=True), is_bear=True)
        assert result["measured_target"] < result["breakout_price"]

    def test_bull_shape_not_detected_as_bear(self):
        assert _detect(_make_bull_pennant_df(confirmed=False), is_bear=True) is None


class TestPhaseLabel:
    def test_bull_forming(self):
        assert phase_label("bull_pennant", "FORMING") == "Bull Pennant (Forming)"

    def test_bear_confirmed(self):
        assert phase_label("bear_pennant", "CONFIRMED") == "Bear Pennant (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "pennant"
        assert PATTERN_TYPES == {"bull_pennant": "up", "bear_pennant": "down"}

    def test_detect_returns_generic_shape(self):
        df = _make_bull_pennant_df(confirmed=True)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {"PENNANT": {
                "MIN_CONSOLIDATION_DAYS": 16, "MAX_CONSOLIDATION_DAYS": 16, "MIN_SLOPE_PCT": 0.05,
            }}},
        }
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert [p["label"] for p in result["points"][:2]] == ["Pole Start", "Pole End"]
        assert len(result["lines"]) == 2
        assert {l["label"] for l in result["lines"]} == {"Resistance (Falling)", "Support (Rising)"}

    def test_detect_respects_family_toggles(self):
        df = _make_bull_pennant_df(confirmed=False)
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"PENNANT": {"BULL_ENABLED": False, "BEAR_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
