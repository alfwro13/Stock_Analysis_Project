"""
Tests for reports_engine.py.

Focus on Python-side logic that the SQL cannot enforce:
  - get_dividend_harvest_setups: ex-div date parsing (Unix ts, ISO string, bad value)
    and ascending sort by date.
  - get_mean_reversion_setups: min_sma_distance in-Python filter.
  - Empty-DB guard paths for all public functions.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import database
from reports_engine import (
    get_dividend_harvest_setups,
    get_mean_reversion_setups,
    get_sector_trends,
    get_leaders_laggards,
    get_quality_compounders,
    get_quality_on_sale,
    get_garp_tenbaggers,
)

pytestmark = pytest.mark.reports


def _seed_universe(conn, ticker, company="Test Co", sector="Technology"):
    conn.execute(
        "INSERT OR REPLACE INTO market_universe (ticker, company_name, sector, is_index) VALUES (?,?,?,1)",
        (ticker, company, sector),
    )


def _seed_quant_signal(conn, ticker, rsi=50.0, close=100.0, sma_50=90.0, sma_200=80.0):
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, rsi_14, close_price, sma_50, sma_200) "
        "VALUES (?,?,?,?,?,?)",
        (ticker, "2026-06-01", rsi, close, sma_50, sma_200),
    )


def _seed_stock_signal(conn, ticker, div_yield=0.04, ex_date="2026-09-01",
                       score=65, quote_type="EQUITY"):
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals "
        "(ticker, dividend_yield, ex_dividend_date, composite_score, quote_type, "
        " roe, profit_margin, debt_to_equity, revenue_growth, current_ratio, "
        " trailing_pe, forward_pe, fifty_two_week_low, peter_lynch_peg) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticker, div_yield, ex_date, score, quote_type,
         0.20, 0.15, 50.0, 0.20, 2.0, 18.0, 20.0, 85.0, 0.5),
    )


class TestDividendHarvestDateParsing:
    """Unit tests for the ex-dividend date normalisation logic."""

    def _fake_row(self, ex_date_raw, div_yield=0.05, score=65):
        return {
            "ticker": "TST", "company_name": "Test", "country": "US",
            "sector": "Tech", "exchange": "NYSE", "currency": "USD",
            "close_price": 100.0, "dividend_yield": div_yield,
            "ex_dividend_date": ex_date_raw, "composite_score": score,
            "ml_confidence_score": 70.0,
        }

    def _run_with_rows(self, rows):
        """Patch the DB query to return the supplied rows and collect the result."""
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.fetchall.return_value = [
            {k: v for k, v in r.items()} for r in rows
        ]
        # Make dict(row) work on plain dicts
        mock_conn.cursor.return_value.fetchall.return_value = rows
        with patch("reports_engine.get_connection", return_value=mock_conn):
            return get_dividend_harvest_setups(min_yield=0.0, min_score=0)

    def test_unix_timestamp_parsed_to_iso_date(self):
        ts = datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp()
        rows = [self._fake_row(str(ts))]
        result = self._run_with_rows(rows)
        assert len(result) == 1
        assert result[0]["ex_dividend_date"] == "2026-09-15"

    def test_iso_string_passthrough(self):
        rows = [self._fake_row("2026-09-15")]
        result = self._run_with_rows(rows)
        assert len(result) == 1
        assert result[0]["ex_dividend_date"] == "2026-09-15"

    def test_unparseable_date_drops_row(self):
        rows = [self._fake_row("bad-date")]
        result = self._run_with_rows(rows)
        assert result == []

    def test_pre_2000_timestamp_drops_row(self):
        # 946684799 = 1999-12-31 23:59:59 UTC — below the post-2000 guard
        rows = [self._fake_row("946684799")]
        result = self._run_with_rows(rows)
        assert result == []

    def test_results_sorted_ascending_by_ex_date(self):
        rows = [
            self._fake_row("2026-12-01"),
            self._fake_row("2026-09-01"),
            self._fake_row("2026-10-15"),
        ]
        result = self._run_with_rows(rows)
        dates = [r["ex_dividend_date"] for r in result]
        assert dates == sorted(dates)

    def test_empty_query_result_returns_empty_list(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.fetchall.return_value = []
        with patch("reports_engine.get_connection", return_value=mock_conn):
            assert get_dividend_harvest_setups() == []


class TestMeanReversionSmaFilter:
    """The min_sma_distance filter is applied in Python, not SQL."""

    def _seed(self):
        conn = database.get_connection()
        try:
            _seed_universe(conn, "AAPL")
            _seed_universe(conn, "MSFT")
            # AAPL: close=110, sma_200=100 → distance = 10%
            _seed_quant_signal(conn, "AAPL", rsi=25.0, close=110.0, sma_50=95.0, sma_200=100.0)
            # MSFT: close=102, sma_200=100 → distance = 2%
            _seed_quant_signal(conn, "MSFT", rsi=28.0, close=102.0, sma_50=95.0, sma_200=100.0)
            conn.commit()
        finally:
            conn.close()

    def test_zero_min_distance_returns_both(self):
        self._seed()
        result = get_mean_reversion_setups(max_rsi=30.0, min_sma_distance=0.0)
        tickers = {r["ticker"] for r in result}
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_high_min_distance_filters_close_stock(self):
        self._seed()
        # Only AAPL (10%) should pass when min_sma_distance=5.0
        result = get_mean_reversion_setups(max_rsi=30.0, min_sma_distance=5.0)
        tickers = {r["ticker"] for r in result}
        assert "AAPL" in tickers
        assert "MSFT" not in tickers


class TestEmptyDbGuards:
    """All public functions must return [] when the query returns no rows."""

    def _empty_conn(self):
        m = MagicMock()
        m.cursor.return_value.fetchall.return_value = []
        return m

    def test_get_sector_trends_empty(self):
        with patch("reports_engine.get_connection", return_value=self._empty_conn()):
            assert get_sector_trends() == []

    def test_get_leaders_laggards_empty(self):
        with patch("reports_engine.get_connection", return_value=self._empty_conn()):
            assert get_leaders_laggards() == []

    def test_get_quality_compounders_empty(self):
        with patch("reports_engine.get_connection", return_value=self._empty_conn()):
            assert get_quality_compounders() == []

    def test_get_quality_on_sale_empty(self):
        with patch("reports_engine.get_connection", return_value=self._empty_conn()):
            assert get_quality_on_sale() == []

    def test_get_garp_tenbaggers_empty(self):
        with patch("reports_engine.get_connection", return_value=self._empty_conn()):
            assert get_garp_tenbaggers() == []
