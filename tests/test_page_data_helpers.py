"""Unit tests for the pure helpers extracted from the Portfolio/Watchlist/Stock Detail route handlers."""
import pytest

from config import BASE_CURRENCY
from page_data_portfolio import (
    apply_change_period,
    format_portfolio_summary,
    portfolio_present_tags,
    portfolio_scope_tickers,
    watchlist_present_tags,
    watchlist_score_buckets,
)
from page_data_stock import data_freshness, parse_json_field
from page_data_stock_panels import earnings_timing


PORTFOLIO_JSON = {
    "AAA": {"ticker": "AAA", "accounts": [{"id": "acct:1"}, {"id": "gf:x"}]},
    "BBB": {"ticker": "BBB", "accounts": [{"id": "acct:2"}]},
    "CCC": {"ticker": "ccc", "accounts": [{"id": "acct:1"}]},
    "meta": {"note": "no ticker key"},
}


def test_portfolio_scope_tickers_all_filters_ignored_and_dedupes():
    tickers = portfolio_scope_tickers(PORTFOLIO_JSON, "all", {"CCC"})
    assert tickers == ["AAA", "BBB"]


def test_portfolio_scope_tickers_single_account():
    assert portfolio_scope_tickers(PORTFOLIO_JSON, "acct:1", set()) == ["AAA", "ccc"]
    assert portfolio_scope_tickers(PORTFOLIO_JSON, "acct:999", set()) == []


def test_apply_change_period_one_day_uses_pulse_change():
    row = {"current_price": 10.0}
    apply_change_period(row, {"change_pct": -1.5, "is_positive": False, "price": 9.0}, {}, "1d")
    assert row["change_pct"] == -1.5
    assert row["change_is_positive"] is False

    empty = {"current_price": 10.0}
    apply_change_period(empty, None, {}, "1d")
    assert empty["change_pct"] is None and empty["change_is_positive"] is None


def test_apply_change_period_longer_window_uses_anchor_and_falls_back_to_signal_price():
    anchors = {"1m": 8.0}
    from_pulse = {"current_price": 10.0}
    apply_change_period(from_pulse, {"price": 9.0, "change_pct": 1.0, "is_positive": True}, anchors, "1m")
    assert from_pulse["change_pct"] == pytest.approx(12.5)
    assert from_pulse["change_is_positive"] is True

    from_signal = {"current_price": 10.0}
    apply_change_period(from_signal, None, anchors, "1m")
    assert from_signal["change_pct"] == pytest.approx(25.0)

    no_anchor = {"current_price": 10.0}
    apply_change_period(no_anchor, None, {}, "1m")
    assert no_anchor["change_pct"] is None and no_anchor["change_is_positive"] is None


def test_format_portfolio_summary_states():
    gain = format_portfolio_summary({"value": 150.0, "cost": 100.0, "missing_fx": False})
    assert gain == {
        "value": f"150.00 {BASE_CURRENCY}",
        "cost": f"100.00 {BASE_CURRENCY}",
        "pnl": f"+50.00 {BASE_CURRENCY}",
        "pnl_pct": "50.00",
        "is_positive": True,
    }
    loss = format_portfolio_summary({"value": 80.0, "cost": 100.0, "missing_fx": False})
    assert loss["pnl"] == f"-20.00 {BASE_CURRENCY}" and loss["is_positive"] is False

    missing = format_portfolio_summary({"value": 0.0, "cost": 100.0, "missing_fx": True})
    assert missing["value"] == "Unavailable — missing FX"
    assert missing["pnl"] == "Unavailable" and missing["pnl_pct"] == "—"

    assert format_portfolio_summary({"value": 0.0, "cost": 0.0, "missing_fx": False}) is None


def test_present_tags_collectors():
    rows = [
        {"trap_phase_label": "Bull Trap", "bubble_flag_label": "Bubble", "pattern_tags": [{"label": "Double Top"}]},
        {"pattern_tags": [{"label": "Double Top"}, {"label": "Head & Shoulders"}]},
    ]
    tags, patterns = portfolio_present_tags(rows)
    assert tags == {"Bull Trap", "Bubble", "Double Top", "Head & Shoulders"}
    assert patterns == ["Double Top", "Head & Shoulders"]

    watch_rows = [{
        "setup_tags_list": [{"name": "Hammer"}], "report_tags": [{"name": "GARP"}],
        "quality_grade": "A", "pattern_tags": [{"label": "Double Top"}],
    }]
    tags, patterns = watchlist_present_tags(watch_rows)
    assert tags == {"Hammer", "GARP", "Grade A", "Double Top"}
    assert patterns == ["Double Top"]


def test_watchlist_score_buckets():
    rows = [{"composite_score": 80}, {"composite_score": 75}, {"composite_score": 60},
            {"composite_score": 59.9}, {"composite_score": 40}, {"composite_score": 10},
            {"composite_score": None}, {}]
    assert watchlist_score_buckets(rows) == {"75", "60", "40", "0"}
    assert watchlist_score_buckets([{"composite_score": 90}]) == {"75"}


def test_data_freshness_states():
    from datetime import datetime, timedelta, timezone
    fmt = "%Y-%m-%d %H:%M:%S"
    now = datetime.now(timezone.utc)
    assert data_freshness({}) == ("red", "Never")
    assert data_freshness({"last_updated": "garbage"}) == ("red", "garbage")
    fresh = (now - timedelta(hours=1)).strftime(fmt)
    stale = (now - timedelta(hours=30)).strftime(fmt)
    assert data_freshness({"last_updated": fresh}) == ("green", fresh)
    assert data_freshness({"last_updated": stale}) == ("yellow", stale)


def test_parse_json_field():
    assert parse_json_field({"top_holdings": '[{"s": "X"}]'}, "top_holdings", "T") == [{"s": "X"}]
    assert parse_json_field({"top_holdings": "{not json"}, "top_holdings", "T") == []
    assert parse_json_field({}, "top_holdings", "T") == []
    assert parse_json_field({"top_holdings": None}, "top_holdings", "T") == []


def test_earnings_timing():
    assert earnings_timing({"next_earnings_date": "Unknown"}, "T") == (None, None)
    assert earnings_timing({}, "T") == (None, None)
    assert earnings_timing({"next_earnings_date": "not-a-date"}, "T") == (None, None)
    days, vol_date = earnings_timing({"next_earnings_date": "2999-01-15"}, "T")
    assert days > 0
    assert vol_date == "2999-01-08"
