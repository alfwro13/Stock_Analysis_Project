# main.py
import uvicorn
import json
import pandas as pd
import yfinance as yf
import os
import signal
import time
import subprocess
from datetime import datetime, timedelta
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
from crash_engine import CrashEngine

from config import (
    PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, 
    PORT, BASE_CURRENCY, SECRETS_PATH, load_config
)
from database import get_connection, init_db
from quant_signals import QuantEngine
from data_engine import DataEngine
from visuals import create_macro_chart, create_intraday_chart
from ghostfolio_sync import GhostfolioSyncEngine

# --- Background Task Scheduler Setup ---
scheduler = BackgroundScheduler()

def trigger_sentiment_report():
    run_nextcloud_alert()

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

    # 5. Intraday Crash Engine
    crash_cfg = scheduling.get("CRASH_ALERTS", {})
    if crash_cfg.get("ENABLED"):
        freq = crash_cfg.get("FREQUENCY", "mon-fri")
        start_time = crash_cfg.get("START_TIME", "09:30")
        end_time = crash_cfg.get("END_TIME", "16:00")
        interval_mins = int(crash_cfg.get("INTERVAL_MINUTES", 10))
        
        try:
            start_h, _ = map(int, start_time.split(':'))
            end_h, _ = map(int, end_time.split(':'))
            
            # Using CronTrigger with bounded hours creates a tight window for execution
            scheduler.add_job(
                run_crash_engine,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{interval_mins}"),
                id='crash_alerts_job'
            )
            print(f"[SCHEDULER] Intraday Crash Scan scheduled for {freq} between {start_time}-{end_time} every {interval_mins} mins.")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Crash Alerts: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
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
    """Safely reads local JSON files."""
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

def run_crash_engine():
    CrashEngine().run()

def run_update_pipeline():
    print("\n--- BACKGROUND UPDATE INITIATED ---")
    DataEngine().update_all_data()
    QuantEngine().run_all()
    print("--- BACKGROUND UPDATE COMPLETE ---\n")

def run_ghostfolio_sync():
    sync_engine = GhostfolioSyncEngine()
    sync_engine.run_full_sync()

@app.post("/api/update")
async def trigger_update(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_update_pipeline)
    return {"status": "success"}

@app.post("/api/sync-ghostfolio")
async def trigger_ghostfolio_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ghostfolio_sync)
    return {"status": "success"}

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
    sentiment_html = get_sentiment_html()
    unread_count = get_unread_count()
    return templates.TemplateResponse(
        request=request, name="market_sentiment.html", 
        context={"sentiment_html": sentiment_html, "unread_count": unread_count}
    )

@app.post("/api/test-sentiment-alert")
def test_sentiment_alert():
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
    unread_count = get_unread_count()
    return templates.TemplateResponse(request=request, name="glossary.html", context={"unread_count": unread_count})

@app.get("/", response_class=RedirectResponse)
async def home():
    return RedirectResponse(url="/portfolio")

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    
    portfolio_json = get_json_data(PORTFOLIO_PATH)
    portfolio_tickers = [data.get("ticker") for key, data in portfolio_json.items() if "ticker" in data]
    portfolio_data = [row for row in db_rows if row['ticker'] in portfolio_tickers]
    
    unread_count = get_unread_count()
    
    return templates.TemplateResponse(
        request=request, name="portfolio.html", 
        context={"portfolio": portfolio_data, "global_updated": global_updated, "embed": embed, "unread_count": unread_count}
    )

@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    
    watchlist_json = get_json_data(WATCHLIST_PATH)
    watchlist_tickers = watchlist_json.get("watchlist", [])
    watchlist_data = [row for row in db_rows if row['ticker'] in watchlist_tickers]
    
    unread_count = get_unread_count()
    
    return templates.TemplateResponse(
        request=request, name="watchlist.html", 
        context={"watchlist": watchlist_data, "global_updated": global_updated, "embed": embed, "unread_count": unread_count}
    )

@app.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals WHERE ticker = ?", (ticker,))
    stock_data = cursor.fetchone()
    
    if stock_data:
        stock_data = dict(stock_data)
    
    top_holdings = []
    sector_weightings = []
    if stock_data and stock_data.get('top_holdings'):
        try: top_holdings = json.loads(stock_data['top_holdings'])
        except: pass
        
    if stock_data and stock_data.get('sector_weightings'):
        try: sector_weightings = json.loads(stock_data['sector_weightings'])
        except: pass

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

    portfolio_json = get_json_data(PORTFOLIO_PATH)
    user_asset = next((data for key, data in portfolio_json.items() if data.get("ticker") == ticker), None)
    
    portfolio_math = None
    if user_asset and stock_data and stock_data['current_price']:
        buy_price = user_asset.get('buy_price', 0)
        shares = user_asset.get('shares', 0)
        
        stock_currency = stock_data['currency']
        exchange_rate = 1.0
        
        if stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp', 'GBP']:
            try:
                fx_ticker = f"{BASE_CURRENCY}{stock_currency}=X"
                fx_data = yf.Ticker(fx_ticker).history(period="1d")
                if not fx_data.empty:
                    exchange_rate = fx_data['Close'].iloc[-1]
            except Exception as e:
                print(f"[WARNING] Could not fetch exchange rate for {fx_ticker}: {e}")
        
        if buy_price > 0 and shares > 0:
            buy_price = buy_price * exchange_rate
            if user_asset.get('price_in_pence', False):
                buy_price = buy_price * 100
                
            current_value = shares * stock_data['current_price']
            cost_basis = shares * buy_price
            pnl = current_value - cost_basis
            pnl_pct = (pnl / cost_basis) * 100
            
            portfolio_math = {
                "shares": shares,
                "buy_price": round(buy_price, 4),
                "current_value": round(current_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2)
            }

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
        macro_html = f"<p>Chart Data Unavailable: {e}</p>"

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        s1_val = price_action['s1'] if price_action else None
        s2_val = price_action['s2'] if price_action else None
        intraday_html = create_intraday_chart(df_intraday, ticker, s1=s1_val, s2=s2_val)
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
            "unread_count": unread_count
        }
    )

if __name__ == "__main__":
    print(f"Starting Quantamental Web Server on Port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)