"""
tests/test_candlestick_patterns.py

Unit tests for get_candlestick_patterns() in quant_signals.py.
All tests use hand-crafted OHLC candles with known anatomy so the
expected result is deterministic without any network or DB access.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pandas as pd

import numpy as np

from quant_signals import get_candlestick_patterns, QuantEngine


def _candle(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"Open": open_, "High": high, "Low": low, "Close": close})


def _names(patterns):
    return [p["name"] for p in patterns]


class TestGetCandlestickPatterns:

    # ------------------------------------------------------------------
    # Return-structure contract
    # ------------------------------------------------------------------

    def test_return_dict_has_required_keys(self):
        prev2 = _candle(100, 105, 95, 98)   # bearish
        prev1 = _candle(97, 99, 95, 96)     # indecision
        curr  = _candle(96, 108, 95, 107)   # strong bullish
        result = get_candlestick_patterns(prev2, prev1, curr)
        assert len(result) > 0
        for p in result:
            assert set(p.keys()) == {"name", "tooltip", "breakdown", "score"}
            assert isinstance(p["score"], int)
            assert isinstance(p["name"], str)

    def test_flat_candles_produce_only_doji(self):
        # Candles with near-zero body and range should only trigger Doji (if anything).
        flat = _candle(100.0, 100.05, 99.95, 100.01)
        result = get_candlestick_patterns(flat, flat, flat)
        names = _names(result)
        # No patterns that require meaningful range/body should fire.
        assert "🪖 Three White Soldiers" not in names
        assert "🐂 Bullish Engulfing" not in names
        assert "🐻 Bearish Engulfing" not in names
        assert "🌅 Morning Star" not in names

    # ------------------------------------------------------------------
    # Regression: existing 6 patterns still fire correctly
    # ------------------------------------------------------------------

    def test_morning_star_still_fires(self):
        prev2 = _candle(110, 112, 95, 97)   # strong bearish, body=13, range=17
        prev1 = _candle(96, 98, 94, 95)     # small body (indecision)
        curr  = _candle(95, 115, 94, 112)   # strong bullish, closes > prev2 midpoint (103.5)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🌅 Morning Star" in names

    def test_evening_star_still_fires(self):
        prev2 = _candle(95, 115, 94, 112)   # strong bullish
        prev1 = _candle(112, 114, 110, 111) # small body
        curr  = _candle(111, 112, 94, 97)   # strong bearish, closes < prev2 midpoint (103.5)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🌇 Evening Star" in names

    def test_bullish_engulfing_still_fires(self):
        prev2 = _candle(100, 105, 98, 101)  # neutral
        prev1 = _candle(104, 105, 98, 99)   # bearish
        curr  = _candle(98, 110, 97, 106)   # bullish, engulfs prev1
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🐂 Bullish Engulfing" in names

    def test_bearish_engulfing_still_fires(self):
        prev2 = _candle(100, 105, 98, 101)  # neutral
        prev1 = _candle(99, 106, 98, 105)   # bullish
        curr  = _candle(106, 107, 97, 98)   # bearish, engulfs prev1
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🐻 Bearish Engulfing" in names

    def test_hammer_still_fires(self):
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(100, 102, 98, 101)
        # Long lower wick, tiny body, tiny upper wick
        curr  = _candle(100, 101, 80, 100)  # body=0, lower_wick=20, upper_wick=1
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🔨 Hammer Rejection" in names

    def test_shooting_star_still_fires(self):
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(100, 102, 98, 101)
        # Long upper wick, tiny body, tiny lower wick
        curr  = _candle(100, 120, 99, 100)  # body=0, upper_wick=20, lower_wick=1
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🌠 Shooting Star" in names

    def test_doji_standalone_still_fires(self):
        # Prior candles are not large — so no Harami Cross fires; standalone Doji should.
        prev2 = _candle(100, 103, 97, 101)
        prev1 = _candle(101, 104, 98, 102)
        curr  = _candle(100, 105, 95, 100)  # body=0, range=10 → doji
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "⚖️ Doji" in names

    # ------------------------------------------------------------------
    # Three White Soldiers
    # ------------------------------------------------------------------

    def test_three_white_soldiers_detected(self):
        # Each candle: bullish, body > 40% range, opens inside prior body, closes higher.
        prev2 = _candle(100, 112, 99, 110)   # body=10, range=13, open inside is N/A for D1
        prev1 = _candle(104, 118, 103, 116)  # opens inside prev2 body (100-110), closes higher
        curr  = _candle(110, 126, 109, 124)  # opens inside prev1 body (104-116), closes higher
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🪖 Three White Soldiers" in names

    def test_three_white_soldiers_score_is_18(self):
        prev2 = _candle(100, 112, 99, 110)
        prev1 = _candle(104, 118, 103, 116)
        curr  = _candle(110, 126, 109, 124)
        result = get_candlestick_patterns(prev2, prev1, curr)
        scores = {p["name"]: p["score"] for p in result}
        assert scores["🪖 Three White Soldiers"] == 18

    def test_three_white_soldiers_misses_when_open_outside_prior_body(self):
        # D2 opens ABOVE D1's close (gap up) — violates "opens inside prior body".
        prev2 = _candle(100, 112, 99, 110)
        prev1 = _candle(115, 128, 114, 126)  # opens at 115, above prev2 close of 110
        curr  = _candle(120, 134, 119, 132)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🪖 Three White Soldiers" not in names

    def test_three_white_soldiers_misses_when_candle_is_bearish(self):
        prev2 = _candle(100, 112, 99, 110)
        prev1 = _candle(104, 118, 103, 116)
        curr  = _candle(112, 115, 100, 102)  # bearish close
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🪖 Three White Soldiers" not in names

    # ------------------------------------------------------------------
    # Bullish Harami Cross
    # ------------------------------------------------------------------

    def test_bullish_harami_cross_detected(self):
        prev2 = _candle(100, 102, 98, 101)   # neutral
        prev1 = _candle(110, 112, 88, 90)    # strong bearish: body=20, range=24 (>50%)
        # Doji inside prev1 body (90–110): open=100, close=100.5, body=0.5, range=11 → body/range ≈ 4.5%
        curr  = _candle(100, 105, 94, 100.5)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🌱 Bullish Harami Cross" in names

    def test_bullish_harami_cross_score_is_8(self):
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(110, 112, 88, 90)
        curr  = _candle(100, 105, 94, 100.5)
        result = get_candlestick_patterns(prev2, prev1, curr)
        scores = {p["name"]: p["score"] for p in result}
        assert scores["🌱 Bullish Harami Cross"] == 8

    def test_bullish_harami_cross_suppresses_standalone_doji(self):
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(110, 112, 88, 90)
        curr  = _candle(100, 105, 94, 100.5)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "⚖️ Doji" not in names
        assert "🌱 Bullish Harami Cross" in names

    # ------------------------------------------------------------------
    # Bearish Harami Cross
    # ------------------------------------------------------------------

    def test_bearish_harami_cross_detected(self):
        prev2 = _candle(100, 102, 98, 101)   # neutral
        prev1 = _candle(90, 112, 88, 110)    # strong bullish: body=20, range=24 (>50%)
        # Doji inside prev1 body (90–110): open=100, close=100.5, body=0.5, range=11
        curr  = _candle(100, 105, 94, 100.5)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🕸️ Bearish Harami Cross" in names

    def test_bearish_harami_cross_suppresses_standalone_doji(self):
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(90, 112, 88, 110)
        curr  = _candle(100, 105, 94, 100.5)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "⚖️ Doji" not in names
        assert "🕸️ Bearish Harami Cross" in names

    # ------------------------------------------------------------------
    # Piercing Line
    # ------------------------------------------------------------------

    def test_piercing_line_detected(self):
        prev2 = _candle(100, 102, 98, 101)   # neutral
        # Strong bearish D1: open=110, close=90, body=20, range=22 (>50%)
        prev1 = _candle(110, 112, 88, 90)
        # Bullish D2: opens at 88 (< D1 close 90), closes at 102 (> midpoint 100, < D1 open 110)
        curr  = _candle(88, 115, 86, 102)
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🗡️ Piercing Line" in names

    def test_piercing_line_score_is_10(self):
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(110, 112, 88, 90)
        curr  = _candle(88, 115, 86, 102)
        result = get_candlestick_patterns(prev2, prev1, curr)
        scores = {p["name"]: p["score"] for p in result}
        assert scores["🗡️ Piercing Line"] == 10

    def test_piercing_line_does_not_fire_when_full_engulf(self):
        # D2 closes above D1 open — that's Bullish Engulfing, not Piercing Line.
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(110, 112, 88, 90)
        curr  = _candle(88, 120, 86, 112)   # closes at 112 > prev1 open 110
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🐂 Bullish Engulfing" in names
        assert "🗡️ Piercing Line" not in names

    def test_piercing_line_does_not_fire_below_midpoint(self):
        # D2 closes only at D1's low — does not pierce above midpoint (100).
        prev2 = _candle(100, 102, 98, 101)
        prev1 = _candle(110, 112, 88, 90)
        curr  = _candle(88, 97, 86, 96)     # closes at 96 < midpoint 100
        names = _names(get_candlestick_patterns(prev2, prev1, curr))
        assert "🗡️ Piercing Line" not in names


# ---------------------------------------------------------------------------
# Helpers for VCP / divergence tests
# ---------------------------------------------------------------------------

def _make_flat_ohlcv(n: int = 260, price: float = 100.0, volume: int = 1_000_000) -> pd.DataFrame:
    dates = pd.bdate_range(start="2024-01-01", periods=n)
    return pd.DataFrame(
        {
            "Open": [price] * n,
            "High": [price * 1.002] * n,
            "Low": [price * 0.998] * n,
            "Close": [price] * n,
            "Volume": [volume] * n,
        },
        index=dates,
    )


class TestCalculateVcpBreakout:

    def setup_method(self):
        self.engine = QuantEngine()

    def test_returns_all_false_for_none_input(self):
        assert self.engine.calculate_vcp_breakout(None) == (False, False, False)

    def test_returns_all_false_when_fewer_than_252_rows(self):
        df = _make_flat_ohlcv(n=100)
        assert self.engine.calculate_vcp_breakout(df) == (False, False, False)

    def test_returns_false_false_false_for_flat_price(self):
        # Flat price: 52W high ≈ 52W low → prior advance ≈ 0 < 0.30.
        df = _make_flat_ohlcv(n=260)
        vcp_base, breakout, uptrend = self.engine.calculate_vcp_breakout(df)
        assert uptrend is False
        assert vcp_base is False
        assert breakout is False

    def test_prior_uptrend_flag_set_when_price_ran_30pct(self):
        # 260-day climb from 100 to 140 (+40%), then drops to 90 (far from 52W high → is_near_high=False).
        n = 260
        dates = pd.bdate_range(start="2024-01-01", periods=n)
        closes = [100 + 40 * i / 200 if i < 200 else 90.0 for i in range(n)]
        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c * 1.002 for c in closes],
                "Low": [c * 0.998 for c in closes],
                "Close": closes,
                "Volume": [1_000_000] * n,
            },
            index=dates,
        )
        _vcp_base, breakout, uptrend = self.engine.calculate_vcp_breakout(df)
        assert uptrend is True
        assert breakout is False

    def test_near_high_flag_false_when_price_far_below_52w_high(self):
        # After the uptrend the price falls 30% below the 52W high — is_near_high must be False.
        n = 260
        dates = pd.bdate_range(start="2024-01-01", periods=n)
        # high_52w ≈ 140, final price = 90  →  dist = (140-90)/140 ≈ 35.7% > 15%
        closes = [100 + 40 * i / 200 if i < 200 else 90.0 for i in range(n)]
        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c * 1.002 for c in closes],
                "Low": [c * 0.998 for c in closes],
                "Close": closes,
                "Volume": [1_000_000] * n,
            },
            index=dates,
        )
        vcp_base, _breakout, _uptrend = self.engine.calculate_vcp_breakout(df)
        assert vcp_base is False


class TestDetectBearishDivergence:

    def setup_method(self):
        self.engine = QuantEngine()

    def test_returns_false_when_rsi_column_missing(self):
        df = _make_flat_ohlcv(n=30)
        assert self.engine.detect_bearish_divergence(df) is False

    def test_returns_false_when_fewer_than_30_rows(self):
        df = _make_flat_ohlcv(n=20)
        df["RSI"] = 55.0
        assert self.engine.detect_bearish_divergence(df) is False

    def test_returns_true_on_classic_bearish_divergence(self):
        # Price: higher high (peak2=116 > peak1=111).
        # RSI:   lower high at peak2 (60 < 70) with first peak RSI > 55.
        n = 30
        dates = pd.bdate_range(start="2024-01-01", periods=n)
        closes = [100.0] * 14 + [110.0] + [105.0] * 8 + [115.0] + [112.0] * 6
        rsi_vals = (
            [50.0] * 12 + [65.0, 68.0, 70.0]  # peak1 at iloc=14; window max=70
            + [55.0] * 6 + [55.0, 58.0, 60.0]  # peak2 at iloc=23; window max=60
            + [55.0] * 6
        )
        df = pd.DataFrame(
            {
                "High": [c + 1.0 for c in closes],
                "Low": [c - 1.0 for c in closes],
                "Close": closes,
                "RSI": rsi_vals,
            },
            index=dates,
        )
        assert self.engine.detect_bearish_divergence(df) is True

    def test_returns_false_when_first_rsi_peak_below_55(self):
        # Same price structure but RSI peak1 = 50 (baseline not bullish enough).
        n = 30
        dates = pd.bdate_range(start="2024-01-01", periods=n)
        closes = [100.0] * 14 + [110.0] + [105.0] * 8 + [115.0] + [112.0] * 6
        rsi_vals = (
            [40.0] * 12 + [45.0, 48.0, 50.0]   # peak1 RSI window max = 50 < 55
            + [40.0] * 6 + [40.0, 43.0, 45.0]
            + [40.0] * 6
        )
        df = pd.DataFrame(
            {
                "High": [c + 1.0 for c in closes],
                "Low": [c - 1.0 for c in closes],
                "Close": closes,
                "RSI": rsi_vals,
            },
            index=dates,
        )
        assert self.engine.detect_bearish_divergence(df) is False

    def test_returns_false_when_no_higher_price_high(self):
        # Price peak2 (110) < price peak1 (115) — not a higher high, so no divergence.
        n = 30
        dates = pd.bdate_range(start="2024-01-01", periods=n)
        closes = [100.0] * 14 + [115.0] + [105.0] * 8 + [110.0] + [108.0] * 6
        rsi_vals = (
            [50.0] * 12 + [65.0, 68.0, 70.0]
            + [55.0] * 6 + [55.0, 58.0, 60.0]
            + [55.0] * 6
        )
        df = pd.DataFrame(
            {
                "High": [c + 1.0 for c in closes],
                "Low": [c - 1.0 for c in closes],
                "Close": closes,
                "RSI": rsi_vals,
            },
            index=dates,
        )
        assert self.engine.detect_bearish_divergence(df) is False
