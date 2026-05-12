# main.py
import uvicorn
import json
import pandas as pd
import yfinance as yf
import os
import signal
import time
import subprocess
import threading
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from contextlib import asynccontextmanager

from sentiment_engine import get_sentiment_html, run_nextcloud_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from intraday_orchestrator import IntradayOrchestrator
from maintenance_engine import MaintenanceEngine
from ai_engine import AIPromptEngine
from market_pulse import get_market_pulse

from config import (
    PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, 
    PORT, BASE_CURRENCY, SECRETS_PATH, SERVER_URL, load_config
)
from database import get_connection, init_db
from quant_signals import QuantEngine, get_candlestick_patterns
from data_engine import DataEngine
from visuals import create_macro_chart, create_intraday_chart
from ghostfolio_sync import GhostfolioSyncEngine

# --- Global Foreign Exchange (FX) Cache ---
# Stores FX pairs (e.g., "USDGBP=X": 0.79) to prevent slow API calls on every page refresh
fx_cache = {}

# --- Background Task Scheduler Setup ---
scheduler = BackgroundScheduler()
task_lock = threading.Lock()

# --- Pulse Request Schema ---
class PulseRequest(BaseModel):
    tickers: Optional[List[str]] = []

def trigger_sentiment_report():
    """Triggered by the scheduler to run the Nextcloud Market Sentiment alert."""
    run_nextcloud_alert()

def run_intraday_orchestrator():
    """Executes the unified high-frequency intraday scan (Crash + Moonshot)."""
    IntradayOrchestrator().run()

def run_maintenance_engine():
    """Executes the background database and file system maintenance."""
    MaintenanceEngine().run()

def reload_scheduler():
    """Reads the latest config.json and updates APScheduler dynamically."""
    print("[SCHEDULER] Reloading scheduled jobs from configuration...")
    scheduler.remove_all_jobs()
    
    config = load_config()
    notifications = config.get("NOTIFICATIONS", {})
    scheduling = config.get("SCHEDULING", {})
    
    # 1. Market Sentiment Job
    sentiment_cfg = notifications.get("MARKET_SENTIMENT", {})
    if sentiment_cfg.get("ENABLED"):
        time_str = sentiment_cfg.get("TIME", "09:30")
        freq = sentiment_cfg.get("FREQUENCY", "mon-fri")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                trigger_sentiment_report,
                CronTrigger(day_of_week=freq, hour=hour, minute=minute),
                id='market_sentiment_job'
            )
            print(f"[SCHEDULER] Market Sentiment Job scheduled for {freq} at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Market Sentiment: {e}")

    # 2. Earnings Alerts Job
    earnings_cfg = notifications.get("EARNINGS_ALERTS", {})
    if earnings_cfg.get("ENABLED"):
        time_str = earnings_cfg.get("TIME", "08:00")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_earnings_alert,
                CronTrigger(day_of_week='mon-fri', hour=hour, minute=minute),
                id='earnings_alert_job'
            )
            print(f"[SCHEDULER] Earnings Alerts Job scheduled for mon-fri at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Earnings Alerts: {e}")
    
    # 3. Insider Trading Alerts Job
    insider_cfg = notifications.get("INSIDER_TRADING", {})
    if insider_cfg.get("ENABLED_PORTFOLIO") or insider_cfg.get("ENABLED_WATCHLIST"):
        time_str = insider_cfg.get("TIME", "18:00")
        freq = insider_cfg.get("FREQUENCY", "mon-fri")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_insider_alert,
                CronTrigger(day_of_week=freq, hour=hour, minute=minute),
                id='insider_alert_job'
            )
            print(f"[SCHEDULER] Insider Trading Alert Job scheduled for {freq} at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Insider Alerts: {e}")

    # 4. Core System Schedulers (Ghostfolio & Quant Analysis)
    ghost_cfg = scheduling.get("GHOSTFOLIO_SYNC", {})
    if ghost_cfg.get("ENABLED"):
        interval = int(ghost_cfg.get("INTERVAL_HOURS", 0))
        freq = ghost_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_ghostfolio_sync, IntervalTrigger(hours=interval), id='ghostfolio_sync_job')
            print(f"[SCHEDULER] Ghostfolio Sync scheduled every {interval} hours.")
        else:
            time_str = ghost_cfg.get("TIME", "06:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_ghostfolio_sync, 
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute), 
                    id='ghostfolio_sync_job'
                )
                print(f"[SCHEDULER] Ghostfolio Sync scheduled for {freq} at {time_str}")
            except Exception as e:
                print(f"[ERROR] Failed to schedule Ghostfolio Sync: {e}")

    quant_cfg = scheduling.get("QUANT_ANALYSIS", {})
    if quant_cfg.get("ENABLED"):
        interval = int(quant_cfg.get("INTERVAL_HOURS", 0))
        freq = quant_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_update_pipeline, IntervalTrigger(hours=interval), id='quant_analysis_job')
            print(f"[SCHEDULER] Quant Analysis scheduled every {interval} hours.")
        else:
            time_str = quant_cfg.get("TIME", "18:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_update_pipeline, 
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute), 
                    id='quant_analysis_job'
                )
                print(f"[SCHEDULER] Quant Analysis scheduled for {freq} at {time_str}")
            except Exception as e:
                print(f"[ERROR] Failed to schedule Quant Analysis: {e}")

    # 5. Unified Intraday Orchestrator (Replaces independent Crash & Moonshot jobs)
    crash_cfg = scheduling.get("CRASH_ALERTS", {})
    moon_cfg = scheduling.get("MOONSHOT_ALERTS", {})
    
    crash_enabled = crash_cfg.get("ENABLED", False)
    moon_enabled = moon_cfg.get("ENABLED", False)
    
    if crash_enabled or moon_enabled:
        # Use Crash config as the master bound, fallback to Moonshot if Crash is disabled
        active_cfg = crash_cfg if crash_enabled else moon_cfg
        
        freq = active_cfg.get("FREQUENCY", "mon-fri")
        start_time = active_cfg.get("START_TIME", "09:30")
        end_time = active_cfg.get("END_TIME", "16:00")
        interval_mins = int(active_cfg.get("INTERVAL_MINUTES", 10))
        
        try:
            start_h, _ = map(int, start_time.split(':'))
            end_h, _ = map(int, end_time.split(':'))
            
            scheduler.add_job(
                run_intraday_orchestrator,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{interval_mins}"),
                id='intraday_orchestrator_job'
            )
            print(f"[SCHEDULER] Unified Intraday Orchestrator scheduled for {freq} between {start_time}-{end_time} every {interval_mins} mins.")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Intraday Orchestrator: {e}")

    # 6. System Maintenance Engine
    maint_cfg = scheduling.get("MAINTENANCE", {})
    if maint_cfg.get("ENABLED", True):
        time_str = maint_cfg.get("TIME", "02:00")
        day_of_week = maint_cfg.get("DAY_OF_WEEK", "sun")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_maintenance_engine,
                CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
                id='maintenance_job'
            )
            print(f"[SCHEDULER] DB/File Maintenance scheduled for {day_of_week} at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Maintenance Job: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the FastAPI application."""
    init_db()
    reload_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="Quantamental Dashboard", lifespan=lifespan)

# Mount the assets directory to serve the logos statically
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

templates = Jinja2Templates(directory="templates")

def get_json_data(filepath):
    """Safely reads local JSON files and handles Missing File errors gracefully."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def get_unread_count():
    """Helper function to fetch the unread notifications count for the navigation bar."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM system_notifications WHERE is_read = 0")
        count = cursor.fetchone()['cnt']
        conn.close()
        return count
    except Exception:
        return 0

def run_update_pipeline():
    """Executes the heavy data ingestion and mathematical quant modeling."""
    if not task_lock.acquire(blocking=False):
        print("[WARNING] System is currently busy. Skipping Update Analysis to prevent clash.")
        return
    try:
        print("\n--- BACKGROUND UPDATE INITIATED ---")
        DataEngine().update_all_data()
        QuantEngine().run_all()
        print("--- BACKGROUND UPDATE COMPLETE ---\n")
    finally:
        task_lock.release()

def run_ghostfolio_sync():
    """Executes the Ghostfolio API Sync to extract account holdings."""
    if not task_lock.acquire(blocking=False):
        print("[WARNING] System is currently busy. Skipping Ghostfolio Sync to prevent clash.")
        return
    try:
        sync_engine = GhostfolioSyncEngine()
        sync_engine.run_full_sync()
    finally:
        task_lock.release()

@app.post("/api/update")
async def trigger_update(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the full market data update."""
    background_tasks.add_task(run_update_pipeline)
    return {"status": "success"}

@app.post("/api/sync-ghostfolio")
async def trigger_ghostfolio_sync(background_tasks: BackgroundTasks):
    """API endpoint to manually trigger the Ghostfolio synchronization."""
    background_tasks.add_task(run_ghostfolio_sync)
    return {"status": "success"}

@app.post("/api/ghostfolio/discover")
async def trigger_discovery():
    """Triggers the Ghostfolio API to discover all active accounts and update config.json."""
    engine = GhostfolioSyncEngine()
    if not engine.authenticate():
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to authenticate with Ghostfolio."})
    
    accounts = engine.discover_accounts()
    if accounts:
        # Reload scheduler/config so the UI sees the new accounts immediately
        reload_scheduler()
        return JSONResponse(content={"status": "success", "message": f"Successfully discovered {len(accounts)} active accounts."})
    else:
        return JSONResponse(status_code=500, content={"status": "error", "message": "No accounts discovered or network error occurred."})


# --- DYNAMIC POST ROUTE FOR MARKET PULSE ---
@app.post("/api/market-pulse")
async def api_market_pulse(request: PulseRequest):
    """API endpoint to fetch live index data AND requested asset prices."""
    pulse_data = get_market_pulse(request.tickers)
    return JSONResponse(content={"status": "success", "data": pulse_data})

# Fallback GET route to preserve functionality
@app.get("/api/market-pulse")
async def api_market_pulse_get():
    pulse_data = get_market_pulse()
    return JSONResponse(content={"status": "success", "data": pulse_data.get("indexes", [])})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Renders the global settings GUI."""
    config_data = load_config()
    unread_count = get_unread_count()
    return templates.TemplateResponse(
        request=request, name="settings.html", 
        context={"config": config_data, "unread_count": unread_count}
    )

@app.get("/market-sentiment", response_class=HTMLResponse)
async def market_sentiment_page(request: Request):
    """Renders the Fear & Greed Index vs S&P 500 correlation chart."""
    sentiment_html = get_sentiment_html()
    unread_count = get_unread_count()
    return templates.TemplateResponse(
        request=request, name="market_sentiment.html", 
        context={"sentiment_html": sentiment_html, "unread_count": unread_count}
    )

@app.post("/api/test-sentiment-alert")
def test_sentiment_alert():
    """Triggered by the GUI to manually test the Nextcloud Market Sentiment Pipeline."""
    success, msg = run_nextcloud_alert()
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@app.post("/api/test-earnings-alert")
def test_earnings_alert():
    """Triggered by the GUI to manually test the Earnings Pipeline."""
    success, msg = run_earnings_alert()
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@app.post("/api/test-insider-alert")
def test_insider_alert():
    """Triggered by the GUI to manually test the Insider Trading Pipeline."""
    success, msg = run_insider_alert()
    if success: return JSONResponse(content={"status": "success", "message": msg})
    else: return JSONResponse(status_code=500, content={"status": "error", "message": msg})

@app.post("/api/system/git-pull")
async def git_pull_update():
    """Executes a git pull to fetch the latest code from GitHub."""
    try:
        # Execute git pull command
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            return JSONResponse(content={"status": "success", "message": f"Update successful. Please restart the service if required.\n\n{result.stdout}"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": f"Git Pull Failed:\n{result.stderr}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

def execute_restart():
    """Background task to wait 2 seconds, then kill the Python process."""
    time.sleep(2)
    # Sending SIGTERM allows FastAPI/Uvicorn to shut down gracefully
    os.kill(os.getpid(), signal.SIGTERM)

@app.post("/api/system/restart")
async def restart_system(background_tasks: BackgroundTasks):
    """Tells the app to exit. Systemd will automatically restart it."""
    background_tasks.add_task(execute_restart)
    return JSONResponse(content={
        "status": "success", 
        "message": "Restart signal sent. The dashboard will be back online in ~5-10 seconds."
    })

@app.post("/api/settings")
async def save_settings(request: Request):
    """Receives JSON from the frontend, updates config.json, and reloads the scheduler."""
    try:
        new_config = await request.json()
        with open(SECRETS_PATH, 'w') as f:
            json.dump(new_config, f, indent=4)
            
        # Immediately push new scheduling rules to APScheduler
        reload_scheduler()
        
        return JSONResponse(content={"status": "success", "message": "Settings saved successfully."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    """Renders the persistent notification center."""
    conn = get_connection()
    cursor = conn.cursor()
    # Fetch latest 100 notifications
    cursor.execute("SELECT * FROM system_notifications ORDER BY timestamp DESC LIMIT 100")
    notifications = cursor.fetchall()
    unread_count = get_unread_count()
    conn.close()
    
    return templates.TemplateResponse(
        request=request, name="notifications.html", 
        context={"notifications": notifications, "unread_count": unread_count}
    )

@app.post("/api/notifications/mark-read")
async def mark_notifications_read():
    """Marks all unread notifications as read."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE system_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        conn.close()
        return JSONResponse(content={"status": "success", "message": "Notifications marked as read."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@app.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    """Renders the educational glossary."""
    unread_count = get_unread_count()
    return templates.TemplateResponse(request=request, name="glossary.html", context={"unread_count": unread_count})

@app.get("/", response_class=RedirectResponse)
async def home():
    """Redirects the root URL to the portfolio dashboard."""
    return RedirectResponse(url="/portfolio")

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, account_id: str = "all", embed: bool = False):
    """
    Renders the portfolio table. Now respects the hierarchical Macro/Micro structure 
    and filters the displayed tickers based on the user's selected account context.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    conn.close()

    # Load Account Configurations for the UI Dropdown
    config_data = load_config()
    active_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    discovered_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("discovered", [])
    
    account_options = [{"id": "all", "name": "Global (All Accounts)"}]
    for acc in discovered_accounts:
        if acc["id"] in active_accounts:
            account_options.append({"id": acc["id"], "name": acc["name"]})
    
    portfolio_json = get_json_data(PORTFOLIO_PATH)
    
    # Filter tickers based on the chosen account context
    portfolio_tickers = []
    for key, data in portfolio_json.items():
        if "ticker" in data:
            if account_id == "all":
                portfolio_tickers.append(data["ticker"])
            else:
                for acc in data.get("accounts", []):
                    if acc["id"] == account_id:
                        portfolio_tickers.append(data["ticker"])
                        break
                        
    portfolio_data = []
    
    # Secure Baseline Math dictionary
    summary_math = {"value": 0.0, "cost": 0.0, "pnl": 0.0, "pnl_pct": 0.0}
    
    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in portfolio_tickers:
            # Safely unpack JSON setup tags for the UI renderer
            if row_dict.get('setup_tags'):
                try: row_dict['setup_tags_list'] = json.loads(row_dict['setup_tags'])
                except: row_dict['setup_tags_list'] = []
            else:
                row_dict['setup_tags_list'] = []
            
            portfolio_data.append(row_dict)
            
            # --- Live Math for Summary Row (FX Corrected) ---
            asset = next((d for d in portfolio_json.values() if d.get("ticker") == row_dict['ticker']), None)
            if asset and row_dict['current_price']:
                shares = 0
                
                # Note: buy_price is ALREADY in BASE_CURRENCY natively from Ghostfolio's 'investment' key
                buy_price_base = 0 
                
                if account_id == "all":
                    shares = asset.get('global_shares', 0)
                    buy_price_base = asset.get('global_buy_price', 0)
                else:
                    for acc in asset.get('accounts', []):
                        if acc['id'] == account_id:
                            shares = acc.get('shares', 0)
                            buy_price_base = acc.get('buy_price', 0)
                            break
                            
                # Cost is purely the quantity multiplied by the base currency buy price
                cost_in_base = shares * buy_price_base
                
                # Market Value Extraction
                native_price = row_dict['current_price']
                stock_currency = row_dict['currency']
                
                exchange_rate = 1.0
                if stock_currency == 'GBp' and BASE_CURRENCY == 'GBP':
                    exchange_rate = 0.01  # Special LSE Math
                elif stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp']:
                    # We are converting FROM native TO base currency (e.g. USD to GBP)
                    pair = f"{stock_currency}{BASE_CURRENCY}=X"
                    if pair not in fx_cache:
                        try:
                            fx_data = yf.Ticker(pair).history(period="1d")
                            if not fx_data.empty:
                                fx_cache[pair] = fx_data['Close'].iloc[-1]
                            else:
                                fx_cache[pair] = 1.0
                        except Exception:
                            fx_cache[pair] = 1.0
                    exchange_rate = fx_cache[pair]
                
                # Final Base Currency Value
                val_in_base = (shares * native_price) * exchange_rate
                
                summary_math["value"] += val_in_base
                summary_math["cost"] += cost_in_base

    # Calculate final P&L logic for the Summary Row
    if summary_math["cost"] > 0:
        summary_math["pnl"] = summary_math["value"] - summary_math["cost"]
        summary_math["pnl_pct"] = (summary_math["pnl"] / summary_math["cost"]) * 100
        
        # Format the numbers (e.g., 10,567.67) and append the Base Currency
        formatted_summary = {
            "value": f"{summary_math['value']:,.2f} {BASE_CURRENCY}",
            "cost": f"{summary_math['cost']:,.2f} {BASE_CURRENCY}",
            "pnl": f"{'+' if summary_math['pnl'] > 0 else ''}{summary_math['pnl']:,.2f} {BASE_CURRENCY}",
            "pnl_pct": f"{summary_math['pnl_pct']:.2f}",
            "is_positive": summary_math["pnl"] > 0
        }
    else:
        formatted_summary = None
    
    unread_count = get_unread_count()
    
    return templates.TemplateResponse(
        request=request, name="portfolio.html", 
        context={
            "portfolio": portfolio_data, 
            "global_updated": global_updated, 
            "embed": embed, 
            "unread_count": unread_count,
            "account_options": account_options,
            "selected_account": account_id,
            "summary_math": formatted_summary,
            "config": config_data
        }
    )

@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, embed: bool = False):
    """Renders the watchlist table."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    conn.close()
    
    config_data = load_config()
    watchlist_json = get_json_data(WATCHLIST_PATH)
    watchlist_tickers = watchlist_json.get("watchlist", [])
    
    watchlist_data = []
    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in watchlist_tickers:
            # Safely unpack JSON setup tags
            if row_dict.get('setup_tags'):
                try: row_dict['setup_tags_list'] = json.loads(row_dict['setup_tags'])
                except: row_dict['setup_tags_list'] = []
            else:
                row_dict['setup_tags_list'] = []
                
            watchlist_data.append(row_dict)
            
    unread_count = get_unread_count()
    
    return templates.TemplateResponse(
        request=request, name="watchlist.html", 
        context={
            "watchlist": watchlist_data, 
            "global_updated": global_updated, 
            "embed": embed, 
            "unread_count": unread_count,
            "config": config_data
        }
    )

@app.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str, embed: bool = False):
    """Renders the deep-dive fundamental and technical analysis view for a specific stock."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals WHERE ticker = ?", (ticker,))
    stock_data = cursor.fetchone()
    
    if stock_data:
        stock_data = dict(stock_data)
    conn.close()
    
    config_data = load_config()
    
    # Process ETF/Mutual Fund specific payloads
    top_holdings = []
    sector_weightings = []
    if stock_data and stock_data.get('top_holdings'):
        try: top_holdings = json.loads(stock_data['top_holdings'])
        except: pass
        
    if stock_data and stock_data.get('sector_weightings'):
        try: sector_weightings = json.loads(stock_data['sector_weightings'])
        except: pass

    # Earnings Volatility Checker
    days_to_earnings = None
    volatility_date = None
    if stock_data and stock_data['next_earnings_date'] and stock_data['next_earnings_date'] != 'Unknown':
        try:
            e_date = datetime.strptime(stock_data['next_earnings_date'], '%Y-%m-%d').date()
            today = datetime.now().date()
            days_to_earnings = (e_date - today).days
            volatility_date = (e_date - timedelta(days=7)).strftime('%Y-%m-%d')
        except Exception:
            pass

    # Portfolio Math Extraction & Processing
    portfolio_json = get_json_data(PORTFOLIO_PATH)
    user_asset = next((data for key, data in portfolio_json.items() if data.get("ticker") == ticker), None)
    
    portfolio_math = None
    if user_asset and stock_data and stock_data['current_price']:
        
        # Determine cross-currency exchange rate to show individual asset in its NATIVE currency
        stock_currency = stock_data['currency']
        exchange_rate = 1.0
        
        if stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp', 'GBP']:
            try:
                # E.g., GBPUSD=X (Converts Base to Native)
                fx_ticker = f"{BASE_CURRENCY}{stock_currency}=X"
                fx_data = yf.Ticker(fx_ticker).history(period="1d")
                if not fx_data.empty:
                    exchange_rate = fx_data['Close'].iloc[-1]
            except Exception as e:
                print(f"[WARNING] Could not fetch exchange rate for {fx_ticker}: {e}")
        
        # Helper function to process P&L cleanly
        def calculate_pnl(shares, buy_price_base):
            if shares <= 0: return None
            
            # Ghostfolio gives us Base (e.g. GBP). We multiply by Exchange Rate to get Native (e.g. USD).
            bp_adj = buy_price_base * exchange_rate
            
            # Undo pence division if Native is GBp
            if user_asset.get('price_in_pence', False):
                bp_adj *= 100
                
            current_value = shares * stock_data['current_price']
            cost_basis = shares * bp_adj
            pnl = current_value - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
            
            return {
                "shares": round(shares, 4),
                "buy_price": round(bp_adj, 4),
                "current_value": round(current_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2)
            }
            
        # 1. Process Global Macro Math
        global_math = calculate_pnl(user_asset.get('global_shares', 0), user_asset.get('global_buy_price', 0))
        
        # 2. Process Individual Micro Ledgers
        account_maths = []
        for acc in user_asset.get('accounts', []):
            acc_m = calculate_pnl(acc.get('shares', 0), acc.get('buy_price', 0))
            if acc_m:
                acc_m["name"] = acc.get("name", "Unknown Account")
                account_maths.append(acc_m)
                
        if global_math:
            portfolio_math = {
                "global": global_math,
                "accounts": account_maths
            }

    # Technical Analysis Chart Generation
    price_action = None
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")
        df_sp500 = pd.read_parquet(HISTORICAL_DIR / "SP500_BASELINE.parquet")
        macro_html = create_macro_chart(df_macro, df_sp500, ticker)
        
        if not df_macro.empty:
            last_day = df_macro.iloc[-1]
            prev_day = df_macro.iloc[-2] if len(df_macro) > 1 else last_day
            last_21 = df_macro.tail(21)

            P = (prev_day['High'] + prev_day['Low'] + prev_day['Close']) / 3
            s1 = (P * 2) - prev_day['High']
            s2 = P - (prev_day['High'] - prev_day['Low'])

            price_action = {
                "day_low": last_day['Low'], 
                "day_high": last_day['High'],
                "month_low": last_21['Low'].min(), 
                "month_high": last_21['High'].max(),
                "s1": s1, 
                "s2": s2
            }
    except Exception as e:
        df_macro = pd.DataFrame()
        macro_html = f"<p>Chart Data Unavailable: {e}</p>"

    # Process Intraday Data and Live Pattern Detection
    live_pattern_name = None
    live_pattern_tooltip = None
    live_pattern_score = None

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        
        # Build a pseudo-daily candle to evaluate live formations against the last 2 closed days
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            prev2_day = df_macro.iloc[-2]
            prev1_day = df_macro.iloc[-1]
            
            # Construct current live candle metrics
            curr_pseudo = pd.Series({
                'Open': df_intraday['Open'].iloc[0],
                'High': df_intraday['High'].max(),
                'Low': df_intraday['Low'].min(),
                'Close': df_intraday['Close'].iloc[-1]
            })
            
            # Run algorithmic scan against live data using the 3-day hierarchy
            live_patterns = get_candlestick_patterns(prev2_day, prev1_day, curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]

        s1_val = price_action['s1'] if price_action else None
        s2_val = price_action['s2'] if price_action else None
        
        intraday_html = create_intraday_chart(
            df_intraday, ticker, s1=s1_val, s2=s2_val,
            live_pattern_name=live_pattern_name,
            live_pattern_tooltip=live_pattern_tooltip,
            live_pattern_score=live_pattern_score
        )
    except Exception:
        intraday_html = "<p>Intraday data unavailable.</p>"
        
    unread_count = get_unread_count()
        
    return templates.TemplateResponse(
        request=request, name="stock_detail.html", 
        context={
            "stock": stock_data, 
            "top_holdings": top_holdings,
            "sector_weightings": sector_weightings,
            "macro_html": macro_html, 
            "intraday_html": intraday_html,
            "portfolio_math": portfolio_math,
            "days_to_earnings": days_to_earnings,   
            "volatility_date": volatility_date,
            "price_action": price_action,
            "unread_count": unread_count,
            "embed": embed,
            "config": config_data
        }
    )

@app.get("/api/ai-prompt/{ticker}")
async def get_ai_prompt(ticker: str, mode: str = "Quantamental Deep-Dive"):
    """
    API endpoint that interfaces with the dedicated AI Prompt Engine.
    Compiles database, technical, and portfolio data into an LLM-ready prompt.
    """
    try:
        engine = AIPromptEngine()
        prompt = engine.generate_prompt(ticker, mode)
        
        if not prompt:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Stock data not found in local database."})
            
        return JSONResponse(content={"status": "success", "prompt": prompt})
    except Exception as e:
        print(f"[ERROR] AI Prompt generation failed for {ticker}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


if __name__ == "__main__":
    print(f"Starting Quantamental Web Server at {SERVER_URL}:{PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
