import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import time_engine
from config import FUNDAMENTALS_DIR
from database import get_connection
from position_sizing import get_position_sizing_config
from portfolio_service import get_rate_to_base
from utils import safe_ticker_filename
from fundamentals_helpers import (
    compute_quality_grade,
    is_quality_compounder,
    is_quality_on_sale,
    is_garp_tenbagger,
    is_mean_reversion_setup,
    is_dividend_harvest_candidate,
)
from bull_bear_trap_engine import phase_label as _trap_phase_label
from bubble_radar_engine import flag_label as _bubble_flag_label
from pattern_detection_engine import DETECTORS

logger = logging.getLogger(__name__)


def get_pattern_tags_by_ticker(tickers: list) -> dict:
    """Every currently-active Pattern Detection result for the given tickers, grouped by
    ticker and resolved to a display label + bullish/bearish direction via each family's
    own registry entry (DETECTORS[family].phase_label()/.PATTERN_TYPES) — shared by
    Portfolio, Watchlist, and Stock Detail so a new pattern family shows up as a tag on all
    three with no per-page changes. Callers fetch this once per page (not once per ticker)
    and attach each ticker's own list into row_dict['pattern_detections'] before calling
    compute_badge_tags()."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, pattern_family, pattern_type, phase FROM pattern_detection_results WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    except Exception as e:
        logger.error("get_pattern_tags_by_ticker failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()

    by_ticker: dict = {}
    for row in rows:
        row = dict(row)
        module = DETECTORS.get(row['pattern_family'])
        if module is None:
            continue
        by_ticker.setdefault(row['ticker'], []).append({
            'label': module.phase_label(row['pattern_type'], row['phase']),
            'phase': row['phase'],
            'direction': module.PATTERN_TYPES.get(row['pattern_type']),
        })
    return by_ticker


def compute_badge_tags(row_dict: dict) -> dict:
    """Shared setup/report/trap/bubble/pattern-detection/quality-grade badge computation for
    a single ticker row — used by Portfolio, Watchlist, and Stock Detail so the three pages
    never drift apart.

    row_dict must carry: setup_tags (raw JSON string from stock_signals), quant_close_price,
    market_cap, trap_phase, bubble_flag, pattern_detections (see get_pattern_tags_by_ticker),
    plus the fundamentals columns compute_quality_grade()/is_*() read directly off stock_signals.
    """
    if row_dict.get('setup_tags'):
        try:
            setup_tags_list = json.loads(row_dict['setup_tags'])
        except Exception:
            setup_tags_list = []
    else:
        setup_tags_list = []

    screen_row = dict(row_dict)
    screen_row['close_price'] = row_dict.get('quant_close_price')
    report_tags = []
    if is_quality_compounder(screen_row):
        report_tags.append({'name': 'Quality Compounder', 'tooltip': 'Meets the Market Reports Quality Compounders screen: ROE>15%, low debt, steady growth, reasonable PE.'})
    if is_quality_on_sale(screen_row):
        report_tags.append({'name': 'Quality on Sale', 'tooltip': 'Meets the Market Reports Quality on Sale screen: near its 52-week low despite solid fundamentals.'})
    if is_garp_tenbagger(screen_row, row_dict.get('market_cap')):
        report_tags.append({'name': 'GARP Tenbagger', 'tooltip': 'Meets the Market Reports GARP Tenbaggers screen: low PEG with strong growth (Peter Lynch style).'})
    if is_mean_reversion_setup(screen_row):
        report_tags.append({'name': 'Mean Reversion Setup', 'tooltip': 'Meets the Market Reports Mean Reversion screen: oversold RSI within a longer-term uptrend.'})
    if is_dividend_harvest_candidate(screen_row):
        report_tags.append({'name': 'Dividend Harvest', 'tooltip': 'Meets the Market Reports Dividend Harvest screen: solid yield with a healthy composite score.'})

    return {
        'setup_tags_list': setup_tags_list,
        'quality_grade': compute_quality_grade(row_dict),
        'report_tags': report_tags,
        'trap_phase_label': _trap_phase_label(row_dict.get('trap_phase')) if row_dict.get('trap_phase') and row_dict['trap_phase'] != 'NEUTRAL' else None,
        'bubble_flag_label': _bubble_flag_label(row_dict.get('bubble_flag')),
        'pattern_tags': row_dict.get('pattern_detections') or [],
    }


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


def _fmt_currency(value) -> Optional[str]:
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


def _fmt_volume(value) -> Optional[str]:
    if value is None:
        return None
    if value >= 1e9:
        return f"{value/1e9:.1f}B"
    if value >= 1e6:
        return f"{value/1e6:.1f}M"
    if value >= 1e3:
        return f"{value/1e3:.0f}K"
    return str(int(value))


def _fmt_price(value, currency: Optional[str], decimals: int = 2, with_symbol: bool = True) -> Optional[str]:
    """GBp-aware per-share price formatting, matching the inline logic already used
    throughout portfolio.html/watchlist.html (halve GBp values, show a currency symbol)."""
    if value is None:
        return None
    val = (value / 100.0) if currency == 'GBp' else value
    if not with_symbol:
        return f"{val:,.{decimals}f}"
    sym = '£' if currency in ('GBP', 'GBp') else ('€' if currency == 'EUR' else '$')
    suffix = f" {currency}" if currency not in ('USD', 'GBP', 'GBp', 'EUR') else ""
    return f"{sym}{val:,.{decimals}f}{suffix}"


def _load_fundamentals_extra(ticker: str) -> dict:
    empty: dict = {
        "market_cap_fmt": None, "trailing_eps": None, "forward_eps": None,
        "earnings_growth": None, "free_cash_flow_fmt": None, "total_debt_fmt": None,
        "total_cash_fmt": None, "net_cash_fmt": None, "roa": None, "quick_ratio": None,
        "insider_ownership": None, "payout_ratio": None, "ex_dividend_date_fmt": None,
        "average_volume_fmt": None, "full_time_employees_fmt": None, "website": None,
    }
    safe_ticker = safe_ticker_filename(ticker)
    path = FUNDAMENTALS_DIR / f"{safe_ticker}.json" if safe_ticker else None
    if path is None or not path.exists():
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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return time_engine.fmt_datetime(dt)
        except ValueError:
            continue
    return s


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
        "config":       get_position_sizing_config(config_data),
        "fx_rates":     fx_rates,
        "base_currency": base_currency,
    }


def calculate_pnl(
    shares: float,
    buy_price_base: float,
    exchange_rate: float,
    current_price: float,
    price_in_pence: bool = False,
) -> Optional[dict]:
    if shares <= 0:
        return None
    bp_adj = buy_price_base * exchange_rate
    if price_in_pence:
        bp_adj *= 100
    current_value = shares * current_price
    cost_basis = shares * bp_adj
    pnl = current_value - cost_basis
    pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
    return {
        "shares":        round(shares, 4),
        "buy_price":     round(bp_adj, 4),
        "current_value": round(current_value, 2),
        "pnl":           round(pnl, 2),
        "pnl_pct":       round(pnl_pct, 2),
    }


def get_portfolio_heat_row(scope: str = "all") -> Optional[dict]:
    """Latest Portfolio Heat Index row for `scope`, with its breakdown parsed from JSON."""
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM portfolio_heat_index WHERE scope = ?", (scope,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["breakdown"] = json.loads(result["breakdown_json"]) if result.get("breakdown_json") else []
        return result
    finally:
        if conn:
            conn.close()


def get_all_scope_heat_tier() -> Optional[str]:
    """The "all"-scope Portfolio Heat Index tier — drives the visual-only BUY-signal warning badge."""
    row = get_portfolio_heat_row("all")
    return row["tier"] if row else None
