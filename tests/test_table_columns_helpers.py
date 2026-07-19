"""
tests/test_table_columns_helpers.py  ── COLUMN REGISTRY & FORMATTING

table_columns_helpers.py is the single source of truth for every optional column
offered by the Portfolio/Watchlist column picker. These tests guard the structural
invariants the templates and column_picker.js rely on (unique keys, stable index
ordering) plus the per-fmt-type formatting logic.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import table_columns_helpers as tch


# ── Registry structure ───────────────────────────────────────────────────────

@pytest.mark.config
def test_optional_columns_have_required_fields():
    required = {"key", "label", "category", "pages", "fmt"}
    for col in tch.OPTIONAL_COLUMNS:
        missing = required - col.keys()
        assert not missing, f"{col.get('key')} missing fields: {missing}"
        assert set(col["pages"]) <= {"portfolio", "watchlist"}


@pytest.mark.config
def test_optional_columns_keys_are_unique():
    keys = [c["key"] for c in tch.OPTIONAL_COLUMNS]
    assert len(keys) == len(set(keys)), "duplicate key in OPTIONAL_COLUMNS"


@pytest.mark.config
@pytest.mark.parametrize("core_columns", [tch.PORTFOLIO_CORE_COLUMNS, tch.WATCHLIST_CORE_COLUMNS])
def test_core_columns_have_a_known_fmt(core_columns):
    """static/js/advanced_filter.js maps fmt -> operator family (numeric/text/date/bool);
    every core column needs one even though _format_value() never touches this list."""
    known_fmts = {
        "pct_from_fraction", "pct_raw", "ratio2", "price", "price_raw",
        "currency_usd", "volume", "date", "text", "bool01", "int", "client",
    }
    for col in core_columns:
        assert "fmt" in col, f"{col['key']} missing fmt"
        assert col["fmt"] in known_fmts, f"{col['key']} has unknown fmt {col['fmt']!r}"


@pytest.mark.config
@pytest.mark.parametrize("page", ["portfolio", "watchlist"])
def test_all_columns_for_page_has_no_duplicate_keys(page):
    all_cols = tch.all_columns_for_page(page)
    keys = [c["key"] for c in all_cols]
    assert len(keys) == len(set(keys)), f"duplicate column key on {page}: {keys}"


@pytest.mark.config
def test_ticker_is_the_only_pinned_column_on_both_pages():
    for page in ("portfolio", "watchlist"):
        pinned = [c["key"] for c in tch.all_columns_for_page(page) if c.get("pinned")]
        assert pinned == ["ticker"]


@pytest.mark.config
def test_all_columns_for_page_tags_core_and_optional_type():
    all_cols = tch.all_columns_for_page("portfolio")
    core_count = len(tch.PORTFOLIO_CORE_COLUMNS)
    assert all(c["type"] == "core" for c in all_cols[:core_count])
    assert all(c["type"] == "optional" for c in all_cols[core_count:])


@pytest.mark.config
def test_portfolio_only_and_watchlist_only_columns_scoped_correctly():
    portfolio_keys = {c["key"] for c in tch.columns_for_page("portfolio")}
    watchlist_keys = {c["key"] for c in tch.columns_for_page("watchlist")}
    # Parity-gap columns must only appear on the page that was missing them.
    for key in ("target_price", "piotroski_f_score", "altman_z_score", "beneish_m_score", "low_target", "high_target"):
        assert key in portfolio_keys
        assert key not in watchlist_keys
    assert "vp_exit_zone" in watchlist_keys
    assert "vp_exit_zone" not in portfolio_keys


# ── Formatting ────────────────────────────────────────────────────────────────

@pytest.mark.config
def test_format_value_pct_from_fraction():
    sort, display = tch._format_value(0.153, "pct_from_fraction", None)
    assert sort == pytest.approx(0.153)
    assert display == "15.30%"


@pytest.mark.config
def test_format_value_pct_raw_no_multiply():
    sort, display = tch._format_value(30.0, "pct_raw", None)
    assert sort == pytest.approx(30.0)
    assert display == "30.0%"


@pytest.mark.config
def test_format_value_ratio2():
    _, display = tch._format_value(1.23456, "ratio2", None)
    assert display == "1.23"


@pytest.mark.config
def test_format_value_price_applies_gbp_halving_and_symbol():
    _, display = tch._format_value(543.2, "price", "GBp")
    assert display == "£5.43"


@pytest.mark.config
def test_format_value_price_usd_no_halving():
    _, display = tch._format_value(150.5, "price", "USD")
    assert display == "$150.50"


@pytest.mark.config
def test_format_value_bool01():
    assert tch._format_value(1, "bool01", None)[1] == "Yes"
    assert tch._format_value(0, "bool01", None)[1] == "No"


@pytest.mark.config
def test_format_value_date_unknown_becomes_placeholder():
    sort, display = tch._format_value("Unknown", "date", None)
    assert sort == "9999-12-31"
    assert display == "N/A"


@pytest.mark.config
def test_format_value_missing_returns_placeholder():
    for fmt in ("ratio2", "pct_from_fraction", "price", "currency_usd", "volume", "int"):
        sort, display = tch._format_value(None, fmt, None)
        assert display == "N/A"


@pytest.mark.config
def test_format_value_client_fmt_is_a_noop():
    sort, display = tch._format_value(123, "client", None)
    assert sort == ""
    assert display == ""


# ── build_optional_column_cells ─────────────────────────────────────────────

@pytest.mark.config
def test_build_optional_column_cells_matches_columns_for_page_order():
    row = {"currency": "USD"}
    cells = tch.build_optional_column_cells(row, "portfolio")
    expected_keys = [c["key"] for c in tch.columns_for_page("portfolio")]
    assert [c["key"] for c in cells] == expected_keys


@pytest.mark.config
def test_build_optional_column_cells_marks_client_rendered_entries():
    row = {"currency": "USD"}
    cells = {c["key"]: c for c in tch.build_optional_column_cells(row, "portfolio")}
    assert cells["shares"]["client"] is True
    assert cells["trailing_pe"]["client"] is False


# ── resolve_column_prefs ────────────────────────────────────────────────────

@pytest.mark.config
def test_resolve_column_prefs_defaults_to_empty_lists():
    prefs = tch.resolve_column_prefs({}, "portfolio")
    assert prefs == {"hidden_core_columns": [], "shown_optional_columns": []}


@pytest.mark.config
def test_resolve_column_prefs_reads_scoped_keys():
    config_data = {"UI_PREFERENCES": {
        "WATCHLIST_HIDDEN_CORE_COLUMNS": ["sentiment"],
        "WATCHLIST_SHOWN_OPTIONAL_COLUMNS": ["beta"],
        "PORTFOLIO_HIDDEN_CORE_COLUMNS": ["score"],
    }}
    assert tch.resolve_column_prefs(config_data, "watchlist") == {
        "hidden_core_columns": ["sentiment"], "shown_optional_columns": ["beta"],
    }
    assert tch.resolve_column_prefs(config_data, "portfolio") == {
        "hidden_core_columns": ["score"], "shown_optional_columns": [],
    }


# ── Stage-2 fields (X-ray / Earnings Volatility) ────────────────────────────

@pytest.mark.config
def test_xray_and_earnings_vol_columns_present_on_both_pages():
    keys = {c["key"] for c in tch.OPTIONAL_COLUMNS}
    for key in ("xray_beta", "xray_annualized_vol", "xray_dividend_yield", "earnings_edge_score", "earnings_implied_move"):
        assert key in keys
        col = next(c for c in tch.OPTIONAL_COLUMNS if c["key"] == key)
        assert col["pages"] == tch._BOTH


@pytest.mark.config
def test_xray_dividend_yield_is_pct_raw_not_pct_from_fraction():
    """xray_dividend_cache.dividend_yield_pct arrives from Ghostfolio already in percentage
    form (e.g. 2.5 for 2.5%) — unlike stock_signals.dividend_yield, which is a fraction."""
    col = next(c for c in tch.OPTIONAL_COLUMNS if c["key"] == "xray_dividend_yield")
    assert col["fmt"] == "pct_raw"
    _, display = tch._format_value(2.5, col["fmt"], None)
    assert display == "2.5%"


# ── Views ────────────────────────────────────────────────────────────────────

@pytest.mark.config
def test_resolve_views_falls_back_to_defaults_when_unset():
    assert tch.resolve_views({}, "portfolio") == tch.DEFAULT_PORTFOLIO_VIEWS
    assert tch.resolve_views({}, "watchlist") == tch.DEFAULT_WATCHLIST_VIEWS


@pytest.mark.config
def test_resolve_views_returns_saved_views_when_present():
    custom = [{"name": "My View", "columns": ["ticker", "price"]}]
    config_data = {"UI_PREFERENCES": {"PORTFOLIO_VIEWS": custom}}
    assert tch.resolve_views(config_data, "portfolio") == custom


@pytest.mark.config
@pytest.mark.parametrize("page,views", [("portfolio", tch.DEFAULT_PORTFOLIO_VIEWS), ("watchlist", tch.DEFAULT_WATCHLIST_VIEWS)])
def test_default_views_only_reference_real_column_keys(page, views):
    valid_keys = {c["key"] for c in tch.all_columns_for_page(page)}
    for view in views:
        unknown = set(view["columns"]) - valid_keys
        assert not unknown, f"{page} view {view['name']!r} references unknown keys: {unknown}"


@pytest.mark.config
@pytest.mark.parametrize("page,views", [("portfolio", tch.DEFAULT_PORTFOLIO_VIEWS), ("watchlist", tch.DEFAULT_WATCHLIST_VIEWS)])
def test_default_views_stay_within_24_columns(page, views):
    for view in views:
        assert len(view["columns"]) <= 24, f"{page} view {view['name']!r} has {len(view['columns'])} columns"


@pytest.mark.config
def test_default_views_include_a_position_targets_view_on_both_pages():
    for views in (tch.DEFAULT_PORTFOLIO_VIEWS, tch.DEFAULT_WATCHLIST_VIEWS):
        names = {v["name"] for v in views}
        assert "Position Targets" in names
