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
from unittest.mock import patch

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
def test_get_workflow_monitor_status_returns_200(client):
    """GET /api/workflow-monitor/status must return nodes, edges and conflicts lists."""
    resp = client.get("/api/workflow-monitor/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    for key in ("nodes", "edges", "conflicts"):
        assert isinstance(data[key], list), f"'{key}' must be a list"
    assert data["nodes"], "workflow graph must expose at least one job node"
    sample = data["nodes"][0]
    for key in ("id", "label", "category", "status", "produces", "consumes"):
        assert key in sample, f"node missing '{key}'"


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


@pytest.mark.api
def test_get_system_metrics_includes_auction_job_keys(client):
    """The Master APScheduler Matrix keys its auction rows by job id, not config key — both must be present."""
    resp = client.get("/api/system/metrics")
    body = _json(resp)
    assert "macro_auction_job_am" in body["scheduler_last_runs"]
    assert "macro_auction_job_pm" in body["scheduler_last_runs"]


# ── Network / Settings ────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_network_status_returns_200(client):
    """GET /api/settings/network-status must return 200 with route and indicator fields."""
    resp = client.get("/api/settings/network-status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "route" in data, f"Missing 'route' in network status: {data}"
    assert "indicator" in data, f"Missing 'indicator' in network status: {data}"
    assert "routing_mode" in data, f"Missing 'routing_mode' in network status: {data}"


@pytest.mark.api
def test_get_yahoo_api_stats_returns_200(client):
    """GET /api/system/yahoo-api-stats must return 200 with rows list."""
    resp = client.get("/api/system/yahoo-api-stats")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "rows" in data, f"Missing 'rows' in yahoo-api-stats response: {data}"
    assert isinstance(data["rows"], list)


@pytest.mark.api
def test_get_yahoo_api_stats_detail_returns_200(client):
    """GET /api/system/yahoo-api-stats/{date} must return 200 with buckets/job_labels/series."""
    resp = client.get("/api/system/yahoo-api-stats/2026-07-01")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert data["date"] == "2026-07-01"
    assert len(data["buckets"]) == 96
    assert isinstance(data["job_labels"], list)
    assert isinstance(data["series"], dict)
    assert len(data["errors_by_bucket"]) == 96


@pytest.mark.api
def test_get_yahoo_api_stats_detail_rejects_bad_date(client):
    resp = client.get("/api/system/yahoo-api-stats/not-a-date")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    assert _json(resp)["status"] == "error"


@pytest.mark.api
def test_get_yahoo_api_stats_detail_reflects_call_log_rows(client):
    import database as db
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO yahoo_api_call_log (call_time, date, interface, status, job_id, action_context) "
            "VALUES ('2026-07-02 09:05:00', '2026-07-02', 'ipv4', 'success', 'quant_analysis_job', 'Ticker Info: AAPL')"
        )
        conn.commit()
    finally:
        conn.close()
    resp = client.get("/api/system/yahoo-api-stats/2026-07-02")
    data = _json(resp)
    assert data["job_labels"], f"Expected at least one job label: {data}"
    total_calls = sum(sum(v) for v in data["series"].values())
    assert total_calls == 1


# ── Market Status (Home Assistant) ────────────────────────────────────────────

@pytest.mark.api
def test_get_market_status_returns_200_with_expected_keys(client):
    """GET /api/system/market-status must return 200 with the four status fields."""
    resp = client.get("/api/system/market-status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    for key in ("us_market_open", "uk_market_open", "yahoo_ok", "system_ok"):
        assert key in data, f"Missing '{key}' in market-status response: {data}"


@pytest.mark.api
def test_get_market_status_reflects_trading_session_true(client):
    with patch("api_routes_system.is_exchange_open", side_effect=lambda exchange: exchange == "NYSE"):
        resp = client.get("/api/system/market-status")
    data = _json(resp)
    assert data["us_market_open"] is True
    assert data["uk_market_open"] is False


@pytest.mark.api
def test_get_market_status_reflects_trading_session_false(client):
    with patch("api_routes_system.is_exchange_open", return_value=False):
        resp = client.get("/api/system/market-status")
    data = _json(resp)
    assert data["us_market_open"] is False
    assert data["uk_market_open"] is False


@pytest.mark.api
def test_get_market_status_self_triggers_refresh_for_stale_proxy_tickers(client):
    """Regression test: without this, a caller that only ever polls market-status (e.g. Home
    Assistant, with no browser dashboard open to drive /api/market-pulse's own polling) would
    never populate market_state at all, and us_market_open/uk_market_open would silently fall
    back to the naive weekday/hours heuristic forever."""
    with patch("api_routes_system.proxy_tickers_needing_refresh", return_value=["^GSPC", "^FTSE"]), \
         patch("api_routes_system.markets_engine.registry_lookup_tickers", return_value=[]), \
         patch("api_routes_system.registry_tickers_needing_refresh", return_value=[]), \
         patch("api_routes_system.fetch_and_save_pulse") as mock_fetch:
        resp = client.get("/api/system/market-status")
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert set(mock_fetch.call_args[0][0]) == {"^GSPC", "^FTSE"}


@pytest.mark.api
def test_get_market_status_does_not_trigger_refresh_when_proxies_fresh(client):
    with patch("api_routes_system.proxy_tickers_needing_refresh", return_value=[]), \
         patch("api_routes_system.markets_engine.registry_lookup_tickers", return_value=[]), \
         patch("api_routes_system.registry_tickers_needing_refresh", return_value=[]), \
         patch("api_routes_system.fetch_and_save_pulse") as mock_fetch:
        resp = client.get("/api/system/market-status")
    assert resp.status_code == 200
    mock_fetch.assert_not_called()


@pytest.mark.api
def test_get_market_status_self_triggers_refresh_for_stale_registry_tickers(client):
    """Home Assistant's coordinator poll of this endpoint is now the only reliable background
    warm-up for the Markets registry on installs that never press "Refresh Data" and never have
    /markets open in a browser tab — added 2026-07-10 after tiles were observed showing stale
    ("crossed over") data on cold visits."""
    with patch("api_routes_system.proxy_tickers_needing_refresh", return_value=[]), \
         patch("api_routes_system.markets_engine.registry_lookup_tickers", return_value=["^KS200", "GC=F"]), \
         patch("api_routes_system.registry_tickers_needing_refresh", return_value=["^KS200", "GC=F"]), \
         patch("api_routes_system.fetch_and_save_pulse") as mock_fetch:
        resp = client.get("/api/system/market-status")
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert set(mock_fetch.call_args[0][0]) == {"^KS200", "GC=F"}


@pytest.mark.api
def test_get_market_status_yahoo_ok_true_when_recent_success(client):
    with patch(
        "database.get_yahoo_api_stats",
        return_value=[{"date": "2026-07-01", "total_calls": 10, "ipv4_calls": 10, "ipv6_calls": 0, "rate_limit_429": 0, "other_errors": 0}],
    ):
        resp = client.get("/api/system/market-status")
    assert _json(resp)["yahoo_ok"] is True


@pytest.mark.api
def test_get_market_status_yahoo_ok_false_when_no_recent_calls(client):
    with patch("database.get_yahoo_api_stats", return_value=[]):
        resp = client.get("/api/system/market-status")
    assert _json(resp)["yahoo_ok"] is False


@pytest.mark.api
def test_get_market_status_yahoo_ok_false_when_all_calls_errored(client):
    with patch(
        "database.get_yahoo_api_stats",
        return_value=[{"date": "2026-07-01", "total_calls": 5, "ipv4_calls": 5, "ipv6_calls": 0, "rate_limit_429": 5, "other_errors": 0}],
    ):
        resp = client.get("/api/system/market-status")
    assert _json(resp)["yahoo_ok"] is False


@pytest.mark.api
def test_get_market_status_system_ok_reflects_issue_count(client):
    with patch("system_check_engine.run_system_checks", return_value=[]):
        resp = client.get("/api/system/market-status")
    assert _json(resp)["system_ok"] is True

    with patch("system_check_engine.run_system_checks", return_value=[{"key": "x", "level": "warning", "message": "m"}]):
        resp = client.get("/api/system/market-status")
    assert _json(resp)["system_ok"] is False


# ── Market Pulse ──────────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_market_pulse_returns_200(client):
    """GET /api/market-pulse must return 200 (cache may be empty, but no crash)."""
    resp = client.get("/api/market-pulse")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    _json(resp)  # just verify valid JSON


@pytest.mark.api
def test_get_market_pulse_index_items_include_invert_color_and_asset_type(client):
    resp = client.get("/api/market-pulse")
    data = _json(resp)["data"]
    assert len(data) > 0
    for item in data:
        assert "invert_color" in item
        assert "asset_type" in item
        assert "is_pulse_mobile" in item
        assert "currency" in item


@pytest.mark.api
def test_get_market_pulse_dynamic_mode_returns_dynamic_selection(client):
    from unittest.mock import patch as _patch
    with _patch("database.load_config", return_value={"UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": True, "MARKET_PULSE_DESKTOP_COUNT": 3}}), \
         _patch("market_pulse.load_config", return_value={"UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": True, "MARKET_PULSE_DESKTOP_COUNT": 3}}), \
         _patch("api_routes_system.load_config", return_value={"UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": True, "MARKET_PULSE_DESKTOP_COUNT": 3}}):
        resp = client.get("/api/market-pulse")
    data = _json(resp)["data"]
    assert len(data) == 3


# ── Markets page ──────────────────────────────────────────────────────────────

@pytest.mark.api
def test_get_markets_dynamic_view_returns_200(client):
    resp = client.get("/api/markets")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert data["data"]["view"] == "dynamic"
    regions = {r["region"] for r in data["data"]["regions"]}
    assert regions == {"Europe", "US", "Asia", "Commodities_FX"}


@pytest.mark.api
def test_get_markets_self_triggers_refresh_for_stale_proxy_tickers(client):
    """Regression test: a region's open/closed badge must not stay stuck on a stale exchange
    proxy's market_state forever just because no tile ticker on this page happened to need a
    price refresh — without this, a region can report "open" indefinitely once no page traffic
    refreshes its proxy (see the analogous GET /api/system/market-status fix)."""
    with patch("api_routes_system.proxy_tickers_needing_refresh", return_value=["^N225", "^HSI"]), \
         patch("api_routes_system.fetch_and_save_pulse") as mock_fetch:
        resp = client.get("/api/markets")
    assert resp.status_code == 200
    assert mock_fetch.called
    fetched = set(mock_fetch.call_args[0][0])
    assert {"^N225", "^HSI"}.issubset(fetched)


@pytest.mark.api
def test_get_markets_static_view_returns_fixed_order(client):
    resp = client.get("/api/markets?view=static")
    assert resp.status_code == 200
    data = _json(resp)["data"]
    assert data["view"] == "static"
    assert [r["region"] for r in data["regions"]] == ["Europe", "US", "Asia", "Commodities_FX"]


@pytest.mark.api
def test_get_markets_tile_response_has_no_leaked_needs_refresh_field(client):
    resp = client.get("/api/markets")
    data = _json(resp)["data"]
    for region in data["regions"]:
        for tile in region["tiles"]:
            assert "needs_refresh" not in tile


@pytest.mark.api
def test_get_market_status_all_returns_exchanges_and_regions(client):
    resp = client.get("/api/system/market-status/all")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert "NYSE" in data["exchanges"]
    assert "LSE" in data["exchanges"]
    assert set(data["regions"].keys()) == {"US", "Europe", "Asia"}


@pytest.mark.api
def test_get_market_status_all_does_not_change_existing_market_status_contract(client):
    """The original two-exchange /system/market-status endpoint must stay untouched — the
    Home Assistant integration binds to exactly these four fields."""
    resp = client.get("/api/system/market-status")
    data = _json(resp)
    for key in ("status", "us_market_open", "uk_market_open", "yahoo_ok", "system_ok"):
        assert key in data
    assert "exchanges" not in data


@pytest.mark.api
def test_get_markets_registry_returns_seeded_tickers(client):
    resp = client.get("/api/markets/registry")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    tickers = {row["ticker"] for row in data["registry"]}
    assert "^GSPC" in tickers
    assert "GC=F" in tickers


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
        "/api/trap-monitor/results",
        "/api/macro-regime-allocation",
        "/api/pairs-spread/results",
        "/api/predicted-movers/leaderboard",
        "/api/predicted-movers/accuracy",
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



# ── Market Trap & Recovery Monitor ────────────────────────────────────────────

@pytest.mark.api
def test_trap_monitor_results_returns_200(client):
    """GET /api/trap-monitor/results must return 200 with a 'results' list."""
    resp = client.get("/api/trap-monitor/results")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success status: {data}"
    assert "results" in data, f"Missing 'results' key: {data}"
    assert isinstance(data["results"], list), "'results' must be a list"


@pytest.mark.api
def test_trap_monitor_results_empty_on_fresh_db(client):
    """On a fresh test database, trap_monitor_results must be an empty list."""
    resp = client.get("/api/trap-monitor/results")
    data = _json(resp)
    assert data["results"] == [], (
        "Expected empty list on fresh DB, got: " + str(data["results"])
    )


# ── Pairs Spread Monitor ──────────────────────────────────────────────────────

@pytest.mark.api
def test_pairs_spread_results_returns_200(client):
    """GET /api/pairs-spread/results must return 200 with a 'results' list."""
    resp = client.get("/api/pairs-spread/results")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success status: {data}"
    assert "results" in data, f"Missing 'results' key: {data}"
    assert isinstance(data["results"], list), "'results' must be a list"


@pytest.mark.api
def test_pairs_spread_results_accepts_universe_scope(client):
    """GET /api/pairs-spread/results?scope=universe must return 200, not the default scope's error."""
    resp = client.get("/api/pairs-spread/results?scope=universe")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success"


@pytest.mark.api
def test_pairs_spread_results_rejects_invalid_scope(client):
    """GET /api/pairs-spread/results?scope=bogus must 422 (FastAPI Query pattern validation), not 500."""
    resp = client.get("/api/pairs-spread/results?scope=bogus")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.api
def test_pairs_spread_chart_returns_404_for_unknown_pair(client):
    """GET /api/pairs-spread/chart/{a}/{b} must 404, not 500, when there's no overlapping history."""
    resp = client.get("/api/pairs-spread/chart/NOSUCHTICKERA/NOSUCHTICKERB")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "error"


# ── Predicted Movers ──────────────────────────────────────────────────────────

@pytest.mark.api
def test_predicted_movers_leaderboard_returns_200(client):
    """GET /api/predicted-movers/leaderboard must return 200 with a 'results' list."""
    resp = client.get("/api/predicted-movers/leaderboard")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success status: {data}"
    assert "results" in data, f"Missing 'results' key: {data}"
    assert isinstance(data["results"], list), "'results' must be a list"


@pytest.mark.api
def test_predicted_movers_leaderboard_accepts_universe_scope_and_all_sorts(client):
    """GET /api/predicted-movers/leaderboard must accept scope=universe and every sort mode."""
    for scope in ("portfolio_watchlist", "universe"):
        for sort in ("gainers", "losers", "movers"):
            resp = client.get(f"/api/predicted-movers/leaderboard?scope={scope}&sort={sort}")
            assert resp.status_code == 200, f"scope={scope} sort={sort} → {resp.status_code}"
            assert _json(resp).get("status") == "success"


@pytest.mark.api
def test_predicted_movers_leaderboard_rejects_invalid_scope(client):
    """GET /api/predicted-movers/leaderboard?scope=bogus must 422, not 500."""
    resp = client.get("/api/predicted-movers/leaderboard?scope=bogus")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.api
def test_predicted_movers_leaderboard_rejects_invalid_sort(client):
    """GET /api/predicted-movers/leaderboard?sort=bogus must 422, not 500."""
    resp = client.get("/api/predicted-movers/leaderboard?sort=bogus")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.api
def test_predicted_movers_accuracy_returns_200(client):
    """GET /api/predicted-movers/accuracy must return 200 with by_ticker/overall shape."""
    resp = client.get("/api/predicted-movers/accuracy")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success status: {data}"
    assert "by_ticker" in data
    assert "overall" in data
    assert isinstance(data["by_ticker"], list)


# ── Log Viewer API ────────────────────────────────────────────────────────────

@pytest.mark.api
def test_logs_tail_returns_200(client):
    """GET /api/logs/tail must return 200 regardless of whether logging is enabled."""
    resp = client.get("/api/logs/tail")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert "status" in data, f"Missing 'status' key: {data}"


@pytest.mark.api
def test_logs_tail_returns_valid_shape(client):
    """GET /api/logs/tail must return status=success with lines list, or status=error with message."""
    resp = client.get("/api/logs/tail")
    data = _json(resp)
    assert data["status"] in ("success", "error"), f"Unexpected status value: {data}"
    if data["status"] == "success":
        assert "lines" in data, f"Missing 'lines' key in success response: {data}"
        assert isinstance(data["lines"], list), "'lines' must be a list"
    else:
        assert "message" in data, f"Missing 'message' key in error response: {data}"


@pytest.mark.api
def test_logs_tail_lines_param_accepted(client):
    """GET /api/logs/tail?lines=100 must not crash (invalid param boundary check)."""
    resp = client.get("/api/logs/tail?lines=100")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


@pytest.mark.api
def test_logs_tail_lines_param_below_min_rejected(client):
    """GET /api/logs/tail?lines=0 must return 422 (below minimum of 1)."""
    resp = client.get("/api/logs/tail?lines=0")
    assert resp.status_code == 422, f"Expected 422 for lines=0, got {resp.status_code}"


@pytest.mark.api
def test_logs_tail_full_param_accepted(client):
    """GET /api/logs/tail?full=true must not crash and returns the same success/error shape."""
    resp = client.get("/api/logs/tail?full=true")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] in ("success", "error"), f"Unexpected status value: {data}"
    if data["status"] == "success":
        assert isinstance(data["lines"], list), "'lines' must be a list"


# ── FX Drag API ───────────────────────────────────────────────────────────────

@pytest.mark.api
def test_fx_drag_returns_200(client):
    """GET /api/fx-drag must return 200."""
    resp = client.get("/api/fx-drag")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"


@pytest.mark.api
def test_fx_drag_response_shape(client):
    """GET /api/fx-drag must return JSON with 'status', 'period', and 'data' (list)."""
    resp = client.get("/api/fx-drag")
    data = _json(resp)
    assert data.get("status") == "success", f"Unexpected status: {data}"
    assert "period" in data, f"Missing 'period' key: {data}"
    assert "data" in data, f"Missing 'data' key: {data}"
    assert isinstance(data["data"], list), "'data' must be a list"


@pytest.mark.api
def test_fx_drag_period_variants_accepted(client):
    """GET /api/fx-drag?period=ytd|1y|2y|lifetime must all return 200."""
    for period in ("ytd", "1y", "2y", "lifetime"):
        resp = client.get(f"/api/fx-drag?period={period}")
        assert resp.status_code == 200, (
            f"period={period} returned HTTP {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.api
def test_fx_drag_invalid_period_rejected(client):
    """GET /api/fx-drag?period=invalid must return 422."""
    resp = client.get("/api/fx-drag?period=invalid")
    assert resp.status_code == 422, f"Expected 422 for invalid period, got {resp.status_code}"


@pytest.mark.api
def test_get_forensic_scores_returns_200(client):
    """GET /api/forensic-scores must return 200 with a results list."""
    resp = client.get("/api/forensic-scores")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert "results" in data
    assert isinstance(data["results"], list)


# ── AI Sector Contagion ───────────────────────────────────────────────────────

@pytest.mark.api
def test_ai_contagion_status_returns_200(client):
    """GET /api/ai-contagion/status must return 200 with a snapshots list."""
    resp = client.get("/api/ai-contagion/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert "snapshots" in data
    assert isinstance(data["snapshots"], list)


@pytest.mark.api
def test_ai_contagion_status_empty_on_fresh_db(client):
    """On a fresh test database, ai-contagion snapshots must be an empty list."""
    resp = client.get("/api/ai-contagion/status")
    data = _json(resp)
    assert data["snapshots"] == []


# ── Market Regime ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_market_regime_current_returns_200(client):
    """GET /api/market-regime/current must return 200 with current/last_change keys."""
    resp = client.get("/api/market-regime/current")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert "current" in data
    assert "last_change" in data


@pytest.mark.api
def test_market_regime_current_null_on_fresh_db(client):
    """On a fresh test database with no HMM data, current must be None."""
    resp = client.get("/api/market-regime/current")
    data = _json(resp)
    assert data["current"] is None


@pytest.mark.api
def test_market_regime_current_includes_us_uk_regime_labels(client):
    """Once a row has both HMM and turbulence-classifier columns, current must expose
    us_regime_label/uk_regime_label alongside the HMM label — a distinct Normal/Volatile/Crash
    taxonomy from the HMM's own Bull/Chop/Crash label on the same row.

    Seeds every turbulence-classifier column (not just the two labels) because this INSERT OR
    REPLACE can make this row "the latest" market_regimes row for the whole shared-session test
    DB — regime_engine.get_latest_regime() (used by /market-sentiment) does a bare
    `SELECT * ... ORDER BY date DESC LIMIT 1` with no NULL guard on us_turbulence/uk_turbulence,
    so a partially-seeded row here previously broke that page's Jinja "%.2f" formatting."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO market_regimes
               (date, price_hmm_state, price_hmm_label, price_hmm_prob,
                us_regime_label, us_turbulence, uk_regime_label, uk_turbulence,
                vix_close, spy_volatility, ftse_volatility)
               VALUES ('2026-06-11', 0, 'Bull', 0.87, 'Normal', 14.0, 'Volatile', 24.0,
                       14.5, 14.0, 24.0)"""
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/market-regime/current")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["current"]["label"] == "Bull"
    assert data["current"]["us_regime_label"] == "Normal"
    assert data["current"]["uk_regime_label"] == "Volatile"


@pytest.mark.api
def test_market_regime_full_returns_200(client):
    """GET /api/market-regime must return 200 with current/history/transition_matrix/regime_stats keys."""
    resp = client.get("/api/market-regime")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert "current" in data
    assert "history" in data


# ── Market Stress ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_market_stress_returns_200(client):
    """GET /api/market-stress must return 200 with current and history keys."""
    resp = client.get("/api/market-stress")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert "current" in data
    assert "history" in data


@pytest.mark.api
def test_market_stress_null_on_fresh_db(client):
    """On a fresh test database with no stress data, current must be None."""
    resp = client.get("/api/market-stress")
    data = _json(resp)
    assert data["current"] is None
    assert data["history"] == []


# ── Macro Conditions ──────────────────────────────────────────────────────────

@pytest.mark.api
def test_macro_conditions_returns_200(client):
    """GET /api/macro-conditions must return 200 with the expected top-level keys."""
    resp = client.get("/api/macro-conditions")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    for key in ("us_threat_level", "uk_threat_level", "treasury_auction", "fear_greed"):
        assert key in data


@pytest.mark.api
def test_macro_conditions_degrades_gracefully_with_no_macro_regimes_row(client):
    """With no macro_regimes row and no recent auctions, threat levels/as_of must be None and
    treasury_auction.healthy must be None (distinct from a false 'Healthy')."""
    resp = client.get("/api/macro-conditions")
    data = _json(resp)
    assert data["as_of"] is None
    assert data["us_threat_level"] is None
    assert data["uk_threat_level"] is None
    assert data["treasury_auction"]["healthy"] is None
    assert data["treasury_auction"]["recent"] == []


@pytest.mark.api
def test_macro_conditions_includes_seeded_threat_levels_and_auction_health(client):
    """Once macro_regimes and treasury_auction_results have rows, the endpoint must surface the
    raw GREEN/YELLOW/RED threat levels and correctly flag auction weakness."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO macro_regimes
               (date, us_threat_level, uk_threat_level, us_yield_velocity, uk_yield_velocity,
                tyx_close, tnx_close, uk_gilt_close, dxy_close, gbpusd_close)
               VALUES ('2026-07-01', 'YELLOW', 'GREEN', 18.5, 4.2, 4.55, 4.30, 4.10, 104.2, 1.27)"""
        )
        conn.execute(
            """INSERT OR REPLACE INTO treasury_auction_results
               (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
               VALUES ('TESTCUSIP1', '10Y', date('now'), 4.31, 2.45, 1.2, 18.0, 65.0, 17.0, 39000, 1)"""
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/macro-conditions")
    data = _json(resp)
    assert data["as_of"] == "2026-07-01"
    assert data["us_threat_level"] == "YELLOW"
    assert data["uk_threat_level"] == "GREEN"
    assert data["treasury_auction"]["healthy"] is False
    assert len(data["treasury_auction"]["recent"]) == 1
    assert data["treasury_auction"]["recent"][0]["maturity_label"] == "10Y"


# ── Stress Test ───────────────────────────────────────────────────────────────

@pytest.mark.api
def test_stress_test_scenarios_returns_200(client):
    """GET /api/stress-test/scenarios must return 200 with a non-empty scenarios dict."""
    resp = client.get("/api/stress-test/scenarios")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert "scenarios" in data
    assert isinstance(data["scenarios"], dict)
    assert len(data["scenarios"]) > 0, "SCENARIOS dict must be non-empty"


# ── AI Prompt — market-level endpoints ────────────────────────────────────────

@pytest.mark.api
def test_ai_prompt_market_regime(client):
    resp = client.get("/api/ai-prompt/market-regime")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert isinstance(data.get("prompt"), str) and len(data["prompt"]) > 0


@pytest.mark.api
def test_ai_prompt_sentiment_us(client):
    resp = client.get("/api/ai-prompt/market-sentiment/us")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert isinstance(data.get("prompt"), str) and len(data["prompt"]) > 0


@pytest.mark.api
def test_ai_prompt_sentiment_uk(client):
    resp = client.get("/api/ai-prompt/market-sentiment/uk")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "success"
    assert isinstance(data.get("prompt"), str) and len(data["prompt"]) > 0


# ── Monte Carlo Accounts ──────────────────────────────────────────────────────

@pytest.mark.api
def test_monte_carlo_accounts_returns_200(client):
    """GET /api/monte-carlo/accounts returns 200; when nothing is configured, status=error."""
    resp = client.get("/api/monte-carlo/accounts")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert "status" in data
    if data["status"] == "success":
        assert "accounts" in data and isinstance(data["accounts"], list)
        assert "total" in data and isinstance(data["total"], (int, float))
    else:
        assert "message" in data


@pytest.mark.api
def test_monte_carlo_accounts_uses_builtin_trading_accounts_when_ghostfolio_disabled(client):
    """With Ghostfolio disabled, a built-in Trading account with holdings must still populate
    the account tiles and total (previously required an active Ghostfolio account)."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
            ("MCTEST", 100.0, "GBP"),
        )
        conn.commit()
    finally:
        conn.close()

    account_id = _db.create_account("MC Builtin Test", "GBP")
    _db.add_transaction(account_id, "Buy", "2026-01-05", ticker="MCTEST", currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

    # list_scope_accounts_with_values() (accounts_engine.py) is the shared account-tile
    # builder behind this endpoint — patch its own load_config, not api_routes'.
    with patch("accounts_engine.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}):
        resp = client.get("/api/monte-carlo/accounts")

    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM stock_signals WHERE ticker = 'MCTEST'")
        conn.commit()
    finally:
        conn.close()

    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert any(a["id"] == f"acct:{account_id}" for a in data["accounts"])
    assert data["total"] >= 1000.0


@pytest.mark.api
def test_performance_analytics_accounts_returns_200(client):
    """GET /api/performance-analytics/accounts returns 200; when nothing is configured, status=error."""
    resp = client.get("/api/performance-analytics/accounts")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert "status" in data
    if data["status"] == "success":
        assert "accounts" in data and isinstance(data["accounts"], list)
        assert "total" in data and isinstance(data["total"], (int, float))
    else:
        assert "message" in data


@pytest.mark.api
def test_performance_analytics_accounts_uses_builtin_trading_accounts_when_ghostfolio_disabled(client):
    """GET /api/performance-analytics/accounts shares the same account-tile builder as Monte
    Carlo — must also populate from a built-in Trading account with Ghostfolio disabled."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
            ("PAETEST", 100.0, "GBP"),
        )
        conn.commit()
    finally:
        conn.close()

    account_id = _db.create_account("PAE Builtin Test", "GBP")
    _db.add_transaction(account_id, "Buy", "2026-01-05", ticker="PAETEST", currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

    with patch("accounts_engine.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}):
        resp = client.get("/api/performance-analytics/accounts")

    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM stock_signals WHERE ticker = 'PAETEST'")
        conn.commit()
    finally:
        conn.close()

    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert any(a["id"] == f"acct:{account_id}" for a in data["accounts"])
    assert data["total"] >= 1000.0


# ── Portfolio Tearsheet (Performance Analytics) ────────────────────────────────

@pytest.mark.api
def test_performance_analytics_report_returns_200(client):
    """GET /api/performance-analytics/report returns 200 with a status field even when the
    requested scope has no holdings (status=error) or too little cached history (metrics=None)."""
    resp = client.get("/api/performance-analytics/report?account_id=all")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert "status" in data
    if data["status"] == "success" and data.get("metrics") is not None:
        for group in ("risk_adjusted_ratios", "drawdown_analytics",
                      "distribution_tail_stats", "win_loss_stats"):
            assert group in data["metrics"]
        for chart in ("underwater", "cumulative_growth", "monthly_heatmap", "histogram"):
            assert chart in data["charts"]


# ── Portfolio Optimizer ─────────────────────────────────────────────────────────

@pytest.mark.api
def test_portfolio_optimizer_accounts_returns_200(client):
    """GET /api/portfolio-optimizer/accounts shares the same account-tile builder as Monte
    Carlo/Tearsheet — must also populate from a built-in Trading account with Ghostfolio disabled."""
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
            ("POATEST", 100.0, "GBP"),
        )
        conn.commit()
    finally:
        conn.close()

    account_id = _db.create_account("POA Builtin Test", "GBP")
    _db.add_transaction(account_id, "Buy", "2026-01-05", ticker="POATEST", currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

    with patch("accounts_engine.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}):
        resp = client.get("/api/portfolio-optimizer/accounts")

    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert any(a["id"] == f"acct:{account_id}" for a in data["accounts"])


@pytest.mark.api
def test_portfolio_optimizer_candidates_returns_held_and_watchlist(client):
    """GET /api/portfolio-optimizer/candidates marks held tickers held=True and Watchlist-only
    tickers held=False with current_weight 0.0."""
    import database as _db
    from db_accounts import get_watchlist_account, add_watchlist_item, remove_watchlist_ticker

    conn = _db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
            ("POCTEST", 100.0, "GBP"),
        )
        conn.commit()
    finally:
        conn.close()

    account_id = _db.create_account("POC Builtin Test", "GBP")
    _db.add_transaction(account_id, "Buy", "2026-01-05", ticker="POCTEST", currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "POCWATCH", company_name="POC Watch Co")
    try:
        with patch("xray_engine.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}):
            resp = client.get(f"/api/portfolio-optimizer/candidates?account_id=acct:{account_id}")

        assert resp.status_code == 200
        data = _json(resp)
        assert data["status"] == "success"
        by_symbol = {c["symbol"]: c for c in data["candidates"]}
        assert by_symbol["POCTEST"]["held"] is True
        assert by_symbol["POCWATCH"]["held"] is False
        assert by_symbol["POCWATCH"]["current_weight"] == 0.0
    finally:
        remove_watchlist_ticker(wl["id"], "POCWATCH")


# ── Backup & Recovery ──────────────────────────────────────────────────────────

@pytest.mark.api
def test_backup_status_returns_200(client):
    """GET /api/backup/status must return 200 with the expected aggregate shape."""
    fake_status = {
        "last_backup": {"started_at": "2026-06-28 03:30:00", "finished_at": "2026-06-28 03:30:05",
                         "status": "success", "components": "data,models,database",
                         "destination": "/tmp/backups", "size_bytes": 1048576, "error_message": None},
        "stored_count": 2,
        "stored_size_bytes": 2097152,
        "backups": [{"filename": "backup_20260628_033000.tar.gz", "size_bytes": 1048576, "mtime": "2026-06-28 03:30:05"}],
    }
    with patch("api_routes_triggers.get_backup_status", return_value=fake_status):
        resp = client.get("/api/backup/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert data.get("status") == "success"
    assert data["stored_count"] == 2
    assert data["last_backup"]["status"] == "success"
    assert len(data["backups"]) == 1


@pytest.mark.api
def test_get_learn_overview_returns_200(client):
    """GET /api/learn/overview must return 200 with levels, due_count, weak_terms."""
    resp = client.get("/api/learn/overview")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert data.get("status") == "success"
    assert isinstance(data.get("levels"), list)
    assert len(data["levels"]) == len(__import__("learn_cards_seed").LEVELS)
    assert "due_count" in data
    assert "weak_terms" in data
    assert "total_learned" in data
