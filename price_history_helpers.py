"""Per-ticker calendar-period price returns (5D/1M/6M/YTD/1Y), trailing N-session window returns and the canonical daily-close loader, derived from the parquet history every ticker already has cached; no DB, no Yahoo Finance calls of its own."""
import calendar
import logging
from collections import OrderedDict
from datetime import date, datetime, timezone
from threading import Lock
from typing import Dict, Optional, Tuple

import pandas as pd

from data_engine import daily_history_cache_revision, load_or_fetch_daily_history
from utils import is_synthetic_ticker

logger = logging.getLogger(__name__)

PERIOD_KEYS = ("5d", "1m", "6m", "ytd", "1y")
CHANGE_PERIODS = ("1d",) + PERIOD_KEYS

_ANCHOR_CACHE_LIMIT = 512
_anchor_cache = OrderedDict()
_anchor_cache_lock = Lock()


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


def _anchor_closes_for_ticker(ticker: str, today: date, *, cache_only: bool = False) -> Dict[str, Optional[float]]:
    anchors: Dict[str, Optional[float]] = {key: None for key in PERIOD_KEYS}
    if is_synthetic_ticker(ticker):
        return anchors
    revision = daily_history_cache_revision(ticker, refresh_stale=cache_only)
    with _anchor_cache_lock:
        for key in list(_anchor_cache):
            if _anchor_cache[key][0] != today:
                del _anchor_cache[key]
        cached = _anchor_cache.pop(ticker, None)
        if revision is not None and cached is not None and cached[:2] == (today, revision):
            _anchor_cache[ticker] = cached
            return dict(cached[2])
    df = load_or_fetch_daily_history(ticker, cache_only=True) if cache_only else load_or_fetch_daily_history(ticker)
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

    if revision is not None and daily_history_cache_revision(ticker) == revision:
        with _anchor_cache_lock:
            _anchor_cache[ticker] = (today, revision, dict(anchors))
            _anchor_cache.move_to_end(ticker)
            while len(_anchor_cache) > _ANCHOR_CACHE_LIMIT:
                _anchor_cache.popitem(last=False)
    return anchors


def get_period_anchor_closes(tickers: list, *, cache_only: bool = False) -> Dict[str, Dict[str, Optional[float]]]:
    """Batch-once-then-enrich (mirrors accounts_engine.current_price_map); returns each period's reference CLOSE (not a %) so callers can combine it with the live price at render/recompute time."""
    today = datetime.now(timezone.utc).date()
    result: Dict[str, Dict[str, Optional[float]]] = {}
    for ticker in tickers:
        try:
            result[ticker] = _anchor_closes_for_ticker(ticker, today, cache_only=cache_only)
        except Exception as e:
            logger.error("Failed to compute period anchor closes for %s: %s", ticker, e)
            result[ticker] = {key: None for key in PERIOD_KEYS}
    return result


def pct_from_anchor(current_price: Optional[float], anchor_close: Optional[float]) -> Optional[float]:
    if current_price is None or anchor_close is None or anchor_close == 0:
        return None
    return (current_price - anchor_close) / anchor_close * 100


def session_window_returns(
    closes: pd.DataFrame, sessions: int, *, calendar_quorum: float = 0.5, min_coverage: float = 0.9,
) -> Optional[Tuple[pd.Timestamp, pd.Timestamp, pd.Series]]:
    """Cumulative return of each column over the last `sessions` sessions of the group's own calendar, where a calendar date is one on which at least `calendar_quorum` of the columns have a close (so a date only a few members traded never becomes the as-of date). A column is dropped unless it has both window endpoints and at least `min_coverage` of the window's closes. Returns (start, as_of, returns) or None when the calendar is too short."""
    positive = closes.where(closes > 0)
    calendar = positive.index[positive.notna().mean(axis=1) >= calendar_quorum]
    if len(calendar) < sessions + 1:
        return None
    window = positive.reindex(calendar[-(sessions + 1):])
    keep = window.notna().mean() >= min_coverage
    keep &= window.iloc[0].notna() & window.iloc[-1].notna()
    kept = window.loc[:, keep]
    returns = kept.iloc[-1] / kept.iloc[0] - 1
    return window.index[0], window.index[-1], returns


def normalized_close(close: pd.Series, tail: Optional[int] = None) -> Optional[pd.Series]:
    """Daily closes with NaNs dropped, a tz-naive midnight index, last-wins de-duplication and ascending order, then trimmed to the last `tail` rows; None when nothing remains."""
    close = close.dropna()
    if close.empty:
        return None
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    close = close[~close.index.duplicated(keep="last")].sort_index()
    return close.tail(tail) if tail else close


def load_daily_close(ticker: str, *, cache_only: bool = False, tail: Optional[int] = None) -> Optional[pd.Series]:
    """The one daily-close loader over data/historical (a missing parquet is fetched unless cache_only)."""
    df = load_or_fetch_daily_history(ticker, cache_only=cache_only)
    if df is None or "Close" not in df.columns:
        return None
    return normalized_close(df["Close"], tail)
