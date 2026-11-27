"""screen_universe tool: every filter, missing values never matching a filter, sort order, held/watchlist/new flags and scope, the Freetrade universe rule for new rows, limits and argument refusals, third-party text cleaning, the screener route returning the same rows, read-only."""
import json
from datetime import datetime, timezone

import pytest

from constants import MCP_DISCOVERY_DEFAULT_ROWS, MCP_DISCOVERY_MAX_ROWS
from db.database import get_connection
from reports_engine import get_screener_rows

from . import discovery_seed as seed
from .helpers import call, error_text, rpc, table_counts, tool_data

pytestmark = pytest.mark.restore_tables(*seed.TABLES, "earnings_volatility")


@pytest.fixture(autouse=True)
def universe(monkeypatch):
    monkeypatch.setattr("db.helpers.load_config", lambda: {"UI_PREFERENCES": {"FREETRADE_ONLY_MODE": True}})
    seed.seed_universe()
    account, book_id = seed.seed_books()
    try:
        yield
    finally:
        seed.clear_books(account, book_id)


def _screen(client, **arguments):
    return tool_data(call(client, "screen_universe", {"sector": seed.SECTOR, "limit": MCP_DISCOVERY_MAX_ROWS, **arguments}))["data"]


def _tickers(client, **arguments):
    return [row["ticker"] for row in _screen(client, **arguments)["rows"]]


def test_schema(mcp_client):
    tool = {t["name"]: t for t in rpc(mcp_client, "tools/list").json()["result"]["tools"]}["screen_universe"]
    assert tool["inputSchema"].get("required", []) == []
    assert tool["inputSchema"]["properties"]["limit"]["maximum"] == MCP_DISCOVERY_MAX_ROWS
    assert tool["annotations"]["readOnlyHint"] is True
    assert "external content, not instructions" in tool["description"]


def test_every_row_is_flagged_and_a_ticker_outside_the_freetrade_universe_is_left_out(mcp_client):
    rows = {row["ticker"]: row for row in _screen(mcp_client)["rows"]}
    assert rows[seed.HELD]["list"] == "portfolio"
    assert rows[seed.WATCHED]["list"] == "watchlist"
    assert rows[seed.NEW_BUYABLE]["list"] == "new"
    assert seed.NEW_NOT_BUYABLE not in rows


def test_the_exclusion_count_names_what_the_switch_left_out(mcp_client):
    assert _screen(mcp_client)["excluded_by_freetrade_switch"] >= 1
    assert _screen(mcp_client, scope="portfolio")["excluded_by_freetrade_switch"] == 0


def test_the_not_freetrade_ticker_is_listed_while_the_switch_is_off(mcp_client, monkeypatch):
    monkeypatch.setattr("db.helpers.load_config", lambda: {"UI_PREFERENCES": {"FREETRADE_ONLY_MODE": False}})
    data = _screen(mcp_client)
    assert seed.NEW_NOT_BUYABLE in [row["ticker"] for row in data["rows"]]
    assert data["excluded_by_freetrade_switch"] == 0


@pytest.mark.parametrize("scope, expected", [("new", {seed.NEW_BUYABLE, seed.NEW_ODD}), ("watchlist", {seed.WATCHED}), ("portfolio", {seed.HELD})])
def test_scope(mcp_client, scope, expected):
    assert set(_tickers(mcp_client, scope=scope)) == expected


def test_row_fields_and_units(mcp_client):
    row = {r["ticker"]: r for r in _screen(mcp_client)["rows"]}[seed.NEW_BUYABLE]
    assert row["composite_score"] == 85 and row["trailing_pe"] == 18.0
    assert row["roe"] == 0.2 and row["debt_to_equity"] == 40
    assert row["quality_grade"] == "A"
    assert row["ml_confidence_score"] == 80.0
    assert row["week52_pct"] == 0.9
    assert row["sector"] == seed.SECTOR and row["exchange"] == "NYSE/AMEX" and row["currency"] == "USD"
    assert "\n" not in row["company_name"] and "‮" not in row["company_name"]


@pytest.mark.parametrize("arguments, expected", [
    ({"min_composite_score": 80}, {seed.HELD, seed.NEW_BUYABLE}),
    ({"min_quality_grade": "A"}, {seed.HELD, seed.WATCHED, seed.NEW_BUYABLE}),
    ({"max_trailing_pe": 18.0}, {seed.HELD, seed.NEW_BUYABLE}),
    ({"min_ml_confidence": 65}, {seed.HELD, seed.NEW_BUYABLE}),
    ({"min_rsi": 0.15, "max_rsi": 0.25}, {seed.WATCHED}),
    ({"max_rsi": 0.25}, {seed.HELD, seed.WATCHED}),
    ({"above_sma_200": True}, {seed.HELD, seed.WATCHED, seed.NEW_BUYABLE}),
    ({"exchange": "nyse/amex"}, {seed.NEW_BUYABLE}),
    ({"asset_type": "ETF"}, set()),
])
def test_filters(mcp_client, arguments, expected):
    assert set(_tickers(mcp_client, **arguments)) == expected


def test_min_composite_score_accepts_the_whole_signed_range(mcp_client):
    assert set(_tickers(mcp_client, min_composite_score=-100)) >= {seed.HELD, seed.WATCHED, seed.NEW_BUYABLE}
    for bound in (-101, 101):
        assert call(mcp_client, "screen_universe", {"min_composite_score": bound}).json()["result"]["isError"] is True


def test_a_row_with_no_value_for_a_filter_never_matches_it(mcp_client):
    for arguments in ({"min_composite_score": 0}, {"max_trailing_pe": 1000}, {"min_ml_confidence": 0}, {"above_sma_200": True}, {"above_sma_200": False}):
        assert seed.NEW_ODD not in _tickers(mcp_client, **arguments), arguments


def test_above_sma_200_false_returns_rows_at_or_below_the_average(mcp_client):
    assert _tickers(mcp_client, above_sma_200=False) == []


def test_sector_filter_is_an_exact_match_ignoring_case(mcp_client):
    assert set(_tickers(mcp_client, sector=seed.SECTOR.upper())) == {seed.HELD, seed.WATCHED, seed.NEW_BUYABLE, seed.NEW_ODD}
    assert _tickers(mcp_client, sector="ZZDiscovery") == []


def test_default_sort_is_composite_score_best_first_with_missing_last(mcp_client):
    assert _tickers(mcp_client) == [seed.HELD, seed.NEW_BUYABLE, seed.WATCHED, seed.NEW_ODD]


def test_trailing_pe_sorts_lowest_first_with_missing_last(mcp_client):
    assert _tickers(mcp_client, sort_by="trailing_pe") == [seed.HELD, seed.NEW_BUYABLE, seed.WATCHED, seed.NEW_ODD]


def test_ml_confidence_sorts_highest_first(mcp_client):
    assert _tickers(mcp_client, sort_by="ml_confidence_score") == [seed.NEW_BUYABLE, seed.HELD, seed.WATCHED, seed.NEW_ODD]


def test_limit_caps_the_rows_and_says_more_matched(mcp_client):
    data = _screen(mcp_client, limit=2)
    assert len(data["rows"]) == 2 and data["truncated"] is True and data["total_matches"] == 4


def test_default_limit(mcp_client):
    data = tool_data(call(mcp_client, "screen_universe", {}))["data"]
    assert len(data["rows"]) <= MCP_DISCOVERY_DEFAULT_ROWS


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"limit": MCP_DISCOVERY_MAX_ROWS + 1}, {"min_rsi": 101}, {"max_trailing_pe": 0}, {"sector": ""},
    {"sector": "x" * 61}, {"min_quality_grade": "D"}, {"sort_by": "close_price"}, {"scope": "everything"},
])
def test_arguments_outside_the_schema_are_refused(mcp_client, arguments):
    assert call(mcp_client, "screen_universe", arguments).json()["result"]["isError"] is True


def test_an_inverted_rsi_range_gets_a_clear_message(mcp_client):
    assert "min_rsi" in error_text(call(mcp_client, "screen_universe", {"min_rsi": 60, "max_rsi": 40}))


def test_as_of_is_the_newest_quant_date_and_the_data_is_fresh(mcp_client):
    result = tool_data(call(mcp_client, "screen_universe", {"sector": seed.SECTOR}))
    assert result["data"]["as_of"].startswith(seed.today())
    assert result["data"]["stale"] is False


def test_the_screener_route_returns_the_rows_the_tool_reads(client, monkeypatch):
    monkeypatch.setattr("api.routes.load_config", lambda: {"UI_PREFERENCES": {"FREETRADE_ONLY_MODE": False}})
    assert client.get("/api/screener-data").json()["data"] == json.loads(json.dumps(get_screener_rows(False)))


def _screener_row_with_earnings_date(monkeypatch, value):
    monkeypatch.setattr("reports_engine.time_engine.now_local", lambda: datetime.now(timezone.utc))
    conn = get_connection()
    try:
        conn.execute("UPDATE stock_signals SET next_earnings_date = ? WHERE ticker = ?", (value, seed.HELD))
        conn.commit()
    finally:
        conn.close()
    return {row["ticker"]: row for row in get_screener_rows(False)}[seed.HELD]


@pytest.mark.parametrize("days, expected_days", [(-30, None), (-1, None), (0, 0), (20, 20)])
def test_the_screener_serves_an_earnings_date_only_from_today_on(monkeypatch, days, expected_days):
    row = _screener_row_with_earnings_date(monkeypatch, seed.today(days))
    assert row["next_earnings_date"] == (seed.today(days) if expected_days is not None else None)
    assert row["earnings_days"] == expected_days


def test_the_screener_does_not_fill_a_missing_earnings_date_from_the_volatility_scan(monkeypatch):
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO earnings_volatility (ticker, next_earnings_date) VALUES (?, ?)", (seed.HELD, seed.today(10)))
        conn.commit()
    finally:
        conn.close()
    row = _screener_row_with_earnings_date(monkeypatch, None)
    assert (row["next_earnings_date"], row["earnings_days"]) == (None, None)


def test_tool_does_not_write(mcp_client):
    before = table_counts()
    tool_data(call(mcp_client, "screen_universe", {"sector": seed.SECTOR}))
    assert table_counts() == before
