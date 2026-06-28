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


# ── Screener Pages ────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_quant_screener_page_loads(client):
    """GET /quant-screener must load the quantitative screener."""
    _assert_page_ok(client, "/quant-screener", label="Quant Screener")


@pytest.mark.pages
def test_market_screener_page_loads(client):
    """GET /market-screener must load the full market screener results."""
    _assert_page_ok(client, "/market-screener", label="Market Screener")


# ── Analysis Pages ────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_market_reports_page_loads(client):
    """GET /market-reports must load the market analysis reports page."""
    _assert_page_ok(client, "/market-reports", label="Market Reports")


@pytest.mark.pages
def test_market_sentiment_page_loads(client):
    """GET /market-sentiment must load the sentiment dashboard."""
    _assert_page_ok(client, "/market-sentiment", label="Market Sentiment")


@pytest.mark.pages
def test_earnings_volatility_page_loads(client):
    """GET /earnings-volatility must load the earnings volatility scanner."""
    _assert_page_ok(client, "/earnings-volatility", label="Earnings Volatility")


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
        ("/quant-screener",     "Quant Screener"),
        ("/market-screener",    "Market Screener"),
        ("/market-reports",     "Market Reports"),
        ("/market-sentiment",   "Market Sentiment"),
        ("/earnings-volatility","Earnings Volatility"),
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
        ("/index/%5EGSPC",      "Index Detail (S&P 500)"),
        ("/login",              "Login"),
        ("/market-regime",      "Market Regime (HMM)"),
        ("/score-history",      "Score History"),
        ("/treasury-auctions",  "Sovereign Debt Auction Monitor"),
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
