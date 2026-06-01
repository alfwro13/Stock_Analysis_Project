# page_routes.py
import json
import re
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from position_sizing import get_position_sizing_config

from config import load_config, PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, BASE_CURRENCY
from database import get_connection
from regime_engine import get_latest_regime
from sentiment_engine import (
    get_sentiment_html,
    get_vix_spy_html,
    get_yield_equity_html,
    get_uk_yield_equity_html,
    get_ftse_gbp_html
)
from market_pulse import get_all_cached_pulse
from utils import normalize_ticker
from visuals import (
    create_macro_chart,
    create_intraday_chart,
    create_us_liquidity_chart,
    create_us_credit_chart,
    create_uk_liquidity_chart,
    create_uk_credit_chart,
    create_yield_curve_chart,
    create_us_inflation_chart,
    create_uk_inflation_chart
)
from portfolio_service import get_rate_to_base, get_rate_from_base
from quant_signals import get_candlestick_patterns
from quant_screener import fetch_latest_signals, generate_markdown_briefing
from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD, CSS_VERSION

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


@page_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@page_router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


@page_router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="change_password.html")


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

def _build_position_sizing_context(config_data: dict, db_rows) -> dict:
    """
    Builds a context dict for client-side position sizing.
    Walks unique currencies in the result set, fetches FX rates once,
    returns a JSON-serializable structure for embedding in templates.
    """
    from portfolio_service import get_rate_to_base
    
    base_currency = config_data.get("BASE_CURRENCY", "GBP")
    
    # Find all unique currencies in the result set
    currencies = set()
    for row in db_rows:
        try:
            cur = row["currency"] if "currency" in row.keys() else None
        except Exception:
            cur = None
        if cur:
            currencies.add(cur)
    currencies.add(base_currency)
    
    # Fetch FX rates (native → base) for each currency seen
    fx_rates = {}
    for cur in currencies:
        try:
            rate = get_rate_to_base(cur)
            if rate is not None and rate > 0:
                fx_rates[cur] = float(rate)
        except Exception:
            pass
    # Always provide a 1.0 entry for the base currency itself
    fx_rates[base_currency] = 1.0
    
    return {
        "config":   get_position_sizing_config(config_data),
        "fx_rates": fx_rates,
        "base_currency": base_currency,
    }


# --- COMPREHENSIVE MACRO GLOSSARY & POLARITY MAPPING ---
EVENT_GLOSSARY = {
    # --- INFLATION & PRICES (Inverse Polarity) ---
    r"\bcpi\b": {"desc": "Consumer Price Index. The primary measure of inflation. Higher than expected forces Central Banks to keep rates high (Bearish for equities).", "polarity": "inverse"},
    r"\bppi\b": {"desc": "Producer Price Index. Measures wholesale inflation before it reaches consumers. A leading indicator for future CPI.", "polarity": "inverse"},
    r"\brpi\b|retail price index": {"desc": "Retail Price Index. An older UK inflation measure, still used heavily for wage and contract pricing negotiations.", "polarity": "inverse"},
    r"house price index": {"desc": "Measures housing inflation and consumer wealth effect.", "polarity": "direct"},
    
    # --- CENTRAL BANKS & LIQUIDITY (Neutral/Narrative Polarity) ---
    r"\bfomc\b": {"desc": "Federal Open Market Committee. The Fed's policy body. Their rate decisions and minutes dictate global liquidity.", "polarity": "neutral"},
    r"\bboe\b": {"desc": "Bank of England. The UK's central bank. Sets base rates impacting GBP and UK equities.", "polarity": "neutral"},
    r"fed's.*speech|boe's.*speech": {"desc": "Central Bank Speaker. Unscheduled volatility risk. Markets scan these speeches for hawkish or dovish policy hints.", "polarity": "neutral"},
    r"auction": {"desc": "Sovereign Debt Auction (Bonds/Bills). Weak demand can cause Treasury yields to spike, triggering algorithmic equity sell-offs.", "polarity": "neutral"},

    # --- ECONOMIC GROWTH & ACTIVITY (Direct/Threshold Polarity) ---
    r"\bpmi\b": {"desc": "Purchasing Managers' Index. A leading indicator of economic health. >50.0 indicates expansion; <50.0 indicates contraction/recession.", "polarity": "threshold"},
    r"\bgdp\b": {"desc": "Gross Domestic Product. The total value of goods produced. High GDP is bullish, but too hot can trigger inflation fears.", "polarity": "direct"},
    r"retail sales": {"desc": "Measures consumer spending, which makes up the majority of Western economic growth.", "polarity": "direct"},
    r"fed manufacturing|empire state|fed activity": {"desc": "Regional Fed Surveys (e.g., Philly, Kansas, NY). Early localized indicators of manufacturing health before national PMI data drops.", "polarity": "direct"},

    # --- LABOR MARKET ---
    r"non-farm|nfp": {"desc": "Non-Farm Payrolls. US employment data. Strong jobs data can be bearish if it forces the Fed to keep rates high to cool the economy.", "polarity": "direct"},
    r"claimant count": {"desc": "UK Unemployment. The change in the number of people claiming jobless benefits. A rising number indicates economic weakness.", "polarity": "inverse"},
    r"jobless claims": {"desc": "US Unemployment filings. Rising claims signal a cooling labor market, which can be perversely bullish if the market expects rate cuts.", "polarity": "inverse"},
    r"unemployment rate": {"desc": "Percentage of the total labor force that is unemployed. Serves as a primary mandate metric for central bank policy.", "polarity": "inverse"},

    # --- HOUSING & REAL ESTATE (Direct Polarity) ---
    r"building permits|housing starts": {"desc": "Leading indicators for the construction sector and broader economic health. Highly sensitive to interest rates.", "polarity": "direct"},
    r"mortgage applications": {"desc": "A direct measure of housing demand. Drops significantly when bond yields/mortgage rates rise.", "polarity": "direct"},

    # --- ENERGY & COMMODITIES (Inverse/Neutral Polarity) ---
    r"crude oil|natural gas": {"desc": "Energy Inventories (EIA/API). Drops in supply can spike oil prices, driving up inflation and hurting consumer discretionary stocks.", "polarity": "inverse"}
}

def enrich_macro_events(events_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Scans event names against the glossary to append tooltips and mathematically 
    calculates the Delta between Forecast and Previous to generate actionable insights.
    """
    for evt in events_list:
        evt_name = evt.get('event_name', '')
        evt['context'] = None
        evt['insight'] = ""
        
        polarity = "neutral"
        
        # 1. Match Glossary Context and Polarity
        for pattern, data in EVENT_GLOSSARY.items():
            if re.search(pattern, evt_name, re.IGNORECASE):
                evt['context'] = data["desc"]
                polarity = data["polarity"]
                break
                
        # 2. Calculate Mathematical Delta & Generate Insight
        f_val = evt.get('forecast_val')
        p_val = evt.get('previous_val')
        
        if f_val is not None and p_val is not None:
            try:
                f_num = float(f_val)
                p_num = float(p_val)
                delta = f_num - p_num
                
                # Apply Polarity Rules
                if polarity == "inverse":
                    if delta < 0:
                        evt['insight'] = f"📉 Expected to drop by {delta:+.2f} (Cooling / Dovish)"
                    elif delta > 0:
                        evt['insight'] = f"📈 Expected to rise by {delta:+.2f} (Hot / Hawkish)"
                    else:
                        evt['insight'] = "⚖️ Expected to remain unchanged"
                        
                elif polarity == "direct":
                    if delta > 0:
                        evt['insight'] = f"📈 Expected to grow by {delta:+.2f} (Expanding / Bullish)"
                    elif delta < 0:
                        evt['insight'] = f"📉 Expected to shrink by {delta:+.2f} (Slowing / Bearish)"
                    else:
                        evt['insight'] = "⚖️ Expected to remain unchanged"
                        
                elif polarity == "threshold":
                    status = "Expansion" if f_num > 50.0 else "Contraction"
                    if delta > 0:
                        evt['insight'] = f"📈 Expected to rise by {delta:+.2f} (Est: {f_num} - {status})"
                    elif delta < 0:
                        evt['insight'] = f"📉 Expected to drop by {delta:+.2f} (Est: {f_num} - {status})"
                    else:
                        evt['insight'] = f"⚖️ Expected unchanged (Est: {f_num} - {status})"
                        
                else:
                    if delta != 0:
                        evt['insight'] = f"Expected change: {delta:+.2f}"
                    else:
                        evt['insight'] = "Expected unchanged"
                        
            except ValueError:
                # Fallback if values cannot be cast to floats
                pass
                
    return events_list


@page_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    import os
    config_data = load_config()
    api_key = os.environ.get("API_KEY", "")
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "config": config_data,
            "unread_count": get_unread_count(),
            "dashboard_username": os.environ.get("DASHBOARD_USERNAME", "admin"),
            "api_key": api_key,
        }
    )


def _parse_cb_nlp_message(msg_text: str, timestamp: str) -> dict | None:
    """Extract structured fields from a stored Macro NLP notification message."""
    try:
        result: dict = {"timestamp": timestamp}
        for line in msg_text.split('\n'):
            if '**Event:**' in line:
                part = line.split('**Event:** ', 1)[1]
                result['event_name'] = part.split(' (')[0].strip()
                result['currency'] = part.split('(')[1].rstrip(')').strip()
            elif '**Calculated Tone:**' in line:
                result['tone'] = line.split('**Calculated Tone:** ', 1)[1].strip()
            elif '**Expected Equity Impact:**' in line:
                result['equity_impact'] = line.split('**Expected Equity Impact:** ', 1)[1].strip()
            elif '**Analyzed FinBERT Score:**' in line:
                result['score'] = line.split('**Analyzed FinBERT Score:** ', 1)[1].strip()
        if 'tone' not in result:
            return None
        tone_upper = result['tone'].upper()
        if 'HAWKISH' in tone_upper:
            result['css_class'] = 'risk-summary-red'
            result['header_class'] = 'red'
        elif 'DOVISH' in tone_upper:
            result['css_class'] = 'risk-summary-green'
            result['header_class'] = 'green'
        else:
            result['css_class'] = 'risk-summary-yellow'
            result['header_class'] = 'yellow'
        return result
    except Exception:
        return None


@page_router.get("/market-sentiment", response_class=HTMLResponse)
async def market_sentiment_page(request: Request):
    regime_data = get_latest_regime()
    if not regime_data:
        regime_data = {
            "us_regime_label": "Unknown", 
            "us_turbulence": 0.0,
            "uk_regime_label": "Unknown", 
            "uk_turbulence": 0.0
        }
        
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
        macro_row = cursor.fetchone()
        macro_regime = dict(macro_row) if macro_row else None
        
        # --- Macroeconomic Event Routing ---
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        horizon_48h = (now + timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        horizon_7d = (now + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        now_str = start_of_day.strftime('%Y-%m-%d %H:%M:%S')

        # Fetch Urgent Events (48 hours)
        cursor.execute("""
            SELECT * FROM macro_calendar 
            WHERE event_date BETWEEN ? AND ? 
            ORDER BY event_date ASC
        """, (now_str, horizon_48h))
        urgent_events = enrich_macro_events([dict(row) for row in cursor.fetchall()])

        # Fetch US 7-Day Calendar
        cursor.execute("""
            SELECT * FROM macro_calendar 
            WHERE currency = 'USD' AND event_date BETWEEN ? AND ? 
            ORDER BY event_date ASC
        """, (now_str, horizon_7d))
        us_events = enrich_macro_events([dict(row) for row in cursor.fetchall()])

        # Fetch UK 7-Day Calendar
        cursor.execute("""
            SELECT * FROM macro_calendar 
            WHERE currency = 'GBP' AND event_date BETWEEN ? AND ? 
            ORDER BY event_date ASC
        """, (now_str, horizon_7d))
        uk_events = enrich_macro_events([dict(row) for row in cursor.fetchall()])

        # Fetch latest Central Bank NLP dispatch
        cb_nlp_latest = None
        cb_row = cursor.execute(
            "SELECT message_text, timestamp FROM system_notifications "
            "WHERE message_type = 'Macro NLP' ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if cb_row:
            cb_nlp_latest = _parse_cb_nlp_message(cb_row["message_text"], cb_row["timestamp"])

        # Process Historical DataFrame Indicators
        try:
            df_indicators = pd.read_sql_query("SELECT * FROM macro_indicators", conn)
            
            if not df_indicators.empty and 'date' in df_indicators.columns:
                df_indicators['date'] = pd.to_datetime(df_indicators['date'])
                df_indicators.set_index('date', inplace=True)
                
                # Extract wide columns and rename to 'value' to satisfy the Plotly wrappers
                df_m2 = df_indicators[['us_m2']].rename(columns={'us_m2': 'value'}).dropna().sort_index() if 'us_m2' in df_indicators.columns else pd.DataFrame()
                df_us_hy = df_indicators[['us_high_yield_spread']].rename(columns={'us_high_yield_spread': 'value'}).dropna().sort_index() if 'us_high_yield_spread' in df_indicators.columns else pd.DataFrame()
                df_m4 = df_indicators[['uk_m4']].rename(columns={'uk_m4': 'value'}).dropna().sort_index() if 'uk_m4' in df_indicators.columns else pd.DataFrame()
                df_uk_ig = df_indicators[['uk_corporate_spread']].rename(columns={'uk_corporate_spread': 'value'}).dropna().sort_index() if 'uk_corporate_spread' in df_indicators.columns else pd.DataFrame()
                df_yield_curve = df_indicators[['us_yield_curve']].rename(columns={'us_yield_curve': 'value'}).dropna().sort_index() if 'us_yield_curve' in df_indicators.columns else pd.DataFrame()
                df_us_cpi = df_indicators[['us_cpi_inflation']].rename(columns={'us_cpi_inflation': 'value'}).dropna().sort_index() if 'us_cpi_inflation' in df_indicators.columns else pd.DataFrame()
                df_uk_cpi = df_indicators[['uk_cpi_inflation']].rename(columns={'uk_cpi_inflation': 'value'}).dropna().sort_index() if 'uk_cpi_inflation' in df_indicators.columns else pd.DataFrame()
            else:
                df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
                df_us_cpi, df_uk_cpi = pd.DataFrame(), pd.DataFrame()
        except Exception as e:
            print(f"[DEBUG] Error processing macro indicators matrix: {e}")
            df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            df_us_cpi, df_uk_cpi = pd.DataFrame(), pd.DataFrame()

        # Safely fetch baseline SPY & FTSE prices
        try:
            df_spy = pd.read_parquet(HISTORICAL_DIR / "SP500_BASELINE.parquet")
        except Exception:
            df_spy = pd.DataFrame()
            
        try:
            df_ftse = pd.read_parquet(HISTORICAL_DIR / "FTSE_BASELINE.parquet")
        except Exception:
            df_ftse = pd.DataFrame()
            
        # Generate chart HTML
        us_liquidity_html = create_us_liquidity_chart(df_spy, df_m2)
        us_credit_html = create_us_credit_chart(df_us_hy)
        uk_liquidity_html = create_uk_liquidity_chart(df_ftse, df_m4)
        uk_credit_html = create_uk_credit_chart(df_uk_ig)
        yield_curve_html = create_yield_curve_chart(df_yield_curve)
        us_inflation_html = create_us_inflation_chart(df_spy, df_us_cpi)
        uk_inflation_html = create_uk_inflation_chart(df_ftse, df_uk_cpi)

    except Exception as e:
        print(f"[DEBUG] Fatal error in market_sentiment route: {e}")
        macro_regime = None
        urgent_events = []
        us_events = []
        uk_events = []
        us_liquidity_html = "<p>Data unavailable.</p>"
        us_credit_html = "<p>Data unavailable.</p>"
        uk_liquidity_html = "<p>Data unavailable.</p>"
        uk_credit_html = "<p>Data unavailable.</p>"
        yield_curve_html = "<p>Data unavailable.</p>"
        us_inflation_html = "<p>Data unavailable.</p>"
        uk_inflation_html = "<p>Data unavailable.</p>"
        cb_nlp_latest = None
    finally:
        conn.close()
        
    return templates.TemplateResponse(
        request=request, 
        name="market_sentiment.html", 
        context={
            "sentiment_html": get_sentiment_html(), 
            "vix_spy_html": get_vix_spy_html(),
            "yield_equity_html": get_yield_equity_html(),
            "uk_yield_equity_html": get_uk_yield_equity_html(),
            "ftse_gbp_html": get_ftse_gbp_html(),
            "us_liquidity_html": us_liquidity_html,
            "us_credit_html": us_credit_html,
            "uk_liquidity_html": uk_liquidity_html,
            "uk_credit_html": uk_credit_html,
            "yield_curve_html": yield_curve_html,
            "us_inflation_html": us_inflation_html,
            "uk_inflation_html": uk_inflation_html,
            "regime_data": regime_data,
            "macro_regime": macro_regime,
            "urgent_events": urgent_events,
            "us_events": us_events,
            "uk_events": uk_events,
            "cb_nlp_latest": cb_nlp_latest,
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
        context={
            "unread_count": get_unread_count(),
            "prediction_horizon": PREDICTION_HORIZON_DAYS,
            "prediction_threshold_pct": int(PREDICTION_RETURN_THRESHOLD * 100),
        }
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
               (SELECT ml_confidence_score FROM quant_signals
                WHERE ticker = s.ticker AND ml_confidence_score IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS ml_confidence_score,
               (SELECT var_95 FROM quant_signals
                WHERE ticker = s.ticker AND var_95 IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS var_95,
               (SELECT cvar_95 FROM quant_signals
                WHERE ticker = s.ticker AND cvar_95 IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS cvar_95,
               (SELECT sentiment_score FROM quant_signals
                WHERE ticker = s.ticker AND sentiment_score IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS sentiment_score,
               q.atr_pct,
               q.close_price as quant_close_price,
               COALESCE(
                   NULLIF(ap.company_name, s.ticker),
                   NULLIF(mu.company_name, s.ticker),
                   s.company_name,
                   s.ticker
               ) as resolved_company_name
        FROM stock_signals s
        LEFT JOIN asset_profiles ap ON s.ticker = ap.ticker
        LEFT JOIN market_universe mu ON s.ticker = mu.ticker
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
    position_sizing_context = _build_position_sizing_context(config_data, db_rows)
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
            # Resolve best available display name — mutual funds often have no shortName
            # from yfinance; fall back through asset_profiles → market_universe
            row_dict['company_name'] = (
                row_dict.get('resolved_company_name')
                or row_dict.get('company_name')
                or row_dict['ticker']
            )
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
            "macro_regime": macro_regime,
            "position_sizing": position_sizing_context
        }
    )


@page_router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, embed: bool = False):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.*,
               (SELECT ml_confidence_score FROM quant_signals
                WHERE ticker = s.ticker AND ml_confidence_score IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS ml_confidence_score,
               (SELECT var_95 FROM quant_signals
                WHERE ticker = s.ticker AND var_95 IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS var_95,
               (SELECT cvar_95 FROM quant_signals
                WHERE ticker = s.ticker AND cvar_95 IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS cvar_95,
               (SELECT sentiment_score FROM quant_signals
                WHERE ticker = s.ticker AND sentiment_score IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS sentiment_score,
               q.atr_pct,
               q.close_price as quant_close_price,
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
    position_sizing_context = _build_position_sizing_context(config_data, db_rows)

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
            "freetrade_only": freetrade_only,
            "position_sizing": position_sizing_context
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
            "config": load_config(),
            "prediction_horizon": PREDICTION_HORIZON_DAYS,
            "prediction_threshold_pct": int(PREDICTION_RETURN_THRESHOLD * 100),
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
    ticker = normalize_ticker(ticker)
    watchlist_json = get_json_data(WATCHLIST_PATH)
    watchlist_tickers = watchlist_json.get("watchlist", [])
    is_in_watchlist = ticker in watchlist_tickers

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, p.business_summary,
               (SELECT ml_confidence_score FROM quant_signals
                WHERE ticker = s.ticker AND ml_confidence_score IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS ml_confidence_score,
               (SELECT var_95 FROM quant_signals
                WHERE ticker = s.ticker AND var_95 IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS var_95,
               (SELECT cvar_95 FROM quant_signals
                WHERE ticker = s.ticker AND cvar_95 IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS cvar_95,
               (SELECT sentiment_score FROM quant_signals
                WHERE ticker = s.ticker AND sentiment_score IS NOT NULL
                ORDER BY date DESC LIMIT 1) AS sentiment_score,
               q.atr_pct,
               COALESCE(
                   NULLIF(p.company_name, s.ticker),
                   NULLIF(mu.company_name, s.ticker),
                   s.company_name,
                   s.ticker
               ) as resolved_company_name
        FROM stock_signals s
        LEFT JOIN asset_profiles p ON s.ticker = p.ticker
        LEFT JOIN market_universe mu ON s.ticker = mu.ticker
        LEFT JOIN quant_signals q ON s.ticker = q.ticker
            AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
        WHERE s.ticker = ?
    ''', (ticker,))
    stock_data = cursor.fetchone()
    
    if stock_data:
        stock_data = dict(stock_data)
        # Resolve best available display name — mutual funds often have no shortName
        # from yfinance; fall back through asset_profiles → market_universe
        stock_data['company_name'] = (
            stock_data.get('resolved_company_name')
            or stock_data.get('company_name')
            or ticker
        )
        stock_data['company_name'] = (
            stock_data['company_name']
            .replace(" - Common Stock", "")
            .replace(" Common Stock", "")
            .strip()
        )
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
                "atr_pct": q_data.get("atr_pct"),
                "atr_stop_loss": None,
                "last_updated": None,
                "ml_confidence_score": q_data.get("ml_confidence_score"),
                "var_95": q_data.get("var_95"),
                "cvar_95": q_data.get("cvar_95"),
                "sentiment_score": q_data.get("sentiment_score"),
                "yield_correlation": None,
                "trailing_pe": None,
                "debt_to_equity": None,
                "forward_pe": None,
                "peg_ratio": None,
                "peter_lynch_peg": None,
                "price_to_book": None,
                "profit_margin": None,
                "roe": None,
                "revenue_growth": None,
                "current_ratio": None,
                "operating_cash_flow": None,
                "short_interest": None,
                "institutional_ownership": None,
                "beta": None,
                "expense_ratio": None,
                "ytd_return": None,
                "total_assets": None,
                "nav_price": None,
                "dividend_yield": None,
                "top_holdings": None,
                "sector_weightings": None
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
                "atr_pct": None,
                "atr_stop_loss": None,
                "last_updated": None,
                "ml_confidence_score": None,
                "var_95": None,
                "cvar_95": None,
                "sentiment_score": None,
                "yield_correlation": None,
                "trailing_pe": None,
                "debt_to_equity": None,
                "forward_pe": None,
                "peg_ratio": None,
                "peter_lynch_peg": None,
                "price_to_book": None,
                "profit_margin": None,
                "roe": None,
                "revenue_growth": None,
                "current_ratio": None,
                "operating_cash_flow": None,
                "short_interest": None,
                "institutional_ownership": None,
                "beta": None,
                "expense_ratio": None,
                "ytd_return": None,
                "total_assets": None,
                "nav_price": None,
                "dividend_yield": None,
                "top_holdings": None,
                "sector_weightings": None
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
    except FileNotFoundError:
        df_macro = pd.DataFrame()
        macro_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:180px;gap:10px;color:#888;'><span style='font-size:2rem;'>📭</span><span style='font-weight:600;'>No historical data yet</span><span style='font-size:0.85rem;'>Press <strong>Refresh</strong> above to fetch price history for this asset.</span></div>"
    except Exception as e:
        df_macro = pd.DataFrame()
        macro_html = f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:180px;gap:8px;color:#888;'><span style='font-size:2rem;'>⚠️</span><span style='font-weight:600;'>Chart unavailable</span><span style='font-size:0.85rem;'>{type(e).__name__}: {e}</span></div>"

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
    except FileNotFoundError:
        intraday_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:120px;gap:10px;color:#888;'><span style='font-size:1.8rem;'>📭</span><span style='font-weight:600;'>No intraday data yet</span><span style='font-size:0.85rem;'>Press <strong>Refresh</strong> above to fetch today's intraday data.</span></div>"
    except Exception:
        intraday_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:120px;gap:8px;color:#888;'><span style='font-size:1.8rem;'>⚠️</span><span style='font-weight:600;'>Intraday data unavailable</span></div>"
    
    config_data = load_config()
    # Build minimal context — single ticker
    fake_rows = [{"currency": stock_data.get("currency", "USD")}]
    position_sizing_context = _build_position_sizing_context(config_data, fake_rows)

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
            "last_updated_str": last_updated_str,
            "position_sizing": position_sizing_context
        }
    )