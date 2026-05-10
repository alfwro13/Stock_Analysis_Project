# main.py
import uvicorn
import json
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from config import PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, PORT, BASE_CURRENCY
from database import get_connection, init_db
from quant_signals import QuantEngine
from data_engine import DataEngine
from visuals import create_macro_chart, create_intraday_chart
from ghostfolio_sync import GhostfolioSyncEngine

app = FastAPI(title="Quantamental Dashboard")
templates = Jinja2Templates(directory="templates")

init_db()

def get_json_data(filepath):
    """Safely reads local JSON files."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

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

@app.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    return templates.TemplateResponse(request=request, name="glossary.html", context={})

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
    
    return templates.TemplateResponse(
        request=request, name="portfolio.html", 
        context={"portfolio": portfolio_data, "global_updated": global_updated, "embed": embed}
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
    
    return templates.TemplateResponse(
        request=request, name="watchlist.html", 
        context={"watchlist": watchlist_data, "global_updated": global_updated, "embed": embed}
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
        
        # --- NEW CURRENCY EXCHANGE LOGIC ---
        stock_currency = stock_data['currency']
        exchange_rate = 1.0
        
        # If the asset is USD, but base Ghostfolio currency is GBP, fetch GBPUSD=X
        if stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp', 'GBP']:
            try:
                fx_ticker = f"{BASE_CURRENCY}{stock_currency}=X"
                fx_data = yf.Ticker(fx_ticker).history(period="1d")
                if not fx_data.empty:
                    exchange_rate = fx_data['Close'].iloc[-1]
            except Exception as e:
                print(f"[WARNING] Could not fetch exchange rate for {fx_ticker}: {e}")
        
        if buy_price > 0 and shares > 0:
            # Convert Ghostfolio's local currency cost to the asset's currency
            buy_price = buy_price * exchange_rate
            
            # UK specific Pence conversion
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
            "price_action": price_action     
        }
    )

if __name__ == "__main__":
    print(f"Starting Quantamental Web Server on Port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)