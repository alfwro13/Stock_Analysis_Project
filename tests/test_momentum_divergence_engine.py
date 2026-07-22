"""
tests/test_momentum_divergence_engine.py — Bullish / Bearish Divergence detection math (the
Pattern Detection "momentum_divergence" family).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from momentum_divergence_engine import (
    _detect_and_build,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_MIN_PRICE_CHANGE_PCT = 1.0
_MIN_RSI_GAP = 3.0
_VOLUME_CONFIRM_MULTIPLIER = 1.5


def _make_bearish_divergence_df(confirmed: bool = False) -> pd.DataFrame:
    """peak1(35, 110, sharp rally in) -> trough(45, 95) -> peak2(85, 116, a genuinely higher
    high reached via a much gentler 40-bar grind, so RSI comes in lower than at peak1 despite
    price being higher) -> pull-back, optional breakdown below the 95 support level."""
    seg1 = np.linspace(90, 100, 26)
    seg1b = np.linspace(100, 110, 11)[1:]
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 116, 41)[1:]
    seg4 = np.linspace(116, 108, 6)[1:]
    parts = [seg1, seg1b, seg2, seg3, seg4]
    if confirmed:
        parts.append(np.linspace(108, 90, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_bullish_divergence_df(confirmed: bool = False) -> pd.DataFrame:
    """trough1(35, 90, sharp decline in) -> peak(45, 105) -> trough2(85, 84, a genuinely lower
    low reached via a much gentler 40-bar grind, so RSI comes in higher than at trough1 despite
    price being lower) -> bounce, optional breakout above the 105 resistance level."""
    seg1 = np.linspace(110, 100, 26)
    seg1b = np.linspace(100, 90, 11)[1:]
    seg2 = np.linspace(90, 105, 11)[1:]
    seg3 = np.linspace(105, 84, 41)[1:]
    seg4 = np.linspace(84, 92, 6)[1:]
    parts = [seg1, seg1b, seg2, seg3, seg4]
    if confirmed:
        parts.append(np.linspace(92, 112, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_no_new_extreme_top_df() -> pd.DataFrame:
    """Second peak barely (below the min-price-change gate) exceeds the first — must be
    rejected as not a genuinely new extreme."""
    seg1 = np.linspace(90, 100, 26)
    seg1b = np.linspace(100, 110, 11)[1:]
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 110.2, 41)[1:]
    seg4 = np.linspace(110.2, 105, 6)[1:]
    prices = np.concatenate([seg1, seg1b, seg2, seg3, seg4])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _with_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.date_range("2024-01-01", periods=len(df), freq="B")
    return df


def _detect(df: pd.DataFrame, is_bottom: bool):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return _detect_and_build(close, volume, rsi_series, vol_sma, is_bottom, _MIN_PRICE_CHANGE_PCT, _MIN_RSI_GAP, _VOLUME_CONFIRM_MULTIPLIER)


class TestDetectAndBuildBearish:
    def test_forming_phase_above_support(self):
        result = _detect(_make_bearish_divergence_df(confirmed=False), is_bottom=False)
        assert result is not None
        assert result["pattern_type"] == "bearish_divergence"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_below_support(self):
        result = _detect(_make_bearish_divergence_df(confirmed=True), is_bottom=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None

    def test_measured_target_below_support(self):
        result = _detect(_make_bearish_divergence_df(confirmed=True), is_bottom=False)
        assert result["measured_target"] < result["neck_value"]

    def test_second_peak_is_higher_high(self):
        result = _detect(_make_bearish_divergence_df(confirmed=False), is_bottom=False)
        assert result["ext2_price"] > result["ext1_price"]

    def test_rejects_when_not_a_new_extreme(self):
        assert _detect(_make_no_new_extreme_top_df(), is_bottom=False) is None

    def test_rsi_divergence_always_true(self):
        result = _detect(_make_bearish_divergence_df(confirmed=False), is_bottom=False)
        assert result["rsi_divergence"] is True

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_bearish_divergence_df(confirmed=False), is_bottom=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildBullish:
    def test_forming_phase_below_resistance(self):
        result = _detect(_make_bullish_divergence_df(confirmed=False), is_bottom=True)
        assert result is not None
        assert result["pattern_type"] == "bullish_divergence"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_above_resistance(self):
        result = _detect(_make_bullish_divergence_df(confirmed=True), is_bottom=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_above_resistance(self):
        result = _detect(_make_bullish_divergence_df(confirmed=True), is_bottom=True)
        assert result["measured_target"] > result["neck_value"]

    def test_second_trough_is_lower_low(self):
        result = _detect(_make_bullish_divergence_df(confirmed=False), is_bottom=True)
        assert result["ext2_price"] < result["ext1_price"]

    def test_top_shape_not_detected_as_bottom(self):
        assert _detect(_make_bearish_divergence_df(confirmed=False), is_bottom=True) is None


class TestPhaseLabel:
    def test_bearish_forming(self):
        assert phase_label("bearish_divergence", "FORMING") == "Bearish Divergence (Forming)"

    def test_bullish_confirmed(self):
        assert phase_label("bullish_divergence", "CONFIRMED") == "Bullish Divergence (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "momentum_divergence"
        assert PATTERN_TYPES == {"bearish_divergence": "down", "bullish_divergence": "up"}

    def test_detect_returns_generic_shape(self):
        df = _with_datetime_index(_make_bearish_divergence_df(confirmed=True))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert len(result["points"]) == 3
        assert len(result["lines"]) == 1
        assert result["lines"][0]["label"] == "Support"

    def test_detect_respects_family_toggles(self):
        df = _with_datetime_index(_make_bearish_divergence_df(confirmed=False))
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"MOMENTUM_DIVERGENCE": {"BEARISH_ENABLED": False, "BULLISH_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None
