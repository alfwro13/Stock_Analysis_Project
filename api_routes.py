# api_routes.py
import asyncio
import os
import time_engine
import shutil
import sqlite3
import json
import time
import signal
import subprocess
import joblib
import pandas as pd
import logging
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Query, Path as PathParam, Response, Depends, Header
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
from pydantic import BaseModel

from log_config import configure_file_logging
from config import (
    load_config,
    update_config_atomic,
    SECRETS_PATH,
    DATA_DIR,
    BASE_DIR,
    DB_PATH,
    PORTFOLIO_PATH,
    WATCHLIST_PATH,
    FUNDAMENTALS_DIR,
    HISTORICAL_DIR,
    INTRADAY_DIR
)
from database import get_connection, get_universe_tickers
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, run_freetrade_sync, reload_scheduler, run_sentiment_scan, run_index_scraper, run_fundamentals_profiler, run_universe_deep_sync_job, get_all_job_last_runs, run_xray_risk_cache_job, run_anomaly_training_job, record_job_run, run_maintenance_engine
from maintenance_engine import MaintenanceEngine
from xray_engine import assemble_xray_report
from ghostfolio_sync import GhostfolioSyncEngine
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse
from sentiment_engine import run_nextcloud_alert
from huggingface_engine import update_all_sentiment
from earnings_engine import run_earnings_alert
from report_dispatcher import push_morning_quant_briefing, push_lunchtime_quant_briefing
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine
from news_feed_engine import run_news_feed_job
from intraday_bottom_engine import IntradayBottomEngine
from data_engine import DataEngine
from utils import normalize_ticker
from quant_signals import QuantEngine
from quant_engine import run_daily_quant_scan
from quant_screener import compute_quality_grade, _get_earnings_days
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe
from reports_engine import get_sector_trends, get_mean_reversion_setups, get_leaders_laggards, get_dividend_harvest_setups, get_quality_compounders, get_garp_tenbaggers, get_quality_on_sale
from options_engine import fetch_options_chain, calculate_payoff_matrix
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
from risk_engine import update_all_tail_risks
from profile_engine import get_profiler_queue_breakdown, update_single_profile
from tools.network_engine import GLOBAL_IPV6_STATUS
from yahoo_engine import yahoo_engine
# Import curl_cffi for resilient IPv6 socket testing
from curl_cffi import requests as cffi_requests
from seed_macro_calendar import seed_calendar
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from macro_ai_engine import MacroAIEngine
from visuals import create_intraday_chart, _intraday_market_tz, _EXCHANGE_DELAYS
from quant_signals import get_candlestick_patterns

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")


def require_confirm_token(x_confirm_token: str = Header(..., alias="X-Confirm-Token")):
    import secrets as _secrets
    expected = os.environ.get("ADMIN_CONFIRM_TOKEN", "")
    if not expected or not _secrets.compare_digest(x_confirm_token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing confirmation token.")


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


@api_router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, response: Response):
    import secrets as _secrets
    valid_user = _secrets.compare_digest(
        body.username.encode(), os.environ.get("DASHBOARD_USERNAME", "").encode()
    )
    valid_pass = _secrets.compare_digest(
        body.password.encode(), os.environ.get("DASHBOARD_PASSWORD", "").encode()
    )
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    from auth import create_session_token, cookie_kwargs
    token = create_session_token(body.username, body.remember_me)
    response.set_cookie(value=token, **cookie_kwargs(body.remember_me))
    return {"status": "ok"}


@api_router.post("/generate-api-key", dependencies=[Depends(require_confirm_token)])
async def generate_api_key():
    import secrets as _secrets
    from dotenv import set_key
    from config import BASE_DIR
    new_key = _secrets.token_hex(32)
    set_key(str(BASE_DIR / ".env"), "API_KEY", new_key)
    os.environ["API_KEY"] = new_key
    return {"api_key": new_key}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@api_router.post("/change-password", dependencies=[Depends(require_confirm_token)])
async def change_password(body: ChangePasswordRequest):
    import secrets as _secrets
    from dotenv import set_key
    from config import BASE_DIR

    current = os.environ.get("DASHBOARD_PASSWORD", "")
    if not _secrets.compare_digest(body.current_password.encode(), current.encode()):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if body.new_password == "changeme":
        raise HTTPException(status_code=400, detail="Please choose a different password.")

    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_PASSWORD", body.new_password)
    os.environ["DASHBOARD_PASSWORD"] = body.new_password
    return {"status": "ok"}


class SaveNextcloudSettingsRequest(BaseModel):
    NEXTCLOUD_URL: str
    BOT_USERNAME: str
    APP_PASSWORD: str
    CONVERSATION_TOKEN: str


@api_router.post("/save-nextcloud-settings", dependencies=[Depends(require_confirm_token)])
async def save_nextcloud_settings(body: SaveNextcloudSettingsRequest):
    from dotenv import set_key
    from config import BASE_DIR
    env_path = str(BASE_DIR / ".env")
    mapping = {
        "NEXTCLOUD_URL": body.NEXTCLOUD_URL,
        "NEXTCLOUD_BOT_USERNAME": body.BOT_USERNAME,
        "NEXTCLOUD_APP_PASSWORD": body.APP_PASSWORD,
        "NEXTCLOUD_CONVERSATION_TOKEN": body.CONVERSATION_TOKEN,
    }
    for key, value in mapping.items():
        set_key(env_path, key, value)
        os.environ[key] = value
    return {"status": "ok"}


@api_router.post("/test-nextcloud-message", dependencies=[Depends(require_confirm_token)])
async def test_nextcloud_message():
    from nextcloud_talk import send_text_message
    url = os.environ.get("NEXTCLOUD_URL", "")
    token = os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN", "")
    user = os.environ.get("NEXTCLOUD_BOT_USERNAME", "")
    pwd = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    if not all([url, token, user, pwd]):
        missing = [k for k, v in {"NEXTCLOUD_URL": url, "NEXTCLOUD_CONVERSATION_TOKEN": token, "NEXTCLOUD_BOT_USERNAME": user, "NEXTCLOUD_APP_PASSWORD": pwd}.items() if not v]
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Missing credentials: {', '.join(missing)}"})
    ok = send_text_message("✅ Quantamental test message — Nextcloud Talk integration is working correctly.", {})
    if ok:
        return JSONResponse(content={"status": "success", "message": "Test message sent successfully."})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Send failed. Check server logs for the HTTP error detail."})


class SaveGhostfolioSettingsRequest(BaseModel):
    GHOSTFOLIO_URL: str
    GHOSTFOLIO_TOKEN: str


@api_router.post("/save-ghostfolio-settings", dependencies=[Depends(require_confirm_token)])
async def save_ghostfolio_settings(body: SaveGhostfolioSettingsRequest):
    from dotenv import set_key
    from config import BASE_DIR
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "GHOSTFOLIO_URL", body.GHOSTFOLIO_URL)
    set_key(env_path, "GHOSTFOLIO_TOKEN", body.GHOSTFOLIO_TOKEN)
    os.environ["GHOSTFOLIO_URL"] = body.GHOSTFOLIO_URL
    os.environ["GHOSTFOLIO_TOKEN"] = body.GHOSTFOLIO_TOKEN
    return {"status": "ok"}


class SaveFredApiKeyRequest(BaseModel):
    FRED_API_KEY: str


@api_router.post("/save-fred-api-key", dependencies=[Depends(require_confirm_token)])
async def save_fred_api_key(body: SaveFredApiKeyRequest):
    from dotenv import set_key
    from config import BASE_DIR
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "FRED_API_KEY", body.FRED_API_KEY)
    os.environ["FRED_API_KEY"] = body.FRED_API_KEY
    return {"status": "ok"}


class SaveHFTokenRequest(BaseModel):
    HF_TOKEN: str


@api_router.post("/save-hf-token", dependencies=[Depends(require_confirm_token)])
async def save_hf_token(body: SaveHFTokenRequest):
    from dotenv import set_key
    from config import BASE_DIR
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "HF_TOKEN", body.HF_TOKEN)
    os.environ["HF_TOKEN"] = body.HF_TOKEN
    return {"status": "ok", "message": "HF Token saved."}


class TestHFTokenRequest(BaseModel):
    HF_TOKEN: str = ""


@api_router.post("/test-hf-token", dependencies=[Depends(require_confirm_token)])
async def test_hf_token(body: TestHFTokenRequest):
    token = body.HF_TOKEN.strip() or os.environ.get("HF_TOKEN", "")
    if not token:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No HuggingFace token provided. Enter a token and try again.")
    try:
        from huggingface_hub import whoami
        info = whoami(token=token)
        username = info.get("name") or info.get("fullname") or "unknown"
        return {"status": "ok", "message": f"Token is valid. Authenticated as: {username}"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Token verification failed: {e}")


class ChangeUsernameRequest(BaseModel):
    new_username: str


@api_router.post("/change-username", dependencies=[Depends(require_confirm_token)])
async def change_username(body: ChangeUsernameRequest):
    from dotenv import set_key
    from config import BASE_DIR
    username = body.new_username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_USERNAME", username)
    os.environ["DASHBOARD_USERNAME"] = username
    return {"status": "ok"}


@api_router.post("/rotate-app-secret", dependencies=[Depends(require_confirm_token)])
async def rotate_app_secret():
    import secrets as _secrets
    from dotenv import set_key
    from config import BASE_DIR
    new_secret = _secrets.token_hex(32)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "APP_SECRET_KEY", new_secret)
    os.environ["APP_SECRET_KEY"] = new_secret
    return {"status": "ok"}


@api_router.post("/rotate-confirm-token", dependencies=[Depends(require_confirm_token)])
async def rotate_confirm_token():
    import secrets as _secrets
    from dotenv import set_key
    from config import BASE_DIR
    new_token = _secrets.token_hex(16)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "ADMIN_CONFIRM_TOKEN", new_token)
    os.environ["ADMIN_CONFIRM_TOKEN"] = new_token
    return {"status": "ok", "new_token": new_token}


# --- RESOLVE CORRECT IMPORT DIRECTORY ---
IMPORT_DIR = BASE_DIR / "tools" / "data" / "imports"

# --- SHARED PYDANTIC SCHEMAS ---
class TickerRequest(BaseModel):
    ticker: str

class OptionLeg(BaseModel):
    type: str
    strike: float
    premium: float
    position: str
    quantity: int = 1

class PayoffRequest(BaseModel):
    current_price: float
    legs: List[OptionLeg]

class ImportRequest(BaseModel):
    filename: str

class PulseRequest(BaseModel):
    tickers: Optional[List[str]] = []

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
    SEND_NEXTCLOUD: Optional[bool] = None
    BULL_TRAP: Optional[bool] = None
    BEAR_TRAP: Optional[bool] = None
    CAPITULATION: Optional[bool] = None
    WYCKOFF: Optional[bool] = None
    MONITOR_PORTFOLIO: Optional[bool] = None

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
    FREETRADE_SYNC: Optional[ScheduleItemConfig] = None
    MACRO_ENGINE: Optional[ScheduleItemConfig] = None
    ML_BACKFILL: Optional[ScheduleItemConfig] = None
    ML_TRAINING: Optional[ScheduleItemConfig] = None
    ML_INFERENCE: Optional[ScheduleItemConfig] = None
    QUANT_ENGINE: Optional[ScheduleItemConfig] = None
    EARNINGS_ENGINE: Optional[ScheduleItemConfig] = None
    DISPATCHER: Optional[ScheduleItemConfig] = None
    UNIVERSE_ENGINE: Optional[ScheduleItemConfig] = None
    CB_NLP_ALERT: Optional[ScheduleItemConfig] = None
    AI_CONTAGION: Optional[ScheduleItemConfig] = None
    NEWS_FEED: Optional[ScheduleItemConfig] = None
    SMGB_PREDICTOR: Optional[ScheduleItemConfig] = None
    TRAP_MONITORS: Optional[ScheduleItemConfig] = None

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
    LEADER_THRESHOLD_PCT: Optional[float] = None
    ETF_CONFIRMATION_THRESHOLD_PCT: Optional[float] = None
    VOLUME_SPIKE_MULTIPLIER: Optional[float] = None
    BELLWETHER_TICKERS: Optional[List[str]] = None
    ETF_BASKET: Optional[List[str]] = None
    NEXTCLOUD_ENABLED: Optional[bool] = None
    PROXY_TICKERS: Optional[List[str]] = None

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
    DIP_RADAR_NEXTCLOUD: Optional[bool] = None

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
    # Credentials live in .env only — never written through this endpoint.
    model_config = {"extra": "forbid"}

    SERVER_URL: Optional[str] = None
    YAHOO_IPV6_ADDRESS: Optional[str] = None
    NETWORK_FAULT_NOTIFY_NEXTCLOUD: Optional[bool] = None
    PORT: Optional[int] = None
    BASE_CURRENCY: Optional[str] = None
    USER_TIMEZONE: Optional[str] = None
    HOME_EXCHANGE: Optional[str] = None
    IGNORED_TICKERS: Optional[List[str]] = None
    GHOSTFOLIO_ACCOUNTS: Optional[GhostfolioAccountsConfig] = None
    UI_PREFERENCES: Optional[UIPreferencesConfig] = None
    POSITION_SIZING: Optional[PositionSizingConfig] = None
    FREETRADE_MAPPINGS: Optional[FreetradeMappingsConfig] = None
    SCHEDULING: Optional[SchedulingConfig] = None
    REPORTS_DEFAULTS: Optional[ReportsDefaultsConfig] = None
    NOTIFICATIONS: Optional[NotificationsConfig] = None
    XRAY_TARGETS: Optional[dict] = None
    FILE_LOGGING: Optional[FileLoggingConfig] = None


def log_notification(message_type: str, message_text: str) -> None:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def bg_execute_quant_scan():
    engine = DataEngine()
    tickers = engine.get_all_tickers()

    if tickers:
        run_daily_quant_scan(tickers)
        update_daily_ml_predictions(tickers)
        update_all_tail_risks(tickers)

def bg_execute_earnings_scan():
    engine = DataEngine()
    tickers = engine.get_all_tickers()
    try:
        run_earnings_vol_scan(tickers)
    finally:
        record_job_run('weekend_earnings_vol_scan_job')

def bg_execute_universe_quant_scan():
    tickers = get_universe_tickers()
    if not tickers:
        logger.warning("Universe is empty. Please trigger a Universe Update first.")
        return
    run_daily_quant_scan(tickers, scan_type='universe')

def bg_execute_universe_quant_scan_subset(tickers: List[str]):
    run_daily_quant_scan(tickers, scan_type='sideload')

def bg_execute_ml_inference():
    tickers = get_universe_tickers()
    if not tickers:
        engine = DataEngine()
        tickers = engine.get_all_tickers()
        
    if tickers:
        update_daily_ml_predictions(tickers)

def bg_init_macro_pipeline():
    try:
        logger.info("Starting Macro AI Initialization Sequence...")
        
        seed_calendar()
        update_macro_calendar()
        update_macro_indicators()

        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        calculate_market_regime()

        ai_engine = MacroAIEngine()
        try:
            ai_engine.train_regime_clustering()
            ai_engine.train_consensus_miss_probability()
            ai_engine.train_volatility_magnitude()
            scan_date = time_engine.now_local().strftime('%Y-%m-%d')
            ai_engine.run_macro_inference(scan_date)
        finally:
            ai_engine.close()

        # Update config.json to mark initialization as complete
        update_config_atomic({"SCHEDULING": {"MACRO_ENGINE": {"INITIALIZED": True}}})
        
        log_notification("Success", "Macro AI Pipeline successfully initialized and trained.")
    except Exception as e:
        logger.error(f"Macro AI Pipeline initialization failed: {e}")
        log_notification("Error", f"Macro AI Pipeline Initialization failed: {e}")

def bg_run_macro_pipeline():
    try:
        logger.info("Starting Macro AI Run Sequence...")

        update_macro_calendar()
        update_macro_indicators()

        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        calculate_market_regime()

        ai_engine = MacroAIEngine()
        try:
            scan_date = time_engine.now_local().strftime('%Y-%m-%d')
            ai_engine.run_macro_inference(scan_date)
        finally:
            ai_engine.close()

        log_notification("Success", "Macro AI Pipeline executed successfully.")
    except Exception as e:
        logger.error(f"Macro AI Pipeline execution failed: {e}")
        log_notification("Error", f"Macro AI Pipeline execution failed: {e}")

@api_router.post("/macro/init-pipeline")
@limiter.limit("2/minute")
async def trigger_macro_init_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_init_macro_pipeline)
    return JSONResponse(content={
        "status": "success", 
        "message": "Macro AI Initialization started in the background. Check notifications."
    })

@api_router.post("/macro/run-pipeline")
@limiter.limit("2/minute")
async def trigger_macro_run_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_run_macro_pipeline)
    return JSONResponse(content={
        "status": "success",
        "message": "Macro AI Run initiated in the background. Check notifications."
    })


@api_router.get("/macro-regime-allocation")
@limiter.limit("30/minute")
async def get_macro_regime_allocation(request: Request):
    """
    Returns the current macro regime label, key driving signals, ideal asset class
    allocation for that regime, current portfolio allocation (requires Ghostfolio),
    an alignment score 0–100, and the last 90 days of regime history.
    """
    from macro_allocator_engine import get_macro_allocation_data
    try:
        data = get_macro_allocation_data()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error("macro-regime-allocation error: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": "Internal error — check server logs."})


# --- MODULAR ML ENDPOINTS ---
@api_router.post("/ml/trigger-backfill")
@limiter.limit("2/minute")
async def trigger_ml_backfill_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_historical_backfill)
    return JSONResponse(content={
        "status": "success", 
        "message": "ML Historical Backfill initiated in the background. Check System Notifications."
    })

@api_router.post("/ml/trigger-training")
@limiter.limit("2/minute")
async def trigger_ml_training_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(train_global_ml_model)
    return JSONResponse(content={
        "status": "success", 
        "message": "Global ML Walk-Forward Training initiated in the background. Check System Notifications."
    })

@api_router.post("/ml/trigger-inference")
@limiter.limit("2/minute")
async def trigger_ml_inference_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_ml_inference)
    return JSONResponse(content={
        "status": "success", 
        "message": "Daily ML Inference initiated in the background. Check System Notifications."
    })

@api_router.post("/ml/trigger-anomaly-training")
@limiter.limit("2/minute")
async def trigger_anomaly_training_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_anomaly_training_job)
    return JSONResponse(content={
        "status": "success",
        "message": "Isolation Forest anomaly training initiated in the background. Check System Notifications."
    })

@api_router.post("/trigger-quant-scan")
@limiter.limit("10/minute")
async def trigger_quant_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Portfolio Quant Scan initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-earnings-scan")
@limiter.limit("10/minute")
async def trigger_earnings_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_earnings_scan)
    return JSONResponse(content={
        "status": "success",
        "message": "Earnings Volatility Scan initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-morning-briefing")
@limiter.limit("10/minute")
async def trigger_morning_briefing_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(push_morning_quant_briefing)
    return JSONResponse(content={
        "status": "success",
        "message": "Morning Briefing generation started in the background. Check reports/ for the output file."
    })

@api_router.post("/trigger-lunch-briefing")
@limiter.limit("10/minute")
async def trigger_lunch_briefing_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(push_lunchtime_quant_briefing)
    return JSONResponse(content={
        "status": "success",
        "message": "Lunchtime Briefing generation started in the background. Check reports/ for the output file."
    })

@api_router.post("/trigger-universe-update")
@limiter.limit("10/minute")
async def trigger_universe_update_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(update_market_universe)
    return JSONResponse(content={
        "status": "success", 
        "message": "Market Universe update initiated in the background. Check System Notifications for progress."
    })

@api_router.get("/universe/profiler-status")
async def get_profiler_status():
    """
    Returns a full breakdown of the Fundamentals Profiler queue so the UI can
    show *why* the pending count is what it is (eligible vs already profiled
    vs stale). The legacy 'pending_count' top-level key is preserved for any
    external callers depending on the original API shape.
    """
    try:
        breakdown = get_profiler_queue_breakdown()
        return JSONResponse(content={
            "status": "success",
            "pending_count": breakdown.get("pending_count", 0),  # legacy key
            "breakdown": breakdown
        })
    except Exception as e:
        logger.error(f"Failed to compute profiler status: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.post("/universe/sync-indices")
@limiter.limit("10/minute")
async def trigger_sync_indices_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_index_scraper)
    return JSONResponse(content={
        "status": "success", 
        "message": "Index Constituent scraping initiated in the background. Check System Notifications for progress."
    })

@api_router.post("/universe/sync-profiler")
@limiter.limit("2/minute")
async def trigger_sync_profiler_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_fundamentals_profiler)
    return JSONResponse(content={
        "status": "success",
        "message": "Fundamentals Profiler initiated in the background. Check System Notifications for progress."
    })

@api_router.post("/universe/deep-sync")
@limiter.limit("2/minute")
async def trigger_universe_deep_sync_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Manually trigger the unified Universe Deep Sync pipeline.

    Sequences: fundamentals → metadata → technicals → ML inference for the
    full index universe (FTSE100 + S&P500), respecting UI_PREFERENCES.
    FREETRADE_ONLY_MODE for the Freetrade firewall. Returns immediately
    while the pipeline runs in the background (≈30–45 minutes).
    """
    background_tasks.add_task(run_universe_deep_sync_job)
    return JSONResponse(content={
        "status": "success",
        "message": (
            "Universe Deep Sync Pipeline initiated in the background. "
            "Sequencing fundamentals → metadata → technicals → ML inference "
            "for the full index universe. Estimated runtime: 30–45 minutes. "
            "Check System Notifications for progress."
        )
    })

@api_router.post("/trigger-universe-quant-scan")
@limiter.limit("2/minute")
async def trigger_universe_quant_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_universe_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Full Universe Quant Scan initiated in the background. This will take over an hour. Check System Notifications for progress."
    })

@api_router.post("/trigger-sentiment-scan")
@limiter.limit("10/minute")
async def trigger_sentiment_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sentiment_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Sentiment Scan initiated in the background. Check System Notifications for progress."
    })

@api_router.get("/universe/imports/list")
async def list_importable_csvs():
    try:
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        files = [f.name for f in IMPORT_DIR.glob("*.csv")]
        logger.info(f"Scan found {len(files)} CSV files in {IMPORT_DIR}")
        return JSONResponse(content={"status": "success", "files": files})
    except Exception as e:
        logger.error(f"Failed to list import directory {IMPORT_DIR}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Failed to list import directory: {str(e)}"})

@api_router.post("/universe/import/server")
async def import_server_csv(request: ImportRequest, background_tasks: BackgroundTasks):
    if not request.filename.endswith('.csv'):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid file type. Only .csv files are supported."})
    file_path = (IMPORT_DIR / request.filename).resolve()
    if not str(file_path).startswith(str(IMPORT_DIR.resolve())):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid filename."})
    conn = None
    try:
        if not file_path.exists():
            return JSONResponse(status_code=404, content={"status": "error", "message": f"File '{request.filename}' not found on server at {file_path}."})
            
        df = pd.read_csv(file_path)
        required_cols = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country', 'exchange']
        for col in required_cols:
            if col not in df.columns:
                return JSONResponse(status_code=400, content={"status": "error", "message": f"Malformed CSV. Missing required column: {col}"})
        
        df = df.dropna(subset=['ticker'])
        records = []
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for _, row in df.iterrows():
            records.append((
                str(row['ticker']),
                str(row['company_name']) if pd.notna(row['company_name']) else 'Unknown',
                str(row['sector']) if pd.notna(row['sector']) else 'Unclassified',
                str(row['industry']) if pd.notna(row['industry']) else 'Unclassified',
                str(row['country']) if pd.notna(row['country']) else 'Unknown',
                str(row['exchange']) if pd.notna(row['exchange']) else 'Unknown',
                current_time
            ))
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT OR REPLACE INTO market_universe
            (ticker, company_name, sector, industry, country, exchange, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', records)
        conn.commit()
        background_tasks.add_task(bg_execute_universe_quant_scan_subset, [r[0] for r in records])
        return JSONResponse(content={
            "status": "success",
            "message": f"Successfully sideloaded {len(records)} assets from '{request.filename}' into the local Market Universe."
        })
    except Exception as e:
        logger.error(f"Fatal error executing CSV parser for {request.filename}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Fatal error executing CSV parser: {str(e)}"})
    finally:
        if conn:
            conn.close()

async def execute_restart():
    await asyncio.sleep(2)
    os.kill(os.getpid(), signal.SIGTERM)

@api_router.post("/update")
@limiter.limit("10/minute")
async def trigger_update(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_update_pipeline)
    return JSONResponse(content={"status": "success"})

@api_router.post("/sync-ghostfolio")
@limiter.limit("10/minute")
async def trigger_ghostfolio_sync(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ghostfolio_sync)
    return JSONResponse(content={"status": "success"})

@api_router.post("/trigger-freetrade-sync")
@limiter.limit("10/minute")
async def trigger_freetrade_sync_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_freetrade_sync)
    return JSONResponse(content={
        "status": "success",
        "message": "Freetrade synchronization initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/maintenance/run")
@limiter.limit("5/minute")
async def trigger_maintenance_run(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_maintenance_engine)
    return JSONResponse(content={"status": "success", "message": "Maintenance job started in the background. Check System Notifications for the summary."})

@api_router.post("/maintenance/dry-run")
@limiter.limit("5/minute")
async def trigger_maintenance_dry_run(request: Request):
    try:
        engine = MaintenanceEngine()
        results = engine.dry_run()
        return JSONResponse(content={"status": "success", "results": results})
    except Exception as e:
        logger.exception("Maintenance dry-run failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.post("/ghostfolio/discover")
async def trigger_discovery():
    try:
        engine = GhostfolioSyncEngine()
        if not engine.authenticate():
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to authenticate with Ghostfolio."})
        accounts = engine.discover_accounts()
        if accounts:
            reload_scheduler()
            return JSONResponse(content={"status": "success", "message": f"Successfully discovered {len(accounts)} active accounts."})
        return JSONResponse(status_code=500, content={"status": "error", "message": "No accounts discovered or network error occurred."})
    except Exception as e:
        logger.exception("Ghostfolio account discovery failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.post("/market-pulse")
async def api_market_pulse(request: PulseRequest, background_tasks: BackgroundTasks):
    config_data = load_config()
    refresh_rate = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    pulse_data = get_cached_pulse_from_db(request.tickers, refresh_rate)
    needs_fetch = [item['ticker'] for item in pulse_data['indexes'] + pulse_data['assets'] if item['is_stale']]
    if needs_fetch:
        background_tasks.add_task(fetch_and_save_pulse, needs_fetch)
    return JSONResponse(content={"status": "success", "data": pulse_data})

@api_router.get("/market-pulse")
async def api_market_pulse_get(background_tasks: BackgroundTasks):
    config_data = load_config()
    refresh_rate = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    pulse_data = get_cached_pulse_from_db([], refresh_rate)
    needs_fetch = [item['ticker'] for item in pulse_data['indexes'] if item['is_stale']]
    if needs_fetch:
        background_tasks.add_task(fetch_and_save_pulse, needs_fetch)
    return JSONResponse(content={"status": "success", "data": pulse_data.get("indexes", [])})

@api_router.post("/test-sentiment-alert")
async def test_sentiment_alert():
    success, msg = await asyncio.to_thread(run_nextcloud_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@api_router.post("/test-earnings-alert")
async def test_earnings_alert():
    success, msg = await asyncio.to_thread(run_earnings_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@api_router.post("/test-insider-alert")
async def test_insider_alert():
    success, msg = await asyncio.to_thread(run_insider_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

# --- NEW: IPv6 DIAGNOSTIC ENDPOINT (cURL CFFI IMPLEMENTATION) ---
@api_router.post("/settings/test-yahoo-ipv6")
async def test_yahoo_ipv6(request: IPv6TestRequest):
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
        
        # Override the session's request method to enforce a strict timeout
        # This prevents the test from hanging indefinitely if the route is blocked
        original_request = test_session.request
        def timeout_request(*args, **kwargs):
            kwargs.setdefault('timeout', 10)
            return original_request(*args, **kwargs)
        test_session.request = timeout_request

        # Perform a lightweight baseline ticker fetch
        tk = yf.Ticker("SPY", session=test_session)
        df = tk.history(period="1d")
        
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
        
        # Intelligent exception parsing to return highly descriptive UI errors
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

@api_router.get("/settings/network-status")
async def get_network_status():
    """Returns the current active route and health status for Yahoo Finance connections."""
    config_data = load_config()
    ipv6_addr = config_data.get("YAHOO_IPV6_ADDRESS", "").strip()
    
    if not ipv6_addr:
        return JSONResponse(content={
            "status": "success",
            "route": "IPv4 (OS Default)",
            "indicator": "green",
            "message": "Using standard IPv4 routing. No custom IPv6 address is configured."
        })
        
    if GLOBAL_IPV6_STATUS["is_failing"]:
        fail_time_str = time_engine.fmt_datetime(datetime.fromtimestamp(GLOBAL_IPV6_STATUS["last_fail_time"], tz=timezone.utc))
        return JSONResponse(content={
            "status": "warning",
            "route": "IPv4 (Failover Rescue Active)",
            "indicator": "yellow",
            "message": f"IPv6 routing failed at {fail_time_str}. Traffic is actively being rescued via IPv4 fallback. Last Error: {GLOBAL_IPV6_STATUS['last_error']}"
        })
        
    return JSONResponse(content={
        "status": "success",
        "route": "IPv6 (Active)",
        "indicator": "green",
        "message": f"Successfully routing Yahoo Finance edge traffic exclusively through {ipv6_addr}."
    })
# --- RESTORED ROUTES BELOW THIS LINE ---

@api_router.get("/system/metrics")
async def get_system_metrics():
    """Returns a comprehensive diagnostic payload of system hardware, DB, and ML states."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 1. Universe & Data Coverage
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
        
        # Table Coverage (Index Tickers)
        coverage = {
            "stock_signals": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN stock_signals t ON m.ticker = t.ticker WHERE m.is_index = 1"),
            "quant_signals": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN quant_signals t ON m.ticker = t.ticker WHERE m.is_index = 1"),
            "ticker_metadata": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN ticker_metadata t ON m.ticker = t.ticker WHERE m.is_index = 1"),
            "asset_profiles": get_cnt("SELECT COUNT(DISTINCT m.ticker) FROM market_universe m INNER JOIN asset_profiles t ON m.ticker = t.ticker WHERE m.is_index = 1")
        }
        
        # Local JSON Trackers
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
        
        # 2. Machine Learning Artifacts
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
        
        # 3. Infrastructure & Storage
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
        
        # 4. State & Ledger Health
        macro_ind_cnt = get_cnt("SELECT COUNT(*) FROM macro_indicators")
        macro_cal_cnt = get_cnt("SELECT COUNT(*) FROM macro_calendar")
        pending_notes = get_cnt("SELECT COUNT(*) FROM system_notifications WHERE is_read = 0")
        sent_notes = get_cnt("SELECT COUNT(*) FROM system_notifications WHERE is_read = 1")

        # 5. Scheduler Last Run Times (keyed by SCHEDULING config key)
        job_last_runs = get_all_job_last_runs()
        config_key_to_job = {
            "GHOSTFOLIO_SYNC":    "ghostfolio_sync_job",
            "QUANT_ANALYSIS":     "quant_analysis_job",
            "SENTIMENT_ENGINE":   "sentiment_scan_job",
            "CRASH_ALERTS":       "intraday_orchestrator_job",
            "MOONSHOT_ALERTS":    "intraday_orchestrator_job",
            "MAINTENANCE":        "maintenance_job",
            "QUANT_ENGINE":       "overnight_quant_scan_job",
            "EARNINGS_ENGINE":    "weekend_earnings_vol_scan_job",
            "DISPATCHER":         "morning_briefing_dispatch_job",
            "UNIVERSE_ENGINE":    "universe_routine_job",
            "ML_BACKFILL":        "ml_backfill_job",
            "ML_TRAINING":        "ml_training_job",
            "ML_INFERENCE":       "ml_inference_job",
            "FREETRADE_SYNC":     "freetrade_sync_job",
            "MACRO_ENGINE":       "macro_calendar_job",
            "SYNC_INDICES":       "index_scraper_job",
            "PROFILER_ENGINE":    "fundamentals_profiler_job",
            "UNIVERSE_DEEP_SYNC": "universe_deep_sync_job",
            "ANOMALY_TRAINING":   "anomaly_training_job",
            "XRAY_RISK_CACHE":    "xray_risk_cache_job",
            "AI_CONTAGION":       "ai_contagion_job",
            "CB_NLP_ALERT":       "cb_nlp_alert_job",
            "NEWS_FEED":          "news_feed_job",
            "SYSTEM_CHECK":       "system_check_job",
            "SMGB_PREDICTOR":     "smgb_predictor_job",
            "TRAP_MONITORS":      "trap_monitor_job",
        }
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
            cfg_key: _localise_ts(job_last_runs.get(job_id, "Never"))
            for cfg_key, job_id in config_key_to_job.items()
        }

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
            "yahoo_cache": yahoo_engine.get_stats(),
        })
    except Exception as e:
        logger.error(f"Failed to fetch system metrics: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()

@api_router.get("/system/checks")
async def get_system_checks(request: Request):
    from system_check_engine import run_system_checks
    issues = run_system_checks()
    return JSONResponse(content={"status": "success", "issues": issues})

@api_router.post("/system/git-pull", dependencies=[Depends(require_confirm_token)])
async def git_pull_update():
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR))
        if result.returncode == 0:
            return JSONResponse(content={"status": "success", "message": f"Update successful. Please restart the service if required.\n\n{result.stdout}"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": f"Git Pull Failed:\n{result.stderr}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/system/active-jobs")
async def get_active_jobs_status():
    from scheduler_engine import get_active_jobs
    jobs = get_active_jobs()
    return JSONResponse(content={"status": "success", "active_jobs": jobs, "busy": bool(jobs)})

@api_router.post("/system/restart", dependencies=[Depends(require_confirm_token)])
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

@api_router.post("/settings", dependencies=[Depends(require_confirm_token)])
async def save_settings(config: SettingsConfig):
    try:
        incoming_data = config.model_dump(exclude_none=True)
        update_config_atomic(incoming_data)
        reload_scheduler()
        configure_file_logging(load_config())
        return JSONResponse(content={"status": "success", "message": "Settings saved successfully."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/notifications/latest")
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
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()

@api_router.post("/notifications/mark-read")
async def mark_notifications_read():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE system_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        return JSONResponse(content={"status": "success", "message": "All notifications marked as read."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()

@api_router.post("/notifications/purge")
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
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()

@api_router.get("/ai-contagion/status")
async def get_ai_contagion_status():
    """Returns the last 20 AI Contagion scan snapshots for the market-sentiment status panel."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT scan_ts, leader_count, etf_count, alert_fired, payload_json "
            "FROM ai_contagion_snapshots ORDER BY scan_ts DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        def _parse_payload(raw_json: str) -> tuple:
            raw = json.loads(raw_json or '{"tickers":[],"severity_score":0.0}')
            if isinstance(raw, list):
                # legacy rows stored a bare list
                return raw, 0.0
            return raw.get("tickers", []), raw.get("severity_score", 0.0)

        snapshots = []
        for row in rows:
            tickers, severity_score = _parse_payload(row["payload_json"])
            snapshots.append({
                "scan_ts": row["scan_ts"],
                "leader_count": row["leader_count"],
                "etf_count": row["etf_count"],
                "alert_fired": bool(row["alert_fired"]),
                "tickers": tickers,
                "severity_score": severity_score,
            })
        return JSONResponse(content={"status": "success", "snapshots": snapshots})
    except Exception as e:
        logger.error(f"ai-contagion/status failed: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()


@api_router.post("/ai-contagion/trigger")
@limiter.limit("4/minute")
async def trigger_ai_contagion(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers an AI Contagion scan in the background (useful for testing)."""
    try:
        from scheduler_engine import run_ai_contagion_job
        background_tasks.add_task(run_ai_contagion_job)
        return JSONResponse(content={"status": "success", "message": "AI Contagion scan triggered."})
    except Exception as e:
        logger.error(f"Failed to trigger AI Contagion scan: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/trap-monitor/results")
@limiter.limit("20/minute")
async def get_trap_monitor_results(request: Request):
    """Returns all trap monitor scan results ordered by phase severity (most severe first)."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trap_monitor_results ORDER BY scan_ts DESC")
        rows = [dict(r) for r in cursor.fetchall()]
        from bull_bear_trap_engine import _phase_severity
        rows.sort(key=lambda r: _phase_severity(r.get("phase", "NEUTRAL")))
        return JSONResponse(content={"status": "success", "results": rows})
    except Exception as e:
        logger.error("trap-monitor/results failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()


@api_router.post("/trap-monitor/run")
@limiter.limit("4/minute")
async def run_trap_monitor(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a Trap Monitor scan in the background."""
    try:
        from scheduler_engine import run_trap_monitor_job
        background_tasks.add_task(run_trap_monitor_job)
        return JSONResponse(content={"status": "success", "message": "Trap Monitor scan triggered."})
    except Exception as e:
        logger.error("Failed to trigger Trap Monitor scan: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/market-regime/current")
@limiter.limit("30/minute")
async def get_market_regime_current(request: Request):
    """Returns the latest HMM price regime state and the most recent regime transition."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, price_hmm_state, price_hmm_label, price_hmm_prob "
            "FROM market_regimes WHERE price_hmm_state IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return JSONResponse(content={"status": "success", "current": None, "last_change": None})

        current = {
            "state": row["price_hmm_state"],
            "label": row["price_hmm_label"],
            "probability": row["price_hmm_prob"],
            "as_of": row["date"],
        }

        # Find the most recent day the label changed
        cursor.execute(
            "SELECT date, price_hmm_label FROM market_regimes "
            "WHERE price_hmm_label IS NOT NULL ORDER BY date DESC LIMIT 60"
        )
        history = cursor.fetchall()
        last_change = None
        current_label = current["label"]
        for i, h in enumerate(history[1:], 1):
            if h["price_hmm_label"] != current_label:
                last_change = {
                    "date": history[i - 1]["date"],
                    "from_label": h["price_hmm_label"],
                    "to_label": current_label,
                }
                break

        return JSONResponse(content={"status": "success", "current": current, "last_change": last_change})
    except Exception as e:
        logger.error("market-regime/current failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()


@api_router.get("/market-stress")
@limiter.limit("30/minute")
async def get_market_stress(request: Request):
    """Returns the latest market-wide Isolation Forest stress score and the last 30 daily values."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, market_stress_score, market_stress_features "
            "FROM market_regimes WHERE market_stress_score IS NOT NULL ORDER BY date DESC LIMIT 30"
        )
        rows = cursor.fetchall()
        if not rows:
            return JSONResponse(content={"status": "success", "current": None, "history": []})

        latest = rows[0]
        try:
            import json as _json
            features = _json.loads(latest["market_stress_features"] or "{}")
        except Exception:
            features = {}

        current = {
            "score": round(float(latest["market_stress_score"]), 4),
            "features": features,
            "date": latest["date"],
        }
        history = [
            {"date": r["date"], "score": round(float(r["market_stress_score"]), 4)}
            for r in reversed(rows)
        ]
        return JSONResponse(content={"status": "success", "current": current, "history": history})
    except Exception as e:
        logger.error("market-stress endpoint failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()


@api_router.get("/market-regime")
@limiter.limit("10/minute")
async def get_market_regime_full(request: Request):
    """Returns full HMM regime history, transition matrix, and per-state statistics."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Current state
        cursor.execute(
            "SELECT date, price_hmm_state, price_hmm_label, price_hmm_prob "
            "FROM market_regimes WHERE price_hmm_state IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        cur_row = cursor.fetchone()
        if not cur_row:
            return JSONResponse(content={"status": "success", "current": None, "history": [], "transition_matrix": None, "regime_stats": {}})

        current = {
            "state": cur_row["price_hmm_state"],
            "label": cur_row["price_hmm_label"],
            "probability": cur_row["price_hmm_prob"],
            "as_of": cur_row["date"],
        }

        # Full history from price_hmm_states
        cursor.execute("SELECT date, state, label, probability FROM price_hmm_states ORDER BY date ASC")
        history = [dict(r) for r in cursor.fetchall()]

        # Last regime change
        last_change = None
        current_label = current["label"]
        for i in range(len(history) - 2, -1, -1):
            if history[i]["label"] != current_label:
                last_change = {
                    "date": history[i + 1]["date"],
                    "from_label": history[i]["label"],
                    "to_label": current_label,
                }
                break

        # Transition matrix (empirical counts from consecutive state pairs)
        n_states = 3
        counts = [[0] * n_states for _ in range(n_states)]
        for i in range(len(history) - 1):
            s_from = history[i]["state"]
            s_to = history[i + 1]["state"]
            if 0 <= s_from < n_states and 0 <= s_to < n_states:
                counts[s_from][s_to] += 1
        transition_matrix = []
        for row_counts in counts:
            total = sum(row_counts)
            transition_matrix.append(
                [round(c / total, 3) if total > 0 else 0.0 for c in row_counts]
            )

        # Regime statistics — join price_hmm_states with SPY close from market_regimes
        cursor.execute(
            "SELECT h.date, h.state, h.label, r.spy_volatility "
            "FROM price_hmm_states h "
            "LEFT JOIN market_regimes r ON h.date = r.date "
            "ORDER BY h.date ASC"
        )
        stat_rows = cursor.fetchall()

        # Also fetch SPY returns from the Parquet cache for mean return calc
        import pandas as pd
        import numpy as np
        from config import HISTORICAL_DIR
        hmm_cache = HISTORICAL_DIR / "SPY_hmm.parquet"
        spy_returns: dict = {}
        if hmm_cache.exists():
            df_spy = pd.read_parquet(hmm_cache)
            log_ret = np.log(df_spy["Close"] / df_spy["Close"].shift(1)).dropna()
            spy_returns = {d.strftime("%Y-%m-%d"): float(v) for d, v in log_ret.items()}

        regime_stats: dict = {}
        for label in ("Bull", "Chop", "Crash"):
            label_rows = [r for r in stat_rows if r["label"] == label]
            days = len(label_rows)
            vols = [r["spy_volatility"] for r in label_rows if r["spy_volatility"] is not None]
            rets = [spy_returns[r["date"]] for r in label_rows if r["date"] in spy_returns]
            regime_stats[label] = {
                "days": days,
                "mean_daily_return": round(float(np.mean(rets)), 5) if rets else None,
                "mean_vol": round(float(np.mean(vols)), 2) if vols else None,
            }

        return JSONResponse(content={
            "status": "success",
            "current": current,
            "last_change": last_change,
            "history": history,
            "transition_matrix": transition_matrix,
            "regime_stats": regime_stats,
        })
    except Exception as e:
        logger.error("market-regime full endpoint failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()


@api_router.post("/market-regime/run")
@limiter.limit("4/minute")
async def run_market_regime_now(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers the HMM price regime calculation in the background."""
    try:
        from regime_engine import run_price_regime_hmm
        background_tasks.add_task(run_price_regime_hmm)
        return JSONResponse(content={"status": "success", "message": "HMM regime calculation triggered."})
    except Exception as e:
        logger.error("market-regime/run failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


class StressTestRequest(BaseModel):
    account_id: str = "all"
    scenario_id: str
    custom_drop: Optional[float] = None


@api_router.get("/stress-test/scenarios")
@limiter.limit("30/minute")
async def get_stress_test_scenarios(request: Request):
    """Returns the list of available stress-test scenarios."""
    try:
        from stress_engine import SCENARIOS
        out = {}
        for key, sc in SCENARIOS.items():
            out[key] = {k: v for k, v in sc.items() if v is not None}
        return JSONResponse(content={"status": "success", "scenarios": out})
    except Exception as e:
        logger.error("stress-test/scenarios failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.post("/stress-test/run")
@limiter.limit("10/minute")
async def run_stress_test(request: Request, body: StressTestRequest):
    """Applies a beta-adjusted scenario shock to the portfolio and returns a monetary impact report."""
    try:
        from stress_engine import run_stress_test as _run
        result = _run(
            account_id=body.account_id,
            scenario_id=body.scenario_id,
            custom_drop=body.custom_drop,
        )
        return JSONResponse(content={"status": "success", "result": result})
    except (ValueError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except Exception as e:
        logger.error("stress-test/run failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/smgb-prediction")
@limiter.limit("10/minute")
async def get_smgb_prediction(request: Request):
    """Returns SMGB.L predicted morning open based on US close prices. Prices in GBX (pence)."""
    try:
        from smgb_predictor import run_smgb_prediction
        result = run_smgb_prediction()
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("smgb-prediction failed: %s", e)
        return JSONResponse(content={"status": "error", "error": str(e), "predicted_price": None})


@api_router.get("/smgb-accuracy")
@limiter.limit("10/minute")
async def get_smgb_accuracy(request: Request):
    """Returns historical SMGB.L prediction accuracy: last 60 rows and summary stats."""
    try:
        from database import get_smgb_accuracy
        return JSONResponse(content=get_smgb_accuracy())
    except Exception as e:
        logger.error("smgb-accuracy failed: %s", e)
        return JSONResponse(content={"rows": [], "summary": {}, "error": str(e)})


class EtfConstituentItem(BaseModel):
    ticker: str
    weight: float


class EtfPredictorConfigBody(BaseModel):
    name: str
    etf_ticker: str
    constituents: List[EtfConstituentItem]
    enabled: Optional[bool] = True
    auto_schedule: Optional[bool] = False
    pre_run_time: Optional[str] = "13:30"
    post_run_time: Optional[str] = "22:00"


class EtfValidateBody(BaseModel):
    etf_ticker: str
    constituents: List[EtfConstituentItem]


def _normalise_constituents(items: List[EtfConstituentItem]) -> List[dict]:
    total = sum(i.weight for i in items)
    if total <= 0:
        return []
    return [{"ticker": i.ticker.upper().strip(), "weight": i.weight / total} for i in items]


@api_router.post("/etf-predictors/validate")
@limiter.limit("10/minute")
async def validate_etf_predictor_config(request: Request, body: EtfValidateBody):
    try:
        etf_ticker = body.etf_ticker.upper().strip()
        etf_info = yahoo_engine.get_ticker_info(etf_ticker)
        etf_result = {
            "ticker": etf_ticker,
            "valid": etf_info is not None,
            "name": (etf_info.get("longName") or etf_info.get("shortName", "")) if etf_info else None,
        }

        constituent_results = []
        total_weight = 0.0
        for item in body.constituents:
            t = item.ticker.upper().strip()
            info = yahoo_engine.get_ticker_info(t)
            constituent_results.append({
                "ticker": t,
                "weight": item.weight,
                "valid": info is not None,
                "name": (info.get("longName") or info.get("shortName", "")) if info else None,
            })
            total_weight += item.weight

        return JSONResponse(content={
            "status": "success",
            "etf": etf_result,
            "constituents": constituent_results,
            "total_weight": round(total_weight, 4),
            "weight_ok": abs(total_weight - 100.0) < 1.0 or abs(total_weight - 1.0) < 0.01,
        })
    except Exception as e:
        logger.error("validate_etf_predictor_config failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/etf-predictors")
@limiter.limit("20/minute")
async def list_etf_predictors(request: Request):
    try:
        from database import get_etf_predictor_configs
        configs = get_etf_predictor_configs()
        return JSONResponse(content={"status": "success", "configs": configs})
    except Exception as e:
        logger.error("list_etf_predictors failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.post("/etf-predictors")
@limiter.limit("10/minute")
async def create_etf_predictor(request: Request, body: EtfPredictorConfigBody):
    try:
        from database import create_etf_predictor_config
        from scheduler_engine import register_etf_predictor_jobs
        if not body.constituents:
            return JSONResponse(status_code=422, content={"status": "error", "message": "At least one constituent required."})
        constituents = _normalise_constituents(body.constituents)
        if not constituents:
            return JSONResponse(status_code=422, content={"status": "error", "message": "Constituent weights must sum to > 0."})
        config_id = create_etf_predictor_config(
            name=body.name,
            etf_ticker=body.etf_ticker.upper().strip(),
            constituents=constituents,
            enabled=body.enabled,
            auto_schedule=body.auto_schedule,
            pre_run_time=body.pre_run_time,
            post_run_time=body.post_run_time,
        )
        if config_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save config."})
        if body.auto_schedule and body.enabled:
            register_etf_predictor_jobs({
                "id": config_id, "enabled": True, "deleted_at": None,
                "pre_run_time": body.pre_run_time, "post_run_time": body.post_run_time,
            })
        return JSONResponse(content={"status": "success", "message": "Predictor created.", "id": config_id})
    except Exception as e:
        logger.error("create_etf_predictor failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.put("/etf-predictors/{config_id}")
@limiter.limit("10/minute")
async def update_etf_predictor(request: Request, config_id: int, body: EtfPredictorConfigBody):
    try:
        from database import update_etf_predictor_config, get_etf_predictor_config
        from scheduler_engine import register_etf_predictor_jobs, unregister_etf_predictor_jobs
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})
        constituents = _normalise_constituents(body.constituents) if body.constituents else None
        if constituents is not None and not constituents:
            return JSONResponse(status_code=422, content={"status": "error", "message": "Constituent weights must sum to > 0."})
        fields: dict = {
            "name": body.name,
            "etf_ticker": body.etf_ticker.upper().strip(),
            "enabled": body.enabled,
            "auto_schedule": body.auto_schedule,
            "pre_run_time": body.pre_run_time,
            "post_run_time": body.post_run_time,
        }
        if constituents is not None:
            fields["constituents"] = constituents
        update_etf_predictor_config(config_id, **fields)
        unregister_etf_predictor_jobs(config_id)
        if body.auto_schedule and body.enabled:
            register_etf_predictor_jobs({
                "id": config_id, "enabled": True, "deleted_at": None,
                "pre_run_time": body.pre_run_time, "post_run_time": body.post_run_time,
            })
        return JSONResponse(content={"status": "success", "message": "Predictor updated."})
    except Exception as e:
        logger.error("update_etf_predictor %s failed: %s", config_id, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.delete("/etf-predictors/{config_id}")
@limiter.limit("10/minute")
async def delete_etf_predictor(request: Request, config_id: int):
    try:
        from database import soft_delete_etf_predictor_config, get_etf_predictor_config
        from scheduler_engine import unregister_etf_predictor_jobs
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})
        unregister_etf_predictor_jobs(config_id)
        soft_delete_etf_predictor_config(config_id)
        return JSONResponse(content={"status": "success", "message": "Predictor deleted."})
    except Exception as e:
        logger.error("delete_etf_predictor %s failed: %s", config_id, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.post("/etf-predictors/{config_id}/run")
@limiter.limit("5/minute")
async def run_etf_predictor(request: Request, config_id: int, background_tasks: BackgroundTasks):
    try:
        from database import get_etf_predictor_config
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})

        def _bg():
            try:
                from etf_predictor_engine import run_prediction
                from scheduler_engine import log_sched_notification
                result = run_prediction(config_id)
                if result.get("status") != "success":
                    log_sched_notification("Warning", f"ETF predictor [{config_id}] run: {result.get('error')}")
                else:
                    ptype = result.get("prediction_type", "next_open")
                    price = result.get("predicted_price")
                    chg = result.get("predicted_change_pct")
                    signal = result.get("signal_source", "")
                    log_sched_notification(
                        "Success",
                        f"ETF predictor [{config_id}] ({ptype}) — "
                        f"{price} ({chg:+.2f}%) | signal: {signal}" if price and chg is not None else
                        f"ETF predictor [{config_id}] prediction complete."
                    )
            except Exception as exc:
                from scheduler_engine import log_sched_notification
                log_sched_notification("Error", f"ETF predictor [{config_id}] run failed: {exc}")

        background_tasks.add_task(_bg)
        return JSONResponse(content={"status": "success", "message": f"ETF predictor {config_id} run initiated."})
    except Exception as e:
        logger.error("run_etf_predictor %s failed: %s", config_id, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.post("/etf-predictors/{config_id}/fill-actuals")
@limiter.limit("5/minute")
async def fill_etf_predictor_actuals(request: Request, config_id: int, background_tasks: BackgroundTasks):
    try:
        from database import get_etf_predictor_config
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})

        def _bg():
            try:
                from etf_predictor_engine import fill_actuals_for_config
                fill_actuals_for_config(config_id)
                from scheduler_engine import log_sched_notification
                log_sched_notification("Success", f"ETF predictor [{config_id}] actuals filled.")
            except Exception as exc:
                from scheduler_engine import log_sched_notification
                log_sched_notification("Error", f"ETF predictor [{config_id}] fill-actuals failed: {exc}")

        background_tasks.add_task(_bg)
        return JSONResponse(content={"status": "success", "message": f"ETF predictor {config_id} fill-actuals initiated."})
    except Exception as e:
        logger.error("fill_etf_predictor_actuals %s failed: %s", config_id, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/etf-predictors/{config_id}/predictions")
@limiter.limit("20/minute")
async def get_etf_predictor_predictions(request: Request, config_id: int):
    try:
        from database import get_etf_accuracy, get_etf_predictor_config
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})
        return JSONResponse(content={"status": "success", **get_etf_accuracy(config_id)})
    except Exception as e:
        logger.error("get_etf_predictor_predictions %s failed: %s", config_id, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/ai-prompt/{ticker}")
async def get_ai_prompt(ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$"), mode: str = "Quantamental Deep-Dive"):
    try:
        ticker = normalize_ticker(ticker)
        engine = AIPromptEngine()
        prompt = engine.generate_prompt(ticker, mode)
        if not prompt:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Stock data not found in local database."})
        return JSONResponse(content={"status": "success", "prompt": prompt})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.post("/watchlist/add")
async def api_watchlist_add(req: TickerRequest):
    engine = GhostfolioSyncEngine()
    added = await asyncio.to_thread(engine.add_to_watchlist, req.ticker)
    if added:
        await asyncio.to_thread(engine.sync_watchlist)
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add to Ghostfolio."})

@api_router.post("/watchlist/remove")
async def api_watchlist_remove(req: TickerRequest):
    engine = GhostfolioSyncEngine()
    removed = await asyncio.to_thread(engine.remove_from_watchlist, req.ticker)
    if removed:
        await asyncio.to_thread(engine.sync_watchlist)
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to remove from Ghostfolio."})

@api_router.post("/data/refresh-single")
async def api_data_refresh_single(req: TickerRequest):
    try:
        update_single_profile(req.ticker)
        data_engine = DataEngine()
        quant_engine = QuantEngine()
        if not data_engine.fetch_and_save_data(req.ticker):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Data fetch failed."})
        quant_engine.analyze_ticker(req.ticker)
        target_list = [req.ticker]
        update_daily_ml_predictions(target_list)
        update_all_tail_risks(target_list)
        update_all_sentiment(target_list)
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.exception("refresh-single failed for %s", req.ticker)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.post("/index/refresh")
async def api_index_refresh(req: TickerRequest):
    ticker = normalize_ticker(req.ticker)
    try:
        fetch_and_save_pulse([ticker])
        data_engine = DataEngine()
        quant_engine = QuantEngine()
        if not data_engine.fetch_and_save_data(ticker):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Data fetch failed."})
        quant_engine.analyze_ticker(ticker)
        target_list = [ticker]
        update_all_tail_risks(target_list)
        update_all_sentiment(target_list)
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.exception("index/refresh failed for %s", ticker)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/options/chain/{ticker}")
@limiter.limit("10/minute")
async def api_options_chain(request: Request, ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
    data = fetch_options_chain(ticker)
    if "error" in data:
        return JSONResponse(status_code=400, content=data)
    return JSONResponse(content=data)

@api_router.post("/options/payoff")
async def api_options_payoff(req: PayoffRequest):
    try:
        legs_dict = [leg.model_dump() for leg in req.legs]
        matrix = calculate_payoff_matrix(legs_dict, req.current_price)
        return JSONResponse(content=matrix)
    except (ValueError, ZeroDivisionError) as e:
        return JSONResponse(status_code=422, content={"status": "error", "message": str(e)})
    except Exception as e:
        logger.exception("Payoff matrix calculation failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/screener-data")
@limiter.limit("20/minute")
async def get_screener_data(request: Request):
    conn = None
    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

        conn = get_connection()
        cursor = conn.cursor()
        query = """
        WITH latest_quant AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        ),
        latest_sentiment AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            WHERE sentiment_score IS NOT NULL
            GROUP BY ticker
        )
        SELECT
            q.ticker,
            COALESCE(p.company_name, s.company_name, m.company_name, q.ticker) as company_name,
            COALESCE(p.sector, s.sector, 'Unclassified') as sector,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            q.date, q.close_price,
            q.volume, q.rsi_14, q.macd_hist, q.sma_50, q.sma_200,
            q.volume_surge, q.bullish_cross,
            q.ml_confidence_score, q.var_95, q.cvar_95, q.atr_pct,
            q.week52_pct,
            qs_sent.sentiment_score,
            s.composite_score, s.overall_signal,
            s.roe, s.peg_ratio, s.trailing_pe, s.debt_to_equity,
            COALESCE(s.next_earnings_date, ev.next_earnings_date) AS next_earnings_date,
            m.is_freetrade, m.freetrade_subtitle, m.freetrade_url,
            COALESCE(p.quote_type, s.quote_type, m.quote_type, 'EQUITY') as quote_type
        FROM quant_signals q
        INNER JOIN latest_quant lq ON q.ticker = lq.ticker AND q.date = lq.max_date
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        LEFT JOIN latest_sentiment ls ON q.ticker = ls.ticker
        LEFT JOIN quant_signals qs_sent
            ON qs_sent.ticker = ls.ticker
            AND qs_sent.date = ls.max_date
            AND qs_sent.sentiment_score IS NOT NULL
        LEFT JOIN earnings_volatility ev ON q.ticker = ev.ticker
        """
        if freetrade_only:
            query += " WHERE m.is_freetrade = 1"

        cursor.execute(query)
        rows = cursor.fetchall()
        today_str = time_engine.now_local().strftime('%Y-%m-%d')
        data = []
        for row in rows:
            r = dict(row)
            r['quality_grade'] = compute_quality_grade(r)
            r['earnings_days'] = _get_earnings_days(r, today_str)
            data.append(r)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.error(f"Failed to fetch screener data: {e}")
        return JSONResponse(status_code=500, content={"error": str(e), "data": []})
    finally:
        if conn:
            conn.close()

@api_router.get("/reports/quality-compounders")
@limiter.limit("10/minute")
async def api_reports_quality_compounders(request: Request):
    try:
        data = get_quality_compounders()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("quality-compounders report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/quality-on-sale")
@limiter.limit("10/minute")
async def api_reports_quality_on_sale(request: Request):
    try:
        data = get_quality_on_sale()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("quality-on-sale report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/garp-tenbaggers")
@limiter.limit("10/minute")
async def api_reports_garp_tenbaggers(request: Request):
    try:
        data = get_garp_tenbaggers()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("garp-tenbaggers report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/sectors")
@limiter.limit("10/minute")
async def api_reports_sectors(request: Request):
    try:
        data = get_sector_trends()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("sectors report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/mean-reversion")
@limiter.limit("10/minute")
async def api_reports_mean_reversion(
    request: Request,
    max_rsi: float = Query(default=30.0, ge=0.0, le=100.0),
    min_sma_distance: float = Query(default=0.0, ge=0.0),
):
    try:
        data = get_mean_reversion_setups(max_rsi=max_rsi, min_sma_distance=min_sma_distance)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("mean-reversion report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/leaders")
@limiter.limit("10/minute")
async def api_reports_leaders(request: Request):
    try:
        data = get_leaders_laggards()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("leaders report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/dividends")
@limiter.limit("10/minute")
async def api_reports_dividends(
    request: Request,
    min_yield: float = Query(default=0.02, ge=0.0, le=1.0),
    min_score: int   = Query(default=50,   ge=0,   le=100),
):
    try:
        data = get_dividend_harvest_setups(min_yield=min_yield, min_score=min_score)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("dividends report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@api_router.get("/freshness")
async def get_data_freshness():
    """Returns model-file age and price-data age, with pre-computed CSS state classes."""
    from constants import (
        FRESHNESS_MODEL_WARN_DAYS, FRESHNESS_MODEL_STALE_DAYS,
        FRESHNESS_PRICES_WARN_DAYS, FRESHNESS_PRICES_STALE_DAYS,
    )
    today = time_engine.now_local().date()

    def _state(days_ago: int, warn: int, stale: int) -> str:
        if days_ago >= stale: return "freshness-stale"
        if days_ago >= warn:  return "freshness-warn"
        return "freshness-fresh"

    # Model file staleness
    model_path = BASE_DIR / "models" / "ml_ensemble.joblib"
    if model_path.exists():
        mtime        = time_engine.to_local(datetime.fromtimestamp(model_path.stat().st_mtime, tz=timezone.utc)).date()
        model_days   = (today - mtime).days
        model_date   = mtime.strftime('%Y-%m-%d')
        model_state  = _state(model_days, FRESHNESS_MODEL_WARN_DAYS, FRESHNESS_MODEL_STALE_DAYS)
    else:
        model_days, model_date, model_state = None, None, "freshness-stale"

    # Price data freshness (latest row in quant_signals)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) AS max_date FROM quant_signals")
        row = cursor.fetchone()
    finally:
        conn.close()

    if row and row["max_date"]:
        prices_d     = datetime.strptime(row["max_date"], "%Y-%m-%d").date()
        prices_days  = (today - prices_d).days
        prices_date  = row["max_date"]
        prices_state = _state(prices_days, FRESHNESS_PRICES_WARN_DAYS, FRESHNESS_PRICES_STALE_DAYS)
    else:
        prices_days, prices_date, prices_state = None, None, "freshness-stale"

    return JSONResponse(content={
        "model_date":    model_date,
        "model_days_ago": model_days,
        "model_state":   model_state,
        "prices_date":   prices_date,
        "prices_days_ago": prices_days,
        "prices_state":  prices_state,
    })


@api_router.get("/xray")
@limiter.limit("10/minute")
async def get_xray_report(request: Request, account_id: str = "all"):
    """
    Returns the full Portfolio X-ray report JSON for the given account scope.
    account_id: "all" for global (all active accounts) or a Ghostfolio account UUID.

    Combines live Ghostfolio holdings/allocations with SQLite-cached risk stats
    (beta, vol, correlation, VaR). The cache is populated by the nightly
    xray_risk_cache_job scheduler job.
    """
    try:
        report = assemble_xray_report(account_id)
        return JSONResponse(content=report)
    except RuntimeError as e:
        logger.warning(f"X-ray report failed for account_id={account_id!r}: {e}")
        return JSONResponse(status_code=503, content={"error": str(e)})
    except Exception as e:
        logger.error(f"X-ray report unexpected error: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal error — check server logs."})


@api_router.post("/xray/trigger")
async def trigger_xray_risk_cache(background_tasks: BackgroundTasks):
    """
    Manually triggers the X-ray risk cache pre-compute job in the background.
    Use this after first setup or after adding new holdings to immediately
    populate the cache without waiting for the scheduled run.
    """
    background_tasks.add_task(run_xray_risk_cache_job)
    return JSONResponse(content={
        "status": "queued",
        "message": "X-ray risk cache job queued. Check system notifications for completion.",
    })


@api_router.get("/news-feed")
async def get_news_feed(
    request: Request,
    source: str = Query(default="all", pattern=r"^(all|portfolio|watchlist|both)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if source == "all":
            cursor.execute(
                "SELECT COUNT(*) as total FROM news_articles"
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                """SELECT id, article_id, ticker, company_name, source_list,
                          headline, summary, full_text, body_fetched,
                          url, publisher, published_at, fetched_at,
                          sentiment_score, sentiment_label
                   FROM news_articles
                   ORDER BY published_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) as total FROM news_articles WHERE source_list IN (?, 'both')",
                (source,),
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                """SELECT id, article_id, ticker, company_name, source_list,
                          headline, summary, full_text, body_fetched,
                          url, publisher, published_at, fetched_at,
                          sentiment_score, sentiment_label
                   FROM news_articles
                   WHERE source_list IN (?, 'both')
                   ORDER BY published_at DESC
                   LIMIT ? OFFSET ?""",
                (source, limit, offset),
            )
        articles = [dict(row) for row in cursor.fetchall()]
        return JSONResponse(content={"articles": articles, "total": total})
    except Exception as e:
        logger.error(f"GET /api/news-feed failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()


@api_router.post("/news-feed/run-now")
async def run_news_feed_now(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_news_feed_job)
    return JSONResponse(content={
        "status": "success",
        "message": "News feed fetch queued. New articles will appear shortly.",
    })


# ---------------------------------------------------------------------------
# Intraday Dip Radar endpoints
# ---------------------------------------------------------------------------

@api_router.post("/intraday-monitor/add")
async def intraday_monitor_add(req: TickerRequest):
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    today = time_engine.now_local().date().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO intraday_monitors (ticker, date_added, is_active, activated_by)
               VALUES (?, ?, 1, 'user')
               ON CONFLICT(ticker) DO UPDATE SET
                   date_added   = excluded.date_added,
                   is_active    = 1,
                   activated_by = 'user'""",
            (ticker, today),
        )
        conn.commit()
        _cur_row = conn.execute(
            "SELECT currency FROM stock_signals WHERE ticker = ?", (ticker,)
        ).fetchone()
        _currency = _cur_row["currency"] if _cur_row else ""
    finally:
        conn.close()
    engine = IntradayBottomEngine()
    await asyncio.to_thread(engine.arm_alert, ticker)
    # One-time notification confirming monitoring is active for this session
    from database import log_notification
    from zoneinfo import ZoneInfo
    from datetime import time as _t
    _exch = time_engine.ticker_exchange(ticker, _currency)
    _params = time_engine.reset_cron_trigger_params(_exch)
    _reset_dt = datetime.combine(time_engine.now_local().date(), _t(_params["hour"], _params["minute"]), tzinfo=ZoneInfo(_params["timezone"]))
    _reset_str = time_engine.fmt_time(_reset_dt)
    log_notification("DipRadar", f"🎯 Dip Radar enabled for {ticker} — scanning every 2 min until {_reset_str}. You will be notified if a bottoming zone is detected.")
    return JSONResponse(content={"status": "ok", "ticker": ticker})


@api_router.post("/intraday-monitor/remove")
async def intraday_monitor_remove(req: TickerRequest):
    ticker = req.ticker.upper().strip()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE intraday_monitors SET is_active = 0 WHERE ticker = ?", (ticker,)
        )
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(content={"status": "ok", "ticker": ticker})


@api_router.get("/intraday-monitor/list")
async def intraday_monitor_list():
    today = time_engine.now_local().date().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, date_added, is_active FROM intraday_monitors WHERE date_added = ? ORDER BY ticker",
            (today,),
        ).fetchall()
        return JSONResponse(content={"monitors": [dict(r) for r in rows]})
    finally:
        conn.close()


@api_router.get("/intraday-monitor/analysis/{ticker}")
async def intraday_monitor_analysis(ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM intraday_monitor_results WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
        if not row:
            return JSONResponse(content=None)
        data = dict(row)
        data["reasons"] = json.loads(data.get("reasons_json") or "[]")
        data.pop("reasons_json", None)
        if "vol_climax" in data and data["vol_climax"] is not None:
            data["vol_climax"] = bool(data["vol_climax"])
        return JSONResponse(content=data)
    finally:
        conn.close()


@api_router.get("/intraday-monitor/summary")
async def intraday_monitor_summary():
    today = time_engine.now_local().date().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.ticker, m.date_added, m.activated_by,
                   r.scan_ts, r.current_price, r.reversal_score,
                   r.is_bottoming, r.rsi, r.bb_lower,
                   r.vwap, r.vwap_lower, r.vwap_deviation, r.vol_climax
            FROM intraday_monitors m
            LEFT JOIN intraday_monitor_results r ON m.ticker = r.ticker
            WHERE m.date_added = ? AND m.is_active = 1
            ORDER BY COALESCE(r.reversal_score, -1) DESC
            """,
            (today,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("vol_climax") is not None:
                d["vol_climax"] = bool(d["vol_climax"])
            result.append(d)
        return JSONResponse(content={"monitors": result, "session_date": today})
    finally:
        conn.close()


@api_router.get("/intraday-chart/{ticker}")
async def get_intraday_chart(ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
    """Return freshly rendered intraday chart HTML for a given ticker."""
    ticker = ticker.upper()
    s1 = s2 = None
    df_macro = pd.DataFrame()

    # Derive exchange metadata for timezone + delay
    conn_meta = get_connection()
    try:
        row = conn_meta.execute(
            "SELECT currency FROM stock_signals WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()
        currency = row["currency"] if row else "USD"
    except Exception:
        currency = "USD"
    finally:
        conn_meta.close()

    mkt_tz = _intraday_market_tz(ticker, currency)
    delay_min = _EXCHANGE_DELAYS.get(currency, 0)

    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")
        if not df_macro.empty and len(df_macro) > 1:
            prev_day = df_macro.iloc[-2]
            P = (prev_day['High'] + prev_day['Low'] + prev_day['Close']) / 3
            s1 = P * 2 - prev_day['High']
            s2 = P - (prev_day['High'] - prev_day['Low'])
    except Exception:
        pass

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
    except FileNotFoundError:
        html = "<div class='intraday-placeholder'><span class='intraday-placeholder-icon'>📭</span><span class='intraday-placeholder-label'>No intraday data yet</span></div>"
        return JSONResponse(content={"html": html})
    except Exception:
        html = "<div class='intraday-placeholder intraday-placeholder--error'><span class='intraday-placeholder-icon'>⚠️</span><span class='intraday-placeholder-label'>Intraday data unavailable</span></div>"
        return JSONResponse(content={"html": html})

    live_pattern_name = live_pattern_tooltip = live_pattern_score = None
    try:
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            curr_pseudo = pd.Series({
                'Open': df_intraday['Open'].iloc[0],
                'High': df_intraday['High'].max(),
                'Low': df_intraday['Low'].min(),
                'Close': df_intraday['Close'].iloc[-1],
            })
            live_patterns = get_candlestick_patterns(df_macro.iloc[-2], df_macro.iloc[-1], curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]
    except Exception:
        pass

    html = create_intraday_chart(
        df_intraday, ticker, s1=s1, s2=s2,
        live_pattern_name=live_pattern_name,
        live_pattern_tooltip=live_pattern_tooltip,
        live_pattern_score=live_pattern_score,
        include_plotlyjs=False,
        market_tz=mkt_tz,
        data_delay_minutes=delay_min,
    )
    return JSONResponse(content={"html": html})


@api_router.post("/intraday-chart/refresh")
async def refresh_intraday_chart(req: TickerRequest):
    """Fetch fresh intraday data from Yahoo Finance, persist to parquet, return re-rendered chart HTML."""
    ticker = req.ticker.upper()

    conn_meta = get_connection()
    try:
        row = conn_meta.execute(
            "SELECT currency FROM stock_signals WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()
        currency = row["currency"] if row else "USD"
    except Exception:
        currency = "USD"
    finally:
        conn_meta.close()

    try:
        with yahoo_engine._lock:
            yahoo_engine._cache.pop(f"intraday:{ticker}:1d:5m:", None)
        result = yahoo_engine.get_intraday([ticker], period="1d", interval="5m")
        df_fetched = result.get(ticker, pd.DataFrame())
        if not df_fetched.empty:
            if df_fetched.index.tz is not None:
                df_fetched.index = df_fetched.index.tz_convert(None)
            df_fetched.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine="pyarrow")
    except Exception:
        pass

    s1 = s2 = None
    df_macro = pd.DataFrame()
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")
        if not df_macro.empty and len(df_macro) > 1:
            prev_day = df_macro.iloc[-2]
            P = (prev_day["High"] + prev_day["Low"] + prev_day["Close"]) / 3
            s1 = P * 2 - prev_day["High"]
            s2 = P - (prev_day["High"] - prev_day["Low"])
    except Exception:
        pass

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
    except FileNotFoundError:
        html = "<div class='intraday-placeholder'><span class='intraday-placeholder-icon'>📭</span><span class='intraday-placeholder-label'>No intraday data yet</span></div>"
        return JSONResponse(content={"html": html})
    except Exception:
        html = "<div class='intraday-placeholder intraday-placeholder--error'><span class='intraday-placeholder-icon'>⚠️</span><span class='intraday-placeholder-label'>Intraday data unavailable</span></div>"
        return JSONResponse(content={"html": html})

    mkt_tz = _intraday_market_tz(ticker, currency)
    delay_min = _EXCHANGE_DELAYS.get(currency, 0)

    live_pattern_name = live_pattern_tooltip = live_pattern_score = None
    try:
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            curr_pseudo = pd.Series({
                "Open": df_intraday["Open"].iloc[0],
                "High": df_intraday["High"].max(),
                "Low": df_intraday["Low"].min(),
                "Close": df_intraday["Close"].iloc[-1],
            })
            live_patterns = get_candlestick_patterns(df_macro.iloc[-2], df_macro.iloc[-1], curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]
    except Exception:
        pass

    html = create_intraday_chart(
        df_intraday, ticker, s1=s1, s2=s2,
        live_pattern_name=live_pattern_name,
        live_pattern_tooltip=live_pattern_tooltip,
        live_pattern_score=live_pattern_score,
        include_plotlyjs=False,
        market_tz=mkt_tz,
        data_delay_minutes=delay_min,
    )
    return JSONResponse(content={"html": html})