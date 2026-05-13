# page_routes.py
import json
import pandas as pd
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config, PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, BASE_CURRENCY
from database import get_connection
from sentiment_engine import get_sentiment_html
from market_pulse import get_cached_pulse
from visuals import create_macro_chart, create_intraday_chart
from quant_signals import get_candlestick_patterns
from portfolio_service import get_rate_to_base, get_rate_from_base

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")

def get_json_data(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def get_unread_count():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM system_notifications WHERE is_read = 0")
        count = cursor.fetchone()['cnt']
        conn.close()
        return count
    except Exception:
        return 0

@page_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config_data = load_config()
    return templates.TemplateResponse(request=request, name="settings.html", context={"config": config_data, "unread_count": get_unread_count()})

@page_router.get("/market-sentiment", response_class=HTMLResponse)
async def market_sentiment_page(request: Request):
    return templates.TemplateResponse(request=request, name="market_sentiment.html", context={"sentiment_html": get_sentiment_html(), "unread_count": get_unread_count()})

@page_router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM system_notifications ORDER BY timestamp DESC LIMIT 100")
    notifications = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name="notifications.html", context={"notifications": notifications, "unread_count": get_unread_count()})

@page_router.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    return templates.TemplateResponse(request=request, name="glossary.html", context={"unread_count": get_unread_count()})

@page_router.get("/", response_class=RedirectResponse)
async def home():
    return RedirectResponse(url="/portfolio")

@page_router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, account_id: str = "all", embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    conn.close()

    config_data = load_config()
    active_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    discovered_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("discovered", [])
    
    account_options = [{"id": "all", "name": "Global (All Accounts)"}]
    for acc in discovered_accounts:
        if acc["id"] in active_accounts:
            account_options.append({"id": acc["id"], "name": acc["name"]})
    
    portfolio_json = get_json_data(PORTFOLIO_PATH)
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
    summary_math = {"value": 0.0, "cost": 0.0, "pnl": 0.0, "pnl_pct": 0.0}
    
    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in portfolio_tickers:
            if row_dict.get('setup_tags'):
                try: row_dict['setup_tags_list'] = json.loads(row_dict['setup_tags'])
                except: row_dict['setup_tags_list'] = []
            else:
                row_dict['setup_tags_list'] = []
            
            portfolio_data.append(row_dict)
            
            asset = next((d for d in portfolio_json.values() if d.get("ticker") == row_dict['ticker']), None)
            if asset and row_dict['current_price']:
                shares = 0
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
                            
                cost_in_base = shares * buy_price_base
                exchange_rate = get_rate_to_base(row_dict['currency'])
                val_in_base = (shares * row_dict['current_price']) * exchange_rate
                
                summary_math["value"] += val_in_base
                summary_math["cost"] += cost_in_base

    if summary_math["cost"] > 0:
        summary_math["pnl"] = summary_math["value"] - summary_math["cost"]
        summary_math["pnl_pct"] = (summary_math["pnl"] / summary_math["cost"]) * 100
        formatted_summary = {
            "value": f"{summary_math['value']:,.2f} {BASE_CURRENCY}",
            "cost": f"{summary_math['cost']:,.2f} {BASE_CURRENCY}",
            "pnl": f"{'+' if summary_math['pnl'] > 0 else ''}{summary_math['pnl']:,.2f} {BASE_CURRENCY}",
            "pnl_pct": f"{summary_math['pnl_pct']:.2f}",
            "is_positive": summary_math["pnl"] > 0
        }
    else:
        formatted_summary = None
    
    return templates.TemplateResponse(
        request=request, name="portfolio.html", 
        context={
            "portfolio": portfolio_data, 
            "global_updated": global_updated, 
            "embed": embed, 
            "unread_count": get_unread_count(),
            "account_options": account_options,
            "selected_account": account_id,
            "summary_math": formatted_summary,
            "config": config_data,
            "cached_pulse": get_cached_pulse()
        }
    )

@page_router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals")
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    global_update_val = cursor.fetchone()['global_updated']
    global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    conn.close()
    
    watchlist_json = get_json_data(WATCHLIST_PATH)
    watchlist_tickers = watchlist_json.get("watchlist", [])
    
    watchlist_data = []
    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in watchlist_tickers:
            if row_dict.get('setup_tags'):
                try: row_dict['setup_tags_list'] = json.loads(row_dict['setup_tags'])
                except: row_dict['setup_tags_list'] = []
            else:
                row_dict['setup_tags_list'] = []
            watchlist_data.append(row_dict)
            
    return templates.TemplateResponse(
        request=request, name="watchlist.html", 
        context={
            "watchlist": watchlist_data, 
            "global_updated": global_updated, 
            "embed": embed, 
            "unread_count": get_unread_count(),
            "config": load_config(),
            "cached_pulse": get_cached_pulse()
        }
    )

@page_router.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str, embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_signals WHERE ticker = ?", (ticker,))
    stock_data = cursor.fetchone()
    
    if stock_data: stock_data = dict(stock_data)
    conn.close()
    
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
        exchange_rate = get_rate_from_base(stock_data['currency'])
        
        def calculate_pnl(shares, buy_price_base):
            if shares <= 0: return None
            bp_adj = buy_price_base * exchange_rate
            if user_asset.get('price_in_pence', False): bp_adj *= 100
                
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
            
        global_math = calculate_pnl(user_asset.get('global_shares', 0), user_asset.get('global_buy_price', 0))
        account_maths = []
        for acc in user_asset.get('accounts', []):
            acc_m = calculate_pnl(acc.get('shares', 0), acc.get('buy_price', 0))
            if acc_m:
                acc_m["name"] = acc.get("name", "Unknown Account")
                account_maths.append(acc_m)
                
        if global_math:
            portfolio_math = {"global": global_math, "accounts": account_maths}

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
            price_action = {
                "day_low": last_day['Low'], 
                "day_high": last_day['High'],
                "month_low": last_21['Low'].min(), 
                "month_high": last_21['High'].max(),
                "s1": (P * 2) - prev_day['High'], 
                "s2": P - (prev_day['High'] - prev_day['Low'])
            }
    except Exception as e:
        df_macro = pd.DataFrame()
        macro_html = f"<p>Chart Data Unavailable: {e}</p>"

    live_pattern_name = live_pattern_tooltip = live_pattern_score = None
    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            curr_pseudo = pd.Series({
                'Open': df_intraday['Open'].iloc[0],
                'High': df_intraday['High'].max(),
                'Low': df_intraday['Low'].min(),
                'Close': df_intraday['Close'].iloc[-1]
            })
            live_patterns = get_candlestick_patterns(df_macro.iloc[-2], df_macro.iloc[-1], curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]

        s1_val = price_action['s1'] if price_action else None
        s2_val = price_action['s2'] if price_action else None
        intraday_html = create_intraday_chart(df_intraday, ticker, s1=s1_val, s2=s2_val, live_pattern_name=live_pattern_name, live_pattern_tooltip=live_pattern_tooltip, live_pattern_score=live_pattern_score)
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
            "price_action": price_action,
            "unread_count": get_unread_count(),
            "embed": embed,
            "config": load_config(),
            "cached_pulse": get_cached_pulse()
        }
    )