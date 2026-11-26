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

pytestmark = [pytest.mark.restore_tables("yahoo_api_call_log", "yahoo_api_stats"), pytest.mark.usefixtures("offline_yahoo")]


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


def _intraday_frame(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-11-25 14:30", periods=len(prices), freq="5min", tz="UTC")
    return pd.DataFrame({"Close": prices}, index=idx)


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


class TestCatalystHeadlines:
    def _report_for(self, news):
        eng = _engine()
        eng.spy_change_pct = None
        df = pd.DataFrame({"Close": [100.0] * 56})
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=False), \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=None), \
             patch("crash_engine.yahoo_engine.get_news", return_value=news):
            return eng._generate_context_report("TEST", -4.0, df, {"company_name": "Test"})

    @staticmethod
    def _item(title, publisher):
        from datetime import datetime, timezone
        pub = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"id": title, "content": {"title": title, "summary": "", "pubDate": pub,
                                          "provider": {"displayName": publisher}}}

    def test_headline_without_publisher_has_no_empty_source_label(self):
        report = self._report_for([self._item("Shares slide on guidance", "")])
        assert "- Shares slide on guidance" in report
        assert "*:*" not in report

    def test_headline_with_publisher_keeps_source_label(self):
        report = self._report_for([self._item("Shares slide on guidance", "Reuters")])
        assert "- *Reuters:* Shares slide on guidance" in report


# ── SPY market context awareness ──────────────────────────────────────────────

class TestSpyMarketContext:
    def _context_df(self) -> pd.DataFrame:
        prices = [100.0] * 55 + [100.0]
        return pd.DataFrame({"Close": prices})

    def test_us_market_closed_no_cache_shows_unavailable_note(self):
        eng = _engine()
        eng.spy_change_pct = None
        df = self._context_df()
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=False), \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=None) as mock_cached:
            report = eng._generate_context_report("LCJP.L", -4.0, df, {"company_name": "Test"})
        mock_cached.assert_called_once_with("^GSPC")
        assert "No recent US S&P 500 data is available" in report

    def test_us_market_closed_falls_back_to_last_session_cached_change(self):
        """Core fix: a foreign/US benchmark that has already closed for the day must still show
        its last completed session's move (from market_pulse_cache, same source as the Markets
        page) instead of reporting unavailable — this is what a real KOSPI-closed alert needed."""
        eng = _engine()
        eng.spy_change_pct = None
        df = self._context_df()
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=False), \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=-9.0):
            report = eng._generate_context_report("LCJP.L", -4.0, df, {"company_name": "Test"})
        assert "US S&P 500, last session: -9.00%" in report

    def test_us_market_open_calls_live_fetch_when_not_injected(self):
        eng = _engine()
        eng.spy_change_pct = None
        df = self._context_df()
        with patch.object(eng, "_fetch_live_change_pct", return_value=None) as mock_fetch, \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=None):
            report = eng._generate_context_report("TEST", -4.0, df, {"company_name": "Test"})
        mock_fetch.assert_called_once_with("SPY", "NYSE")
        assert "No recent US S&P 500 data is available" in report

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

    def test_etf_benchmark_market_closed_no_cache_shows_unavailable_note(self):
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
        ), patch.object(eng, "_fetch_live_change_pct", return_value=None), \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=None):
            report = eng._generate_context_report("VAPX.L", -4.0, df, meta)
        assert "No recent Nikkei 225 data is available" in report

    def test_etf_benchmark_market_closed_falls_back_to_last_session_cached_change(self):
        """The KOSPI real-world case: the exchange the ETF tracks has already closed, but its
        last session's move (cached from Markets page traffic) is still the relevant context."""
        eng = _engine()
        eng.benchmark_changes = {}
        df = self._context_df()
        meta = {
            "company_name": "Korea Heavy ETF",
            "quote_type": "ETF",
            "top_holdings": json.dumps([{"symbol": "005930.KS", "name": "Samsung", "weight": 0.2}]),
        }
        with patch(
            "crash_engine.markets_engine.resolve_benchmark_for_holdings",
            return_value={"ticker": "^KS11", "display_name": "KOSPI", "exchange": "KRX"},
        ), patch.object(eng, "_fetch_live_change_pct", return_value=None), \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=-9.0):
            report = eng._generate_context_report("KODEX.L", -4.0, df, meta)
        assert "KOSPI, last session: -9.00%" in report

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
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=False), \
             patch("crash_engine.yahoo_engine.get_intraday") as mock_intraday:
            result = eng._fetch_market_context()
        assert result is None
        mock_intraday.assert_not_called()

    def test_fetch_market_context_fetches_when_nyse_open(self):
        eng = _engine()
        spy_df = _intraday_frame([400.0, 398.0, 396.0, 394.0, 392.0])
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=True), \
             patch("crash_engine.yahoo_engine.get_intraday", return_value={"SPY": spy_df}), \
             patch("crash_engine.previous_settled_close", return_value=400.0):
            result = eng._fetch_market_context()
        assert result is not None
        assert result < 0

    def test_live_change_is_measured_from_the_previous_close_not_the_first_bar(self):
        eng = _engine()
        df = _intraday_frame([92.0, 92.0, 92.0, 92.0])
        with patch("crash_engine.yahoo_engine.get_intraday", return_value={"AAA": df}), \
             patch("crash_engine.previous_settled_close", return_value=100.0) as prev:
            result = eng._fetch_live_change_pct("AAA", None)
        prev.assert_called_once_with("AAA", df.index[-1].date())
        assert result == pytest.approx(-8.0)

    def test_live_change_is_none_without_a_previous_close(self):
        eng = _engine()
        df = _intraday_frame([92.0, 92.0, 92.0, 92.0])
        with patch("crash_engine.yahoo_engine.get_intraday", return_value={"AAA": df}), \
             patch("crash_engine.previous_settled_close", return_value=None):
            assert eng._fetch_live_change_pct("AAA", None) is None


# ── Volume and peer context ───────────────────────────────────────────────────

class TestVolumeContext:
    def _frames(self, last_hist_volume):
        hist_index = pd.date_range("2026-10-26", periods=25, freq="B")
        volumes = [100.0] * 24 + [last_hist_volume]
        df_hist = pd.DataFrame({"Close": [100.0] * 25, "Volume": volumes}, index=hist_index)
        tick = hist_index[-1] + pd.Timedelta(days=1, hours=15, minutes=10)
        df_combined = pd.concat([df_hist[["Close"]], pd.DataFrame({"Close": [95.0]}, index=[tick])])
        return df_hist, df_combined

    def _report(self, df_hist, df_combined, session_volume, projected_volume):
        eng = _engine()
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=False), \
             patch("crash_engine.market_pulse.get_cached_change_pct", return_value=0.0), \
             patch("crash_engine.yahoo_engine.get_news", return_value=[]):
            return eng._generate_context_report(
                "TEST", -5.0, df_combined, {"company_name": "Test"}, df_hist, session_volume, projected_volume,
            )

    def test_report_quotes_todays_session_volume_not_the_last_stored_bar(self):
        df_hist, df_combined = self._frames(last_hist_volume=776.0)
        report = self._report(df_hist, df_combined, session_volume=150.0, projected_volume=300.0)
        assert "Volume so far today is 150" in report
        assert "776" not in report

    def test_last_stored_bar_of_a_prior_session_counts_in_the_average(self):
        df_hist, df_combined = self._frames(last_hist_volume=1300.0)
        report = self._report(df_hist, df_combined, session_volume=150.0, projected_volume=300.0)
        assert "20-day average of 160" in report

    def test_todays_own_forming_bar_is_left_out_of_the_average(self):
        df_hist, df_combined = self._frames(last_hist_volume=1300.0)
        df_combined = df_combined.iloc[:-1].copy()
        report = self._report(df_hist, df_combined, session_volume=100.0, projected_volume=250.0)
        assert "20-day average of 100" in report

    def test_projected_volume_at_or_below_1_5x_average_reads_as_average(self):
        df_hist, df_combined = self._frames(last_hist_volume=100.0)
        report = self._report(df_hist, df_combined, session_volume=60.0, projected_volume=120.0)
        assert "low/average volume so far today" in report
        assert "Selling pressure is severe" not in report

    def test_no_session_volume_leaves_out_the_volume_sentence(self):
        df_hist, df_combined = self._frames(last_hist_volume=776.0)
        report = self._report(df_hist, df_combined, session_volume=None, projected_volume=None)
        assert "volume" not in report.lower()

    def test_evaluate_passes_the_session_volume_into_the_report(self):
        eng = _engine()
        df_hist, df_combined = self._frames(last_hist_volume=100.0)
        with patch.object(eng, "_generate_context_report", return_value="ctx") as mock_report:
            eng.evaluate("TEST", 90.0, df_combined, {"beta": 1.0, "company_name": "Test"}, df_hist, None, 150.0, 300.0)
        assert mock_report.call_args.args[-2:] == (150.0, 300.0)


class TestPeerContext:
    DF = pd.DataFrame({"Close": [100.0] * 56})

    def _report(self, peer_changes, spy_change=0.5, group="Semiconductors"):
        eng = _engine()
        eng.peer_changes = peer_changes
        meta = {"company_name": "Test", "peer_group": group}
        with patch("crash_engine.market_session_helpers.is_exchange_open", return_value=True), \
             patch.object(eng, "_fetch_live_change_pct", return_value=spy_change), \
             patch("crash_engine.yahoo_engine.get_news", return_value=[]):
            return eng._generate_context_report("TEST", -5.0, self.DF, meta)

    def test_flat_index_with_peers_also_down_is_not_called_isolated(self):
        report = self._report({"Semiconductors": {"TEST": -6.0, "MU": -7.0, "INTC": -5.0}})
        assert "idiosyncratic" not in report
        assert "shared across its peers" in report
        assert "2 of 2 other Semiconductors holdings are also down this session (median -6.00%)." in report

    def test_flat_index_with_flat_peers_is_called_isolated(self):
        report = self._report({"Semiconductors": {"TEST": -6.0, "MU": 0.4, "INTC": 0.2}})
        assert "isolated (idiosyncratic)" in report
        assert "0 of 2 other Semiconductors holdings" in report

    def test_fewer_than_two_peers_says_the_context_is_unavailable(self):
        report = self._report({"Semiconductors": {"TEST": -6.0, "MU": -7.0}})
        assert "idiosyncratic" not in report
        assert "no peer data is available" in report

    def test_ticker_without_a_peer_group_says_the_context_is_unavailable(self):
        report = self._report({}, group=None)
        assert "no peer data is available" in report

    def test_heavy_index_sell_off_keeps_its_own_sentence(self):
        report = self._report({"Semiconductors": {"TEST": -6.0, "MU": -7.0, "INTC": -5.0}}, spy_change=-2.0)
        assert "heavy sell-off" in report
        assert "2 of 2 other Semiconductors holdings" in report


class TestEtfBenchmarkByName:
    def test_nasdaq_100_fund_is_compared_with_the_nasdaq_index_not_the_sp500(self):
        eng = _engine()
        eng.benchmark_changes = {"^NDX": -3.2}
        meta = {
            "company_name": "INVESCO MARKETS III PLC INVESCO", "quote_type": "ETF",
            "long_name": "Invesco EQQQ NASDAQ-100 UCITS ETF (GBP Hdg)",
            "top_holdings": json.dumps([{"symbol": "AAPL", "name": "Apple", "weight": 0.08}]),
        }
        report = eng._generate_context_report("EQGB.L", -4.0, pd.DataFrame({"Close": [100.0] * 56}), meta)
        assert "US Nasdaq 100" in report
        assert "S&P 500" not in report
        assert "-3.20%" in report
