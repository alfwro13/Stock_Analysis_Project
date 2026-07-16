"""
tests/test_03_api_post_endpoints.py  ── API POST ENDPOINTS

Verifies that every POST endpoint:
  1. Returns HTTP 200 (not a crash)
  2. Returns {"status": "success"} for action / trigger endpoints
  3. Returns a proper error response (not 500) for invalid input

"Trigger" endpoints (quant scan, ML training, sentiment, etc.) kick off
background tasks and return immediately — they are the most likely to
break after a code change without being obvious to the developer.

Endpoints that call external services (Ghostfolio, Nextcloud, yfinance)
have those services mocked to avoid network dependency.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from starlette.background import BackgroundTasks as _StarletteBackgroundTasks

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _json(resp) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\nStatus: {resp.status_code}\nBody: {resp.text[:500]}"
        ) from exc


def _assert_success(resp, endpoint: str):
    assert resp.status_code == 200, f"{endpoint}: Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success", (
        f"{endpoint}: Expected {{status: 'success'}}, got: {data}"
    )


# ── Notification CRUD ─────────────────────────────────────────────────────────

@pytest.mark.api
def test_post_notifications_mark_read(client):
    """POST /api/notifications/mark-read must mark all notifications as read."""
    resp = client.post("/api/notifications/mark-read")
    _assert_success(resp, "POST /api/notifications/mark-read")


@pytest.mark.api
def test_post_notifications_purge(client):
    """POST /api/notifications/purge must delete all notifications without crashing."""
    resp = client.post("/api/notifications/purge")
    _assert_success(resp, "POST /api/notifications/purge")


@pytest.mark.api
def test_post_notifications_purge_then_latest_empty(client):
    """After purging, GET /api/notifications/latest must return an empty list."""
    client.post("/api/notifications/purge")
    resp = client.get("/api/notifications/latest")
    data = _json(resp)
    assert data["notifications"] == [], "Notifications list must be empty after purge"


# ── Background-task trigger endpoints ────────────────────────────────────────
# These endpoints add a task to FastAPI's BackgroundTasks queue and return
# immediately with {"status": "success"}.  The actual work is done in the
# background.  We verify the HTTP contract only.

TRIGGER_ENDPOINTS = [
    ("/api/trigger-quant-scan",          "Quant Scan"),
    ("/api/trigger-earnings-scan",       "Earnings Scan"),
    ("/api/trigger-universe-update",     "Universe Update"),
    ("/api/trigger-universe-quant-scan", "Universe Quant Scan"),
    ("/api/trigger-sentiment-scan",      "Sentiment Scan"),
    ("/api/ml/trigger-backfill",         "ML Historical Backfill"),
    ("/api/ml/trigger-training",         "ML Training"),
    ("/api/ml/trigger-inference",        "ML Inference"),
    ("/api/macro/init-pipeline",         "Macro Init Pipeline"),
    ("/api/macro/run-pipeline",          "Macro Run Pipeline"),
    ("/api/universe/sync-indices",       "Sync Indices"),
    ("/api/universe/sync-profiler",      "Sync Profiler"),
    ("/api/universe/deep-sync",          "Universe Deep Sync"),
    ("/api/update",                      "Full Data Update"),
    ("/api/sync-ghostfolio",             "Ghostfolio Sync"),
    ("/api/trigger-freetrade-sync",      "Freetrade Sync"),
    ("/api/news-feed/run-now",           "News Feed Run Now"),
    ("/api/backup/run",                  "Automated Backup"),
    ("/api/trigger-treasury-auction-check", "Treasury Auction Check"),
]


@pytest.mark.api
@pytest.mark.parametrize("endpoint,label", TRIGGER_ENDPOINTS, ids=[t[1] for t in TRIGGER_ENDPOINTS])
def test_trigger_endpoint_returns_success(client, endpoint, label):
    """
    Every background-task trigger must return HTTP 200 with status='success'.

    BackgroundTasks.add_task is patched to prevent the actual heavy work
    (yfinance downloads, ML training, etc.) from running during tests.
    We are only testing the HTTP contract here — that the handler does not
    crash before queuing the task and returning the response.
    """
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post(endpoint)
    assert resp.status_code == 200, f"{label}: Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success", (
        f"{label}: Expected {{status: 'success'}}, got: {data}"
    )


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.mark.api
def test_post_settings_with_valid_payload(client, confirm_token):
    """POST /api/settings with a valid partial payload must return status=success."""
    payload = {
        "UI_PREFERENCES": {
            "LIVE_PORTFOLIO": True,
            "LIVE_WATCHLIST": True,
            "LIVE_DETAILS": False,
            "REFRESH_RATE": 30,
            "FREETRADE_ONLY_MODE": False,
        }
    }
    resp = client.post("/api/settings", json=payload, headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success, got: {data}"


@pytest.mark.api
def test_post_settings_with_account_currencies(client, confirm_token):
    """POST /api/settings with ACCOUNT_CURRENCIES must persist and round-trip via load_config."""
    import config as _config
    payload = {"ACCOUNT_CURRENCIES": ["GBP", "GBp", "USD", "EUR", "JPY"]}
    resp = client.post("/api/settings", json=payload, headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    assert _config.load_config().get("ACCOUNT_CURRENCIES") == ["GBP", "GBp", "USD", "EUR", "JPY"]


@pytest.mark.api
def test_post_settings_with_notification_routing(client, confirm_token):
    """POST /api/settings with NOTIFICATION_ROUTING must persist and round-trip via load_config."""
    import config as _config
    payload = {
        "NOTIFICATION_ROUTING": {
            "crash_alert": {"log_file": True, "in_app": True, "nextcloud_talk": False},
            "quant_analysis_job": {"log_file": False, "in_app": True, "nextcloud_talk": True},
        }
    }
    resp = client.post("/api/settings", json=payload, headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    routing = _config.load_config().get("NOTIFICATION_ROUTING", {})
    assert routing.get("crash_alert", {}).get("nextcloud_talk") is False
    assert routing.get("quant_analysis_job", {}).get("nextcloud_talk") is True


@pytest.mark.api
def test_post_settings_ghostfolio_disabled_purges_files_and_clears_accounts(client, confirm_token):
    """POST /api/settings with GHOSTFOLIO_ENABLED=False must purge portfolio.json/watchlist.json,
    clear GHOSTFOLIO_ACCOUNTS, and force GHOSTFOLIO_SYNC.ENABLED off."""
    import config as _config
    payload = {
        "GHOSTFOLIO_ENABLED": False,
        "GHOSTFOLIO_ACCOUNTS": {"discovered": [{"id": "acc-1", "name": "Test", "currency": "GBP"}], "active": ["acc-1"]},
        "SCHEDULING": {"GHOSTFOLIO_SYNC": {"ENABLED": True, "FREQUENCY": "mon-fri", "INTERVAL_HOURS": 0, "TIME": "06:00"}},
    }
    with patch("api_routes_system.purge_ghostfolio_files") as mock_purge:
        resp = client.post("/api/settings", json=payload, headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    mock_purge.assert_called_once()
    cfg = _config.load_config()
    assert cfg.get("GHOSTFOLIO_ENABLED") is False
    assert cfg.get("GHOSTFOLIO_ACCOUNTS") == {"discovered": [], "active": []}
    assert cfg.get("SCHEDULING", {}).get("GHOSTFOLIO_SYNC", {}).get("ENABLED") is False


@pytest.mark.api
def test_post_settings_ghostfolio_enabled_does_not_purge(client, confirm_token):
    """POST /api/settings with GHOSTFOLIO_ENABLED=True must not touch portfolio.json/watchlist.json."""
    payload = {"GHOSTFOLIO_ENABLED": True}
    with patch("api_routes_system.purge_ghostfolio_files") as mock_purge:
        resp = client.post("/api/settings", json=payload, headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200
    assert _json(resp).get("status") == "success"
    mock_purge.assert_not_called()


@pytest.mark.api
def test_post_settings_with_position_sizing(client, confirm_token):
    """POST /api/settings with position sizing values must return status=success."""
    payload = {
        "POSITION_SIZING": {
            "ACCOUNT_VALUE": 50000.0,
            "RISK_PCT": 1.0,
            "STOP_MULTIPLE": 2.0,
        }
    }
    resp = client.post("/api/settings", json=payload, headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"


# ── Watchlist (native store, no Ghostfolio) ──────────────────────────────────

@pytest.mark.api
def test_post_watchlist_add_with_mock(client):
    """POST /api/watchlist/add inserts into watchlist_items (Yahoo profile/history calls mocked out)."""
    import database as _db
    fake_meta = {"company_name": "Apple Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE"}
    with (
        patch("api_routes.resolve_watchlist_metadata", return_value=fake_meta),
        patch("api_routes.update_single_profile"),
        patch("api_routes.fetch_and_save_single_ticker"),
        patch("api_routes.QuantEngine"),
    ):
        resp = client.post("/api/watchlist/add", json={"ticker": "AAPL"})
    assert resp.status_code == 200
    assert _json(resp)["status"] == "success"
    wl = _db.get_watchlist_account()
    assert "AAPL" in _db.get_watchlist_tickers()
    _db.remove_watchlist_ticker(wl["id"], "AAPL")


@pytest.mark.api
def test_post_watchlist_add_unknown_ticker_triggers_yahoo_fetch(client):
    """A ticker with no existing asset_profiles row must get an immediate profile + price-history
    fetch queued, not wait for the next nightly scan cycle."""
    import database as _db
    fake_meta = {"company_name": "Zzz Corp.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE"}
    with (
        patch("api_routes.resolve_watchlist_metadata", return_value=fake_meta),
        patch("api_routes.update_single_profile") as mock_profile,
        patch("api_routes.fetch_and_save_single_ticker") as mock_fetch,
        patch("api_routes.QuantEngine"),
    ):
        resp = client.post("/api/watchlist/add", json={"ticker": "ZZZNOTREAL3"})
    assert resp.status_code == 200
    mock_profile.assert_called_once_with("ZZZNOTREAL3")
    mock_fetch.assert_called_once_with("ZZZNOTREAL3")

    wl = _db.get_watchlist_account()
    _db.remove_watchlist_ticker(wl["id"], "ZZZNOTREAL3")


@pytest.mark.api
def test_post_watchlist_add_known_ticker_skips_yahoo_fetch(client):
    """A ticker that already has an asset_profiles row must not re-trigger a background fetch."""
    import database as _db
    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO asset_profiles (ticker, company_name) VALUES (?, ?)",
            ("AAPL", "Apple Inc."),
        )
        conn.commit()
    finally:
        conn.close()

    fake_meta = {"company_name": "Apple Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE"}
    with (
        patch("api_routes.resolve_watchlist_metadata", return_value=fake_meta),
        patch("api_routes.update_single_profile") as mock_profile,
        patch("api_routes.fetch_and_save_single_ticker") as mock_fetch,
        patch("api_routes.QuantEngine"),
    ):
        resp = client.post("/api/watchlist/add", json={"ticker": "AAPL"})
    assert resp.status_code == 200
    mock_profile.assert_not_called()
    mock_fetch.assert_not_called()

    wl = _db.get_watchlist_account()
    _db.remove_watchlist_ticker(wl["id"], "AAPL")


@pytest.mark.api
def test_post_watchlist_add_missing_stock_signals_row_triggers_analyze(client):
    """A ticker that's already 'known' (asset_profiles exists) but has no stock_signals row yet
    (e.g. profile/parquet were fetched but the quant scan never ran) must still get an immediate
    analyze_ticker() call queued — otherwise it silently never appears on the Watchlist page,
    since that page's query filters FROM stock_signals."""
    import database as _db
    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO asset_profiles (ticker, company_name) VALUES (?, ?)",
            ("ZZANALYZEME", "Analyze Me Inc."),
        )
        conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZANALYZEME'")
        conn.commit()
    finally:
        conn.close()

    fake_meta = {"company_name": "Analyze Me Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE"}
    mock_engine = MagicMock()
    with (
        patch("api_routes.resolve_watchlist_metadata", return_value=fake_meta),
        patch("api_routes.update_single_profile") as mock_profile,
        patch("api_routes.fetch_and_save_single_ticker") as mock_fetch,
        patch("api_routes.QuantEngine", return_value=mock_engine),
    ):
        resp = client.post("/api/watchlist/add", json={"ticker": "ZZANALYZEME"})
    assert resp.status_code == 200
    mock_profile.assert_not_called()
    mock_fetch.assert_not_called()
    mock_engine.analyze_ticker.assert_called_once_with("ZZANALYZEME")

    wl = _db.get_watchlist_account()
    _db.remove_watchlist_ticker(wl["id"], "ZZANALYZEME")
    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM asset_profiles WHERE ticker = 'ZZANALYZEME'")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.api
def test_post_watchlist_add_existing_stock_signals_row_skips_analyze(client):
    """A ticker that already has a stock_signals row must not re-trigger analyze_ticker()."""
    import database as _db
    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO asset_profiles (ticker, company_name) VALUES (?, ?)",
            ("AAPL", "Apple Inc."),
        )
        conn.execute("INSERT OR REPLACE INTO stock_signals (ticker) VALUES ('AAPL')")
        conn.commit()
    finally:
        conn.close()

    fake_meta = {"company_name": "Apple Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE"}
    mock_engine = MagicMock()
    with (
        patch("api_routes.resolve_watchlist_metadata", return_value=fake_meta),
        patch("api_routes.update_single_profile"),
        patch("api_routes.fetch_and_save_single_ticker"),
        patch("api_routes.QuantEngine", return_value=mock_engine),
    ):
        resp = client.post("/api/watchlist/add", json={"ticker": "AAPL"})
    assert resp.status_code == 200
    mock_engine.analyze_ticker.assert_not_called()

    wl = _db.get_watchlist_account()
    _db.remove_watchlist_ticker(wl["id"], "AAPL")
    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM stock_signals WHERE ticker = 'AAPL'")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.api
def test_post_watchlist_remove_with_mock(client):
    """POST /api/watchlist/remove deletes from watchlist_items."""
    import database as _db
    wl = _db.get_watchlist_account()
    _db.add_watchlist_item(wl["id"], "MSFT", "Microsoft Corp.", "USD", "EQUITY", "NYSE")

    resp = client.post("/api/watchlist/remove", json={"ticker": "MSFT"})
    assert resp.status_code == 200
    assert _json(resp)["status"] == "success"
    assert "MSFT" not in _db.get_watchlist_tickers()


# ── Options payoff (pure math, no external calls) ────────────────────────────

@pytest.mark.api
def test_post_options_payoff_call_option(client):
    """POST /api/options/payoff with a simple call option must return a payoff matrix."""
    payload = {
        "current_price": 150.0,
        "legs": [
            {"type": "call", "strike": 155.0, "premium": 3.50, "position": "long", "quantity": 1}
        ],
    }
    resp = client.post("/api/options/payoff", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    data = _json(resp)
    assert "payoff" in data or "data" in data or "chart" in data or "prices" in data, (
        f"Options payoff response missing expected key: {data}"
    )


@pytest.mark.api
def test_post_options_payoff_invalid_input(client):
    """POST /api/options/payoff with missing fields must return 422, not 500."""
    resp = client.post("/api/options/payoff", json={"bad": "payload"})
    assert resp.status_code in (400, 422), (
        f"Expected 400 or 422 for invalid payoff input, got {resp.status_code}"
    )


# ── Market Pulse ──────────────────────────────────────────────────────────────

@pytest.mark.api
def test_post_market_pulse_with_mocked_fetch(client):
    """POST /api/market-pulse must return 200 (fetch is mocked to avoid network calls)."""
    with patch("api_routes.fetch_and_save_pulse") as mock_fetch:
        mock_fetch.return_value = None
        resp = client.post("/api/market-pulse", json={"tickers": ["AAPL", "MSFT"]})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"


# ── Data refresh single (mocked DataEngine) ──────────────────────────────────

@pytest.mark.api
def test_post_data_refresh_single_with_mock(client):
    """POST /api/data/refresh-single must not crash (heavy deps mocked)."""
    with patch("api_routes.update_single_profile"), \
         patch("api_routes.DataEngine") as MockDE, \
         patch("api_routes.QuantEngine") as MockQE, \
         patch("api_routes.update_daily_ml_predictions"), \
         patch("api_routes.update_all_tail_risks"), \
         patch("api_routes.update_all_sentiment"):
        instance = MockDE.return_value
        instance.fetch_and_save_data.return_value = True
        resp = client.post("/api/data/refresh-single", json={"ticker": "AAPL"})
    assert resp.status_code in (200, 400, 404, 422), (
        f"Unexpected status {resp.status_code} for single refresh"
    )


# ── Safety net: no POST trigger returns 500 ──────────────────────────────────

@pytest.mark.api
def test_no_trigger_endpoint_returns_500(client):
    """
    All background-task trigger endpoints must return < 500.
    A 500 means the routing / handler code itself crashed before even
    queuing the background task — that is always a regression.
    """
    failures = []
    for endpoint, label in TRIGGER_ENDPOINTS:
        with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
            r = client.post(endpoint)
        if r.status_code >= 500:
            failures.append(f"  {label} ({endpoint}) → HTTP {r.status_code}")

    assert not failures, (
        "The following POST trigger endpoints returned server errors (500+):\n"
        + "\n".join(failures)
    )


# ── Import security: path traversal ──────────────────────────────────────────

@pytest.mark.api
class TestImportServerSecurity:

    def test_path_traversal_with_dotdot_is_rejected(self, client):
        resp = client.post("/api/universe/import/server", json={"filename": "../../../etc/passwd.csv"})
        assert resp.status_code == 400
        assert _json(resp).get("status") == "error"

    def test_absolute_path_disguised_as_csv_is_rejected(self, client):
        resp = client.post("/api/universe/import/server", json={"filename": "/etc/passwd.csv"})
        assert resp.status_code == 400
        assert _json(resp).get("status") == "error"

    def test_non_csv_extension_is_rejected(self, client):
        resp = client.post("/api/universe/import/server", json={"filename": "data.txt"})
        assert resp.status_code == 400

    def test_valid_filename_passes_path_check(self, client):
        # A well-formed filename should pass the path/extension guards.
        # The file won't exist on disk, so we expect 404 (not 400).
        resp = client.post("/api/universe/import/server", json={"filename": "my_universe.csv"})
        assert resp.status_code == 404


# ── Intraday Dip Radar ────────────────────────────────────────────────────────

@pytest.mark.api
class TestIntradayDipRadar:

    def test_add_monitor_returns_ok(self, client):
        """POST /api/intraday-monitor/add must return 200 with status='ok' and the ticker."""
        resp = client.post("/api/intraday-monitor/add", json={"ticker": "AAPL"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = _json(resp)
        assert data.get("status") == "ok", f"Expected status='ok', got: {data}"
        assert data.get("ticker") == "AAPL"

    def test_add_monitor_persists_to_db(self, client):
        """After adding, the ticker must appear as active in the DB."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import database as _db
        from datetime import date

        client.post("/api/intraday-monitor/add", json={"ticker": "MSFT"})
        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT is_active FROM intraday_monitors WHERE ticker = ? AND date_added = ?",
                ("MSFT", date.today().isoformat()),
            ).fetchone()
            assert row is not None, "Monitor row not found in DB after add"
            assert row["is_active"] == 1, "Monitor was not set to is_active=1"
        finally:
            conn.execute("DELETE FROM intraday_monitors WHERE ticker = 'MSFT'")
            conn.commit()
            conn.close()

    def test_remove_monitor_returns_ok(self, client):
        """POST /api/intraday-monitor/remove must return 200 with status='ok'."""
        client.post("/api/intraday-monitor/add", json={"ticker": "GOOG"})
        resp = client.post("/api/intraday-monitor/remove", json={"ticker": "GOOG"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = _json(resp)
        assert data.get("status") == "ok"

    def test_remove_monitor_sets_inactive_in_db(self, client):
        """After removing, is_active must be 0 in the DB."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import database as _db

        client.post("/api/intraday-monitor/add", json={"ticker": "META"})
        client.post("/api/intraday-monitor/remove", json={"ticker": "META"})
        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT is_active FROM intraday_monitors WHERE ticker = 'META'"
            ).fetchone()
            assert row is not None, "Monitor row disappeared after remove"
            assert row["is_active"] == 0, "is_active was not set to 0 after remove"
        finally:
            conn.execute("DELETE FROM intraday_monitors WHERE ticker = 'META'")
            conn.commit()
            conn.close()

    def test_add_monitor_missing_ticker_returns_422(self, client):
        """POST /api/intraday-monitor/add without a ticker body must return 422 (validation error)."""
        resp = client.post("/api/intraday-monitor/add", json={})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_add_monitor_idempotent(self, client):
        """Calling add twice for the same ticker must not duplicate the DB row."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import database as _db

        client.post("/api/intraday-monitor/add", json={"ticker": "NVDA"})
        client.post("/api/intraday-monitor/add", json={"ticker": "NVDA"})
        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM intraday_monitors WHERE ticker = 'NVDA'"
            ).fetchone()
            assert row["cnt"] == 1, "Duplicate row created by idempotent add"
        finally:
            conn.execute("DELETE FROM intraday_monitors WHERE ticker = 'NVDA'")
            conn.commit()
            conn.close()


# ── Market Trap & Recovery Monitor ────────────────────────────────────────────

@pytest.mark.api
def test_trap_monitor_run_returns_success(client):
    """POST /api/trap-monitor/run must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/trap-monitor/run")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


# ── Pairs Spread Monitor ──────────────────────────────────────────────────────

@pytest.mark.api
def test_pairs_spread_run_returns_success(client):
    """POST /api/pairs-spread/run must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/pairs-spread/run")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


@pytest.mark.api
def test_pairs_spread_run_universe_returns_success(client):
    """POST /api/pairs-spread/run-universe must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/pairs-spread/run-universe")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


# ── Forensic Screener ──────────────────────────────────────────────────────────

@pytest.mark.api
def test_forensic_run_fetch_returns_success(client):
    """POST /api/forensic-scores/run-fetch must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/forensic-scores/run-fetch")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


@pytest.mark.api
def test_forensic_run_score_returns_success(client):
    """POST /api/forensic-scores/run-score must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/forensic-scores/run-score")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


# ── AI Sector Contagion ───────────────────────────────────────────────────────

@pytest.mark.api
def test_ai_contagion_trigger_returns_success(client):
    """POST /api/ai-contagion/trigger must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/ai-contagion/trigger")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


# ── Market Regime ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_market_regime_run_returns_success(client):
    """POST /api/market-regime/run must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/market-regime/run")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Expected success, got: {data}"


# ── Stress Test ───────────────────────────────────────────────────────────────

@pytest.mark.api
def test_stress_test_run_rejects_unknown_scenario(client):
    """POST /api/stress-test/run with an unknown scenario_id must return 400."""
    resp = client.post("/api/stress-test/run", json={"scenario_id": "NONEXISTENT_SCENARIO_XYZ"})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "error"


@pytest.mark.api
def test_stress_test_run_valid_scenario(client):
    """POST /api/stress-test/run with a known scenario must return 200 or 400 (not 500)."""
    from stress_engine import SCENARIOS
    scenario_id = next(iter(SCENARIOS))
    resp = client.post("/api/stress-test/run", json={"scenario_id": scenario_id, "account_id": "all"})
    assert resp.status_code in (200, 400), f"Must not 500, got {resp.status_code}: {resp.text}"


# ── Monte Carlo ───────────────────────────────────────────────────────────────

@pytest.mark.api
def test_monte_carlo_run_returns_success(client):
    """POST /api/monte-carlo/run must return 200 with status=success and required keys."""
    payload = {
        "portfolio_value": 50_000.0,
        "monthly_contribution": 0.0,
        "horizon_years": 10,
        "target_wealth": 100_000.0,
        "drift_overrides": {},
        "inflation_pct": 2.5,
    }
    resp = client.post("/api/monte-carlo/run", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success, got: {data}"
    for key in ("percentiles", "percentiles_real", "probability_of_success", "median_final", "p5_final"):
        assert key in data, f"Missing key '{key}' in response"
    for pkey in ("p5", "p25", "p50", "p75", "p95"):
        assert pkey in data["percentiles"], f"Missing percentile '{pkey}'"
        assert len(data["percentiles"][pkey]) == 11  # year 0..10


def test_monte_carlo_rejects_horizon_above_50(client):
    """horizon_years > 50 must be rejected with 422 (Field le=50 constraint)."""
    payload = {"portfolio_value": 50_000.0, "horizon_years": 51}
    resp = client.post("/api/monte-carlo/run", json=payload)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


def test_monte_carlo_rejects_nonpositive_portfolio_value(client):
    """portfolio_value <= 0 must be rejected with 422 (Field gt=0 constraint)."""
    for bad_value in (0.0, -1.0):
        payload = {"portfolio_value": bad_value, "horizon_years": 10}
        resp = client.post("/api/monte-carlo/run", json=payload)
        assert resp.status_code == 422, f"Expected 422 for pv={bad_value}, got {resp.status_code}"


# ── Portfolio Optimizer ─────────────────────────────────────────────────────────

@pytest.mark.api
def test_portfolio_optimizer_run_returns_200_for_empty_scope(client):
    """POST /api/portfolio-optimizer/run must return 200 with status=error (not a 500) for a
    freshly created account with zero holdings — mirrors optimize_portfolio()'s
    RuntimeError-to-error-status contract. An unrecognised account_id falls back to the "all"
    scope (accounts_engine._classify_scope), so a genuinely empty scope must be a real,
    holdings-free account id, not an arbitrary string."""
    import database as _db

    account_id = _db.create_account("POA Empty Test", "GBP")
    resp = client.post("/api/portfolio-optimizer/run", json={"account_id": f"acct:{account_id}"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert data["status"] == "error"


@pytest.mark.api
def test_portfolio_optimizer_run_full_report(client):
    """POST /api/portfolio-optimizer/run with two held tickers plus 252 days of cached returns
    must return closed-form Min-Variance/Max-Sharpe weights and an efficient frontier."""
    import json as _json_mod
    import numpy as np
    import pandas as pd
    import database as _db
    from xray_engine import BENCHMARK_SYMBOL

    conn = _db.get_connection()
    try:
        for ticker in ("POAT1", "POAT2"):
            conn.execute(
                "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
                (ticker, 100.0, "GBP"),
            )
        rng = np.random.default_rng(11)
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", periods=252)]
        for ticker, mean, vol in (("POAT1", 0.0004, 0.012), ("POAT2", 0.0003, 0.009)):
            rets = rng.normal(mean, vol, 252).tolist()
            conn.execute(
                """INSERT OR REPLACE INTO xray_returns_cache
                   (ticker, benchmark, last_updated, dates_json, returns_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (ticker, BENCHMARK_SYMBOL, "2026-06-03", _json_mod.dumps(dates), _json_mod.dumps(rets)),
            )
        conn.commit()
    finally:
        conn.close()

    account_id = _db.create_account("POA Full Test", "GBP")
    _db.add_transaction(account_id, "Buy", "2026-01-05", ticker="POAT1", currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)
    _db.add_transaction(account_id, "Buy", "2026-01-05", ticker="POAT2", currency="GBP",
                         quantity=5, unit_price=50, exchange_rate=1.0)

    with patch("xray_engine.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}, "RISK_FREE_RATE": 0.045}), \
         patch("portfolio_optimizer_engine.load_config", return_value={"RISK_FREE_RATE": 0.045}):
        resp = client.post("/api/portfolio-optimizer/run", json={"account_id": f"acct:{account_id}"})

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert data["status"] == "success"
    assert data["weights"] is not None
    assert {w["symbol"] for w in data["weights"]} == {"POAT1", "POAT2"}
    assert data["efficient_frontier"] is not None


# ── Backup & Recovery ────────────────────────────────────────────────────────

@pytest.mark.api
def test_backup_restore_missing_confirm_token_returns_422(client):
    """POST /api/backup/restore without the required X-Confirm-Token header must return 422."""
    resp = client.post("/api/backup/restore", json={"filename": "backup_20260101_000000.tar.gz"})
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.api
def test_backup_restore_wrong_confirm_token_returns_403(client):
    """POST /api/backup/restore with an incorrect X-Confirm-Token must return 403."""
    resp = client.post(
        "/api/backup/restore",
        json={"filename": "backup_20260101_000000.tar.gz"},
        headers={"X-Confirm-Token": "wrong-token"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"


@pytest.mark.api
def test_backup_restore_rejects_path_traversal(client, confirm_token):
    """POST /api/backup/restore with a path-traversal filename must return 400, not touch disk."""
    resp = client.post(
        "/api/backup/restore",
        json={"filename": "../../etc/passwd"},
        headers={"X-Confirm-Token": confirm_token},
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert _json(resp).get("status") == "error"


@pytest.mark.api
def test_backup_restore_success(client, confirm_token):
    """POST /api/backup/restore with a valid filename must return status=success when the engine succeeds."""
    with patch("api_routes_triggers.restore_backup", return_value={"status": "success", "message": "Restore completed."}):
        resp = client.post(
            "/api/backup/restore",
            json={"filename": "backup_20260101_000000.tar.gz"},
            headers={"X-Confirm-Token": confirm_token},
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert _json(resp).get("status") == "success"


@pytest.mark.api
def test_backup_restore_engine_error_returns_500(client, confirm_token):
    """POST /api/backup/restore must surface an engine-level failure as a 500 with status=error."""
    with patch("api_routes_triggers.restore_backup", return_value={"status": "error", "message": "Backup file not found"}):
        resp = client.post(
            "/api/backup/restore",
            json={"filename": "backup_missing.tar.gz"},
            headers={"X-Confirm-Token": confirm_token},
        )
    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"
    assert _json(resp).get("status") == "error"


@pytest.mark.api
def test_git_pull_flags_requirements_txt_change(client, confirm_token):
    """POST /api/system/git-pull must flag requirements_changed when it appears in the pulled diff."""
    import api_routes_system

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="oldsha\n", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Updating oldsha..newsha\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return MagicMock(returncode=0, stdout="requirements.txt\nmain.py\n", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    api_routes_system._requirements_changed_pending = False
    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        resp = client.post("/api/system/git-pull", headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200
    body = _json(resp)
    assert body["requirements_changed"] is True
    assert api_routes_system._requirements_changed_pending is True
    api_routes_system._requirements_changed_pending = False


@pytest.mark.api
def test_git_pull_does_not_flag_unrelated_changes(client, confirm_token):
    """POST /api/system/git-pull must not flag requirements_changed when requirements.txt wasn't touched."""
    import api_routes_system

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="oldsha\n", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Updating oldsha..newsha\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return MagicMock(returncode=0, stdout="main.py\n", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    api_routes_system._requirements_changed_pending = False
    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        resp = client.post("/api/system/git-pull", headers={"X-Confirm-Token": confirm_token})
    assert resp.status_code == 200
    assert _json(resp)["requirements_changed"] is False
    assert api_routes_system._requirements_changed_pending is False


@pytest.mark.api
def test_active_jobs_reports_requirements_changed_pending(client):
    """GET /api/system/active-jobs must surface the pending pip-install flag."""
    import api_routes_system

    api_routes_system._requirements_changed_pending = True
    resp = client.get("/api/system/active-jobs")
    assert resp.status_code == 200
    assert _json(resp)["requirements_changed_pending"] is True
    api_routes_system._requirements_changed_pending = False


def test_execute_restart_installs_pip_dependencies_when_pending():
    """execute_restart() must run pip install before signalling shutdown when requirements.txt changed."""
    import asyncio
    import api_routes_system

    api_routes_system._requirements_changed_pending = True
    fake_pip_result = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch("api_routes_system.subprocess.run", return_value=fake_pip_result) as mock_run,
        patch("api_routes_system.notify") as mock_notify,
        patch("api_routes_system.asyncio.sleep", new=AsyncMock()),
        patch("api_routes_system.os.kill"),
    ):
        asyncio.run(api_routes_system.execute_restart())

    assert mock_run.call_args[0][0][:3] == [sys.executable, "-m", "pip"]
    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][0] == "system_update_status"
    assert api_routes_system._requirements_changed_pending is False


def test_execute_restart_skips_pip_install_when_not_pending():
    """execute_restart() must not touch pip when no requirements.txt change is pending."""
    import asyncio
    import api_routes_system

    api_routes_system._requirements_changed_pending = False
    with (
        patch("api_routes_system.subprocess.run") as mock_run,
        patch("api_routes_system.notify") as mock_notify,
        patch("api_routes_system.asyncio.sleep", new=AsyncMock()),
        patch("api_routes_system.os.kill"),
    ):
        asyncio.run(api_routes_system.execute_restart())

    mock_run.assert_not_called()
    mock_notify.assert_not_called()


def test_bg_execute_ml_inference_runs_quantile_scoring_too():
    """The manual "Run Inference Now" trigger must match the scheduled ml_inference_job's
    behavior (update_daily_ml_predictions + score_quantile_predictions), not just the first
    half — the missing second call previously left price_q10/price_q90 stuck stale until the
    next scheduled run, even after a manual trigger reported success (found 2026-07-10)."""
    import api_routes_triggers

    with (
        patch("api_routes_triggers.get_universe_tickers", return_value=["ZZINFER"]),
        patch("api_routes_triggers.update_daily_ml_predictions") as mock_predict,
        patch("api_routes_triggers.score_quantile_predictions") as mock_quantile,
    ):
        api_routes_triggers.bg_execute_ml_inference()

    mock_predict.assert_called_once_with(["ZZINFER"])
    mock_quantile.assert_called_once_with(["ZZINFER"])


def test_bg_execute_ml_training_runs_quantile_training_too():
    """The manual "Run Training Now" trigger must match the scheduled ml_training_job's
    behavior (train_global_ml_model + train_quantile_models) — the same missing-second-call
    pattern as bg_execute_ml_inference, found in the same 2026-07-10 audit pass."""
    import api_routes_triggers

    with (
        patch("api_routes_triggers.train_global_ml_model") as mock_train,
        patch("api_routes_triggers.train_quantile_models") as mock_train_quantile,
    ):
        api_routes_triggers.bg_execute_ml_training()

    mock_train.assert_called_once()
    mock_train_quantile.assert_called_once()


@pytest.mark.api
def test_post_learn_session_returns_cards(client):
    resp = client.post("/api/learn/session")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "success"
    assert isinstance(data.get("cards"), list)
    assert len(data["cards"]) == 10


@pytest.mark.api
def test_post_learn_session_with_section_id_scopes_to_that_section(client):
    resp = client.post("/api/learn/session?section_id=candlesticks&size=30")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "success"

    import learn_cards_seed
    expected_total = sum(1 for c in learn_cards_seed.CARDS if c["section_id"] == "candlesticks")
    assert len(data["cards"]) == expected_total


@pytest.mark.api
def test_post_learn_answer_creates_state_and_advances_box(client):
    resp = client.post("/api/learn/answer", json={"term_key": "market-capitalisation", "grade": "good"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "success"
    assert data["box"] == 1
    assert data["term_key"] == "market-capitalisation"

    import database as _db
    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT box FROM learn_term_state WHERE term_key = 'market-capitalisation'"
        ).fetchone()
        assert row["box"] == 1
    finally:
        conn.execute("DELETE FROM learn_term_state WHERE term_key = 'market-capitalisation'")
        conn.commit()
        conn.close()


@pytest.mark.api
def test_post_learn_answer_invalid_grade_returns_400(client):
    resp = client.post("/api/learn/answer", json={"term_key": "market-capitalisation", "grade": "bogus"})
    assert resp.status_code == 400


@pytest.mark.api
def test_post_learn_answer_unknown_term_key_returns_400(client):
    resp = client.post("/api/learn/answer", json={"term_key": "not-a-real-term", "grade": "good"})
    assert resp.status_code == 400
