import logging
import os
import time
from typing import Dict, Optional

import pandas as pd

from config import HISTORICAL_DIR
from database import upsert_instrument_session
from market_session_helpers import cached_registry_exchange_map, has_reported_session, reported_session, resolve_ticker_exchange, stored_sessions
from price_repair_engine import apply_saved_repairs
from utils import is_daily_bar_still_forming, is_excluded_from_yahoo_fetch, safe_ticker_filename, write_parquet_atomic
from yahoo_engine import yahoo_engine
import time_engine

logger = logging.getLogger(__name__)

SESSION_REFRESH_SECONDS = 30 * 86400
SESSION_RETRY_SECONDS = 3600

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


def prepare_daily_history(ticker: str, df: pd.DataFrame, df_live: Optional[pd.DataFrame], *, drop_missing_volume: bool = False) -> pd.DataFrame:
    """Single cleaning path for every daily-history writer so saved repairs survive any refresh; df_live=None means no live feed was fetched, so the exchange state and the bar's own date decide whether the last bar is still forming; a ticker with a Yahoo-reported session is judged by that window alone."""
    df = df.dropna(subset=["Close", "Volume"] if drop_missing_volume else ["Close"])
    for col in ("Open", "High", "Low"):
        if col in df.columns:
            mask = (df[col] == 0) & (df["Close"] > 0)
            df.loc[mask, col] = df.loc[mask, "Close"]
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
    write_parquet_atomic(df, path)
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
        data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d", force_refresh=True)
    else:
        data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
    df = data.get(ticker)
    if df is None or df.empty:
        return None
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df = prepare_daily_history(ticker, df, None)
    if df.empty:
        return None
    write_parquet_atomic(df, path)
    return df
