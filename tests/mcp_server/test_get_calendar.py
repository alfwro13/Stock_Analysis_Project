"""get_calendar tool: earnings and ex-dividend dates of held and watched tickers inside the window, macro events with the model's surprise fields, window and row caps, coverage and staleness markers, read-only."""
from datetime import datetime, timedelta, timezone

import pytest

from constants import MCP_CALENDAR_MAX_DAYS, MCP_CALENDAR_MAX_ROWS
from database import add_transaction, create_account, get_connection
from db_accounts import add_watchlist_item, get_watchlist_account

from .helpers import ACCOUNT_TABLES, call, emptied_tables, rpc, table_counts, tool_data

HELD, WATCHED, FAR, UNKNOWN, PAST = "ZZCALH", "ZZCALW", "ZZCALF", "ZZCALU", "ZZCALP"
_TICKERS = (HELD, WATCHED, FAR, UNKNOWN, PAST)
_STAMP = "%Y-%m-%d %H:%M:%S"


def _day(days_from_now=0):
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


def _at(days_from_now=0, hours=14):
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=days_from_now, hours=hours)).strftime(_STAMP)


def _stamp(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime(_STAMP)


def _signal(conn, ticker, earnings, ex_div, updated):
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, company_name, last_updated, next_earnings_date, ex_dividend_date) VALUES (?, ?, ?, ?, ?)",
        (ticker, f"{ticker} Corp", updated, earnings, ex_div),
    )


def _event(conn, event_id, when, name, currency="USD", forecast=None, previous=None, actual=None, miss=None, vol=0.0):
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name, forecast_val, previous_val, actual_val, ai_consensus_miss_prob, ai_volatility_warning) "
        "VALUES (?, ?, ?, 'High', ?, ?, ?, ?, ?, ?)",
        (event_id, when, currency, name, forecast, previous, actual, miss, vol),
    )


@pytest.fixture
def calendar():
    with emptied_tables("macro_calendar", "scheduler_run_log"):
        conn = get_connection()
        try:
            _signal(conn, HELD, _day(3), _day(5), _stamp(hours=2))
            _signal(conn, WATCHED, _day(1), _day(-2), _stamp(hours=2))
            _signal(conn, FAR, _day(20), _day(40), _stamp(hours=2))
            _signal(conn, UNKNOWN, "Unknown", None, _stamp(hours=2))
            _signal(conn, PAST, _day(-3), _day(-10), _stamp(hours=2))
            _event(conn, "zz-cpi", _at(2), "CPI y/y", forecast=3.1, previous=3.0, miss=0.62, vol=1.4)
            _event(conn, "zz-boe", _at(4, 12), "BoE Official Bank Rate", currency="GBP", forecast=4.5, previous=4.5)
            _event(conn, "zz-past", _at(-2), "Old release", actual=1.0)
            _event(conn, "zz-late", _at(25), "Beyond the window")
            conn.execute("INSERT OR REPLACE INTO scheduler_run_log (job_id, last_run) VALUES ('macro_calendar_job', ?)", (_stamp(hours=3),))
            conn.commit()
        finally:
            conn.close()
        account = create_account("CalBookAcc", "GBP", initial_cash=0.0)
        add_transaction(account, "Buy", "2026-01-05", ticker=HELD, currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
        book = get_watchlist_account()
        for ticker in (HELD, WATCHED, FAR, UNKNOWN, PAST):
            add_watchlist_item(book["id"], ticker, currency="USD", quote_type="EQUITY")
        try:
            yield
        finally:
            conn = get_connection()
            try:
                for table in ACCOUNT_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account,))
                conn.execute("DELETE FROM accounts WHERE id = ?", (account,))
                conn.execute("DELETE FROM watchlist_items WHERE account_id = ?", (book["id"],))
                conn.executemany("DELETE FROM stock_signals WHERE ticker = ?", [(t,) for t in _TICKERS])
                conn.commit()
            finally:
                conn.close()


def _calendar(client, **arguments):
    return tool_data(call(client, "get_calendar", arguments))


class TestTickerDates:
    def test_envelope_and_window(self, mcp_client, calendar):
        data = _calendar(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert (data["data"]["days"], data["data"]["from"], data["data"]["to"]) == (7, _day(0), _day(7))

    def test_earnings_inside_the_window_soonest_first_with_the_list(self, mcp_client, calendar):
        rows = _calendar(mcp_client)["data"]["earnings"]["rows"]
        assert [(r["ticker"], r["list"], r["date"], r["days_until"]) for r in rows] == [
            (WATCHED, "watchlist", _day(1), 1), (HELD, "portfolio", _day(3), 3),
        ]
        assert rows[0]["company_name"] == f"{WATCHED} Corp"

    def test_unknown_past_and_far_dates_are_left_out(self, mcp_client, calendar):
        tickers = {r["ticker"] for r in _calendar(mcp_client)["data"]["earnings"]["rows"]}
        assert not tickers & {FAR, UNKNOWN, PAST}

    def test_only_upcoming_ex_dividend_dates_are_listed(self, mcp_client, calendar):
        rows = _calendar(mcp_client)["data"]["ex_dividends"]["rows"]
        assert [(r["ticker"], r["date"], r["days_until"]) for r in rows] == [(HELD, _day(5), 5)]

    def test_a_longer_window_reaches_the_far_dates(self, mcp_client, calendar):
        data = _calendar(mcp_client, days=30)["data"]
        assert {r["ticker"] for r in data["earnings"]["rows"]} == {WATCHED, HELD, FAR}
        assert {r["ticker"] for r in data["ex_dividends"]["rows"]} == {HELD}
        assert _calendar(mcp_client, days=MCP_CALENDAR_MAX_DAYS)["data"]["to"] == _day(MCP_CALENDAR_MAX_DAYS)

    def test_the_last_day_and_today_are_included(self, mcp_client, calendar):
        conn = get_connection()
        try:
            _signal(conn, PAST, _day(0), _day(7), _stamp(hours=1))
            conn.commit()
        finally:
            conn.close()
        data = _calendar(mcp_client)["data"]
        assert PAST in {r["ticker"] for r in data["earnings"]["rows"]}
        assert PAST in {r["ticker"] for r in data["ex_dividends"]["rows"]}

    def test_a_non_iso_stored_value_is_skipped_not_guessed(self, mcp_client, calendar):
        conn = get_connection()
        try:
            _signal(conn, PAST, "1900000000", "garbage", _stamp(hours=1))
            conn.commit()
        finally:
            conn.close()
        data = _calendar(mcp_client, days=30)["data"]
        assert PAST not in {r["ticker"] for r in data["earnings"]["rows"] + data["ex_dividends"]["rows"]}

    def test_part_as_of_is_the_oldest_stock_row_and_fresh_rows_are_not_stale(self, mcp_client, calendar):
        part = _calendar(mcp_client)["data"]["earnings"]
        assert part["stale"] is False
        assert part["as_of"] is not None

    def test_stale_stock_rows_are_flagged(self, mcp_client, calendar):
        conn = get_connection()
        try:
            conn.execute("UPDATE stock_signals SET last_updated = ? WHERE ticker = ?", (_stamp(days=9), HELD))
            conn.commit()
        finally:
            conn.close()
        data = _calendar(mcp_client)
        assert data["data"]["earnings"]["stale"] is True and data["data"]["ex_dividends"]["stale"] is True

    def test_a_held_and_watched_ticker_is_listed_once_as_portfolio(self, mcp_client, calendar):
        rows = _calendar(mcp_client)["data"]["earnings"]["rows"]
        assert [r["ticker"] for r in rows].count(HELD) == 1
        assert next(r for r in rows if r["ticker"] == HELD)["list"] == "portfolio"


class TestMacroEvents:
    def test_events_in_the_window_with_the_model_fields(self, mcp_client, calendar):
        rows = _calendar(mcp_client)["data"]["macro_events"]["rows"]
        assert [r["name"] for r in rows] == ["CPI y/y", "BoE Official Bank Rate"]
        cpi = rows[0]
        assert (cpi["currency"], cpi["impact"], cpi["forecast"], cpi["previous"], cpi["actual"]) == ("USD", "High", 3.1, 3.0, None)
        assert (cpi["ai_consensus_miss_probability"], cpi["ai_volatility_forecast_pct"]) == (0.62, 1.4)
        assert cpi["at"].endswith("Z")

    def test_unscored_events_have_null_model_fields_not_the_default_zero(self, mcp_client, calendar):
        boe = _calendar(mcp_client)["data"]["macro_events"]["rows"][1]
        assert boe["ai_consensus_miss_probability"] is None and boe["ai_volatility_forecast_pct"] is None

    def test_past_and_later_events_are_left_out(self, mcp_client, calendar):
        names = {r["name"] for r in _calendar(mcp_client)["data"]["macro_events"]["rows"]}
        assert not names & {"Old release", "Beyond the window"}

    def test_a_window_beyond_the_stored_events_is_reported_missing(self, mcp_client, calendar):
        assert not any("only lists events up to" in m for m in _calendar(mcp_client, days=20)["missing"])
        conn = get_connection()
        try:
            conn.execute("DELETE FROM macro_calendar WHERE event_id = 'zz-late'")
            conn.commit()
        finally:
            conn.close()
        data = _calendar(mcp_client, days=30)
        assert any("only lists events up to" in m for m in data["missing"]) and data["stale"] is True

    def test_no_recorded_run_and_no_events_are_explicit(self, mcp_client, calendar):
        conn = get_connection()
        try:
            conn.execute("DELETE FROM scheduler_run_log WHERE job_id = 'macro_calendar_job'")
            conn.execute("DELETE FROM macro_calendar")
            conn.commit()
        finally:
            conn.close()
        data = _calendar(mcp_client)
        assert data["data"]["macro_events"] == {"as_of": None, "stale": True, "rows": [], "truncated": False}
        assert any("no recorded run" in m for m in data["missing"]) and any("holds no events" in m for m in data["missing"])

    def test_an_old_macro_run_is_flagged_stale(self, mcp_client, calendar):
        conn = get_connection()
        try:
            conn.execute("UPDATE scheduler_run_log SET last_run = ? WHERE job_id = 'macro_calendar_job'", (_stamp(days=4),))
            conn.commit()
        finally:
            conn.close()
        assert _calendar(mcp_client)["data"]["macro_events"]["stale"] is True


class TestArgumentsAndCaps:
    @pytest.mark.parametrize("days", [0, -1, MCP_CALENDAR_MAX_DAYS + 1, 365])
    def test_a_bad_window_is_refused(self, mcp_client, calendar, days):
        assert call(mcp_client, "get_calendar", {"days": days}).json()["result"]["isError"] is True

    def test_the_schema_states_the_window(self, mcp_client):
        tools = {t["name"]: t for t in rpc(mcp_client, "tools/list").json()["result"]["tools"]}
        days = tools["get_calendar"]["inputSchema"]["properties"]["days"]
        assert (days["minimum"], days["maximum"], days["default"]) == (1, MCP_CALENDAR_MAX_DAYS, 7)

    def test_rows_per_list_are_capped_and_say_truncated(self, mcp_client, calendar):
        conn = get_connection()
        try:
            for index in range(MCP_CALENDAR_MAX_ROWS + 5):
                _event(conn, f"zz-many-{index}", _at(1, hours=index % 20), f"Many {index}")
            conn.commit()
        finally:
            conn.close()
        macro = _calendar(mcp_client)["data"]["macro_events"]
        assert len(macro["rows"]) == MCP_CALENDAR_MAX_ROWS and macro["truncated"] is True


class TestEmptyAndReadOnly:
    def test_an_empty_book_returns_empty_lists(self, mcp_client):
        with emptied_tables("macro_calendar", "scheduler_run_log"):
            data = _calendar(mcp_client)
        assert data["data"]["earnings"]["rows"] == [] and data["data"]["ex_dividends"]["rows"] == []

    def test_the_tool_writes_nothing(self, mcp_client, calendar):
        before = table_counts()
        _calendar(mcp_client, days=30)
        assert table_counts() == before
