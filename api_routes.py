# api_routes.py
import os
import json
import time
import signal
import subprocess
from typing import List, Optional

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import load_config, SECRETS_PATH
from database import get_connection, get_universe_tickers
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, reload_scheduler
from ghostfolio_sync import GhostfolioSyncEngine
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse
from sentiment_engine import run_nextcloud_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine
from data_engine import DataEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe

# --- OPTIONS SANDBOX IMPORTS ---
from options_engine import fetch_options_chain, calculate_payoff_matrix

api_router = APIRouter(prefix="/api")

# --- OPTIONS SANDBOX PYDANTIC SCHEMAS ---
class OptionLeg(BaseModel):
    type: str
    strike: float
    premium: float
    position: str
    quantity: int = 1

class PayoffRequest(BaseModel):
    current_price: float
    legs: List[OptionLeg]


def bg_execute_quant_scan():
    """Background task wrapper for the heavy Quant engine."""
    tickers = get_universe_tickers()
    if not tickers:
        print("[WARNING] Universe is empty. Please trigger a Universe Update first.")
        return
    run_daily_quant_scan(tickers)

def bg_execute_earnings_scan():
    """Background task wrapper for the heavy Earnings Volatility engine."""
    tickers = get_universe_tickers()
    if not tickers:
        print("[WARNING] Universe is empty. Please trigger a Universe Update first.")
        return
    run_earnings_vol_scan(tickers)

@api_router.post("/trigger-quant-scan")
async def trigger_quant_scan_endpoint(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the heavy overnight Quant Screener calculations."""
    background_tasks.add_task(bg_execute_quant_scan)
    return JSONResponse(content={
        "status": "success", 
        "message": "Quant Scan initiated in the background. Check System Notifications for progress updates."
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

class PulseRequest(BaseModel):
    tickers: Optional[List[str]] = []

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
    
    # Check if any data returned was stale or entirely missing from the DB
    needs_fetch = [item['ticker'] for item in pulse_data['indexes'] + pulse_data['assets'] if item['is_stale']]
    if needs_fetch:
        # Offload the slow Yahoo Finance extraction to a background thread
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