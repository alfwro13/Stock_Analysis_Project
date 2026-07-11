import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

import pandas as pd
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config, HISTORICAL_DIR, INTRADAY_DIR
import time_engine
from database import get_connection, get_auction_summary, get_ticker_registry_row, get_ticker_registry_row_by_future
from macro_data_engine import get_uk_cpi_yoy_series
from regime_engine import get_latest_regime
from sentiment_engine import (
    get_sentiment_html,
    get_vix_spy_html,
    get_yield_equity_html,
    get_uk_yield_equity_html,
    get_ftse_gbp_html,
)
import markets_engine
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
)
from constants import CSS_VERSION
from page_helpers import get_unread_count, _utc_str_to_local

logger = logging.getLogger(__name__)

page_router_macro = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


_INDEX_QUANT_DEFAULTS: Dict[str, Any] = dict.fromkeys([
    "rsi_14", "macd", "macd_signal", "macd_hist", "sma_50", "sma_200",
    "mom_1m", "mom_3m", "mom_6m", "mom_12m_skip1m", "hist_vol_20", "atr_pct",
    "var_95", "cvar_95", "sentiment_score", "volume", "volume_surge",
    "composite_score", "close_price", "date",
])

EVENT_GLOSSARY = {
    r"\bcpi\b": {"desc": "Consumer Price Index. The primary measure of inflation. Higher than expected forces Central Banks to keep rates high (Bearish for equities).", "polarity": "inverse"},
    r"\bppi\b": {"desc": "Producer Price Index. Measures wholesale inflation before it reaches consumers. A leading indicator for future CPI.", "polarity": "inverse"},
    r"\brpi\b|retail price index": {"desc": "Retail Price Index. An older UK inflation measure, still used heavily for wage and contract pricing negotiations.", "polarity": "inverse"},
    r"house price index": {"desc": "Measures housing inflation and consumer wealth effect.", "polarity": "direct"},
    r"\bfomc\b": {"desc": "Federal Open Market Committee. The Fed's policy body. Their rate decisions and minutes dictate global liquidity.", "polarity": "neutral"},
    r"\bboe\b": {"desc": "Bank of England. The UK's central bank. Sets base rates impacting GBP and UK equities.", "polarity": "neutral"},
    r"fed's.*speech|boe's.*speech": {"desc": "Central Bank Speaker. Unscheduled volatility risk. Markets scan these speeches for hawkish or dovish policy hints.", "polarity": "neutral"},
    r"auction": {"desc": "Sovereign Debt Auction (Bonds/Bills). Weak demand can cause Treasury yields to spike, triggering algorithmic equity sell-offs.", "polarity": "neutral"},
    r"\bpmi\b": {"desc": "Purchasing Managers' Index. A leading indicator of economic health. >50.0 indicates expansion; <50.0 indicates contraction/recession.", "polarity": "threshold"},
    r"\bgdp\b": {"desc": "Gross Domestic Product. The total value of goods produced. High GDP is bullish, but too hot can trigger inflation fears.", "polarity": "direct"},
    r"retail sales": {"desc": "Measures consumer spending, which makes up the majority of Western economic growth.", "polarity": "direct"},
    r"fed manufacturing|empire state|fed activity": {"desc": "Regional Fed Surveys (e.g., Philly, Kansas, NY). Early localized indicators of manufacturing health before national PMI data drops.", "polarity": "direct"},
    r"non-farm|nfp": {"desc": "Non-Farm Payrolls. US employment data. Strong jobs data can be bearish if it forces the Fed to keep rates high to cool the economy.", "polarity": "direct"},
    r"claimant count": {"desc": "UK Unemployment. The change in the number of people claiming jobless benefits. A rising number indicates economic weakness.", "polarity": "inverse"},
    r"jobless claims": {"desc": "US Unemployment filings. Rising claims signal a cooling labor market, which can be perversely bullish if the market expects rate cuts.", "polarity": "inverse"},
    r"unemployment rate": {"desc": "Percentage of the total labor force that is unemployed. Serves as a primary mandate metric for central bank policy.", "polarity": "inverse"},
    r"building permits|housing starts": {"desc": "Leading indicators for the construction sector and broader economic health. Highly sensitive to interest rates.", "polarity": "direct"},
    r"mortgage applications": {"desc": "A direct measure of housing demand. Drops significantly when bond yields/mortgage rates rise.", "polarity": "direct"},
    r"crude oil|natural gas": {"desc": "Energy Inventories (EIA/API). Drops in supply can spike oil prices, driving up inflation and hurting consumer discretionary stocks.", "polarity": "inverse"},
}


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
                pass

    return events_list


def _parse_cb_nlp_message(msg_text: str, timestamp: str) -> dict | None:
    try:
        result: dict = {"timestamp": _utc_str_to_local(timestamp)}
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


@page_router_macro.get("/market-sentiment", response_class=HTMLResponse)
async def market_sentiment_page(request: Request):
    regime_data = get_latest_regime()
    if not regime_data:
        regime_data = {
            "us_regime_label": "Unknown",
            "us_turbulence": 0.0,
            "uk_regime_label": "Unknown",
            "uk_turbulence": 0.0,
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

        auction_rows = get_auction_summary()

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
                df_uk_cpi = get_uk_cpi_yoy_series().rename('value').to_frame()
            else:
                df_m2 = df_us_hy = df_m4 = df_uk_ig = df_yield_curve = pd.DataFrame()
                df_us_cpi = df_uk_cpi = pd.DataFrame()
        except Exception as e:
            logger.error("Error processing macro indicators matrix: %s", e)
            df_m2 = df_us_hy = df_m4 = df_uk_ig = df_yield_curve = pd.DataFrame()
            df_us_cpi = df_uk_cpi = pd.DataFrame()

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
        auction_rows = []
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="market_sentiment.html",
        context={
            "sentiment_html":      get_sentiment_html(),
            "vix_spy_html":        get_vix_spy_html(),
            "yield_equity_html":   get_yield_equity_html(),
            "uk_yield_equity_html": get_uk_yield_equity_html(),
            "ftse_gbp_html":       get_ftse_gbp_html(),
            "us_liquidity_html":   us_liquidity_html,
            "us_credit_html":      us_credit_html,
            "uk_liquidity_html":   uk_liquidity_html,
            "uk_credit_html":      uk_credit_html,
            "yield_curve_html":    yield_curve_html,
            "us_inflation_html":   us_inflation_html,
            "uk_inflation_html":   uk_inflation_html,
            "regime_data":         regime_data,
            "macro_regime":        macro_regime,
            "urgent_events":       urgent_events,
            "us_events":           us_events,
            "uk_events":           uk_events,
            "cb_nlp_latest":       cb_nlp_latest,
            "ai_contagion_status": ai_contagion_status,
            "auction_rows":        auction_rows,
            "user_tz_label":       time_engine.now_local().strftime("%Z"),
            "unread_count":        get_unread_count(),
            "config":              load_config(),
        }
    )


@page_router_macro.get("/index/{ticker}", response_class=HTMLResponse)
async def index_detail(request: Request, ticker: str):
    ticker = normalize_ticker(ticker)
    registry_row = get_ticker_registry_row(ticker)
    if registry_row is None:
        # Not a canonical (spot) registry ticker — check whether it's a paired future instead,
        # so a direct hit on e.g. /index/ES=F lands on the one detail page for that index
        # (spot/future is a single tile per AGENTS.md's Markets page rule) rather than 404ing.
        future_row = get_ticker_registry_row_by_future(ticker)
        if future_row is not None:
            return RedirectResponse(f"/index/{future_row['ticker']}", status_code=302)

    showing_future = False
    future_display_name = None
    if registry_row and registry_row.get("future_ticker"):
        _, _, showing_future = markets_engine.resolve_tile(registry_row)
        if showing_future:
            future_display_name = registry_row.get("future_display_name") or registry_row["display_name"]

    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT ticker, name, price, change_pts, change_pct, is_positive FROM market_pulse_cache WHERE ticker = ?",
            (ticker,)
        )
        pulse_row = cursor.fetchone()
        pulse = dict(pulse_row) if pulse_row else {
            "price": None, "change_pts": None, "change_pct": None,
            "is_positive": None, "name": registry_row["display_name"] if registry_row else ticker,
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
    finally:
        conn.close()

    price_action = None
    # Prefer fresh per-ticker parquet (written by /api/index/refresh); fall back to shared baseline
    _ticker_parquet = HISTORICAL_DIR / f"{ticker}.parquet"
    _baseline_name = registry_row.get("baseline_parquet") if registry_row else None
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
        macro_html = "<div class='chart-ph chart-ph--lg'><span class='chart-ph__icon'>📭</span><span class='chart-ph__title'>No historical data yet</span></div>"
    except Exception as e:
        macro_html = f"<div class='chart-ph chart-ph--lg chart-ph--gap-sm'><span class='chart-ph__icon'>⚠️</span><span class='chart-ph__title'>Chart unavailable</span><span class='chart-ph__hint'>{type(e).__name__}: {e}</span></div>"

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        s1 = price_action['s1'] if price_action else None
        s2 = price_action['s2'] if price_action else None
        mkt_tz = _intraday_market_tz(ticker, currency)
        delay_min = _EXCHANGE_DELAYS.get(currency, 0)
        intraday_html = create_intraday_chart(df_intraday, ticker, s1=s1, s2=s2,
                                              market_tz=mkt_tz, data_delay_minutes=delay_min)
    except FileNotFoundError:
        intraday_html = "<div class='chart-ph chart-ph--sm'><span class='chart-ph__icon'>📭</span><span class='chart-ph__title'>No intraday data yet</span></div>"
    except Exception:
        intraday_html = "<div class='chart-ph chart-ph--sm chart-ph--gap-sm'><span class='chart-ph__icon'>⚠️</span><span class='chart-ph__title'>Intraday data unavailable</span></div>"

    return templates.TemplateResponse(
        request=request, name="index_detail.html",
        context={
            "ticker":        ticker,
            "display_name":  registry_row["display_name"] if registry_row else ticker,
            "asset_type":    registry_row["asset_type"] if registry_row else None,
            "showing_future": showing_future,
            "future_ticker": registry_row.get("future_ticker") if registry_row else None,
            "future_display_name": future_display_name,
            "pulse":         pulse,
            "quant":         quant,
            "currency":      currency,
            "context_blurb": (registry_row.get("context_blurb") if registry_row else None) or "",
            "macro_html":    macro_html,
            "intraday_html": intraday_html,
            "price_action":  price_action,
            "unread_count":  get_unread_count(),
            "config":        load_config(),
            "css_version":   CSS_VERSION,
        }
    )
