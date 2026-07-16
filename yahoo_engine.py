import time
import logging
import threading
from collections import namedtuple
from typing import Optional

import yfinance as yf
import pandas as pd

from tools.network_engine import yahoo_connection_boundary, wait_for_yahoo_rate_limit_reset, suppress_yf_delisted_noise
from notification_engine import notify

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
    "ticker_search":          3600,  # 1 h — company-name/ticker autocomplete
    "market_state":            300,  # 5 min — needs to reflect the live open/closed transition
}


_INTRADAY_GAP_ALERT_MINUTES = 30  # how long a ticker must be empty before it's "persistent", not a blip
_HISTORY_GAP_ALERT_MINUTES = 1800  # 30h — daily bars only refresh ~once/night, so this is "missed a nightly run", not a blip


class YahooEngine:
    def __init__(self) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        # Central intraday-data-gap tracker (see _track_intraday_gap_misses/_hits): in-memory,
        # not DB-backed — resets on restart, which is fine since a persisting gap re-detects and
        # re-times itself within _INTRADAY_GAP_ALERT_MINUTES regardless.
        self._intraday_gap_since: dict[str, float] = {}
        self._intraday_gap_alerted: set[str] = set()
        self._intraday_gap_lock = threading.Lock()
        # Mirrors the intraday gap tracker above, for the daily-history endpoint — closes the gap that let
        # MSFT/META silently fall out of the nightly bulk download for weeks with no notification at all
        # (found 2026-07-16: is_daily_bar_still_forming and current_price_map guard against a still-forming
        # bar, but nothing previously detected a ticker simply missing from the bulk yf.download result).
        self._history_gap_since: dict[str, float] = {}
        self._history_gap_alerted: set[str] = set()
        self._history_gap_lock = threading.Lock()

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
        force_refresh: bool = False,
    ) -> dict[str, pd.DataFrame]:
        # Returns {ticker: DataFrame}; missing/failed tickers omitted — callers fall back to local Parquet.
        tickers = list(dict.fromkeys(tickers))
        ttl = self._ttl("history")
        key_fn = lambda t: f"history:{t}:{period}:{interval}"

        # force_refresh=True for the authoritative nightly writers (data_engine.bulk_download_historical,
        # fetch_market_baseline) -- the 4h TTL exists to spare Yahoo from redundant same-session requests,
        # but it can span across market close: an earlier same-day call (a manual single-ticker refresh, a
        # stock-detail-page view) caches a pre-close bar, and the nightly job then silently writes that
        # stale bar into stock_signals with a fresh last_updated timestamp -- current_price_map() then
        # trusts it as the verified close purely because the timestamp looks new. Found 2026-07-08.
        result: dict[str, Optional[pd.DataFrame]] = (
            {t: None for t in tickers} if force_refresh else {t: self._get(key_fn(t)) for t in tickers}
        )
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

            gap_tickers = [t for t in missing if result.get(t) is None]
            hit_tickers = [t for t in missing if result.get(t) is not None]
            if gap_tickers:
                self._track_history_gap_misses(gap_tickers)
            if hit_tickers:
                self._track_history_gap_hits(hit_tickers)

        return {t: df for t, df in result.items() if df is not None}

    def _track_history_gap_misses(self, tickers: list[str]) -> None:
        """Records the first time each ticker's daily-history fetch came back empty; once a ticker
        has been empty continuously for _HISTORY_GAP_ALERT_MINUTES, fires one aggregated notification
        covering every ticker that just crossed that threshold — mirrors _track_intraday_gap_misses."""
        now = time.time()
        newly_persistent: list[tuple[str, float]] = []
        with self._history_gap_lock:
            for t in tickers:
                since = self._history_gap_since.setdefault(t, now)
                age_min = (now - since) / 60
                if age_min >= _HISTORY_GAP_ALERT_MINUTES and t not in self._history_gap_alerted:
                    self._history_gap_alerted.add(t)
                    newly_persistent.append((t, age_min))
        if newly_persistent:
            self._notify_history_gap(newly_persistent)

    def _track_history_gap_hits(self, tickers: list[str]) -> None:
        """Clears gap tracking for tickers whose fetch just succeeded; fires one aggregated
        recovery notification for any that had previously crossed the alert threshold."""
        recovered = []
        with self._history_gap_lock:
            for t in tickers:
                self._history_gap_since.pop(t, None)
                if t in self._history_gap_alerted:
                    self._history_gap_alerted.discard(t)
                    recovered.append(t)
        if recovered:
            self._notify_history_gap_recovered(recovered)

    @staticmethod
    def _notify_history_gap(newly_persistent: list[tuple[str, float]]) -> None:
        lines = ", ".join(f"{t} ({int(age / 60)}h)" for t, age in sorted(newly_persistent))
        plural = "s" if len(newly_persistent) > 1 else ""
        notify(
            "yahoo_history_gap_alert", "Warning",
            f"Yahoo Finance has returned no daily historical data for {len(newly_persistent)} ticker{plural} "
            f"for at least {_HISTORY_GAP_ALERT_MINUTES // 60} hours: {lines}. Their daily parquet, quant "
            f"score, and any alerts derived from them are frozen at their last successful fetch until this recovers.",
            level="warning",
        )

    @staticmethod
    def _notify_history_gap_recovered(recovered: list[str]) -> None:
        plural = "s" if len(recovered) > 1 else ""
        notify(
            "yahoo_history_gap_alert", "Info",
            f"Yahoo Finance daily historical data has resumed for {len(recovered)} ticker{plural}: "
            f"{', '.join(sorted(recovered))}.",
            level="info",
        )

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
                # A thinly-traded ticker can have zero prints yet today, so Yahoo's period=1d
                # intraday endpoint legitimately returns nothing for it — not a fetch bug, and
                # not something a wider period should paper over with stale multi-day-old data
                # mislabeled as current (confirmed 2026-07-10: LCJP.L/SMGB.L had full daily-bar
                # coverage and a live quote, only the tight 1d intraday window was empty). yfinance
                # logs this as "possibly delisted", which is misleading for this case, so it's
                # demoted to DEBUG here; _track_intraday_gap_misses/_hits below is the actual
                # tracking + escalation mechanism (see class docstring-level comment there).
                suppress_yf_delisted_noise(True)
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
            finally:
                suppress_yf_delisted_noise(False)

            gap_tickers = [t for t in missing if result.get(t) is None]
            hit_tickers = [t for t in missing if result.get(t) is not None]
            if gap_tickers:
                self._track_intraday_gap_misses(gap_tickers)
            if hit_tickers:
                self._track_intraday_gap_hits(hit_tickers)

        return {t: df for t, df in result.items() if df is not None}

    def _track_intraday_gap_misses(self, tickers: list[str]) -> None:
        """Records the first time each ticker's intraday fetch came back empty; once a ticker has
        been empty continuously for _INTRADAY_GAP_ALERT_MINUTES, fires one aggregated notification
        covering every ticker that just crossed that threshold (not one notification per ticker —
        the whole point of tracking this centrally in the engine, rather than per-caller, is to
        catch e.g. the crash/moonshot scan, dip radar, and ETF predictor all hitting the same
        Yahoo-side gap and only alert once)."""
        now = time.time()
        newly_persistent: list[tuple[str, float]] = []
        with self._intraday_gap_lock:
            for t in tickers:
                since = self._intraday_gap_since.setdefault(t, now)
                age_min = (now - since) / 60
                if age_min >= _INTRADAY_GAP_ALERT_MINUTES and t not in self._intraday_gap_alerted:
                    self._intraday_gap_alerted.add(t)
                    newly_persistent.append((t, age_min))
        if newly_persistent:
            self._notify_intraday_gap(newly_persistent)

    def _track_intraday_gap_hits(self, tickers: list[str]) -> None:
        """Clears gap tracking for tickers whose fetch just succeeded; fires one aggregated
        recovery notification for any that had previously crossed the alert threshold."""
        recovered = []
        with self._intraday_gap_lock:
            for t in tickers:
                self._intraday_gap_since.pop(t, None)
                if t in self._intraday_gap_alerted:
                    self._intraday_gap_alerted.discard(t)
                    recovered.append(t)
        if recovered:
            self._notify_intraday_gap_recovered(recovered)

    @staticmethod
    def _notify_intraday_gap(newly_persistent: list[tuple[str, float]]) -> None:
        lines = ", ".join(f"{t} ({int(age)}m)" for t, age in sorted(newly_persistent))
        plural = "s" if len(newly_persistent) > 1 else ""
        notify(
            "yahoo_intraday_gap_alert", "Warning",
            f"Yahoo Finance has returned no intraday data for {len(newly_persistent)} ticker{plural} "
            f"for at least {_INTRADAY_GAP_ALERT_MINUTES} minutes: {lines}. Daily data and quotes may "
            f"still be fine — this only affects intraday charts and intraday-based alerts for these tickers.",
            level="warning",
        )

    @staticmethod
    def _notify_intraday_gap_recovered(recovered: list[str]) -> None:
        plural = "s" if len(recovered) > 1 else ""
        notify(
            "yahoo_intraday_gap_alert", "Info",
            f"Yahoo Finance intraday data has resumed for {len(recovered)} ticker{plural}: {', '.join(sorted(recovered))}.",
            level="info",
        )

    def is_intraday_gap_alerted(self, ticker: str) -> bool:
        """True once a ticker's intraday gap has crossed _INTRADAY_GAP_ALERT_MINUTES and hasn't
        recovered yet — used by the Stock Detail page to show a "data currently unavailable" note
        instead of silently rendering a possibly stale cached chart."""
        with self._intraday_gap_lock:
            return ticker in self._intraday_gap_alerted

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

    def get_market_state(self, ticker: str) -> Optional[str]:
        """Live yfinance marketState ('REGULAR'/'PRE'/'POST'/'CLOSED'/...), cached 5 min —
        deliberately much shorter than get_ticker_info's 6h TTL since this must reflect the
        live open/closed transition, including exchange holidays get_ticker_info's cache would
        otherwise mask for hours."""
        key = f"market_state:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Market State: {ticker}") as session:
                    info = yf.Ticker(ticker, session=session).info
            state = info.get("marketState") if info else None
            if state:
                self._set(key, state, _TTLS["market_state"])
                return state
        except Exception:
            logger.error("get_market_state failed for %s", ticker, exc_info=True)
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
                    # top_holdings is a lazy property that triggers its own network fetch —
                    # must be read before the session context above closes it.
                    df = funds_data.top_holdings if funds_data is not None else None
            if df is not None and not df.empty:
                self._set(key, df, _TTLS["fund_holdings"])
                return df
        except Exception:
            logger.error("get_fund_holdings failed for %s", ticker, exc_info=True)
        return None

    def get_fund_sector_weightings(self, ticker: str) -> Optional[dict]:
        """Fund sector weightings dict (snake_case GICS-style keys, sums to ~1.0), cached 24 h."""
        key = f"fund_sector_weightings:{ticker}"
        cached = self._get(key)
        if cached is not None:
            return cached
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Fund Sector Weightings: {ticker}") as session:
                    funds_data = yf.Ticker(ticker, session=session).get_funds_data()
                    weights = funds_data.sector_weightings if funds_data is not None else None
            if weights:
                self._set(key, weights, _TTLS["fund_holdings"])
                return weights
        except Exception:
            logger.error("get_fund_sector_weightings failed for %s", ticker, exc_info=True)
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

    def search_ticker(self, query: str, max_results: int = 8) -> list[dict]:
        """Company-name or ticker autocomplete for the watchlist add-ticker UI, cached 1 h."""
        key = f"ticker_search:{query.lower()}:{max_results}"
        cached = self._get(key)
        if cached is not None:
            return cached
        result: list[dict] = []
        try:
            with _yf_singleton_lock:
                with yahoo_connection_boundary(f"Ticker Search: {query}") as session:
                    quotes = yf.Search(query, max_results=max_results, session=session).quotes
            result = [
                {
                    "ticker": q.get("symbol"),
                    "company_name": q.get("longname") or q.get("shortname"),
                    "quote_type": q.get("quoteType"),
                }
                for q in quotes if q.get("symbol")
            ]
            self._set(key, result, _TTLS["ticker_search"])
        except Exception:
            logger.error("search_ticker failed for %s", query, exc_info=True)
        return result

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
