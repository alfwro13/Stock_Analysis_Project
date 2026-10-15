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


# ── Index refresh ─────────────────────────────────────────────────────────────

@pytest.mark.api
@pytest.mark.parametrize("ticker", ["UK10YG", "TBILL-606"])
def test_post_index_refresh_rejects_non_yahoo_tickers_before_any_fetch(client, ticker):
    with patch("api_routes.fetch_and_save_pulse") as pulse, patch("api_routes.DataEngine") as engine:
        resp = client.post("/api/index/refresh", json={"ticker": ticker})
    assert resp.status_code == 422
    assert _json(resp)["status"] == "error"
    pulse.assert_not_called()
    engine.assert_not_called()


@pytest.mark.api
def test_post_index_refresh_still_refreshes_a_yahoo_index(client):
    with patch("api_routes.fetch_and_save_pulse") as pulse, \
         patch("api_routes.DataEngine") as engine, \
         patch("api_routes.QuantEngine"), \
         patch("api_routes.update_all_tail_risks"), \
         patch("api_routes.update_all_sentiment"):
        engine.return_value.fetch_and_save_data.return_value = True
        resp = client.post("/api/index/refresh", json={"ticker": "^GSPC"})
    _assert_success(resp, "/api/index/refresh")
    pulse.assert_called_once_with(["^GSPC"])


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


@pytest.mark.api
@pytest.mark.parametrize("endpoint,label", TRIGGER_ENDPOINTS, ids=[t[1] for t in TRIGGER_ENDPOINTS])
def test_trigger_endpoint_answers_409_busy_while_the_job_is_running(client, endpoint, label):
    """A manual trigger for a job that is already running is refused before any task is queued."""
    with patch("scheduler_engine.is_job_running", return_value=True), \
         patch.object(_StarletteBackgroundTasks, "add_task") as add_task:
        resp = client.post(endpoint)
    assert resp.status_code == 409, f"{label}: expected 409, got {resp.status_code}"
    data = _json(resp)
    assert data["status"] == "busy"
    assert "already running" in data["message"]
    add_task.assert_not_called()


@pytest.mark.api
@pytest.mark.parametrize("endpoint", [
    "/api/ai-contagion/trigger", "/api/trap-monitor/run", "/api/alert-referee/train",
    "/api/alert-referee/train?engine=Confluence", "/api/bubble-radar/run", "/api/risk-orchestrator/run",
    "/api/pairs-spread/run", "/api/pairs-spread/run-universe", "/api/pattern-detection/run",
    "/api/pattern-detection/backfill", "/api/market-regime/run", "/api/sector-relative-momentum/run",
    "/api/predicted-movers/shortlist/run", "/api/forensic-scores/run-fetch", "/api/forensic-scores/run-score",
    "/api/xray/trigger", "/api/accounts/value-snapshot/trigger",
    "/api/accounts/treasury-bills/maturity-sweep/trigger",
])
def test_other_trigger_endpoints_answer_409_busy_while_the_job_is_running(client, endpoint):
    with patch("scheduler_engine.is_job_running", return_value=True), \
         patch.object(_StarletteBackgroundTasks, "add_task") as add_task:
        resp = client.post(endpoint)
    assert resp.status_code == 409, f"{endpoint}: expected 409, got {resp.status_code}"
    assert _json(resp)["status"] == "busy"
    add_task.assert_not_called()


@pytest.mark.api
def test_every_manual_job_route_checks_the_running_guard():
    """A route that queues run_manual_job must call job_busy_response in the same function."""
    import ast
    unguarded = []
    for path in sorted(Path(__file__).parent.parent.glob("api_routes*.py")):
        for fn in ast.walk(ast.parse(path.read_text())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            if "run_manual_job" in names and "job_busy_response" not in names:
                unguarded.append(f"{path.name}:{fn.name}")
    assert unguarded == []


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.mark.api
def test_post_settings_with_valid_payload(client, step_up_headers):
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
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    data = _json(resp)
    assert data.get("status") == "success", f"Expected success, got: {data}"


@pytest.mark.api
def test_post_settings_with_account_currencies(client, step_up_headers):
    """POST /api/settings with ACCOUNT_CURRENCIES must persist and round-trip via load_config."""
    import config as _config
    payload = {"ACCOUNT_CURRENCIES": ["GBP", "GBp", "USD", "EUR", "JPY"]}
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    assert _config.load_config().get("ACCOUNT_CURRENCIES") == ["GBP", "GBp", "USD", "EUR", "JPY"]


@pytest.mark.api
def test_post_settings_with_notification_routing(client, step_up_headers):
    """POST /api/settings with NOTIFICATION_ROUTING must persist and round-trip via load_config."""
    import config as _config
    payload = {
        "NOTIFICATION_ROUTING": {
            "crash_alert": {"log_file": True, "in_app": True, "nextcloud_talk": False},
            "quant_analysis_job": {"log_file": False, "in_app": True, "nextcloud_talk": True},
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    routing = _config.load_config().get("NOTIFICATION_ROUTING", {})
    assert routing.get("crash_alert", {}).get("nextcloud_talk") is False
    assert routing.get("quant_analysis_job", {}).get("nextcloud_talk") is True


@pytest.mark.api
def test_post_settings_with_risk_orchestrator(client, step_up_headers):
    """POST /api/settings with SCHEDULING.RISK_ORCHESTRATOR (weights/thresholds) and
    RISK_ORCHESTRATOR_DIGEST must persist and round-trip via load_config — regression for a
    bug where SchedulingConfig had no RISK_ORCHESTRATOR field at all, so the whole block was
    silently dropped by Pydantic on every save."""
    import config as _config
    payload = {
        "SCHEDULING": {
            "RISK_ORCHESTRATOR": {
                "ENABLED": True,
                "TIME": "20:05",
                "WEIGHTS": {"VAR": 0.5, "CORRELATION": 0.25, "DRAWDOWN": 0.25},
                "THRESHOLDS": {"PHI_YELLOW": 35, "PHI_RED": 70},
            },
            "RISK_ORCHESTRATOR_DIGEST": {"ENABLED": True, "TIME": "20:20"},
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    scheduling = _config.load_config().get("SCHEDULING", {})
    ro = scheduling.get("RISK_ORCHESTRATOR", {})
    assert ro.get("TIME") == "20:05"
    assert ro.get("WEIGHTS", {}).get("VAR") == 0.5
    assert ro.get("THRESHOLDS", {}).get("PHI_YELLOW") == 35
    digest = scheduling.get("RISK_ORCHESTRATOR_DIGEST", {})
    assert digest.get("ENABLED") is True
    assert digest.get("TIME") == "20:20"


@pytest.mark.api
def test_post_settings_with_stable_shortlist_round_trips(client, step_up_headers):
    """POST /api/settings with SCHEDULING.STABLE_SHORTLIST must persist every field and reload the scheduler."""
    import config as _config
    block = {"ENABLED": True, "DAYS": ["thu"], "TIME": "20:15", "TOPK": 8, "N_DROP": 3, "HOLD_THRESH": 1,
             "SECTOR_CAP": 3, "MAX_SIGNAL_AGE_DAYS": 5, "MIN_QUANT_SCORE": 60}
    resp = client.post("/api/settings", json={"SCHEDULING": {"STABLE_SHORTLIST": block}},
                       headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    saved = _config.load_config()["SCHEDULING"]["STABLE_SHORTLIST"]
    assert {k: saved[k] for k in block} == block


@pytest.mark.api
@pytest.mark.parametrize("field,value", [
    ("TOPK", 0), ("TOPK", 51), ("N_DROP", -1), ("HOLD_THRESH", 13), ("SECTOR_CAP", 0),
    ("MAX_SIGNAL_AGE_DAYS", 0), ("MIN_QUANT_SCORE", 101),
])
def test_post_settings_rejects_out_of_range_stable_shortlist_values(client, step_up_headers, field, value):
    """The Stable Shortlist turnover settings are bounded at the settings boundary."""
    resp = client.post("/api/settings", json={"SCHEDULING": {"STABLE_SHORTLIST": {field: value}}},
                       headers=step_up_headers)
    assert resp.status_code == 422, f"{field}={value} → {resp.status_code}"


@pytest.mark.api
@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN", "1e999"])
@pytest.mark.parametrize("template", [
    '{"POSITION_SIZING": {"ACCOUNT_VALUE": %s}}',
    '{"REPORTS_DEFAULTS": {"DIV_MIN_YIELD": %s}}',
    '{"SCHEDULING": {"SYNC_INDICES": {"FLASH_CRASH_THRESHOLD": %s}}}',
    '{"SCHEDULING": {"RISK_ORCHESTRATOR": {"WEIGHTS": {"VAR": %s}}}}',
    '{"NOTIFICATIONS": {"CRASH_ALERTS": {"DROP_PERCENT": %s}}}',
    '{"NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {"HEAD_SHOULDERS": {"NUM_STD": %s}}}}',
    '{"XRAY_TARGETS": {"concentration_targets": {"max_single_position_pct": %s}}}',
    '{"META_SCORING": {"REGIME_WEIGHTS": {"bull": [1.0, %s]}}}',
    '{"NOTIFICATION_ROUTING": {"sentiment": {"weight": %s}}}',
    '{"FREETRADE_MAPPINGS": {"EXCHANGES": {"XLON": %s}}}',
    '{"UI_PREFERENCES": {"PORTFOLIO_VIEWS": [{"name": "v", "min": %s}]}}',
    '{"GHOSTFOLIO_ACCOUNTS": {"discovered": [{"id": "a", "balance": %s}]}}',
])
def test_post_settings_rejects_non_finite_numbers(client, step_up_headers, template, literal):
    """A non-finite number anywhere in the settings body, typed or free-form, is a 422 and never reaches config.json."""
    headers = {**step_up_headers, "Content-Type": "application/json"}
    resp = client.post("/api/settings", content=template % literal, headers=headers)
    assert resp.status_code == 422, f"{template % literal} → {resp.status_code}"


@pytest.mark.api
def test_post_settings_still_accepts_finite_numbers_in_free_form_blocks(client, step_up_headers):
    import config as _config
    payload = {"XRAY_TARGETS": {"concentration_targets": {"max_single_position_pct": 22.5}},
               "POSITION_SIZING": {"ACCOUNT_VALUE": 12345.5}}
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    saved = _config.load_config()
    assert saved["XRAY_TARGETS"]["concentration_targets"]["max_single_position_pct"] == 22.5
    assert saved["POSITION_SIZING"]["ACCOUNT_VALUE"] == 12345.5


@pytest.mark.api
def test_post_settings_with_alert_referee_training_confluence(client, step_up_headers):
    """POST /api/settings with SCHEDULING.ALERT_REFEREE_TRAINING_CONFLUENCE (the Cross-Engine
    Alert Referee's own schedule/mode/threshold block) must persist and round-trip via
    load_config — regression for a bug where SchedulingConfig had no
    ALERT_REFEREE_TRAINING_CONFLUENCE field at all, so the whole block (including the Enable
    checkbox) was silently dropped by Pydantic on every save."""
    import config as _config
    payload = {
        "SCHEDULING": {
            "ALERT_REFEREE_TRAINING_CONFLUENCE": {
                "ENABLED": True,
                "DAYS": ["mon", "wed", "fri"],
                "TIME": "06:15",
                "MODE": "active",
                "VETO_THRESHOLD": 0.4,
                "MIN_TRAINING_SAMPLES": 150,
            },
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    block = _config.load_config().get("SCHEDULING", {}).get("ALERT_REFEREE_TRAINING_CONFLUENCE", {})
    assert block.get("ENABLED") is True
    assert block.get("DAYS") == ["mon", "wed", "fri"]
    assert block.get("TIME") == "06:15"
    assert block.get("MODE") == "active"
    assert block.get("VETO_THRESHOLD") == 0.4
    assert block.get("MIN_TRAINING_SAMPLES") == 150


@pytest.mark.api
def test_post_settings_with_position_sizing_min_risk_reward(client, step_up_headers):
    """POST /api/settings with POSITION_SIZING.MIN_RISK_REWARD must persist and round-trip via
    load_config — regression for a bug where PositionSizingConfig had no MIN_RISK_REWARD field,
    so the Position Sizing Defaults card's Min Risk/Reward input was silently dropped on every
    save and position_sizing.py's Risk/Reward Gate always ran on the hardcoded 1.5 default."""
    import config as _config
    payload = {
        "POSITION_SIZING": {
            "ACCOUNT_VALUE": 20000.0,
            "RISK_PCT": 2.0,
            "STOP_MULTIPLE": 2.5,
            "MIN_RISK_REWARD": 1.8,
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    block = _config.load_config().get("POSITION_SIZING", {})
    assert block.get("MIN_RISK_REWARD") == 1.8


@pytest.mark.api
def test_post_settings_with_crash_alerts_flash_crash_threshold(client, step_up_headers):
    """POST /api/settings with NOTIFICATIONS.CRASH_ALERTS.FLASH_CRASH_THRESHOLD must persist and
    round-trip via load_config — regression for a bug where NotificationItemConfig had no
    FLASH_CRASH_THRESHOLD field, so the Crash & Moonshot Alerts card's Flash Crash Threshold
    input was silently dropped on every save and crash_engine.py always ran on the hardcoded
    3.0 default."""
    import config as _config
    payload = {
        "NOTIFICATIONS": {
            "CRASH_ALERTS": {
                "DROP_PERCENT": 6.0,
                "DROP_DAYS": 3,
                "SMA_LENGTH": 10,
                "SMA_GAP_PERCENT": 2.0,
                "FLASH_CRASH_THRESHOLD": 4.5,
            }
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    block = _config.load_config().get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {})
    assert block.get("FLASH_CRASH_THRESHOLD") == 4.5


@pytest.mark.api
def test_post_settings_with_risk_orchestrator_alerts(client, step_up_headers):
    """POST /api/settings with NOTIFICATIONS.RISK_ORCHESTRATOR_ALERTS (Pillar C2's shared
    cooldown block for PHI/correlation/stop-breach escalations) must persist and round-trip."""
    import config as _config
    payload = {
        "NOTIFICATIONS": {
            "RISK_ORCHESTRATOR_ALERTS": {
                "ENABLED": True, "COOLDOWN_MINUTES": 45.0, "RETRIGGER_PERCENT": 4.0, "REARM_PERCENT": 8.0,
            }
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    block = _config.load_config().get("NOTIFICATIONS", {}).get("RISK_ORCHESTRATOR_ALERTS", {})
    assert block.get("ENABLED") is True
    assert block.get("COOLDOWN_MINUTES") == 45.0
    assert block.get("RETRIGGER_PERCENT") == 4.0
    assert block.get("REARM_PERCENT") == 8.0


@pytest.mark.api
def test_post_settings_with_meta_scoring(client, step_up_headers):
    """POST /api/settings with META_SCORING (Regime-Weighted Conviction Score's regime-switched
    weight vectors and crash-veto threshold) must persist and round-trip via load_config."""
    import config as _config
    payload = {
        "META_SCORING": {
            "REGIME_WEIGHTS": {
                "Bull": {"composite_score": 0.5, "ml_confidence": 0.3, "pattern": 0.15, "trap": 0.05},
                "Chop": {"composite_score": 0.2, "ml_confidence": 0.2, "pattern": 0.4, "trap": 0.2},
            },
            "CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.8},
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    ms = _config.load_config().get("META_SCORING", {})
    assert ms.get("REGIME_WEIGHTS", {}).get("Bull", {}).get("composite_score") == 0.5
    assert ms.get("REGIME_WEIGHTS", {}).get("Chop", {}).get("pattern") == 0.4
    assert ms.get("CRASH_VETO", {}).get("MARKET_STRESS_THRESHOLD") == 0.8


@pytest.mark.api
def test_post_settings_ghostfolio_disabled_purges_files_and_clears_accounts(client, step_up_headers):
    """POST /api/settings with GHOSTFOLIO_ENABLED=False must purge portfolio.json/watchlist.json,
    clear GHOSTFOLIO_ACCOUNTS, and force GHOSTFOLIO_SYNC.ENABLED off."""
    import config as _config
    payload = {
        "GHOSTFOLIO_ENABLED": False,
        "GHOSTFOLIO_ACCOUNTS": {"discovered": [{"id": "acc-1", "name": "Test", "currency": "GBP"}], "active": ["acc-1"]},
        "SCHEDULING": {"GHOSTFOLIO_SYNC": {"ENABLED": True, "FREQUENCY": "mon-fri", "INTERVAL_HOURS": 0, "TIME": "06:00"}},
    }
    with patch("api_routes_system.purge_ghostfolio_files") as mock_purge:
        resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    assert _json(resp).get("status") == "success"
    mock_purge.assert_called_once()
    cfg = _config.load_config()
    assert cfg.get("GHOSTFOLIO_ENABLED") is False
    assert cfg.get("GHOSTFOLIO_ACCOUNTS") == {"discovered": [], "active": []}
    assert cfg.get("SCHEDULING", {}).get("GHOSTFOLIO_SYNC", {}).get("ENABLED") is False


@pytest.mark.api
def test_post_settings_ghostfolio_enabled_does_not_purge(client, step_up_headers):
    """POST /api/settings with GHOSTFOLIO_ENABLED=True must not touch portfolio.json/watchlist.json."""
    payload = {"GHOSTFOLIO_ENABLED": True}
    with patch("api_routes_system.purge_ghostfolio_files") as mock_purge:
        resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200
    assert _json(resp).get("status") == "success"
    mock_purge.assert_not_called()


@pytest.mark.api
def test_post_settings_with_position_sizing(client, step_up_headers):
    """POST /api/settings with position sizing values must return status=success."""
    payload = {
        "POSITION_SIZING": {
            "ACCOUNT_VALUE": 50000.0,
            "RISK_PCT": 1.0,
            "STOP_MULTIPLE": 2.0,
        }
    }
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
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
@pytest.mark.parametrize("field", ["current_price", "strike", "premium"])
def test_post_options_payoff_rejects_non_finite_number(client, field):
    values = {"current_price": "150.0", "strike": "155.0", "premium": "3.5"}
    values[field] = "Infinity"
    body = (
        '{"current_price": %s, "legs": [{"type": "call", "strike": %s, "premium": %s, '
        '"position": "long", "quantity": 1}]}' % (values["current_price"], values["strike"], values["premium"])
    )
    resp = client.post("/api/options/payoff", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422


@pytest.mark.api
def test_validation_error_response_does_not_echo_the_rejected_input(client):
    resp = client.post(
        "/api/options/payoff",
        content='{"current_price": Infinity, "legs": []}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert errors and all({"loc", "msg", "type"} <= set(e) and "input" not in e for e in errors)


@pytest.mark.api
def test_post_options_payoff_caps_the_number_of_legs(client):
    from constants import OPTION_LEGS_MAX
    leg = {"type": "call", "strike": 155.0, "premium": 3.5, "position": "long", "quantity": 1}
    ok = client.post("/api/options/payoff", json={"current_price": 150.0, "legs": [leg] * OPTION_LEGS_MAX})
    too_many = client.post("/api/options/payoff", json={"current_price": 150.0, "legs": [leg] * (OPTION_LEGS_MAX + 1)})
    assert ok.status_code == 200
    assert too_many.status_code == 422


@pytest.mark.api
def test_post_options_payoff_is_rate_limited_to_sixty_per_minute(client):
    from api_deps import limiter
    limiter.reset()
    leg = {"type": "call", "strike": 155.0, "premium": 3.5, "position": "long", "quantity": 1}
    try:
        codes = [
            client.post("/api/options/payoff", json={"current_price": 150.0, "legs": [leg]}).status_code
            for _ in range(61)
        ]
    finally:
        limiter.reset()
    assert codes == [200] * 60 + [429]


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


@pytest.mark.api
def test_refresh_single_completes_analysis_before_reporting_fx_failure(client, monkeypatch):
    calls = []
    def failed(currencies):
        raise RuntimeError("FX refresh failed for USDGBP=X; last-good cached data retained.")
    monkeypatch.setattr("portfolio_service.refresh_fx_rates", failed)
    monkeypatch.setattr("accounts_engine.native_currencies", lambda tickers: {"AAPL": "USD"})
    with patch("api_routes.update_single_profile"), \
         patch("api_routes.DataEngine") as MockDE, \
         patch("api_routes.QuantEngine"), \
         patch("api_routes.update_daily_ml_predictions", side_effect=lambda tickers: calls.append("ml")), \
         patch("api_routes.update_all_tail_risks", side_effect=lambda tickers: calls.append("tail_risk")), \
         patch("api_routes.update_all_sentiment", side_effect=lambda tickers: calls.append("sentiment")):
        MockDE.return_value.fetch_and_save_data.return_value = True
        resp = client.post("/api/data/refresh-single", json={"ticker": "AAPL"})
    assert resp.status_code == 500
    assert resp.json()["status"] == "error"
    assert calls == ["ml", "tail_risk", "sentiment"]


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

    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        from api_deps import limiter
        limiter.reset()
        yield
        limiter.reset()

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


@pytest.mark.api
class TestImportServerJobGuard:
    """The CSV import's quant scan is guarded by its own job id and refused before any DB write while it runs."""

    TICKER = "ZZIMPORTGUARD.L"

    @pytest.fixture(autouse=True)
    def _csv_dir(self, tmp_path, monkeypatch):
        import api_routes_triggers
        from api_deps import limiter
        from database import get_connection
        limiter.reset()
        monkeypatch.setattr(api_routes_triggers, "IMPORT_DIR", tmp_path)
        (tmp_path / "guard.csv").write_text(
            "ticker,company_name,sector,industry,currency,country,exchange\n"
            f"{self.TICKER},Guard Co,Technology,Software,GBP,UK,LSE\n"
        )
        yield
        limiter.reset()
        conn = None
        try:
            conn = get_connection()
            conn.execute("DELETE FROM market_universe WHERE ticker = ?", (self.TICKER,))
            conn.commit()
        finally:
            if conn:
                conn.close()

    def _universe_row(self):
        from database import get_connection
        conn = None
        try:
            conn = get_connection()
            return conn.execute("SELECT ticker FROM market_universe WHERE ticker = ?", (self.TICKER,)).fetchone()
        finally:
            if conn:
                conn.close()

    def test_busy_answers_409_before_any_write_or_queue(self, client):
        with patch("scheduler_engine.is_job_running", return_value=True), \
             patch.object(_StarletteBackgroundTasks, "add_task") as add_task:
            resp = client.post("/api/universe/import/server", json={"filename": "guard.csv"})
        assert resp.status_code == 409
        data = _json(resp)
        assert data["status"] == "busy"
        assert "Manual Local File Import" in data["message"]
        assert "already running" in data["message"]
        add_task.assert_not_called()
        assert self._universe_row() is None

    def test_import_queues_the_scan_under_its_own_job_id(self, client):
        from scheduler_engine import run_manual_job, run_universe_csv_import_scan
        with patch.object(_StarletteBackgroundTasks, "add_task") as add_task:
            resp = client.post("/api/universe/import/server", json={"filename": "guard.csv"})
        assert resp.status_code == 200
        assert _json(resp)["status"] == "success"
        add_task.assert_called_once()
        assert add_task.call_args.args == (
            run_manual_job, "universe_csv_import_source", run_universe_csv_import_scan, [self.TICKER],
        )
        assert self._universe_row() is not None

    def test_a_running_universe_update_does_not_block_the_import(self, client):
        with patch("scheduler_engine.is_job_running", side_effect=lambda job_id: job_id == "universe_routine_job"), \
             patch.object(_StarletteBackgroundTasks, "add_task") as add_task:
            resp = client.post("/api/universe/import/server", json={"filename": "guard.csv"})
        assert resp.status_code == 200
        add_task.assert_called_once()


# ── Intraday Dip Radar ────────────────────────────────────────────────────────

@pytest.mark.api
class TestIntradayDipRadar:

    @pytest.fixture(autouse=True)
    def _clean_monitors(self):
        yield
        from database import get_connection
        conn = None
        try:
            conn = get_connection()
            conn.execute("DELETE FROM intraday_monitors")
            conn.commit()
        finally:
            if conn:
                conn.close()

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


# ── Alert Confidence Referee ──────────────────────────────────────────────────

@pytest.mark.api
def test_alert_referee_train_returns_success(client):
    """POST /api/alert-referee/train must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None):
        resp = client.post("/api/alert-referee/train")
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


# ── Sector-Relative Momentum ──────────────────────────────────────────────────

@pytest.mark.api
def test_sector_relative_momentum_run_returns_success(client):
    """POST /api/sector-relative-momentum/run must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None) as add_task:
        resp = client.post("/api/sector-relative-momentum/run")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json().get("status") == "success"
    assert add_task.call_args.args[1] == "sector_relative_momentum_job"


# ── Stable Shortlist ──────────────────────────────────────────────────────────

@pytest.mark.api
def test_stable_shortlist_run_returns_success(client):
    """POST /api/predicted-movers/shortlist/run must return 200 {status: success} immediately."""
    with patch.object(_StarletteBackgroundTasks, "add_task", return_value=None) as add_task:
        resp = client.post("/api/predicted-movers/shortlist/run")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json().get("status") == "success"
    assert add_task.call_args.args[1] == "stable_shortlist_job"


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
    assert data["mode"] == "unconstrained"
    assert all("suggested_weight_ew" in w for w in data["weights"])


@pytest.mark.api
def test_portfolio_optimizer_run_long_only_respects_cap(client):
    """Long-Only weights must stay within [0, max_weight] and sum to 1 − cash_reserve."""
    import json as _json_mod
    import numpy as np
    import pandas as pd
    import database as _db
    from xray_engine import BENCHMARK_SYMBOL

    tickers = ("POAL1", "POAL2", "POAL3", "POAL4")
    conn = _db.get_connection()
    try:
        rng = np.random.default_rng(12)
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", periods=252)]
        for i, ticker in enumerate(tickers):
            conn.execute(
                "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
                (ticker, 100.0, "GBP"),
            )
            rets = rng.normal(0.0003 + 0.0001 * i, 0.008 + 0.002 * i, 252).tolist()
            conn.execute(
                """INSERT OR REPLACE INTO xray_returns_cache
                   (ticker, benchmark, last_updated, dates_json, returns_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (ticker, BENCHMARK_SYMBOL, "2026-06-03", _json_mod.dumps(dates), _json_mod.dumps(rets)),
            )
        conn.commit()
    finally:
        conn.close()

    account_id = _db.create_account("POA Long-Only Test", "GBP")
    for ticker in tickers:
        _db.add_transaction(account_id, "Buy", "2026-01-05", ticker=ticker, currency="GBP",
                             quantity=10, unit_price=80, exchange_rate=1.0)

    with patch("xray_engine.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}, "RISK_FREE_RATE": 0.045}), \
         patch("portfolio_optimizer_engine.load_config", return_value={"RISK_FREE_RATE": 0.045}):
        resp = client.post("/api/portfolio-optimizer/run", json={
            "account_id": f"acct:{account_id}", "include_tickers": list(tickers),
            "mode": "long_only", "max_weight": 0.3, "cash_reserve": 0.05,
        })

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = _json(resp)
    assert data["status"] == "success"
    assert data["mode"] == "long_only"
    assert data["max_weight"] == 0.3
    assert data["cash_reserve"] == 0.05
    mv = [w["suggested_weight_mv"] for w in data["weights"]]
    assert sum(mv) == pytest.approx(0.95, abs=1e-3)
    assert min(mv) >= 0 and max(mv) <= 0.3 + 1e-4
    assert data["efficient_frontier"]["equal_weight"] is not None
    assert data["estimation_window"]["trading_days"] == 252


@pytest.mark.api
@pytest.mark.parametrize("body", [
    {"mode": "short_only"},
    {"mode": "long_only", "max_weight": 0},
    {"mode": "long_only", "max_weight": 1.5},
    {"mode": "long_only", "cash_reserve": 1.0},
    {"mode": "long_only", "cash_reserve": -0.1},
    {"min_history_days": -1},
    {"min_history_days": 300},
    {"strategy": "not_a_strategy"},
])
def test_portfolio_optimizer_run_rejects_invalid_rules(client, body):
    """Mode, Weight Cap and Cash Reserve are validated at the API boundary (422)."""
    resp = client.post("/api/portfolio-optimizer/run", json={"account_id": "all", **body})
    assert resp.status_code == 422, f"Expected 422 for {body}, got {resp.status_code}"


# ── Backup & Recovery ────────────────────────────────────────────────────────

@pytest.mark.api
def test_backup_restore_without_step_up_returns_403(client):
    """POST /api/backup/restore without a step-up cookie must return 403 and flag the step-up prompt."""
    resp = client.post("/api/backup/restore", json={"filename": "backup_20260101_000000.tar.gz"})
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
    assert resp.headers.get("X-Step-Up-Required") == "1"


@pytest.mark.api
def test_backup_restore_rejects_path_traversal(client, step_up_headers):
    """POST /api/backup/restore with a path-traversal filename must return 400, not touch disk."""
    resp = client.post(
        "/api/backup/restore",
        json={"filename": "../../etc/passwd"},
        headers=step_up_headers,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert _json(resp).get("status") == "error"


@pytest.mark.api
def test_backup_restore_success(client, step_up_headers):
    """POST /api/backup/restore with a valid filename must return status=success when the engine succeeds."""
    with patch("api_routes_triggers.restore_backup", return_value={"status": "success", "message": "Restore completed."}):
        resp = client.post(
            "/api/backup/restore",
            json={"filename": "backup_20260101_000000.tar.gz"},
            headers=step_up_headers,
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert _json(resp).get("status") == "success"


@pytest.mark.api
def test_backup_restore_engine_error_returns_500(client, step_up_headers):
    """POST /api/backup/restore must surface an engine-level failure as a 500 with status=error."""
    with patch("api_routes_triggers.restore_backup", return_value={"status": "error", "message": "Backup file not found"}):
        resp = client.post(
            "/api/backup/restore",
            json={"filename": "backup_missing.tar.gz"},
            headers=step_up_headers,
        )
    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"
    assert _json(resp).get("status") == "error"


@pytest.mark.api
def test_git_pull_flags_requirements_lock_change(client, step_up_headers):
    """POST /api/system/git-pull must flag requirements_changed when requirements.lock appears in the pulled diff."""
    import api_routes_system

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="oldsha\n", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Updating oldsha..newsha\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return MagicMock(returncode=0, stdout="requirements.lock\nmain.py\n", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    api_routes_system._requirements_changed_pending = False
    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        resp = client.post("/api/system/git-pull", headers=step_up_headers)
    assert resp.status_code == 200
    body = _json(resp)
    assert body["requirements_changed"] is True
    assert api_routes_system._requirements_changed_pending is True
    api_routes_system._requirements_changed_pending = False


@pytest.mark.api
def test_git_pull_warns_when_scikit_learn_pin_changes(client, step_up_headers):
    import api_routes_system

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="oldsha\n", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Updating oldsha..newsha\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return MagicMock(returncode=0, stdout="requirements.lock\n", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    api_routes_system._requirements_changed_pending = False
    api_routes_system._sklearn_change_pending = None
    with (
        patch("api_routes_system.subprocess.run", side_effect=fake_run),
        patch(
            "api_routes_system.Path.read_text",
            side_effect=["scikit-learn==1.9.0\n", "scikit-learn==1.9.1\n"],
        ),
    ):
        resp = client.post("/api/system/git-pull", headers=step_up_headers)

    body = _json(resp)
    assert resp.status_code == 200
    assert body["scikit_learn_changed"] is True
    assert body["scikit_learn_change"] == {
        "from_version": "1.9.0",
        "to_version": "1.9.1",
    }
    assert "normally within seconds" in body["message"]

    status = _json(client.get("/api/system/active-jobs"))
    assert status["scikit_learn_change_pending"] == body["scikit_learn_change"]
    api_routes_system._requirements_changed_pending = False
    api_routes_system._sklearn_change_pending = None


@pytest.mark.api
def test_git_pull_does_not_flag_unrelated_changes(client, step_up_headers):
    """POST /api/system/git-pull must not flag requirements_changed when requirements.lock wasn't touched; the input file alone changes nothing that is installed."""
    import api_routes_system

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="oldsha\n", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Updating oldsha..newsha\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return MagicMock(returncode=0, stdout="main.py\nrequirements.txt\n", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    api_routes_system._requirements_changed_pending = False
    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        resp = client.post("/api/system/git-pull", headers=step_up_headers)
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


@pytest.mark.api
@pytest.mark.parametrize(
    "running_head, current_head, expected",
    [
        ("aaa111", "aaa111", False),
        ("aaa111", "bbb222", True),
        (None, "bbb222", False),
        ("aaa111", None, False),
    ],
)
def test_active_jobs_restart_pending_compares_running_and_disk_commit(client, monkeypatch, running_head, current_head, expected):
    """GET /api/system/active-jobs flags restart_pending only when both commits are known and differ."""
    import api_routes_system

    monkeypatch.setattr(api_routes_system, "_RUNNING_HEAD", running_head)
    monkeypatch.setattr(api_routes_system, "_git_head", lambda: current_head)
    resp = client.get("/api/system/active-jobs")
    assert resp.status_code == 200
    assert _json(resp)["restart_pending"] is expected


@pytest.mark.api
def test_restart_pending_survives_second_pull_that_is_already_up_to_date(client, step_up_headers, monkeypatch):
    """A second Pull Latest click reporting 'Already up to date' must not clear the restart-pending state."""
    import api_routes_system

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="newsha\n", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Already up to date.\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    monkeypatch.setattr(api_routes_system, "_RUNNING_HEAD", "oldsha")
    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        pull = client.post("/api/system/git-pull", headers=step_up_headers)
        status = client.get("/api/system/active-jobs")
    assert pull.status_code == 200
    assert _json(status)["restart_pending"] is True


@pytest.mark.parametrize(
    "outcome",
    [
        MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository"),
        OSError("git not installed"),
    ],
)
def test_git_head_returns_none_when_git_unavailable(outcome):
    import api_routes_system

    kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
    with patch("api_routes_system.subprocess.run", **kwargs):
        assert api_routes_system._git_head() is None


@pytest.mark.api
def test_active_jobs_reports_average_duration_per_running_job(client):
    """GET /api/system/active-jobs must return each running job's average duration beside its start time."""
    import scheduler_engine

    name = "Account Price Scraper — API Test"
    with patch.object(scheduler_engine, "get_all_job_last_runs", return_value={"account_scraper_99_job": {"avg_duration_sec": 42.5}}):
        scheduler_engine._mark_job_started(name, "account_scraper_99_job")
        try:
            body = _json(client.get("/api/system/active-jobs"))
        finally:
            scheduler_engine._mark_job_done(name)
    assert name in body["active_jobs"]
    assert body["active_job_avg_sec"][name] == 42.5


@pytest.fixture
def restart_env(tmp_path, monkeypatch):
    import api_routes_system

    monkeypatch.setattr(api_routes_system, "_pip_tmp_dir", lambda: tmp_path / "pip_tmp")
    monkeypatch.setattr(api_routes_system, "_requirements_changed_pending", False)
    monkeypatch.setattr(api_routes_system, "check_requirements_drift", lambda: [])
    monkeypatch.setitem(api_routes_system._dependency_install, "state", "idle")
    monkeypatch.setitem(api_routes_system._dependency_install, "command", None)
    return api_routes_system


def _run_execute_restart(module, pip_result=None, pip_side_effect=None):
    import asyncio

    with (
        patch("api_routes_system.subprocess.run", return_value=pip_result, side_effect=pip_side_effect) as mock_run,
        patch("api_routes_system.notify") as mock_notify,
        patch("api_routes_system.asyncio.sleep", new=AsyncMock()),
        patch("api_routes_system.os.kill") as mock_kill,
    ):
        asyncio.run(module.execute_restart())
    return mock_run, mock_notify, mock_kill


def _manual_pip_command():
    import shlex
    from config import BASE_DIR

    return shlex.join([sys.executable, "-m", "pip", "install", "--require-hashes", "-r", str(BASE_DIR / "requirements.lock")])


def test_execute_restart_installs_pip_dependencies_when_pending(restart_env, tmp_path):
    """execute_restart() must run pip install before signalling shutdown when requirements.lock changed."""
    from constants import DEPENDENCY_INSTALL_TIMEOUT_SECONDS

    restart_env._requirements_changed_pending = True
    mock_run, mock_notify, mock_kill = _run_execute_restart(
        restart_env, pip_result=MagicMock(returncode=0, stdout="", stderr=""),
    )

    args, kwargs = mock_run.call_args
    assert args[0][:6] == [sys.executable, "-m", "pip", "install", "--require-hashes", "-r"]
    assert "--resume-retries" in args[0]
    assert kwargs["timeout"] == DEPENDENCY_INSTALL_TIMEOUT_SECONDS
    assert kwargs["env"]["TMPDIR"] == str(tmp_path / "pip_tmp")
    assert not (tmp_path / "pip_tmp").exists()
    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][:2] == ("system_update_status", "Success")
    mock_kill.assert_called_once()
    assert restart_env._requirements_changed_pending is False
    assert restart_env._dependency_install["state"] == "restarting"


def test_execute_restart_skips_pip_install_when_not_pending(restart_env):
    """execute_restart() must not touch pip when nothing is pending and installed packages match requirements.lock."""
    mock_run, mock_notify, mock_kill = _run_execute_restart(restart_env)

    mock_run.assert_not_called()
    mock_notify.assert_not_called()
    mock_kill.assert_called_once()


def test_execute_restart_installs_when_packages_drifted_without_pending_flag(restart_env, monkeypatch):
    """A lost in-memory pull flag must not stop the reinstall while installed packages differ from requirements.lock."""
    monkeypatch.setattr(restart_env, "check_requirements_drift", lambda: ["torch: installed 2.13.0, requirements.lock pins ==2.14.1"])
    mock_run, mock_notify, mock_kill = _run_execute_restart(
        restart_env, pip_result=MagicMock(returncode=0, stdout="", stderr=""),
    )

    mock_run.assert_called_once()
    mock_kill.assert_called_once()
    assert mock_notify.call_args[0][:2] == ("system_update_status", "Success")


def test_execute_restart_aborts_and_gives_shell_command_when_pip_fails(restart_env):
    """A failed install must not restart onto missing packages; the notification carries the command to run by hand."""
    restart_env._requirements_changed_pending = True
    mock_run, mock_notify, mock_kill = _run_execute_restart(
        restart_env, pip_result=MagicMock(returncode=1, stdout="", stderr="ERROR: boom"),
    )

    mock_kill.assert_not_called()
    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][:2] == ("system_update_status", "Error")
    assert _manual_pip_command() in mock_notify.call_args[0][2]
    assert "ERROR: boom" in mock_notify.call_args[0][2]
    assert restart_env._requirements_changed_pending is True
    assert restart_env._dependency_install["state"] == "failed"
    assert restart_env._dependency_install["command"] == _manual_pip_command()
    assert restart_env._dependency_install_lock.acquire(blocking=False)
    restart_env._dependency_install_lock.release()


def test_execute_restart_aborts_when_pip_times_out(restart_env):
    """A pip timeout is a failed install: no restart, flag kept, shell command reported."""
    import subprocess

    restart_env._requirements_changed_pending = True
    _, mock_notify, mock_kill = _run_execute_restart(
        restart_env, pip_side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=1800),
    )

    mock_kill.assert_not_called()
    assert mock_notify.call_args[0][:2] == ("system_update_status", "Error")
    assert "within 30 minutes" in mock_notify.call_args[0][2]
    assert _manual_pip_command() in mock_notify.call_args[0][2]
    assert restart_env._requirements_changed_pending is True
    assert restart_env._dependency_install["state"] == "failed"


def test_execute_restart_does_not_start_a_second_pip_while_one_is_running(restart_env):
    """A second Restart click while pip runs must neither start another pip nor restart the app."""
    restart_env._requirements_changed_pending = True
    assert restart_env._dependency_install_lock.acquire(blocking=False)
    try:
        mock_run, mock_notify, mock_kill = _run_execute_restart(restart_env)
    finally:
        restart_env._dependency_install_lock.release()

    mock_run.assert_not_called()
    mock_notify.assert_not_called()
    mock_kill.assert_not_called()


@pytest.mark.api
def test_restart_reports_installing_when_dependencies_must_be_reinstalled(client, step_up_headers, restart_env):
    restart_env._requirements_changed_pending = True
    with patch("api_routes_system.execute_restart", new=AsyncMock()):
        resp = client.post("/api/system/restart", headers=step_up_headers)

    assert resp.status_code == 200
    body = _json(resp)
    assert body["installing"] is True
    assert "Installing updated dependencies" in body["message"]


@pytest.mark.api
def test_restart_keeps_quick_restart_message_when_nothing_to_install(client, step_up_headers, restart_env):
    with patch("api_routes_system.execute_restart", new=AsyncMock()):
        resp = client.post("/api/system/force-restart", headers=step_up_headers)

    assert resp.status_code == 200
    body = _json(resp)
    assert body["installing"] is False
    assert "back online in ~5-10 seconds" in body["message"]


@pytest.mark.api
def test_restart_is_refused_while_dependencies_are_installing(client, step_up_headers, restart_env):
    restart_env._dependency_install["state"] = "running"
    with patch("api_routes_system.execute_restart", new=AsyncMock()) as mock_restart:
        resp = client.post("/api/system/force-restart", headers=step_up_headers)

    assert resp.status_code == 409
    assert "already being installed" in _json(resp)["message"]
    mock_restart.assert_not_called()


@pytest.mark.api
def test_active_jobs_reports_package_drift_and_install_state(client, restart_env, monkeypatch):
    monkeypatch.setattr(restart_env, "check_requirements_drift", lambda: ["torch: installed 2.13.0, requirements.lock pins ==2.14.1"])
    restart_env._dependency_install.update(state="failed", command="/venv/bin/python3 -m pip install --require-hashes -r requirements.lock")

    body = _json(client.get("/api/system/active-jobs"))

    assert body["requirements_changed_pending"] is True
    assert body["dependency_install"] == {"state": "failed", "command": "/venv/bin/python3 -m pip install --require-hashes -r requirements.lock"}


@pytest.mark.api
def test_active_jobs_never_serves_pip_output_after_failed_install(client, restart_env):
    """pip's stderr belongs to the log and the Error notification; the active-jobs poll every session reads carries only state and command."""
    restart_env._requirements_changed_pending = True
    _, mock_notify, _ = _run_execute_restart(
        restart_env, pip_result=MagicMock(returncode=1, stdout="", stderr="ERROR: pip-secret-marker /srv/private/path"),
    )
    assert "pip-secret-marker" in mock_notify.call_args[0][2]

    resp = client.get("/api/system/active-jobs")

    assert "pip-secret-marker" not in resp.text
    assert set(_json(resp)["dependency_install"]) == {"state", "command"}


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


@pytest.mark.api
def test_post_learn_preference_persists_unlock_all_flag(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/learn/preference", json={"unlock_all": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_update.assert_called_once_with({"UI_PREFERENCES": {"GLOSSARY_LEARN_UNLOCK_ALL": True}})


@pytest.mark.api
def test_post_learn_preference_persists_study_all_flag(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/learn/preference", json={"study_all": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_update.assert_called_once_with({"UI_PREFERENCES": {"GLOSSARY_LEARN_STUDY_ALL": True}})


@pytest.mark.api
def test_post_learn_preference_with_both_fields_persists_both(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/learn/preference", json={"unlock_all": False, "study_all": True})
    assert resp.status_code == 200
    mock_update.assert_called_once_with(
        {"UI_PREFERENCES": {"GLOSSARY_LEARN_UNLOCK_ALL": False, "GLOSSARY_LEARN_STUDY_ALL": True}}
    )


@pytest.mark.api
def test_post_ui_preferences_columns_persists_portfolio_scope(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/ui-preferences/columns", json={
            "scope": "portfolio",
            "hidden_core_columns": ["sentiment"],
            "shown_optional_columns": ["trailing_pe", "beta"],
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_update.assert_called_once_with({"UI_PREFERENCES": {
        "PORTFOLIO_HIDDEN_CORE_COLUMNS": ["sentiment"],
        "PORTFOLIO_SHOWN_OPTIONAL_COLUMNS": ["trailing_pe", "beta"],
    }})


@pytest.mark.api
def test_post_ui_preferences_columns_persists_watchlist_scope(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/ui-preferences/columns", json={
            "scope": "watchlist",
            "hidden_core_columns": [],
            "shown_optional_columns": ["market_cap"],
        })
    assert resp.status_code == 200
    mock_update.assert_called_once_with({"UI_PREFERENCES": {
        "WATCHLIST_HIDDEN_CORE_COLUMNS": [],
        "WATCHLIST_SHOWN_OPTIONAL_COLUMNS": ["market_cap"],
    }})


@pytest.mark.api
def test_post_ui_preferences_columns_rejects_invalid_scope(client):
    resp = client.post("/api/ui-preferences/columns", json={"scope": "bogus"})
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


@pytest.mark.api
def test_post_ui_preferences_views_persists_portfolio_scope(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/ui-preferences/views", json={
            "scope": "portfolio",
            "views": [{"name": "My View", "columns": ["ticker", "price", "score"]}],
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_update.assert_called_once_with({"UI_PREFERENCES": {
        "PORTFOLIO_VIEWS": [{"name": "My View", "columns": ["ticker", "price", "score"]}],
    }})


@pytest.mark.api
def test_ui_preferences_columns_accept_the_largest_registry_column_list(client):
    from constants import TABLE_COLUMN_KEYS_MAX
    from table_columns_helpers import all_columns_for_page
    assert max(len(all_columns_for_page(p)) for p in ("portfolio", "watchlist")) <= TABLE_COLUMN_KEYS_MAX
    with patch("api_routes.update_config_atomic"):
        resp = client.post("/api/ui-preferences/columns", json={
            "scope": "portfolio", "column_order": ["ticker"] * TABLE_COLUMN_KEYS_MAX,
        })
    assert resp.status_code == 200


@pytest.mark.api
def test_ui_preferences_columns_reject_oversize_lists_and_keys(client):
    from constants import TABLE_COLUMN_KEY_MAX_CHARS, TABLE_COLUMN_KEYS_MAX
    too_long_list = ["ticker"] * (TABLE_COLUMN_KEYS_MAX + 1)
    too_long_key = "k" * (TABLE_COLUMN_KEY_MAX_CHARS + 1)
    with patch("api_routes.update_config_atomic") as mock_update:
        for body in (
            {"scope": "portfolio", "hidden_core_columns": too_long_list},
            {"scope": "portfolio", "shown_optional_columns": too_long_list},
            {"scope": "portfolio", "column_order": too_long_list},
            {"scope": "portfolio", "shown_optional_columns": [too_long_key]},
        ):
            assert client.post("/api/ui-preferences/columns", json=body).status_code == 422
    mock_update.assert_not_called()


@pytest.mark.api
def test_ui_preferences_views_reject_oversize_payloads(client):
    from constants import (
        TABLE_FILTER_CONDITIONS_MAX, TABLE_FILTER_VALUE_MAX_CHARS, TABLE_VIEW_NAME_MAX_CHARS, TABLE_VIEWS_MAX,
    )
    view = {"name": "V", "columns": ["ticker"]}
    condition = {"key": "price", "operator": "gt", "value": "1"}
    payloads = [
        [view] * (TABLE_VIEWS_MAX + 1),
        [{**view, "name": "n" * (TABLE_VIEW_NAME_MAX_CHARS + 1)}],
        [{**view, "columns": ["ticker"] * 201}],
        [{**view, "filter": {"logic": "AND", "conditions": [condition] * (TABLE_FILTER_CONDITIONS_MAX + 1)}}],
        [{**view, "filter": {"conditions": [{**condition, "value": "9" * (TABLE_FILTER_VALUE_MAX_CHARS + 1)}]}}],
    ]
    with patch("api_routes.update_config_atomic") as mock_update:
        for views in payloads:
            resp = client.post("/api/ui-preferences/views", json={"scope": "portfolio", "views": views})
            assert resp.status_code == 422
    mock_update.assert_not_called()


@pytest.mark.api
def test_ui_preferences_views_accept_the_cap(client):
    from constants import TABLE_VIEWS_MAX
    with patch("api_routes.update_config_atomic"):
        resp = client.post("/api/ui-preferences/views", json={
            "scope": "portfolio", "views": [{"name": f"V{i}", "columns": ["ticker"]} for i in range(TABLE_VIEWS_MAX)],
        })
    assert resp.status_code == 200


@pytest.mark.api
def test_post_ui_preferences_views_persists_watchlist_scope(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/ui-preferences/views", json={"scope": "watchlist", "views": []})
    assert resp.status_code == 200
    mock_update.assert_called_once_with({"UI_PREFERENCES": {"WATCHLIST_VIEWS": []}})


@pytest.mark.api
def test_post_ui_preferences_views_rejects_invalid_scope(client):
    resp = client.post("/api/ui-preferences/views", json={"scope": "bogus", "views": []})
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


@pytest.mark.api
def test_post_ui_preferences_views_persists_filter_conditions(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/ui-preferences/views", json={
            "scope": "portfolio",
            "views": [{
                "name": "Oversold",
                "columns": ["ticker", "price", "rsi"],
                "filter": {"logic": "AND", "conditions": [{"key": "rsi", "operator": "lt", "value": "30"}]},
            }],
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_update.assert_called_once_with({"UI_PREFERENCES": {
        "PORTFOLIO_VIEWS": [{
            "name": "Oversold",
            "columns": ["ticker", "price", "rsi"],
            "filter": {"logic": "AND", "conditions": [{"key": "rsi", "operator": "lt", "value": "30"}]},
        }],
    }})


@pytest.mark.api
def test_post_ui_preferences_views_persists_or_logic(client):
    """The Low Target / High Target 'has a target set' use case needs OR, not AND."""
    with patch("api_routes.update_config_atomic") as mock_update:
        resp = client.post("/api/ui-preferences/views", json={
            "scope": "watchlist",
            "views": [{
                "name": "Has Target Set",
                "columns": ["ticker", "low_target", "high_target"],
                "filter": {"logic": "OR", "conditions": [
                    {"key": "low_target", "operator": "not_empty"},
                    {"key": "high_target", "operator": "not_empty"},
                ]},
            }],
        })
    assert resp.status_code == 200
    saved_view = mock_update.call_args[0][0]["UI_PREFERENCES"]["WATCHLIST_VIEWS"][0]
    assert saved_view["filter"]["logic"] == "OR"
    assert len(saved_view["filter"]["conditions"]) == 2


@pytest.mark.api
def test_post_ui_preferences_views_defaults_filter_logic_to_and(client):
    with patch("api_routes.update_config_atomic") as mock_update:
        client.post("/api/ui-preferences/views", json={
            "scope": "portfolio",
            "views": [{"name": "V", "columns": ["ticker"], "filter": {"conditions": []}}],
        })
    saved_view = mock_update.call_args[0][0]["UI_PREFERENCES"]["PORTFOLIO_VIEWS"][0]
    assert saved_view["filter"]["logic"] == "AND"


@pytest.mark.api
def test_post_ui_preferences_views_rejects_filter_condition_missing_operator(client):
    resp = client.post("/api/ui-preferences/views", json={
        "scope": "portfolio",
        "views": [{"name": "Bad", "columns": ["ticker"], "filter": {"conditions": [{"key": "rsi"}]}}],
    })
    assert resp.status_code == 422


@pytest.mark.api
def test_post_ui_preferences_views_rejects_invalid_filter_logic(client):
    resp = client.post("/api/ui-preferences/views", json={
        "scope": "portfolio",
        "views": [{"name": "Bad", "columns": ["ticker"], "filter": {"logic": "XOR", "conditions": []}}],
    })
    assert resp.status_code == 422


@pytest.mark.api
def test_post_learn_session_with_study_all_includes_locked_levels(client):
    import database as _db
    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM learn_term_state")
        conn.commit()

        import glossary_learn_engine
        overview_result = glossary_learn_engine.overview()
        level1_total = next(
            lvl["total"] for lvl in overview_result["levels"] if lvl["section_id"] == "market-fundamentals"
        )
        assert level1_total < 30, "test assumes level 1 alone can't fill a size=30 session"

        gated_resp = client.post("/api/learn/session?size=30")
        assert len(gated_resp.json()["cards"]) == level1_total

        unlocked_resp = client.post("/api/learn/session?size=30&study_all=true")
        assert unlocked_resp.status_code == 200
        assert len(unlocked_resp.json()["cards"]) == 30
    finally:
        conn.close()


@pytest.mark.api
@pytest.mark.parametrize("scope", ["portfolio", "watchlist"])
def test_post_column_order_persists_normalized_order(client, scope):
    from table_columns_helpers import normalize_column_order
    with patch("api_routes.update_config_atomic") as update:
        response = client.post("/api/ui-preferences/columns", json={
            "scope": scope, "column_order": ["score", "price", "score", "unknown"]})
    assert response.status_code == 200
    assert update.call_args.args[0]["UI_PREFERENCES"][f"{scope.upper()}_COLUMN_ORDER"] == normalize_column_order(["score", "price"], scope)


@pytest.mark.api
@pytest.mark.parametrize("scope", ["portfolio", "watchlist"])
def test_post_view_preserves_order_visibility_and_filter(client, scope):
    from table_columns_helpers import normalize_column_order
    view = {"name": "Ordered", "columns": ["ticker", "score", "price"],
            "column_order": ["score", "price"],
            "filter": {"logic": "AND", "conditions": [{"key": "score", "operator": "gt", "value": "60"}]}}
    with patch("api_routes.update_config_atomic") as update:
        response = client.post("/api/ui-preferences/views", json={"scope": scope, "views": [view]})
    assert response.status_code == 200
    saved = update.call_args.args[0]["UI_PREFERENCES"][f"{scope.upper()}_VIEWS"][0]
    assert saved == dict(view, column_order=normalize_column_order(view["column_order"], scope))


@pytest.mark.api
@pytest.mark.parametrize("endpoint,payload", [
    ("columns", {"column_order": "price"}),
    ("views", {"views": [{"name": "Invalid", "columns": [], "column_order": [42]}]}),
])
def test_post_column_order_rejects_invalid_types(client, endpoint, payload):
    response = client.post(f"/api/ui-preferences/{endpoint}", json=dict(scope="portfolio", **payload))
    assert response.status_code == 422


@pytest.mark.api
@pytest.mark.parametrize("success", [True, False])
def test_fx_refresh_awaits_force_attempt_and_retains_last_good(client, monkeypatch, success):
    from db_helpers import get_cached_fx_quote, upsert_fx_quote
    from yahoo_engine import yahoo_engine

    upsert_fx_quote("CADGBP=X", 0.6, 1000)
    def refresh(pair, *, force):
        assert pair == "CADGBP=X" and force is True
        if success:
            upsert_fx_quote(pair, 0.65, 2000)
            return 0.65
        return None
    monkeypatch.setattr(yahoo_engine, "get_fx_rate", refresh)
    response = client.post("/api/fx/refresh", json={"currency": "CAD"})
    assert response.status_code == 200
    assert response.json()["status"] == ("success" if success else "error")
    assert get_cached_fx_quote("CADGBP=X")["rate"] == (0.65 if success else 0.6)
    assert response.json()["quotes"][0]["updated_at"] == (2000 if success else 1000)


@pytest.mark.api
@pytest.mark.parametrize("currency", ["../USD", "USDGBP=X", "usd", "", "USDD"])
def test_fx_refresh_rejects_invalid_currency(client, currency):
    assert client.post("/api/fx/refresh", json={"currency": currency}).status_code == 422


@pytest.mark.api
@pytest.mark.parametrize("currency", ["GBP", "GBp"])
def test_fx_refresh_base_currency_needs_no_network(client, monkeypatch, currency):
    from yahoo_engine import yahoo_engine

    monkeypatch.setattr(yahoo_engine, "get_fx_rate", lambda *args, **kwargs: pytest.fail("base currency must not fetch"))
    response = client.post("/api/fx/refresh", json={"currency": currency})
    assert response.json()["status"] == "success"
    assert response.json()["quotes"] == []


@pytest.mark.api
def test_post_watchlist_add_rejects_an_unsafe_ticker_without_a_yahoo_lookup(client):
    import database as _db
    with patch("api_routes.resolve_watchlist_metadata") as mock_meta:
        resp = client.post("/api/watchlist/add", json={"ticker": "../X"})
    assert resp.status_code == 422
    assert "ticker" in _json(resp)["message"]
    mock_meta.assert_not_called()
    assert "../X" not in _db.get_watchlist_tickers()


@pytest.mark.api
def test_post_intraday_monitor_add_rejects_an_unsafe_ticker(client):
    import database as _db
    resp = client.post("/api/intraday-monitor/add", json={"ticker": "../X"})
    assert resp.status_code == 422
    conn = _db.get_connection()
    try:
        assert conn.execute("SELECT 1 FROM intraday_monitors WHERE ticker = '../X'").fetchone() is None
    finally:
        conn.close()


@pytest.mark.api
def test_git_pull_failure_does_not_return_git_output(client, step_up_headers):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="oldsha\n", stderr="")
        return MagicMock(returncode=1, stdout="", stderr="error: Your local changes to /opt/secret/path would be overwritten")

    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        resp = client.post("/api/system/git-pull", headers=step_up_headers)
    assert resp.status_code == 500
    assert "/opt/secret/path" not in resp.text


@pytest.mark.api
def test_git_pull_success_does_not_return_git_output(client, step_up_headers):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "pull"]:
            return MagicMock(returncode=0, stdout="Updating abc..def\n secret_file.py | 2 +-\n", stderr="")
        return MagicMock(returncode=0, stdout="oldsha\n", stderr="")

    with patch("api_routes_system.subprocess.run", side_effect=fake_run):
        resp = client.post("/api/system/git-pull", headers=step_up_headers)
    assert resp.status_code == 200
    assert "secret_file.py" not in resp.text


@pytest.mark.api
def test_yahoo_ipv6_test_hides_unclassified_error_text(client):
    with patch("api_routes_system.cffi_requests.Session"), \
            patch("yahoo_engine.fetch_diagnostic_history", side_effect=RuntimeError("curl: (35) TLS handshake to 10.0.0.9 failed")):
        resp = client.post("/api/settings/test-yahoo-ipv6", json={"ipv6_address": "2001:db8::1"})
    assert resp.status_code == 502
    assert "10.0.0.9" not in resp.text and "curl" not in resp.text


@pytest.mark.api
@pytest.mark.parametrize("log_dir", ["/etc", "/var/log/app", "../outside", "logs/../../outside", "", "   "])
def test_post_settings_rejects_a_log_dir_outside_the_project(client, step_up_headers, log_dir):
    resp = client.post("/api/settings", json={"FILE_LOGGING": {"LOG_DIR": log_dir}}, headers=step_up_headers)
    assert resp.status_code == 422, f"{log_dir!r} → {resp.status_code}"


@pytest.mark.api
def test_post_settings_accepts_a_relative_log_dir(client, step_up_headers):
    import config as _config
    resp = client.post("/api/settings", json={"FILE_LOGGING": {"ENABLED": False, "LOG_DIR": "logs/archive"}},
                       headers=step_up_headers)
    assert resp.status_code == 200, resp.text
    assert _config.load_config()["FILE_LOGGING"]["LOG_DIR"] == "logs/archive"
    client.post("/api/settings", json={"FILE_LOGGING": {"LOG_DIR": "logs"}}, headers=step_up_headers)


@pytest.mark.api
@pytest.mark.parametrize("routing", [
    {"not_a_source": {"in_app": True}},
    {"crash_alert": True},
    {"crash_alert": ["in_app"]},
    {"crash_alert": {"in_app": "yes"}},
    {"crash_alert": {"in_app": 1}},
    {"crash_alert": {"carrier_pigeon": True}},
    ["crash_alert"],
])
def test_post_settings_rejects_a_malformed_notification_routing(client, step_up_headers, routing):
    resp = client.post("/api/settings", json={"NOTIFICATION_ROUTING": routing}, headers=step_up_headers)
    assert resp.status_code == 422, f"{routing!r} → {resp.status_code}"


@pytest.mark.api
@pytest.mark.parametrize("targets", [
    {"unknown_section": {}},
    {"sector_targets": ["Technology"]},
    {"sector_targets": {"Technology": 35}},
    {"sector_targets": {"Technology": {"max": "35"}}},
    {"sector_targets": {"Technology": {"max": True}}},
    {"sector_targets": {"Technology": {"max": 150}}},
    {"sector_targets": {"Technology": {"max": -1}}},
    {"sector_targets": {"Technology": {"limit": 10}}},
    {"sector_targets": {"": {"max": 10}}},
    {"sector_targets": {"x" * 65: {"max": 10}}},
    {"concentration_targets": {"hhi_max": 2}},
    {"concentration_targets": {"max_single_position_pct": 101}},
    {"concentration_targets": {"made_up_limit": 5}},
    {"risk_metric_targets": {"sharpe_ratio_min": "high"}},
    "all",
])
def test_post_settings_rejects_malformed_xray_targets(client, step_up_headers, targets):
    resp = client.post("/api/settings", json={"XRAY_TARGETS": targets}, headers=step_up_headers)
    assert resp.status_code == 422, f"{targets!r} → {resp.status_code}"


@pytest.mark.api
def test_post_settings_accepts_a_full_ui_shaped_xray_targets_payload(client, step_up_headers):
    import config as _config
    payload = {"XRAY_TARGETS": {
        "market_development": {"Developed Markets": {"min": 80, "max": 95}},
        "country_concentration": {"United States": {"min": None, "max": 70}},
        "sector_targets": {"Technology": {"min": None, "max": 35}},
        "concentration_targets": {"max_single_position_pct": 15, "hhi_max": 0.15},
        "risk_metric_targets": {"portfolio_beta_min": 0.6, "sharpe_ratio_min": 0.5},
        "income_targets": {"dividend_yield_min_pct": None},
    }}
    resp = client.post("/api/settings", json=payload, headers=step_up_headers)
    assert resp.status_code == 200, resp.text
    assert _config.load_config()["XRAY_TARGETS"]["country_concentration"]["United States"]["max"] == 70


@pytest.mark.api
@pytest.mark.parametrize("scoring", [
    {"UNKNOWN": {}},
    {"REGIME_WEIGHTS": ["Bull"]},
    {"REGIME_WEIGHTS": {"Bear": {"composite_score": 0.5}}},
    {"REGIME_WEIGHTS": {"Bull": {"composite_score": 1.5}}},
    {"REGIME_WEIGHTS": {"Bull": {"composite_score": -0.1}}},
    {"REGIME_WEIGHTS": {"Bull": {"sentiment": 0.5}}},
    {"REGIME_WEIGHTS": {"Bull": {"composite_score": "0.5"}}},
    {"REGIME_WEIGHTS": {"Bull": [0.4, 0.3]}},
    {"CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 2}},
    {"CRASH_VETO": {"MARKET_STRESS_THRESHOLD": None}},
    {"CRASH_VETO": 0.75},
])
def test_post_settings_rejects_malformed_meta_scoring(client, step_up_headers, scoring):
    resp = client.post("/api/settings", json={"META_SCORING": scoring}, headers=step_up_headers)
    assert resp.status_code == 422, f"{scoring!r} → {resp.status_code}"
