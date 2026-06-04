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


# ── Watchlist (mocked Ghostfolio) ─────────────────────────────────────────────

@pytest.mark.api
def test_post_watchlist_add_with_mock(client):
    """POST /api/watchlist/add must not crash (Ghostfolio is mocked)."""
    with patch("api_routes.GhostfolioSyncEngine") as MockEngine:
        instance = MockEngine.return_value
        instance.add_to_watchlist.return_value = True
        instance.sync_watchlist.return_value = None
        resp = client.post("/api/watchlist/add", json={"ticker": "AAPL"})
    assert resp.status_code in (200, 400, 422), (
        f"Unexpected status code {resp.status_code} for watchlist add"
    )


@pytest.mark.api
def test_post_watchlist_remove_with_mock(client):
    """POST /api/watchlist/remove must not crash (Ghostfolio is mocked)."""
    with patch("api_routes.GhostfolioSyncEngine") as MockEngine:
        instance = MockEngine.return_value
        instance.remove_from_watchlist.return_value = True
        instance.sync_watchlist.return_value = None
        resp = client.post("/api/watchlist/remove", json={"ticker": "AAPL"})
    assert resp.status_code in (200, 400, 422), (
        f"Unexpected status code {resp.status_code} for watchlist remove"
    )


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
