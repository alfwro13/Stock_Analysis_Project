"""get_ticker_snapshot tool: held vs watchlist-only vs universe-only vs unknown tickers, cache-first price with a Yahoo fallback, extended hours kept apart, documented units, stale and missing parts, no write to the database."""
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import page_data_stock
from database import add_transaction, create_account, get_connection
from db_accounts import add_watchlist_item, get_watchlist_account, upsert_holding_price_limit

from .helpers import ACCOUNT_TABLES, EXPECTED_TOOLS, call, error_text, table_counts, tool_data

HELD, WATCHED, UNIVERSE, EMPTY_HELD, THIN = "ZZSNAPH", "ZZSNAPW", "ZZSNAPQ", "ZZSNAPN", "ZZSNAPT"
_TICKERS = (HELD, WATCHED, UNIVERSE, EMPTY_HELD, THIN)
_SNAPSHOT = {
    "market_state": "REGULAR", "regular_price": 101.5, "regular_change": 1.5, "regular_change_pct": 1.5, "regular_previous_close": 100.0,
    "pre_market_price": 103.0, "pre_market_change": 3.0, "pre_market_change_pct": 3.0,
    "post_market_price": 99.0, "post_market_change": -1.0, "post_market_change_pct": -1.0,
}


def _utc(days_ago=0, seconds_ago=0):
    return datetime.now(timezone.utc) - timedelta(days=days_ago, seconds=seconds_ago)


def _stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _day(days_ago=0):
    return _utc(days_ago).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def yahoo():
    with patch("mcp_server.tools.get_ticker_snapshot.yahoo_engine") as engine:
        engine.get_quote_snapshot.side_effect = AssertionError("a test that expects a Yahoo call must set a return value")
        yield engine


@pytest.fixture
def fundamentals_dir(tmp_path):
    with patch("config.FUNDAMENTALS_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def book(fundamentals_dir):
    live_epoch = time.time() - 60
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, sector, currency, country, quote_type, current_price, "
            "trend_50d, trend_200d, rsi_14, atr_stop_loss, fifty_two_week_low, fifty_two_week_high, trailing_pe, forward_pe, peg_ratio, "
            "peter_lynch_peg, price_to_book, price_to_sales, free_cash_flow, profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, "
            "operating_cash_flow, dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date, short_interest, "
            "institutional_ownership, beta, overall_signal, composite_score, piotroski_f_score, altman_z_score, beneish_m_score, forensic_last_updated) "
            "VALUES (?, ?, 'Held Corp', 'Technology', 'GBp', 'United Kingdom', 'EQUITY', 120.0, 'UP', 'UP', 55.0, 95.0, 80.0, 140.0, 18.0, 15.0, "
            "1.2, 1.1, 3.0, 2.5, 5e8, 0.21, 0.15, 0.08, 30.0, 1.8, 7e8, 0.03, '2026-09-01', 150.0, 'buy', ?, 0.02, 0.6, 1.1, 'BUY', 70, "
            "8, 3.4, -2.5, ?)",
            (HELD, _stamp(_utc(seconds_ago=3600)), _day(-20), _stamp(_utc(days_ago=10))),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, currency, quote_type, current_price, overall_signal) "
            "VALUES (?, ?, 'Watched Inc', 'USD', 'EQUITY', 40.0, 'HOLD')",
            (WATCHED, _stamp(_utc(seconds_ago=3600))),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, last_updated, company_name, currency, quote_type, current_price, overall_signal) "
            "VALUES (?, ?, 'Thin Corp', 'USD', 'EQUITY', 0.0, 'HOLD')",
            (THIN, _stamp(_utc(seconds_ago=3600))),
        )
        for ticker, close in ((HELD, 119.0), (UNIVERSE, 12.0)):
            conn.execute(
                "INSERT OR REPLACE INTO quant_signals (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist, sma_50, sma_200, "
                "volume_surge, bullish_cross, ml_confidence_score, mom_1m, mom_3m, mom_6m, mom_12m_skip1m, atr_pct, hist_vol_20, "
                "rel_strength_5d, rel_strength_20d, price_q10, price_q90) "
                "VALUES (?, ?, ?, 1000, 55.0, 1.2, 1.0, 0.2, 110.0, 100.0, 1, 0, 72.5, 0.04, 0.08, 0.12, 0.2, 0.02, 0.25, 0.01, 0.03, 100.0, 140.0)",
                (ticker, _day(1), close),
            )
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated, "
            "extended_price, extended_change_pts, extended_change_pct, extended_session) VALUES (?, 'Held Corp', 121.0, 1.5, 1.25, 1, ?, 125.0, 4.0, 3.3, 'pre')",
            (HELD, live_epoch),
        )
        conn.commit()
    finally:
        conn.close()
    held_account = create_account("SnapAccA", "GBP", initial_cash=0.0)
    other_account = create_account("SnapAccB", "GBP", initial_cash=0.0)
    add_transaction(held_account, "Buy", "2026-01-05", ticker=HELD, currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(other_account, "Buy", "2026-01-06", ticker=HELD, currency="GBP", quantity=2, unit_price=110, exchange_rate=1.0)
    add_transaction(other_account, "Buy", "2026-01-06", ticker=EMPTY_HELD, currency="GBP", quantity=3, unit_price=5, exchange_rate=1.0)
    watchlist = get_watchlist_account()
    add_watchlist_item(watchlist["id"], WATCHED, currency="USD", quote_type="EQUITY")
    add_watchlist_item(watchlist["id"], HELD, currency="GBp", quote_type="EQUITY")
    upsert_holding_price_limit(held_account, HELD, low_limit=100.0, high_limit=150.0)
    upsert_holding_price_limit(watchlist["id"], HELD, low_limit=90.0)
    ids = {"held": held_account, "other": other_account, "watchlist": watchlist["id"], "live_epoch": live_epoch}
    try:
        yield ids
    finally:
        conn = get_connection()
        try:
            for account_id in (held_account, other_account):
                for table in ACCOUNT_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM holding_price_limits WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.execute("DELETE FROM holding_price_limits WHERE account_id = ?", (watchlist["id"],))
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ?", (watchlist["id"],))
            for table in ("stock_signals", "quant_signals", "market_pulse_cache"):
                conn.executemany(f"DELETE FROM {table} WHERE ticker = ?", [(t,) for t in _TICKERS])
            conn.commit()
        finally:
            conn.close()


def _snapshot(client, ticker=HELD):
    return tool_data(call(client, "get_ticker_snapshot", {"ticker": ticker}))


class TestHeldTicker:
    def test_envelope_and_coverage(self, mcp_client, book):
        data = _snapshot(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        body = data["data"]
        assert (body["ticker"], body["name"], body["coverage"]) == (HELD, "Held Corp", "full")
        assert (body["sector"], body["country"], body["quote_type"]) == ("Technology", "United Kingdom", "EQUITY")
        assert data["missing"] == []

    def test_price_comes_from_the_shared_cache_and_never_calls_yahoo(self, mcp_client, book, yahoo):
        price = _snapshot(mcp_client)["data"]["price"]
        assert (price["value"], price["currency"], price["source"]) == (121.0, "GBp", "shared_cache")
        assert (price["day_change"], price["day_change_pct"]) == (1.5, 1.25)
        assert price["stale"] is False
        assert price["as_of"] == datetime.fromtimestamp(book["live_epoch"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        yahoo.get_quote_snapshot.assert_not_called()

    def test_extended_hours_is_a_separate_object_never_merged_into_the_price(self, mcp_client, book):
        body = _snapshot(mcp_client)["data"]
        assert body["price"]["value"] == 121.0 and body["price"]["day_change_pct"] == 1.25
        assert body["extended_hours"] == {"session": "pre", "price": 125.0, "change": 4.0, "change_pct": 3.3}

    def test_trend_and_technicals_use_the_documented_units(self, mcp_client, book):
        body = _snapshot(mcp_client)["data"]
        assert body["trend"]["trend_50d"] == "UP" and body["trend"]["sma_50"] == 110.0 and body["trend"]["sma_200"] == 100.0
        assert (body["trend"]["fifty_two_week_low"], body["trend"]["fifty_two_week_high"], body["trend"]["atr_stop_loss"]) == (80.0, 140.0, 95.0)
        technicals = body["technicals"]
        assert (technicals["rsi_14"], technicals["atr_pct"], technicals["mom_1m"], technicals["hist_vol_20"]) == (55.0, 0.02, 0.04, 0.25)
        assert (technicals["volume_surge"], technicals["bullish_cross"], technicals["close_price"]) == (True, False, 119.0)

    def test_fundamentals_keep_roe_as_a_fraction_and_debt_to_equity_on_yahoos_scale(self, mcp_client, book):
        data = _snapshot(mcp_client)
        fundamentals = data["data"]["fundamentals"]
        assert (fundamentals["roe"], fundamentals["debt_to_equity"], fundamentals["profit_margin"]) == (0.15, 30.0, 0.21)
        assert data["units"]["fundamentals"].count("are fractions (0.15 = 15%)") and data["units"]["fundamentals"].count("30 means 30% (not 0.30)")
        assert data["units"]["price.value"].count("GBp means pence")

    def test_valuation_forensic_ml_and_earnings(self, mcp_client, book):
        body = _snapshot(mcp_client)["data"]
        valuation = body["valuation"]
        assert (valuation["trailing_pe"], valuation["dividend_yield"], valuation["target_price"], valuation["ex_dividend_date"]) == (18.0, 0.03, 150.0, "2026-09-01")
        forensic = body["forensic_scores"]
        assert (forensic["piotroski_f_score"], forensic["altman_z_score"], forensic["beneish_m_score"]) == (8, 3.4, -2.5)
        assert forensic["as_of"] is not None
        assert body["ml"]["confidence"] == 72.5 and (body["ml"]["price_q10"], body["ml"]["price_q90"]) == (100.0, 140.0)
        assert body["next_earnings"]["date"] == _day(-20)

    def test_cached_company_dump_adds_raw_numbers_without_the_website(self, mcp_client, book, fundamentals_dir):
        (fundamentals_dir / f"{HELD}.json").write_text(json.dumps({
            "marketCap": 12865473536, "totalCash": 855, "totalDebt": 300, "financialCurrency": "USD", "trailingEps": 0.14,
            "exDividendDate": 1781740800, "fullTimeEmployees": 1200, "website": "https://example.com", "averageVolume": 6772679,
        }))
        extra = _snapshot(mcp_client)["data"]["fundamentals"]["extra"]
        assert (extra["market_cap"], extra["net_cash"], extra["financial_currency"], extra["trailing_eps"]) == (12865473536, 555, "USD", 0.14)
        assert (extra["ex_dividend_date"], extra["full_time_employees"], extra["average_volume"]) == ("2026-06-18", 1200, 6772679)
        assert "website" not in extra

    def test_missing_company_dump_is_null(self, mcp_client, book):
        assert _snapshot(mcp_client)["data"]["fundamentals"]["extra"] is None

    def test_position_and_targets_cover_each_holding_account_and_the_watchlist(self, mcp_client, book):
        body = _snapshot(mcp_client)["data"]
        assert body["position"] == {"held": True, "in_watchlist": True, "total_shares": 12}
        rows = {r["account_name"]: r for r in body["position_targets"]}
        assert rows["SnapAccA"] == {
            "account_type": "Trading", "scope_id": f"acct:{book['held']}", "account_name": "SnapAccA", "shares": 10,
            "low_limit": 100.0, "high_limit": 150.0,
        }
        assert (rows["SnapAccB"]["low_limit"], rows["SnapAccB"]["high_limit"]) == (None, None)
        watchlist = next(r for r in body["position_targets"] if r["account_type"] == "Watchlist")
        assert (watchlist["scope_id"], watchlist["shares"], watchlist["low_limit"], watchlist["high_limit"]) == (None, None, 90.0, None)

    def test_lowercase_input_is_matched_and_surrounding_space_trimmed(self, mcp_client, book):
        assert _snapshot(mcp_client, f"  {HELD.lower()} ")["data"]["ticker"] == HELD


class TestWatchlistOnlyTicker:
    def test_is_served_with_a_watchlist_target_row_and_no_position(self, mcp_client, book):
        data = _snapshot(mcp_client, WATCHED)
        body = data["data"]
        assert body["position"] == {"held": False, "in_watchlist": True, "total_shares": None}
        assert [r["account_type"] for r in body["position_targets"]] == ["Watchlist"]
        assert body["price"]["value"] == 40.0 and body["price"]["currency"] == "USD" and body["price"]["source"] == "shared_cache"
        assert body["price"]["day_change_pct"] is None and body["extended_hours"] is None
        assert body["technicals"]["rsi_14"] is None
        assert any("No quant_signals row" in item for item in data["missing"])


class TestUniverseOnlyTicker:
    def test_serves_technicals_only_and_calls_yahoo_for_the_missing_price(self, mcp_client, book, yahoo):
        yahoo.get_quote_snapshot.side_effect = None
        yahoo.get_quote_snapshot.return_value = _SNAPSHOT
        data = _snapshot(mcp_client, UNIVERSE)
        body = data["data"]
        assert body["coverage"] == "universe_scan_only"
        assert body["technicals"]["rsi_14"] == 55.0 and body["technicals"]["close_price"] == 12.0
        assert body["trend"]["trend_50d"] == "DOWN" and body["trend"]["sma_50"] == 110.0
        assert body["valuation"] is None and body["fundamentals"] is None and body["forensic_scores"] is None and body["next_earnings"] is None
        assert body["position"]["held"] is False and body["position_targets"] == []
        assert body["quote_type"] is None and body["country"] is None
        assert body["price"]["source"] == "yahoo_live" and body["price"]["currency"] is None
        assert any("Universe scan only" in item for item in data["missing"]) and data["stale"] is True
        yahoo.get_quote_snapshot.assert_called_once_with(UNIVERSE)

    def test_yahoo_price_keeps_regular_and_extended_values_apart_by_yahoos_own_market_state(self, mcp_client, book, yahoo):
        yahoo.get_quote_snapshot.side_effect = None
        for state, expected in (
            ("REGULAR", None),
            ("PRE", {"session": "pre", "price": 103.0, "change": 3.0, "change_pct": 3.0}),
            ("POST", {"session": "post", "price": 99.0, "change": -1.0, "change_pct": -1.0}),
        ):
            yahoo.get_quote_snapshot.return_value = {**_SNAPSHOT, "market_state": state}
            body = _snapshot(mcp_client, UNIVERSE)["data"]
            assert body["price"]["value"] == 101.5 and body["price"]["day_change_pct"] == 1.5
            assert (body["price"]["previous_close"], body["price"]["market_state"]) == (100.0, state)
            assert body["extended_hours"] == expected

    def test_a_failed_yahoo_quote_gives_a_null_price_and_a_marker(self, mcp_client, book, yahoo):
        yahoo.get_quote_snapshot.side_effect = None
        yahoo.get_quote_snapshot.return_value = None
        data = _snapshot(mcp_client, UNIVERSE)
        assert data["data"]["price"] is None and data["data"]["extended_hours"] is None
        assert any("price is null" in item for item in data["missing"]) and data["stale"] is True

    def test_a_placeholder_cache_row_does_not_count_as_a_cached_price(self, mcp_client, book, yahoo):
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
                "VALUES (?, 'x', 0.0, 0.0, 0.0, 1, 0)", (UNIVERSE,),
            )
            conn.commit()
        finally:
            conn.close()
        yahoo.get_quote_snapshot.side_effect = None
        yahoo.get_quote_snapshot.return_value = _SNAPSHOT
        assert _snapshot(mcp_client, UNIVERSE)["data"]["price"]["source"] == "yahoo_live"


class TestHeldTickerWithoutData:
    def test_returns_an_explicit_empty_snapshot_and_never_asks_yahoo_for_a_synthetic_ticker(self, mcp_client, book, yahoo):
        yahoo.get_quote_snapshot.side_effect = None
        yahoo.get_quote_snapshot.return_value = _SNAPSHOT
        data = _snapshot(mcp_client, EMPTY_HELD)
        body = data["data"]
        assert body["coverage"] == "none" and body["name"] is None
        assert all(body[part] is None for part in ("trend", "technicals", "valuation", "fundamentals", "forensic_scores", "ml", "next_earnings"))
        assert body["position"]["held"] is True and body["position"]["total_shares"] == 3
        assert any("No stock_signals or quant_signals data yet" in item for item in data["missing"])
        assert data["stale"] is True

    def test_synthetic_treasury_bill_ticker_is_excluded_from_yahoo(self, mcp_client, book, yahoo):
        with patch("mcp_server.tools.get_ticker_snapshot.get_combined_holdings", return_value={"TBILL-77": {"global_shares": 1, "accounts": []}}):
            data = _snapshot(mcp_client, "TBILL-77")
        assert data["data"]["price"] is None
        yahoo.get_quote_snapshot.assert_not_called()


class TestStaleAndMissingData:
    def test_old_nightly_data_marks_each_part_stale(self, mcp_client, book):
        conn = get_connection()
        try:
            conn.execute("UPDATE stock_signals SET last_updated = ? WHERE ticker = ?", (_stamp(_utc(days_ago=9)), HELD))
            conn.execute("UPDATE quant_signals SET date = ? WHERE ticker = ?", (_day(9), HELD))
            conn.commit()
        finally:
            conn.close()
        body = _snapshot(mcp_client)["data"]
        assert body["trend"]["stale"] is True and body["technicals"]["stale"] is True
        assert body["valuation"]["stale"] is True and body["fundamentals"]["stale"] is True
        assert body["price"]["stale"] is False

    def test_fresh_nightly_data_is_not_stale(self, mcp_client, book):
        body = _snapshot(mcp_client)["data"]
        assert not any(body[part]["stale"] for part in ("trend", "technicals", "valuation", "fundamentals"))

    def test_trend_is_null_not_down_when_the_moving_average_is_missing(self, mcp_client, book):
        conn = get_connection()
        try:
            conn.execute("UPDATE quant_signals SET sma_50 = NULL, sma_200 = NULL WHERE ticker = ?", (HELD,))
            conn.commit()
        finally:
            conn.close()
        trend = _snapshot(mcp_client)["data"]["trend"]
        assert (trend["trend_50d"], trend["trend_200d"], trend["sma_50"]) == (None, None, None)

    def test_missing_forensic_scores_and_a_past_earnings_date_are_reported(self, mcp_client, book):
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE stock_signals SET forensic_last_updated = NULL, piotroski_f_score = NULL, altman_z_score = NULL, beneish_m_score = NULL, "
                "next_earnings_date = ? WHERE ticker = ?", (_day(30), HELD),
            )
            conn.commit()
        finally:
            conn.close()
        data = _snapshot(mcp_client)
        assert data["data"]["forensic_scores"]["piotroski_f_score"] is None and data["data"]["forensic_scores"]["as_of"] is None
        assert any("Forensic scores have not been computed" in item for item in data["missing"])
        assert any("next earnings date is in the past" in item for item in data["missing"])
        assert data["stale"] is True

    def test_an_unparseable_earnings_date_is_null_not_a_placeholder(self, mcp_client, book):
        conn = get_connection()
        try:
            conn.execute("UPDATE stock_signals SET next_earnings_date = 'Unknown' WHERE ticker = ?", (HELD,))
            conn.commit()
        finally:
            conn.close()
        assert _snapshot(mcp_client)["data"]["next_earnings"]["date"] is None

    def test_a_zero_price_row_yields_a_null_trend_not_a_down_trend(self, mcp_client, book, yahoo):
        yahoo.get_quote_snapshot.side_effect = None
        yahoo.get_quote_snapshot.return_value = None
        conn = get_connection()
        try:
            conn.execute("INSERT OR REPLACE INTO quant_signals (ticker, date, close_price, sma_50, sma_200) VALUES (?, ?, 1.0, 5.0, 6.0)", (THIN, _day(1)))
            conn.commit()
        finally:
            conn.close()
        trend = _snapshot(mcp_client, THIN)["data"]["trend"]
        assert (trend["trend_50d"], trend["trend_200d"], trend["sma_50"]) == (None, None, 5.0)


class TestArguments:
    def test_unknown_ticker_is_a_clear_not_found_that_does_not_echo_the_input(self, mcp_client, book, yahoo):
        message = error_text(call(mcp_client, "get_ticker_snapshot", {"ticker": "ZZNOSUCH"}))
        assert message.startswith("Unknown ticker.") and "ZZNOSUCH" not in message
        yahoo.get_quote_snapshot.assert_not_called()

    @pytest.mark.parametrize("bad", ["", "   ", "../etc/passwd", "AAA BBB", "A;B", "AAPL\nX", "ü"])
    def test_malformed_ticker_is_refused_before_any_lookup(self, mcp_client, bad, yahoo):
        message = error_text(call(mcp_client, "get_ticker_snapshot", {"ticker": bad}))
        assert message.startswith("Invalid ticker:")
        assert bad.strip() == "" or bad.strip() not in message
        yahoo.get_quote_snapshot.assert_not_called()

    def test_over_long_ticker_is_refused(self, mcp_client):
        response = call(mcp_client, "get_ticker_snapshot", {"ticker": "A" * 65})
        assert response.json()["result"]["isError"] is True

    def test_ticker_is_required(self, mcp_client):
        assert call(mcp_client, "get_ticker_snapshot", {}).json()["result"]["isError"] is True

    def test_tool_is_listed_with_a_ticker_parameter_capped_at_64(self, mcp_client):
        from .helpers import rpc
        tools = {t["name"]: t for t in rpc(mcp_client, "tools/list").json()["result"]["tools"]}
        assert "get_ticker_snapshot" in EXPECTED_TOOLS
        schema = tools["get_ticker_snapshot"]["inputSchema"]
        assert schema["required"] == ["ticker"] and schema["properties"]["ticker"]["maxLength"] == 64


class TestSafety:
    def test_failure_returns_fixed_text_not_the_exception(self, mcp_client, book):
        with patch("mcp_server.tools.get_ticker_snapshot.get_all_cached_pulse", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = call(mcp_client, "get_ticker_snapshot", {"ticker": HELD})
        assert response.json()["result"]["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text

    def test_leaves_the_database_unchanged_for_every_kind_of_ticker(self, mcp_client, book, yahoo):
        yahoo.get_quote_snapshot.side_effect = None
        yahoo.get_quote_snapshot.return_value = _SNAPSHOT
        before = table_counts()
        for ticker in (HELD, WATCHED, UNIVERSE, EMPTY_HELD):
            _snapshot(mcp_client, ticker)
        assert table_counts() == before

    def test_load_stock_row_still_marks_its_placeholder_rows_with_the_sentinels_the_tool_reads(self):
        row, _ = page_data_stock.load_stock_row("ZZSNAPNOSUCHROW")
        assert row["overall_signal"] == page_data_stock.UNKNOWN_SIGNAL
        assert page_data_stock._quant_only_stock("X", {"date": "2026-01-01"})["overall_signal"] == page_data_stock.QUANT_ONLY_SIGNAL
        assert page_data_stock._quant_only_stock("X", {"date": "2026-01-01"})["quant_date"] == "2026-01-01"
