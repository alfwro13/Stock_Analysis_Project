"""Tests for universe_fundamentals_engine pure functions."""
import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

import database as _db
from universe_fundamentals_engine import (
    _compute_fundamental_score, _clean, _needs_holdings_refresh, sync_etf_holdings_cache,
)


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
            conn.execute("DELETE FROM stock_signals WHERE ticker IN ('TST_ETF', 'TST_EQUITY')")
            conn.commit()
        finally:
            conn.close()

    def test_fetches_and_caches_holdings_for_etf(self):
        _upsert_stock_signal("TST_ETF", "ETF")
        df = pd.DataFrame(
            {"Name": ["Toyota"], "Holding Percent": [0.12]},
            index=pd.Index(["7203.T"], name="Symbol"),
        )
        with patch("universe_fundamentals_engine.yahoo_engine.get_fund_holdings", return_value=df):
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
