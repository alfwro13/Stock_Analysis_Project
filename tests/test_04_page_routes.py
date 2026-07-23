"""
tests/test_04_page_routes.py  ── HTML PAGE ROUTES

Verifies that every page in the application:
  1. Returns HTTP 200 (or 302 for redirect pages)
  2. Returns HTML content (not a JSON error or empty body)
  3. Does NOT crash with an unhandled 500 error

These tests run against an empty database, so they also verify that pages
handle the "no data yet" state gracefully — a common source of crashes after
a refactor.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _assert_page_ok(client, url: str, *, follow_redirects: bool = True, label: str = ""):
    resp = client.get(url, follow_redirects=follow_redirects)
    name = label or url
    assert resp.status_code < 500, (
        f"Page '{name}' crashed with HTTP {resp.status_code}.\n"
        f"Body (first 500 chars): {resp.text[:500]}"
    )
    assert resp.status_code in (200, 301, 302, 303, 307, 308), (
        f"Page '{name}' returned unexpected status {resp.status_code}"
    )


# ── Redirect ──────────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_root_redirects_to_portfolio(client):
    """GET / must redirect to /portfolio (not crash)."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302, 307, 308), (
        f"Expected redirect from /, got {resp.status_code}"
    )


# ── Core Pages ────────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_portfolio_page_loads(client):
    """GET /portfolio must load without crashing, even with an empty database."""
    _assert_page_ok(client, "/portfolio", label="Portfolio")


@pytest.mark.pages
def test_watchlist_page_loads(client):
    """GET /watchlist must load without crashing, even with an empty database."""
    _assert_page_ok(client, "/watchlist", label="Watchlist")


@pytest.mark.pages
def test_settings_page_loads(client):
    """GET /settings must load the configuration page."""
    _assert_page_ok(client, "/settings", label="Settings")


@pytest.mark.pages
def test_notifications_page_loads(client):
    """GET /notifications must load the notification center."""
    _assert_page_ok(client, "/notifications", label="Notifications")


@pytest.mark.pages
def test_notifications_page_converts_timestamp_to_local_time(client):
    """Notification timestamps are stored in UTC and must be displayed in local time — regression
    for a bug where the raw UTC string was rendered as-is, showing the wrong hour whenever local
    time differs from UTC (e.g. British Summer Time)."""
    import database as _db
    from datetime import datetime, timezone
    import time_engine

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT INTO system_notifications (message_type, message_text, timestamp) VALUES (?, ?, ?)",
            ("Scheduler", "Regression test notification — tz check", "2026-01-15 01:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    expected_local = time_engine.fmt_datetime(datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc))
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert expected_local in resp.text
    assert "2026-01-15 01:00:00" not in resp.text


@pytest.mark.pages
def test_glossary_page_loads(client):
    """GET /glossary must load the educational glossary."""
    _assert_page_ok(client, "/glossary", label="Glossary")


@pytest.mark.pages
def test_glossary_page_has_learn_button(client):
    """The Glossary page must link to the Glossary Learning page (button next to the header)."""
    resp = client.get("/glossary")
    assert resp.status_code == 200
    assert '/glossary/learn' in resp.text


@pytest.mark.pages
def test_glossary_learn_page_loads(client):
    """GET /glossary/learn must load the Glossary Learning study page."""
    _assert_page_ok(client, "/glossary/learn", label="Glossary Learning")


# ── Screener Pages ────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_market_screener_page_loads(client):
    """GET /market-screener must load the full market screener results."""
    _assert_page_ok(client, "/market-screener", label="Market Screener")


# ── Analysis Pages ────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_quality_compounders_page_loads(client):
    """GET /quality-compounders must load the Quality Compounders report page."""
    _assert_page_ok(client, "/quality-compounders", label="Quality Compounders")


@pytest.mark.pages
def test_garp_tenbaggers_page_loads(client):
    """GET /garp-tenbaggers must load the GARP Tenbaggers report page."""
    _assert_page_ok(client, "/garp-tenbaggers", label="GARP Tenbaggers")


@pytest.mark.pages
def test_quality_on_sale_page_loads(client):
    """GET /quality-on-sale must load the Quality on Sale report page."""
    _assert_page_ok(client, "/quality-on-sale", label="Quality on Sale")


@pytest.mark.pages
def test_sector_trends_page_loads(client):
    """GET /sector-trends must load the Sector Trends report page."""
    _assert_page_ok(client, "/sector-trends", label="Sector Trends")


@pytest.mark.pages
def test_relative_strength_leaders_page_loads(client):
    """GET /relative-strength-leaders must load the Relative Strength Leaders report page."""
    _assert_page_ok(client, "/relative-strength-leaders", label="Relative Strength Leaders")


@pytest.mark.pages
def test_mean_reversion_page_loads(client):
    """GET /mean-reversion must load the Mean Reversion Screener report page."""
    _assert_page_ok(client, "/mean-reversion", label="Mean Reversion")


@pytest.mark.pages
def test_dividend_harvest_page_loads(client):
    """GET /dividend-harvest must load the Dividend Harvest report page."""
    _assert_page_ok(client, "/dividend-harvest", label="Dividend Harvest")


@pytest.mark.pages
def test_market_sentiment_page_loads(client):
    """GET /market-sentiment must load the sentiment dashboard."""
    _assert_page_ok(client, "/market-sentiment", label="Market Sentiment")


@pytest.mark.pages
def test_earnings_volatility_page_loads(client):
    """GET /earnings-volatility must load the earnings volatility scanner."""
    _assert_page_ok(client, "/earnings-volatility", label="Earnings Volatility")


@pytest.mark.pages
def test_earnings_volatility_accuracy_page_loads(client):
    """GET /earnings-volatility/accuracy must load the prediction accuracy scorecard."""
    _assert_page_ok(client, "/earnings-volatility/accuracy", label="Earnings Volatility Accuracy")


@pytest.mark.pages
def test_options_sandbox_page_loads(client):
    """GET /options-sandbox must load the options payoff calculator."""
    _assert_page_ok(client, "/options-sandbox", label="Options Sandbox")


@pytest.mark.pages
def test_news_page_loads(client):
    """GET /news must load the news feed reader page without a server error."""
    _assert_page_ok(client, "/news", label="News Feed")


@pytest.mark.pages
def test_tools_page_loads(client):
    """GET /tools must load the tools launcher page without a server error."""
    _assert_page_ok(client, "/tools", label="Tools")


@pytest.mark.pages
def test_markets_page_loads(client):
    """GET /markets must load the Markets page without a server error."""
    _assert_page_ok(client, "/markets", label="Markets")


@pytest.mark.pages
def test_markets_page_defaults_to_dynamic_view_with_no_cookie(client):
    resp = client.get("/markets")
    assert resp.status_code == 200
    assert 'window.MARKETS_DEFAULT_VIEW = "dynamic"' in resp.text


@pytest.mark.pages
def test_markets_page_respects_static_view_cookie(client):
    resp = client.get("/markets", cookies={"markets_view": "static"})
    assert resp.status_code == 200
    assert 'window.MARKETS_DEFAULT_VIEW = "static"' in resp.text


@pytest.mark.pages
def test_markets_page_rejects_invalid_view_cookie(client):
    resp = client.get("/markets", cookies={"markets_view": "bogus"})
    assert resp.status_code == 200
    assert 'window.MARKETS_DEFAULT_VIEW = "dynamic"' in resp.text


@pytest.mark.pages
def test_markets_page_defaults_to_showing_futures_with_no_cookie(client):
    resp = client.get("/markets")
    assert resp.status_code == 200
    assert "window.MARKETS_HIDE_US_FUTURES = false" in resp.text


@pytest.mark.pages
def test_markets_page_respects_hide_us_futures_cookie(client):
    resp = client.get("/markets", cookies={"markets_hide_us_futures": "1"})
    assert resp.status_code == 200
    assert "window.MARKETS_HIDE_US_FUTURES = true" in resp.text


# ── Index Detail (Markets page registry) ────────────────────────────────────────

@pytest.mark.pages
def test_index_detail_future_ticker_renders_its_own_page(client):
    """A direct hit on a future ticker's own URL renders that future's own detail page (the
    Markets page's tiles now link straight to it) rather than redirecting to its paired spot."""
    resp = client.get("/index/ES=F", follow_redirects=False)
    assert resp.status_code == 200
    assert "S&amp;P 500 Futures (ES=F)" in resp.text
    assert "Futures contract for" in resp.text
    assert '/index/^GSPC">US S&amp;P 500 (^GSPC)' in resp.text


@pytest.mark.pages
def test_index_detail_unknown_ticker_does_not_crash(client):
    """A ticker with no registry row at all (not spot, not a paired future) must not 500."""
    resp = client.get("/index/NOT_A_REAL_TICKER")
    assert resp.status_code == 200
    assert "NOT_A_REAL_TICKER" in resp.text


@pytest.mark.pages
def test_index_detail_new_ticker_shows_no_historical_data_placeholder(client, tmp_path, monkeypatch):
    """A newly-seeded ticker with no baseline_parquet must show the existing placeholder,
    not crash — confirmed no new bootstrap code path is needed. HISTORICAL_DIR is redirected
    to an empty tmp dir so this doesn't depend on ^AXJO genuinely lacking a real fetched
    parquet on disk — it's a tracked market_ticker_registry index (ASX 200) since the Markets
    page feature, so a dev/prod checkout that has ever run a daily fetch will have real data
    for it, which used to make this test's "no data" assumption false (found 2026-07-10)."""
    monkeypatch.setattr("page_routes_macro.HISTORICAL_DIR", tmp_path)
    resp = client.get("/index/%5EAXJO")
    assert resp.status_code == 200
    assert "No historical data yet" in resp.text


@pytest.mark.pages
def test_index_detail_shows_futures_banner_when_cash_market_closed(client):
    with patch("page_routes_macro.markets_engine.resolve_tile", return_value=("ES=F", "S&P 500 Futures", True)):
        resp = client.get("/index/%5EGSPC")
    assert resp.status_code == 200
    assert "Cash market closed" in resp.text
    assert '/index/ES=F">S&amp;P 500 Futures (ES=F)' in resp.text


@pytest.mark.pages
def test_index_detail_no_futures_banner_when_cash_market_open(client):
    with patch("page_routes_macro.markets_engine.resolve_tile", return_value=("^GSPC", "US S&P 500", False)):
        resp = client.get("/index/%5EGSPC")
    assert resp.status_code == 200
    assert "cash market closed" not in resp.text


@pytest.mark.pages
def test_index_detail_wires_intraday_auto_refresh_globals(client):
    """The intraday chart must auto-refresh on load, mirroring the Stock Detail page —
    the refresh-status countdown element and the ENABLE_LIVE_ASSETS/INDEX_REFRESH_RATE_MS
    globals index_detail.js reads to drive it must be present."""
    resp = client.get("/index/%5EGSPC")
    assert resp.status_code == 200
    assert 'id="refresh-status"' in resp.text
    assert "window.ENABLE_LIVE_ASSETS" in resp.text
    assert "window.INDEX_REFRESH_RATE_MS" in resp.text


# ── Stock Detail ──────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_stock_detail_unknown_ticker_does_not_crash(client):
    """GET /stock/FAKEXYZ must not return 500 — the page renders a 'data not found' state."""
    resp = client.get("/stock/FAKEXYZ", follow_redirects=True)
    assert resp.status_code < 500, (
        f"Stock detail page crashed for unknown ticker: HTTP {resp.status_code}\n"
        f"Body: {resp.text[:500]}"
    )


@pytest.mark.pages
def test_stock_detail_missing_data_does_not_crash(client):
    """GET /stock/ZZNOTREAL99 must not return 500 (renders 'data not found' fallback)."""
    resp = client.get("/stock/ZZNOTREAL99", follow_redirects=True)
    assert resp.status_code < 500, (
        f"Stock detail page crashed for unknown ticker: HTTP {resp.status_code}"
    )


@pytest.mark.pages
def test_stock_detail_embed_mode_still_shows_back_button(client):
    """Embed mode hides the full anchor nav (Verdict/Charts/Glossary etc.) and macro cards,
    but a bare Back button must still render — otherwise an embedded iframe (e.g. Home
    Assistant) has no way to return to the Watchlist/Portfolio page it was opened from."""
    resp = client.get("/stock/ZZNOTREAL99?embed=true", follow_redirects=True)
    assert resp.status_code < 500
    assert "history.back()" in resp.text
    assert "Verdict</a>" not in resp.text


@pytest.mark.pages
def test_stock_detail_quant_signals_only_does_not_crash(client, tmp_path, monkeypatch):
    """A ticker that has a quant_signals row but no stock_signals row yet (e.g. freshly
    fetched, before the full nightly pipeline has run) hits the 'UNIVERSE SCAN ONLY'
    fallback in page_routes.py — that dict must carry every key stock_detail.html reads
    (fifty_two_week_low/high, ma_50_day, ma_200_day, country), or the template 500s.
    The page_action block that reads those keys only renders when a daily Parquet file
    exists for the ticker, so one must be written here to actually exercise that path."""
    import pandas as pd
    import database as _db

    historical_dir = tmp_path / "historical"
    historical_dir.mkdir()
    df = pd.DataFrame({
        "Open": [100.0, 101.0], "High": [102.0, 103.0],
        "Low": [99.0, 100.0], "Close": [101.0, 102.0], "Volume": [1000, 1100],
    }, index=pd.date_range("2026-01-01", periods=2))
    df.to_parquet(historical_dir / "ZZQUANTONLY.parquet")
    monkeypatch.setattr("page_routes.HISTORICAL_DIR", historical_dir)

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT INTO quant_signals (ticker, date, close_price, volume, sma_50, sma_200) "
            "VALUES ('ZZQUANTONLY', '2026-01-05', 100.0, 1000, 95.0, 90.0)"
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/stock/ZZQUANTONLY", follow_redirects=True)
    assert resp.status_code < 500, (
        f"Stock detail page crashed for quant_signals-only ticker: HTTP {resp.status_code}\n"
        f"Body: {resp.text[:500]}"
    )

    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM quant_signals WHERE ticker = 'ZZQUANTONLY'")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.pages
def test_stock_detail_shows_setup_and_derived_tags(client):
    """Stock Detail must show the same badge-style tags as Portfolio/Watchlist —
    setup_tags (candlestick patterns), Trap Monitor phase, and every currently-active
    Pattern Detection family (not just Head & Shoulders) — next to the header."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type, setup_tags) "
            "VALUES ('ZZTAGGED', 50.0, 'USD', 'EQUITY', ?)",
            ('[{"name": "Bullish Engulfing", "tooltip": "A bullish reversal candlestick pattern."}]',),
        )
        conn.execute(
            "INSERT INTO trap_monitor_results (ticker, phase) VALUES ('ZZTAGGED', 'BULL_TRAP')"
        )
        conn.execute(
            """INSERT INTO pattern_detection_results
               (ticker, pattern_family, pattern_type, phase, points_json, lines_json, scan_ts)
               VALUES ('ZZTAGGED', 'head_shoulders', 'regular', 'CONFIRMED', '[]', '[]', '2026-01-01 00:00:00')"""
        )
        conn.execute(
            """INSERT INTO pattern_detection_results
               (ticker, pattern_family, pattern_type, phase, points_json, lines_json, scan_ts)
               VALUES ('ZZTAGGED', 'double_top_bottom', 'double_bottom', 'FORMING', '[]', '[]', '2026-01-01 00:00:00')"""
        )
        conn.commit()
    finally:
        conn.close()

    try:
        resp = client.get("/stock/ZZTAGGED", follow_redirects=True)
        assert resp.status_code < 500
        assert "Bullish Engulfing" in resp.text
        assert "setup-tag" in resp.text
        assert "Head &amp; Shoulders (Confirmed)" in resp.text
        assert "Double Bottom (Forming)" in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZTAGGED'")
            conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'ZZTAGGED'")
            conn.execute("DELETE FROM pattern_detection_results WHERE ticker = 'ZZTAGGED'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_trap_phase_button_shown_when_not_neutral(client):
    """A ticker with a non-NEUTRAL Trap Monitor phase must get a sub-menu button labeled
    with the phase's display name, linking to the shared /trap-monitor page."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZTRAPBTN', 50.0, 'USD', 'EQUITY')"
        )
        conn.execute(
            "INSERT INTO trap_monitor_results (ticker, phase) VALUES ('ZZTRAPBTN', 'ACTIVE_SELLOFF')"
        )
        conn.commit()
    finally:
        conn.close()

    try:
        resp = client.get("/stock/ZZTRAPBTN", follow_redirects=True)
        assert resp.status_code < 500
        assert '<a href="/trap-monitor" class="btn btn-outline-secondary btn-sm">Active Selloff</a>' in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZTRAPBTN'")
            conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'ZZTRAPBTN'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_trap_phase_button_hidden_when_neutral(client):
    """A ticker whose Trap Monitor phase is NEUTRAL must not show the trap-phase button."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZTRAPNEUT', 50.0, 'USD', 'EQUITY')"
        )
        conn.execute(
            "INSERT INTO trap_monitor_results (ticker, phase) VALUES ('ZZTRAPNEUT', 'NEUTRAL')"
        )
        conn.commit()
    finally:
        conn.close()

    try:
        resp = client.get("/stock/ZZTRAPNEUT", follow_redirects=True)
        assert resp.status_code < 500
        assert '<a href="/trap-monitor"' not in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZTRAPNEUT'")
            conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'ZZTRAPNEUT'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_etf_predictor_button_shown_when_configured(client):
    """A ticker configured as an ETF Predictor's etf_ticker must get a sub-menu button
    linking to that config's detail page."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZETFBTN', 50.0, 'USD', 'ETF')"
        )
        cursor = conn.execute(
            "INSERT INTO etf_predictor_configs (name, etf_ticker, constituents) "
            "VALUES ('ZZETFBTN Test Config', 'ZZETFBTN', '[]')"
        )
        config_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    try:
        resp = client.get("/stock/ZZETFBTN", follow_redirects=True)
        assert resp.status_code < 500
        assert f'<a href="/etf-predictor/{config_id}" class="btn btn-outline-secondary btn-sm">&#128202; ETF Predictor</a>' in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZETFBTN'")
            conn.execute("DELETE FROM etf_predictor_configs WHERE etf_ticker = 'ZZETFBTN'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_etf_predictor_button_hidden_when_not_configured(client):
    """A ticker with no matching ETF Predictor config must not show the button."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZNOETFBTN', 50.0, 'USD', 'EQUITY')"
        )
        conn.commit()
    finally:
        conn.close()

    try:
        resp = client.get("/stock/ZZNOETFBTN", follow_redirects=True)
        assert resp.status_code < 500
        assert "ETF Predictor</a>" not in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZNOETFBTN'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_watchlist_only_ticker_shows_position_targets_box(client):
    """A ticker with no built-in-account holding but present on the Watchlist has no
    "Your Position" box (portfolio_math is None), but must still get a standalone
    "Position Targets" box with a Watchlist row, since Set Targets no longer lives
    inside the Your Position box."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZWATCHONLY', 50.0, 'USD', 'EQUITY')"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    assert wl is not None
    add_watchlist_item(wl["id"], "ZZWATCHONLY", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/stock/ZZWATCHONLY", follow_redirects=True)
        assert resp.status_code < 500
        assert "Position Targets" in resp.text
        assert "Watchlist" in resp.text
        assert "Your Position (Global Aggregation)" not in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZWATCHONLY"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZWATCHONLY'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_gbp_target_input_prefilled_in_pounds_not_pence(client):
    """A GBp (LSE pence) holding_price_limits row stored as raw pence (e.g. 637) must
    prefill the Position Targets input box in pounds (6.37), matching the ML suggestion's
    units — not the raw pence value, which previously redisplayed as 0.06 after a user
    typed the suggested pounds figure and it was saved unconverted."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item, upsert_holding_price_limit

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZGBPTARGET.L', 637.0, 'GBp', 'EQUITY')"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    assert wl is not None
    add_watchlist_item(wl["id"], "ZZGBPTARGET.L", currency="GBp", quote_type="EQUITY")
    upsert_holding_price_limit(wl["id"], "ZZGBPTARGET.L", low_limit=637.0, high_limit=650.0)

    try:
        resp = client.get("/stock/ZZGBPTARGET.L", follow_redirects=True)
        assert resp.status_code < 500
        assert 'value="6.37"' in resp.text
        assert 'value="6.5"' in resp.text
        assert 'value="637.0"' not in resp.text
        assert 'value="650.0"' not in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZGBPTARGET.L"))
            conn.execute("DELETE FROM holding_price_limits WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZGBPTARGET.L"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZGBPTARGET.L'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_gbp_ticker_no_target_set_does_not_crash(client):
    """A GBp (LSE pence) watchlist ticker with no holding_price_limits row at all must not
    500 — limits.get('low_limit') is None (real None), not Jinja Undefined, so the pence
    conversion's `is not none` check must correctly skip the division."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZNOTARGET.L', 637.0, 'GBp', 'EQUITY')"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    assert wl is not None
    add_watchlist_item(wl["id"], "ZZNOTARGET.L", currency="GBp", quote_type="EQUITY")

    try:
        resp = client.get("/stock/ZZNOTARGET.L", follow_redirects=True)
        assert resp.status_code < 500
        assert "Position Targets" in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZNOTARGET.L"))
            conn.execute("DELETE FROM holding_price_limits WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZNOTARGET.L"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZNOTARGET.L'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_stock_detail_stale_pulse_fallback_price_does_not_crash(client):
    """get_all_cached_pulse() intentionally sets change_pct/is_positive to None when a
    stuck market_pulse_cache row falls back to stock_signals.current_price (see its own
    docstring) — cp is still truthy in that case, so the template must not assume
    cp.change_pct is numeric just because cp exists. Reproduces a real 500 (TypeError:
    NoneType doesn't define __round__) seen after a market_pulse_cache row went stale."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type, last_updated) "
            "VALUES ('ZZSTALEPULSE', 105.0, 'USD', 'EQUITY', datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
            "VALUES ('ZZSTALEPULSE', 'ZZ Stale Pulse', 100.0, 5.0, 5.0, 1, 0)"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    assert wl is not None
    add_watchlist_item(wl["id"], "ZZSTALEPULSE", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/stock/ZZSTALEPULSE", follow_redirects=True)
        assert resp.status_code < 500, (
            f"Stock detail page crashed on a stale-pulse fallback price: HTTP {resp.status_code}\n"
            f"Body: {resp.text[:500]}"
        )
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZSTALEPULSE"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZSTALEPULSE'")
            conn.execute("DELETE FROM market_pulse_cache WHERE ticker = 'ZZSTALEPULSE'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_watchlist_page_stale_pulse_fallback_price_does_not_crash(client):
    """Same fallback scenario as test_stock_detail_stale_pulse_fallback_price_does_not_crash,
    but on the /watchlist table row itself, which reads the same cached_pulse dict."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type, last_updated) "
            "VALUES ('ZZSTALEPULSEWL', 105.0, 'USD', 'EQUITY', datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
            "VALUES ('ZZSTALEPULSEWL', 'ZZ Stale Pulse WL', 100.0, 5.0, 5.0, 1, 0)"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    assert wl is not None
    add_watchlist_item(wl["id"], "ZZSTALEPULSEWL", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist", follow_redirects=True)
        assert resp.status_code < 500, (
            f"Watchlist page crashed on a stale-pulse fallback price: HTTP {resp.status_code}\n"
            f"Body: {resp.text[:500]}"
        )
        assert "ZZSTALEPULSEWL" in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZSTALEPULSEWL"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZSTALEPULSEWL'")
            conn.execute("DELETE FROM market_pulse_cache WHERE ticker = 'ZZSTALEPULSEWL'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_watchlist_ticker_link_propagates_embed_token(client):
    """When /watchlist is loaded with ?embed=true&embed_token=..., its ticker links to
    /stock/{ticker} must carry the same embed_token — otherwise clicking through from an
    embedded (no-session-cookie) view hits the login-redirect middleware branch."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZEMBEDLINK', 50.0, 'USD', 'EQUITY')"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "ZZEMBEDLINK", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist?embed=true&embed_token=my-test-token")
        assert resp.status_code < 500
        assert "/stock/ZZEMBEDLINK?embed=true&embed_token=my-test-token" in resp.text
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZEMBEDLINK"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZEMBEDLINK'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_watchlist_page_renders_new_analytics_and_removed_sections(client):
    """The Watchlist analytics overhaul: forensic score columns, Quality Grade / report-screen /
    trap / bubble badges render for a seeded ticker, and the Market Pulse widget + US 10Y
    Treasury / UK 10Y Gilt cards are gone from the page entirely."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_signals (
                ticker, current_price, currency, quote_type, composite_score, sector,
                piotroski_f_score, altman_z_score, beneish_m_score,
                roe, debt_to_equity, profit_margin, revenue_growth, current_ratio, trailing_pe,
                next_earnings_date
            ) VALUES (
                'ZZANALYTICS', 100.0, 'USD', 'EQUITY', 70, 'Technology',
                2, 1.2, -1.0,
                0.20, 30, 0.15, 0.10, 2.0, 20,
                'Unknown'
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO trap_monitor_results (ticker, phase) VALUES ('ZZANALYTICS', 'BULL_TRAP_RISK')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO bubble_radar_metrics (ticker, scan_date, flag) VALUES ('ZZANALYTICS', '2026-07-10', 'bubble')"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "ZZANALYTICS", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist")
        assert resp.status_code == 200
        body = resp.text
        assert "Grade A" in body
        assert "Quality Compounder" in body
        assert "Bull Trap Risk" in body
        assert "Bubble Risk" in body
        assert "Piotroski" in body
        assert "Altman Z" in body
        assert "Beneish M" in body
        assert "macro-cards-container" not in body
        assert "US 10Y Treasury" not in body
        assert "UK 10Y Gilt" not in body
        assert 'id="sectorFilter"' in body
        assert '<option value="Technology">Technology</option>' in body
        assert '<option value="Quality Compounder">Quality Compounder</option>' in body
        assert '<option value="Bubble Risk">Bubble Risk</option>' in body
        assert 'data-sector="Technology"' in body
        # 'Unknown' is the literal sentinel written when Yahoo has no earnings date — the visible
        # cell text must render as "-", not the raw string (it may still appear in data-sort).
        row_html = body[body.index('data-ticker="ZZANALYTICS"'):].split('</tr>')[0]
        assert '>-</td>' in row_html
        assert '>Unknown</td>' not in row_html
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZANALYTICS"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZANALYTICS'")
            conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'ZZANALYTICS'")
            conn.execute("DELETE FROM bubble_radar_metrics WHERE ticker = 'ZZANALYTICS'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_portfolio_and_watchlist_thead_th_count_matches_column_registry(client):
    """Regression guard against PORTFOLIO_CORE_COLUMNS/WATCHLIST_CORE_COLUMNS (hand-maintained,
    must mirror the templates' hardcoded <thead> order) drifting from the real rendered markup."""
    import table_columns_helpers as tch

    for page, url in (("portfolio", "/portfolio"), ("watchlist", "/watchlist")):
        resp = client.get(url)
        assert resp.status_code == 200
        rendered = resp.text.count("data-col-key=")
        # Body rows also carry data-col-key on optional <td>s, but the count of distinct <th>
        # entries is what column_picker.js indexes against — check the <thead> slice only.
        thead = resp.text[resp.text.index("<thead>"):resp.text.index("</thead>")]
        th_count = thead.count("data-col-key=")
        assert th_count == len(tch.all_columns_for_page(page)), (
            f"{page}: rendered {th_count} <th data-col-key> but registry has "
            f"{len(tch.all_columns_for_page(page))} columns"
        )


@pytest.mark.pages
def test_watchlist_page_renders_new_optional_columns_for_seeded_ticker(client):
    """New optional Watchlist columns (Fundamentals + Watchlist's Exit Target parity gap) render
    with the correct GBp-aware/percent formatting for a seeded ticker."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_signals (
                ticker, current_price, currency, quote_type, composite_score, sector,
                trailing_pe, roe
            ) VALUES ('ZZOPTCOL', 500.0, 'GBp', 'EQUITY', 55, 'Technology', 18.5, 0.20)
        """)
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "ZZOPTCOL", currency="GBp", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist")
        assert resp.status_code == 200
        body = resp.text
        assert 'data-col-key="trailing_pe"' in body
        assert 'data-col-key="roe"' in body
        assert 'data-col-key="vp_exit_zone"' in body
        row_html = body[body.index('data-ticker="ZZOPTCOL"'):].split('</tr>')[0]
        assert '18.50' in row_html
        assert '20.00%' in row_html
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZOPTCOL"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZOPTCOL'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_watchlist_page_renders_xray_and_earnings_vol_columns_for_seeded_ticker(client):
    """The stage-2 deferred columns (X-ray beta/annualised vol/dividend yield, Earnings
    Volatility edge score/implied move) render correctly for a ticker with rows in all three
    backing tables, using the fixed X-ray benchmark (xray_engine.BENCHMARK_SYMBOL)."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item
    from xray_engine import BENCHMARK_SYMBOL

    conn = _db.get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type, composite_score)
            VALUES ('ZZXRAYVOL', 100.0, 'USD', 'EQUITY', 55)
        """)
        conn.execute(
            "INSERT OR REPLACE INTO xray_risk_cache (ticker, benchmark, last_updated, beta, annualized_vol) "
            "VALUES ('ZZXRAYVOL', ?, '2026-07-19', 1.234, 0.256)",
            (BENCHMARK_SYMBOL,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO xray_dividend_cache (ticker, data_source, last_updated, dividend_yield_pct, dividend_in_base_currency) "
            "VALUES ('ZZXRAYVOL', 'YAHOO', '2026-07-19', 3.45, 12.0)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO earnings_volatility (ticker, next_earnings_date, implied_move_pct, historical_avg_move_pct, edge_score, options_volume, last_updated) "
            "VALUES ('ZZXRAYVOL', '2026-07-25', 4.23, 6.10, 1.87, 500, '2026-07-19')"
        )
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "ZZXRAYVOL", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist")
        assert resp.status_code == 200
        body = resp.text
        for key in ("xray_beta", "xray_annualized_vol", "xray_dividend_yield", "earnings_edge_score", "earnings_implied_move"):
            assert f'data-col-key="{key}"' in body
        row_html = body[body.index('data-ticker="ZZXRAYVOL"'):].split('</tr>')[0]
        assert "1.23" in row_html       # beta, ratio2
        assert "25.60%" in row_html     # annualized_vol, fraction -> pct_from_fraction
        assert "3.5%" in row_html       # xray dividend yield, already-percent -> pct_raw (1 decimal)
        assert "1.87" in row_html       # earnings edge score, ratio2
        assert "4.2%" in row_html       # implied move, pct_raw
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZXRAYVOL"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZXRAYVOL'")
            conn.execute("DELETE FROM xray_risk_cache WHERE ticker = 'ZZXRAYVOL'")
            conn.execute("DELETE FROM xray_dividend_cache WHERE ticker = 'ZZXRAYVOL'")
            conn.execute("DELETE FROM earnings_volatility WHERE ticker = 'ZZXRAYVOL'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_portfolio_and_watchlist_render_views_bootstrap_with_defaults(client):
    """window.*_VIEWS must be present on both pages and default to the 3 built-in views when
    nothing has been saved yet — this bootstrap script is unconditional (unlike the toolbar
    buttons, which only render once the page has content to act on)."""
    for global_name, url in (("PORTFOLIO_VIEWS", "/portfolio"), ("WATCHLIST_VIEWS", "/watchlist")):
        resp = client.get(url)
        assert resp.status_code == 200
        body = resp.text
        assert f"window.{global_name} = " in body
        assert "Technical Signals" in body
        assert "Position Targets" in body
        assert 'id="targetFilter"' not in body


@pytest.mark.pages
def test_portfolio_page_renders_views_button_when_holdings_exist(client):
    """The Views/Columns toolbar (like the pre-existing Columns button) only renders once the
    Portfolio page has a summary to show — confirm the button appears for a real holding."""
    from database import create_account, add_transaction, get_connection as _get_conn

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type) "
            "VALUES ('ZZVIEWSBTN', 50.0, 'USD', 'EQUITY')"
        )
        conn.commit()
    finally:
        conn.close()

    aid = create_account("Views Button Test Account", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZVIEWSBTN", company_name="Views Btn Co",
                     currency="USD", quantity=2, unit_price=50, exchange_rate=1.0)

    resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="viewsPickerToggle"' in body
    assert 'id="viewsPickerMenu"' in body


@pytest.mark.pages
def test_watchlist_page_renders_views_button(client):
    resp = client.get("/watchlist")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="viewsPickerToggle"' in body
    assert 'id="viewsPickerMenu"' in body


@pytest.mark.pages
def test_portfolio_page_renders_watchlist_parity_columns_for_builtin_holding(client):
    """Portfolio previously had no Piotroski/Altman/Beneish/Target Price columns at all, even
    though Watchlist already showed them from the same stock_signals row — this is the parity
    gap the optional-columns catalog closes."""
    from database import create_account, add_transaction, get_connection as _get_conn

    conn = _get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_signals (
                ticker, current_price, currency, quote_type, composite_score,
                piotroski_f_score, altman_z_score, beneish_m_score, target_price
            ) VALUES ('ZZPGPARITY', 50.0, 'USD', 'EQUITY', 60, 7, 3.2, -2.1, 65.0)
        """)
        conn.commit()
    finally:
        conn.close()

    aid = create_account("Parity Test Account", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZPGPARITY", company_name="Parity Co",
                     currency="USD", quantity=2, unit_price=50, exchange_rate=1.0)

    resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert resp.status_code == 200
    body = resp.text
    assert 'data-col-key="piotroski_f_score"' in body
    assert 'data-col-key="altman_z_score"' in body
    assert 'data-col-key="beneish_m_score"' in body
    assert 'data-col-key="target_price"' in body
    row_html = body[body.index('data-ticker="ZZPGPARITY"'):].split('</tr>')[0]
    assert '>7</td>' in row_html
    assert '$65.00' in row_html


@pytest.mark.pages
def test_watchlist_filters_only_show_present_values(client):
    """Filter dropdowns must only offer options with at least one matching row — an option with
    zero matches would filter the table down to nothing for no useful reason."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item

    conn = _db.get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_signals (
                ticker, current_price, currency, quote_type, composite_score, overall_signal, sector
            ) VALUES (
                'ZZFILTERONLY', 50.0, 'USD', 'EQUITY', 65, 'NEUTRAL', 'Utilities'
            )
        """)
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "ZZFILTERONLY", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist")
        assert resp.status_code == 200
        body = resp.text
        assert '<option value="NEUTRAL">Neutral</option>' in body
        assert '<option value="60">60' in body
        assert '<option value="Utilities">Utilities</option>' in body
        # This ticker has no fundamentals set, so it can't qualify for any report-screen tag —
        # if it's the only watchlist row, that option must not render.
        assert 'value="Quality Compounder"' not in body
        assert 'value="STRONG BUY"' not in body
        assert 'value="BEARISH / CAUTION"' not in body
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZFILTERONLY"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZFILTERONLY'")
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pages
def test_watchlist_target_column_no_longer_has_a_filter(client):
    """The 'Has Target Set' filter dropdown was removed in favor of the Views feature — Low/High
    Target render as plain always-available columns regardless of whether any ticker has a
    target set, and #targetFilter must never appear."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item, upsert_holding_price_limit

    conn = _db.get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type)
            VALUES ('ZZTARGET', 50.0, 'USD', 'EQUITY')
        """)
        conn.commit()
    finally:
        conn.close()

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "ZZTARGET", currency="USD", quote_type="EQUITY")

    try:
        resp = client.get("/watchlist")
        assert resp.status_code == 200
        assert 'id="targetFilter"' not in resp.text

        upsert_holding_price_limit(wl["id"], "ZZTARGET", low_limit=40.0, high_limit=60.0)

        resp = client.get("/watchlist")
        assert resp.status_code == 200
        body = resp.text
        assert 'id="targetFilter"' not in body
        assert 'data-has-target' not in body
        assert "40.00" in body and "60.00" in body
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZTARGET"))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZTARGET'")
            conn.execute("DELETE FROM holding_price_limits WHERE account_id = ? AND ticker = ?", (wl["id"], "ZZTARGET"))
            conn.commit()
        finally:
            conn.close()


# ── Safety net: no page returns 500 ──────────────────────────────────────────

@pytest.mark.pages
def test_no_page_route_returns_500(client):
    """
    All page routes must return < 500 status code.
    A 500 means the template rendering or DB query code crashed — a regression.
    """
    pages = [
        ("/portfolio",          "Portfolio"),
        ("/accounts",           "Accounts"),
        ("/watchlist",          "Watchlist"),
        ("/settings",           "Settings"),
        ("/notifications",      "Notifications"),
        ("/glossary",           "Glossary"),
        ("/glossary/learn",     "Glossary Learning"),
        ("/market-screener",    "Market Screener"),
        ("/quality-compounders", "Quality Compounders"),
        ("/garp-tenbaggers",    "GARP Tenbaggers"),
        ("/quality-on-sale",    "Quality on Sale"),
        ("/sector-trends",      "Sector Trends"),
        ("/relative-strength-leaders", "Relative Strength Leaders"),
        ("/mean-reversion",     "Mean Reversion"),
        ("/dividend-harvest",   "Dividend Harvest"),
        ("/market-sentiment",   "Market Sentiment"),
        ("/earnings-volatility","Earnings Volatility"),
        ("/earnings-volatility/accuracy","Earnings Volatility Accuracy"),
        ("/options-sandbox",    "Options Sandbox"),
        ("/news",               "News Feed"),
        ("/tools",              "Tools"),
        ("/stress-test",        "Historical Stress Tester"),
        ("/trap-monitor",       "Trap Monitor"),
        ("/ai-contagion",       "AI Sector Contagion Monitor"),
        ("/bubble-radar",       "Bubble Radar"),
        ("/change-password",    "Change Password"),
        ("/reset-password",     "Reset Password"),
        ("/dip-radar",          "Dip Radar Summary"),
        ("/forensic-screener",  "Forensic Screener"),
        ("/fx-drag",            "FX Drag Analyzer"),
        ("/monte-carlo",        "Monte Carlo Wealth Simulator"),
        ("/performance-analytics", "Portfolio Tearsheet"),
        ("/portfolio-optimizer", "Portfolio Optimizer"),
        ("/index/%5EGSPC",      "Index Detail (S&P 500)"),
        ("/index/%5EAXJO",      "Index Detail (ASX 200, no baseline parquet)"),
        ("/index/000001.SS",    "Index Detail (Shanghai Composite)"),
        ("/markets",            "Markets"),
        ("/login",              "Login"),
        ("/market-regime",      "Market Regime (HMM)"),
        ("/score-history",      "Score History"),
        ("/treasury-auctions",  "Sovereign Debt Auction Monitor"),
        ("/yahoo-api-usage",    "Yahoo Finance API Usage"),
        ("/pairs-spread",       "Pairs Spread Monitor"),
        ("/reports",            "Reports"),
        ("/pattern-detection",  "Pattern Detection"),
        ("/pattern-detection/NOTAREALTICKER", "Pattern Detection Detail"),
    ]
    failures = []
    for url, label in pages:
        r = client.get(url, follow_redirects=True)
        if r.status_code >= 500:
            failures.append(f"  {label} ({url}) → HTTP {r.status_code}")

    assert not failures, (
        "The following pages crashed with a server error (500+):\n"
        + "\n".join(failures)
        + "\n\nThis means the page template or its database query threw an exception."
    )


# ── Market Trap & Recovery Monitor ────────────────────────────────────────────

@pytest.mark.pages
def test_trap_monitor_page_loads(client):
    """GET /trap-monitor must load with an empty results table without crashing."""
    _assert_page_ok(client, "/trap-monitor", label="Trap Monitor")


# ── Pairs Spread Monitor ──────────────────────────────────────────────────────

@pytest.mark.pages
def test_pairs_spread_monitor_page_loads(client):
    """GET /pairs-spread must load with an empty results table without crashing."""
    _assert_page_ok(client, "/pairs-spread", label="Pairs Spread Monitor")


def test_pairs_spread_monitor_page_links_back_to_reports(client):
    """GET /pairs-spread must link back to the Reports hub, not the old Tools hub."""
    resp = client.get("/pairs-spread")
    assert resp.status_code == 200
    assert b'href="/reports"' in resp.content


# ── Pattern Detection ─────────────────────────────────────────────────────────

@pytest.mark.pages
def test_pattern_detection_page_loads(client):
    """GET /pattern-detection must load with an empty results table without crashing."""
    _assert_page_ok(client, "/pattern-detection", label="Pattern Detection")


@pytest.mark.pages
def test_pattern_detection_detail_page_loads_for_unknown_ticker(client):
    """GET /pattern-detection/{ticker} must not crash even for a ticker with no data on
    file — the page is a thin shell that fetches everything client-side."""
    resp = client.get("/pattern-detection/NOTAREALTICKER")
    assert resp.status_code == 200
    assert b"NOTAREALTICKER" in resp.content


@pytest.mark.pages
def test_head_shoulders_redirects_to_pattern_detection(client):
    """The old /head-shoulders route must redirect to the unified /pattern-detection page."""
    resp = client.get("/head-shoulders", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/pattern-detection"


# ── Reports Menu ───────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_reports_page_loads(client):
    """GET /reports must load the reports launcher page without a server error."""
    _assert_page_ok(client, "/reports", label="Reports")


def test_reports_page_links_to_pairs_spread(client):
    """GET /reports must link to the Pairs Spread Monitor report."""
    resp = client.get("/reports")
    assert resp.status_code == 200
    assert b'href="/pairs-spread"' in resp.content


# ── Sovereign Debt Auction Monitor ────────────────────────────────────────────

@pytest.mark.pages
def test_treasury_auctions_page_has_back_to_tools_and_run_now(client):
    """GET /treasury-auctions must expose a Back to Tools link and a Run Now trigger button."""
    resp = client.get("/treasury-auctions")
    assert resp.status_code == 200
    assert b'href="/tools"' in resp.content
    assert b'id="btn-run-auction-check"' in resp.content
    assert b'/api/trigger-treasury-auction-check' in resp.content


@pytest.mark.pages
def test_treasury_auctions_page_shows_plain_english_alert_in_summary(client):
    """The Data Summary callout must additively surface the same plain-English text as the notification."""
    import database as db
    conn = db.get_connection()
    try:
        conn.executemany(
            """INSERT INTO treasury_auction_results
               (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
               VALUES (?, '10Y', ?, 4.40, ?, 1.0, NULL, NULL, NULL, NULL, 0)""",
            [
                ("PAGEWEAK01", "2026-05-01", 2.8),
                ("PAGEWEAK02", "2026-04-01", 2.9),
                ("PAGEWEAK03", "2026-03-01", 2.7),
            ],
        )
        conn.execute(
            """INSERT INTO treasury_auction_results
               (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
               VALUES ('PAGEWEAKNOW', '10Y', '2026-06-01', 4.52, 2.3, 2.0, NULL, NULL, NULL, NULL, 1)"""
        )
        conn.commit()

        resp = client.get("/treasury-auctions")
        assert resp.status_code == 200
        assert "Most recent alert" in resp.text
        assert "Why it matters" in resp.text
        assert "Demand (bid-to-cover)" in resp.text
    finally:
        conn.execute("DELETE FROM treasury_auction_results WHERE cusip LIKE 'PAGEWEAK%'")
        conn.commit()
        conn.close()


# ── Log Viewer ────────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_log_viewer_page_loads(client):
    """GET /log-viewer must return 200 without crashing."""
    _assert_page_ok(client, "/log-viewer", label="Log Viewer")


# ── Admin Password Reset ──────────────────────────────────────────────────────

@pytest.mark.pages
def test_admin_reset_password_page_renders_when_flag_enabled(client):
    """When FORCE_PASSWORD_RESET is True the page must render (not 500)."""
    from unittest.mock import patch
    with patch("page_routes.load_config", return_value={"FORCE_PASSWORD_RESET": True}):
        resp = client.get("/admin-reset-password", follow_redirects=False)
    assert resp.status_code == 200
    assert b"Admin Password Reset" in resp.content


@pytest.mark.pages
def test_admin_reset_password_page_redirects_when_flag_disabled(client):
    """When FORCE_PASSWORD_RESET is False the page must redirect to /login."""
    from unittest.mock import patch
    with patch("page_routes.load_config", return_value={"FORCE_PASSWORD_RESET": False}):
        resp = client.get("/admin-reset-password", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


# ── ETF Predictor ─────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_etf_predictor_index_page_loads(client):
    """GET /etf-predictor must render with an empty config list without crashing."""
    _assert_page_ok(client, "/etf-predictor", label="ETF Predictor Index")


@pytest.mark.pages
def test_etf_predictor_detail_unknown_id_redirects(client):
    """GET /etf-predictor/99999 with no such config must redirect (not 500)."""
    resp = client.get("/etf-predictor/99999", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308), (
        f"Expected redirect for unknown ETF config id, got {resp.status_code}"
    )
    assert "/etf-predictor" in resp.headers["location"]


@pytest.mark.pages
def test_etf_predictor_detail_renders_bias_and_blend_tiles(client, monkeypatch):
    """GET /etf-predictor/{id} must render the new Bias-Corrected/Blend tiles without a Jinja crash."""
    import database as _db
    import pandas as pd
    import etf_predictor_engine

    config_id = _db.create_etf_predictor_config(
        name="Page Test ETF", etf_ticker="PTEST.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    fake_prediction = {
        "status": "success",
        "config_id": config_id,
        "predicted_price": 101.0,
        "last_etf_close": 100.0,
        "predicted_change_pct": 1.0,
        "data_source": "holdings",
        "signal_source": "intraday_premarket",
        "prediction_type": "us_open_impact",
        "session_relationship": "behind",
        "constituent_exchanges": ["NYSE"],
        "fx_rate": 1.0,
        "fx_pair": None,
        "as_of_utc": "2026-07-06 12:00 UTC",
        "as_of_local": "2026-07-06 12:00",
        "next_open_date": "2026-07-07",
        "n_holdings_used": 1,
        "holdings_engine": {"predicted_price": 101.0, "predicted_change_pct": 1.0, "contributions": [], "fx_adjustment_pct": 0.0},
        "regression_engine": None,
        "bias_corrected_price": 101.5,
        "bias_corrected_change_pct": 1.5,
        "blended_price": 101.2,
        "blended_change_pct": 1.2,
        "constituent_snapshot": "[]",
        "etf_info": {"exchange": "LSE", "currency": "GBP", "name": "PTEST.L"},
        "error": None,
    }
    try:
        monkeypatch.setattr(
            etf_predictor_engine, "fetch_shared_prediction_data",
            lambda cfg: (pd.DataFrame(), {}),
        )
        monkeypatch.setattr(
            etf_predictor_engine, "run_prediction",
            lambda cid, daily_df=None, intraday_data=None: fake_prediction,
        )
        monkeypatch.setattr(
            etf_predictor_engine, "get_etf_correlation_data",
            lambda cfg, days=60, daily_df=None: {
                "normalized_df": pd.DataFrame(), "rolling_corr": pd.Series(dtype=float), "error": "No data",
            },
        )
        monkeypatch.setattr(
            etf_predictor_engine, "get_etf_intraday_overlay_data",
            lambda cfg, prediction=None, intraday_data=None, daily_df=None: {
                "etf_series": pd.Series(dtype=float), "constituent_series": {}, "now_utc": None,
                "trading_date": None, "etf_last_close": 100.0, "constituent_prev_closes": {},
                "prediction": prediction or {}, "next_open_date": None,
                "constituent_exchanges": ["NYSE"], "session_relationship": "behind",
            },
        )
        resp = client.get(f"/etf-predictor/{config_id}")
        assert resp.status_code == 200
        assert "Bias-Corrected" in resp.text
        assert "Confidence-Weighted Blend" in resp.text
        assert "101.5" in resp.text
        assert "101.2" in resp.text
    finally:
        _db.soft_delete_etf_predictor_config(config_id)


# ── Account Detail ──────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_account_detail_unknown_id_redirects(client):
    """GET /accounts/99999 with no such account must redirect (not 500)."""
    resp = client.get("/accounts/99999", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308), (
        f"Expected redirect for unknown account id, got {resp.status_code}"
    )
    assert "/accounts" in resp.headers["location"]


@pytest.mark.pages
def test_account_detail_page_loads_for_real_account(client):
    """GET /accounts/{id} for a real account (with a transaction) must render without crashing."""
    import database as _db
    account_id = _db.create_account("Page Test Account", "GBP", initial_cash=100.0)
    _db.add_transaction(account_id, "Cash", "2026-01-01", unit_price=50)
    try:
        _assert_page_ok(client, f"/accounts/{account_id}", label="Account Detail")
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_account_detail_page_renders_compact_view_for_watchlist_account(client):
    """GET /accounts/{watchlist_id} must render the compact watchlist_account_detail.html, not the ledger view."""
    import database as _db
    wl = _db.get_watchlist_account()
    resp = client.get(f"/accounts/{wl['id']}")
    assert resp.status_code == 200
    assert "wl-items-table" in resp.text
    assert "watchlist_account.js" in resp.text


@pytest.mark.pages
def test_account_detail_page_redirects_house_to_dedicated_page(client):
    """The generic ledger page is for Trading only now — House has its own dedicated page."""
    import database as _db
    account_id = _db.create_account("Page Test House", "GBP", account_type="House")
    try:
        resp = client.get(f"/accounts/{account_id}", follow_redirects=False)
        assert resp.status_code in (302, 303, 307, 308)
        assert resp.headers["location"] == f"/accounts/{account_id}/house"
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_house_detail_unknown_id_redirects(client):
    """GET /accounts/99999/house with no such account must redirect (not 500)."""
    resp = client.get("/accounts/99999/house", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)
    assert "/accounts" in resp.headers["location"]


@pytest.mark.pages
def test_house_detail_rejects_non_house_account(client):
    """GET /accounts/{id}/house for a non-House account must redirect, not render the wrong page."""
    import database as _db
    account_id = _db.create_account("Page Test Trading2", "GBP", account_type="Trading")
    try:
        resp = client.get(f"/accounts/{account_id}/house", follow_redirects=False)
        assert resp.status_code in (302, 303, 307, 308)
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_house_detail_page_loads_with_chart_and_scraper(client):
    """House's dedicated page renders the value chart and the Scraper action, with no
    holdings/closed-positions/activities tables (House has no transaction ledger concept)."""
    import database as _db
    account_id = _db.create_account("Page Test House", "GBP", account_type="House")
    _db.add_price_history(account_id, "2026-01-01", 300000.0, source="purchase")
    try:
        _assert_page_ok(client, f"/accounts/{account_id}/house", label="House Account Detail")
        resp = client.get(f"/accounts/{account_id}/house")
        assert "Scraper" in resp.text
        assert "House Value Over Time" in resp.text
        assert 'id="holdingsTable"' not in resp.text
        assert 'id="closedTable"' not in resp.text
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_account_detail_page_redirects_pension_to_dedicated_page(client):
    """The generic ledger page is for Trading/House — Pension has its own dedicated page now."""
    import database as _db
    account_id = _db.create_account("Page Test Pension", "GBP", account_type="Pension")
    try:
        resp = client.get(f"/accounts/{account_id}", follow_redirects=False)
        assert resp.status_code in (302, 303, 307, 308)
        assert resp.headers["location"] == f"/accounts/{account_id}/pension"
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_pension_detail_unknown_id_redirects(client):
    """GET /accounts/99999/pension with no such account must redirect (not 500)."""
    resp = client.get("/accounts/99999/pension", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)
    assert "/accounts" in resp.headers["location"]


@pytest.mark.pages
def test_pension_detail_rejects_non_pension_account(client):
    """GET /accounts/{id}/pension for a non-Pension account must redirect, not render the wrong page."""
    import database as _db
    account_id = _db.create_account("Page Test Trading", "GBP", account_type="Trading")
    try:
        resp = client.get(f"/accounts/{account_id}/pension", follow_redirects=False)
        assert resp.status_code in (302, 303, 307, 308)
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_pension_detail_page_loads_with_actions(client):
    """Pension's dedicated page renders both charts, the Pay In / Admin Fee actions, and the
    Running Total Units / Notes activities columns."""
    import database as _db
    account_id = _db.create_account("Page Test Pension", "GBP", account_type="Pension")
    _db.add_transaction(
        account_id, "Buy", "2026-01-01", ticker=f"PENSION-{account_id}",
        quantity=10, unit_price=1.0, update_cash=False, notes="Opening balance",
    )
    try:
        _assert_page_ok(client, f"/accounts/{account_id}/pension", label="Pension Account Detail")
        resp = client.get(f"/accounts/{account_id}/pension")
        assert "Pay In" in resp.text
        assert "Admin Fee" in resp.text
        assert "Running Total Units" in resp.text
        assert "Pension Value" in resp.text
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_pension_activities_table_has_no_edit_button(client):
    """Every Pension activity row is system-generated (Opening Balance / Pay In / Admin Fee) —
    editing one through the generic Buy/Sell modal silently forces update_cash back to True,
    corrupting the cash-free Pension ledger. Edit must not be offered; Delete still is."""
    import database as _db
    account_id = _db.create_account("Page Test Pension NoEdit", "GBP", account_type="Pension")
    _db.add_transaction(
        account_id, "Buy", "2026-01-01", ticker=f"PENSION-{account_id}",
        quantity=10, unit_price=1.0, update_cash=False, notes="Opening balance",
    )
    try:
        resp = client.get(f"/accounts/{account_id}/pension")
        assert "editTransaction(" not in resp.text
        assert "deleteTransaction(" in resp.text
    finally:
        _db.soft_delete_account(account_id)


@pytest.mark.pages
def test_danger_zone_with_delete_confirmation_on_all_three_detail_pages(client):
    """Delete moved off the account tile onto each detail page's Danger Zone, gated behind a
    checkbox-confirmation modal rather than a one-click browser confirm()."""
    import database as _db
    trading_id = _db.create_account("Danger Zone Trading", "GBP", account_type="Trading")
    pension_id = _db.create_account("Danger Zone Pension", "GBP", account_type="Pension")
    house_id = _db.create_account("Danger Zone House", "GBP", account_type="House")
    try:
        for url in (f"/accounts/{trading_id}", f"/accounts/{pension_id}/pension", f"/accounts/{house_id}/house"):
            resp = client.get(url)
            assert resp.status_code == 200
            assert "Danger Zone" in resp.text
            assert 'id="deleteAccountModal"' in resp.text
            assert 'id="delete-account-confirm-checkbox"' in resp.text
            assert "account_danger_zone.js" in resp.text
    finally:
        _db.soft_delete_account(trading_id)
        _db.soft_delete_account(pension_id)
        _db.soft_delete_account(house_id)
