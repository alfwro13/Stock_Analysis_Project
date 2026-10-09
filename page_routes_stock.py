import json
import logging

import pandas as pd

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config, HISTORICAL_DIR, INTRADAY_DIR
import time_engine
from database import get_connection, get_watchlist_tickers
from market_pulse import get_all_cached_pulse, get_index_tickers
from utils import normalize_ticker, measure_request_stage, time_request_call
from visuals import (
    create_macro_chart,
    create_intraday_chart,
    intraday_market_tz,
    EXCHANGE_DELAYS,
    create_anomaly_score_chart,
    create_anomaly_feature_radar,
)
from portfolio_service import get_rate_from_base, get_fx_cache_status
from fx_drag_engine import compute_fx_breakdown
from quant_signals import get_candlestick_patterns
from constants import CSS_VERSION
from page_helpers import (
    intraday_chart_revision,
    _load_fundamentals_extra,
    _build_position_sizing_context,
    get_unread_count,
    calculate_pnl,
    compute_badge_tags,
    get_pattern_tags_by_ticker,
    get_all_scope_heat_tier,
)

logger = logging.getLogger(__name__)

page_router_stock = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


@page_router_stock.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_detail(request: Request, ticker: str, embed: bool = False, embed_token: str = ""):
    ticker = normalize_ticker(ticker)
    registry_ticker = next((t for t in get_index_tickers() if t == ticker), None)
    if registry_ticker:
        return RedirectResponse(f"/index/{registry_ticker}", status_code=302)
    is_in_watchlist = ticker in get_watchlist_tickers()

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        time_request_call("sql", cursor.execute, '''
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
                   q.anomaly_score, q.close_price as quant_close_price,
                   q.vp_poc, q.vp_val, q.vp_vah, q.vp_entry_zone, q.vp_exit_zone,
                   q.kc_z_score, q.kc_entry_signal, q.kc_exit_signal,
                   q.price_q10, q.price_q90,
                   mu.industry, mu.index_membership,
                   cno.display_name as name_override,
                   tmeta.market_cap,
                   trap.phase as trap_phase,
                   (SELECT flag FROM bubble_radar_metrics
                    WHERE ticker = s.ticker ORDER BY scan_date DESC LIMIT 1) AS bubble_flag,
                   COALESCE(
                       cno.display_name,
                       NULLIF(p.company_name, s.ticker),
                       NULLIF(mu.company_name, s.ticker),
                       s.company_name,
                       s.ticker
                   ) as resolved_company_name
            FROM stock_signals s
            LEFT JOIN asset_profiles p ON s.ticker = p.ticker
            LEFT JOIN market_universe mu ON s.ticker = mu.ticker
            LEFT JOIN company_name_overrides cno ON s.ticker = cno.ticker
            LEFT JOIN quant_signals q ON s.ticker = q.ticker
                AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            LEFT JOIN ticker_metadata tmeta ON s.ticker = tmeta.ticker
            LEFT JOIN trap_monitor_results trap ON s.ticker = trap.ticker
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
            stock_data['has_name_override'] = bool(stock_data.get('name_override'))
            stock_data['pattern_detections'] = get_pattern_tags_by_ticker([ticker]).get(ticker, [])
            stock_data.update(compute_badge_tags(stock_data))
        else:
            cursor.execute('''
                SELECT q.*,
                       cno.display_name as name_override,
                       COALESCE(cno.display_name, p.company_name, m.company_name, q.ticker) as company_name,
                       COALESCE(p.sector, 'Unclassified') as sector,
                       COALESCE(p.currency, 'USD') as currency,
                       COALESCE(p.quote_type, 'EQUITY') as quote_type,
                       p.business_summary,
                       m.industry, m.index_membership
                FROM quant_signals q
                LEFT JOIN market_universe m ON q.ticker = m.ticker
                LEFT JOIN asset_profiles p ON q.ticker = p.ticker
                LEFT JOIN company_name_overrides cno ON q.ticker = cno.ticker
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
                    "country": None,
                    "fifty_two_week_low": None,
                    "fifty_two_week_high": None,
                    "ma_50_day": q_data.get("sma_50"),
                    "ma_200_day": q_data.get("sma_200"),
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
                    "has_name_override": bool(q_data.get("name_override")),
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
                    "has_name_override": False,
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

        earnings_vol: dict = {}
        if stock_data:
            cursor.execute('''
                SELECT drift_avg_pct_1d, drift_up_count_1d, drift_sample_size_1d,
                       drift_avg_pct_5d, drift_up_count_5d, drift_sample_size_5d,
                       drift_avg_pct_20d, drift_up_count_20d, drift_sample_size_20d
                FROM earnings_volatility WHERE ticker = ?
            ''', (ticker,))
            ev_row = cursor.fetchone()
            if ev_row:
                earnings_vol = dict(ev_row)

    finally:
        if conn:
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

    from db_helpers import get_latest_resolved_earnings_drift
    latest_resolved_drift = get_latest_resolved_earnings_drift(ticker) if stock_data else {1: None, 5: None, 20: None}

    from db_etf import get_etf_predictor_config_by_ticker
    etf_predictor_config = get_etf_predictor_config_by_ticker(ticker)

    from accounts_engine import get_combined_holdings, current_price_map
    user_asset = get_combined_holdings().get(ticker)

    portfolio_math = None
    target_accounts = []
    holding_price_limits = {}
    if user_asset and stock_data and stock_data.get('current_price'):
        priced = current_price_map([ticker]).get(ticker)
        live_current_price = priced[0] if priced and priced[0] else stock_data['current_price']
        with measure_request_stage("fx_rate"):
            exchange_rate = get_rate_from_base(stock_data['currency'], cache_only=True)
        price_in_pence = stock_data['currency'] == 'GBp'

        global_math = calculate_pnl(
            user_asset.get('global_shares', 0),
            user_asset.get('global_buy_price', 0),
            exchange_rate,
            live_current_price,
            price_in_pence,
        )
        account_maths = []
        for acc in user_asset.get('accounts', []):
            acc_m = calculate_pnl(
                acc.get('shares', 0),
                acc.get('buy_price', 0),
                exchange_rate,
                live_current_price,
                price_in_pence,
            )
            if acc_m:
                acc_m["name"] = acc.get("name", "Unknown Account")
                account_maths.append(acc_m)

            acc_raw_id = acc.get("id", "")
            if isinstance(acc_raw_id, str) and acc_raw_id.startswith("acct:"):
                target_accounts.append({
                    "account_id": int(acc_raw_id[len("acct:"):]),
                    "name": acc.get("name", "Unknown Account"),
                })

        if global_math:
            portfolio_math = {"global": global_math, "accounts": account_maths}

    if is_in_watchlist:
        from db_accounts import get_watchlist_account
        watchlist_account = get_watchlist_account()
        if watchlist_account:
            target_accounts.append({
                "account_id": watchlist_account["id"],
                "name": "Watchlist",
            })

    if target_accounts:
        from db_accounts import get_holding_price_limits_for_ticker
        holding_price_limits = get_holding_price_limits_for_ticker(ticker)

    from db_helpers import get_ticker_notes
    ticker_notes = get_ticker_notes(ticker)
    for note in ticker_notes:
        note["created_display"] = time_engine.fmt_datetime(
            datetime.strptime(note["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        )
        note["updated_display"] = None
        if note.get("updated_at"):
            note["updated_display"] = time_engine.fmt_datetime(
                datetime.strptime(note["updated_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            )

    fx_breakdown = None
    if portfolio_math and stock_data and stock_data.get("currency") == "USD":
        now = datetime.now(timezone.utc)
        ytd_days = (now.date() - now.date().replace(month=1, day=1)).days or 1
        fx_breakdown = compute_fx_breakdown(ticker, ytd_days)

    currency = stock_data.get("currency", "USD") if stock_data else "USD"
    intraday_revision = intraday_chart_revision(ticker, currency)
    price_action = None
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")

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

        with measure_request_stage("chart"):
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
        macro_html = "<div class='chart-ph chart-ph--lg'><span class='chart-ph__icon'>📭</span><span class='chart-ph__title'>No historical data yet</span><span class='chart-ph__hint'>Press <strong>Refresh</strong> above to fetch price history for this asset.</span></div>"
    except Exception as e:
        df_macro = pd.DataFrame()
        macro_html = f"<div class='chart-ph chart-ph--lg chart-ph--gap-sm'><span class='chart-ph__icon'>⚠️</span><span class='chart-ph__title'>Chart unavailable</span><span class='chart-ph__hint'>{type(e).__name__}: {e}</span></div>"

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
        mkt_tz = intraday_market_tz(ticker, currency)
        delay_min = EXCHANGE_DELAYS.get(currency, 0)
        with measure_request_stage("chart"):
            intraday_html = create_intraday_chart(
                df_intraday, ticker, s1=s1_val, s2=s2_val,
                live_pattern_name=live_pattern_name,
                live_pattern_tooltip=live_pattern_tooltip,
                live_pattern_score=live_pattern_score,
                market_tz=mkt_tz,
                data_delay_minutes=delay_min,
            )
    except FileNotFoundError:
        intraday_revision = ""
        intraday_html = "<div class='chart-ph chart-ph--sm'><span class='chart-ph__icon'>📭</span><span class='chart-ph__title'>No intraday data yet</span><span class='chart-ph__hint'>Press <strong>Refresh</strong> above to fetch today's intraday data.</span></div>"
    except Exception:
        intraday_revision = ""
        intraday_html = "<div class='chart-ph chart-ph--sm chart-ph--gap-sm'><span class='chart-ph__icon'>⚠️</span><span class='chart-ph__title'>Intraday data unavailable</span></div>"

    config_data = load_config()
    fake_rows = [{"currency": stock_data.get("currency", "USD")}]
    position_sizing_context = _build_position_sizing_context(config_data, fake_rows)
    if user_asset and stock_data:
        position_sizing_context["fx_status"].extend(get_fx_cache_status([stock_data["currency"]], from_base=True))
    anomaly_chart_html = (
        "<div class='chart-ph chart-ph--lg'>"
        "<span class='chart-ph__icon'>📊</span>"
        "<span class='chart-ph__title'>No anomaly data yet</span>"
        "<span class='chart-ph__hint'>Scores are written during market hours once models are trained.</span>"
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
            with measure_request_stage("chart"):
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

    bubble_data = None
    try:
        from bubble_radar_engine import get_bubble_ticker_detail
        bubble_data = get_bubble_ticker_detail(ticker)
    except Exception:
        pass

    ticker_risk = None
    conn_risk = None
    try:
        conn_risk = get_connection()
        row = conn_risk.execute(
            "SELECT * FROM ticker_risk_contribution WHERE ticker = ?", (ticker,)
        ).fetchone()
        ticker_risk = dict(row) if row else None
    finally:
        if conn_risk:
            conn_risk.close()

    from score_analysis import compute_regime_weighted_score, evaluate_buy_recommendation_batch, evaluate_pillar_confluence
    pillar_confluence = evaluate_pillar_confluence(ticker)
    regime_weighted = compute_regime_weighted_score(ticker)
    buy_recommendation = evaluate_buy_recommendation_batch(
        [ticker], confluence_by_ticker={ticker: pillar_confluence},
        regime_score_by_ticker={ticker: regime_weighted},
    ).get(ticker)

    return time_request_call("template", templates.TemplateResponse,
        request=request, name="stock_detail.html",
        context={
            "stock": stock_data,
            "top_holdings": top_holdings,
            "sector_weightings": sector_weightings,
            "macro_html": macro_html,
            "intraday_html": intraday_html,
            "intraday_revision": intraday_revision,
            "anomaly_chart_html": anomaly_chart_html,
            "anomaly_radar_html": anomaly_radar_html,
            "anomaly_percentile": anomaly_percentile,
            "portfolio_math": portfolio_math,
            "target_accounts": target_accounts,
            "holding_price_limits": holding_price_limits,
            "ticker_notes": ticker_notes,
            "fx_breakdown": fx_breakdown,
            "days_to_earnings": days_to_earnings,
            "volatility_date": volatility_date,
            "price_action": price_action,
            "unread_count": get_unread_count(),
            "embed": embed,
            "embed_token": embed_token,
            "config": load_config(),
            "cached_pulse": get_all_cached_pulse(),
            "is_in_watchlist": is_in_watchlist,
            "is_dip_monitored": is_dip_monitored,
            "data_status": data_status,
            "last_updated_str": last_updated_str,
            "position_sizing": position_sizing_context,
            "earnings_vol": earnings_vol,
            "latest_resolved_drift": latest_resolved_drift,
            "fundamentals_extra": fundamentals_extra,
            "bubble_data": bubble_data,
            "ticker_risk": ticker_risk,
            "pillar_confluence": pillar_confluence,
            "regime_weighted_score": regime_weighted,
            "buy_recommendation": buy_recommendation,
            "risk_paused": get_all_scope_heat_tier() == "RED",
            "etf_predictor_config": etf_predictor_config,
        }
    )
