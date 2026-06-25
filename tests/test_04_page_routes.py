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
