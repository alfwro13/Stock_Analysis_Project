import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

import time_engine
from config import HISTORICAL_DIR, INTRADAY_DIR
from database import get_connection
from utils import measure_request_stage
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
from page_helpers import intraday_chart_revision, _build_position_sizing_context, calculate_pnl

logger = logging.getLogger(__name__)


def earnings_timing(stock_data, ticker):
    if not stock_data.get('next_earnings_date') or stock_data['next_earnings_date'] == 'Unknown':
        return None, None
    try:
        e_date = datetime.strptime(stock_data['next_earnings_date'], '%Y-%m-%d').date()
        today = time_engine.now_local().date()
        return (e_date - today).days, (e_date - timedelta(days=7)).strftime('%Y-%m-%d')
    except Exception:
        logger.warning("Could not parse next_earnings_date for %s: %s", ticker, stock_data.get('next_earnings_date'))
        return None, None


def position_targets(ticker, stock_data, user_asset, is_in_watchlist):
    from accounts_engine import current_price_map

    portfolio_math = None
    target_accounts = []
    holding_price_limits = {}
    if user_asset and stock_data.get('current_price'):
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

    return portfolio_math, target_accounts, holding_price_limits


def ticker_notes_for_display(ticker):
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
    return ticker_notes


def fx_breakdown_for(ticker, stock_data, portfolio_math):
    if not portfolio_math or stock_data.get("currency") != "USD":
        return None
    now = datetime.now(timezone.utc)
    ytd_days = (now.date() - now.date().replace(month=1, day=1)).days or 1
    return compute_fx_breakdown(ticker, ytd_days)


def _macro_panel(ticker, currency):
    price_action = None
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")

        baseline_file = "FTSE_BASELINE.parquet" if ticker.endswith('.L') or currency in ['GBp', 'GBP'] else "SP500_BASELINE.parquet"
        try:
            df_baseline = pd.read_parquet(HISTORICAL_DIR / baseline_file)
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
    return df_macro, macro_html, price_action


def _intraday_panel(ticker, currency, df_macro, price_action):
    intraday_revision = intraday_chart_revision(ticker, currency)
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
    return intraday_html, intraday_revision


def price_chart_panels(ticker, currency):
    df_macro, macro_html, price_action = _macro_panel(ticker, currency)
    intraday_html, intraday_revision = _intraday_panel(ticker, currency, df_macro, price_action)
    return {
        "macro_html": macro_html,
        "price_action": price_action,
        "intraday_html": intraday_html,
        "intraday_revision": intraday_revision,
    }


def position_sizing_for(config_data, stock_data, user_asset):
    position_sizing_context = _build_position_sizing_context(config_data, [{"currency": stock_data.get("currency", "USD")}])
    if user_asset:
        position_sizing_context["fx_status"].extend(get_fx_cache_status([stock_data["currency"]], from_base=True))
    return position_sizing_context


def anomaly_panel(ticker, stock_data, config_data):
    chart_html = (
        "<div class='chart-ph chart-ph--lg'>"
        "<span class='chart-ph__icon'>📊</span>"
        "<span class='chart-ph__title'>No anomaly data yet</span>"
        "<span class='chart-ph__hint'>Scores are written during market hours once models are trained.</span>"
        "</div>"
    )
    percentile = None
    radar_html = None
    conn = None
    try:
        conn = get_connection()
        anomaly_rows = conn.execute(
            "SELECT date, anomaly_score, close_price FROM quant_signals "
            "WHERE ticker = ? AND anomaly_score IS NOT NULL "
            "ORDER BY date DESC LIMIT 90",
            (ticker,),
        ).fetchall()
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
                chart_html = create_anomaly_score_chart(df_anomaly, ticker, threshold=anomaly_threshold)

            latest_score = df_anomaly["anomaly_score"].iloc[-1]
            history = df_anomaly["anomaly_score"]
            percentile = round(float((history <= latest_score).mean() * 100), 1)

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
            radar_html = create_anomaly_feature_radar(radar_features, ticker)
    except Exception:
        pass  # fallback placeholder already set
    finally:
        if conn:
            conn.close()
    return chart_html, percentile, radar_html


def is_dip_monitored(ticker):
    conn = None
    try:
        conn = get_connection()
        today = datetime.now(timezone.utc).date().isoformat()
        dip_row = conn.execute(
            "SELECT 1 FROM intraday_monitors WHERE ticker = ? AND is_active = 1 AND expire_date >= ?",
            (ticker, today),
        ).fetchone()
        return bool(dip_row)
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


def bubble_detail(ticker):
    try:
        from bubble_radar_engine import get_bubble_ticker_detail
        return get_bubble_ticker_detail(ticker)
    except Exception:
        return None


def ticker_risk_row(ticker):
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM ticker_risk_contribution WHERE ticker = ?", (ticker,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def score_panels(ticker):
    from score_analysis import compute_regime_weighted_score, evaluate_buy_recommendation_batch, evaluate_pillar_confluence

    pillar_confluence = evaluate_pillar_confluence(ticker)
    regime_weighted = compute_regime_weighted_score(ticker)
    buy_recommendation = evaluate_buy_recommendation_batch(
        [ticker], confluence_by_ticker={ticker: pillar_confluence},
        regime_score_by_ticker={ticker: regime_weighted},
    ).get(ticker)
    return pillar_confluence, regime_weighted, buy_recommendation
