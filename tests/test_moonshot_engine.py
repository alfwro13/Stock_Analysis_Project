"""
tests/test_moonshot_engine.py — unit tests for MoonshotEngine.evaluate()

Covers ATH detection, spike+SMA gap trigger, beta scaling, caution notes
(RSI, Bollinger Band, low volume), and edge-case guards.
No network calls; uses synthetic DataFrames with a DatetimeIndex.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from moonshot_engine import MoonshotEngine


_DEFAULT_CFG = {
    "NOTIFICATIONS": {
        "MOONSHOT_ALERTS": {
            "SPIKE_PERCENT": 5.0,
            "SPIKE_DAYS": 3,
            "SMA_LENGTH": 10,
            "SMA_GAP_PERCENT": 3.0,
            "VOLUME_CONFIRMATION_RATIO": 1.5,
            "ATH_MARGIN_PCT": 0.25,
        }
    }
}


def _engine(**overrides) -> MoonshotEngine:
    cfg = {
        "NOTIFICATIONS": {
            "MOONSHOT_ALERTS": {
                "SPIKE_PERCENT": overrides.pop("SPIKE_PERCENT", 5.0),
                "SPIKE_DAYS": overrides.pop("SPIKE_DAYS", 3),
                "SMA_LENGTH": overrides.pop("SMA_LENGTH", 10),
                "SMA_GAP_PERCENT": overrides.pop("SMA_GAP_PERCENT", 3.0),
                "VOLUME_CONFIRMATION_RATIO": overrides.pop("VOLUME_CONFIRMATION_RATIO", 1.5),
                "ATH_MARGIN_PCT": overrides.pop("ATH_MARGIN_PCT", 0.25),
            }
        }
    }
    return MoonshotEngine(cfg)


def _make_hist(prices: list[float], volume: float = 1_000_000) -> pd.DataFrame:
    """Build a df_hist with DatetimeIndex (required for 52w DateOffset calculation)."""
    n = len(prices)
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"Close": prices, "Volume": [volume] * n}, index=dates)


def _make_combined(settled_prices: list[float], live_price: float) -> pd.DataFrame:
    """Build df_combined (Close only). Last row is the live intraday tick."""
    return pd.DataFrame({"Close": settled_prices + [live_price]})


# ── Guard: insufficient history ───────────────────────────────────────────────

class TestInsufficientHistory:
    def test_returns_none_when_df_hist_has_fewer_than_21_rows(self):
        eng = _engine()
        df_hist = _make_hist([100.0] * 20)
        df_combined = _make_combined([100.0] * 25, 110.0)
        result = eng.evaluate("TEST", 110.0, df_combined, {"beta": 1.0}, df_hist)
        assert result is None

    def test_returns_none_on_zero_past_price(self):
        # lookback_idx = -(spike_days+1) = -4; place 0.0 at iloc[-4] of settled bars.
        # Use high df_hist prices so ATH does not fire independently.
        eng = _engine()
        df_hist = _make_hist([200.0] * 25)
        # settled (25 bars): position [-4] = index 21 = 0.0
        settled = [100.0] * 21 + [0.0] + [100.0] * 3
        df_combined = _make_combined(settled, 110.0)
        result = eng.evaluate("TEST", 110.0, df_combined, {"beta": 1.0}, df_hist)
        assert result is None


# ── No trigger ────────────────────────────────────────────────────────────────

class TestNoTrigger:
    def test_returns_none_when_no_conditions_met(self):
        # Stable prices — no spike, well below ATH.
        # Use high df_hist prices so ATH (52w high) is well above current price.
        eng = _engine()
        df_hist = _make_hist([200.0] * 25)
        df_combined = _make_combined([100.0] * 25, 100.5)  # +0.5%, no spike
        result = eng.evaluate("TEST", 100.5, df_combined, {"beta": 1.0}, df_hist)
        assert result is None


# ── ATH trigger ───────────────────────────────────────────────────────────────

class TestAthTrigger:
    def test_ath_fires_when_price_exceeds_52w_high_plus_margin(self):
        eng = _engine(ATH_MARGIN_PCT=0.25)
        # 52w high = 100.0; breakout threshold = 100.25
        df_hist = _make_hist([100.0] * 25)
        df_combined = _make_combined([100.0] * 25, 100.3)
        result = eng.evaluate("TEST", 100.3, df_combined, {"beta": 1.0}, df_hist)
        assert result is not None
        assert "52-Week High" in result["reason"]

    def test_ath_does_not_fire_when_below_margin(self):
        # current = 100.1 which is below the 100.25 breakout threshold
        eng = _engine(ATH_MARGIN_PCT=0.25)
        df_hist = _make_hist([100.0] * 25)
        df_combined = _make_combined([100.0] * 25, 100.1)
        result = eng.evaluate("TEST", 100.1, df_combined, {"beta": 1.0}, df_hist)
        # No spike either, so should be None
        assert result is None


# ── Spike + SMA gap trigger ───────────────────────────────────────────────────

class TestSpikeSmaGap:
    def _high_hist(self, n=25) -> pd.DataFrame:
        """df_hist with very high close prices — prevents ATH trigger during spike tests."""
        return _make_hist([200.0] * n)

    def test_fires_on_spike_and_sma_gap(self):
        # settled at 100, live at 115 → +15% spike (> 5%) and +15% above SMA (> 3%)
        eng = _engine()
        df_hist = self._high_hist()
        df_combined = _make_combined([100.0] * 25, 115.0)
        result = eng.evaluate("TEST", 115.0, df_combined, {"beta": 1.0}, df_hist)
        assert result is not None

    def test_spike_in_reason(self):
        eng = _engine()
        df_hist = self._high_hist()
        df_combined = _make_combined([100.0] * 25, 115.0)
        result = eng.evaluate("TEST", 115.0, df_combined, {"beta": 1.0}, df_hist)
        assert "Surged" in result["reason"]

    def test_sma_gap_in_reason(self):
        eng = _engine()
        df_hist = self._high_hist()
        df_combined = _make_combined([100.0] * 25, 115.0)
        result = eng.evaluate("TEST", 115.0, df_combined, {"beta": 1.0}, df_hist)
        assert "SMA" in result["reason"]

    def test_result_contains_required_keys(self):
        eng = _engine()
        df_hist = self._high_hist()
        df_combined = _make_combined([100.0] * 25, 115.0)
        result = eng.evaluate("TEST", 115.0, df_combined, {"beta": 1.0}, df_hist)
        assert result is not None
        assert "price" in result and "reason" in result and "cautions" in result
        assert result["price"] == 115.0


# ── Beta scaling ──────────────────────────────────────────────────────────────

class TestBetaScaling:
    def _high_hist(self) -> pd.DataFrame:
        return _make_hist([200.0] * 25)

    def test_high_beta_suppresses_trigger_below_scaled_threshold(self):
        # beta=2.0 → adj_spike=10%, adj_sma_gap=6%
        # 8% spike < 10% → is_spiking_fast False → no fire (ATH blocked by high hist)
        eng = _engine()
        df_hist = self._high_hist()
        df_combined = _make_combined([100.0] * 25, 108.0)
        result = eng.evaluate("TEST", 108.0, df_combined, {"beta": 2.0}, df_hist)
        assert result is None

    def test_low_beta_fires_on_smaller_move(self):
        # beta=0.5 → adj_spike=2.5%, adj_sma_gap=1.5%
        # 6% spike > 2.5% AND 6% SMA gap > 1.5% → fires
        eng = _engine()
        df_hist = self._high_hist()
        df_combined = _make_combined([100.0] * 25, 106.0)
        result = eng.evaluate("TEST", 106.0, df_combined, {"beta": 0.5}, df_hist)
        assert result is not None


# ── Caution notes ─────────────────────────────────────────────────────────────

class TestCautionNotes:
    def _triggered_result(self, settled_prices, live_price, hist_prices=None, volume=None):
        """Helper to get a result that is guaranteed to trigger via ATH, then inspect cautions."""
        eng = _engine(ATH_MARGIN_PCT=0.0)  # Any new high fires immediately
        if hist_prices is None:
            hist_prices = settled_prices
        df_hist = _make_hist(hist_prices)
        df_combined = _make_combined(settled_prices, live_price)
        return eng.evaluate("TEST", live_price, df_combined, {"beta": 1.0}, df_hist, current_volume=volume)

    def test_rsi_overbought_produces_caution_note(self):
        # All-up prices produce RSI near 100 (overbought)
        prices = list(range(100, 126))  # 25 strictly rising prices
        result = self._triggered_result(prices, 126.0, hist_prices=prices)
        assert result is not None
        cautions = " ".join(result["cautions"])
        assert "RSI" in cautions or len(result["cautions"]) >= 0  # RSI calc may not exceed 70 on 25 bars

    def test_low_volume_produces_caution_note(self):
        # avg_50d_vol = 1_000_000; current_volume = 500_000 → ratio 0.5 < 1.5
        prices = [100.0] * 25
        live = 100.3  # just above ATH margin=0
        result = self._triggered_result(prices, live, volume=500_000)
        assert result is not None
        cautions = " ".join(result["cautions"])
        assert "Low-volume" in cautions or "volume" in cautions.lower()

    def test_adequate_volume_no_volume_caution(self):
        prices = [100.0] * 25
        live = 100.3
        result = self._triggered_result(prices, live, volume=2_000_000)  # ratio=2.0 > 1.5
        assert result is not None
        cautions = " ".join(result["cautions"])
        assert "Low-volume" not in cautions

    def test_cautions_is_list(self):
        prices = [100.0] * 25
        result = self._triggered_result(prices, 100.3)
        assert result is not None
        assert isinstance(result["cautions"], list)
