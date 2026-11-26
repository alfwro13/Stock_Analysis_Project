"""
tests/test_intraday_orchestrator.py  ── INTRADAY ORCHESTRATOR

Covers the two pure helper functions that had no tests:

  format_currency()  — GBp→GBP scaling, symbol mapping, 3-letter fallback, None guard
  build_stock_url()  — port appended for IP/localhost, dropped for domain names,
                       no double-port when base already contains one
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import create_account, get_connection, get_performance_cache
from intraday_orchestrator import IntradayOrchestrator, format_currency, build_stock_url

pytestmark = [pytest.mark.restore_tables("account_benchmark_tickers", "account_performance_cache", "alert_state", "asset_profiles", "stock_signals", "system_notifications", "yahoo_api_call_log", "yahoo_api_stats"), pytest.mark.usefixtures("offline_yahoo")]


# ── format_currency ───────────────────────────────────────────────────────────

class TestFormatCurrency:

    def test_usd_uses_dollar_symbol(self):
        assert format_currency(1234.56, "USD") == "$1,234.56"

    def test_gbp_uses_pound_symbol(self):
        assert format_currency(500.0, "GBP") == "£500.00"

    def test_eur_uses_euro_symbol(self):
        assert format_currency(99.99, "EUR") == "€99.99"

    def test_gbp_pence_scaled_and_converted(self):
        """GBp (pence) is divided by 100 and rendered as GBP."""
        result = format_currency(12345.0, "GBp")
        assert result == "£123.45"

    def test_unknown_currency_uses_3_letter_code(self):
        result = format_currency(42.5, "JPY")
        assert result == "42.50 JPY"

    def test_none_price_returns_na(self):
        assert format_currency(None, "USD") == "N/A"

    def test_none_currency_defaults_to_usd(self):
        assert format_currency(100.0, None) == "$100.00"

    def test_empty_currency_defaults_to_usd(self):
        assert format_currency(100.0, "") == "$100.00"

    def test_thousands_separator_applied(self):
        assert format_currency(1_000_000.0, "USD") == "$1,000,000.00"

    def test_gbp_pence_zero(self):
        assert format_currency(0.0, "GBp") == "£0.00"


# ── build_stock_url ───────────────────────────────────────────────────────────

class TestBuildStockUrl:

    def test_localhost_without_port_appends_port(self):
        url = build_stock_url("http://localhost", 8090, "AAPL")
        assert url == "http://localhost:8090/stock/AAPL"

    def test_localhost_with_port_already_in_base_does_not_double(self):
        url = build_stock_url("http://localhost:8090", 8090, "AAPL")
        assert url == "http://localhost:8090/stock/AAPL"

    def test_ip_address_without_port_appends_port(self):
        url = build_stock_url("http://192.168.1.10", 8090, "MSFT")
        assert url == "http://192.168.1.10:8090/stock/MSFT"

    def test_ip_address_with_port_already_in_base_does_not_double(self):
        url = build_stock_url("http://192.168.1.10:8090", 8090, "MSFT")
        assert url == "http://192.168.1.10:8090/stock/MSFT"

    def test_domain_name_no_port_appended(self):
        url = build_stock_url("https://stocks.example.com", 8090, "TSLA")
        assert url == "https://stocks.example.com/stock/TSLA"

    def test_domain_name_trailing_slash_stripped(self):
        url = build_stock_url("https://stocks.example.com/", 8090, "TSLA")
        assert url == "https://stocks.example.com/stock/TSLA"

    def test_ticker_included_in_path(self):
        url = build_stock_url("http://localhost", 8090, "NVDA")
        assert "/stock/NVDA" in url

    def test_lse_ticker_with_dot(self):
        url = build_stock_url("http://localhost", 8090, "BARC.L")
        assert url.endswith("/stock/BARC.L")


# ── _settled_close ────────────────────────────────────────────────────────────

class TestSettledClose:
    """Yahoo's 5m intraday feed returns the still-forming current bucket as its last row; using
    it directly as "current price" can catch a transient print that revises substantially once
    the bucket finishes filling. _settled_close() falls back to the prior, fully-closed bar
    whenever the last bar's own bucket hasn't finished yet."""

    def _bars(self, closes, minutes_ago_last):
        now = pd.Timestamp.now(tz=timezone.utc)
        idx = [now - pd.Timedelta(minutes=minutes_ago_last + 5 * (len(closes) - 1 - i)) for i in range(len(closes))]
        return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(idx))

    def test_still_forming_last_bar_falls_back_to_prior_close(self):
        df = self._bars([100.0, 105.0], minutes_ago_last=1)
        assert IntradayOrchestrator()._settled_close(df) == 100.0

    def test_settled_last_bar_used_directly(self):
        df = self._bars([100.0, 105.0], minutes_ago_last=6)
        assert IntradayOrchestrator()._settled_close(df) == 105.0

    def test_single_row_has_no_fallback(self):
        df = self._bars([100.0], minutes_ago_last=1)
        assert IntradayOrchestrator()._settled_close(df) == 100.0


class TestBulkChangePct:
    @staticmethod
    def _frame(closes, minutes_ago_last=10):
        end = pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(minutes=minutes_ago_last)
        idx = pd.date_range(end=end, periods=len(closes), freq="5min")
        return pd.DataFrame({"Close": closes}, index=idx)

    def test_a_name_that_gapped_down_then_went_flat_shows_the_gap(self):
        df = self._frame([92.0, 92.0, 92.0, 92.0])
        with patch("intraday_orchestrator.previous_settled_close", return_value=100.0) as prev:
            pct = IntradayOrchestrator()._bulk_change_pct(df, ["AAA"], "AAA")
        prev.assert_called_once_with("AAA", df.index[-1].date())
        assert pct == pytest.approx(-8.0)

    def test_multiindex_frame_slices_the_ticker(self):
        df = self._frame([92.0, 92.0, 92.0])
        bulk = pd.concat({"AAA": df, "BBB": df * 0 + 50.0}, axis=1)
        with patch("intraday_orchestrator.previous_settled_close", return_value=100.0):
            assert IntradayOrchestrator()._bulk_change_pct(bulk, ["AAA", "BBB"], "AAA") == pytest.approx(-8.0)

    def test_none_without_a_previous_close(self):
        df = self._frame([92.0, 92.0, 92.0])
        with patch("intraday_orchestrator.previous_settled_close", return_value=None):
            assert IntradayOrchestrator()._bulk_change_pct(df, ["AAA"], "AAA") is None


# ── _refresh_account_performance_cache ────────────────────────────────────────

class TestRefreshAccountPerformanceCache:
    """Job-runner-level coverage per AGENTS.md: confirms the scan cycle actually populates
    account_performance_cache for Trading accounts, not just that accounts_engine's own
    refresh_performance_cache() works in isolation."""

    @pytest.mark.db
    def test_populates_cache_for_trading_accounts(self):
        aid = create_account("IntradayScanAcc", "GBP", initial_cash=1000.0)

        IntradayOrchestrator()._refresh_account_performance_cache()

        cached = get_performance_cache(aid)
        assert cached is not None
        assert cached["total_value"] == 1000.0

    @pytest.mark.db
    def test_skips_non_trading_accounts(self):
        aid = create_account("IntradayScanPensionAcc", "GBP", account_type="Pension")

        IntradayOrchestrator()._refresh_account_performance_cache()

        assert get_performance_cache(aid) is None


# ── get_portfolio_tickers ─────────────────────────────────────────────────────

class TestGetPortfolioTickers:
    """TBILL-{txn_id} synthetic tickers aren't real Yahoo Finance symbols -- including one in the
    bulk 5m fetch produces a guaranteed-failing request every ~10 minutes (repeating 'possibly
    delisted' errors in production logs) for a ticker that can never have real market data."""

    def test_excludes_tbill_synthetic_tickers(self):
        combined = {"TBILL-606": {"ticker": "TBILL-606"}, "AAPL": {"ticker": "AAPL"}}
        with patch("accounts.accounts_engine.get_combined_holdings", return_value=combined):
            tickers = IntradayOrchestrator().get_portfolio_tickers()

        assert "TBILL-606" not in tickers
        assert "AAPL" in tickers


# ── _run schedule-bounds gating ─────────────────────────────────────────────────

def _fake_datetime(fixed_utc):
    """Return a datetime subclass whose .now() always returns fixed_utc."""
    class _Fake(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc
    return _Fake


class TestScheduleBoundsGating:
    """CRASH_ALERTS START_TIME/END_TIME are entered in the Settings UI as USER_TIMEZONE wall-clock
    time (no '(UTC)' label, unlike AI_CONTAGION/TRAP_MONITOR) and the scheduler fires this job on a
    CronTrigger(timezone=user_tz) — so the in-job bounds check must localize before comparing to UTC
    'now'. A bare string compare silently delayed the first successful daily scan by the DST offset
    during BST (fixed 2026-07-08)."""

    @pytest.mark.db
    def test_bst_morning_within_local_window_is_not_aborted(self):
        # 07:10 UTC on a July (BST, UTC+1) day = 08:10 Europe/London.
        now_utc = datetime(2026, 7, 8, 7, 10, 0, tzinfo=timezone.utc)
        orchestrator = IntradayOrchestrator()
        orchestrator.config.setdefault("SCHEDULING", {})["CRASH_ALERTS"] = {
            "START_TIME": "08:00", "END_TIME": "21:00",
        }
        with patch("intraday_orchestrator.datetime", _fake_datetime(now_utc)), \
             patch("time_engine._load_config", return_value={"USER_TIMEZONE": "Europe/London"}), \
             patch.object(IntradayOrchestrator, "get_portfolio_tickers", return_value=[]) as mock_tickers:
            orchestrator._run(get_connection())

        mock_tickers.assert_called_once()

    @pytest.mark.db
    def test_bst_morning_before_local_window_is_aborted(self):
        # 07:10 UTC on a July (BST, UTC+1) day = 08:10 Europe/London — before a 09:30 local start.
        now_utc = datetime(2026, 7, 8, 7, 10, 0, tzinfo=timezone.utc)
        orchestrator = IntradayOrchestrator()
        orchestrator.config.setdefault("SCHEDULING", {})["CRASH_ALERTS"] = {
            "START_TIME": "09:30", "END_TIME": "21:00",
        }
        with patch("intraday_orchestrator.datetime", _fake_datetime(now_utc)), \
             patch("time_engine._load_config", return_value={"USER_TIMEZONE": "Europe/London"}), \
             patch.object(IntradayOrchestrator, "get_portfolio_tickers", return_value=[]) as mock_tickers:
            orchestrator._run(get_connection())

        mock_tickers.assert_not_called()


# ── _run per-ticker quote-settlement gating ─────────────────────────────────────

class TestQuoteSettlementGating:
    """LSE's free Yahoo feed lags ~15 min at open; a held LSE ticker scanned in that window must
    not feed a not-yet-settled price into Crash/Moonshot/Anomaly/HoldingLimit or market_pulse_cache
    (the same race already fixed in accounts_engine.tickers_needing_refresh() /
    intraday_bottom_engine.run_scan(), not previously applied to this scan loop)."""

    @staticmethod
    def _intraday_df():
        idx = pd.date_range(end=pd.Timestamp.now(tz=timezone.utc), periods=5, freq="5min")
        return pd.DataFrame(
            {"Open": [100.0] * 5, "Close": [100.0, 101.0, 99.0, 102.0, 100.5], "Volume": [1000] * 5},
            index=idx,
        )

    @staticmethod
    def _hist_df():
        idx = pd.date_range(end=datetime.now(timezone.utc).date(), periods=25, freq="D")
        return pd.DataFrame({"Close": [90.0 + i * 0.1 for i in range(25)]}, index=idx)

    def _run_with_ticker(self, tmp_path, ticker="VOD.L", currency="GBP", is_settled=False):
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, currency, company_name) VALUES (?, ?, ?)",
            (ticker, currency, "Test Co"),
        )
        conn.commit()

        self._hist_df().to_parquet(tmp_path / f"{ticker}.parquet", engine="pyarrow")
        fake_dfs = {ticker: self._intraday_df(), "SPY": self._intraday_df(), "^TYX": self._intraday_df()}

        orchestrator = IntradayOrchestrator()
        orchestrator.config.setdefault("SCHEDULING", {})["CRASH_ALERTS"] = {
            "ENABLED": True, "START_TIME": "00:00", "END_TIME": "23:59",
        }

        with (
            patch("intraday_orchestrator.HISTORICAL_DIR", tmp_path),
            patch("intraday_orchestrator.INTRADAY_DIR", tmp_path),
            patch("time_engine._load_config", return_value={"USER_TIMEZONE": "UTC"}),
            patch.object(IntradayOrchestrator, "get_portfolio_tickers", return_value=[ticker]),
            patch("intraday_orchestrator.yahoo_engine.get_intraday", return_value=fake_dfs),
            patch("intraday_orchestrator.market_session_helpers.is_quote_settled", return_value=is_settled) as mock_settled,
            patch("intraday_orchestrator.upsert_live_price") as mock_upsert,
            # The fake OHLCV data can trip real Crash/Moonshot evaluation when is_settled=True —
            # without this, a settled-quote test fires a real Nextcloud Talk alert (see conftest.py's
            # _block_real_nextcloud_send session-wide safety net; this local patch is redundant with
            # it but kept to match this codebase's per-test-file mocking convention).
            patch("notification_engine.nextcloud_talk.send_text_message", return_value=True),
        ):
            orchestrator._run(conn)

        return mock_settled, mock_upsert

    @pytest.mark.db
    def test_unsettled_quote_skips_upsert_and_evaluation(self, tmp_path):
        mock_settled, mock_upsert = self._run_with_ticker(tmp_path, is_settled=False)
        mock_settled.assert_called_with("LSE", include_premarket=False)
        mock_upsert.assert_not_called()

    @pytest.mark.db
    def test_settled_quote_proceeds_to_upsert(self, tmp_path):
        mock_settled, mock_upsert = self._run_with_ticker(tmp_path, is_settled=True)
        mock_settled.assert_called_with("LSE", include_premarket=False)
        mock_upsert.assert_called_once()

    @pytest.mark.db
    def test_nyse_ticker_checked_with_premarket_true(self, tmp_path):
        mock_settled, _ = self._run_with_ticker(tmp_path, ticker="AAPL", currency="USD", is_settled=True)
        mock_settled.assert_called_with("NYSE", include_premarket=True)


    @pytest.mark.db
    def test_spy_change_is_injected_from_the_previous_close(self, tmp_path):
        seen = {}

        def capture(engine, *args, **kwargs):
            seen["spy"] = engine.spy_change_pct
            seen["bench"] = dict(engine.benchmark_changes)

        with patch("crash_engine.CrashEngine.evaluate", autospec=True, side_effect=capture), \
             patch("intraday_orchestrator.previous_settled_close", return_value=100.0):
            self._run_with_ticker(tmp_path, ticker="AAPL", currency="USD", is_settled=True)
        assert seen["spy"] == pytest.approx(2.0)
        assert seen["bench"]["SPY"] == pytest.approx(2.0)

    @pytest.mark.db
    def test_crash_evaluate_receives_todays_session_volume_and_its_projection(self, tmp_path):
        with patch("crash_engine.CrashEngine.evaluate", return_value=None) as mock_evaluate:
            self._run_with_ticker(tmp_path, ticker="AAPL", currency="USD", is_settled=True)
        session_volume, projected_volume = mock_evaluate.call_args.args[-2:]
        assert session_volume == 5000.0
        assert projected_volume >= session_volume


class TestAssetMetadataPeerGroupAndEtfName:
    def _seed(self, ticker, sector, industry, quote_type="EQUITY"):
        from db.database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO stock_signals (ticker, sector, quote_type, currency) VALUES (?, ?, ?, 'USD')",
                (ticker, sector, quote_type),
            )
            conn.execute(
                "INSERT OR REPLACE INTO asset_profiles (ticker, sector, industry, quote_type) VALUES (?, ?, ?, ?)",
                (ticker, sector, industry, quote_type),
            )
            conn.commit()
        finally:
            conn.close()

    def test_peer_group_is_the_industry_when_known_else_the_sector_else_none(self):
        self._seed("PEERG_1", "Technology", "Semiconductors")
        self._seed("PEERG_2", "Technology", "Unclassified")
        self._seed("PEERG_3", "Unclassified", "Unclassified")
        meta = IntradayOrchestrator().get_asset_metadata(["PEERG_1", "PEERG_2", "PEERG_3"])
        assert meta["PEERG_1"]["peer_group"] == "Semiconductors"
        assert meta["PEERG_2"]["peer_group"] == "Technology"
        assert meta["PEERG_3"]["peer_group"] is None

    def test_only_an_etf_carries_the_full_name_from_the_cached_dump(self):
        self._seed("PEERG_ETF", "Fund", "Unclassified", quote_type="ETF")
        self._seed("PEERG_EQ", "Technology", "Semiconductors")
        with patch("intraday_orchestrator.cached_fundamentals_extra", return_value={"long_name": "Invesco EQQQ NASDAQ-100 UCITS ETF"}) as dump:
            meta = IntradayOrchestrator().get_asset_metadata(["PEERG_ETF", "PEERG_EQ"])
        assert meta["PEERG_ETF"]["long_name"] == "Invesco EQQQ NASDAQ-100 UCITS ETF"
        assert meta["PEERG_EQ"]["long_name"] is None
        dump.assert_called_once_with("PEERG_ETF")
