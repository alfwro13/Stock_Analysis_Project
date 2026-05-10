# main.py
import uvicorn
import json
import pandas as pd
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
# Import PORT from config
from config import PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, PORT
from database import get_connection, init_db
from quant_signals import QuantEngine
from data_engine import DataEngine
from visuals import create_macro_chart, create_intraday_chart
from ghostfolio_sync import GhostfolioSyncEngine

app = FastAPI(title="Quantamental Dashboard")
templates = Jinja2Templates(directory="templates")

# Run database setup on server boot
init_db()

def get_json_data(filepath):
    """Safely reads local JSON files."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def run_update_pipeline():
    """Background worker for Yahoo Finance data fetching and math crunching."""
    print("\n--- BACKGROUND UPDATE INITIATED ---")
    DataEngine().update_all_data()
    QuantEngine().run_all()
    print("--- BACKGROUND UPDATE COMPLETE ---\n")

def run_ghostfolio_sync():
    """Background worker to download the latest assets from Ghostfolio."""
    sync_engine = GhostfolioSyncEngine()
    sync_engine.run_full_sync()

@app.post("/api/update")
async def trigger_update(background_tasks: BackgroundTasks):
    """Triggers the heavy Yahoo Finance pipeline."""
    background_tasks.add_task(run_update_pipeline)
    return {"status": "success"}

@app.post("/api/sync-ghostfolio")
async def trigger_ghostfolio_sync(background_tasks: BackgroundTasks):
    """Triggers the Ghostfolio JSON overwrite."""
    background_tasks.add_task(run_ghostfolio_sync)
    return {"status": "success"}

@app.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    """Dedicated educational page."""
    return templates.TemplateResponse(request=request, name="glossary.html", context={})

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Loads the main dashboard."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    
    portfolio_json = get_json_data(PORTFOLIO_PATH)
    watchlist_json = get_json_data(WATCHLIST_PATH)
    
    portfolio_tickers = [data.get("ticker") for key, data in portfolio_json.items() if "ticker" in data]
    watchlist_tickers = watchlist_json.get("watchlist", [])
    
    portfolio_data = [row for row in db_rows if row['ticker'] in portfolio_tickers]
    watchlist_data = [row for row in db_rows if row['ticker'] in watchlist_tickers]
    
    return templates.TemplateResponse(
        request=request, name="index.html", 
        context={"portfolio": portfolio_data, "watchlist": watchlist_data, "global_updated": global_updated}
    )

@app.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str):
    """Loads the Detailed Analysis View."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals WHERE ticker = ?", (ticker,))
    stock_data = cursor.fetchone()
    
    # --- Dynamic Earnings Date Math ---
    days_to_earnings = None
    volatility_date = None
    
    if stock_data and stock_data['next_earnings_date'] and stock_data['next_earnings_date'] != 'Unknown':
        try:
            # Parse the stored string into a Python datetime object
            e_date = datetime.strptime(stock_data['next_earnings_date'], '%Y-%m-%d').date()
            today = datetime.now().date()
            
            # Calculate exactly how many days are left
            days_to_earnings = (e_date - today).days
            
            # Options market volatility generally spikes ~7 days before an earnings report
            volatility_date = (e_date - timedelta(days=7)).strftime('%Y-%m-%d')
        except Exception as e:
            print(f"[WARNING] Could not parse earnings date for {ticker}: {e}")

    # Calculate Portfolio Mathematics if owned
    portfolio_json = get_json_data(PORTFOLIO_PATH)
    user_asset = next((data for key, data in portfolio_json.items() if data.get("ticker") == ticker), None)
    
    portfolio_math = None
    if user_asset and stock_data and stock_data['current_price']:
        buy_price = user_asset.get('buy_price', 0)
        shares = user_asset.get('shares', 0)
        
        # Ghostfolio currency conversion logic
        if user_asset.get('price_in_pence', False):
            buy_price = buy_price * 100
            
        if buy_price > 0 and shares > 0:
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

    # Load Parquet Data for Visuals
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")
        df_sp500 = pd.read_parquet(HISTORICAL_DIR / "SP500_BASELINE.parquet")
        macro_html = create_macro_chart(df_macro, df_sp500, ticker)
    except Exception as e:
        macro_html = f"<p>Chart Data Unavailable: {e}</p>"

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        intraday_html = create_intraday_chart(df_intraday, ticker)
    except Exception:
        intraday_html = "<p>Intraday data unavailable.</p>"
        
    return templates.TemplateResponse(
        request=request, name="stock_detail.html", 
        context={
            "stock": stock_data, 
            "macro_html": macro_html, 
            "intraday_html": intraday_html,
            "portfolio_math": portfolio_math,
            "days_to_earnings": days_to_earnings,   # NEW: Passed to HTML
            "volatility_date": volatility_date      # NEW: Passed to HTML
        }
    )

if __name__ == "__main__":
    # Now dynamically uses the port defined in config.py or config.json
    print(f"Starting Quantamental Web Server on Port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)