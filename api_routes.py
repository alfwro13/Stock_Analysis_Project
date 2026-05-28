# api_routes.py
import asyncio
import os
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
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Query, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import (
    load_config, 
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
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, run_freetrade_sync, reload_scheduler, run_sentiment_scan, run_index_scraper, run_fundamentals_profiler, run_universe_deep_sync_job
from ghostfolio_sync import GhostfolioSyncEngine
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse
from sentiment_engine import run_nextcloud_alert, update_all_sentiment
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine
from data_engine import DataEngine
from utils import normalize_ticker
from quant_signals import QuantEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe
from reports_engine import get_sector_trends, get_mean_reversion_setups, get_leaders_laggards, get_dividend_harvest_setups, get_quality_compounders, get_garp_tenbaggers
from options_engine import fetch_options_chain, calculate_payoff_matrix
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
from risk_engine import update_all_tail_risks
from profile_engine import count_pending_profiles, get_profiler_queue_breakdown, update_single_profile
from tools.network_engine import GLOBAL_IPV6_STATUS
# Import curl_cffi for resilient IPv6 socket testing
from curl_cffi import requests as cffi_requests
from seed_macro_calendar import seed_calendar
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from macro_ai_engine import MacroAIEngine

# Configure logger
logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")

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
    discovered: Optional[List[str]] = None
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

class NotificationsConfig(BaseModel):
    MARKET_SENTIMENT: Optional[NotificationItemConfig] = None
    EARNINGS_ALERTS: Optional[NotificationItemConfig] = None
    INSIDER_TRADING: Optional[NotificationItemConfig] = None
    CRASH_ALERTS: Optional[NotificationItemConfig] = None
    MOONSHOT_ALERTS: Optional[NotificationItemConfig] = None

class FreetradeMappingsConfig(BaseModel):
    US_MICS: Optional[List[str]] = None
    EXCHANGES: Optional[dict] = None

class SettingsConfig(BaseModel):
    SERVER_URL: Optional[str] = None
    GHOSTFOLIO_URL: Optional[str] = None
    API_TOKEN: Optional[str] = None
    FRED_API_KEY: Optional[str] = None
    YAHOO_IPV6_ADDRESS: Optional[str] = None
    PORT: Optional[int] = None
    BASE_CURRENCY: Optional[str] = None
    IGNORED_TICKERS: Optional[List[str]] = None
    GHOSTFOLIO_ACCOUNTS: Optional[GhostfolioAccountsConfig] = None
    NEXTCLOUD_URL: Optional[str] = None
    BOT_USERNAME: Optional[str] = None
    APP_PASSWORD: Optional[str] = None
    CONVERSATION_TOKEN: Optional[str] = None
    UI_PREFERENCES: Optional[UIPreferencesConfig] = None
    POSITION_SIZING: Optional[PositionSizingConfig] = None
    FREETRADE_MAPPINGS: Optional[FreetradeMappingsConfig] = None
    SCHEDULING: Optional[SchedulingConfig] = None
    REPORTS_DEFAULTS: Optional[ReportsDefaultsConfig] = None
    NOTIFICATIONS: Optional[NotificationsConfig] = None


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
    """
    Executes the complete daily quant scan including Machine Learning 
    inference and Tail Risk calculations for the active portfolio/watchlist.
    """
    engine = DataEngine()
    tickers = engine.get_all_tickers()
    
    if tickers:
        # 1. Execute Core Technicals & Screener Flags
        run_daily_quant_scan(tickers)
        
        # 2. Execute Machine Learning (XGBoost/RF) Inference
        update_daily_ml_predictions(tickers)
        
        # 3. Calculate Parametric Log-Return VaR & CVaR (Expected Shortfall)
        update_all_tail_risks(tickers)

def bg_execute_earnings_scan():
    engine = DataEngine()
    tickers = engine.get_all_tickers()
    run_earnings_vol_scan(tickers)

def bg_execute_universe_quant_scan():
    tickers = get_universe_tickers()
    if not tickers:
        logger.warning("Universe is empty. Please trigger a Universe Update first.")
        return
    run_daily_quant_scan(tickers, scan_type='universe')

def bg_execute_universe_quant_scan_subset(tickers: List[str]):
    run_daily_quant_scan(tickers, scan_type='sideload')

def bg_execute_ml_inference():
    """Wrapper function to perform the dynamic ML daily inference routing."""
    tickers = get_universe_tickers()
    if not tickers:
        engine = DataEngine()
        tickers = engine.get_all_tickers()
        
    if tickers:
        update_daily_ml_predictions(tickers)

def bg_init_macro_pipeline():
    """Executes full Macro AI initialization: Seeding -> Calendar Sync -> Data Sync -> Training -> Inference."""
    try:
        logger.info("Starting Macro AI Initialization Sequence...")
        
        seed_calendar()
        update_macro_calendar()
        update_macro_indicators() # FETCH THE MACRO INDICATOR DATA
        
        ai_engine = MacroAIEngine()
        ai_engine.train_regime_clustering()
        ai_engine.train_consensus_miss_probability()
        ai_engine.train_volatility_magnitude()
        
        scan_date = datetime.now().strftime('%Y-%m-%d')
        ai_engine.run_macro_inference(scan_date)
        
        # Update config.json to mark initialization as complete
        if SECRETS_PATH.exists():
            with open(SECRETS_PATH, 'r') as f:
                config_data = json.load(f)
                
            if "SCHEDULING" not in config_data:
                config_data["SCHEDULING"] = {}
            if "MACRO_ENGINE" not in config_data["SCHEDULING"]:
                config_data["SCHEDULING"]["MACRO_ENGINE"] = {}
                
            config_data["SCHEDULING"]["MACRO_ENGINE"]["INITIALIZED"] = True
            
            with open(SECRETS_PATH, 'w') as f:
                json.dump(config_data, f, indent=4)
        
        log_notification("Success", "Macro AI Pipeline successfully initialized and trained.")
    except Exception as e:
        logger.error(f"Macro AI Pipeline initialization failed: {e}")
        log_notification("Error", f"Macro AI Pipeline Initialization failed: {e}")

def bg_run_macro_pipeline():
    """Executes standard Macro AI run: Calendar Sync -> Data Sync -> Inference."""
    try:
        logger.info("Starting Macro AI Run Sequence...")
        
        update_macro_calendar()
        update_macro_indicators() # REFRESH THE MACRO INDICATOR DATA
        
        ai_engine = MacroAIEngine()
        scan_date = datetime.now().strftime('%Y-%m-%d')
        ai_engine.run_macro_inference(scan_date)
        
        log_notification("Success", "Macro AI Pipeline executed successfully.")
    except Exception as e:
        logger.error(f"Macro AI Pipeline execution failed: {e}")
        log_notification("Error", f"Macro AI Pipeline execution failed: {e}")

@api_router.post("/macro/init-pipeline")
async def trigger_macro_init_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_init_macro_pipeline)
    return JSONResponse(content={
        "status": "success", 
        "message": "Macro AI Initialization started in the background. Check notifications."
    })

@api_router.post("/macro/run-pipeline")
async def trigger_macro_run_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_run_macro_pipeline)
    return JSONResponse(content={
        "status": "success", 
        "message": "Macro AI Run initiated in the background. Check notifications."
    })

# --- MODULAR ML ENDPOINTS ---
@api_router.post("/ml/trigger-backfill")
async def trigger_ml_backfill_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_historical_backfill)
    return JSONResponse(content={
        "status": "success", 
        "message": "ML Historical Backfill initiated in the background. Check System Notifications."
    })

@api_router.post("/ml/trigger-training")
async def trigger_ml_training_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(train_global_ml_model)
    return JSONResponse(content={
        "status": "success", 
        "message": "Global ML Walk-Forward Training initiated in the background. Check System Notifications."
    })

@api_router.post("/ml/trigger-inference")
async def trigger_ml_inference_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_ml_inference)
    return JSONResponse(content={
        "status": "success", 
        "message": "Daily ML Inference initiated in the background. Check System Notifications."
    })

@api_router.post("/trigger-quant-scan")
async def trigger_quant_scan_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Portfolio Quant Scan initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-earnings-scan")
async def trigger_earnings_scan_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_earnings_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Earnings Volatility Scan initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-universe-update")
async def trigger_universe_update_endpoint(background_tasks: BackgroundTasks):
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
async def trigger_sync_indices_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_index_scraper)
    return JSONResponse(content={
        "status": "success", 
        "message": "Index Constituent scraping initiated in the background. Check System Notifications for progress."
    })

@api_router.post("/universe/sync-profiler")
async def trigger_sync_profiler_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_fundamentals_profiler)
    return JSONResponse(content={
        "status": "success",
        "message": "Fundamentals Profiler initiated in the background. Check System Notifications for progress."
    })

@api_router.post("/universe/deep-sync")
async def trigger_universe_deep_sync_endpoint(background_tasks: BackgroundTasks):
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
async def trigger_universe_quant_scan_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_universe_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Full Universe Quant Scan initiated in the background. This will take over an hour. Check System Notifications for progress."
    })

@api_router.post("/trigger-sentiment-scan")
async def trigger_sentiment_scan_endpoint(background_tasks: BackgroundTasks):
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
    conn = None
    try:
        file_path = IMPORT_DIR / request.filename
        if not file_path.exists():
            return JSONResponse(status_code=404, content={"status": "error", "message": f"File '{request.filename}' not found on server at {file_path}."})
            
        df = pd.read_csv(file_path)
        required_cols = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country', 'exchange']
        for col in required_cols:
            if col not in df.columns:
                return JSONResponse(status_code=400, content={"status": "error", "message": f"Malformed CSV. Missing required column: {col}"})
        
        df = df.dropna(subset=['ticker'])
        records = []
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
async def trigger_update(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_update_pipeline)
    return JSONResponse(content={"status": "success"})

@api_router.post("/sync-ghostfolio")
async def trigger_ghostfolio_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ghostfolio_sync)
    return JSONResponse(content={"status": "success"})

@api_router.post("/trigger-freetrade-sync")
async def trigger_freetrade_sync_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_freetrade_sync)
    return JSONResponse(content={
        "status": "success",
        "message": "Freetrade synchronization initiated in the background. Check System Notifications for progress updates."
    })

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
    loop = asyncio.get_event_loop()
    success, msg = await loop.run_in_executor(None, run_nextcloud_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@api_router.post("/test-earnings-alert")
async def test_earnings_alert():
    loop = asyncio.get_event_loop()
    success, msg = await loop.run_in_executor(None, run_earnings_alert)
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@api_router.post("/test-insider-alert")
async def test_insider_alert():
    loop = asyncio.get_event_loop()
    success, msg = await loop.run_in_executor(None, run_insider_alert)
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
        fail_time_str = datetime.fromtimestamp(GLOBAL_IPV6_STATUS["last_fail_time"]).strftime('%Y-%m-%d %H:%M:%S')
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
        conn.row_factory = sqlite3.Row
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
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            size_mb = round(path.stat().st_size / (1024 * 1024), 2)
            return {"exists": True, "mtime": mtime, "size_mb": size_mb}
            
        ensemble_stats = get_file_stats(ml_model_path)
        
        feature_count = 0
        if feat_stats_path.exists():
            try:
                f_stats = joblib.load(feat_stats_path)
                feature_count = len(f_stats.keys()) if isinstance(f_stats, dict) else 0
            except Exception:
                pass
        
        hmm_states = get_cnt("SELECT COUNT(*) FROM market_regimes WHERE ai_hmm_state IS NOT NULL")
        rf_states = get_cnt("SELECT COUNT(*) FROM macro_calendar WHERE ai_consensus_miss_prob IS NOT NULL")
        
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
                "macro_hmm_outputs": hmm_states, "macro_rf_outputs": rf_states
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
            }
        })
    except Exception as e:
        logger.error(f"Failed to fetch system metrics: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.close()

@api_router.post("/system/git-pull")
async def git_pull_update():
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR))
        if result.returncode == 0:
            return JSONResponse(content={"status": "success", "message": f"Update successful. Please restart the service if required.\n\n{result.stdout}"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": f"Git Pull Failed:\n{result.stderr}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.post("/system/restart")
async def restart_system(background_tasks: BackgroundTasks):
    background_tasks.add_task(execute_restart)
    return JSONResponse(content={"status": "success", "message": "Restart signal sent. The dashboard will be back online in ~5-10 seconds."})

@api_router.post("/settings")
async def save_settings(config: SettingsConfig):
    try:
        with open(SECRETS_PATH, 'w') as f:
            json.dump(config.model_dump(exclude_none=True), f, indent=4)
        reload_scheduler()
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
            "SELECT id, message_type, message_text, timestamp FROM system_notifications WHERE id > ? ORDER BY id ASC",
            (last_id,)
        )
        rows = cursor.fetchall()
        notifications = [
            {
                "id": row["id"],
                "type": row["message_type"],
                "text": row["message_text"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]
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

@api_router.get("/ai-prompt/{ticker}")
async def get_ai_prompt(ticker: str = Path(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$"), mode: str = "Quantamental Deep-Dive"):
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
    loop = asyncio.get_event_loop()
    engine = GhostfolioSyncEngine()
    added = await loop.run_in_executor(None, engine.add_to_watchlist, req.ticker)
    if added:
        await loop.run_in_executor(None, engine.sync_watchlist)
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add to Ghostfolio."})

@api_router.post("/watchlist/remove")
async def api_watchlist_remove(req: TickerRequest):
    loop = asyncio.get_event_loop()
    engine = GhostfolioSyncEngine()
    removed = await loop.run_in_executor(None, engine.remove_from_watchlist, req.ticker)
    if removed:
        await loop.run_in_executor(None, engine.sync_watchlist)
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

@api_router.get("/options/chain/{ticker}")
async def api_options_chain(ticker: str = Path(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
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
async def get_screener_data():
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
            qs_sent.sentiment_score,
            s.composite_score,
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
        """
        if freetrade_only:
            query += " AND m.is_freetrade = 1"
            
        cursor.execute(query)
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.error(f"Failed to fetch screener data: {e}")
        return JSONResponse(status_code=500, content={"error": str(e), "data": []})
    finally:
        if conn:
            conn.close()

@api_router.get("/reports/quality-compounders")
async def api_reports_quality_compounders():
    try:
        data = get_quality_compounders()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("quality-compounders report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/garp-tenbaggers")
async def api_reports_garp_tenbaggers():
    try:
        data = get_garp_tenbaggers()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("garp-tenbaggers report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/sectors")
async def api_reports_sectors():
    try:
        data = get_sector_trends()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("sectors report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/mean-reversion")
async def api_reports_mean_reversion(
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
async def api_reports_leaders():
    try:
        data = get_leaders_laggards()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("leaders report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/reports/dividends")
async def api_reports_dividends(
    min_yield: float = Query(default=0.02, ge=0.0, le=1.0),
    min_score: int   = Query(default=50,   ge=0,   le=100),
):
    try:
        data = get_dividend_harvest_setups(min_yield=min_yield, min_score=min_score)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("dividends report failed")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})