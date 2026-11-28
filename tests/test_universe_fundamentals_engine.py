"""Tests for universe_fundamentals_engine pure functions."""
import json
import math
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from constants import (
    FUNDAMENTALS_MAX_AGE_DAYS, FUNDAMENTALS_REFRESH_DAYS, FUNDAMENTALS_REFRESH_MAX_RATE_LIMITS,
    FUNDAMENTALS_REFRESH_SETTLE_SECONDS,
)
from db import database as _db
from universe_fundamentals_engine import (
    _compute_fundamental_score, _clean, _needs_holdings_refresh, sync_etf_holdings_cache,
    _upsert_fundamentals,
)

pytestmark = pytest.mark.restore_tables("stock_signals")


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------

class TestClean:
    def test_none_returns_none(self):
        assert _clean(None) is None

    def test_integer_passthrough(self):
        assert _clean(42) == 42

    def test_normal_float_passthrough(self):
        assert _clean(3.14) == pytest.approx(3.14)

    def test_nan_float_returns_none(self):
        assert _clean(float('nan')) is None

    def test_inf_float_returns_none(self):
        assert _clean(float('inf')) is None

    def test_neg_inf_float_returns_none(self):
        assert _clean(float('-inf')) is None

    def test_nan_string_returns_none(self):
        assert _clean('nan') is None

    def test_inf_string_returns_none(self):
        assert _clean('inf') is None

    def test_neg_inf_string_returns_none(self):
        assert _clean('-inf') is None

    def test_numeric_string_returns_float(self):
        assert _clean('1.5') == pytest.approx(1.5)

    def test_non_numeric_string_returned_as_is(self):
        assert _clean('hello') == 'hello'

    def test_zero_passthrough(self):
        assert _clean(0) == 0

    def test_negative_number_passthrough(self):
        assert _clean(-5.5) == pytest.approx(-5.5)


# ---------------------------------------------------------------------------
# _compute_fundamental_score — scoring tiers
# ---------------------------------------------------------------------------

class TestComputeFundamentalScoreBasic:
    def test_empty_info_returns_zero_score(self):
        score, signal, _ = _compute_fundamental_score({})
        assert score == 0
        assert signal == "STRONG SELL"

    def test_score_clamped_to_100(self):
        """All metrics at exceptional tier should not exceed 100."""
        info = {
            "returnOnEquity": 0.35,       # +20
            "profitMargins": 0.30,         # +20
            "debtToEquity": 10,            # +20
            "revenueGrowth": 0.25,         # +15
            "currentRatio": 2.5,           # +15
            "trailingPE": 18,              # +10
        }
        score, _, _ = _compute_fundamental_score(info)
        assert score == 100

    def test_score_clamped_to_zero(self):
        """All metrics at worst tier should not go below 0."""
        info = {
            "returnOnEquity": -0.10,
            "profitMargins": -0.05,
            "debtToEquity": 300,
            "revenueGrowth": -0.15,
            "currentRatio": 0.5,
            "trailingPE": 55,
        }
        score, _, _ = _compute_fundamental_score(info)
        assert score == 0


class TestRoeScoring:
    def test_roe_above_30_adds_20(self):
        score, _, _ = _compute_fundamental_score({"returnOnEquity": 0.35})
        assert score == 20

    def test_roe_above_20_adds_15(self):
        score, _, _ = _compute_fundamental_score({"returnOnEquity": 0.25})
        assert score == 15

    def test_roe_above_15_adds_10(self):
        score, _, _ = _compute_fundamental_score({"returnOnEquity": 0.17})
        assert score == 10

    def test_roe_above_5_adds_5(self):
        score, _, _ = _compute_fundamental_score({"returnOnEquity": 0.08})
        assert score == 5

    def test_roe_negative_subtracts_10(self):
        score, _, _ = _compute_fundamental_score({"returnOnEquity": -0.05})
        assert score == 0  # clamped from -10


class TestSignalBuckets:
    def test_signal_strong_buy_above_70(self):
        # ROE>30%(+20) + margin>25%(+20) + D/E<20%(+20) + rev_growth>20%(+15) = 75
        info = {"returnOnEquity": 0.35, "profitMargins": 0.30, "debtToEquity": 10, "revenueGrowth": 0.25}
        _, signal, _ = _compute_fundamental_score(info)
        assert signal == "STRONG BUY"

    def test_signal_bullish_hold_50_to_69(self):
        # 15 + 15 + 20 = 50
        info = {"returnOnEquity": 0.25, "profitMargins": 0.20, "debtToEquity": 10}
        _, signal, _ = _compute_fundamental_score(info)
        assert signal == "BULLISH / HOLD"

    def test_signal_neutral_30_to_49(self):
        # ROE>5%(+5) + margin>10%(+10) + rev_growth>5%(+5) + D/E<100%(+10) = 30
        info = {"returnOnEquity": 0.08, "profitMargins": 0.12, "revenueGrowth": 0.07, "debtToEquity": 80}
        _, signal, _ = _compute_fundamental_score(info)
        assert signal == "NEUTRAL"

    def test_signal_bearish_caution_10_to_29(self):
        info = {"returnOnEquity": 0.08}  # score = 5 → 0 after clamp? No, 5 → BEARISH?
        # Only ROE, gives 5 → actually BEARISH? Let's check: score 5 → 5 >= 10? No. score >= 10 needed.
        # Let me use 10 exactly: ROE > 15% = 10
        info = {"returnOnEquity": 0.17}  # +10
        _, signal, _ = _compute_fundamental_score(info)
        assert signal == "BEARISH / CAUTION"

    def test_strong_sell_below_10(self):
        info = {"returnOnEquity": 0.08}  # +5, score=5 → < 10
        _, signal, _ = _compute_fundamental_score(info)
        assert signal == "STRONG SELL"


class TestBreakdownHtml:
    def test_notes_html_contains_breakdown(self):
        _, _, notes_html = _compute_fundamental_score({"returnOnEquity": 0.35})
        assert "ROE" in notes_html
        assert "<li>" in notes_html

    def test_notes_html_contains_universe_note(self):
        _, _, notes_html = _compute_fundamental_score({})
        assert "Technical signals unavailable" in notes_html


# ---------------------------------------------------------------------------
# _needs_holdings_refresh
# ---------------------------------------------------------------------------

class TestNeedsHoldingsRefresh:
    def test_missing_holdings_needs_refresh(self):
        assert _needs_holdings_refresh(None, None) is True

    def test_missing_timestamp_needs_refresh(self):
        assert _needs_holdings_refresh("[]", None) is True

    def test_unparseable_timestamp_needs_refresh(self):
        assert _needs_holdings_refresh("[]", "not-a-date") is True

    def test_recent_timestamp_does_not_need_refresh(self):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert _needs_holdings_refresh("[]", recent) is False

    def test_stale_timestamp_needs_refresh(self):
        stale = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        assert _needs_holdings_refresh("[]", stale) is True


# ---------------------------------------------------------------------------
# sync_etf_holdings_cache
# ---------------------------------------------------------------------------

def _upsert_stock_signal(ticker, quote_type, top_holdings=None, holdings_updated_at=None):
    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, quote_type, top_holdings, holdings_updated_at) "
            "VALUES (?, ?, ?, ?)",
            (ticker, quote_type, top_holdings, holdings_updated_at),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.db
class TestSyncEtfHoldingsCache:
    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute(
                "DELETE FROM stock_signals WHERE ticker IN ('TST_ETF', 'TST_EQUITY', 'TST_MUTUALFUND')"
            )
            conn.commit()
        finally:
            conn.close()

    def test_fetches_and_caches_holdings_for_etf(self):
        _upsert_stock_signal("TST_ETF", "ETF")
        df = pd.DataFrame(
            {"Name": ["Toyota"], "Holding Percent": [0.12]},
            index=pd.Index(["7203.T"], name="Symbol"),
        )
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings", return_value=df), \
             patch("universe_fundamentals_engine.yahoo_engine.get_fund_sector_weightings", return_value=None):
            sync_etf_holdings_cache(["TST_ETF"])

        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT top_holdings, holdings_updated_at FROM stock_signals WHERE ticker='TST_ETF'"
            ).fetchone()
        finally:
            conn.close()
        assert row["holdings_updated_at"] is not None
        holdings = json.loads(row["top_holdings"])
        assert holdings == [{"symbol": "7203.T", "name": "Toyota", "weight": 0.12}]

    def test_fetches_and_caches_sector_weightings_for_etf(self):
        _upsert_stock_signal("TST_ETF", "ETF")
        raw_weights = {"technology": 0.386, "financial_services": 0.115, "energy": 0.0}
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings", return_value=None), \
             patch("universe_fundamentals_engine.yahoo_engine.get_fund_sector_weightings", return_value=raw_weights):
            sync_etf_holdings_cache(["TST_ETF"])

        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT sector_weightings FROM stock_signals WHERE ticker='TST_ETF'"
            ).fetchone()
        finally:
            conn.close()
        weights = json.loads(row["sector_weightings"])
        # zero-weight sectors (energy) are dropped; snake_case keys map to Title Case names
        assert weights == [
            {"name": "Technology", "weight": 0.386},
            {"name": "Financial Services", "weight": 0.115},
        ]

    def test_fetches_holdings_for_mutualfund_ticker(self):
        _upsert_stock_signal("TST_MUTUALFUND", "MUTUALFUND")
        df = pd.DataFrame(
            {"Name": ["NVIDIA"], "Holding Percent": [0.04]},
            index=pd.Index(["NVDA"], name="Symbol"),
        )
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings", return_value=df), \
             patch("universe_fundamentals_engine.yahoo_engine.get_fund_sector_weightings", return_value=None):
            sync_etf_holdings_cache(["TST_MUTUALFUND"])

        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT top_holdings FROM stock_signals WHERE ticker='TST_MUTUALFUND'"
            ).fetchone()
        finally:
            conn.close()
        assert json.loads(row["top_holdings"]) == [{"symbol": "NVDA", "name": "NVIDIA", "weight": 0.04}]

    def test_skips_non_etf_ticker(self):
        _upsert_stock_signal("TST_EQUITY", "EQUITY")
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings") as mock_fetch:
            sync_etf_holdings_cache(["TST_EQUITY"])
        mock_fetch.assert_not_called()

    def test_skips_etf_with_fresh_cached_holdings(self):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _upsert_stock_signal("TST_ETF", "ETF", top_holdings="[]", holdings_updated_at=recent)
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings") as mock_fetch:
            sync_etf_holdings_cache(["TST_ETF"])
        mock_fetch.assert_not_called()

    def test_empty_ticker_list_is_noop(self):
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings") as mock_fetch:
            sync_etf_holdings_cache([])
        mock_fetch.assert_not_called()


class TestUpsertFundamentalsDoesNotWipeOtherEnginesColumns:
    """INSERT OR REPLACE used to reset every column not in this statement to its schema default,
    silently wiping top_holdings/sector_weightings/holdings_updated_at (this module's own other
    writer, sync_etf_holdings_cache) and piotroski_f_score/altman_z_score/beneish_m_score/
    forensic_last_updated (scheduler_jobs.py's monthly Forensic job) on every weekly sync run.
    Fixed via ON CONFLICT DO UPDATE with an explicit column list (found/fixed 2026-07-13)."""

    TICKER = "TST_UPSERT_WIPE"

    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker=?", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

    def test_second_write_preserves_other_engines_columns(self):
        conn = _db.get_connection()
        conn.execute(
            "INSERT INTO stock_signals (ticker, top_holdings, sector_weightings, holdings_updated_at, "
            "piotroski_f_score, altman_z_score, beneish_m_score, forensic_last_updated, setup_tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.TICKER, '[{"symbol": "NVDA", "weight": 0.1}]', '[{"name": "Technology", "weight": 1.0}]',
             "2026-07-01 00:00:00", 7.0, 3.2, -2.1, "2026-07-01 00:00:00", '["Quality Compounder"]'),
        )
        conn.commit()
        conn.close()

        _upsert_fundamentals(self.TICKER, {"shortName": "Test Co", "sector": "Technology", "country": "United States"})

        conn = _db.get_connection()
        row = conn.execute(
            "SELECT company_name, top_holdings, sector_weightings, holdings_updated_at, piotroski_f_score, "
            "altman_z_score, beneish_m_score, forensic_last_updated, setup_tags FROM stock_signals WHERE ticker=?",
            (self.TICKER,),
        ).fetchone()
        conn.close()

        assert row["company_name"] == "Test Co"  # confirms the upsert actually ran, not a no-op
        assert row["top_holdings"] == '[{"symbol": "NVDA", "weight": 0.1}]'
        assert row["sector_weightings"] == '[{"name": "Technology", "weight": 1.0}]'
        assert row["holdings_updated_at"] == "2026-07-01 00:00:00"
        assert row["piotroski_f_score"] == 7.0
        assert row["altman_z_score"] == 3.2
        assert row["beneish_m_score"] == -2.1
        assert row["forensic_last_updated"] == "2026-07-01 00:00:00"
        assert row["setup_tags"] == '["Quality Compounder"]'

    def test_first_insert_still_creates_a_row(self):
        _upsert_fundamentals(self.TICKER, {"shortName": "Test Co", "sector": "Technology", "country": "United States"})
        conn = _db.get_connection()
        row = conn.execute("SELECT company_name FROM stock_signals WHERE ticker=?", (self.TICKER,)).fetchone()
        conn.close()
        assert row["company_name"] == "Test Co"

    @pytest.mark.parametrize("days_ahead", [None, 20])
    def test_stores_the_earnings_date_yahoo_gives_or_null(self, days_ahead):
        report = None if days_ahead is None else datetime.now(timezone.utc) + timedelta(days=days_ahead)
        info = {"shortName": "Test Co", "sector": "Technology", "country": "United States"}
        if report:
            info["earningsTimestamp"] = int(report.timestamp())
        conn = _db.get_connection()
        try:
            conn.execute("INSERT INTO stock_signals (ticker, next_earnings_date) VALUES (?, 'stale')", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

        _upsert_fundamentals(self.TICKER, info)

        conn = _db.get_connection()
        try:
            row = conn.execute("SELECT next_earnings_date FROM stock_signals WHERE ticker=?", (self.TICKER,)).fetchone()
        finally:
            conn.close()
        assert row["next_earnings_date"] == (report.strftime("%Y-%m-%d") if report else None)


class TestUpsertFundamentalsStoresYahooUnits:
    """Yahoo sends dividendYield as a percent and, for a listing whose financialCurrency differs from its
    quote currency, a priceToBook / priceToSalesTrailing12Months that mixes the two currencies."""

    TICKERS = ("TST_UNITS_ADR", "TST_UNITS_LSE")

    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker IN (?, ?)", self.TICKERS)
            conn.commit()
        finally:
            conn.close()

    def _row(self, ticker):
        conn = _db.get_connection()
        try:
            return conn.execute(
                "SELECT dividend_yield, price_to_book, price_to_sales FROM stock_signals WHERE ticker=?", (ticker,)
            ).fetchone()
        finally:
            conn.close()

    def test_adr_with_foreign_financials_is_rebuilt_and_its_yield_is_a_fraction(self):
        _upsert_fundamentals("TST_UNITS_ADR", {
            "shortName": "ADR Co", "currency": "USD", "financialCurrency": "TWD", "dividendYield": 1.04,
            "priceToBook": 67.07491, "priceToSalesTrailing12Months": 0.36626557,
            "trailingPE": 22.631239, "returnOnEquity": 0.4186, "profitMargins": 0.51116,
        })
        row = self._row("TST_UNITS_ADR")
        assert row["dividend_yield"] == pytest.approx(0.0104)
        assert row["price_to_book"] == pytest.approx(9.47, abs=0.01)
        assert row["price_to_sales"] == pytest.approx(11.57, abs=0.01)

    def test_pence_listing_under_one_percent_is_divided_like_every_other(self):
        _upsert_fundamentals("TST_UNITS_LSE", {
            "shortName": "LSE Co", "currency": "GBp", "financialCurrency": "GBP", "dividendYield": 0.8,
            "priceToBook": 1.4, "priceToSalesTrailing12Months": 0.7,
        })
        row = self._row("TST_UNITS_LSE")
        assert row["dividend_yield"] == pytest.approx(0.008)
        assert (row["price_to_book"], row["price_to_sales"]) == (pytest.approx(1.4), pytest.approx(0.7))


class TestUpsertFundamentalsStoresMarketCap:
    TICKER = "TST_UNITS_MCAP"

    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker=?", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

    def _stored(self):
        conn = _db.get_connection()
        try:
            return conn.execute("SELECT market_cap FROM stock_signals WHERE ticker=?", (self.TICKER,)).fetchone()["market_cap"]
        finally:
            conn.close()

    def test_stores_market_cap_from_the_dump_and_null_when_the_dump_lacks_it(self):
        _upsert_fundamentals(self.TICKER, {"shortName": "Cap Co", "currency": "USD", "marketCap": 3.2e9})
        assert self._stored() == 3.2e9

        _upsert_fundamentals(self.TICKER, {"shortName": "Cap Co", "currency": "USD"})
        assert self._stored() is None


def _write_aged_dump(directory, ticker, payload, days_old):
    path = directory / f"{ticker}.json"
    path.write_text(json.dumps(payload))
    written = (datetime.now(timezone.utc) - timedelta(days=days_old)).timestamp()
    os.utime(path, (written, written))
    return path


class TestFetchInfoRefreshesAnAgedDump:
    """The Universe Deep Sync read data/fundamentals/{ticker}.json whatever its age, so universe-only
    tickers carried a six-month-old `.info` quote (CCEP.L at 6925 GBp against an 8875 close) and
    fundamentals into stock_signals every week."""

    TICKER = "ZZINFOAGE.L"

    def _write_dump(self, directory, payload, days_old):
        return _write_aged_dump(directory, self.TICKER, payload, days_old)

    def test_a_young_dump_is_used_without_a_lookup(self, tmp_path):
        import universe_fundamentals_engine as engine

        self._write_dump(tmp_path, {"quoteType": "EQUITY", "regularMarketPrice": 8890.0}, days_old=1)
        with patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine.yahoo_engine, "get_ticker_info") as lookup:
            assert engine._fetch_info(self.TICKER)["regularMarketPrice"] == 8890.0
        lookup.assert_not_called()

    def test_an_aged_dump_is_fetched_again_and_replaced(self, tmp_path):
        import universe_fundamentals_engine as engine

        path = self._write_dump(tmp_path, {"quoteType": "EQUITY", "regularMarketPrice": 6925.0}, days_old=FUNDAMENTALS_REFRESH_DAYS + 1)
        fresh = {"quoteType": "EQUITY", "regularMarketPrice": 8890.0}
        with patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine.yahoo_engine, "get_ticker_info", return_value=fresh):
            assert engine._fetch_info(self.TICKER) == fresh
        assert json.loads(path.read_text()) == fresh

    @pytest.mark.parametrize("days_old", [FUNDAMENTALS_REFRESH_DAYS + 1, FUNDAMENTALS_MAX_AGE_DAYS - 1])
    def test_a_failed_fetch_keeps_the_aged_dump(self, tmp_path, days_old):
        import universe_fundamentals_engine as engine

        old = {"quoteType": "EQUITY", "regularMarketPrice": 6925.0}
        path = self._write_dump(tmp_path, old, days_old=days_old)
        with patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine.yahoo_engine, "get_ticker_info", return_value=None) as lookup:
            assert engine._fetch_info(self.TICKER) == old
        lookup.assert_called_once()
        assert json.loads(path.read_text()) == old

    @pytest.mark.parametrize("failure", [{"return_value": None}, {"side_effect": RuntimeError("boom")}])
    def test_a_failed_fetch_does_not_serve_an_over_age_dump_but_keeps_the_file(self, tmp_path, failure):
        import universe_fundamentals_engine as engine

        old = {"quoteType": "EQUITY", "regularMarketPrice": 6925.0}
        path = self._write_dump(tmp_path, old, days_old=FUNDAMENTALS_MAX_AGE_DAYS + 1)
        with patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine.yahoo_engine, "get_ticker_info", **failure):
            assert engine._fetch_info(self.TICKER) == {}
        assert json.loads(path.read_text()) == old


class TestDueFundamentalsTickers:
    def test_missing_index_dumps_come_first_then_the_oldest_in_scope_dumps(self, tmp_path):
        import universe_fundamentals_engine as engine

        aged, old = FUNDAMENTALS_REFRESH_DAYS + 1, FUNDAMENTALS_MAX_AGE_DAYS + 30
        for ticker, days_old in (
            ("AGED", aged), ("OLDEST", old), ("YOUNG", FUNDAMENTALS_REFRESH_DAYS - 1),
            ("OUTSIDE", old), ("BLACKAGED", old), ("IGNAGED", old),
        ):
            _write_aged_dump(tmp_path, ticker, {"quoteType": "EQUITY"}, days_old)
        scope = {"AGED", "OLDEST", "YOUNG", "BLACKAGED", "IGNAGED", "NODUMP", "BLACKNEW", "IGNNEW", "SCOPEDNODUMP"}
        index = ["AGED", "BLACKNEW", "IGNNEW", "NODUMP", "YOUNG"]

        with patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine, "_get_universe_target_tickers", return_value=index) as targets, \
             patch.object(engine, "load_blacklist", return_value={"BLACKAGED", "BLACKNEW"}), \
             patch.object(engine, "ignored_tickers_set", return_value={"IGNAGED", "IGNNEW"}):
            due = engine._due_fundamentals_tickers(scope)

        assert due == ["NODUMP", "OLDEST", "AGED"]
        targets.assert_called_once_with(freetrade_firewall=False)

    def test_portfolio_and_watchlist_tickers_without_a_dump_are_due_but_other_tickers_are_not(self, tmp_path):
        import universe_fundamentals_engine as engine

        with patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine, "_get_universe_target_tickers", return_value=["INDEXED"]), \
             patch.object(engine, "get_portfolio_watchlist_tickers", return_value=["HELD"]), \
             patch.object(engine, "load_blacklist", return_value=set()), \
             patch.object(engine, "ignored_tickers_set", return_value=set()):
            due = engine._due_fundamentals_tickers({"INDEXED", "HELD", "OTHER"})

        assert sorted(due) == ["HELD", "INDEXED"]


class TestRefreshDueFundamentals:
    @pytest.fixture
    def run(self):
        """The refresh with its scope, due list, fetch, rate-limit breaker and clock patched; `parent` records their call order."""
        import universe_fundamentals_engine as engine

        parent = MagicMock()
        state = {"trips": 7}
        with patch("quant_signals.QuantEngine._scan_scope", return_value={"AAA"}) as scope, \
             patch.object(engine, "_due_fundamentals_tickers", return_value=["AAA", "BBB", "CCC", "DDD"]) as due, \
             patch.object(engine, "_fetch_info") as fetch, \
             patch.object(engine, "yahoo_rate_limit_trips", side_effect=lambda: state["trips"]), \
             patch.object(engine, "wait_for_yahoo_rate_limit_reset") as wait, \
             patch.object(engine, "time") as clock:
            parent.attach_mock(fetch, "fetch")
            parent.attach_mock(wait, "wait")
            parent.attach_mock(clock.sleep, "sleep")
            yield SimpleNamespace(engine=engine, scope=scope, due=due, fetch=fetch, wait=wait, sleep=clock.sleep, calls=parent, state=state)

    def test_refetches_the_aged_and_missing_dumps_only(self, tmp_path, caplog):
        import universe_fundamentals_engine as engine

        _write_aged_dump(tmp_path, "AGED", {"quoteType": "EQUITY", "beta": 1.0}, FUNDAMENTALS_MAX_AGE_DAYS + 1)
        young = _write_aged_dump(tmp_path, "YOUNG", {"quoteType": "EQUITY", "beta": 1.0}, 1)
        fresh = {"quoteType": "EQUITY", "beta": 2.0}
        with patch("quant_signals.QuantEngine._scan_scope", return_value={"AGED", "YOUNG", "NODUMP"}), \
             patch.object(engine, "FUNDAMENTALS_DIR", tmp_path), \
             patch.object(engine, "_get_universe_target_tickers", return_value=["NODUMP"]), \
             patch.object(engine, "load_blacklist", return_value=set()), \
             patch.object(engine, "ignored_tickers_set", return_value=set()), \
             patch.object(engine.yahoo_engine, "get_ticker_info", return_value=fresh) as lookup, \
             patch.object(engine, "time"), \
             caplog.at_level("INFO", logger="universe_fundamentals_engine"):
            engine.refresh_due_fundamentals()

        assert lookup.call_args_list == [call("NODUMP"), call("AGED")]
        assert json.loads((tmp_path / "AGED.json").read_text()) == fresh
        assert json.loads((tmp_path / "NODUMP.json").read_text()) == fresh
        assert json.loads(young.read_text())["beta"] == 1.0
        assert "2 of 2 due dump(s) refreshed" in caplog.text

    def test_one_rate_limit_waits_for_the_reset_settles_and_continues(self, run):
        def fetch(ticker):
            if ticker == "BBB":
                run.state["trips"] += 1

        run.fetch.side_effect = fetch
        run.engine.refresh_due_fundamentals()

        assert run.calls.mock_calls == [
            call.fetch("AAA"), call.sleep(0.4),
            call.fetch("BBB"), call.wait(), call.sleep(FUNDAMENTALS_REFRESH_SETTLE_SECONDS),
            call.fetch("CCC"), call.sleep(0.4),
            call.fetch("DDD"), call.sleep(0.4),
        ]

    def test_stops_for_the_night_at_the_rate_limit_ceiling(self, run, caplog):
        def fetch(ticker):
            run.state["trips"] += 1

        run.fetch.side_effect = fetch
        with caplog.at_level("WARNING", logger="universe_fundamentals_engine"):
            run.engine.refresh_due_fundamentals()

        assert run.fetch.call_count == FUNDAMENTALS_REFRESH_MAX_RATE_LIMITS
        assert run.wait.call_count == FUNDAMENTALS_REFRESH_MAX_RATE_LIMITS - 1
        assert "Fundamentals refresh stopped" in caplog.text

    def test_a_ticker_that_raises_does_not_stop_the_loop(self, run):
        def fetch(ticker):
            if ticker == "AAA":
                raise OSError("disk full")

        run.fetch.side_effect = fetch
        run.engine.refresh_due_fundamentals()

        assert run.fetch.call_args_list == [call("AAA"), call("BBB"), call("CCC"), call("DDD")]

    @pytest.mark.parametrize("failing", ["scope", "due"])
    def test_a_failed_listing_is_logged_and_never_raises(self, run, caplog, failing):
        getattr(run, failing).side_effect = OSError("unreadable")
        with caplog.at_level("ERROR", logger="universe_fundamentals_engine"):
            run.engine.refresh_due_fundamentals()

        run.fetch.assert_not_called()
        assert "could not list the due dumps" in caplog.text

    @pytest.mark.parametrize("scope", [None, set()])
    def test_without_a_scan_scope_nothing_is_done(self, run, scope):
        run.scope.return_value = scope
        run.engine.refresh_due_fundamentals()

        run.due.assert_not_called()
        run.fetch.assert_not_called()
        run.sleep.assert_not_called()


class TestUpsertFundamentalsLeavesTheBarPrice:
    """stock_signals.current_price is the Quant Scan's last daily close; the weekly `.info` quote only fills a row that has none."""

    TICKER = "ZZUPSERTPRICE.L"

    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker=?", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

    def _price(self):
        conn = _db.get_connection()
        try:
            return conn.execute("SELECT current_price, company_name FROM stock_signals WHERE ticker=?", (self.TICKER,)).fetchone()
        finally:
            conn.close()

    def test_an_existing_price_is_kept(self):
        conn = _db.get_connection()
        try:
            conn.execute("INSERT INTO stock_signals (ticker, current_price) VALUES (?, ?)", (self.TICKER, 8890.0))
            conn.commit()
        finally:
            conn.close()

        _upsert_fundamentals(self.TICKER, {"shortName": "Test Co", "regularMarketPrice": 6925.0})

        row = self._price()
        assert row["company_name"] == "Test Co"
        assert row["current_price"] == 8890.0

    def test_a_missing_price_is_filled(self):
        conn = _db.get_connection()
        try:
            conn.execute("INSERT INTO stock_signals (ticker, piotroski_f_score) VALUES (?, ?)", (self.TICKER, 6.0))
            conn.commit()
        finally:
            conn.close()

        _upsert_fundamentals(self.TICKER, {"shortName": "Test Co", "regularMarketPrice": 6925.0})

        assert self._price()["current_price"] == 6925.0

    def test_a_new_row_takes_the_quote(self):
        _upsert_fundamentals(self.TICKER, {"shortName": "Test Co", "regularMarketPrice": 6925.0})

        assert self._price()["current_price"] == 6925.0
