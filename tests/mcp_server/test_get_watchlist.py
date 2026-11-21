"""get_watchlist tool: the Watchlist page's tickers with held flagged, key signals from the page's shared signal rows, cache-first price with no Yahoo call, null versus real values, page enrichments, stale signals, empty Watchlist, filters, paging, compact rows and refused caps, no write to the database."""
import contextlib
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import yahoo_engine
from db.database import add_transaction, create_account, get_connection
from db.account_store import add_watchlist_item, get_watchlist_account, upsert_holding_price_limit

from constants import MCP_WATCHLIST_DEFAULT_ROWS, MCP_WATCHLIST_MAX_OFFSET, MCP_WATCHLIST_MAX_ROWS

from .helpers import ACCOUNT_TABLES, call, error_text, rpc, table_counts, tool_data

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
            "INSERT OR REPLACE INTO quant_signals (ticker, date, rsi_14, atr_pct, mom_1m, mom_3m, week52_pct, ml_confidence_score, ml_scored_at) "
            "VALUES (?, ?, 55.0, 0.02, 0.04, 0.08, 0.75, 72.5, datetime('now'))",
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


@contextlib.contextmanager
def _sessions(open_exchanges=(), last_close_ago=7200):
    last_close = datetime.now(timezone.utc) - timedelta(seconds=last_close_ago)
    with patch("market_session_helpers.is_trading_session", side_effect=lambda exchange, include_premarket=False: exchange in open_exchanges), \
         patch("market_session_helpers.last_settled_session_close_utc", return_value=last_close):
        yield


def _seed_cache(ticker, price, seconds_ago):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, 'W', ?, 0.5, 0.3, 1, ?)",
            (ticker, price, time.time() - seconds_ago),
        )
        conn.commit()
    finally:
        conn.close()


def _watchlist(client, **arguments):
    return tool_data(call(client, "get_watchlist", {"limit": MCP_WATCHLIST_MAX_ROWS, **arguments}))


def _tickers(data):
    return [row["ticker"] for row in data["data"]["tickers"]]


def _row(data, ticker):
    return next(row for row in data["data"]["tickers"] if row["ticker"] == ticker)


class TestRows:
    def test_envelope_and_every_watchlist_ticker_with_held_flagged(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "incomplete", "missing", "data"}
        assert data["data"]["count"] == len(data["data"]["tickers"])
        assert {t["ticker"] for t in data["data"]["tickers"]} >= set(_TICKERS)
        assert _row(data, HELD)["held"] is True
        assert all(_row(data, t)["held"] is False for t in (WATCHED, NO_SIGNALS, STALE, BARE))

    def test_rows_match_the_watchlist_page_ticker_list(self, mcp_client, watchlist):
        from db.database import get_watchlist_tickers
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

    def test_a_past_earnings_date_is_null_because_it_is_the_last_report(self, mcp_client, watchlist):
        conn = get_connection()
        try:
            conn.execute("UPDATE stock_signals SET next_earnings_date = ? WHERE ticker = ?", (_day(-3), HELD))
            conn.commit()
        finally:
            conn.close()
        assert _row(_watchlist(mcp_client), HELD)["next_earnings_date"] is None

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

    def test_a_cached_quote_from_before_the_tickers_last_close_gives_way_to_the_nightly_price_with_its_own_as_of(self, mcp_client, watchlist):
        _seed_cache(WATCHED, 36.0, 5 * 3600)
        with _sessions(last_close_ago=7200):
            price = _row(_watchlist(mcp_client), WATCHED)["price"]
        assert (price["value"], price["stale"]) == (40.0, False)
        assert (price["day_change"], price["day_change_pct"]) == (None, None)
        nightly = datetime.now(timezone.utc) - timedelta(seconds=3600)
        assert abs(datetime.strptime(price["as_of"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - nightly) < timedelta(seconds=10)

    def test_a_cached_quote_from_before_the_last_close_with_no_newer_nightly_price_is_stale(self, mcp_client, watchlist):
        _seed_cache(WATCHED, 36.0, 5 * 3600)
        with _sessions(last_close_ago=1800):
            price = _row(_watchlist(mcp_client), WATCHED)["price"]
        assert (price["value"], price["stale"]) == (36.0, True)

    def test_price_staleness_follows_the_tickers_own_exchange(self, mcp_client, watchlist):
        _seed_cache(WATCHED, 36.0, 600)
        with _sessions(open_exchanges=("NYSE",)):
            assert _row(_watchlist(mcp_client), WATCHED)["price"]["stale"] is True
        with _sessions(open_exchanges=("LSE",)):
            assert _row(_watchlist(mcp_client), WATCHED)["price"]["stale"] is False

    def test_the_nightly_price_is_stale_while_its_exchange_is_open_and_fresh_after_the_close(self, mcp_client, watchlist):
        with _sessions(open_exchanges=("NYSE",)):
            assert _row(_watchlist(mcp_client), WATCHED)["price"]["stale"] is True
        with _sessions(last_close_ago=7200):
            assert _row(_watchlist(mcp_client), WATCHED)["price"]["stale"] is False

    def test_units_state_that_staleness_is_judged_on_the_tickers_own_exchange(self, mcp_client, watchlist):
        assert "own exchange" in _watchlist(mcp_client)["units"]["price.stale"]

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
        assert data["incomplete"] is True

    def test_signals_older_than_five_days_are_flagged_per_row(self, mcp_client, watchlist):
        data = _watchlist(mcp_client)
        assert _row(data, STALE)["signals_stale"] is True
        assert _row(data, HELD)["signals_stale"] is False
        assert _row(data, HELD)["signals_as_of"].endswith("Z")

    def test_empty_watchlist(self, mcp_client):
        with patch("mcp_server.tools.get_watchlist.get_watchlist_tickers", return_value=[]):
            data = _watchlist(mcp_client)
        assert data["data"] == {
            "count": 0, "total_matches": 0, "truncated": False, "next_offset": None,
            "bullish_confluence_count": 0, "buy_recommended_count": 0, "tickers": [],
        }
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


class TestPayloadControls:
    def test_schema_has_only_optional_arguments_and_the_caps(self, mcp_client):
        tool = {t["name"]: t for t in rpc(mcp_client, "tools/list").json()["result"]["tools"]}["get_watchlist"]
        properties = tool["inputSchema"]["properties"]
        assert tool["inputSchema"].get("required", []) == []
        assert (properties["limit"]["maximum"], properties["limit"]["default"]) == (MCP_WATCHLIST_MAX_ROWS, MCP_WATCHLIST_DEFAULT_ROWS)
        assert properties["offset"]["maximum"] == MCP_WATCHLIST_MAX_OFFSET
        assert set(properties) == {"scope", "min_composite_score", "only_buy_recommended", "limit", "offset", "compact"}

    @pytest.mark.parametrize("arguments, name", [
        ({"limit": MCP_WATCHLIST_MAX_ROWS + 1}, "limit"), ({"limit": 0}, "limit"), ({"offset": -1}, "offset"),
        ({"offset": MCP_WATCHLIST_MAX_OFFSET + 1}, "offset"), ({"min_composite_score": 101}, "min_composite_score"), ({"min_composite_score": -101}, "min_composite_score"),
        ({"scope": "everything"}, "scope"),
    ])
    def test_a_value_past_a_cap_is_refused_not_clamped(self, mcp_client, watchlist, arguments, name):
        assert name in error_text(call(mcp_client, "get_watchlist", arguments))

    def test_no_arguments_returns_the_first_default_page(self, mcp_client, watchlist):
        data = tool_data(call(mcp_client, "get_watchlist"))
        assert data["data"]["count"] == len(data["data"]["tickers"]) <= MCP_WATCHLIST_DEFAULT_ROWS
        assert data["data"]["total_matches"] >= len(_TICKERS)

    def test_pages_follow_the_watchlist_order_and_cover_every_ticker_once(self, mcp_client, watchlist):
        full = _tickers(_watchlist(mcp_client))
        seen, offset = [], 0
        while offset is not None:
            page = _watchlist(mcp_client, limit=2, offset=offset)
            assert page["data"]["total_matches"] == len(full)
            assert page["data"]["count"] == len(page["data"]["tickers"]) <= 2
            assert page["data"]["truncated"] is (page["data"]["next_offset"] is not None)
            seen += _tickers(page)
            offset = page["data"]["next_offset"]
        assert seen == full

    def test_an_offset_past_the_end_is_an_empty_last_page(self, mcp_client, watchlist):
        data = _watchlist(mcp_client, offset=MCP_WATCHLIST_MAX_OFFSET)["data"]
        assert data["tickers"] == [] and data["count"] == 0
        assert data["total_matches"] > 0 and data["truncated"] is False and data["next_offset"] is None

    def test_scope_keeps_held_or_not_held_tickers(self, mcp_client, watchlist):
        held = _watchlist(mcp_client, scope="held")
        assert HELD in _tickers(held)
        assert all(row["held"] is True for row in held["data"]["tickers"])
        not_held = _watchlist(mcp_client, scope="watchlist_only")
        assert HELD not in _tickers(not_held)
        assert {WATCHED, NO_SIGNALS, STALE, BARE} <= set(_tickers(not_held))
        assert all(row["held"] is False for row in not_held["data"]["tickers"])
        assert held["data"]["total_matches"] + not_held["data"]["total_matches"] == _watchlist(mcp_client)["data"]["total_matches"]

    def test_min_composite_score_leaves_out_lower_and_missing_scores(self, mcp_client, watchlist):
        assert set(_tickers(_watchlist(mcp_client, min_composite_score=60))) & set(_TICKERS) == {HELD}
        kept = set(_tickers(_watchlist(mcp_client, min_composite_score=0))) & set(_TICKERS)
        assert kept == {HELD, WATCHED, STALE}

    def test_min_composite_score_accepts_a_negative_floor(self, mcp_client, watchlist):
        conn = get_connection()
        try:
            conn.execute("UPDATE stock_signals SET composite_score = -20 WHERE ticker = ?", (STALE,))
            conn.commit()
        finally:
            conn.close()
        assert STALE in set(_tickers(_watchlist(mcp_client, min_composite_score=-20)))
        assert STALE not in set(_tickers(_watchlist(mcp_client, min_composite_score=-19)))
        assert STALE not in set(_tickers(_watchlist(mcp_client, min_composite_score=0)))

    def test_only_buy_recommended_filters_before_paging(self, mcp_client, watchlist):
        recommendation = {"recommended": True, "pillar_confluence": None, "risk_reward": {"passes": True, "risk_reward": 2.0}, "regime_weighted_score": None}
        results = {HELD: recommendation, WATCHED: {**recommendation, "recommended": False}}
        with patch("mcp_server.tools.get_watchlist.evaluate_buy_recommendation_batch", return_value=results) as batch:
            data = _watchlist(mcp_client, only_buy_recommended=True, limit=1)
        assert _tickers(data) == [HELD]
        assert (data["data"]["total_matches"], data["data"]["truncated"]) == (1, False)
        assert set(_TICKERS) <= set(batch.call_args.args[0])

    def test_counts_cover_every_matching_ticker_not_only_the_returned_page(self, mcp_client, watchlist):
        bullish = {"bullish_pillars": ["technical", "ml"], "bearish_pillars": [], "confluence": True, "direction": "bullish"}
        bearish = {"bullish_pillars": [], "bearish_pillars": ["technical", "ml"], "confluence": True, "direction": "bearish"}
        confluence = {HELD: bullish, WATCHED: bullish, STALE: bearish}
        recommendation = {"recommended": True, "pillar_confluence": bullish, "risk_reward": {"passes": True, "risk_reward": 2.0}, "regime_weighted_score": None}
        results = {HELD: recommendation, WATCHED: {**recommendation, "recommended": False}}
        with patch("mcp_server.tools.get_watchlist.evaluate_pillar_confluence_batch", return_value=confluence) as batch, \
             patch("mcp_server.tools.get_watchlist.evaluate_buy_recommendation_batch", return_value=results):
            data = _watchlist(mcp_client, limit=1)
            narrowed = _watchlist(mcp_client, limit=1, only_buy_recommended=True)
        assert data["data"]["count"] == 1
        assert (data["data"]["bullish_confluence_count"], data["data"]["buy_recommended_count"]) == (2, 1)
        assert (narrowed["data"]["bullish_confluence_count"], narrowed["data"]["buy_recommended_count"]) == (2, 1)
        assert set(_TICKERS) <= set(batch.call_args.args[0])

    def test_counts_are_zero_when_nothing_is_bullish(self, mcp_client, watchlist):
        with patch("mcp_server.tools.get_watchlist.evaluate_pillar_confluence_batch", return_value={}):
            data = _watchlist(mcp_client)
        assert (data["data"]["bullish_confluence_count"], data["data"]["buy_recommended_count"]) == (0, 0)
        assert "bullish_confluence_count" in data["units"]["bullish_confluence_count/buy_recommended_count"]

    def test_compact_leaves_out_null_fields_and_keeps_real_values(self, mcp_client, watchlist):
        full = _row(_watchlist(mcp_client), WATCHED)
        compact = _row(_watchlist(mcp_client, compact=True), WATCHED)
        assert compact["composite_score"] == 0 and compact["held"] is False
        assert "rsi_14" not in compact and "position_targets" not in compact and "pillar_confluence" not in compact
        assert full["price"]["day_change"] is None and "day_change" not in compact["price"]
        assert compact["price"]["value"] == full["price"]["value"]
        held = _row(_watchlist(mcp_client, compact=True), HELD)
        assert held["position_targets"] == {"low_limit": 90.0, "high_limit": 140.0}
        assert all(value is not None for value in held.values())

    def test_compact_row_of_a_ticker_without_signals_keeps_only_what_exists(self, mcp_client, watchlist):
        assert _row(_watchlist(mcp_client, compact=True), NO_SIGNALS) == {"ticker": NO_SIGNALS, "held": False, "signals_stale": False}

    def test_missing_names_only_the_tickers_on_the_returned_page(self, mcp_client, watchlist):
        data = _watchlist(mcp_client, scope="held")
        assert NO_SIGNALS not in " ".join(data["missing"])

    def test_units_explain_the_filters_paging_and_compact(self, mcp_client, watchlist):
        units = _watchlist(mcp_client)["units"]
        assert "next_offset" in units["limit/offset"] and "refused" in units["limit/offset"]
        assert "never zero" in units["compact"]
        assert "never matches" in units["filters"]
