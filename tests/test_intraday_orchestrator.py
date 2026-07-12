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

from database import create_account, get_connection, get_performance_cache
from intraday_orchestrator import IntradayOrchestrator, format_currency, build_stock_url


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
        with patch("accounts_engine.get_combined_holdings", return_value=combined):
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
            patch("intraday_orchestrator.market_pulse.is_quote_settled", return_value=is_settled) as mock_settled,
            patch("intraday_orchestrator.upsert_live_price") as mock_upsert,
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
