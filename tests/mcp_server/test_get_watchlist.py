"""get_watchlist tool: the Watchlist page's tickers with held flagged, key signals from the page's shared signal rows, cache-first price with no Yahoo call, null versus real values, page enrichments, stale signals, empty Watchlist, no write to the database."""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import yahoo_engine
from database import add_transaction, create_account, get_connection
from db_accounts import add_watchlist_item, get_watchlist_account, upsert_holding_price_limit

from .helpers import ACCOUNT_TABLES, call, table_counts, tool_data

HELD, WATCHED, NO_SIGNALS, STALE, BARE = "ZZWLH", "ZZWLW", "ZZWLN", "ZZWLS", "ZZWLB"
_TICKERS = (HELD, WATCHED, NO_SIGNALS, STALE, BARE)


def _stamp(days_ago=0, seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, seconds=seconds_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _day(days_from_now=0):
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def no_yahoo():
    with patch.object(yahoo_engine.yahoo_engine, "get_quote_snapshot", side_effect=AssertionError("get_watchlist must never call Yahoo")):
        yield


@pytest.fixture
def watchlist():
    live_epoch = time.time() - 60
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, sector, currency, quote_type, current_price, ma_50_day, ma_200_day, "
            "trend_50d, trend_200d, rsi_14, overall_signal, composite_score, ml_confidence, next_earnings_date) "
            "VALUES (?, ?, 'Held Corp', 'Technology', 'GBp', 'EQUITY', 120.0, 110.0, 100.0, 'UP', 'UP', 55.0, 'BUY', 70, 66.0, ?)",
            (HELD, _stamp(seconds_ago=3600), _day(20)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, currency, quote_type, current_price, overall_signal, composite_score) "
            "VALUES (?, ?, 'Watched Inc', 'USD', 'EQUITY', 40.0, 'HOLD', 0)",
            (WATCHED, _stamp(seconds_ago=3600)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, currency, quote_type, current_price, ma_50_day, trend_50d, overall_signal, composite_score) "
            "VALUES (?, ?, 'Stale Ltd', 'USD', 'EQUITY', 30.0, 25.0, 'UP', 'SELL', 20)",
            (STALE, _stamp(days_ago=10)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, currency, quote_type, current_price, trend_50d, trend_200d) "
            "VALUES (?, ?, 'Bare Co', 'USD', 'EQUITY', 0.0, 'UP', 'UP')",
            (BARE, _stamp(seconds_ago=3600)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO quant_signals (ticker, date, rsi_14, atr_pct, mom_1m, mom_3m, week52_pct, ml_confidence_score) "
            "VALUES (?, ?, 55.0, 0.02, 0.04, 0.08, 0.75, 72.5)",
            (HELD, _day(-1)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO trap_monitor_results (ticker, phase, bull_trap_level, bear_trap_level, cap_level, wyckoff_level, ema_distance, rsi, scan_ts) "
            "VALUES (?, 'BULL_TRAP_RISK', 'ELEVATED_RISK', 'SAFE', 'SAFE', 'SAFE', 1.0, 60.0, ?)",
            (HELD, _stamp(seconds_ago=600)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
            "VALUES (?, 'Held Corp', 121.0, 1.5, 1.25, 1, ?)",
            (HELD, live_epoch),
        )
        conn.commit()
    finally:
        conn.close()
    account = create_account("WlBookAcc", "GBP", initial_cash=0.0)
    add_transaction(account, "Buy", "2026-01-05", ticker=HELD, currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
    book = get_watchlist_account()
    for ticker in _TICKERS:
        add_watchlist_item(book["id"], ticker, currency="USD", quote_type="EQUITY")
    upsert_holding_price_limit(book["id"], HELD, low_limit=90.0, high_limit=140.0)
    try:
        yield {"account": account, "watchlist": book["id"], "live_epoch": live_epoch}
    finally:
        conn = get_connection()
        try:
            for table in ACCOUNT_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account,))
            conn.execute("DELETE FROM accounts WHERE id = ?", (account,))
            conn.execute("DELETE FROM holding_price_limits WHERE account_id = ?", (book["id"],))
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ?", (book["id"],))
            for table in ("stock_signals", "quant_signals", "market_pulse_cache", "trap_monitor_results"):
                conn.executemany(f"DELETE FROM {table} WHERE ticker = ?", [(t,) for t in _TICKERS])
            conn.commit()
        finally:
            conn.close()


def _watchlist(client):
    return tool_data(call(client, "get_watchlist"))


def _row(data, ticker):
    return next(row for row in data["data"]["tickers"] if row["ticker"] == ticker)


class TestRows:
    def test_envelope_and_every_watchlist_ticker_with_held_flagged(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert data["data"]["count"] == len(data["data"]["tickers"])
        assert {t["ticker"] for t in data["data"]["tickers"]} >= set(_TICKERS)
        assert _row(data, HELD)["held"] is True
        assert all(_row(data, t)["held"] is False for t in (WATCHED, NO_SIGNALS, STALE, BARE))

    def test_rows_match_the_watchlist_page_ticker_list(self, mcp_client, watchlist):
        from database import get_watchlist_tickers
        data = _watchlist(mcp_client)
        assert [t["ticker"] for t in data["data"]["tickers"]] == list(dict.fromkeys(get_watchlist_tickers()))

    def test_signals_come_from_the_page_rows_in_documented_units(self, mcp_client, watchlist):
        row = _row(_watchlist(mcp_client), HELD)
        assert (row["name"], row["sector"], row["currency"]) == ("Held Corp", "Technology", "GBp")
        assert (row["overall_signal"], row["composite_score"]) == ("BUY", 70)
        assert (row["trend_50d"], row["trend_200d"], row["rsi_14"]) == ("UP", "UP", 55.0)
        assert (row["atr_pct"], row["mom_1m"], row["mom_3m"], row["week52_pct"]) == (0.02, 0.04, 0.08, 0.75)
        assert (row["ml_confidence"], row["trap_phase"]) == (72.5, "BULL_TRAP_RISK")
        assert row["position_targets"] == {"low_limit": 90.0, "high_limit": 140.0}
        assert row["next_earnings_date"] == _day(20)

    def test_price_is_regular_session_from_the_shared_cache(self, mcp_client, watchlist):
        row = _row(_watchlist(mcp_client), HELD)
        price = row["price"]
        assert (price["value"], price["currency"], price["source"]) == (121.0, "GBp", "shared_cache")
        assert (price["day_change"], price["day_change_pct"], price["stale"]) == (1.5, 1.25, False)
        assert "extended_hours" not in row

    def test_price_falls_back_to_the_nightly_stock_signals_price(self, mcp_client, watchlist):
        price = _row(_watchlist(mcp_client), WATCHED)["price"]
        assert (price["value"], price["currency"]) == (40.0, "USD")
        assert (price["day_change"], price["day_change_pct"]) == (None, None)

    def test_zero_score_is_kept_and_a_missing_value_is_null(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        watched = _row(data, WATCHED)
        assert watched["composite_score"] == 0
        assert watched["rsi_14"] is None and watched["ml_confidence"] is None and watched["trap_phase"] is None
        assert watched["position_targets"] == {"low_limit": None, "high_limit": None}
        assert watched["next_earnings_date"] is None

    def test_trend_needs_the_average_and_a_price(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        stale = _row(data, STALE)
        assert (stale["trend_50d"], stale["trend_200d"]) == ("UP", None)
        bare = _row(data, BARE)
        assert (bare["trend_50d"], bare["trend_200d"], bare["price"]) == (None, None, None)


class TestMissingAndStale:
    def test_ticker_without_signals_is_all_null_and_listed(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        row = _row(data, NO_SIGNALS)
        assert row["name"] is None and row["price"] is None and row["overall_signal"] is None
        assert row["composite_score"] is None and row["signals_as_of"] is None and row["signals_stale"] is False
        assert any(NO_SIGNALS in line and "no stock_signals row" in line for line in data["missing"])
        assert any(BARE in line and "no cached price" in line for line in data["missing"])
        assert data["stale"] is True

    def test_signals_older_than_five_days_are_flagged_per_row(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        assert _row(data, STALE)["signals_stale"] is True
        assert _row(data, HELD)["signals_stale"] is False
        assert _row(data, HELD)["signals_as_of"].endswith("Z")

    def test_empty_watchlist(self, mcp_client):
        with patch("mcp_server.tools.get_watchlist.get_watchlist_tickers", return_value=[]):
            data = _watchlist(mcp_client)
        assert data["data"] == {"count": 0, "tickers": []}
        assert data["as_of"] is None and data["missing"] == []


class TestEnrichments:
    def test_confluence_recommendation_and_regime_score_are_shaped_from_the_page_functions(self, mcp_client, watchlist):
        confluence = {
            HELD: {"bullish_pillars": ["technical", "ml"], "bearish_pillars": [], "confluence": True, "direction": "bullish"},
            WATCHED: {"bullish_pillars": ["ml"], "bearish_pillars": [], "confluence": False, "direction": None},
        }
        regime = {HELD: {"score": 71.5, "regime": "Bull", "components": {}}}
        recommendation = {HELD: {"recommended": True, "pillar_confluence": confluence[HELD], "risk_reward": {"passes": True, "risk_reward": 2.4}, "regime_weighted_score": regime[HELD]}}
        with patch("mcp_server.tools.get_watchlist.evaluate_pillar_confluence_batch", return_value=confluence), \
             patch("mcp_server.tools.get_watchlist.compute_regime_weighted_score_batch", return_value=regime), \
             patch("mcp_server.tools.get_watchlist.evaluate_buy_recommendation_batch", return_value=recommendation) as batch:
            data = _watchlist(mcp_client)
        held = _row(data, HELD)
        assert held["pillar_confluence"] == {"direction": "bullish", "pillars": ["technical", "ml"]}
        assert held["buy_recommendation"] == {"recommended": True, "risk_reward": 2.4}
        assert held["regime_weighted_score"] == {"score": 71.5, "regime": "Bull"}
        watched = _row(data, WATCHED)
        assert (watched["pillar_confluence"], watched["buy_recommendation"], watched["regime_weighted_score"]) == (None, None, None)
        assert batch.call_args.kwargs == {"confluence_by_ticker": confluence, "regime_score_by_ticker": regime}

    def test_units_explain_that_null_confluence_is_not_missing_data(self, mcp_client, watchlist):
        units = _watchlist(mcp_client)["units"]
        assert "not missing data" in units["pillar_confluence"]
        assert "Crash" in units["regime_weighted_score"]


class TestReadOnly:
    def test_tool_leaves_the_database_unchanged(self, mcp_client, watchlist):
        before = table_counts()
        _watchlist(mcp_client)
        assert table_counts() == before
