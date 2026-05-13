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
from database import get_connection
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, reload_scheduler
from ghostfolio_sync import GhostfolioSyncEngine
from market_pulse import get_market_pulse
from sentiment_engine import run_nextcloud_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine

api_router = APIRouter(prefix="/api")

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
async def api_market_pulse(request: PulseRequest):
    """API endpoint to fetch live index data AND requested asset prices."""
    config_data = load_config()
    refresh_rate = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    pulse_data = get_market_pulse(request.tickers, refresh_rate=refresh_rate)
    return JSONResponse(content={"status": "success", "data": pulse_data})

@api_router.get("/market-pulse")
async def api_market_pulse_get():
    pulse_data = get_market_pulse()
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

@api_router.post("/notifications/mark-read")
async def mark_notifications_read():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE system_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        conn.close()
        return JSONResponse(content={"status": "success"})
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