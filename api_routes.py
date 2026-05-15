# api_routes.py
import os
import io
import glob
import json
import time
import signal
import subprocess
import pandas as pd
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import load_config, SECRETS_PATH, DATA_DIR
from database import get_connection, get_universe_tickers
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, reload_scheduler
from ghostfolio_sync import GhostfolioSyncEngine
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse
from sentiment_engine import run_nextcloud_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine
from data_engine import DataEngine
from quant_signals import QuantEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe

# --- REPORTS ENGINE IMPORTS ---
from reports_engine import get_sector_trends, get_mean_reversion_setups, get_leaders_laggards

# --- OPTIONS SANDBOX IMPORTS ---
from options_engine import fetch_options_chain, calculate_payoff_matrix

# --- AI PREDICTION ENGINE IMPORTS ---
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill

api_router = APIRouter(prefix="/api")

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


def bg_execute_quant_scan():
    """Background task wrapper for the heavy Quant engine (Portfolio/Watchlist)."""
    engine = DataEngine()
    tickers = engine.get_all_tickers()
    run_daily_quant_scan(tickers)

def bg_execute_earnings_scan():
    """Background task wrapper for the heavy Earnings Volatility engine."""
    engine = DataEngine()
    tickers = engine.get_all_tickers()
    run_earnings_vol_scan(tickers)

def bg_execute_universe_quant_scan():
    """Background task wrapper for scanning the entire 4,000+ Universe."""
    tickers = get_universe_tickers()
    if not tickers:
        print("[WARNING] Universe is empty. Please trigger a Universe Update first.")
        return
    run_daily_quant_scan(tickers, scan_type='universe')

def bg_init_ml_pipeline():
    """Background task wrapper for initializing the AI engine end-to-end."""
    run_historical_backfill()
    train_global_ml_model()
    
    tickers = get_universe_tickers()
    if not tickers:
        engine = DataEngine()
        tickers = engine.get_all_tickers()
        
    if tickers:
        update_daily_ml_predictions(tickers)


@api_router.post("/ml/init-pipeline")
async def trigger_ml_init_endpoint(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the end-to-end ML initialization."""
    background_tasks.add_task(bg_init_ml_pipeline)
    return JSONResponse(content={
        "status": "success", 
        "message": "ML Pipeline initialized in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-quant-scan")
async def trigger_quant_scan_endpoint(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the daily Portfolio/Watchlist Quant Screener."""
    background_tasks.add_task(bg_execute_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Portfolio Quant Scan initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-earnings-scan")
async def trigger_earnings_scan_endpoint(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the Options Implied Volatility calculations."""
    background_tasks.add_task(bg_execute_earnings_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Earnings Volatility Scan initiated in the background. Check System Notifications for progress updates."
    })

@api_router.post("/trigger-universe-update")
async def trigger_universe_update_endpoint(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger a scrape of the Nasdaq FTP server."""
    background_tasks.add_task(update_market_universe)
    return JSONResponse(content={
        "status": "success", 
        "message": "Market Universe update initiated in the background. Check System Notifications for progress."
    })

@api_router.post("/trigger-universe-quant-scan")
async def trigger_universe_quant_scan_endpoint(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger a full scan of the 4,000+ Universe tickers."""
    background_tasks.add_task(bg_execute_universe_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Full Universe Quant Scan initiated in the background. This will take over an hour. Check System Notifications for progress."
    })

@api_router.get("/universe/imports/list")
async def list_importable_csvs():
    """Scans the designated imports directory in tools/data/imports for CSV files."""
    try:
        # Ensure directory exists to prevent crashes on fresh installs
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        
        # Use pathlib globbing to extract valid CSVs from the tools directory
        files = [f.name for f in IMPORT_DIR.glob("*.csv")]
        return JSONResponse(content={"status": "success", "files": files})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Failed to list import directory: {str(e)}"})

@api_router.post("/universe/import/server")
async def import_server_csv(request: ImportRequest):
    """
    API endpoint to securely read a CSV file directly from the tools/data/imports directory,
    parse it using Pandas, and bulk-load it into SQLite.
    """
    if not request.filename.endswith('.csv'):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid file type. Only .csv files are supported."})
    
    try:
        file_path = IMPORT_DIR / request.filename
        
        if not file_path.exists():
            return JSONResponse(status_code=404, content={"status": "error", "message": f"File '{request.filename}' not found on server at {file_path}."})
            
        df = pd.read_csv(file_path)
        
        # Enforce exact column structures 
        required_cols = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country']
        for col in required_cols:
            if col not in df.columns:
                return JSONResponse(status_code=400, content={"status": "error", "message": f"Malformed CSV. Missing required column: {col}"})
        
        # Scrub unprocessable rows
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
                current_time
            ))
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # Optimized SQLite bulk insert utilizing transaction wrapping
        cursor.executemany('''
            INSERT OR REPLACE INTO market_universe 
            (ticker, company_name, sector, industry, country, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)
        
        conn.commit()
        conn.close()
        
        return JSONResponse(content={
            "status": "success", 
            "message": f"Successfully sideloaded {len(records)} assets from '{request.filename}' into the local Market Universe."
        })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Fatal error executing CSV parser: {str(e)}"})


def execute_restart():
    """Background task to wait 2 seconds, then kill the Python process."""
    time.sleep(2)
    os.kill(os.getpid(), signal.SIGTERM)

@api_router.post("/update")
async def trigger_update(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the full market data update."""
    background_tasks.add_task(run_update_pipeline)
    return {"status": "success"}

@api_router.post("/sync-ghostfolio")
async def trigger_ghostfolio_sync(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the Ghostfolio synchronization."""
    background_tasks.add_task(run_ghostfolio_sync)
    return {"status": "success"}

@api_router.post("/ghostfolio/discover")
async def trigger_discovery():
    """Triggers the Ghostfolio API to discover all active accounts and update config.json."""
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
    """API endpoint to fetch live index data AND requested asset prices instantaneously from DB."""
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

@api_router.post("/system/git-pull")
async def git_pull_update():
    """Executes a git pull to fetch the latest code from GitHub."""
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
    """API endpoint to poll for new system notifications for browser alerts."""
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
    """API endpoint to mark all notifications as read."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE system_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        conn.close()
        return JSONResponse(content={"status": "success", "message": "All notifications marked as read."})
    except Exception as e:
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

# --- WATCHLIST MANAGEMENT ENDPOINTS ---
@api_router.post("/watchlist/add")
async def api_watchlist_add(req: TickerRequest):
    """Adds a ticker to Ghostfolio and synchronizes the local JSON engine."""
    engine = GhostfolioSyncEngine()
    if engine.add_to_watchlist(req.ticker):
        engine.sync_watchlist()
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add to Ghostfolio."})

@api_router.post("/watchlist/remove")
async def api_watchlist_remove(req: TickerRequest):
    """Removes a ticker from Ghostfolio and synchronizes the local JSON engine."""
    engine = GhostfolioSyncEngine()
    if engine.remove_from_watchlist(req.ticker):
        engine.sync_watchlist()
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to remove from Ghostfolio."})

# --- MANUAL DATA REFRESH ENDPOINT ---
@api_router.post("/data/refresh-single")
async def api_data_refresh_single(req: TickerRequest):
    """Synchronously fetches fresh market data and evaluates the quant models for a single ticker."""
    data_engine = DataEngine()
    quant_engine = QuantEngine()
    
    if data_engine.fetch_and_save_data(req.ticker):
        quant_engine.analyze_ticker(req.ticker)
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Data fetch failed."})


# --- OPTIONS SANDBOX ENDPOINTS ---
@api_router.get("/options/chain/{ticker}")
async def api_options_chain(ticker: str):
    """Fetches the options chain for the Sandbox UI."""
    data = fetch_options_chain(ticker)
    if "error" in data:
        return JSONResponse(status_code=400, content=data)
    return JSONResponse(content=data)

@api_router.post("/options/payoff")
async def api_options_payoff(req: PayoffRequest):
    """Calculates the P&L matrix for the provided strategy legs."""
    legs_dict = [leg.model_dump() for leg in req.legs]
    matrix = calculate_payoff_matrix(legs_dict, req.current_price)
    return JSONResponse(content=matrix)


# --- MARKET SCREENER API (4000+ UNIVERSE) ---
@api_router.get("/screener-data")
async def get_screener_data():
    """
    Fetches the 4000+ rows of quantitative signals from the overnight Market Universe scan.
    Optimized SQLite join directly converting to a JSON array for DataTables.js rendering.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # UPDATED QUERY: Added composite_score for frontend UI ML divergence correlation, and Country support
        query = """
        SELECT 
            q.ticker, 
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name, 
            COALESCE(p.sector, s.sector, 'Unclassified') as sector, 
            COALESCE(p.country, m.country, 'US') as country,
            q.date, q.close_price, 
            q.volume, q.rsi_14, q.macd_hist, q.sma_50, q.sma_200, 
            q.volume_surge, q.bullish_cross,
            q.ml_confidence_score, q.sentiment_score, q.var_95, q.cvar_95,
            s.composite_score
        FROM quant_signals q
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = q.ticker)
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        data = [dict(row) for row in rows]
        
        return JSONResponse(content={"data": data})
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "data": []})

# --- ADVANCED MARKET REPORTS ENDPOINTS ---
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