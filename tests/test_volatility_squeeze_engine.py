"""
tests/test_volatility_squeeze_engine.py — Volatility Squeeze detection math (the Pattern
Detection "volatility_squeeze" family). Orchestration (ticker scans, DB save/dedup, scheduler
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
from volatility_squeeze_engine import (
    _find_latest_squeeze_run,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_CONFIG = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}


def _make_squeeze_df(mode: str, seed: int = 2) -> pd.DataFrame:
    """40 bars of ordinary volatility, then a 35-bar tight/flat consolidation that reliably
    trips the Bollinger-inside-Keltner squeeze condition, then a 3-bar tail: `mode` selects
    whether the squeeze is still on ('forming'), has broken up ('bull'), broken down ('bear'),
    or (mode='none') the price never compresses at all."""
    rng = np.random.RandomState(seed)
    if mode == "none":
        prices = 100.0 + np.cumsum(rng.normal(0, 1.0, 80))
    else:
        pre = 100.0 + np.cumsum(rng.normal(0, 1.0, 40))
        tight = pre[-1] + rng.normal(0, 0.03, 35)
        if mode == "bull":
            tail = tight[-1] + np.array([1.0, 2.5, 5.0])
        elif mode == "bear":
            tail = tight[-1] - np.array([1.0, 2.5, 5.0])
        elif mode == "forming":
            tail = tight[-1] + rng.normal(0, 0.03, 3)
        else:
            raise ValueError(mode)
        prices = np.concatenate([pre, tight, tail])

    volume = np.full(len(prices), 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.05, "Low": prices - 0.05,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _detect(df: pd.DataFrame) -> dict:
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return detect("FAKE", df, rsi_series, vol_sma, _CONFIG)


class TestFindLatestSqueezeRun:
    def test_no_run_returns_none(self):
        assert _find_latest_squeeze_run(np.array([False, False, False]), 90) is None

    def test_finds_trailing_run(self):
        squeeze_on = np.array([False, True, True, True, False, False])
        assert _find_latest_squeeze_run(squeeze_on, 90) == (1, 3)

    def test_run_ending_at_last_bar(self):
        squeeze_on = np.array([False, False, True, True])
        assert _find_latest_squeeze_run(squeeze_on, 90) == (2, 3)


class TestDetectForming:
    def test_forming_when_still_squeezed(self):
        result = _detect(_make_squeeze_df("forming"))
        assert result is not None
        assert result["pattern_type"] == "volatility_squeeze"
        assert result["phase"] == "FORMING"
        assert result["breakout_date"] is None
        assert PATTERN_TYPES.get("volatility_squeeze") is None


class TestDetectConfirmed:
    def test_bullish_breakout(self):
        result = _detect(_make_squeeze_df("bull"))
        assert result is not None
        assert result["pattern_type"] == "volatility_squeeze_bullish"
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_date"] is not None
        assert result["measured_target"] > result["breakout_price"]

    def test_bearish_breakout(self):
        result = _detect(_make_squeeze_df("bear"))
        assert result is not None
        assert result["pattern_type"] == "volatility_squeeze_bearish"
        assert result["phase"] == "CONFIRMED"
        assert result["measured_target"] < result["breakout_price"]

    def test_pattern_r2_is_none(self):
        result = _detect(_make_squeeze_df("bull"))
        assert result["pattern_r2"] is None


class TestDetectRejects:
    def test_no_squeeze_no_result(self):
        assert _detect(_make_squeeze_df("none")) is None

    def test_too_few_bars(self):
        df = _make_squeeze_df("forming").iloc[:50]
        assert _detect(df) is None


class TestFamilyToggles:
    def test_bullish_disabled_suppresses_bull_breakout(self):
        df = _make_squeeze_df("bull")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"VOLATILITY_SQUEEZE": {"BULLISH_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None

    def test_bearish_disabled_suppresses_bear_breakout(self):
        df = _make_squeeze_df("bear")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"VOLATILITY_SQUEEZE": {"BEARISH_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None


class TestPhaseLabel:
    def test_forming(self):
        assert phase_label("volatility_squeeze", "FORMING") == "Volatility Squeeze (Forming)"

    def test_confirmed_bullish(self):
        assert phase_label("volatility_squeeze_bullish", "CONFIRMED") == "Volatility Squeeze (Bullish) (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "volatility_squeeze"
        assert PATTERN_TYPES == {"volatility_squeeze_bullish": "up", "volatility_squeeze_bearish": "down"}

    def test_lines_carry_path_not_straight_segment(self):
        result = _detect(_make_squeeze_df("bull"))
        assert len(result["lines"]) == 2
        for line in result["lines"]:
            assert "path" in line
            assert len(line["path"]) >= 6
        assert len(result["points"]) == 4
