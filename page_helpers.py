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

logger = logging.getLogger(__name__)


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
