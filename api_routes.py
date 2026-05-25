# api_routes.py
import os
import io
import glob
import json
import time
import signal
import subprocess
import pandas as pd
import logging
import requests
import yfinance as yf
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import load_config, SECRETS_PATH, DATA_DIR, BASE_DIR
from database import get_connection, get_universe_tickers
# Assumed run_freetrade_sync is managed by the scheduler engine alongside ghostfolio
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, run_freetrade_sync, reload_scheduler, run_sentiment_scan
from ghostfolio_sync import GhostfolioSyncEngine
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse
from sentiment_engine import run_nextcloud_alert, update_all_sentiment
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine
from data_engine import DataEngine
from quant_signals import QuantEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe
from reports_engine import get_sector_trends, get_mean_reversion_setups, get_leaders_laggards, get_dividend_harvest_setups
from options_engine import fetch_options_chain, calculate_payoff_matrix
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
from risk_engine import update_all_tail_risks
from profile_engine import update_single_profile
from tools.network_engine import GLOBAL_IPV6_STATUS
# Import curl_cffi for resilient IPv6 socket testing
from curl_cffi import requests as cffi_requests

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
        print("[WARNING] Universe is empty. Please trigger a Universe Update first.")
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
        from seed_macro_calendar import seed_calendar
        from macro_calendar_engine import update_macro_calendar
        from macro_data_engine import update_macro_indicators
        from macro_ai_engine import MacroAIEngine
        
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
        from macro_calendar_engine import update_macro_calendar
        from macro_data_engine import update_macro_indicators
        from macro_ai_engine import MacroAIEngine
        
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
        conn.close()
        
        background_tasks.add_task(bg_execute_universe_quant_scan_subset, [r[0] for r in records])
        return JSONResponse(content={
            "status": "success", 
            "message": f"Successfully sideloaded {len(records)} assets from '{request.filename}' into the local Market Universe."
        })
    except Exception as e:
        logger.error(f"Fatal error executing CSV parser for {request.filename}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Fatal error executing CSV parser: {str(e)}"})

def execute_restart():
    time.sleep(2)
    os.kill(os.getpid(), signal.SIGTERM)

@api_router.post("/update")
async def trigger_update(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_update_pipeline)
    return {"status": "success"}

@api_router.post("/sync-ghostfolio")
async def trigger_ghostfolio_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ghostfolio_sync)
    return {"status": "success"}

@api_router.post("/trigger-freetrade-sync")
async def trigger_freetrade_sync_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_freetrade_sync)
    return JSONResponse(content={
        "status": "success",
        "message": "Freetrade synchronization initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/ghostfolio/discover")
async def trigger_discovery():
    engine = GhostfolioSyncEngine()
    if not engine.authenticate():
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to authenticate with Ghostfolio."})
    accounts = engine.discover_accounts()
    if accounts:
        reload_scheduler()
        return JSONResponse(content={"status": "success", "message": f"Successfully discovered {len(accounts)} active accounts."})
    else:
        return JSONResponse(status_code=500, content={"status": "error", "message": "No accounts discovered or network error occurred."})

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
def test_sentiment_alert():
    success, msg = run_nextcloud_alert()
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@api_router.post("/test-earnings-alert")
def test_earnings_alert():
    success, msg = run_earnings_alert()
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@api_router.post("/test-insider-alert")
def test_insider_alert():
    success, msg = run_insider_alert()
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

@api_router.post("/system/git-pull")
async def git_pull_update():
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15)
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
async def save_settings(request: Request):
    try:
        new_config = await request.json()
        with open(SECRETS_PATH, 'w') as f:
            json.dump(new_config, f, indent=4)
        reload_scheduler()
        return JSONResponse(content={"status": "success", "message": "Settings saved successfully."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/notifications/latest")
async def get_latest_notifications(last_id: int = 0):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, message_type, message_text, timestamp FROM system_notifications WHERE id > ? ORDER BY id ASC", 
            (last_id,)
        )
        rows = cursor.fetchall()
        conn.close()
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

@api_router.post("/notifications/mark-read")
async def mark_notifications_read():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE system_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        conn.close()
        return JSONResponse(content={"status": "success", "message": "All notifications marked as read."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.post("/notifications/purge")
async def purge_all_notifications():
    """
    Purges all historical notifications from the SQLite database.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM system_notifications")
        conn.commit()
        conn.close()
        return JSONResponse(content={"status": "success", "message": "All notifications purged successfully."})
    except Exception as e:
        logger.error(f"Failed to purge notifications: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@api_router.get("/ai-prompt/{ticker}")
async def get_ai_prompt(ticker: str, mode: str = "Quantamental Deep-Dive"):
    try:
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
    if engine.add_to_watchlist(req.ticker):
        engine.sync_watchlist()
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add to Ghostfolio."})

@api_router.post("/watchlist/remove")
async def api_watchlist_remove(req: TickerRequest):
    engine = GhostfolioSyncEngine()
    if engine.remove_from_watchlist(req.ticker):
        engine.sync_watchlist()
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to remove from Ghostfolio."})

@api_router.post("/data/refresh-single")
async def api_data_refresh_single(req: TickerRequest):
    update_single_profile(req.ticker)
    data_engine = DataEngine()
    quant_engine = QuantEngine()
    if data_engine.fetch_and_save_data(req.ticker):
        quant_engine.analyze_ticker(req.ticker)
        target_list = [req.ticker]
        update_daily_ml_predictions(target_list)
        update_all_tail_risks(target_list)
        update_all_sentiment(target_list)
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Data fetch failed."})

@api_router.get("/options/chain/{ticker}")
async def api_options_chain(ticker: str):
    data = fetch_options_chain(ticker)
    if "error" in data:
        return JSONResponse(status_code=400, content=data)
    return JSONResponse(content=data)

@api_router.post("/options/payoff")
async def api_options_payoff(req: PayoffRequest):
    legs_dict = [leg.model_dump() for leg in req.legs]
    matrix = calculate_payoff_matrix(legs_dict, req.current_price)
    return JSONResponse(content=matrix)

@api_router.get("/screener-data")
async def get_screener_data():
    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
        
        conn = get_connection()
        cursor = conn.cursor()
        query = """
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
            q.ml_confidence_score, q.var_95, q.cvar_95,
                (SELECT qs2.sentiment_score
                FROM quant_signals qs2
                WHERE qs2.ticker = q.ticker
                AND qs2.sentiment_score IS NOT NULL
                ORDER BY qs2.date DESC
                LIMIT 1) as sentiment_score,
            s.composite_score,
            m.is_freetrade, m.freetrade_subtitle, m.freetrade_url, COALESCE(p.quote_type, s.quote_type, 'EQUITY') as quote_type
        FROM quant_signals q
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = q.ticker)
        """
        if freetrade_only:
            query += " AND m.is_freetrade = 1"
            
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        data = [dict(row) for row in rows]
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.error(f"Failed to fetch screener data: {e}")
        return JSONResponse(status_code=500, content={"error": str(e), "data": []})

@api_router.get("/reports/sectors")
async def api_reports_sectors():
    data = get_sector_trends()
    return JSONResponse(content={"data": data})

@api_router.get("/reports/mean-reversion")
async def api_reports_mean_reversion(max_rsi: float = 30.0, min_sma_distance: float = 0.0):
    data = get_mean_reversion_setups(max_rsi=max_rsi, min_sma_distance=min_sma_distance)
    return JSONResponse(content={"data": data})

@api_router.get("/reports/leaders")
async def api_reports_leaders():
    data = get_leaders_laggards()
    return JSONResponse(content={"data": data})

@api_router.get("/reports/dividends")
async def api_reports_dividends(min_yield: float = 0.02, min_score: int = 50):
    data = get_dividend_harvest_setups(min_yield=min_yield, min_score=min_score)
    return JSONResponse(content={"data": data})