import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

import joblib
import time_engine
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from api_deps import limiter, require_confirm_token, _error_500

from config import (
    BASE_DIR, DATA_DIR, DB_PATH, FUNDAMENTALS_DIR, HISTORICAL_DIR,
    INTRADAY_DIR, PORTFOLIO_PATH, WATCHLIST_PATH, load_config,
    update_config_atomic,
)
from database import get_connection, get_ticker_registry, get_ticker_registry_row, upsert_ticker_registry_row, soft_delete_ticker_registry_row
from ghostfolio_sync import purge_ghostfolio_files
from log_config import configure_file_logging as _configure_file_logging
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse, is_exchange_open, proxy_tickers_needing_refresh, registry_tickers_needing_refresh, reload_ticker_registry
import markets_engine
from notification_engine import notify
from scheduler_engine import (
    build_workflow_graph, detect_workflow_conflicts, get_all_job_last_runs,
    reload_scheduler, run_xray_risk_cache_job, CONFIG_KEY_TO_JOB,
)
from sentiment_engine import run_nextcloud_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from tools.network_engine import GLOBAL_IPV6_STATUS
from yahoo_engine import yahoo_engine
from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

system_router = APIRouter()


# --- SHARED PYDANTIC SCHEMAS (also used by other sub-routers / main api_routes.py) ---

class PulseRequest(BaseModel):
    tickers: Optional[List[str]] = []

class TickerRegistryBody(BaseModel):
    ticker: Optional[str] = None
    display_name: str
    region: str
    asset_type: str
    exchange: Optional[str] = None
    currency: str = "USD"
    future_ticker: Optional[str] = None
    future_display_name: Optional[str] = None
    invert_color: bool = False
    is_pulse_tile: bool = False
    pulse_sort_order: int = 0
    is_pulse_mobile: bool = True
    sort_order: int = 0
    context_blurb: Optional[str] = None

class IPv6TestRequest(BaseModel):
    ipv6_address: str

class GhostfolioAccountsConfig(BaseModel):
    discovered: Optional[List[Any]] = None
    active: Optional[List[str]] = None

class UIPreferencesConfig(BaseModel):
    LIVE_PORTFOLIO: Optional[bool] = None
    LIVE_WATCHLIST: Optional[bool] = None
    LIVE_DETAILS: Optional[bool] = None
    REFRESH_RATE: Optional[int] = None
    FREETRADE_ONLY_MODE: Optional[bool] = None
    MARKET_PULSE_DYNAMIC: Optional[bool] = None
    MARKET_PULSE_DESKTOP_COUNT: Optional[int] = None
    MARKET_PULSE_MOBILE_COUNT: Optional[int] = None
    PORTFOLIO_HIDDEN_CORE_COLUMNS: Optional[List[str]] = None
    PORTFOLIO_SHOWN_OPTIONAL_COLUMNS: Optional[List[str]] = None
    WATCHLIST_HIDDEN_CORE_COLUMNS: Optional[List[str]] = None
    WATCHLIST_SHOWN_OPTIONAL_COLUMNS: Optional[List[str]] = None
    PORTFOLIO_VIEWS: Optional[List[Any]] = None
    WATCHLIST_VIEWS: Optional[List[Any]] = None
    FONT_SIZE_NAV: Optional[int] = None
    FONT_SIZE_TABLE: Optional[int] = None
    FONT_SIZE_DT_TABLE: Optional[int] = None
    FONT_SIZE_FORM: Optional[int] = None
    FONT_SIZE_BTN: Optional[int] = None
    FONT_SIZE_SECTION: Optional[int] = None
    FONT_SIZE_BODY: Optional[int] = None
    FONT_SIZE_H1: Optional[int] = None
    FONT_SIZE_H2: Optional[int] = None
    FONT_SIZE_H3: Optional[int] = None

class PositionSizingConfig(BaseModel):
    ACCOUNT_VALUE: Optional[float] = None
    RISK_PCT: Optional[float] = None
    STOP_MULTIPLE: Optional[float] = None

class ScheduleItemConfig(BaseModel):
    ENABLED: Optional[bool] = None
    DAYS: Optional[List[str]] = None
    TIME: Optional[str] = None
    INDICES: Optional[List[str]] = None
    BATCH_SIZE: Optional[int] = None
    FREQUENCY: Optional[str] = None
    INTERVAL_HOURS: Optional[int] = None
    START_TIME: Optional[str] = None
    END_TIME: Optional[str] = None
    INTERVAL_MINUTES: Optional[int] = None
    FLASH_CRASH_THRESHOLD: Optional[float] = None
    INITIALIZED: Optional[bool] = None
    CALENDAR_TIME: Optional[str] = None
    DATA_DAY: Optional[str] = None
    DATA_TIME: Optional[str] = None
    DAY_OF_WEEK: Optional[str] = None
    DAYS_TO_KEEP_FILES: Optional[int] = None
    MAX_PER_TICKER: Optional[int] = None
    MAX_AGE_DAYS: Optional[int] = None
    PRE_US_OPEN_TIME: Optional[str] = None
    POST_US_CLOSE_TIME: Optional[str] = None
    AM_TIME: Optional[str] = None
    PM_TIME: Optional[str] = None
    BULL_TRAP: Optional[bool] = None
    BEAR_TRAP: Optional[bool] = None
    CAPITULATION: Optional[bool] = None
    WYCKOFF: Optional[bool] = None
    MONITOR_PORTFOLIO: Optional[bool] = None
    MONITOR_WATCHLIST: Optional[bool] = None
    WATCH_THRESHOLD: Optional[int] = None
    FLAG_THRESHOLD: Optional[int] = None
    LOCATION: Optional[str] = None
    LOCAL_PATH: Optional[str] = None
    NFS_SERVER: Optional[str] = None
    NFS_PATH: Optional[str] = None
    INCLUDE_DATA: Optional[bool] = None
    INCLUDE_MODELS: Optional[bool] = None
    INCLUDE_DATABASE: Optional[bool] = None
    RETENTION_COUNT: Optional[int] = None
    MODE: Optional[str] = None
    VETO_THRESHOLD: Optional[float] = None
    MIN_TRAINING_SAMPLES: Optional[int] = None
    CORRELATION_THRESHOLD: Optional[float] = None
    ZSCORE_THRESHOLD: Optional[float] = None
    REGULAR_ENABLED: Optional[bool] = None
    INVERSE_ENABLED: Optional[bool] = None

class PatternFamilyScheduleConfig(BaseModel):
    REGULAR_ENABLED: Optional[bool] = None
    INVERSE_ENABLED: Optional[bool] = None
    TOP_ENABLED: Optional[bool] = None
    BOTTOM_ENABLED: Optional[bool] = None
    BULL_ENABLED: Optional[bool] = None
    BEAR_ENABLED: Optional[bool] = None
    ASCENDING_ENABLED: Optional[bool] = None
    DESCENDING_ENABLED: Optional[bool] = None
    RISING_ENABLED: Optional[bool] = None
    FALLING_ENABLED: Optional[bool] = None
    NR4_ENABLED: Optional[bool] = None
    NR7_ENABLED: Optional[bool] = None
    BULLISH_ENABLED: Optional[bool] = None
    BEARISH_ENABLED: Optional[bool] = None
    OVERBOUGHT_ENABLED: Optional[bool] = None
    OVERSOLD_ENABLED: Optional[bool] = None
    ENGULFING_ENABLED: Optional[bool] = None
    PIN_BAR_ENABLED: Optional[bool] = None

class PatternDetectionScheduleConfig(BaseModel):
    ENABLED: Optional[bool] = None
    MONITOR_PORTFOLIO: Optional[bool] = None
    MONITOR_WATCHLIST: Optional[bool] = None
    DAYS: Optional[List[str]] = None
    TIME: Optional[str] = None
    HEAD_SHOULDERS: Optional[PatternFamilyScheduleConfig] = None
    DOUBLE_TOP_BOTTOM: Optional[PatternFamilyScheduleConfig] = None
    FLAG: Optional[PatternFamilyScheduleConfig] = None
    TRIANGLE: Optional[PatternFamilyScheduleConfig] = None
    WEDGE: Optional[PatternFamilyScheduleConfig] = None
    PENNANT: Optional[PatternFamilyScheduleConfig] = None
    VOLATILITY_SQUEEZE: Optional[PatternFamilyScheduleConfig] = None
    NARROW_RANGE: Optional[PatternFamilyScheduleConfig] = None
    PARABOLIC_STRETCH: Optional[PatternFamilyScheduleConfig] = None
    MOMENTUM_DIVERGENCE: Optional[PatternFamilyScheduleConfig] = None
    CANDLESTICK_TRIGGER: Optional[PatternFamilyScheduleConfig] = None

class RiskOrchestratorWeightsConfig(BaseModel):
    VAR: Optional[float] = None
    CORRELATION: Optional[float] = None
    DRAWDOWN: Optional[float] = None

class RiskOrchestratorThresholdsConfig(BaseModel):
    PHI_YELLOW: Optional[float] = None
    PHI_RED: Optional[float] = None
    VAR_PCT_YELLOW: Optional[float] = None
    VAR_PCT_RED: Optional[float] = None
    MAX_CORR_YELLOW: Optional[float] = None
    MAX_CORR_RED: Optional[float] = None
    DRAWDOWN_PCT_YELLOW: Optional[float] = None
    DRAWDOWN_PCT_RED: Optional[float] = None

class RiskOrchestratorScheduleConfig(BaseModel):
    ENABLED: Optional[bool] = None
    DAYS: Optional[List[str]] = None
    TIME: Optional[str] = None
    WEIGHTS: Optional[RiskOrchestratorWeightsConfig] = None
    THRESHOLDS: Optional[RiskOrchestratorThresholdsConfig] = None

class SchedulingConfig(BaseModel):
    SYNC_INDICES: Optional[ScheduleItemConfig] = None
    PROFILER_ENGINE: Optional[ScheduleItemConfig] = None
    UNIVERSE_DEEP_SYNC: Optional[ScheduleItemConfig] = None
    GHOSTFOLIO_SYNC: Optional[ScheduleItemConfig] = None
    QUANT_ANALYSIS: Optional[ScheduleItemConfig] = None
    SENTIMENT_ENGINE: Optional[ScheduleItemConfig] = None
    CRASH_ALERTS: Optional[ScheduleItemConfig] = None
    MOONSHOT_ALERTS: Optional[ScheduleItemConfig] = None
    MAINTENANCE: Optional[ScheduleItemConfig] = None
    ACCOUNT_VALUE_SNAPSHOT: Optional[ScheduleItemConfig] = None
    FREETRADE_SYNC: Optional[ScheduleItemConfig] = None
    MACRO_ENGINE: Optional[ScheduleItemConfig] = None
    ML_BACKFILL: Optional[ScheduleItemConfig] = None
    ML_TRAINING: Optional[ScheduleItemConfig] = None
    ML_INFERENCE: Optional[ScheduleItemConfig] = None
    QUANT_ENGINE: Optional[ScheduleItemConfig] = None
    EARNINGS_ENGINE: Optional[ScheduleItemConfig] = None
    UNIVERSE_ENGINE: Optional[ScheduleItemConfig] = None
    CB_NLP_ALERT: Optional[ScheduleItemConfig] = None
    AI_CONTAGION: Optional[ScheduleItemConfig] = None
    NEWS_FEED: Optional[ScheduleItemConfig] = None
    TRAP_MONITORS: Optional[ScheduleItemConfig] = None
    BUBBLE_RADAR: Optional[ScheduleItemConfig] = None
    PAIRS_SPREAD_MONITOR: Optional[ScheduleItemConfig] = None
    PATTERN_DETECTION: Optional[PatternDetectionScheduleConfig] = None
    FORENSIC_QUARTERLY_FETCH: Optional[ScheduleItemConfig] = None
    FORENSIC_SCORES: Optional[ScheduleItemConfig] = None
    MACRO_AUCTIONS: Optional[ScheduleItemConfig] = None
    BACKUP: Optional[ScheduleItemConfig] = None
    ALERT_REFEREE_TRAINING: Optional[ScheduleItemConfig] = None
    RISK_ORCHESTRATOR: Optional[RiskOrchestratorScheduleConfig] = None
    RISK_ORCHESTRATOR_DIGEST: Optional[ScheduleItemConfig] = None

class ReportsDefaultsConfig(BaseModel):
    MR_MAX_RSI: Optional[int] = None
    DIV_MIN_YIELD: Optional[float] = None
    DIV_MIN_SCORE: Optional[int] = None

class NotificationItemConfig(BaseModel):
    ENABLED: Optional[bool] = None
    TIME: Optional[str] = None
    FREQUENCY: Optional[str] = None
    DAYS_AHEAD: Optional[int] = None
    ALERT_TYPE: Optional[str] = None
    ENABLED_PORTFOLIO: Optional[bool] = None
    ENABLED_WATCHLIST: Optional[bool] = None
    MIN_VALUE: Optional[float] = None
    DAYS_BACK: Optional[int] = None
    DROP_PERCENT: Optional[float] = None
    DROP_DAYS: Optional[int] = None
    SMA_LENGTH: Optional[int] = None
    SMA_GAP_PERCENT: Optional[float] = None
    SPIKE_PERCENT: Optional[float] = None
    SPIKE_DAYS: Optional[int] = None
    THRESHOLD: Optional[float] = None
    COOLDOWN_MINUTES: Optional[float] = None
    RETRIGGER_PERCENT: Optional[float] = None
    REARM_PERCENT: Optional[float] = None
    MAX_ALERTS_PER_DAY: Optional[int] = None
    LEADER_THRESHOLD_PCT: Optional[float] = None
    ETF_CONFIRMATION_THRESHOLD_PCT: Optional[float] = None
    VOLUME_SPIKE_MULTIPLIER: Optional[float] = None
    BELLWETHER_TICKERS: Optional[List[str]] = None
    ETF_BASKET: Optional[List[str]] = None
    PROXY_TICKERS: Optional[List[str]] = None
    PRIOR_TREND_MIN_PCT: Optional[float] = None
    VOLUME_CONFIRM_MULTIPLIER: Optional[float] = None

class PatternFamilyAlertConfig(BaseModel):
    PRIOR_TREND_MIN_PCT: Optional[float] = None
    VOLUME_CONFIRM_MULTIPLIER: Optional[float] = None
    BALANCE_TOLERANCE_PCT: Optional[float] = None
    MIN_SEPARATION_PCT: Optional[float] = None
    SIGMA_MULTIPLIER: Optional[float] = None
    FLAGPOLE_LOOKBACK_DAYS: Optional[int] = None
    SIGMA_WINDOW_DAYS: Optional[int] = None
    MIN_CONSOLIDATION_DAYS: Optional[int] = None
    MAX_CONSOLIDATION_DAYS: Optional[int] = None
    MAX_CHANNEL_SLOPE_PCT: Optional[float] = None
    PARALLEL_TOLERANCE_PCT: Optional[float] = None
    WINDOW_DAYS: Optional[int] = None
    FLAT_SLOPE_EPSILON_PCT: Optional[float] = None
    MIN_SLOPE_PCT: Optional[float] = None
    MIN_CONVERGENCE_DIFF_PCT: Optional[float] = None
    NUM_STD: Optional[float] = None
    KC_MULTIPLIER: Optional[float] = None
    MIN_SQUEEZE_DAYS: Optional[int] = None
    BREAKOUT_LOOKAHEAD_DAYS: Optional[int] = None
    SMA_WINDOW: Optional[int] = None
    Z_WINDOW_DAYS: Optional[int] = None
    Z_THRESHOLD: Optional[float] = None
    CONFIRM_Z_THRESHOLD: Optional[float] = None
    MIN_PRICE_CHANGE_PCT: Optional[float] = None
    MIN_RSI_GAP: Optional[float] = None
    RSI_OVERSOLD: Optional[float] = None
    RSI_OVERBOUGHT: Optional[float] = None
    BB_WINDOW_DAYS: Optional[int] = None
    BB_NUM_STD: Optional[float] = None
    WICK_MULTIPLIER: Optional[float] = None
    OPPOSITE_WICK_MAX_PCT: Optional[float] = None

class PatternDetectionAlertConfig(BaseModel):
    COOLDOWN_MINUTES: Optional[float] = None
    RETRIGGER_PERCENT: Optional[float] = None
    REARM_PERCENT: Optional[float] = None
    HEAD_SHOULDERS: Optional[PatternFamilyAlertConfig] = None
    DOUBLE_TOP_BOTTOM: Optional[PatternFamilyAlertConfig] = None
    FLAG: Optional[PatternFamilyAlertConfig] = None
    TRIANGLE: Optional[PatternFamilyAlertConfig] = None
    WEDGE: Optional[PatternFamilyAlertConfig] = None
    PENNANT: Optional[PatternFamilyAlertConfig] = None
    VOLATILITY_SQUEEZE: Optional[PatternFamilyAlertConfig] = None
    NARROW_RANGE: Optional[PatternFamilyAlertConfig] = None
    PARABOLIC_STRETCH: Optional[PatternFamilyAlertConfig] = None
    MOMENTUM_DIVERGENCE: Optional[PatternFamilyAlertConfig] = None
    CANDLESTICK_TRIGGER: Optional[PatternFamilyAlertConfig] = None

class NotificationsConfig(BaseModel):
    MARKET_SENTIMENT: Optional[NotificationItemConfig] = None
    EARNINGS_ALERTS: Optional[NotificationItemConfig] = None
    INSIDER_TRADING: Optional[NotificationItemConfig] = None
    CRASH_ALERTS: Optional[NotificationItemConfig] = None
    MOONSHOT_ALERTS: Optional[NotificationItemConfig] = None
    MACRO_ALERTS: Optional[NotificationItemConfig] = None
    ANOMALY_ALERTS: Optional[NotificationItemConfig] = None
    RSS_FEED: Optional[NotificationItemConfig] = None
    AI_CONTAGION: Optional[NotificationItemConfig] = None
    TRAP_MONITOR_ALERTS: Optional[NotificationItemConfig] = None
    PAIRS_SPREAD_MONITOR_ALERTS: Optional[NotificationItemConfig] = None
    PATTERN_DETECTION_ALERTS: Optional[PatternDetectionAlertConfig] = None
    RISK_ORCHESTRATOR_ALERTS: Optional[NotificationItemConfig] = None

class FreetradeMappingsConfig(BaseModel):
    US_MICS: Optional[List[str]] = None
    EXCHANGES: Optional[dict] = None

class FileLoggingConfig(BaseModel):
    ENABLED: Optional[bool] = None
    LEVEL: Optional[str] = None
    DAYS_TO_KEEP: Optional[int] = None
    ARCHIVE: Optional[bool] = None
    LOG_DIR: Optional[str] = None

class SettingsConfig(BaseModel):
    model_config = {"extra": "forbid"}

    SERVER_URL: Optional[str] = None
    YAHOO_IPV6_ADDRESS: Optional[str] = None
    YAHOO_USE_IPV4: Optional[bool] = None
    YAHOO_USE_IPV6: Optional[bool] = None
    PORT: Optional[int] = None
    BASE_CURRENCY: Optional[str] = None
    USER_TIMEZONE: Optional[str] = None
    HOME_EXCHANGE: Optional[str] = None
    IGNORED_TICKERS: Optional[List[str]] = None
    ACCOUNT_CURRENCIES: Optional[List[str]] = None
    GHOSTFOLIO_ENABLED: Optional[bool] = None
    GHOSTFOLIO_ACCOUNTS: Optional[GhostfolioAccountsConfig] = None
    UI_PREFERENCES: Optional[UIPreferencesConfig] = None
    POSITION_SIZING: Optional[PositionSizingConfig] = None
    FREETRADE_MAPPINGS: Optional[FreetradeMappingsConfig] = None
    SCHEDULING: Optional[SchedulingConfig] = None
    REPORTS_DEFAULTS: Optional[ReportsDefaultsConfig] = None
    NOTIFICATIONS: Optional[NotificationsConfig] = None
    NOTIFICATION_ROUTING: Optional[dict] = None
    XRAY_TARGETS: Optional[dict] = None
    FILE_LOGGING: Optional[FileLoggingConfig] = None


_requirements_changed_pending = False


async def execute_restart():
    global _requirements_changed_pending
    if _requirements_changed_pending:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(BASE_DIR / "requirements.txt")],
                capture_output=True, text=True, timeout=300, cwd=str(BASE_DIR),
            )
            if result.returncode == 0:
                notify("system_update_status", "Success", "requirements.txt changed on the last pull — dependencies were reinstalled before restart.")
            else:
                notify("system_update_status", "Error", f"pip install failed before restart:\n{result.stderr[-2000:]}", level="error")
        except Exception as e:
            logger.error("pip install before restart failed: %s", e)
            notify("system_update_status", "Error", f"pip install before restart failed: {e}", level="error")
        _requirements_changed_pending = False
    await asyncio.sleep(2)
    os.kill(os.getpid(), signal.SIGTERM)


@system_router.post("/market-pulse")
async def api_market_pulse(request: PulseRequest, background_tasks: BackgroundTasks):
    config_data = load_config()
    refresh_rate = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    pulse_data = get_cached_pulse_from_db(request.tickers, refresh_rate)
    all_items = pulse_data['indexes'] + pulse_data['assets']
    needs_fetch = [item['ticker'] for item in all_items if item.pop('needs_refresh', False)]
    if needs_fetch:
        background_tasks.add_task(fetch_and_save_pulse, needs_fetch)
    return JSONResponse(content={"status": "success", "data": pulse_data})

@system_router.get("/market-pulse")
async def api_market_pulse_get(background_tasks: BackgroundTasks):
    config_data = load_config()
    refresh_rate = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    pulse_data = get_cached_pulse_from_db([], refresh_rate)
    needs_fetch = [item['ticker'] for item in pulse_data['indexes'] if item.pop('needs_refresh', False)]
    if needs_fetch:
        background_tasks.add_task(fetch_and_save_pulse, needs_fetch)
    return JSONResponse(content={"status": "success", "data": pulse_data.get("indexes", [])})


@system_router.get("/markets")
async def api_markets(background_tasks: BackgroundTasks, view: str = "dynamic"):
    payload = markets_engine.assemble_markets_payload(view)
    needs_fetch = {
        tile["ticker"]
        for region in payload["regions"]
        for tile in region["tiles"]
        if tile.pop("needs_refresh", False)
    }
    # A tile's own price can be fresh (needs_refresh above) while the open/closed proxy state
    # for its exchange is separately stale — e.g. a region with no held tile ticker on this page
    # view, or a proxy whose market_state hasn't been touched since that exchange was last open.
    # Without this, a region can report "open" indefinitely once no page traffic refreshes its
    # proxy's market_state (see GET /api/system/market-status's identical self-heal).
    needs_fetch.update(proxy_tickers_needing_refresh())
    if needs_fetch:
        background_tasks.add_task(fetch_and_save_pulse, list(needs_fetch))
    return JSONResponse(content={"status": "success", "data": payload})


@system_router.get("/system/market-status/all")
async def api_market_status_all(background_tasks: BackgroundTasks):
    """Net-new, generalized N-exchange market status — does NOT change the existing
    /system/market-status response (that contract is consumed as-is by the Home Assistant
    integration; see AGENTS.md rule on additive-only changes to that endpoint)."""
    stale_proxies = proxy_tickers_needing_refresh()
    if stale_proxies:
        background_tasks.add_task(fetch_and_save_pulse, stale_proxies)
    exchanges = sorted({r["exchange"] for r in get_ticker_registry(enabled_only=True) if r["exchange"]})
    return JSONResponse(content={
        "status": "success",
        "exchanges": {ex: is_exchange_open(ex) for ex in exchanges},
        "regions": {
            region: markets_engine.get_region_state(region)["state"]
            for region in ("US", "Europe", "Asia")
        },
    })


@system_router.get("/markets/registry")
async def api_markets_registry_list():
    return JSONResponse(content={"status": "success", "registry": get_ticker_registry(enabled_only=False)})


def _invalid_exchange_error(exchange: Optional[str]):
    """A non-empty exchange that isn't a recognized time_engine.EXCHANGE_HOURS key silently
    falls back to "always open" (see markets_engine.assemble_markets_payload) instead of a real
    open/closed check — found 2026-07-09 on a manually-added ^KS200 row. Reject it loudly here
    rather than let a typo or wrong case (e.g. "krx" vs "KRX") pass through unnoticed."""
    if not exchange:
        return None
    if exchange in time_engine.EXCHANGE_HOURS:
        return None
    valid = ", ".join(sorted(time_engine.EXCHANGE_HOURS.keys()))
    return JSONResponse(status_code=422, content={
        "status": "error",
        "message": f"Unknown exchange '{exchange}' (case-sensitive). Leave blank for no open/closed status, or use one of: {valid}",
    })


@system_router.post("/markets/registry")
async def api_markets_registry_create(body: TickerRegistryBody):
    ticker = (body.ticker or "").strip()
    if not ticker:
        return JSONResponse(status_code=422, content={"status": "error", "message": "ticker is required."})
    if get_ticker_registry_row(ticker) is not None:
        return JSONResponse(status_code=409, content={"status": "error", "message": f"{ticker} already exists in the registry."})
    exchange_error = _invalid_exchange_error(body.exchange)
    if exchange_error:
        return exchange_error
    ok = upsert_ticker_registry_row(ticker=ticker, **body.model_dump(exclude={"ticker"}))
    if not ok:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save ticker."})
    reload_ticker_registry()
    return JSONResponse(content={"status": "success", "message": f"{ticker} added to the Markets registry."})


@system_router.put("/markets/registry/{ticker}")
async def api_markets_registry_update(ticker: str, body: TickerRegistryBody):
    if get_ticker_registry_row(ticker) is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": f"{ticker} not found in the registry."})
    exchange_error = _invalid_exchange_error(body.exchange)
    if exchange_error:
        return exchange_error
    ok = upsert_ticker_registry_row(ticker=ticker, **body.model_dump(exclude={"ticker"}))
    if not ok:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to update ticker."})
    reload_ticker_registry()
    return JSONResponse(content={"status": "success", "message": f"{ticker} updated."})


@system_router.delete("/markets/registry/{ticker}")
async def api_markets_registry_delete(ticker: str):
    if get_ticker_registry_row(ticker) is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": f"{ticker} not found in the registry."})
    soft_delete_ticker_registry_row(ticker)
    reload_ticker_registry()
    return JSONResponse(content={"status": "success", "message": f"{ticker} removed from the Markets registry."})

@system_router.post("/test-sentiment-alert")
async def test_sentiment_alert():
    success, msg = await asyncio.to_thread(run_nextcloud_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@system_router.post("/test-earnings-alert")
async def test_earnings_alert():
    from scheduler_engine import record_job_run
    success, msg = await asyncio.to_thread(run_earnings_alert)
    record_job_run('earnings_alert_job')
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@system_router.post("/test-insider-alert")
async def test_insider_alert():
    success, msg = await asyncio.to_thread(run_insider_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})


@system_router.post("/settings/test-yahoo-ipv6")
def test_yahoo_ipv6(request: IPv6TestRequest):
    """
    Diagnostic endpoint to safely verify an IPv6 socket binding.
    Executes a low-latency price fetch against Yahoo Finance edge nodes using curl_cffi.
    """
    ipv6_addr = request.ipv6_address.strip()
    if not ipv6_addr:
        return JSONResponse(status_code=400, content={"status": "error", "message": "IPv6 address cannot be empty."})

    test_session = cffi_requests.Session(impersonate="chrome", interface=ipv6_addr)

    try:
        logger.info(f"Executing diagnostic IPv6 socket bind test for {ipv6_addr}...")

        original_request = test_session.request
        def timeout_request(*args, **kwargs):
            kwargs.setdefault('timeout', 10)
            return original_request(*args, **kwargs)
        test_session.request = timeout_request

        from yahoo_engine import fetch_diagnostic_history
        df = fetch_diagnostic_history(test_session)

        if not df.empty:
            logger.info(f"IPv6 Diagnostic Success: Received data payload via {ipv6_addr}.")
            return JSONResponse(content={
                "status": "success",
                "message": f"Successfully verified stable IPv6 socket connection to Yahoo Finance edge nodes via {ipv6_addr}."
            })
        else:
            logger.warning(f"IPv6 Diagnostic Warning: Connection succeeded but payload was empty.")
            return JSONResponse(status_code=500, content={
                "status": "error",
                "message": "Connection established, but Yahoo Finance returned empty data. The API endpoint may be restricting responses."
            })

    except Exception as e:
        error_str = str(e)
        logger.error(f"IPv6 Diagnostic Exception: {error_str}")

        if "Couldn't bind" in error_str or "bind failed" in error_str.lower() or "assign requested address" in error_str.lower():
            msg = f"Socket binding failed. The address '{ipv6_addr}' is not assigned to any physical or virtual local network interface on this server."
        elif "Network is unreachable" in error_str or "unreachable" in error_str.lower():
            msg = "Network unreachable. The socket bound successfully, but your server lacks an active IPv6 upstream internet gateway."
        elif "Timeout" in error_str or "timeout" in error_str.lower():
            return JSONResponse(status_code=504, content={"status": "error", "message": "Connection timed out. The IPv6 address may be unroutable, blocked by your firewall, or lacks internet access."})
        else:
            msg = f"Connection refused or failed during socket negotiation: {error_str}"

        return JSONResponse(status_code=502, content={"status": "error", "message": msg})

    finally:
        test_session.close()


@system_router.get("/ui-theme.css", response_class=PlainTextResponse)
async def ui_theme_css():
    ui = load_config().get("UI_PREFERENCES", {})
    props = " ".join([
        f"--font-size-nav: {ui.get('FONT_SIZE_NAV', 12)}px;",
        f"--font-size-table: {ui.get('FONT_SIZE_TABLE', 12)}px;",
        f"--font-size-dt-table: {ui.get('FONT_SIZE_DT_TABLE', 12)}px;",
        f"--font-size-form: {ui.get('FONT_SIZE_FORM', 12)}px;",
        f"--font-size-btn: {ui.get('FONT_SIZE_BTN', 12)}px;",
        f"--font-size-section: {ui.get('FONT_SIZE_SECTION', 13)}px;",
        f"--font-size-body: {ui.get('FONT_SIZE_BODY', 12)}px;",
        f"--font-size-h1: {ui.get('FONT_SIZE_H1', 17)}px;",
        f"--font-size-h2: {ui.get('FONT_SIZE_H2', 14)}px;",
        f"--font-size-h3: {ui.get('FONT_SIZE_H3', 12)}px;",
    ])
    return PlainTextResponse(f":root {{{props}}}", media_type="text/css")


@system_router.get("/settings/network-status")
async def get_network_status():
    """Returns the current active route and health status for Yahoo Finance connections."""
    config_data = load_config()
    ipv6_addr = config_data.get("YAHOO_IPV6_ADDRESS", "").strip()
    use_ipv4 = config_data.get("YAHOO_USE_IPV4", True)
    use_ipv6 = config_data.get("YAHOO_USE_IPV6", bool(ipv6_addr)) and bool(ipv6_addr)

    if use_ipv4 and use_ipv6:
        routing_mode = "Dual (Round-robin)"
    elif use_ipv6:
        routing_mode = "IPv6 Only"
    else:
        routing_mode = "IPv4 Only"

    if not ipv6_addr or not use_ipv6:
        return JSONResponse(content={
            "status": "success",
            "route": "IPv4 Only",
            "routing_mode": routing_mode,
            "indicator": "green",
            "message": "Using standard IPv4 routing. No IPv6 address is configured or IPv6 is disabled."
        })

    if GLOBAL_IPV6_STATUS["is_failing"]:
        fail_time_str = time_engine.fmt_datetime(datetime.fromtimestamp(GLOBAL_IPV6_STATUS["last_fail_time"], tz=timezone.utc))
        return JSONResponse(content={
            "status": "warning",
            "route": "IPv4 (Failover Rescue Active)",
            "routing_mode": routing_mode,
            "indicator": "yellow",
            "message": "IPv6 routing failed at %s. Traffic is actively being rescued via IPv4 fallback. Last Error: %s" % (fail_time_str, GLOBAL_IPV6_STATUS["last_error"])
        })

    if use_ipv4 and use_ipv6:
        return JSONResponse(content={
            "status": "success",
            "route": "Dual (Round-robin)",
            "routing_mode": routing_mode,
            "indicator": "green",
            "message": "Round-robin load balancing between IPv4 and IPv6 (%s)." % ipv6_addr
        })

    return JSONResponse(content={
        "status": "success",
        "route": "IPv6 Only",
        "routing_mode": routing_mode,
        "indicator": "green",
        "message": "Routing all Yahoo Finance traffic exclusively through IPv6 (%s)." % ipv6_addr
    })


@system_router.get("/system/yahoo-api-stats")
async def get_yahoo_api_stats_endpoint():
    from database import get_yahoo_api_stats
    rows = get_yahoo_api_stats(days=8)
    return JSONResponse(content={"status": "success", "rows": rows})


@system_router.get("/system/yahoo-api-stats/{date_str}")
async def get_yahoo_api_stats_detail_endpoint(date_str: str):
    from collections import defaultdict
    from database import get_yahoo_api_call_log
    from scheduler_manifest import job_label

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid date, expected YYYY-MM-DD."})

    try:
        raw_rows = get_yahoo_api_call_log(date_str)
        bucket_jobs: dict = defaultdict(lambda: defaultdict(lambda: {"count": 0, "errors": 0, "yf_errors": 0}))
        for r in raw_rows:
            naive = datetime.strptime(r["minute_ts"] + ":00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            local_dt = time_engine.to_local(naive)
            bucket_minute = (local_dt.minute // 15) * 15
            bucket_label = local_dt.strftime("%H:") + "%02d" % bucket_minute
            label = job_label(r["job_id"]) if r["job_id"] else "Manual / On-Demand"
            entry = bucket_jobs[bucket_label][label]
            entry["count"] += r["call_count"]
            if r["status"] in ("429", "error"):
                entry["errors"] += r["call_count"]
            entry["yf_errors"] += r["yf_logged_errors"] or 0

        all_buckets = ["%02d:%02d" % (h, m) for h in range(24) for m in (0, 15, 30, 45)]
        job_labels = sorted({label for jobs in bucket_jobs.values() for label in jobs})
        series = {
            label: [bucket_jobs.get(b, {}).get(label, {}).get("count", 0) for b in all_buckets]
            for label in job_labels
        }
        errors_by_bucket = [
            sum(job_counts["errors"] for job_counts in bucket_jobs.get(b, {}).values())
            for b in all_buckets
        ]
        # Tracked separately from errors_by_bucket: yfinance logged these internally (e.g. "possibly
        # delisted", a 404 on an unsupported quoteSummary module) without raising, so they never set
        # stat_status="error" in yahoo_connection_boundary — a real request outcome, just not a request failure.
        yf_logged_errors_by_bucket = [
            sum(job_counts["yf_errors"] for job_counts in bucket_jobs.get(b, {}).values())
            for b in all_buckets
        ]
        return JSONResponse(content={
            "status": "success",
            "date": date_str,
            "buckets": all_buckets,
            "job_labels": job_labels,
            "series": series,
            "errors_by_bucket": errors_by_bucket,
            "yf_logged_errors_by_bucket": yf_logged_errors_by_bucket,
        })
    except Exception as e:
        return _error_500(e)


@system_router.get("/system/metrics")
async def get_system_metrics():
    """Returns a comprehensive diagnostic payload of system hardware, DB, and ML states."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        def get_cnt(query: str) -> int:
            try:
                cursor.execute(query)
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            except Exception:
                return 0

        total_universe = get_cnt("SELECT COUNT(*) FROM market_universe")
        total_index = get_cnt("SELECT COUNT(*) FROM market_universe WHERE is_index = 1")
        total_ft = get_cnt("SELECT COUNT(*) FROM market_universe WHERE is_freetrade = 1")
        total_sp500 = get_cnt("SELECT COUNT(*) FROM market_universe WHERE is_index = 1 AND index_membership LIKE '%SP500%'")
        total_ftse = get_cnt("SELECT COUNT(*) FROM market_universe WHERE is_index = 1 AND index_membership LIKE '%FTSE100%'")

        coverage = {
            "stock_signals": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN stock_signals t ON m.ticker = t.ticker WHERE m.is_index = 1"),
            "quant_signals": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN quant_signals t ON m.ticker = t.ticker WHERE m.is_index = 1"),
            "ticker_metadata": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN ticker_metadata t ON m.ticker = t.ticker WHERE m.is_index = 1"),
            "asset_profiles": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN asset_profiles t ON m.ticker = t.ticker WHERE m.is_index = 1")
        }

        def get_json_len(path_obj: Path, list_key: str = None) -> int:
            if not path_obj.exists(): return 0
            try:
                with open(path_obj, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and list_key:
                        return len(data.get(list_key, []))
                    elif isinstance(data, dict):
                        return len(data.keys())
                    elif isinstance(data, list):
                        return len(data)
            except Exception:
                return 0
            return 0

        blacklist_path = DATA_DIR / "freetrade_blacklist.json"
        json_trackers = {
            "portfolio": get_json_len(PORTFOLIO_PATH),
            "watchlist": get_json_len(WATCHLIST_PATH, "watchlist"),
            "blacklist": get_json_len(blacklist_path)
        }

        fundamentals_files = len(list(FUNDAMENTALS_DIR.glob("*.json"))) if FUNDAMENTALS_DIR.exists() else 0

        models_dir = BASE_DIR / "models"
        ml_model_path = models_dir / "ml_ensemble.joblib"
        feat_stats_path = models_dir / "feature_stats.joblib"

        def get_file_stats(path: Path) -> dict:
            if not path.exists():
                return {"exists": False, "mtime": "Not Found", "size_mb": 0.0}
            mtime = time_engine.fmt_datetime(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
            size_mb = round(path.stat().st_size / (1024 * 1024), 2)
            return {"exists": True, "mtime": mtime, "size_mb": size_mb}

        ensemble_stats = get_file_stats(ml_model_path)

        feature_count = 0
        train_universe_size = 0
        if feat_stats_path.exists():
            try:
                f_stats = joblib.load(feat_stats_path)
                feature_count = len(f_stats.keys()) if isinstance(f_stats, dict) else 0
                train_universe_size = int(f_stats.get("_meta", {}).get("train_universe_size") or 0) if isinstance(f_stats, dict) else 0
            except Exception:
                logger.warning("Failed to load ML feature stats from %s", feat_stats_path, exc_info=True)

        inference_coverage = get_cnt("""
            SELECT COUNT(DISTINCT qs.ticker) FROM quant_signals qs
            WHERE qs.mom_1m IS NOT NULL AND qs.atr_pct IS NOT NULL
              AND qs.rel_strength_5d IS NOT NULL AND qs.rel_strength_20d IS NOT NULL
              AND qs.date = (
                  SELECT MAX(qs2.date) FROM quant_signals qs2
                  WHERE qs2.ticker           = qs.ticker
                    AND qs2.mom_1m           IS NOT NULL
                    AND qs2.atr_pct          IS NOT NULL
                    AND qs2.rel_strength_5d  IS NOT NULL
                    AND qs2.rel_strength_20d IS NOT NULL
              )
        """)
        inference_threshold = max(30, int(0.25 * train_universe_size)) if train_universe_size else 0

        hmm_states = get_cnt("SELECT COUNT(*) FROM market_regimes WHERE ai_hmm_state IS NOT NULL")
        rf_states = get_cnt("SELECT COUNT(*) FROM macro_calendar WHERE ai_consensus_miss_prob IS NOT NULL")

        from config import ANOMALY_MODELS_DIR
        _stale_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        anomaly_model_cnt = 0
        anomaly_stale_cnt = 0
        if ANOMALY_MODELS_DIR.exists():
            for _jf in ANOMALY_MODELS_DIR.glob("*.joblib"):
                anomaly_model_cnt += 1
                try:
                    _payload = joblib.load(_jf)
                    _trained_at = _payload.get('trained_at')
                    if not _trained_at:
                        anomaly_stale_cnt += 1
                        continue
                    _dt = datetime.fromisoformat(_trained_at)
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    if _dt < _stale_cutoff:
                        anomaly_stale_cnt += 1
                except Exception:
                    anomaly_stale_cnt += 1

        cpu_load = os.getloadavg() if hasattr(os, 'getloadavg') else (0.0, 0.0, 0.0)
        total_disk, used_disk, free_disk = shutil.disk_usage(BASE_DIR)

        def get_dir_size(path: Path):
            if not path.exists(): return 0.0, 0
            files = list(path.glob("*.*"))
            size_mb = sum(f.stat().st_size for f in files if f.is_file()) / (1024 * 1024)
            return round(size_mb, 2), len(files)

        hist_size, hist_cnt = get_dir_size(HISTORICAL_DIR)
        intra_size, intra_cnt = get_dir_size(INTRADAY_DIR)
        db_size = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2) if DB_PATH.exists() else 0.0

        macro_ind_cnt = get_cnt("SELECT COUNT(*) FROM macro_indicators")
        macro_cal_cnt = get_cnt("SELECT COUNT(*) FROM macro_calendar")
        pending_notes = get_cnt("SELECT COUNT(*) FROM system_notifications WHERE is_read = 0")
        sent_notes = get_cnt("SELECT COUNT(*) FROM system_notifications WHERE is_read = 1")

        job_last_runs = get_all_job_last_runs()
        config_key_to_job = CONFIG_KEY_TO_JOB
        def _localise_ts(ts: str) -> str:
            if not ts or ts == "Never":
                return "Never"
            from datetime import datetime as _dt
            for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
                try:
                    return time_engine.fmt_datetime(_dt.strptime(ts, fmt))
                except ValueError:
                    continue
            return ts

        scheduler_last_runs = {
            cfg_key: _localise_ts((job_last_runs.get(job_id) or {}).get("last_run", "Never"))
            for cfg_key, job_id in config_key_to_job.items()
        }
        scheduler_last_runs_sort = {
            cfg_key: (job_last_runs.get(job_id) or {}).get("last_run") or ""
            for cfg_key, job_id in config_key_to_job.items()
        }
        for auction_job_id in ("macro_auction_job_am", "macro_auction_job_pm"):
            scheduler_last_runs[auction_job_id] = _localise_ts((job_last_runs.get(auction_job_id) or {}).get("last_run", "Never"))
            scheduler_last_runs_sort[auction_job_id] = (job_last_runs.get(auction_job_id) or {}).get("last_run") or ""

        return JSONResponse(content={
            "status": "success",
            "universe": {
                "total": total_universe, "index": total_index, "freetrade": total_ft,
                "sp500": total_sp500, "ftse": total_ftse,
                "coverage": coverage, "json_trackers": json_trackers,
                "fundamentals_files": fundamentals_files
            },
            "ml": {
                "ensemble": ensemble_stats, "feature_count": feature_count,
                "macro_hmm_outputs": hmm_states, "macro_rf_outputs": rf_states,
                "anomaly_model_count": anomaly_model_cnt,
                "anomaly_stale_count": anomaly_stale_cnt,
                "inference_coverage": inference_coverage,
                "train_universe_size": train_universe_size,
                "inference_threshold": inference_threshold
            },
            "infra": {
                "cpu": [round(c, 2) for c in cpu_load],
                "disk_used_gb": round(used_disk / (1024**3), 2),
                "disk_total_gb": round(total_disk / (1024**3), 2),
                "disk_pct": round((used_disk / total_disk) * 100, 1),
                "db_size_mb": db_size,
                "hist_size_mb": hist_size, "hist_cnt": hist_cnt,
                "intra_size_mb": intra_size, "intra_cnt": intra_cnt
            },
            "state": {
                "macro_ind": macro_ind_cnt, "macro_cal": macro_cal_cnt,
                "notes_pending": pending_notes, "notes_sent": sent_notes
            },
            "scheduler_last_runs": scheduler_last_runs,
            "scheduler_last_runs_sort": scheduler_last_runs_sort,
            "yahoo_cache": yahoo_engine.get_stats(),
        })
    except Exception as e:
        logger.error(f"Failed to fetch system metrics: {e}")
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@system_router.get("/system/checks")
async def get_system_checks(request: Request):
    from system_check_engine import run_system_checks
    issues = run_system_checks()
    return JSONResponse(content={"status": "success", "issues": issues})


def _yahoo_ok() -> bool:
    # yahoo_engine.get_stats()'s hit_rate_pct is an in-process cache stat that reads 0% right after
    # a restart even when Yahoo itself is perfectly healthy — get_yahoo_api_stats() is real call
    # history, so the most recent day's row is used to check for at least one non-error call.
    from database import get_yahoo_api_stats
    rows = get_yahoo_api_stats(days=1)
    if not rows:
        return False
    row = rows[0]
    total = row.get("total_calls") or 0
    errors = (row.get("rate_limit_429") or 0) + (row.get("other_errors") or 0)
    return total > errors


@system_router.get("/system/market-status")
async def api_market_status(background_tasks: BackgroundTasks):
    from system_check_engine import run_system_checks
    issues = run_system_checks()
    # Home Assistant's coordinator polls this endpoint unconditionally every cycle regardless of
    # market hours (see AGENTS.md "Always-on polling") — it's the only endpoint every HA install
    # is guaranteed to hit passively, so it doubles as the Markets registry's background warm-up
    # for installs that never press "Refresh Data" and never have /markets open in a browser tab.
    # Without this, the Markets page's tickers only got warmed by page traffic and stayed stale
    # (visibly "crossed over") between visits — found 2026-07-10.
    stale_tickers = set(proxy_tickers_needing_refresh())
    stale_tickers.update(registry_tickers_needing_refresh(markets_engine.registry_lookup_tickers()))
    if stale_tickers:
        background_tasks.add_task(fetch_and_save_pulse, list(stale_tickers))
    return JSONResponse(content={
        "status": "success",
        "us_market_open": is_exchange_open("NYSE"),
        "uk_market_open": is_exchange_open("LSE"),
        "yahoo_ok": _yahoo_ok(),
        "system_ok": len(issues) == 0,
    })


@system_router.post("/system/git-pull", dependencies=[Depends(require_confirm_token)])
async def git_pull_update():
    global _requirements_changed_pending
    try:
        old_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR)).stdout.strip()
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR))
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={"status": "error", "message": f"Git Pull Failed:\n{result.stderr}"})
        diff = subprocess.run(["git", "diff", "--name-only", old_head, "HEAD"], capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR))
        requirements_changed = "requirements.txt" in diff.stdout.splitlines()
        _requirements_changed_pending = _requirements_changed_pending or requirements_changed
        message = f"Update successful. Please restart the service if required.\n\n{result.stdout}"
        if requirements_changed:
            message += "\n\n⚠️ requirements.txt changed — dependencies will be reinstalled automatically before the next restart."
        return JSONResponse(content={"status": "success", "message": message, "requirements_changed": requirements_changed})
    except Exception as e:
        return _error_500(e)


@system_router.get("/system/active-jobs")
async def get_active_jobs_status():
    from scheduler_engine import get_active_jobs
    jobs = get_active_jobs()
    return JSONResponse(content={"status": "success", "active_jobs": jobs, "busy": bool(jobs), "requirements_changed_pending": _requirements_changed_pending})


@system_router.post("/system/restart", dependencies=[Depends(require_confirm_token)])
async def restart_system(background_tasks: BackgroundTasks):
    from scheduler_engine import get_active_jobs
    active = get_active_jobs()
    if active:
        names = ", ".join(active.keys())
        return JSONResponse(
            status_code=409,
            content={"status": "busy", "message": f"Cannot restart: {names} is currently running. Please wait for it to complete and try again."}
        )
    background_tasks.add_task(execute_restart)
    return JSONResponse(content={"status": "success", "message": "Restart signal sent. The dashboard will be back online in ~5-10 seconds."})


@system_router.post("/system/force-restart", dependencies=[Depends(require_confirm_token)])
async def force_restart_system(background_tasks: BackgroundTasks):
    background_tasks.add_task(execute_restart)
    return JSONResponse(content={"status": "success", "message": "Force restart signal sent. The dashboard will be back online in ~5-10 seconds."})


@system_router.post("/system/terminate-jobs", dependencies=[Depends(require_confirm_token)])
async def terminate_active_jobs():
    from scheduler_engine import get_active_jobs, force_clear_active_jobs
    cleared = get_active_jobs()
    force_clear_active_jobs()
    names = list(cleared.keys())
    return JSONResponse(content={"status": "success", "terminated": names})


@system_router.post("/settings", dependencies=[Depends(require_confirm_token)])
async def save_settings(config: SettingsConfig):
    try:
        incoming_data = config.model_dump(exclude_none=True)
        if incoming_data.get("GHOSTFOLIO_ENABLED") is False:
            incoming_data["GHOSTFOLIO_ACCOUNTS"] = {"discovered": [], "active": []}
            scheduling = incoming_data.setdefault("SCHEDULING", {})
            scheduling["GHOSTFOLIO_SYNC"] = {**scheduling.get("GHOSTFOLIO_SYNC", {}), "ENABLED": False}
            purge_ghostfolio_files()
        update_config_atomic(incoming_data)
        reload_scheduler()
        _configure_file_logging(load_config())
        return JSONResponse(content={"status": "success", "message": "Settings saved successfully."})
    except Exception as e:
        return _error_500(e)


@system_router.get("/notifications/latest")
async def get_latest_notifications(last_id: int = 0):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, message_type, message_text, timestamp FROM system_notifications WHERE id > ? AND is_read = 0 ORDER BY id ASC",
            (last_id,)
        )
        rows = cursor.fetchall()
        notifications = []
        for row in rows:
            ts_raw = row["timestamp"] or ""
            try:
                from datetime import datetime as _dt
                dt = _dt.strptime(ts_raw[:19], "%Y-%m-%d %H:%M:%S")
                ts_display = time_engine.fmt_datetime(dt)
            except (ValueError, TypeError):
                ts_display = ts_raw
            notifications.append({
                "id": row["id"],
                "type": row["message_type"],
                "text": row["message_text"],
                "timestamp": ts_display,
            })
        return JSONResponse(content={"status": "success", "notifications": notifications})
    except Exception as e:
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@system_router.get("/workflow-monitor/status")
async def get_workflow_monitor_status():
    try:
        graph = build_workflow_graph()
        conflicts = detect_workflow_conflicts(graph)

        def _localise(ts: str):
            if not ts:
                return None
            from datetime import datetime as _dt
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return time_engine.fmt_datetime(_dt.strptime(ts, fmt))
                except ValueError:
                    continue
            return ts

        for node in graph["nodes"]:
            node["last_run_display"] = _localise(node.get("last_run"))
            node["next_run_display"] = _localise(node.get("next_run"))

        return JSONResponse(content={
            "status": "success",
            "nodes": graph["nodes"],
            "edges": graph["edges"],
            "conflicts": conflicts,
        })
    except Exception as e:
        return _error_500(e)


@system_router.post("/notifications/mark-read")
async def mark_notifications_read():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE system_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        return JSONResponse(content={"status": "success", "message": "All notifications marked as read."})
    except Exception as e:
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@system_router.post("/notifications/purge")
async def purge_all_notifications():
    """
    Purges all historical notifications from the SQLite database.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM system_notifications")
        conn.commit()
        return JSONResponse(content={"status": "success", "message": "All notifications purged successfully."})
    except Exception as e:
        logger.error(f"Failed to purge notifications: {e}")
        return _error_500(e)
    finally:
        if conn:
            conn.close()
