"""
tests/test_02_api_get_endpoints.py  ── API GET ENDPOINTS

Verifies that every GET endpoint in the application:
  1. Returns HTTP 200 (not a crash / 500 Internal Server Error)
  2. Returns valid JSON (not garbled output)
  3. Contains the expected top-level keys in its response

Tests use the session-scoped TestClient from conftest.py which runs against
the temp database.  No live network calls are made.

All "trigger" POST endpoints are tested separately in test_03_*.
"""

import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _json(resp) -> dict:
    """Parse response body as JSON, raise a clear error if it fails."""
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response body is not valid JSON.\n"
            f"Status: {resp.status_code}\n"
            f"Body (first 500 chars): {resp.text[:500]}"
        ) from exc


# ── Notifications ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_notifications_latest_returns_200(client):
    """GET /api/notifications/latest must return 200 with a notifications list."""
    resp = client.get("/api/notifications/latest")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "notifications" in data, f"Missing 'notifications' key in response: {data}"
    assert isinstance(data["notifications"], list), "'notifications' must be a list"


@pytest.mark.api
def test_get_notifications_with_last_id_filter(client):
    """GET /api/notifications/latest?last_id=9999 must return an empty list (no notifications above that ID)."""
    resp = client.get("/api/notifications/latest?last_id=9999999")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["notifications"] == [], "Expected empty list for very high last_id filter"


# ── Screener & Freshness ──────────────────────────────────────────────────────

@pytest.mark.api
def test_get_screener_data_returns_200(client):
    """GET /api/screener-data must return 200 with a data list (may be empty)."""
    resp = client.get("/api/screener-data")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "data" in data, f"Missing 'data' key in response: {data}"
    assert isinstance(data["data"], list), "'data' must be a list"


@pytest.mark.api
def test_get_freshness_returns_200(client):
    """GET /api/freshness must return 200 with model and price freshness fields."""
    resp = client.get("/api/freshness")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    expected_keys = {"model_state", "prices_state"}
    missing = expected_keys - set(data.keys())
    assert not missing, f"Response missing expected freshness keys: {missing}"


# ── System Metrics ────────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_system_metrics_returns_200(client):
    """GET /api/system/metrics must return 200 with system diagnostic data."""
    resp = client.get("/api/system/metrics")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = _json(resp)
    assert isinstance(body, dict), "System metrics response must be a JSON object"
    assert "scheduler_last_runs" in body, "Missing 'scheduler_last_runs' key in system metrics"
    assert isinstance(body["scheduler_last_runs"], dict), "'scheduler_last_runs' must be a dict"


# ── Network / Settings ────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_network_status_returns_200(client):
    """GET /api/settings/network-status must return 200 with route and indicator fields."""
    resp = client.get("/api/settings/network-status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "route" in data, f"Missing 'route' in network status: {data}"
    assert "indicator" in data, f"Missing 'indicator' in network status: {data}"


# ── Market Pulse ──────────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_market_pulse_returns_200(client):
    """GET /api/market-pulse must return 200 (cache may be empty, but no crash)."""
    resp = client.get("/api/market-pulse")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    _json(resp)  # just verify valid JSON


# ── Universe ──────────────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_universe_profiler_status_returns_200(client):
    """GET /api/universe/profiler-status must return 200 with queue breakdown."""
    resp = client.get("/api/universe/profiler-status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    _json(resp)


@pytest.mark.api
def test_get_universe_imports_list_returns_200(client):
    """GET /api/universe/imports/list must return 200 with a files list."""
    resp = client.get("/api/universe/imports/list")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "files" in data, f"Missing 'files' key in response: {data}"
    assert isinstance(data["files"], list), "'files' must be a list"


# ── Reports (7 report types) ──────────────────────────────────────────────────

REPORT_ENDPOINTS = [
    ("/api/reports/quality-compounders", "Quality Compounders"),
    ("/api/reports/quality-on-sale",     "Quality On Sale"),
    ("/api/reports/garp-tenbaggers",     "GARP 10-Baggers"),
    ("/api/reports/sectors",             "Sector Trends"),
    ("/api/reports/mean-reversion",      "Mean Reversion Setups"),
    ("/api/reports/leaders",             "Leaders & Laggards"),
    ("/api/reports/dividends",           "Dividend Harvest"),
]


@pytest.mark.api
@pytest.mark.parametrize("endpoint,label", REPORT_ENDPOINTS, ids=[r[1] for r in REPORT_ENDPOINTS])
def test_report_endpoint_returns_200(client, endpoint, label):
    """Every report endpoint must return 200 with a 'data' list (may be empty)."""
    resp = client.get(endpoint)
    assert resp.status_code == 200, f"{label}: Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "data" in data, f"{label}: Missing 'data' key in response: {data}"
    assert isinstance(data["data"], list), f"{label}: 'data' must be a list"


# ── AI Prompt (missing ticker → 404, not 500) ─────────────────────────────────

@pytest.mark.api
def test_get_ai_prompt_unknown_ticker_returns_404(client):
    """GET /api/ai-prompt/UNKNOWN must return 404 for a ticker not in the database (not a 500 crash)."""
    resp = client.get("/api/ai-prompt/UNKNOWN")
    assert resp.status_code == 404, (
        f"Expected 404 for unknown ticker, got {resp.status_code}. "
        "A 500 here means the AI prompt engine crashed."
    )


# ── Response speed sanity check ───────────────────────────────────────────────

@pytest.mark.api
def test_no_endpoint_returns_500(client):
    """
    All GET endpoints must return a non-500 status.
    A 500 means the code threw an unhandled exception — a regression.
    """
    get_endpoints = [
        "/api/notifications/latest",
        "/api/screener-data",
        "/api/freshness",
        "/api/system/metrics",
        "/api/settings/network-status",
        "/api/market-pulse",
        "/api/universe/profiler-status",
        "/api/universe/imports/list",
        "/api/reports/quality-compounders",
        "/api/reports/quality-on-sale",
        "/api/reports/garp-tenbaggers",
        "/api/reports/sectors",
        "/api/reports/mean-reversion",
        "/api/reports/leaders",
        "/api/reports/dividends",
        "/api/intraday-monitor/list",
        "/api/intraday-monitor/analysis/AAPL",
        "/api/smgb-prediction",
    ]
    failures = []
    for url in get_endpoints:
        r = client.get(url)
        if r.status_code >= 500:
            failures.append(f"  {url} → HTTP {r.status_code}")

    assert not failures, (
        "The following GET endpoints returned server errors (500+):\n"
        + "\n".join(failures)
        + "\nThis means code in those routes crashed.  Check logs above for the traceback."
    )


# ── Intraday Dip Radar ────────────────────────────────────────────────────────

@pytest.mark.api
def test_intraday_monitor_list_returns_200(client):
    """GET /api/intraday-monitor/list must return 200 with a monitors list."""
    resp = client.get("/api/intraday-monitor/list")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "monitors" in data, f"Missing 'monitors' key in response: {data}"
    assert isinstance(data["monitors"], list), "'monitors' must be a list"


@pytest.mark.api
def test_intraday_monitor_list_is_empty_on_fresh_db(client):
    """On a fresh test database, no monitors exist so the list must be empty."""
    resp = client.get("/api/intraday-monitor/list")
    data = _json(resp)
    assert data["monitors"] == [], (
        "Expected empty list on fresh DB — found unexpected monitors: "
        + str(data["monitors"])
    )


@pytest.mark.api
def test_intraday_monitor_analysis_unknown_ticker_returns_null(client):
    """GET /api/intraday-monitor/analysis/ZZNONE must return 200 with null body (no crash)."""
    resp = client.get("/api/intraday-monitor/analysis/ZZNONE")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json() is None, (
        "Expected null for a ticker with no scan results, got: " + resp.text[:200]
    )


@pytest.mark.api
def test_intraday_monitor_analysis_returns_data_after_result_inserted(client):
    """After inserting a result row, GET /api/intraday-monitor/analysis/{ticker} must return it."""
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import database as _db

    conn = _db.get_connection()
    reasons = ["Extreme Oversold (RSI: 21.5)", "Volume Capitulation detected"]
    try:
        conn.execute(
            """INSERT OR REPLACE INTO intraday_monitor_results
               (ticker, scan_ts, current_price, reversal_score, is_bottoming,
                reasons_json, rsi, vwap, vwap_deviation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("TESTRADAR", "2025-01-15 10:32", 110.5, 75, 1,
             json.dumps(reasons), 21.5, 113.0, -2.5),
        )
        conn.commit()

        resp = client.get("/api/intraday-monitor/analysis/TESTRADAR")
        assert resp.status_code == 200
        data = _json(resp)
        assert data is not None, "Expected a result dict, got null"
        assert data["ticker"] == "TESTRADAR"
        assert data["reversal_score"] == 75
        assert data["is_bottoming"] == 1
        assert isinstance(data["reasons"], list), "'reasons' must be a decoded list"
        assert len(data["reasons"]) == 2
    finally:
        conn.execute("DELETE FROM intraday_monitor_results WHERE ticker = 'TESTRADAR'")
        conn.commit()
        conn.close()


# ── News Feed API ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_news_feed_returns_200(client):
    """GET /api/news-feed must return 200."""
    resp = client.get("/api/news-feed")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"


@pytest.mark.api
def test_news_feed_response_shape(client):
    """GET /api/news-feed must return a JSON object with 'articles' (list) and 'total' (int)."""
    resp = client.get("/api/news-feed")
    data = _json(resp)
    assert "articles" in data, f"Missing 'articles' key: {data}"
    assert "total" in data, f"Missing 'total' key: {data}"
    assert isinstance(data["articles"], list), "'articles' must be a list"
    assert isinstance(data["total"], int), "'total' must be an int"


@pytest.mark.api
def test_news_feed_source_filter_accepted(client):
    """GET /api/news-feed?source=portfolio must return 200 without server error."""
    for src in ("portfolio", "watchlist", "both", "all"):
        resp = client.get(f"/api/news-feed?source={src}")
        assert resp.status_code == 200, (
            f"source={src} returned HTTP {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.api
def test_news_feed_pagination_params_accepted(client):
    """GET /api/news-feed?limit=10&offset=0 must return 200 without server error."""
    resp = client.get("/api/news-feed?limit=10&offset=0")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "articles" in data


@pytest.mark.api
def test_news_feed_returns_inserted_article(client):
    """After inserting an article directly, GET /api/news-feed must include it."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO news_articles
               (article_id, ticker, source_list, headline, published_at, fetched_at,
                sentiment_score, sentiment_label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("api-test-article-001", "MSFT", "portfolio",
             "API test headline for news feed", 1700000100, 1700001100, 0.55, "positive"),
        )
        conn.commit()

        resp = client.get("/api/news-feed?source=all&limit=200")
        assert resp.status_code == 200
        data = _json(resp)
        ids = [a.get("article_id") for a in data["articles"]]
        assert "api-test-article-001" in ids, (
            "Inserted article not found in /api/news-feed response. "
            f"article_ids returned: {ids[:10]}"
        )
    finally:
        conn.execute("DELETE FROM news_articles WHERE article_id = 'api-test-article-001'")
        conn.commit()
        conn.close()


# ── SMGB.L Predictor ─────────────────────────────────────────────────────────

@pytest.mark.api
def test_smgb_prediction_returns_200_with_status_key(client):
    """GET /api/smgb-prediction must return 200 with a 'status' key.
    yfinance will fail in the test environment, so 'status' may be 'error' —
    but the endpoint must never return a 500 server crash.
    """
    resp = client.get("/api/smgb-prediction")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "status" in data, f"Missing 'status' key in smgb-prediction response: {data}"
