import email.utils
import ipaddress
import json
import logging
import re
from pathlib import Path

import markdown as _markdown
import pandas as pd

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Dict, Any, List
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from position_sizing import get_position_sizing_config

from config import load_config, PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR, BASE_CURRENCY
import time_engine
from database import get_connection
from regime_engine import get_latest_regime
from sentiment_engine import (
    get_sentiment_html,
    get_vix_spy_html,
    get_yield_equity_html,
    get_uk_yield_equity_html,
    get_ftse_gbp_html
)
from market_pulse import get_all_cached_pulse, INDEX_TICKERS
from utils import normalize_ticker
from visuals import (
    create_macro_chart,
    create_intraday_chart,
    _intraday_market_tz,
    _EXCHANGE_DELAYS,
    create_us_liquidity_chart,
    create_us_credit_chart,
    create_uk_liquidity_chart,
    create_uk_credit_chart,
    create_yield_curve_chart,
    create_us_inflation_chart,
    create_uk_inflation_chart,
    create_anomaly_score_chart,
    create_anomaly_feature_radar,
    create_ai_contagion_performance_chart,
    create_ai_contagion_correlation_heatmap,
    create_etf_correlation_chart,
    create_etf_prediction_chart,
    create_etf_contributions_chart,
    create_etf_overlay_chart,
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


@page_router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    token = request.query_params.get("token", "")
    return templates.TemplateResponse(request=request, name="reset_password.html", context={"token": token})


@page_router.get("/admin-reset-password", response_class=HTMLResponse)
async def admin_reset_password_page(request: Request):
    from config import load_config
    if not load_config().get("FORCE_PASSWORD_RESET", False):
        from fastapi.responses import RedirectResponse as _Redir
        return _Redir("/login", status_code=302)
    return templates.TemplateResponse(request=request, name="admin_reset_password.html")


def get_json_data(filepath: str) -> Dict[str, Any]:
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def get_unread_count() -> int:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM system_notifications WHERE is_read = 0")
        return cursor.fetchone()['cnt']
    except Exception:
        return 0
    finally:
        if conn:
            conn.close()

def _build_position_sizing_context(config_data: dict, db_rows) -> dict:
    base_currency = config_data.get("BASE_CURRENCY", "GBP")
    
    currencies = set()
    for row in db_rows:
        try:
            cur = row["currency"] if "currency" in row.keys() else None
        except Exception:
            cur = None
        if cur:
            currencies.add(cur)
    currencies.add(base_currency)
    fx_rates = {}
    for cur in currencies:
        try:
            rate = get_rate_to_base(cur)
            if rate is not None and rate > 0:
                fx_rates[cur] = float(rate)
        except Exception:
            logger.warning("FX rate lookup failed for currency %s", cur, exc_info=True)
    fx_rates[base_currency] = 1.0
    
    return {
        "config":   get_position_sizing_config(config_data),
        "fx_rates": fx_rates,
        "base_currency": base_currency,
    }


INDEX_PARQUET_MAP: Dict[str, str] = {
    "^GSPC":    "SP500_BASELINE.parquet",
    "^FTSE":    "FTSE_BASELINE.parquet",
    "^TNX":     "TNX_BASELINE.parquet",
    "^TYX":     "TYX_BASELINE.parquet",
    "DX-Y.NYB": "DXY_BASELINE.parquet",
    "GBPUSD=X": "GBPUSD_BASELINE.parquet",
    "UK10YG":   "UK_GILT_BASELINE.parquet",
}

_INDEX_QUANT_DEFAULTS: Dict[str, Any] = dict.fromkeys([
    "rsi_14", "macd", "macd_signal", "macd_hist", "sma_50", "sma_200",
    "mom_1m", "mom_3m", "mom_6m", "mom_12m_skip1m", "hist_vol_20", "atr_pct",
    "var_95", "cvar_95", "sentiment_score", "volume", "volume_surge",
    "composite_score", "close_price", "date",
])

INDEX_CONTEXT_BLURBS: Dict[str, str] = {
    "^FTSE":    "The FTSE 100 tracks the 100 largest companies on the London Stock Exchange. Heavily weighted to mining, energy, and banks; often moves inversely to GBP strength.",
    "^FTMC":    "The FTSE 250 tracks mid-cap UK companies (ranks 101–350 on LSE). More domestically driven than the FTSE 100 — a purer barometer of UK economic health.",
    "GBPUSD=X": "GBP/USD exchange rate. Weakness boosts FTSE 100 exporters' translated earnings; strength signals UK economic confidence and tighter BoE policy expectations.",
    "BZ=F":     "Brent Crude Oil futures — the global benchmark for oil pricing. Elevated prices raise input costs across the economy and pressure rate-sensitive equities.",
    "UK10YG":   "The UK 10-Year Gilt Yield reflects sovereign borrowing costs and BoE monetary policy expectations. Rising yields compress equity multiples and increase corporate financing costs.",
    "^GSPC":    "The S&P 500 tracks 500 large-cap US equities — the primary benchmark for US equity market health and the foundation of most global asset allocation frameworks.",
    "^NDX":     "The Nasdaq 100 tracks the 100 largest non-financial companies on Nasdaq. Tech-heavy and highly sensitive to real interest rate expectations and liquidity conditions.",
    "^TYX":     "The US 30-Year Treasury Yield gauges long-term US borrowing costs and inflation expectations. Directly impacts mortgage rates and long-duration equity discount rates.",
    "^TNX":     "The US 10-Year Treasury Yield is the global risk-free rate benchmark. Rising yields tighten financial conditions, compress equity multiples, and strengthen the US Dollar.",
    "DX-Y.NYB": "The US Dollar Index (DXY) measures USD strength vs a basket of major currencies. A rising DXY tightens global dollar liquidity and pressures commodities and EM assets.",
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

def _fmt_currency(value) -> str | None:
    if value is None:
        return None
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 1e12:
        return f"{sign}${abs_val/1e12:.2f}T"
    if abs_val >= 1e9:
        return f"{sign}${abs_val/1e9:.2f}B"
    if abs_val >= 1e6:
        return f"{sign}${abs_val/1e6:.1f}M"
    return f"{sign}${abs_val:,.0f}"


def _fmt_volume(value) -> str | None:
    if value is None:
        return None
    if value >= 1e9:
        return f"{value/1e9:.1f}B"
    if value >= 1e6:
        return f"{value/1e6:.1f}M"
    if value >= 1e3:
        return f"{value/1e3:.0f}K"
    return str(int(value))


def _load_fundamentals_extra(ticker: str) -> dict:
    empty: dict = {
        "market_cap_fmt": None, "trailing_eps": None, "forward_eps": None,
        "earnings_growth": None, "free_cash_flow_fmt": None, "total_debt_fmt": None,
        "total_cash_fmt": None, "net_cash_fmt": None, "roa": None, "quick_ratio": None,
        "insider_ownership": None, "payout_ratio": None, "ex_dividend_date_fmt": None,
        "average_volume_fmt": None, "full_time_employees_fmt": None, "website": None,
    }
    path = FUNDAMENTALS_DIR / f"{ticker}.json"
    if not path.exists():
        return empty
    try:
        with open(path) as f:
            d = json.load(f)

        ex_div_fmt = None
        ex_div_ts = d.get("exDividendDate")
        if ex_div_ts:
            try:
                ex_div_fmt = datetime.fromtimestamp(ex_div_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                logger.warning("Could not parse exDividendDate timestamp %s", ex_div_ts)

        total_cash = d.get("totalCash")
        total_debt = d.get("totalDebt")
        net_cash = (total_cash - total_debt) if (total_cash is not None and total_debt is not None) else None

        employees = d.get("fullTimeEmployees")
        return {
            "market_cap_fmt": _fmt_currency(d.get("marketCap")),
            "trailing_eps": d.get("trailingEps"),
            "forward_eps": d.get("forwardEps"),
            "earnings_growth": d.get("earningsGrowth"),
            "free_cash_flow_fmt": _fmt_currency(d.get("freeCashflow")),
            "total_debt_fmt": _fmt_currency(total_debt),
            "total_cash_fmt": _fmt_currency(total_cash),
            "net_cash_fmt": _fmt_currency(net_cash),
            "roa": d.get("returnOnAssets"),
            "quick_ratio": d.get("quickRatio"),
            "insider_ownership": d.get("heldPercentInsiders"),
            "payout_ratio": d.get("payoutRatio"),
            "ex_dividend_date_fmt": ex_div_fmt,
            "average_volume_fmt": _fmt_volume(d.get("averageVolume")),
            "full_time_employees_fmt": f"{employees:,}" if employees else None,
            "website": d.get("website"),
        }
    except Exception:
        return empty


def _utc_str_to_local(s: str) -> str:
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return time_engine.fmt_datetime(dt)
    except Exception:
        return s


def enrich_macro_events(events_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for evt in events_list:
        evt_name = evt.get('event_name', '')
        evt['context'] = None
        evt['insight'] = ""
        evt['display_date'] = _utc_str_to_local(evt.get('event_date', ''))

        polarity = "neutral"
        for pattern, data in EVENT_GLOSSARY.items():
            if re.search(pattern, evt_name, re.IGNORECASE):
                evt['context'] = data["desc"]
                polarity = data["polarity"]
                break
        f_val = evt.get('forecast_val')
        p_val = evt.get('previous_val')
        
        if f_val is not None and p_val is not None:
            try:
                f_num = float(f_val)
                p_num = float(p_val)
                delta = f_num - p_num
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
    from scheduler_engine import scheduler_display_names
    from notification_engine import build_routing_panel
    config_data = load_config()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "config": config_data,
            "scheduler_job_labels": scheduler_display_names(),
            "notification_routing": build_routing_panel(config_data),
            "unread_count": get_unread_count(),
            "dashboard_username": os.environ.get("DASHBOARD_USERNAME", "admin"),
            "api_key": os.environ.get("API_KEY", ""),
            "confirm_token": os.environ.get("ADMIN_CONFIRM_TOKEN", ""),
            "nextcloud_url": os.environ.get("NEXTCLOUD_URL", ""),
            "nextcloud_bot_username": os.environ.get("NEXTCLOUD_BOT_USERNAME", ""),
            "nextcloud_app_password": os.environ.get("NEXTCLOUD_APP_PASSWORD", ""),
            "nextcloud_conversation_token": os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN", ""),
            "ghostfolio_url": os.environ.get("GHOSTFOLIO_URL", ""),
            "ghostfolio_token": os.environ.get("GHOSTFOLIO_TOKEN", ""),
            "fred_api_key": os.environ.get("FRED_API_KEY", ""),
            "hf_token": os.environ.get("HF_TOKEN", ""),
            "account_email": os.environ.get("ACCOUNT_EMAIL", ""),
        }
    )


def _parse_cb_nlp_message(msg_text: str, timestamp: str) -> dict | None:
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
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        horizon_48h = (now + timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        horizon_7d = (now + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        now_str = start_of_day.strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute("""
            SELECT * FROM macro_calendar 
            WHERE event_date BETWEEN ? AND ? 
            ORDER BY event_date ASC
        """, (now_str, horizon_48h))
        urgent_events = enrich_macro_events([dict(row) for row in cursor.fetchall()])

        cursor.execute("""
            SELECT * FROM macro_calendar 
            WHERE currency = 'USD' AND event_date BETWEEN ? AND ? 
            ORDER BY event_date ASC
        """, (now_str, horizon_7d))
        us_events = enrich_macro_events([dict(row) for row in cursor.fetchall()])

        cursor.execute("""
            SELECT * FROM macro_calendar 
            WHERE currency = 'GBP' AND event_date BETWEEN ? AND ? 
            ORDER BY event_date ASC
        """, (now_str, horizon_7d))
        uk_events = enrich_macro_events([dict(row) for row in cursor.fetchall()])

        cb_nlp_latest = None
        cb_row = cursor.execute(
            "SELECT message_text, timestamp FROM system_notifications "
            "WHERE message_type = 'Macro NLP' ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if cb_row:
            cb_nlp_latest = _parse_cb_nlp_message(cb_row["message_text"], cb_row["timestamp"])

        ai_contagion_status = []
        try:
            rows = cursor.execute(
                "SELECT scan_ts, leader_count, etf_count, alert_fired, payload_json "
                "FROM ai_contagion_snapshots ORDER BY scan_ts DESC LIMIT 5"
            ).fetchall()
            for r in rows:
                raw = json.loads(r["payload_json"] or '{"tickers":[],"severity_score":0.0}')
                if isinstance(raw, list):
                    tickers, severity_score = raw, 0.0
                else:
                    tickers, severity_score = raw.get("tickers", []), raw.get("severity_score", 0.0)
                ai_contagion_status.append({
                    "scan_ts": _utc_str_to_local(r["scan_ts"]),
                    "leader_count": r["leader_count"],
                    "etf_count": r["etf_count"],
                    "alert_fired": bool(r["alert_fired"]),
                    "tickers": tickers,
                    "severity_score": severity_score,
                })
        except Exception:
            pass  # table absent on first boot — silently ignore

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
            logger.error("Error processing macro indicators matrix: %s", e)
            df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            df_us_cpi, df_uk_cpi = pd.DataFrame(), pd.DataFrame()

        try:
            df_spy = pd.read_parquet(HISTORICAL_DIR / "SP500_BASELINE.parquet")
        except Exception:
            df_spy = pd.DataFrame()
            
        try:
            df_ftse = pd.read_parquet(HISTORICAL_DIR / "FTSE_BASELINE.parquet")
        except Exception:
            df_ftse = pd.DataFrame()
        us_liquidity_html = create_us_liquidity_chart(df_spy, df_m2)
        us_credit_html = create_us_credit_chart(df_us_hy)
        uk_liquidity_html = create_uk_liquidity_chart(df_ftse, df_m4)
        uk_credit_html = create_uk_credit_chart(df_uk_ig)
        yield_curve_html = create_yield_curve_chart(df_yield_curve)
        us_inflation_html = create_us_inflation_chart(df_spy, df_us_cpi)
        uk_inflation_html = create_uk_inflation_chart(df_ftse, df_uk_cpi)

    except Exception as e:
        logger.error("Fatal error in market_sentiment route: %s", e)
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
        ai_contagion_status = []
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
            "ai_contagion_status": ai_contagion_status,
            "user_tz_label": time_engine.now_local().strftime("%Z"),
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
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM system_notifications ORDER BY timestamp DESC LIMIT 100")
        notifications = cursor.fetchall()
    finally:
        conn.close()
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"notifications": notifications, "unread_count": get_unread_count()}
    )


_ASSETS_DIR = Path(__file__).parent / "assets"
_MD = _markdown.Markdown(extensions=["tables", "fenced_code"])


def _render_asset_docs() -> list[dict]:
    docs = []
    for md_path in sorted(_ASSETS_DIR.glob("*.md")):
        raw = md_path.read_text(encoding="utf-8")
        title = md_path.stem.replace("_", " ").title()
        for line in raw.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        _MD.reset()
        html = _MD.convert(raw)
        slug = "doc-" + md_path.stem.lower().replace("_", "-")
        docs.append({"title": title, "slug": slug, "html": html})
    return docs


@page_router.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="glossary.html",
        context={
            "unread_count": get_unread_count(),
            "prediction_horizon": PREDICTION_HORIZON_DAYS,
            "prediction_threshold_pct": int(PREDICTION_RETURN_THRESHOLD * 100),
            "asset_docs": _render_asset_docs(),
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
               q.vp_entry_zone,
               q.vp_exit_zone,
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

    portfolio_data.sort(key=lambda x: x['ticker'])

    live_pulse = get_all_cached_pulse()

    for row_dict in portfolio_data:
        row_dict['market_value_base'] = None
        row_dict['global_market_value'] = None
        row_dict['global_unrealized_pnl'] = None
        row_dict['global_unrealized_pnl_pct'] = None
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
            row_dict['market_value_base'] = round(val_in_base, 2)

            summary_math["value"] += val_in_base
            summary_math["cost"] += cost_in_base

            # Global aggregation — use live pulse price (same source as Price column)
            # to avoid stale DB price diverging from what the user sees
            pulse_entry = live_pulse.get(row_dict['ticker'])
            live_price = (pulse_entry['price'] if pulse_entry and pulse_entry['price'] > 0
                          else row_dict['current_price'])
            global_shares = asset.get('global_shares', 0)
            global_buy_price = asset.get('global_buy_price', 0)
            global_cost = global_shares * global_buy_price
            global_val = (global_shares * live_price) * exchange_rate
            row_dict['global_market_value'] = round(global_val, 2)
            global_pnl = global_val - global_cost
            row_dict['global_unrealized_pnl'] = round(global_pnl, 2)
            row_dict['global_unrealized_pnl_pct'] = round((global_pnl / global_cost) * 100, 2) if global_cost else None

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
            "cached_pulse": live_pulse,
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
               q.vp_entry_zone,
               q.vp_exit_zone,
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

    watchlist_data.sort(key=lambda x: x['ticker'])

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


@page_router.get("/news", response_class=HTMLResponse)
async def news_page(request: Request):
    config_data = load_config()
    return templates.TemplateResponse(
        request=request,
        name="news.html",
        context={
            "unread_count": get_unread_count(),
            "config": config_data,
        },
    )


@page_router.get("/earnings-volatility", response_class=HTMLResponse)
async def earnings_volatility_page(request: Request):
    today_str = time_engine.now_local().strftime('%Y-%m-%d')
    
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
    import os as _os
    today = time_engine.now_local()
    target_date = today.strftime('%Y-%m-%d')

    reports_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "reports")

    # Prefer the most recently generated briefing (lunch takes precedence over morning when both
    # exist for today; fall back to yesterday if nothing generated today yet).
    def _best_briefing(date_str):
        candidates = []
        for prefix in ("lunch_briefing", "morning_briefing"):
            f = _os.path.join(reports_dir, f"{prefix}_{date_str}.md")
            if _os.path.exists(f):
                candidates.append((f, _os.path.getmtime(f)))
        return max(candidates, key=lambda x: x[1]) if candidates else None

    yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    best = _best_briefing(target_date) or _best_briefing(yesterday)

    if best:
        best_file, _ = best
        base = _os.path.basename(best_file)
        target_date = yesterday if yesterday in base else target_date
        try:
            with open(best_file, "r", encoding="utf-8") as f:
                markdown_content = f.read()
        except Exception:
            markdown_content = None
    else:
        markdown_content = None

    if not markdown_content:
        signals = fetch_latest_signals(target_date)
        if not signals:
            target_date = yesterday
            signals = fetch_latest_signals(target_date)

        if signals:
            markdown_content = generate_markdown_briefing(target_date, signals)
        else:
            markdown_content = (
                f"# 📊 Morning Quant Briefing\n"
                f"**Date:** {target_date}\n\n"
                f"*No briefing generated yet today. Use the Run Morning Briefing Now button in Settings, "
                f"or wait for the scheduled run. Ensure the overnight quant scan is running.*"
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


@page_router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request):
    lse_open_utc, _ = time_engine.market_window_utc("LSE")
    lse_open_dt = datetime.combine(datetime.now(timezone.utc).date(), lse_open_utc, tzinfo=timezone.utc)
    lse_open_str = time_engine.fmt_time(lse_open_dt)
    return templates.TemplateResponse(
        request=request,
        name="tools.html",
        context={"unread_count": get_unread_count(), "lse_open_time": lse_open_str},
    )


@page_router.get("/dip-radar", response_class=HTMLResponse)
async def dip_radar_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dip_radar_summary.html",
        context={"unread_count": get_unread_count()},
    )




@page_router.get("/trap-monitor", response_class=HTMLResponse)
async def trap_monitor_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="trap_monitor.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/market-regime", response_class=HTMLResponse)
async def market_regime_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="market_regime.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/etf-predictor", response_class=HTMLResponse)
async def etf_predictor_index_page(request: Request):
    from database import get_etf_predictor_configs, get_etf_accuracy
    configs = get_etf_predictor_configs()
    tiles = []
    for cfg in configs:
        accuracy = get_etf_accuracy(cfg["id"])
        rows = accuracy["next_open"]["rows"]
        last_row = rows[0] if rows else None
        last_resolved = next((r for r in rows if r.get("actual_open") is not None), None)
        tiles.append({
            "config": cfg,
            "last_prediction": last_row,
            "last_resolved": last_resolved,
            "summary": accuracy["next_open"]["summary"],
        })
    return templates.TemplateResponse(
        request=request,
        name="etf_predictor.html",
        context={
            "tiles": tiles,
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/etf-predictor/{config_id}", response_class=HTMLResponse)
async def etf_predictor_detail_page(request: Request, config_id: int):
    from database import get_etf_predictor_config
    from etf_predictor_engine import (
        detect_etf_info, run_prediction,
        get_etf_correlation_data, get_etf_intraday_overlay_data,
    )
    cfg = get_etf_predictor_config(config_id)
    if cfg is None:
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={"unread_count": get_unread_count()},
            status_code=404,
        )

    error_html = "<p class='error-text'>Data unavailable — please try again later.</p>"
    etf_info = detect_etf_info(cfg["etf_ticker"])
    constituent_tickers = [h["ticker"] for h in cfg["constituents"]]

    try:
        prediction = run_prediction(config_id)
    except Exception as exc:
        logger.error("etf_predictor_detail run_prediction failed: %s", exc)
        prediction = {"status": "error", "error": str(exc), "predicted_price": None}

    correlation_chart_html = error_html
    prediction_chart_html = error_html
    contributions_chart_html = ""
    overlay_chart_html = error_html

    try:
        corr_data = get_etf_correlation_data(cfg, days=60)
        if not corr_data["normalized_df"].empty:
            correlation_chart_html = create_etf_correlation_chart(
                cfg["etf_ticker"],
                constituent_tickers,
                corr_data["normalized_df"],
                corr_data["rolling_corr"],
            )
    except Exception as exc:
        logger.warning("etf_predictor_detail corr chart failed: %s", exc)

    try:
        raw_df = corr_data.get("raw_df", pd.DataFrame())
        etf_hist = None
        if not raw_df.empty and cfg["etf_ticker"] in raw_df.columns:
            etf_hist = raw_df[cfg["etf_ticker"]].dropna().tail(25)
        prediction_chart_html = create_etf_prediction_chart(
            cfg["etf_ticker"], etf_info["currency"], etf_hist, prediction
        )
        if prediction.get("holdings_engine") and prediction["holdings_engine"].get("contributions"):
            contributions_chart_html = create_etf_contributions_chart(
                cfg["etf_ticker"], prediction["holdings_engine"]["contributions"]
            )
    except Exception as exc:
        logger.warning("etf_predictor_detail pred charts failed: %s", exc)

    try:
        overlay_data = get_etf_intraday_overlay_data(cfg, prediction)
        overlay_chart_html = create_etf_overlay_chart(
            cfg["etf_ticker"],
            etf_info["exchange"],
            overlay_data["constituent_exchanges"],
            overlay_data["etf_series"],
            overlay_data["constituent_series"],
            overlay_data["etf_last_close"],
            prediction=overlay_data["prediction"],
            next_open_date=overlay_data["next_open_date"],
            constituent_prev_closes=overlay_data.get("constituent_prev_closes"),
            now_utc=overlay_data.get("now_utc"),
            trading_date=overlay_data.get("trading_date"),
            session_relationship=overlay_data.get("session_relationship", "behind"),
        )
    except Exception as exc:
        logger.warning("etf_predictor_detail overlay chart failed: %s", exc)

    etf_pnl = None
    try:
        portfolio = get_json_data(PORTFOLIO_PATH)
        position = next((v for v in portfolio.values() if v.get("ticker") == cfg["etf_ticker"]), None)
        if position and prediction.get("status") == "success":
            shares = float(position.get("global_shares", 0))
            avg_buy = float(position.get("global_buy_price", 0))
            last_close = prediction.get("last_etf_close", 0)
            pred_price = prediction.get("predicted_price", 0)
            if shares > 0 and pred_price and last_close:
                predicted_value = shares * pred_price
                current_value = shares * last_close
                cost_basis = shares * avg_buy
                etf_pnl = {
                    "shares": round(shares, 4),
                    "avg_buy_price": round(avg_buy, 4),
                    "current_value": round(current_value, 2),
                    "predicted_value": round(predicted_value, 2),
                    "predicted_pnl_open": round(predicted_value - current_value, 2),
                    "total_unrealised_pnl": round(predicted_value - cost_basis, 2),
                }
    except Exception:
        pass

    return templates.TemplateResponse(
        request=request,
        name="etf_predictor_detail.html",
        context={
            "cfg": cfg,
            "etf_info": etf_info,
            "prediction": prediction,
            "correlation_chart_html": correlation_chart_html,
            "prediction_chart_html": prediction_chart_html,
            "contributions_chart_html": contributions_chart_html,
            "overlay_chart_html": overlay_chart_html,
            "etf_pnl": etf_pnl,
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/stress-test", response_class=HTMLResponse)
async def stress_test_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="stress_test.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/ai-contagion", response_class=HTMLResponse)
async def ai_contagion_page(request: Request):
    from ai_contagion_engine import get_ai_contagion_data
    error_html = "<p class='error-text'>Data unavailable — please try again later.</p>"
    try:
        data = get_ai_contagion_data(days=30)
        daily_dfs = data["daily_dfs"]
        intraday_dfs = data["intraday_dfs"]

        perf_daily_html = create_ai_contagion_performance_chart(daily_dfs, period_label="30-Day")
        perf_intraday_html = create_ai_contagion_performance_chart(intraday_dfs, period_label="Intraday") if intraday_dfs else ""
        corr_html = create_ai_contagion_correlation_heatmap(daily_dfs, window=20)
    except Exception as exc:
        logger.error("ai_contagion_page failed: %s", exc)
        perf_daily_html = error_html
        perf_intraday_html = ""
        corr_html = error_html

    return templates.TemplateResponse(
        request=request,
        name="ai_contagion.html",
        context={
            "perf_daily_html": perf_daily_html,
            "perf_intraday_html": perf_intraday_html,
            "corr_html": corr_html,
            "unread_count": get_unread_count(),
        },
    )


@page_router.get("/score-history", response_class=HTMLResponse)
async def score_history_page(request: Request, filter: str = "all", ref: str = ""):
    from score_analysis import get_score_analysis
    valid_filters = {"all", "portfolio", "watchlist"}
    active_filter = filter if filter in valid_filters else "all"
    data = get_score_analysis(active_filter)
    return templates.TemplateResponse(
        request=request,
        name="score_history.html",
        context={
            "data": data,
            "active_filter": active_filter,
            "back_url": ref if ref else None,
            "unread_count": get_unread_count(),
            "config": load_config(),
        }
    )


@page_router.get("/index/{ticker}", response_class=HTMLResponse)
async def index_detail(request: Request, ticker: str):
    ticker = normalize_ticker(ticker)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT ticker, name, price, change_pts, change_pct, is_positive FROM market_pulse_cache WHERE ticker = ?",
        (ticker,)
    )
    pulse_row = cursor.fetchone()
    pulse = dict(pulse_row) if pulse_row else {
        "price": None, "change_pts": None, "change_pct": None,
        "is_positive": None, "name": INDEX_TICKERS.get(ticker, ticker),
    }

    cursor.execute("""
        SELECT rsi_14, macd, macd_signal, macd_hist,
               sma_50, sma_200, mom_1m, mom_3m, mom_6m, mom_12m_skip1m,
               hist_vol_20, atr_pct, var_95, cvar_95,
               sentiment_score, volume, volume_surge, composite_score, close_price, date
        FROM quant_signals
        WHERE ticker = ?
        ORDER BY date DESC LIMIT 1
    """, (ticker,))
    q_row = cursor.fetchone()
    quant = {**_INDEX_QUANT_DEFAULTS, **(dict(q_row) if q_row else {})}

    cursor.execute("SELECT currency FROM asset_profiles WHERE ticker = ?", (ticker,))
    ap = cursor.fetchone()
    currency = ap["currency"] if ap else "USD"
    conn.close()

    price_action = None
    # Prefer fresh per-ticker parquet (written by /api/index/refresh); fall back to shared baseline
    _ticker_parquet = HISTORICAL_DIR / f"{ticker}.parquet"
    _baseline_name = INDEX_PARQUET_MAP.get(ticker)
    parquet_path = _ticker_parquet if _ticker_parquet.exists() else (HISTORICAL_DIR / _baseline_name if _baseline_name else None)
    try:
        df_macro = pd.read_parquet(parquet_path) if parquet_path else pd.DataFrame()
        if df_macro.empty:
            raise FileNotFoundError
        macro_html = create_macro_chart(df_macro, None, ticker)
        last_day = df_macro.iloc[-1]
        prev_day = df_macro.iloc[-2] if len(df_macro) > 1 else last_day
        last_21 = df_macro.tail(21)
        P = (prev_day['High'] + prev_day['Low'] + prev_day['Close']) / 3
        price_action = {
            "day_low": last_day['Low'], "day_high": last_day['High'],
            "month_low": last_21['Low'].min(), "month_high": last_21['High'].max(),
            "s1": (P * 2) - prev_day['High'],
            "s2": P - (prev_day['High'] - prev_day['Low']),
        }
    except FileNotFoundError:
        macro_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:180px;gap:10px;color:#888;'><span style='font-size:2rem;'>📭</span><span style='font-weight:600;'>No historical data yet</span></div>"
    except Exception as e:
        macro_html = f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:180px;gap:8px;color:#888;'><span style='font-size:2rem;'>⚠️</span><span style='font-weight:600;'>Chart unavailable</span><span style='font-size:0.85rem;'>{type(e).__name__}: {e}</span></div>"

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        s1 = price_action['s1'] if price_action else None
        s2 = price_action['s2'] if price_action else None
        mkt_tz = _intraday_market_tz(ticker, currency)
        delay_min = _EXCHANGE_DELAYS.get(currency, 0)
        intraday_html = create_intraday_chart(df_intraday, ticker, s1=s1, s2=s2,
                                              market_tz=mkt_tz, data_delay_minutes=delay_min)
    except FileNotFoundError:
        intraday_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:120px;gap:10px;color:#888;'><span style='font-size:1.8rem;'>📭</span><span style='font-weight:600;'>No intraday data yet</span></div>"
    except Exception:
        intraday_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:120px;gap:8px;color:#888;'><span style='font-size:1.8rem;'>⚠️</span><span style='font-weight:600;'>Intraday data unavailable</span></div>"

    return templates.TemplateResponse(
        request=request, name="index_detail.html",
        context={
            "ticker":        ticker,
            "display_name":  INDEX_TICKERS.get(ticker, ticker),
            "pulse":         pulse,
            "quant":         quant,
            "currency":      currency,
            "context_blurb": INDEX_CONTEXT_BLURBS.get(ticker, ""),
            "macro_html":    macro_html,
            "intraday_html": intraday_html,
            "price_action":  price_action,
            "unread_count":  get_unread_count(),
            "config":        load_config(),
            "css_version":   CSS_VERSION,
        }
    )


@page_router.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str, embed: bool = False):
    ticker = normalize_ticker(ticker)
    if ticker in INDEX_TICKERS:
        return RedirectResponse(f"/index/{ticker}", status_code=302)
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
               q.atr_pct, q.volume, q.volume_surge, q.bullish_cross,
               q.macd, q.macd_signal, q.macd_hist,
               q.sma_50, q.sma_200,
               q.mom_1m, q.mom_3m, q.mom_6m, q.mom_12m_skip1m,
               q.hist_vol_20, q.rel_strength_5d, q.rel_strength_20d,
               q.anomaly_score,
               q.vp_poc, q.vp_val, q.vp_vah, q.vp_entry_zone, q.vp_exit_zone,
               q.kc_z_score, q.kc_entry_signal, q.kc_exit_signal,
               q.price_q10, q.price_q90,
               mu.industry, mu.index_membership,
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
        _cp = stock_data.get("current_price") or 0.0
        stock_data["trend_50d"] = "UP" if stock_data.get("sma_50") and _cp > stock_data["sma_50"] else "DOWN"
        stock_data["trend_200d"] = "UP" if stock_data.get("sma_200") and _cp > stock_data["sma_200"] else "DOWN"
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
                   p.business_summary,
                   m.industry, m.index_membership
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
                "sector_weightings": None,
                "volume": q_data.get("volume"),
                "volume_surge": q_data.get("volume_surge"),
                "bullish_cross": q_data.get("bullish_cross"),
                "macd": q_data.get("macd"),
                "macd_signal": q_data.get("macd_signal"),
                "macd_hist": q_data.get("macd_hist"),
                "sma_50": q_data.get("sma_50"),
                "sma_200": q_data.get("sma_200"),
                "mom_1m": q_data.get("mom_1m"),
                "mom_3m": q_data.get("mom_3m"),
                "mom_6m": q_data.get("mom_6m"),
                "mom_12m_skip1m": q_data.get("mom_12m_skip1m"),
                "hist_vol_20": q_data.get("hist_vol_20"),
                "rel_strength_5d": q_data.get("rel_strength_5d"),
                "rel_strength_20d": q_data.get("rel_strength_20d"),
                "anomaly_score": q_data.get("anomaly_score"),
                "industry": q_data.get("industry"),
                "index_membership": q_data.get("index_membership"),
                "vp_poc": q_data.get("vp_poc"),
                "vp_val": q_data.get("vp_val"),
                "vp_vah": q_data.get("vp_vah"),
                "vp_entry_zone": q_data.get("vp_entry_zone"),
                "vp_exit_zone": q_data.get("vp_exit_zone"),
                "kc_z_score": q_data.get("kc_z_score"),
                "kc_entry_signal": q_data.get("kc_entry_signal"),
                "kc_exit_signal": q_data.get("kc_exit_signal"),
                "price_q10": q_data.get("price_q10"),
                "price_q90": q_data.get("price_q90"),
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
                "sector_weightings": None,
                "volume": None, "volume_surge": None, "bullish_cross": None,
                "macd": None, "macd_signal": None, "macd_hist": None,
                "sma_50": None, "sma_200": None,
                "mom_1m": None, "mom_3m": None, "mom_6m": None, "mom_12m_skip1m": None,
                "hist_vol_20": None, "rel_strength_5d": None, "rel_strength_20d": None,
                "anomaly_score": None,
                "industry": None, "index_membership": None,
                "vp_poc": None, "vp_val": None, "vp_vah": None,
                "vp_entry_zone": None, "vp_exit_zone": None,
                "kc_z_score": None, "kc_entry_signal": None, "kc_exit_signal": None,
                "price_q10": None, "price_q90": None,
            }

    # --- earnings_volatility enrichment ---
    earnings_vol: dict = {}
    if stock_data:
        cursor.execute('''
            SELECT implied_move_pct, historical_avg_move_pct, edge_score, options_volume
            FROM earnings_volatility WHERE ticker = ?
        ''', (ticker,))
        ev_row = cursor.fetchone()
        if ev_row:
            earnings_vol = dict(ev_row)

    conn.close()

    fundamentals_extra = _load_fundamentals_extra(ticker)

    data_status = 'red'
    last_updated_str = "Never"
    if stock_data and stock_data.get('last_updated'):
        last_updated_str = stock_data['last_updated']
        try:
            lu_date = datetime.strptime(last_updated_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - lu_date < timedelta(hours=24):
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
            logger.warning("Failed to parse top_holdings JSON for %s", ticker, exc_info=True)
    if stock_data and stock_data.get('sector_weightings'):
        try:
            sector_weightings = json.loads(stock_data['sector_weightings'])
        except Exception:
            logger.warning("Failed to parse sector_weightings JSON for %s", ticker, exc_info=True)

    days_to_earnings = None
    volatility_date = None
    if stock_data and stock_data.get('next_earnings_date') and stock_data['next_earnings_date'] != 'Unknown':
        try:
            e_date = datetime.strptime(stock_data['next_earnings_date'], '%Y-%m-%d').date()
            today = time_engine.now_local().date()
            days_to_earnings = (e_date - today).days
            volatility_date = (e_date - timedelta(days=7)).strftime('%Y-%m-%d')
        except Exception:
            logger.warning("Could not parse next_earnings_date for %s: %s", ticker, stock_data.get('next_earnings_date'))

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
        mkt_tz = _intraday_market_tz(ticker, currency)
        delay_min = _EXCHANGE_DELAYS.get(currency, 0)
        intraday_html = create_intraday_chart(
            df_intraday, ticker, s1=s1_val, s2=s2_val,
            live_pattern_name=live_pattern_name,
            live_pattern_tooltip=live_pattern_tooltip,
            live_pattern_score=live_pattern_score,
            market_tz=mkt_tz,
            data_delay_minutes=delay_min,
        )
    except FileNotFoundError:
        intraday_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:120px;gap:10px;color:#888;'><span style='font-size:1.8rem;'>📭</span><span style='font-weight:600;'>No intraday data yet</span><span style='font-size:0.85rem;'>Press <strong>Refresh</strong> above to fetch today's intraday data.</span></div>"
    except Exception:
        intraday_html = "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:120px;gap:8px;color:#888;'><span style='font-size:1.8rem;'>⚠️</span><span style='font-weight:600;'>Intraday data unavailable</span></div>"
    
    config_data = load_config()
    fake_rows = [{"currency": stock_data.get("currency", "USD")}]
    position_sizing_context = _build_position_sizing_context(config_data, fake_rows)
    anomaly_chart_html = (
        "<div style='display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;height:180px;gap:10px;color:#888;'>"
        "<span style='font-size:2rem;'>📊</span>"
        "<span style='font-weight:600;'>No anomaly data yet</span>"
        "<span style='font-size:0.85rem;'>Scores are written during market hours once models are trained.</span>"
        "</div>"
    )
    anomaly_percentile = None
    anomaly_radar_html = None
    try:
        conn_a = get_connection()
        anomaly_rows = conn_a.execute(
            "SELECT date, anomaly_score, close_price FROM quant_signals "
            "WHERE ticker = ? AND anomaly_score IS NOT NULL "
            "ORDER BY date DESC LIMIT 90",
            (ticker,),
        ).fetchall()
        conn_a.close()
        if anomaly_rows:
            df_anomaly = pd.DataFrame(
                [(r["date"], r["anomaly_score"], r["close_price"]) for r in anomaly_rows],
                columns=["date", "anomaly_score", "close_price"],
            )
            df_anomaly["date"] = pd.to_datetime(df_anomaly["date"])
            df_anomaly.set_index("date", inplace=True)
            df_anomaly.sort_index(inplace=True)  # DESC fetch → re-sort ASC for chart
            anomaly_threshold = float(
                config_data.get("NOTIFICATIONS", {}).get("ANOMALY_ALERTS", {}).get("THRESHOLD", 0.7)
            )
            anomaly_chart_html = create_anomaly_score_chart(df_anomaly, ticker, threshold=anomaly_threshold)

            latest_score = df_anomaly["anomaly_score"].iloc[-1]
            history = df_anomaly["anomaly_score"]
            anomaly_percentile = round(float((history <= latest_score).mean() * 100), 1)

            current_price = stock_data.get("current_price") or 0.0
            sma_50 = stock_data.get("sma_50") or current_price
            sma50_dist_pct = ((current_price - sma_50) / sma_50 * 100) if sma_50 else 0.0
            radar_features = {
                "volume_ratio":     stock_data.get("volume_surge") or 1.0,
                "rsi_14":           stock_data.get("rsi_14") or 50.0,
                "daily_return_pct": stock_data.get("mom_1m") or 0.0,
                "sma50_dist_pct":   sma50_dist_pct,
                "hist_vol_20":      stock_data.get("hist_vol_20") or 0.2,
                "beta":             stock_data.get("beta") or 1.0,
            }
            anomaly_radar_html = create_anomaly_feature_radar(radar_features, ticker)
    except Exception:
        pass  # fallback placeholder already set

    is_dip_monitored = False
    conn_dip = None
    try:
        conn_dip = get_connection()
        _today = datetime.now(timezone.utc).date().isoformat()
        dip_row = conn_dip.execute(
            "SELECT 1 FROM intraday_monitors WHERE ticker = ? AND is_active = 1 AND expire_date >= ?",
            (ticker, _today),
        ).fetchone()
        is_dip_monitored = bool(dip_row)
    except Exception:
        pass
    finally:
        if conn_dip:
            conn_dip.close()

    return templates.TemplateResponse(
        request=request, name="stock_detail.html",
        context={
            "stock": stock_data,
            "top_holdings": top_holdings,
            "sector_weightings": sector_weightings,
            "macro_html": macro_html,
            "intraday_html": intraday_html,
            "anomaly_chart_html": anomaly_chart_html,
            "anomaly_radar_html": anomaly_radar_html,
            "anomaly_percentile": anomaly_percentile,
            "portfolio_math": portfolio_math,
            "days_to_earnings": days_to_earnings,
            "volatility_date": volatility_date,
            "price_action": price_action,
            "unread_count": get_unread_count(),
            "embed": embed,
            "config": load_config(),
            "cached_pulse": get_all_cached_pulse(),
            "is_in_watchlist": is_in_watchlist,
            "is_dip_monitored": is_dip_monitored,
            "data_status": data_status,
            "last_updated_str": last_updated_str,
            "position_sizing": position_sizing_context,
            "earnings_vol": earnings_vol,
            "fundamentals_extra": fundamentals_extra,
        }
    )


def _build_rss_base_url(server_url: str, port: int) -> str:
    base = str(server_url).rstrip('/')
    parsed = urlparse(base if "://" in base else f"http://{base}")
    hostname = parsed.hostname or ""
    is_local = hostname == "localhost"
    if not is_local:
        try:
            ipaddress.ip_address(hostname)
            is_local = True
        except ValueError:
            pass
    if is_local and parsed.port is None:
        return f"{base}:{port}"
    return base


@page_router.get("/rss/alerts.xml")
async def rss_alerts_feed():
    cfg = load_config()
    if not cfg.get("NOTIFICATIONS", {}).get("RSS_FEED", {}).get("ENABLED", False):
        return Response(status_code=404)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, message_type, message_text, timestamp FROM system_notifications "
            "WHERE message_type IN ('Crash', 'Moonshot') ORDER BY id DESC LIMIT 50"
        )
        rows = cursor.fetchall()
    finally:
        if conn:
            conn.close()

    base_url = _build_rss_base_url(
        cfg.get("SERVER_URL", "http://localhost"),
        cfg.get("PORT", 8090)
    )
    now_str = email.utils.formatdate(usegmt=True)

    items = []
    for row in rows:
        try:
            dt = datetime.strptime(row["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            pub_date = dt.replace(tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            pub_date = now_str

        msg_type = row["message_type"]
        msg_text = row["message_text"] or ""

        m = re.search(r"triggered for ([A-Z0-9.\-\^=]+)\.", msg_text)
        ticker = m.group(1) if m else "Unknown"

        title = html_escape(f"{msg_type} Alert — {ticker}")
        desc = html_escape(msg_text.replace("**", ""))
        link = html_escape(f"{base_url}/stock/{ticker}")

        items.append(
            f"    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <description>{desc}</description>\n"
            f"      <link>{link}</link>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <guid isPermaLink=\"false\">alert-{row['id']}</guid>\n"
            f"    </item>"
        )

    feed_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        '  <channel>\n'
        '    <title>Quantamental Dashboard &#8212; Crash &amp; Moonshot Alerts</title>\n'
        f'    <link>{html_escape(base_url)}</link>\n'
        '    <description>Real-time intraday crash and moonshot alerts from your portfolio scanner</description>\n'
        f'    <lastBuildDate>{now_str}</lastBuildDate>\n'
        + ("\n".join(items) + "\n" if items else "")
        + '  </channel>\n'
        '</rss>'
    )

    return Response(content=feed_xml, media_type="application/rss+xml")

@page_router.get("/log-viewer", response_class=HTMLResponse)
async def log_viewer_page(request: Request):
    cfg = load_config()
    fl = cfg.get("FILE_LOGGING", {})
    logging_enabled = fl.get("ENABLED", False)
    return templates.TemplateResponse(
        request=request,
        name="log_viewer.html",
        context={"logging_enabled": logging_enabled},
    )
