import logging
import os
import time
from typing import Dict, Optional

import numpy as np
import pandas as pd

from config import HISTORICAL_DIR
from db.database import upsert_instrument_session
from market_session_helpers import cached_registry_exchange_map, has_reported_session, reported_session, resolve_ticker_exchange, stored_sessions
from price_repair_engine import apply_saved_repairs
from utils import is_daily_bar_still_forming, is_excluded_from_yahoo_fetch, safe_ticker_filename, write_parquet_atomic
from yahoo_engine import yahoo_engine
import time_engine

logger = logging.getLogger(__name__)

SESSION_REFRESH_SECONDS = 30 * 86400
SESSION_RETRY_SECONDS = 3600
HISTORY_YEARS = 2
UNIT_FLIP_RATIO_RANGE = (70.0, 150.0)
UNIT_FLIP_PERSISTENT_BARS = 20
LISTING_BREAK_RATIO = 150.0
LISTING_BREAK_MIN_ZERO_VOLUME_BARS = 5
_PRICE_COLUMNS = ("Open", "High", "Low", "Close")

_session_lookup_failed: Dict[str, float] = {}


def history_exchange(ticker: str) -> str:
    """Registry exchange first (^FTSE is LSE, not NYSE), then the ticker suffix; never HOME_EXCHANGE, which would judge AAPL's bar against the operator's home session."""
    return resolve_ticker_exchange(ticker, registry_exchange_map=cached_registry_exchange_map(), suffix_only=True)


def _learn_reported_session(ticker: str) -> None:
    """Runs from prepare_daily_history so every history writer learns a session on the refresh that first needs it; a failed lookup is retried after SESSION_RETRY_SECONDS and the last stored session survives it."""
    if not has_reported_session(ticker) or is_excluded_from_yahoo_fetch(ticker):
        return
    now = time.time()
    stored = stored_sessions().get(ticker)
    if stored and now - stored["updated_at"] < SESSION_REFRESH_SECONDS:
        return
    if now - _session_lookup_failed.get(ticker, 0.0) < SESSION_RETRY_SECONDS:
        return
    shape = yahoo_engine.get_session_shape(ticker)
    try:
        regular_end = time_engine.local_hm_from_epoch(shape["regular_end"], shape["tz"]) if shape else None
    except Exception as e:
        logger.error("Unusable session for %s: %s", ticker, e)
        regular_end = None
    if regular_end is None:
        _session_lookup_failed[ticker] = now
        return
    if upsert_instrument_session(ticker, shape["tz"], regular_end):
        stored_sessions()[ticker] = {"tz": shape["tz"], "regular_end": regular_end, "updated_at": now}


def _drop_in_progress_last_bar(df_daily: pd.DataFrame, df_live: Optional[pd.DataFrame], ticker: Optional[str] = None) -> pd.DataFrame:
    """Yahoo's daily endpoint often includes today's still-forming bar when queried mid-session; trim it so the stored daily history never stores a partial-session close as if it were final (same comparison market_pulse_write.fetch_and_save_pulse already makes against its own live feed). Unlike the intraday scanners' own use of is_daily_bar_still_forming(), this runs at arbitrary times of day (nightly Update Pipeline, on-demand single-ticker fetch) rather than only while an exchange is confirmed open, so ticker must be passed to resolve whether its exchange has already closed for the day — otherwise a same-day post-close fetch is indistinguishable from a genuine mid-session one."""
    if df_live is None or df_live.empty or len(df_daily) < 2:
        return df_daily
    exchange_open = time_engine.is_market_open(history_exchange(ticker)) if ticker else None
    if is_daily_bar_still_forming(df_daily.index[-1].date(), df_live.index[-1].date(), exchange_open):
        return df_daily.iloc[:-1]
    return df_daily


def fix_unit_flips(ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    """Yahoo intermittently quotes LSE bars in pounds inside a pence history (or the reverse): a Close step of 70-150x or its reciprocal is a unit flip, and every bar is rescaled to one unit: the latest run's when it lasts UNIT_FLIP_PERSISTENT_BARS bars, else the unit most bars use when the series has stepped both ways; a series that only ever stepped one way and ends in a short run may be a real change and is left alone."""
    if not ticker.endswith(".L") or len(df) < 3:
        return df
    close = df["Close"].to_numpy(float)
    previous, current = close[:-1], close[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = current / previous
    usable = (previous > 0) & (current > 0)
    low, high = UNIT_FLIP_RATIO_RANGE
    up = usable & (ratio >= low) & (ratio <= high)
    down = usable & (ratio <= 1 / low) & (ratio >= 1 / high)
    steps = np.concatenate(([0], up.astype(int) - down.astype(int)))
    if not steps.any():
        return df
    level = np.cumsum(steps)
    if len(level) - np.flatnonzero(steps)[-1] >= UNIT_FLIP_PERSISTENT_BARS:
        anchor = level[-1]
    elif up.any() and down.any():
        levels, counts = np.unique(level, return_counts=True)
        tied = levels[counts == counts.max()]
        anchor = level[-1] if level[-1] in tied else tied[-1]
    else:
        return df
    shift = level - anchor
    if not shift.any():
        return df
    columns = [c for c in _PRICE_COLUMNS if c in df.columns]
    fixed = df.copy()
    fixed[columns] = df[columns].to_numpy(float) / (100.0 ** shift)[:, None]
    return fixed


def trim_listing_break(df: pd.DataFrame) -> pd.DataFrame:
    """A symbol reissued to another company keeps the earlier company's bars, then a run of zero-volume prints, then a jump of more than LISTING_BREAK_RATIO to the first traded bar; the bars before that jump cannot be compared with the new listing's, so they are dropped."""
    if len(df) < 2 or "Volume" not in df.columns:
        return df
    close = df["Close"].to_numpy(float)
    volume = df["Volume"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = close[1:] / close[:-1]
    usable = (close[:-1] > 0) & (close[1:] > 0) & (volume[1:] > 0)
    jumps = np.flatnonzero(usable & ((ratio > LISTING_BREAK_RATIO) | (ratio < 1 / LISTING_BREAK_RATIO))) + 1
    for i in jumps[::-1]:
        zero_bars = 0
        while zero_bars < i and volume[i - 1 - zero_bars] == 0:
            zero_bars += 1
        if zero_bars >= LISTING_BREAK_MIN_ZERO_VOLUME_BARS:
            return df.iloc[i:]
    return df


def prepare_daily_history(ticker: str, df: pd.DataFrame, df_live: Optional[pd.DataFrame], *, drop_missing_volume: bool = False) -> pd.DataFrame:
    """Single cleaning path for every daily-history writer so saved repairs survive any refresh; df_live=None means no live feed was fetched, so the exchange state and the bar's own date decide whether the last bar is still forming; a ticker with a Yahoo-reported session is judged by that window alone."""
    df = df.dropna(subset=["Close", "Volume"] if drop_missing_volume else ["Close"])
    for col in ("Open", "High", "Low"):
        if col in df.columns:
            mask = (df[col] == 0) & (df["Close"] > 0)
            df.loc[mask, col] = df.loc[mask, "Close"]
    df = trim_listing_break(fix_unit_flips(ticker, df))
    _learn_reported_session(ticker)
    session = reported_session(ticker)
    if session is not None:
        if not df.empty and time_engine.reported_bar_still_forming(df.index[-1].date(), *session):
            df = df.iloc[:-1]
    elif df_live is not None:
        df = _drop_in_progress_last_bar(df, df_live, ticker)
    elif not df.empty:
        exchange_open = time_engine.is_market_open(history_exchange(ticker))
        last_date = df.index[-1].date()
        if is_daily_bar_still_forming(last_date, last_date, exchange_open):
            df = df.iloc[:-1]
    return apply_saved_repairs(ticker, df)


def write_daily_history(df: pd.DataFrame, path) -> pd.DataFrame:
    """Yahoo answers with a symbol's last bar alone once its listing is gone or damaged (found 2026-10-16: replacing the file erased 475-508 stored bars for 23 tickers); when the answer fails to cover at least as many stored in-window bars as it carries, the stored bars are kept and the answer is merged in. Returns the frame written."""
    try:
        stored = pd.read_parquet(path)
    except (OSError, ValueError):
        stored = None
    if stored is not None and not stored.empty and not df.empty:
        window_start = df.index[-1] - pd.DateOffset(years=HISTORY_YEARS)
        uncovered = len(stored[(stored.index >= window_start) & (stored.index < df.index[0])])
        if uncovered >= len(df):
            merged = pd.concat([stored[~stored.index.isin(df.index)], df]).sort_index()
            df = trim_listing_break(merged[merged.index >= merged.index[-1] - pd.DateOffset(years=HISTORY_YEARS)])
            logger.info("Kept %s stored daily bars for %s: Yahoo returned only its newest bar(s).", uncovered, os.path.basename(path)[:-len(".parquet")])
    write_parquet_atomic(df, path)
    return df


def persist_daily_history(ticker: str, df: pd.DataFrame, df_live: Optional[pd.DataFrame]) -> bool:
    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker:
        logger.warning("Skipping historical write for unsafe ticker %r.", ticker)
        return False
    history_root = os.path.realpath(HISTORICAL_DIR)
    path = os.path.realpath(os.path.join(history_root, f"{safe_ticker}.parquet"))
    if not path.startswith(history_root + os.sep):
        logger.warning("Skipping historical path outside cache root for ticker %r.", ticker)
        return False
    df = prepare_daily_history(ticker, df, df_live, drop_missing_volume=True)
    if df.empty:
        return False
    write_daily_history(df, path)
    return True


def fetch_daily_history(ticker: str, *, force_refresh=False):
    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker or is_excluded_from_yahoo_fetch(ticker):
        return None
    history_root = os.path.realpath(HISTORICAL_DIR)
    path = os.path.realpath(os.path.join(history_root, f"{safe_ticker}.parquet"))
    if not path.startswith(history_root + os.sep):
        logger.error("Refusing historical path outside cache root for ticker %r.", ticker)
        return None
    if force_refresh:
        data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d", force_refresh=True, repair=True)
    else:
        data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d", repair=True)
    df = data.get(ticker)
    if df is None or df.empty:
        return None
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df = prepare_daily_history(ticker, df, None)
    if df.empty:
        return None
    return write_daily_history(df, path)
