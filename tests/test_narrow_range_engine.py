"""
tests/test_narrow_range_engine.py — NR4/NR7 Narrow Range detection math (the Pattern Detection
"narrow_range" family). Orchestration (ticker scans, DB save/dedup, scheduler wiring, chart API)
is generic across every family and is covered by tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from narrow_range_engine import (
    _is_narrow_bar,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_CONFIG = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
_N = 70


def _make_narrow_range_df(mode: str, seed: int = 5) -> pd.DataFrame:
    """70 bars of ordinary daily ranges (1.5-3.0), with an NR7-qualifying inside bar inserted
    either at today ('forming') or a few bars back followed by a decisive breakout of its own
    high ('bull') or low ('bear'); 'none' has no compression at all."""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.3, _N))
    ranges = rng.uniform(1.5, 3.0, _N)
    high = close + ranges / 2
    low = close - ranges / 2

    if mode == "none":
        pass
    else:
        narrow_idx = _N - 1 if mode == "forming" else _N - 4
        prev_high, prev_low = high[narrow_idx - 1], low[narrow_idx - 1]
        narrow_close = close[narrow_idx - 1] + 0.05
        narrow_high = min(prev_high - 0.2, narrow_close + 0.1)
        narrow_low = max(prev_low + 0.2, narrow_close - 0.1)
        close[narrow_idx] = narrow_close
        high[narrow_idx] = narrow_high
        low[narrow_idx] = narrow_low

        if mode in ("bull", "bear"):
            mid = (narrow_high + narrow_low) / 2
            for i in range(narrow_idx + 1, _N - 1):
                close[i] = mid
                high[i] = mid + 0.05
                low[i] = mid - 0.05
            if mode == "bull":
                close[_N - 1] = narrow_high + 1.0
                high[_N - 1] = close[_N - 1] + 0.1
                low[_N - 1] = close[_N - 1] - 0.5
            else:
                close[_N - 1] = narrow_low - 1.0
                high[_N - 1] = close[_N - 1] + 0.5
                low[_N - 1] = close[_N - 1] - 0.1

    volume = np.full(_N, 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=_N, freq="B")
    return pd.DataFrame({
        "Open": close, "High": high, "Low": low, "Close": close, "Volume": volume,
    }, index=idx)


def _detect(df: pd.DataFrame) -> dict:
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return detect("FAKE", df, rsi_series, vol_sma, _CONFIG)


class TestIsNarrowBar:
    def test_too_early_in_series_is_false(self):
        tr = np.array([1.0, 1.0, 1.0])
        high = np.array([10.0, 10.0, 10.0])
        low = np.array([9.0, 9.0, 9.0])
        assert _is_narrow_bar(tr, high, low, 1, window=4) is False

    def test_widest_bar_in_window_is_not_narrow(self):
        tr = np.array([1.0, 1.0, 1.0, 5.0])
        high = np.array([10.0, 10.0, 10.0, 12.0])
        low = np.array([9.0, 9.0, 9.0, 6.0])
        assert _is_narrow_bar(tr, high, low, 3, window=4) is False

    def test_narrowest_but_not_inside_bar_is_false(self):
        tr = np.array([5.0, 5.0, 5.0, 1.0])
        high = np.array([10.0, 10.0, 10.0, 11.0])  # breaks above prior high
        low = np.array([1.0, 1.0, 1.0, 2.0])
        assert _is_narrow_bar(tr, high, low, 3, window=4) is False

    def test_narrowest_and_inside_bar_is_true(self):
        tr = np.array([5.0, 5.0, 5.0, 1.0])
        high = np.array([10.0, 10.0, 10.0, 9.5])
        low = np.array([1.0, 1.0, 1.0, 1.5])
        assert _is_narrow_bar(tr, high, low, 3, window=4) is True


class TestDetectForming:
    def test_forming_when_bar_is_today(self):
        result = _detect(_make_narrow_range_df("forming"))
        assert result is not None
        assert result["pattern_type"] == "nr7"
        assert result["phase"] == "FORMING"
        assert result["breakout_date"] is None
        assert PATTERN_TYPES.get("nr7") is None


class TestDetectConfirmed:
    def test_bullish_breakout(self):
        result = _detect(_make_narrow_range_df("bull"))
        assert result is not None
        assert result["pattern_type"] == "nr7_bullish"
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_date"] is not None
        assert result["measured_target"] > result["breakout_price"]

    def test_bearish_breakout(self):
        result = _detect(_make_narrow_range_df("bear"))
        assert result is not None
        assert result["pattern_type"] == "nr7_bearish"
        assert result["phase"] == "CONFIRMED"
        assert result["measured_target"] < result["breakout_price"]

    def test_pattern_r2_is_none(self):
        result = _detect(_make_narrow_range_df("bull"))
        assert result["pattern_r2"] is None


class TestDetectRejects:
    def test_no_compression_no_result(self):
        assert _detect(_make_narrow_range_df("none")) is None

    def test_too_few_bars(self):
        df = _make_narrow_range_df("forming").iloc[:50]
        assert _detect(df) is None


class TestFamilyToggles:
    def test_nr7_disabled_falls_back_to_nr4(self):
        df = _make_narrow_range_df("forming")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"NARROW_RANGE": {"NR7_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        result = detect("FAKE", df, rsi_series, vol_sma, config)
        assert result is not None
        assert result["pattern_type"] == "nr4"

    def test_both_windows_disabled_no_result(self):
        df = _make_narrow_range_df("forming")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"NARROW_RANGE": {"NR4_ENABLED": False, "NR7_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None

    def test_bullish_disabled_suppresses_bull_breakout(self):
        df = _make_narrow_range_df("bull")
        rsi_series = compute_rsi(df["Close"])
        vol_sma = compute_volume_sma(df["Volume"])
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"NARROW_RANGE": {"BULLISH_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert detect("FAKE", df, rsi_series, vol_sma, config) is None


class TestPhaseLabel:
    def test_forming(self):
        assert phase_label("nr7", "FORMING") == "NR7 Narrow Range (Forming)"

    def test_confirmed_bullish(self):
        assert phase_label("nr4_bullish", "CONFIRMED") == "NR4 Breakout (Bullish) (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "narrow_range"
        assert PATTERN_TYPES == {
            "nr4_bullish": "up", "nr4_bearish": "down",
            "nr7_bullish": "up", "nr7_bearish": "down",
        }

    def test_detect_returns_generic_shape(self):
        result = _detect(_make_narrow_range_df("bull"))
        assert len(result["points"]) == 2
        assert len(result["lines"]) == 2
        assert result["lines"][0]["label"] == "Breakout Trigger (High)"
