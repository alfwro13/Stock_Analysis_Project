"""Per-ticker calendar-period price returns (5D/1M/6M/YTD/1Y), derived from the parquet history every ticker already has cached; no DB, no Yahoo Finance calls of its own."""
import calendar
import logging
from datetime import date, datetime, timezone
from typing import Dict, Optional

from data_engine import load_or_fetch_daily_history

logger = logging.getLogger(__name__)

PERIOD_KEYS = ("5d", "1m", "6m", "ytd", "1y")
CHANGE_PERIODS = ("1d",) + PERIOD_KEYS


def _calendar_offset(d: date, months_back: int) -> date:
    """today minus N calendar months, clamping the day to the target month's length (e.g. Mar 31 - 1mo -> Feb 28/29)."""
    total_months = d.month - 1 - months_back
    year = d.year + total_months // 12
    month = total_months % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _anchor_cutoffs(today: date) -> Dict[str, date]:
    return {
        "1m": _calendar_offset(today, 1),
        "6m": _calendar_offset(today, 6),
        "ytd": date(today.year - 1, 12, 31),
        "1y": _calendar_offset(today, 12),
    }


def _anchor_closes_for_ticker(ticker: str, today: date) -> Dict[str, Optional[float]]:
    anchors: Dict[str, Optional[float]] = {key: None for key in PERIOD_KEYS}
    df = load_or_fetch_daily_history(ticker)
    if df is None or df.empty:
        return anchors

    close = df["Close"]
    if len(close) >= 6:
        anchors["5d"] = float(close.iloc[-6])

    cutoffs = _anchor_cutoffs(today)
    for key, cutoff in cutoffs.items():
        matching = close[close.index.date <= cutoff]
        if not matching.empty:
            anchors[key] = float(matching.iloc[-1])

    return anchors


def get_period_anchor_closes(tickers: list) -> Dict[str, Dict[str, Optional[float]]]:
    """Batch-once-then-enrich (mirrors accounts_engine.current_price_map); returns each period's reference CLOSE (not a %) so callers can combine it with the live price at render/recompute time."""
    today = datetime.now(timezone.utc).date()
    result: Dict[str, Dict[str, Optional[float]]] = {}
    for ticker in tickers:
        try:
            result[ticker] = _anchor_closes_for_ticker(ticker, today)
        except Exception as e:
            logger.error("Failed to compute period anchor closes for %s: %s", ticker, e)
            result[ticker] = {key: None for key in PERIOD_KEYS}
    return result


def pct_from_anchor(current_price: Optional[float], anchor_close: Optional[float]) -> Optional[float]:
    if current_price is None or anchor_close is None or anchor_close == 0:
        return None
    return (current_price - anchor_close) / anchor_close * 100
