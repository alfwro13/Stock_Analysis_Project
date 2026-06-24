import time
import logging
import threading
from collections import namedtuple
from typing import Optional

import yfinance as yf
import pandas as pd

from tools.network_engine import yahoo_connection_boundary, wait_for_yahoo_rate_limit_reset

logger = logging.getLogger(__name__)

class _RateLimitAwareLock:
    # Waits on the global 429 circuit-breaker event *before* acquiring the real lock.
    # Without this, a thread sleeping inside the lock on 429 backoff starves all other
    # Yahoo callers (background jobs and web-request threads alike) until the sleep ends.
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self):
        wait_for_yahoo_rate_limit_reset()
        return self._lock.__enter__()

    def __exit__(self, *args):
        return self._lock.__exit__(*args)


# Prevents concurrent writes to the yfinance process-global YfData singleton; without this,
# parallel callers corrupt the session/crumb and trigger an infinite 401 re-fetch loop.
_yf_singleton_lock = _RateLimitAwareLock()

_CacheEntry = namedtuple("_CacheEntry", ["data", "expires_at"])

_TTLS: dict[str, int] = {
    "history":               14400,  # 4 h — daily bars, any period
    "intraday_1m":              60,
    "intraday_2m":             120,
    "intraday_5m":             300,
    "intraday_15m":            300,
    "intraday_30m":            600,
    "intraday":                300,  # fallback for unlisted intervals
    "info":                  21600,  # 6 h
    "options_chain":           900,  # 15 min
    "options_expirations":     900,
    "news":                  14400,  # 4 h
    "insider_transactions":  86400,  # 24 h
    "earnings_dates":        86400,
    "fund_holdings":         86400,
    "ticker_actions":        86400,
    "fx_rate":                 600,  # 10 min
    "annual_financials":     86400,  # 24 h — annual statements change quarterly
    "isin_search":           86400,  # 24 h — ISIN→ticker mapping is stable
}


class YahooEngine:
    def __init__(self) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _ttl(self, data_type: str, interval: str = "") -> int:
        if data_type == "intraday":
            return _TTLS.get(f"intraday_{interval}", _TTLS["intraday"])
        return _TTLS.get(data_type, 300)

    def _get(self, key: str):
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and time.time() < entry.expires_at:
                self._hits += 1
                return entry.data
            self._misses += 1
            return None

    def _set(self, key: str, data, ttl: int) -> None:
        with self._lock:
            self._cache[key] = _CacheEntry(data=data, expires_at=time.time() + ttl)

    @staticmethod
    def _slice_bulk(
        df_bulk: pd.DataFrame, ticker: str, is_single: bool
    ) -> Optional[pd.DataFrame]:
        """Extract and strip-tz one ticker's slice from a yf.download bulk frame."""
        if df_bulk.empty:
            return None
        if isinstance(df_bulk.columns, pd.MultiIndex):
            if ticker not in df_bulk.columns.get_level_values(0):
                return None
            df = df_bulk[ticker].copy()
        elif is_single:
            df = df_bulk.copy()
        else:
            return None
        if df.empty:
            return None
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        return df

    def get_price_history(
        self,
        tickers: list[str],
        period: str = "2y",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        # Returns {ticker: DataFrame}; missing/failed tickers omitted — callers fall back to local Parquet.
        tickers = list(dict.fromkeys(tickers))
        ttl = self._ttl("history")
        key_fn = lambda t: f"history:{t}:{period}:{interval}"

        result: dict[str, Optional[pd.DataFrame]] = {t: self._get(key_fn(t)) for t in tickers}
        missing = [t for t, v in result.items() if v is None]

        if missing:
            try:
                with _yf_singleton_lock:
                    with yahoo_connection_boundary(f"Price History {period}/{interval}") as session:
                        df_bulk = yf.download(
                            missing, period=period, interval=interval,
                            group_by="ticker", auto_adjust=True, progress=False,
                            session=session,
                        )
                if not df_bulk.empty:
                    is_single = len(missing) == 1
                    for t in missing:
                        df = self._slice_bulk(df_bulk, t, is_single)
                        self._set(key_fn(t), df, ttl)
                        result[t] = df
            except Exception:
                logger.error("get_price_history failed for %s", missing, exc_info=True)

        return {t: df for t, df in result.items() if df is not None}

    def get_intraday(
        self,
        tickers: list[str],
        period: str = "1d",
        interval: str = "5m",
        prepost: bool = False,
    ) -> dict[str, pd.DataFrame]:
        # Cache key includes the prepost flag to keep extended-hours data separate.
        tickers = list(dict.fromkeys(tickers))
        ttl = self._ttl("intraday", interval)
        pp = "pp" if prepost else ""
        key_fn = lambda t: f"intraday:{t}:{period}:{interval}:{pp}"

        result: dict[str, Optional[pd.DataFrame]] = {t: self._get(key_fn(t)) for t in tickers}
        missing = [t for t, v in result.items() if v is None]

        if missing:
            try:
                with _yf_singleton_lock:
                    with yahoo_connection_boundary(f"Intraday {period}/{interval}") as session:
                        df_bulk = yf.download(
                            missing, period=period, interval=interval, prepost=prepost,
                            group_by="ticker", auto_adjust=True, progress=False,
                            session=session,
                        )
                if not df_bulk.empty:
                    is_single = len(missing) == 1
                    for t in missing:
                        df = self._slice_bulk(df_bulk, t, is_single)
                        self._set(key_fn(t), df, ttl)
                        result[t] = df
            except Exception:
                logger.error("get_intraday failed for %s", missing, exc_info=True)

        return {t: df for t, df in result.items() if df is not None}

    def get_ticker_info(self, ticker: str) -> Optional[dict]:
        """Raw yfinance .info dict for one ticker, cached 6 h."""
        key = f"info:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Ticker Info: {ticker}") as session:
                    info = yf.Ticker(ticker, session=session).info
            if info:
                self._set(key, info, _TTLS["info"])
                return info
        except Exception:
            logger.error("get_ticker_info failed for %s", ticker, exc_info=True)
        return None

    def get_options_expirations(self, ticker: str) -> Optional[list]:
        """Available options expiration dates for a ticker, cached 15 min."""
        key = f"options_expirations:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Options Expirations: {ticker}") as session:
                    expirations = yf.Ticker(ticker, session=session).options
            if expirations:
                result = list(expirations)
                self._set(key, result, _TTLS["options_expirations"])
                return result
        except Exception:
            logger.error("get_options_expirations failed for %s", ticker, exc_info=True)
        return None

    def get_options_chain(
        self, ticker: str, expiry: str
    ) -> Optional[tuple[pd.DataFrame, pd.DataFrame]]:
        """(calls_df, puts_df) for a specific expiry, cached 15 min."""
        key = f"options_chain:{ticker}:{expiry}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Options Chain: {ticker} {expiry}") as session:
                    chain = yf.Ticker(ticker, session=session).option_chain(expiry)
            result = (chain.calls, chain.puts)
            self._set(key, result, _TTLS["options_chain"])
            return result
        except Exception:
            logger.error("get_options_chain failed for %s %s", ticker, expiry, exc_info=True)
        return None

    def get_news(self, ticker: str) -> Optional[list]:
        """Latest news items for a ticker, cached 4 h."""
        key = f"news:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"News: {ticker}") as session:
                    news = yf.Ticker(ticker, session=session).news
            if news is not None:
                self._set(key, news, _TTLS["news"])
                return news
        except Exception:
            logger.error("get_news failed for %s", ticker, exc_info=True)
        return None

    def get_insider_transactions(self, ticker: str) -> Optional[pd.DataFrame]:
        """SEC Form 4 insider transactions, cached 24 h."""
        key = f"insider_transactions:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Insider Transactions: {ticker}") as session:
                    tk = yf.Ticker(ticker, session=session)
                    try:
                        df = tk.insider_transactions
                    except Exception:
                        df = tk.get_insider_transactions()
            if df is not None and not df.empty:
                self._set(key, df, _TTLS["insider_transactions"])
                return df
        except Exception:
            logger.debug("get_insider_transactions failed for %s", ticker, exc_info=True)
        return None

    def get_earnings_dates(self, ticker: str, limit: int = 10) -> Optional[pd.DataFrame]:
        """Historical earnings dates, cached 24 h. limit controls how many rows yfinance returns."""
        key = f"earnings_dates:{ticker}:{limit}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Earnings Dates: {ticker}") as session:
                    df = yf.Ticker(ticker, session=session).get_earnings_dates(limit=limit)
            if df is not None and not df.empty:
                self._set(key, df, _TTLS["earnings_dates"])
                return df
        except Exception:
            logger.error("get_earnings_dates failed for %s", ticker, exc_info=True)
        return None

    def get_fund_holdings(self, ticker: str) -> Optional[pd.DataFrame]:
        """Top fund holdings DataFrame, cached 24 h."""
        key = f"fund_holdings:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Fund Holdings: {ticker}") as session:
                    funds_data = yf.Ticker(ticker, session=session).get_funds_data()
            if funds_data is not None:
                df = funds_data.top_holdings
                if df is not None and not df.empty:
                    self._set(key, df, _TTLS["fund_holdings"])
                    return df
        except Exception:
            logger.error("get_fund_holdings failed for %s", ticker, exc_info=True)
        return None

    def get_ticker_actions(self, ticker: str) -> Optional[pd.DataFrame]:
        """Dividends/splits actions DataFrame, cached 24 h."""
        key = f"ticker_actions:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Ticker Actions: {ticker}") as session:
                    actions = yf.Ticker(ticker, session=session).actions
            if actions is not None:
                self._set(key, actions, _TTLS["ticker_actions"])
                return actions
        except Exception:
            logger.error("get_ticker_actions failed for %s", ticker, exc_info=True)
        return None

    def get_annual_financials(
        self, ticker: str
    ) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """Annual balance sheet, income statement, and cash flow DataFrames. Cached 24 h.
        Returns (balance_sheet, income_stmt, cash_flow); any element may be None on failure."""
        key = f"annual_financials:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Annual Financials: {ticker}") as session:
                    tk  = yf.Ticker(ticker, session=session)
                    bs  = tk.balance_sheet
                    fin = tk.income_stmt
                    cf  = tk.cash_flow
            result = (
                bs  if bs  is not None and not bs.empty  else None,
                fin if fin is not None and not fin.empty else None,
                cf  if cf  is not None and not cf.empty  else None,
            )
            self._set(key, result, _TTLS["annual_financials"])
            return result
        except Exception:
            logger.error("get_annual_financials failed for %s", ticker, exc_info=True)
        return (None, None, None)

    def get_fx_rate(self, pair: str) -> Optional[float]:
        # Returns None on failure; callers should use their own stale-cache fallback.
        key = f"fx_rate:{pair}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"FX Rate: {pair}") as session:
                    df = yf.Ticker(pair, session=session).history(period="1d")
            if not df.empty:
                rate = float(df["Close"].iloc[-1])
                self._set(key, rate, _TTLS["fx_rate"])
                return rate
        except Exception:
            logger.error("get_fx_rate failed for %s", pair, exc_info=True)
        return None

    def get_single_ticker_history(self, ticker: str, period: str = "5d") -> Optional[pd.DataFrame]:
        """Single-ticker daily history via yf.Ticker.history(); fallback for tickers yf.download() does not support."""
        key = f"single_history:{ticker}:{period}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Single History: {ticker}") as session:
                    df = yf.Ticker(ticker, session=session).history(period=period)
            if df is not None and not df.empty:
                if df.index.tz is not None:
                    df.index = df.index.tz_convert(None)
                self._set(key, df, _TTLS["history"])
                return df
        except Exception:
            logger.error("get_single_ticker_history failed for %s", ticker, exc_info=True)
        return None

    def search_by_isin(self, isin: str) -> Optional[str]:
        """Return the first Yahoo Finance symbol for an ISIN, or None on failure."""
        key = f"isin_search:{isin}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={isin}"
            with yahoo_connection_boundary(f"ISIN search: {isin}") as session:
                resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                quotes = resp.json().get("quotes", [])
                if quotes:
                    symbol = quotes[0].get("symbol")
                    if symbol:
                        self._set(key, symbol, _TTLS["isin_search"])
                        return symbol
        except Exception:
            logger.debug("ISIN search failed for %s", isin, exc_info=True)
        return None

    def get_stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_pct": round(100 * self._hits / total, 1) if total else 0.0,
                "cached_keys": len(self._cache),
            }

    def invalidate(self, ticker: Optional[str] = None) -> None:
        """Flush one ticker's cache entries, or the entire cache when ticker is None."""
        with self._lock:
            if ticker is None:
                self._cache.clear()
                logger.info("Yahoo engine cache cleared (all entries).")
            else:
                keys = [
                    k for k in self._cache
                    if f":{ticker}:" in k or k.endswith(f":{ticker}")
                ]
                for k in keys:
                    del self._cache[k]
                logger.info("Yahoo engine cache cleared for %s (%d keys).", ticker, len(keys))


yahoo_engine = YahooEngine()


def fetch_diagnostic_history(session) -> "pd.DataFrame":
    """Fetch 1-day SPY history using a caller-supplied session (e.g. curl_cffi for IPv6 tests).
    Kept here so yfinance stays confined to yahoo_engine.py."""
    return yf.Ticker("SPY", session=session).history(period="1d")
