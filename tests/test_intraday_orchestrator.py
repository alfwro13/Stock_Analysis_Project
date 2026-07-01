"""
tests/test_intraday_orchestrator.py  ── INTRADAY ORCHESTRATOR

Covers the two pure helper functions that had no tests:

  format_currency()  — GBp→GBP scaling, symbol mapping, 3-letter fallback, None guard
  build_stock_url()  — port appended for IP/localhost, dropped for domain names,
                       no double-port when base already contains one
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import create_account, get_performance_cache
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
