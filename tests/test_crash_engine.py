"""
tests/test_crash_engine.py — unit tests for crash_engine.CrashEngine.evaluate()

Covers session-crash detection, multi-day trend bleed, ATR floor, beta scaling,
and the AI Volatility Defense cap. No network calls; uses synthetic DataFrames.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from crash_engine import CrashEngine

CFG = {
    "NOTIFICATIONS": {
        "CRASH_ALERTS": {
            "DROP_PERCENT": 5.0,
            "DROP_DAYS": 3,
            "SMA_LENGTH": 5,
            "SMA_GAP_PERCENT": 2.0,
            "SESSION_CRASH_THRESHOLD": 3.0,
        }
    }
}

META_NEUTRAL = {"beta": 1.0, "company_name": "Test Corp"}


def _make_df(prices: list[float]) -> pd.DataFrame:
    """Build a minimal Close-only DataFrame (settled bars + 1 live tick appended last)."""
    return pd.DataFrame({"Close": prices})


def _engine(**overrides) -> CrashEngine:
    cfg = {
        "NOTIFICATIONS": {
            "CRASH_ALERTS": {
                "DROP_PERCENT": overrides.pop("DROP_PERCENT", 5.0),
                "DROP_DAYS": overrides.pop("DROP_DAYS", 3),
                "SMA_LENGTH": overrides.pop("SMA_LENGTH", 5),
                "SMA_GAP_PERCENT": overrides.pop("SMA_GAP_PERCENT", 2.0),
                "SESSION_CRASH_THRESHOLD": overrides.pop("SESSION_CRASH_THRESHOLD", 3.0),
            }
        }
    }
    e = CrashEngine(cfg)
    e.spy_change_pct = 0.0
    return e


# ── Session crash detection ───────────────────────────────────────────────────

class TestSessionCrash:
    def test_fires_on_session_crash_without_session_open(self):
        eng = _engine()
        # prev_close = 100, current = 96 → −4% (> threshold of 3%)
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 96.0])
        result = eng.evaluate("TEST", 96.0, df, META_NEUTRAL)
        assert result is not None
        assert "SESSION CRASH" in result["reason"]

    def test_no_fire_on_small_drop(self):
        eng = _engine()
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 98.5])
        result = eng.evaluate("TEST", 98.5, df, META_NEUTRAL)
        assert result is None

    def test_gap_and_crash_fires_when_still_below_open(self):
        eng = _engine()
        # Opened at 97 (gap down 3%), drifted further to 95 (−2% since open, −5% total)
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 95.0])
        result = eng.evaluate("TEST", 95.0, df, META_NEUTRAL, session_open=97.0)
        assert result is not None
        assert "SESSION CRASH" in result["reason"]

    def test_gap_and_recovery_does_not_fire(self):
        # Opened at 94 (gap down 6%) but has since recovered to 97 (above session open)
        eng = _engine()
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 97.0])
        result = eng.evaluate("TEST", 97.0, df, META_NEUTRAL, session_open=94.0)
        assert result is None, "A gap-down that has since recovered should not fire a crash alert"


# ── Multi-day trend bleed ─────────────────────────────────────────────────────

class TestTrendBleed:
    def test_fires_on_multiday_drop_below_sma(self):
        # 6 settled bars (need 3+1 for lookback) + 1 live tick
        eng = _engine(DROP_PERCENT=5.0, DROP_DAYS=3, SMA_LENGTH=5, SMA_GAP_PERCENT=1.0)
        # Falling prices: settled closes 100→98→96→94→92, live tick at 88
        df = _make_df([100.0, 98.0, 96.0, 94.0, 92.0, 88.0])
        # prev_close=92, 3-day lookback price=96, drop ~8.3% — exceeds 5%; also well below SMA
        result = eng.evaluate("TEST", 88.0, df, META_NEUTRAL)
        assert result is not None

    def test_no_fire_when_drop_insufficient(self):
        eng = _engine(DROP_PERCENT=10.0, SMA_GAP_PERCENT=5.0)
        df = _make_df([100.0, 99.0, 98.5, 98.0, 97.5, 97.0])
        result = eng.evaluate("TEST", 97.0, df, META_NEUTRAL)
        assert result is None


# ── ATR floor ─────────────────────────────────────────────────────────────────

class TestAtrFloor:
    def test_fires_when_price_breaks_atr_floor(self):
        eng = _engine()
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 99.0])
        atr_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        meta = {**META_NEUTRAL, "atr_stop_loss": 100.0, "atr_last_updated": atr_ts}
        result = eng.evaluate("TEST", 99.0, df, meta)
        assert result is not None
        assert "ATR" in result["reason"]

    def test_no_fire_when_atr_is_stale(self):
        eng = _engine()
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 99.0])
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        meta = {**META_NEUTRAL, "atr_stop_loss": 100.0, "atr_last_updated": old_ts}
        result = eng.evaluate("TEST", 99.0, df, meta)
        assert result is None

    def test_no_fire_when_atr_not_set(self):
        eng = _engine()
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 99.0])
        result = eng.evaluate("TEST", 99.0, df, META_NEUTRAL)
        assert result is None


# ── Beta scaling ──────────────────────────────────────────────────────────────

class TestBetaScaling:
    def test_high_beta_requires_larger_drop(self):
        # beta=2.0 → adj_threshold = 3.0 * 2.0 = 6.0; a 4% drop should NOT fire
        eng = _engine(SESSION_CRASH_THRESHOLD=3.0)
        meta = {**META_NEUTRAL, "beta": 2.0}
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 96.0])
        result = eng.evaluate("TEST", 96.0, df, meta)
        assert result is None

    def test_low_beta_trips_on_smaller_drop(self):
        # beta=0.5 → adj_threshold = 3.0 * 0.5 = 1.5; a 2% drop should fire
        eng = _engine(SESSION_CRASH_THRESHOLD=3.0)
        meta = {**META_NEUTRAL, "beta": 0.5}
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 98.0])
        result = eng.evaluate("TEST", 98.0, df, meta)
        assert result is not None


# ── AI Volatility Defense cap ─────────────────────────────────────────────────

class TestAiThresholdCap:
    def test_cap_overrides_beta_scaled_threshold(self):
        # beta=2.0 would scale threshold to 6%; cap at 2% means a 3% drop still fires
        eng = _engine(SESSION_CRASH_THRESHOLD=3.0)
        eng.ai_threshold_cap = 2.0
        meta = {**META_NEUTRAL, "beta": 2.0}
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 97.0])
        result = eng.evaluate("TEST", 97.0, df, meta)
        assert result is not None, "AI cap should override the beta-scaled threshold"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_returns_none_when_insufficient_history(self):
        eng = _engine(SMA_LENGTH=10, DROP_DAYS=5)
        df = _make_df([100.0, 98.0])  # only 2 rows, not enough settled bars
        result = eng.evaluate("TEST", 98.0, df, META_NEUTRAL)
        assert result is None

    def test_result_contains_price(self):
        eng = _engine()
        df = _make_df([100.0, 100.0, 100.0, 100.0, 100.0, 95.0])
        result = eng.evaluate("TEST", 95.0, df, META_NEUTRAL)
        assert result is not None
        assert result["price"] == 95.0


# ── SPY market context awareness ──────────────────────────────────────────────

class TestSpyMarketContext:
    def _context_df(self) -> pd.DataFrame:
        prices = [100.0] * 55 + [100.0]
        return pd.DataFrame({"Close": prices})

    def test_us_market_closed_skips_fetch_and_shows_unavailable_note(self):
        eng = _engine()
        eng.spy_change_pct = None
        df = self._context_df()
        with patch("crash_engine.market_pulse.is_exchange_open", return_value=False) as mock_open, \
             patch.object(eng, "_fetch_market_context") as mock_fetch:
            report = eng._generate_context_report("LCJP.L", -4.0, df, {"company_name": "Test"})
        mock_fetch.assert_not_called()
        assert "US market is currently closed" in report

    def test_us_market_open_calls_live_fetch_when_not_injected(self):
        eng = _engine()
        eng.spy_change_pct = None
        df = self._context_df()
        with patch("crash_engine.market_pulse.is_exchange_open", return_value=True), \
             patch.object(eng, "_fetch_market_context", return_value=None) as mock_fetch:
            report = eng._generate_context_report("TEST", -4.0, df, {"company_name": "Test"})
        mock_fetch.assert_called_once()
        assert "US market is currently closed" in report

    def test_etf_with_unresolved_benchmark_omits_comparison_sentence(self):
        """Per operator direction: an ETF whose holdings aren't cached yet, or don't map to a
        single dominant exchange, must omit the S&P 500 (or any) comparison sentence rather
        than default to a misleading one."""
        eng = _engine()
        df = self._context_df()
        meta = {"company_name": "Global Thematic ETF", "quote_type": "ETF", "top_holdings": None}
        report = eng._generate_context_report("ARKK", -4.0, df, meta)
        assert "S&P 500" not in report
        assert "currently closed" not in report

    def test_etf_with_resolved_benchmark_uses_relevant_index_name(self):
        eng = _engine()
        eng.benchmark_changes = {"^N225": -2.0}
        df = self._context_df()
        meta = {
            "company_name": "Asia Pacific ETF",
            "quote_type": "ETF",
            "top_holdings": json.dumps([{"symbol": "7203.T", "name": "Toyota", "weight": 0.2}]),
        }
        with patch(
            "crash_engine.markets_engine.resolve_benchmark_for_holdings",
            return_value={"ticker": "^N225", "display_name": "Nikkei 225", "exchange": "TSE"},
        ):
            report = eng._generate_context_report("VAPX.L", -4.0, df, meta)
        assert "Nikkei 225" in report
        assert "S&P 500" not in report
        assert "-2.00%" in report

    def test_etf_benchmark_falls_back_to_live_fetch_when_not_injected(self):
        eng = _engine()
        eng.benchmark_changes = {}
        df = self._context_df()
        meta = {
            "company_name": "Asia Pacific ETF",
            "quote_type": "ETF",
            "top_holdings": json.dumps([{"symbol": "7203.T", "name": "Toyota", "weight": 0.2}]),
        }
        with patch(
            "crash_engine.markets_engine.resolve_benchmark_for_holdings",
            return_value={"ticker": "^N225", "display_name": "Nikkei 225", "exchange": "TSE"},
        ), patch.object(eng, "_fetch_live_change_pct", return_value=-3.0) as mock_fetch:
            report = eng._generate_context_report("VAPX.L", -4.0, df, meta)
        mock_fetch.assert_called_once_with("^N225", "TSE")
        assert "-3.00%" in report

    def test_etf_benchmark_market_closed_shows_index_specific_note(self):
        eng = _engine()
        eng.benchmark_changes = {}
        df = self._context_df()
        meta = {
            "company_name": "Asia Pacific ETF",
            "quote_type": "ETF",
            "top_holdings": json.dumps([{"symbol": "7203.T", "name": "Toyota", "weight": 0.2}]),
        }
        with patch(
            "crash_engine.markets_engine.resolve_benchmark_for_holdings",
            return_value={"ticker": "^N225", "display_name": "Nikkei 225", "exchange": "TSE"},
        ), patch.object(eng, "_fetch_live_change_pct", return_value=None):
            report = eng._generate_context_report("VAPX.L", -4.0, df, meta)
        assert "Nikkei 225 is currently closed" in report

    def test_non_etf_ticker_keeps_sp500_comparison_even_with_holdings_resolver_available(self):
        eng = _engine()
        df = self._context_df()
        meta = {"company_name": "Test Corp", "quote_type": "EQUITY"}
        with patch("crash_engine.markets_engine.resolve_benchmark_for_holdings") as mock_resolve:
            report = eng._generate_context_report("TEST", -4.0, df, meta)
        mock_resolve.assert_not_called()
        assert "S&P 500" in report

    def test_fetch_market_context_skips_when_nyse_closed(self):
        eng = _engine()
        with patch("crash_engine.market_pulse.is_exchange_open", return_value=False), \
             patch("crash_engine.yahoo_engine.get_intraday") as mock_intraday:
            result = eng._fetch_market_context()
        assert result is None
        mock_intraday.assert_not_called()

    def test_fetch_market_context_fetches_when_nyse_open(self):
        eng = _engine()
        spy_df = pd.DataFrame({"Close": [400.0, 398.0, 396.0, 394.0, 392.0]})
        with patch("crash_engine.market_pulse.is_exchange_open", return_value=True), \
             patch("crash_engine.yahoo_engine.get_intraday", return_value={"SPY": spy_df}):
            result = eng._fetch_market_context()
        assert result is not None
        assert result < 0
