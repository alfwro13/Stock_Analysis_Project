# page_routes.py
import json
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config, PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, BASE_CURRENCY
from database import get_connection
from regime_engine import get_latest_regime
from sentiment_engine import (
    get_sentiment_html,
    get_vix_spy_html,
    get_yield_gauge_html,
    get_yield_equity_html,
    get_uk_yield_equity_html,
    get_ftse_gbp_html
)
from market_pulse import get_all_cached_pulse
from visuals import create_macro_chart, create_intraday_chart
from portfolio_service import get_rate_to_base, get_rate_from_base
from quant_signals import get_candlestick_patterns
from quant_screener import fetch_latest_signals, generate_markdown_briefing

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_json_data(filepath: str) -> Dict[str, Any]:
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def get_unread_count() -> int:
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
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"config": config_data, "unread_count": get_unread_count()}
    )


@page_router.get("/market-sentiment", response_class=HTMLResponse)
async def market_sentiment_page(request: Request):
    regime_data = get_latest_regime()
    if not regime_data:
        regime_data = {"regime_label": "Unknown", "turbulence_index": 0.0}
        
    return templates.TemplateResponse(
        request=request, 
        name="market_sentiment.html", 
        context={
            "sentiment_html": get_sentiment_html(), 
            "vix_spy_html": get_vix_spy_html(),
            "yield_gauge_html": get_yield_gauge_html(),
            "yield_equity_html": get_yield_equity_html(),
            "uk_yield_equity_html": get_uk_yield_equity_html(),
            "ftse_gbp_html": get_ftse_gbp_html(),
            "regime_data": regime_data,
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/options-sandbox", response_class=HTMLResponse)
async def options_sandbox_page(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="options_sandbox.html", 
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
            "cached_pulse": get_all_cached_pulse()
        }
    )


@page_router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM system_notifications ORDER BY timestamp DESC LIMIT 100")
    notifications = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"notifications": notifications, "unread_count": get_unread_count()}
    )


@page_router.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="glossary.html",
        context={"unread_count": get_unread_count()}
    )


@page_router.get("/", response_class=RedirectResponse)
async def home():
    return RedirectResponse(url="/portfolio")


@page_router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, account_id: str = "all", embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.*, 
               q.ml_confidence_score, 
               q.var_95, 
               q.cvar_95, 
               q.sentiment_score
        FROM stock_signals s
        LEFT JOIN quant_signals q ON s.ticker = q.ticker 
        AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
    """)
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
    macro_row = cursor.fetchone()
    macro_regime = dict(macro_row) if macro_row else None
    
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
                try: 
                    row_dict['setup_tags_list'] = json.loads(row_dict['setup_tags'])
                except Exception: 
                    row_dict['setup_tags_list'] = []
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
            "cached_pulse": get_all_cached_pulse(),
            "macro_regime": macro_regime
        }
    )


@page_router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.*, 
               q.ml_confidence_score, 
               q.var_95, 
               q.cvar_95, 
               q.sentiment_score,
               m.is_freetrade
        FROM stock_signals s
        LEFT JOIN quant_signals q ON s.ticker = q.ticker 
        AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
        LEFT JOIN market_universe m ON s.ticker = m.ticker
    """)
    db_rows = cursor.fetchall()
    
    cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
    macro_row = cursor.fetchone()
    macro_regime = dict(macro_row) if macro_row else None
    
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
                try: 
                    row_dict['setup_tags_list'] = json.loads(row_dict['setup_tags'])
                except Exception: 
                    row_dict['setup_tags_list'] = []
            else:
                row_dict['setup_tags_list'] = []
            watchlist_data.append(row_dict)

    config_data = load_config()
    freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
            
    return templates.TemplateResponse(
        request=request, name="watchlist.html", 
        context={
            "watchlist": watchlist_data, 
            "global_updated": global_updated, 
            "embed": embed, 
            "unread_count": get_unread_count(),
            "config": config_data,
            "cached_pulse": get_all_cached_pulse(),
            "macro_regime": macro_regime,
            "freetrade_only": freetrade_only
        }
    )


@page_router.get("/earnings-volatility", response_class=HTMLResponse)
async def earnings_volatility_page(request: Request):
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT * FROM earnings_volatility 
        WHERE next_earnings_date >= ?
        ORDER BY next_earnings_date ASC, edge_score DESC
    """
    cursor.execute(query, (today_str,))
    rows = cursor.fetchall()
    
    earnings_data = [dict(row) for row in rows]
    conn.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="earnings_volatility.html", 
        context={
            "earnings_data": earnings_data,
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/quant-screener", response_class=HTMLResponse)
async def quant_screener_page(request: Request):
    today = datetime.now()
    target_date = today.strftime('%Y-%m-%d')
    
    signals = fetch_latest_signals(target_date)
    
    if not signals:
        yesterday = today - timedelta(days=1)
        target_date = yesterday.strftime('%Y-%m-%d')
        signals = fetch_latest_signals(target_date)
        
    if signals:
        markdown_content = generate_markdown_briefing(target_date, signals)
    else:
        markdown_content = (
            f"# 📊 Morning Quant Briefing\n"
            f"**Date:** {target_date}\n\n"
            f"*No signals available for today or yesterday. Ensure the `quant_engine` scheduled overnight scan is running successfully.*"
        )
        
    return templates.TemplateResponse(
        request=request, 
        name="quant_screener.html", 
        context={
            "markdown_content": markdown_content,
            "target_date": target_date,
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/market-screener", response_class=HTMLResponse)
async def market_screener_page(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="market_screener.html", 
        context={
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/market-reports", response_class=HTMLResponse)
async def market_reports_page(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="market_reports.html", 
        context={
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str, embed: bool = False):
    watchlist_json = get_json_data(WATCHLIST_PATH)
    watchlist_tickers = watchlist_json.get("watchlist", [])
    is_in_watchlist = ticker in watchlist_tickers

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, p.business_summary,
               q.ml_confidence_score, q.var_95, q.cvar_95, q.sentiment_score
        FROM stock_signals s
        LEFT JOIN asset_profiles p ON s.ticker = p.ticker
        LEFT JOIN quant_signals q ON s.ticker = q.ticker
            AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
        WHERE s.ticker = ?
    ''', (ticker,))
    stock_data = cursor.fetchone()
    
    if stock_data: 
        stock_data = dict(stock_data)
        if stock_data.get("company_name"):
            stock_data["company_name"] = stock_data["company_name"].replace(" - Common Stock", "").replace(" Common Stock", "").strip()
    else:
        cursor.execute('''
            SELECT q.*, 
                   COALESCE(p.company_name, m.company_name, q.ticker) as company_name, 
                   COALESCE(p.sector, 'Unclassified') as sector, 
                   COALESCE(p.currency, 'USD') as currency, 
                   COALESCE(p.quote_type, 'EQUITY') as quote_type, 
                   p.business_summary 
            FROM quant_signals q
            LEFT JOIN market_universe m ON q.ticker = m.ticker
            LEFT JOIN asset_profiles p ON q.ticker = p.ticker
            WHERE q.ticker = ? ORDER BY q.date DESC LIMIT 1
        ''', (ticker,))
        q_data = cursor.fetchone()
        
        if q_data:
            q_data = dict(q_data)
            company_name = q_data.get("company_name") or ticker
            company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()
            
            c_price = q_data.get("close_price")
            c_price = float(c_price) if c_price is not None else 0.0
            
            stock_data = {
                "ticker": ticker,
                "company_name": company_name,
                "sector": q_data.get("sector") or "Unclassified",
                "quote_type": q_data.get("quote_type") or "EQUITY",
                "currency": q_data.get("currency") or "USD",
                "current_price": c_price,
                "overall_signal": "UNIVERSE SCAN ONLY",
                "composite_score": "N/A",
                "educational_notes": "This asset is part of the broader market universe scan. Add it to your Ghostfolio or Watchlist to trigger a deep, institutional fundamental evaluation.",
                "business_summary": q_data.get("business_summary"),
                "next_earnings_date": "Unknown",
                "target_price": None,
                "trend_50d": "UP" if q_data.get("sma_50") and c_price > q_data.get("sma_50") else "DOWN",
                "trend_200d": "UP" if q_data.get("sma_200") and c_price > q_data.get("sma_200") else "DOWN",
                "rsi_14": q_data.get("rsi_14"),
                "atr_stop_loss": None,
                "last_updated": None,
                "ml_confidence_score": q_data.get("ml_confidence_score"),
                "var_95": q_data.get("var_95"),
                "cvar_95": q_data.get("cvar_95"),
                "sentiment_score": q_data.get("sentiment_score")
            }
        else:
            stock_data = {
                "ticker": ticker,
                "company_name": ticker,
                "sector": "Unknown",
                "quote_type": "UNKNOWN",
                "currency": "USD",
                "current_price": 0.0,
                "overall_signal": "UNKNOWN",
                "composite_score": "N/A",
                "educational_notes": "Data not found. Asset may not be tracked.",
                "business_summary": None,
                "next_earnings_date": "Unknown",
                "target_price": None,
                "trend_50d": "N/A",
                "trend_200d": "N/A",
                "rsi_14": None,
                "atr_stop_loss": None,
                "last_updated": None,
                "ml_confidence_score": None,
                "var_95": None,
                "cvar_95": None,
                "sentiment_score": None
            }
            
    conn.close()
    
    data_status = 'red'
    last_updated_str = "Never"
    if stock_data and stock_data.get('last_updated'):
        last_updated_str = stock_data['last_updated']
        try:
            lu_date = datetime.strptime(last_updated_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - lu_date < timedelta(hours=24):
                data_status = 'green'
            else:
                data_status = 'yellow'
        except Exception:
            data_status = 'red'

    top_holdings = []
    sector_weightings = []
    if stock_data and stock_data.get('top_holdings'):
        try: 
            top_holdings = json.loads(stock_data['top_holdings'])
        except Exception: 
            pass
    if stock_data and stock_data.get('sector_weightings'):
        try: 
            sector_weightings = json.loads(stock_data['sector_weightings'])
        except Exception: 
            pass

    days_to_earnings = None
    volatility_date = None
    if stock_data and stock_data.get('next_earnings_date') and stock_data['next_earnings_date'] != 'Unknown':
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
    if user_asset and stock_data and stock_data.get('current_price'):
        exchange_rate = get_rate_from_base(stock_data['currency'])
        
        def calculate_pnl(shares, buy_price_base):
            if shares <= 0: return None
            bp_adj = buy_price_base * exchange_rate
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
        
        # --- DYNAMIC BENCHMARK SELECTION ---
        currency = stock_data.get('currency', 'USD') if stock_data else 'USD'
        if ticker.endswith('.L') or currency in ['GBp', 'GBP']:
            try:
                df_baseline = pd.read_parquet(HISTORICAL_DIR / "FTSE_BASELINE.parquet")
            except Exception:
                df_baseline = None
        else:
            try:
                df_baseline = pd.read_parquet(HISTORICAL_DIR / "SP500_BASELINE.parquet")
            except Exception:
                df_baseline = None
                
        macro_html = create_macro_chart(df_macro, df_baseline, ticker)
        
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
        macro_html = f"<p style='color:#888; font-style:italic;'>Historical Chart Data Unavailable for this asset. Error: {e}</p>"

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
        intraday_html = create_intraday_chart(
            df_intraday, ticker, s1=s1_val, s2=s2_val,
            live_pattern_name=live_pattern_name,
            live_pattern_tooltip=live_pattern_tooltip,
            live_pattern_score=live_pattern_score
        )
    except Exception:
        intraday_html = "<p style='color:#888; font-style:italic;'>Intraday data unavailable.</p>"
        
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
            "cached_pulse": get_all_cached_pulse(),
            "is_in_watchlist": is_in_watchlist,
            "data_status": data_status,
            "last_updated_str": last_updated_str
        }
    )