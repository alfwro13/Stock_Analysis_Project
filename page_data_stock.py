import json
import logging
from datetime import datetime, timedelta, timezone

from database import get_connection
from utils import time_request_call
from page_helpers import compute_badge_tags, get_pattern_tags_by_ticker

logger = logging.getLogger(__name__)


def _quant_only_stock(ticker, q_data):
    company_name = q_data.get("company_name") or ticker
    company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()

    c_price = q_data.get("close_price")
    c_price = float(c_price) if c_price is not None else 0.0

    return {
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

def _unknown_stock(ticker):
    return {
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


def load_stock_row(ticker):
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
            # Mutual funds often have no shortName from yfinance; the query already fell back through asset_profiles → market_universe
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
            stock_data = _quant_only_stock(ticker, dict(q_data)) if q_data else _unknown_stock(ticker)

        cursor.execute('''
            SELECT drift_avg_pct_1d, drift_up_count_1d, drift_sample_size_1d,
                   drift_avg_pct_5d, drift_up_count_5d, drift_sample_size_5d,
                   drift_avg_pct_20d, drift_up_count_20d, drift_sample_size_20d
            FROM earnings_volatility WHERE ticker = ?
        ''', (ticker,))
        ev_row = cursor.fetchone()
        earnings_vol = dict(ev_row) if ev_row else {}
    finally:
        if conn:
            conn.close()
    return stock_data, earnings_vol


def data_freshness(stock_data):
    last_updated = stock_data.get('last_updated')
    if not last_updated:
        return 'red', "Never"
    try:
        lu_date = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return 'red', last_updated
    return ('green' if datetime.now(timezone.utc) - lu_date < timedelta(hours=24) else 'yellow'), last_updated


def parse_json_field(stock_data, field, ticker):
    if not stock_data.get(field):
        return []
    try:
        return json.loads(stock_data[field])
    except Exception:
        logger.warning("Failed to parse %s JSON for %s", field, ticker, exc_info=True)
        return []
