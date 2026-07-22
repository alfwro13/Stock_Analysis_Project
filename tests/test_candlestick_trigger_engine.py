"""
tests/test_candlestick_trigger_engine.py — Micro-Structure Candlestick Trigger detection math
(the Pattern Detection "candlestick_trigger" family: Bullish/Bearish Engulfing, Hammer/Shooting
Star Pin Bars). Orchestration (ticker scans, DB save/dedup, scheduler wiring, chart API) is
generic across every family and is covered by tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from candlestick_trigger_engine import (
    detect,
    phase_label,
    FAMILY,
    PATTERN_TYPES,
)

_CONFIG = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}
_N = 70


def _base_downtrend_df() -> pd.DataFrame:
    """70 bars trending from 100 to 80 — deep enough that the final bar's RSI/Bollinger Band
    context gate is satisfied for a bullish trigger by construction."""
    close = np.linspace(100.0, 80.0, _N)
    open_ = close + 0.3
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    volume = np.full(_N, 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=_N, freq="B")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def _base_uptrend_df() -> pd.DataFrame:
    """Mirror of _base_downtrend_df for bearish triggers (satisfies the overbought/upper-band
    context gate on the final bar by construction)."""
    close = np.linspace(80.0, 100.0, _N)
    open_ = close - 0.3
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    volume = np.full(_N, 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=_N, freq="B")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def _make_bullish_engulfing_df() -> pd.DataFrame:
    df = _base_downtrend_df()
    df.loc[df.index[-2], ["Open", "Close", "High", "Low"]] = [80.6, 80.0, 80.8, 79.8]
    df.loc[df.index[-1], ["Open", "Close", "High", "Low"]] = [79.9, 80.9, 81.0, 79.7]
    df.loc[df.index[-1], "Volume"] = 2_500_000.0
    return df


def _make_bearish_engulfing_df() -> pd.DataFrame:
    df = _base_uptrend_df()
    df.loc[df.index[-2], ["Open", "Close", "High", "Low"]] = [99.4, 100.0, 100.2, 99.2]
    df.loc[df.index[-1], ["Open", "Close", "High", "Low"]] = [100.1, 99.1, 100.3, 99.0]
    df.loc[df.index[-1], "Volume"] = 2_500_000.0
    return df


def _make_hammer_df() -> pd.DataFrame:
    df = _base_downtrend_df()
    df.loc[df.index[-1], ["Open", "Close", "High", "Low"]] = [79.8, 80.0, 80.05, 78.0]
    df.loc[df.index[-1], "Volume"] = 2_000_000.0
    return df


def _make_shooting_star_df() -> pd.DataFrame:
    df = _base_uptrend_df()
    df.loc[df.index[-1], ["Open", "Close", "High", "Low"]] = [100.2, 100.0, 102.0, 99.95]
    df.loc[df.index[-1], "Volume"] = 2_000_000.0
    return df


def _make_no_context_hammer_df() -> pd.DataFrame:
    """Same hammer geometry, but the preceding bars are flat (not a downtrend), so the RSI/
    Bollinger context gate should not be satisfied."""
    close = np.full(_N, 100.0)
    open_ = close.copy()
    high = close + 0.3
    low = close - 0.3
    volume = np.full(_N, 1_000_000.0)
    idx = pd.date_range("2024-01-01", periods=_N, freq="B")
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)
    df.loc[df.index[-1], ["Open", "Close", "High", "Low"]] = [99.9, 100.0, 100.05, 98.0]
    return df


def _detect(df: pd.DataFrame, config: dict = _CONFIG) -> dict:
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return detect("FAKE", df, rsi_series, vol_sma, config)


class TestBullishEngulfing:
    def test_detected_with_context_gate_satisfied(self):
        result = _detect(_make_bullish_engulfing_df())
        assert result is not None
        assert result["pattern_type"] == "bullish_engulfing"
        assert result["phase"] == "CONFIRMED"
        assert result["measured_target"] > result["close_price"]
        assert result["rsi_divergence"] is True

    def test_missing_directional_requirement_is_not_flagged(self):
        """Body-containment inequalities alone (curr_open < prev_close, curr_close > prev_open)
        can hold even when the prior candle isn't bearish or the current isn't bullish — this
        must not be flagged as an engulfing pattern."""
        df = _make_bullish_engulfing_df()
        # Flip the prior bar to bullish (Close > Open) while keeping the raw containment
        # inequalities true against today's candle.
        df.loc[df.index[-2], ["Open", "Close"]] = [79.9, 80.6]
        result = _detect(df)
        assert result is None or result["pattern_type"] != "bullish_engulfing"


class TestBearishEngulfing:
    def test_detected_with_context_gate_satisfied(self):
        result = _detect(_make_bearish_engulfing_df())
        assert result is not None
        assert result["pattern_type"] == "bearish_engulfing"
        assert result["phase"] == "CONFIRMED"
        assert result["measured_target"] < result["close_price"]


class TestHammer:
    def test_detected_with_context_gate_satisfied(self):
        result = _detect(_make_hammer_df())
        assert result is not None
        assert result["pattern_type"] == "hammer"
        assert result["phase"] == "CONFIRMED"
        assert result["key_level"] < result["close_price"]
        assert result["measured_target"] > result["close_price"]

    def test_rejected_without_context_gate(self):
        result = _detect(_make_no_context_hammer_df())
        assert result is None


class TestShootingStar:
    def test_detected_with_context_gate_satisfied(self):
        result = _detect(_make_shooting_star_df())
        assert result is not None
        assert result["pattern_type"] == "shooting_star"
        assert result["phase"] == "CONFIRMED"
        assert result["key_level"] > result["close_price"]
        assert result["measured_target"] < result["close_price"]


class TestSelfExpiring:
    def test_no_longer_detected_once_bar_is_no_longer_latest(self):
        """The whole family is single-bar: appending one more ordinary bar after the trigger
        must make detect() return None again (or a different pattern), proving these patterns
        can't linger as 'active' past the day they fired."""
        df = _make_bullish_engulfing_df()
        result_today = _detect(df)
        assert result_today is not None

        extra = df.iloc[[-1]].copy()
        extra.index = [df.index[-1] + pd.Timedelta(days=1)]
        extra.loc[:, ["Open", "Close", "High", "Low"]] = [80.9, 80.85, 81.0, 80.8]
        df_extended = pd.concat([df, extra])
        result_next_day = _detect(df_extended)
        assert result_next_day is None or result_next_day["pattern_type"] != "bullish_engulfing"


class TestFamilyToggles:
    def test_engulfing_disabled_suppresses_result(self):
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"CANDLESTICK_TRIGGER": {"ENGULFING_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert _detect(_make_bullish_engulfing_df(), config) is None

    def test_pin_bar_disabled_suppresses_result(self):
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"CANDLESTICK_TRIGGER": {"PIN_BAR_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert _detect(_make_hammer_df(), config) is None

    def test_bearish_disabled_suppresses_bearish_engulfing(self):
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"CANDLESTICK_TRIGGER": {"BEARISH_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert _detect(_make_bearish_engulfing_df(), config) is None

    def test_custom_rsi_threshold_applied(self):
        """The default bullish engulfing fixture qualifies via the RSI half of the context gate
        (close sits above the lower Bollinger Band, so BB alone wouldn't qualify it) — an
        unreachable oversold threshold must suppress the result entirely."""
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {"CANDLESTICK_TRIGGER": {"RSI_OVERSOLD": 0.0}}},
        }
        assert _detect(_make_bullish_engulfing_df(), config) is None


class TestDetectRejects:
    def test_too_few_bars(self):
        df = _make_bullish_engulfing_df().iloc[:50]
        assert _detect(df) is None

    def test_no_pattern_no_result(self):
        assert _detect(_base_downtrend_df()) is None


class TestPhaseLabel:
    def test_confirmed_bullish(self):
        assert phase_label("bullish_engulfing", "CONFIRMED") == "Bullish Engulfing (Confirmed)"

    def test_confirmed_bearish_pin_bar(self):
        assert phase_label("shooting_star", "CONFIRMED") == "Shooting Star (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "CONFIRMED") == "CONFIRMED"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "candlestick_trigger"
        assert PATTERN_TYPES == {
            "bullish_engulfing": "up", "bearish_engulfing": "down",
            "hammer": "up", "shooting_star": "down",
        }

    def test_detect_returns_generic_shape(self):
        result = _detect(_make_bullish_engulfing_df())
        assert len(result["points"]) == 4
        assert len(result["lines"]) == 1
        assert result["pattern_r2"] is None
