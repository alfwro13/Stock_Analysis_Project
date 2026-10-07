import json
import os
import time
import random
import logging
from pathlib import Path
import pandas as pd
from typing import Set, List, Dict, Any, Optional

from config import HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR, load_config
from database import get_watchlist_tickers, get_all_account_tickers, get_mutual_fund_tickers, get_registry_spot_future_tickers
from gilt_engine import GiltDataService
from yahoo_engine import yahoo_engine
from price_repair_engine import apply_saved_repairs
import time_engine

from utils import normalize_ticker, is_daily_bar_still_forming, ignored_tickers_set, is_excluded_from_yahoo_fetch, safe_ticker_filename  # noqa: F401 — normalize_ticker re-exported for callers

logger = logging.getLogger(__name__)


def _drop_in_progress_last_bar(df_daily: pd.DataFrame, df_live: Optional[pd.DataFrame], ticker: Optional[str] = None) -> pd.DataFrame:
    """Yahoo's daily endpoint often includes today's still-forming bar when queried mid-session; trim it so the stored daily history never stores a partial-session close as if it were final (same comparison market_pulse.fetch_and_save_pulse already makes against its own live feed). Unlike the intraday scanners' own use of is_daily_bar_still_forming(), this runs at arbitrary times of day (nightly Update Pipeline, on-demand single-ticker fetch) rather than only while an exchange is confirmed open, so ticker must be passed to resolve whether its exchange has already closed for the day — otherwise a same-day post-close fetch is indistinguishable from a genuine mid-session one."""
    if df_live is None or df_live.empty or len(df_daily) < 2:
        return df_daily
    exchange_open = time_engine.is_market_open(time_engine.ticker_exchange_from_suffix(ticker)) if ticker else None
    if is_daily_bar_still_forming(df_daily.index[-1].date(), df_live.index[-1].date(), exchange_open):
        return df_daily.iloc[:-1]
    return df_daily


def _prepare_daily_history(ticker: str, df: pd.DataFrame, df_live: Optional[pd.DataFrame], *, drop_missing_volume: bool = False) -> pd.DataFrame:
    """Single cleaning path for every daily-history writer so saved repairs survive any refresh; df_live=None means no live feed was fetched, so the exchange state and the bar's own date decide whether the last bar is still forming."""
    df = df.dropna(subset=["Close", "Volume"] if drop_missing_volume else ["Close"])
    for col in ("Open", "High", "Low"):
        if col in df.columns:
            mask = (df[col] == 0) & (df["Close"] > 0)
            df.loc[mask, col] = df.loc[mask, "Close"]
    if df_live is not None:
        df = _drop_in_progress_last_bar(df, df_live, ticker)
    elif not df.empty:
        exchange_open = time_engine.is_market_open(time_engine.ticker_exchange_from_suffix(ticker))
        last_date = df.index[-1].date()
        if is_daily_bar_still_forming(last_date, last_date, exchange_open):
            df = df.iloc[:-1]
    return apply_saved_repairs(ticker, df)


class DataEngine:
    def __init__(self) -> None:
        self.watchlist: Dict[str, Any] = {"watchlist": get_watchlist_tickers()}
        self.account_tickers: List[str] = get_all_account_tickers()
        self._ensure_directories()

    @staticmethod
    def _ensure_directories() -> None:
        """Idempotently guarantees all data output directories exist before any write."""
        for directory in (HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR):
            try:
                Path(directory).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error('Failed to create data directory %s: %s', directory, e)

    @staticmethod
    def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        return df

    def get_all_tickers(self) -> List[str]:
        from accounts_engine import get_combined_holdings
        tickers: Set[str] = set()
        ignored_tickers = ignored_tickers_set(load_config())

        for ticker in get_combined_holdings().keys():
            if ticker and not is_excluded_from_yahoo_fetch(ticker, ignored_tickers):
                tickers.add(normalize_ticker(ticker))

        if isinstance(self.watchlist.get("watchlist"), list):
            for ticker in self.watchlist["watchlist"]:
                if ticker:
                    tickers.add(normalize_ticker(ticker))

        for ticker in self.account_tickers:
            if ticker:
                tickers.add(normalize_ticker(ticker))

        for ticker in get_registry_spot_future_tickers():
            if not is_excluded_from_yahoo_fetch(ticker, ignored_tickers):
                tickers.add(normalize_ticker(ticker))

        valid_tickers = [t for t in tickers if t not in ignored_tickers]
        return sorted(valid_tickers)

    def fetch_market_baseline(self) -> None:
        logger.info("Fetching Market and Intermarket Baselines (US & UK)...")
        try:
            baselines = {
                "^GSPC": "SP500_BASELINE",
                "^FTSE": "FTSE_BASELINE",
                "^TYX": "TYX_BASELINE",
                "^TNX": "TNX_BASELINE",
                "DX-Y.NYB": "DXY_BASELINE",
                "GBPUSD=X": "GBPUSD_BASELINE",
                "SPY": "SPY_BASELINE",
                "RSP": "RSP_BASELINE",
            }

            ticker_dfs = yahoo_engine.get_price_history(list(baselines.keys()), period="2y", interval="1d", force_refresh=True)

            if not ticker_dfs:
                logger.warning("Baseline bulk download returned empty.")
            else:
                for ticker, name in baselines.items():
                    df = ticker_dfs.get(ticker)
                    if df is None or df.empty:
                        continue
                    df = df.dropna(subset=['Close'])
                    for col in ('Open', 'High', 'Low'):
                        mask = (df[col] == 0) & (df['Close'] > 0)
                        df.loc[mask, col] = df.loc[mask, 'Close']
                    if not df.empty:
                        df.to_parquet(HISTORICAL_DIR / f"{name}.parquet", engine='pyarrow')
                logger.info("All Market and Intermarket Baselines secured successfully.")

        except Exception as e:
            logger.error('Failed to fetch Market baselines: %s', e)

        try:
            GiltDataService().sync_gilt_data()
        except Exception as e:
            logger.error('Gilt data sync failed (independent of Yahoo baselines): %s', e)

    def bulk_download_historical(self, tickers: List[str]) -> None:
        """Vectorized bulk download of 2-year daily prices to bypass rate limits."""
        if not tickers:
            return

        logger.info('Bulk downloading 2Y Macro Historical data for %s assets...', len(tickers))
        try:
            ticker_dfs = yahoo_engine.get_price_history(tickers, period="2y", interval="1d", force_refresh=True)
            if not ticker_dfs:
                logger.warning("Historical bulk download returned empty.")
                return
            mutual_funds = get_mutual_fund_tickers(tickers)
            intraday_targets = [t for t in ticker_dfs if t not in mutual_funds]
            live_dfs = yahoo_engine.get_intraday(intraday_targets, period="1d", interval="5m") if intraday_targets else {}
            history_root = os.path.realpath(HISTORICAL_DIR)
            for ticker, df in ticker_dfs.items():
                if df is None or df.empty:
                    continue
                safe_ticker = safe_ticker_filename(ticker)
                if not safe_ticker:
                    logger.warning("Skipping historical write for unsafe ticker %r.", ticker)
                    continue
                path = os.path.realpath(os.path.join(history_root, f"{safe_ticker}.parquet"))
                if not path.startswith(history_root + os.sep):
                    logger.warning("Skipping historical path outside cache root for ticker %r.", ticker)
                    continue
                df = _prepare_daily_history(ticker, df, live_dfs.get(ticker, pd.DataFrame()), drop_missing_volume=True)
                if not df.empty:
                    df.to_parquet(path, engine='pyarrow')
        except Exception as e:
            logger.error('Fatal error during bulk historical download: %s', e)

    def bulk_download_intraday(self, tickers: List[str]) -> None:
        if not tickers:
            return

        mutual_funds = get_mutual_fund_tickers(tickers)
        if mutual_funds:
            tickers = [t for t in tickers if t not in mutual_funds]
        if not tickers:
            return

        logger.info('Bulk downloading 1D Intraday data for %s assets...', len(tickers))
        try:
            ticker_dfs = yahoo_engine.get_intraday(tickers, period="1d", interval="5m")
            if not ticker_dfs:
                return
            intraday_root = os.path.realpath(INTRADAY_DIR)
            for ticker, df in ticker_dfs.items():
                if df is None or df.empty:
                    continue
                safe_ticker = safe_ticker_filename(ticker)
                if not safe_ticker:
                    logger.warning("Skipping intraday write for unsafe ticker %r.", ticker)
                    continue
                path = os.path.realpath(os.path.join(intraday_root, f"{safe_ticker}_intraday.parquet"))
                if not path.startswith(intraday_root + os.sep):
                    logger.warning("Skipping intraday path outside cache root for ticker %r.", ticker)
                    continue
                df = df.dropna(subset=['Close'])
                if not df.empty:
                    df.to_parquet(path, engine='pyarrow')
        except Exception as e:
            logger.error('Fatal error during bulk intraday download: %s', e)

    def drip_feed_fundamentals(self, tickers: List[str]) -> None:
        """
        Slow, randomized drip-feed loop to fetch the raw .info JSON payloads.
        Mitigates strict JSON-endpoint rate-limiting.
        """
        logger.info('Drip-feeding Fundamental JSONs for %s assets...', len(tickers))
        fundamentals_root = os.path.realpath(FUNDAMENTALS_DIR)
        for i, ticker in enumerate(tickers):
            try:
                safe_ticker = safe_ticker_filename(ticker)
                if not safe_ticker:
                    logger.warning("Skipping fundamentals fetch for unsafe ticker %r.", ticker)
                    continue
                path = os.path.realpath(os.path.join(fundamentals_root, f"{safe_ticker}.json"))
                if not path.startswith(fundamentals_root + os.sep):
                    logger.warning("Skipping fundamentals path outside cache root for ticker %r.", ticker)
                    continue
                fundamentals = yahoo_engine.get_ticker_info(ticker)

                if fundamentals:
                    with open(path, 'w') as f:
                        json.dump(fundamentals, f, default=str)

                if i > 0 and i % 50 == 0:
                    logger.info('Fundamentals progress: %s/%s...', i, len(tickers))

            except Exception as e:
                logger.warning('Failed to fetch fundamentals for %s: %s', ticker, e)
            finally:
                # Institutional Anti-Bot Randomization — pacing stays here, not in the engine
                time.sleep(random.uniform(0.5, 2.0))

    def fetch_and_save_data(self, ticker: str) -> bool:
        """Legacy single-ticker fetcher used by manual UI refresh."""
        logger.info('Processing Data for single ticker %s...', ticker)
        safe_ticker = safe_ticker_filename(ticker)
        if not safe_ticker:
            logger.error("Refusing to fetch unsafe ticker %r.", ticker)
            return False
        intraday_root = os.path.realpath(INTRADAY_DIR)
        intraday_path = os.path.realpath(os.path.join(intraday_root, f"{safe_ticker}_intraday.parquet"))
        history_root = os.path.realpath(HISTORICAL_DIR)
        history_path = os.path.realpath(os.path.join(history_root, f"{safe_ticker}.parquet"))
        fundamentals_root = os.path.realpath(FUNDAMENTALS_DIR)
        fundamentals_path = os.path.realpath(os.path.join(fundamentals_root, f"{safe_ticker}.json"))
        if (
            not intraday_path.startswith(intraday_root + os.sep)
            or not history_path.startswith(history_root + os.sep)
            or not fundamentals_path.startswith(fundamentals_root + os.sep)
        ):
            logger.error("Refusing cache path outside configured root for ticker %r.", ticker)
            return False
        try:
            persisted = False

            df_live = pd.DataFrame()
            if ticker not in get_mutual_fund_tickers([ticker]):
                _intraday = yahoo_engine.get_intraday([ticker], period="1d", interval="5m")
                df_intraday = _intraday.get(ticker, pd.DataFrame())
                if not df_intraday.empty:
                    self._strip_tz(df_intraday)
                    df_intraday.to_parquet(intraday_path, engine='pyarrow')
                    persisted = True
                    df_live = df_intraday

            _daily = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
            df_daily = _daily.get(ticker, pd.DataFrame())
            if not df_daily.empty:
                self._strip_tz(df_daily)
                df_daily = _prepare_daily_history(ticker, df_daily, df_live)
                if not df_daily.empty:
                    df_daily.to_parquet(history_path, engine='pyarrow')
                    persisted = True

            fundamentals = yahoo_engine.get_ticker_info(ticker) or {}
            if fundamentals:
                with open(fundamentals_path, 'w') as f:
                    json.dump(fundamentals, f, default=str)

            if not persisted:
                logger.warning('No price data returned for %s — nothing persisted.', ticker)
                return False

            return True
        except Exception as e:
            logger.error('Pipeline failed for %s: %s', ticker, str(e))
            return False

    def update_all_data(self) -> None:
        self.fetch_market_baseline()

        tickers = self.get_all_tickers()
        logger.info('Target Acquisition: Found %s unique assets.', len(tickers))

        if not tickers:
            return

        self.bulk_download_historical(tickers)
        self.bulk_download_intraday(tickers)
        self.drip_feed_fundamentals(tickers)

        logger.info("Massive data pipeline ingestion completed successfully.")


def fetch_and_save_single_ticker(ticker: str) -> bool:
    """Background-task entry point for a brand-new account ticker — avoids DataEngine.__init__'s
    portfolio/watchlist/account-ticker DB reads, which are irrelevant for a single fetch."""
    return DataEngine.__new__(DataEngine).fetch_and_save_data(ticker)


def _fetch_daily_history(ticker: str, *, force_refresh=False):
    import tempfile

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
    df = _prepare_daily_history(ticker, df, None)
    if df.empty:
        return None
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=HISTORICAL_DIR, suffix=".parquet", delete=False) as handle:
            temporary = Path(handle.name)
        df.to_parquet(temporary, engine="pyarrow")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return df


def daily_history_cache_revision(ticker: str, *, refresh_stale: bool = False):
    from cache_refresh_helpers import request_cache_refresh

    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker:
        return None
    history_root = os.path.realpath(HISTORICAL_DIR)
    path = os.path.realpath(os.path.join(history_root, f"{safe_ticker}.parquet"))
    if not path.startswith(history_root + os.sep):
        return None
    try:
        state = os.stat(path)
    except FileNotFoundError:
        state = None
    if refresh_stale and not is_excluded_from_yahoo_fetch(ticker):
        settled_close = time_engine.last_settled_session_close_utc(time_engine.ticker_exchange_from_suffix(ticker))
        if state is None or state.st_mtime < settled_close.timestamp():
            request_cache_refresh("daily:" + ticker, lambda: _fetch_daily_history(ticker, force_refresh=True))
    if state is None:
        return None
    return (path, state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)


def load_or_fetch_daily_history(ticker: str, *, cache_only: bool = False) -> Optional[pd.DataFrame]:
    from cache_refresh_helpers import submit_cache_refresh

    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker:
        logger.error("Refusing to load history for unsafe ticker %r.", ticker)
        return None
    history_root = os.path.realpath(HISTORICAL_DIR)
    path = os.path.realpath(os.path.join(history_root, f"{safe_ticker}.parquet"))
    if not path.startswith(history_root + os.sep):
        logger.error("Refusing historical path outside cache root for ticker %r.", ticker)
        return None
    df = None
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            logger.error("Failed to read historical parquet for %s: %s", ticker, e)
    if cache_only:
        if df is None:
            from cache_refresh_helpers import request_cache_refresh
            if not is_excluded_from_yahoo_fetch(ticker):
                request_cache_refresh("daily:" + ticker, lambda: _fetch_daily_history(ticker, force_refresh=True))
        else:
            daily_history_cache_revision(ticker, refresh_stale=True)
        return df
    if df is not None:
        return df
    try:
        future = submit_cache_refresh("daily:" + ticker, lambda: _fetch_daily_history(ticker))
        return future.result() if future is not None else None
    except Exception as e:
        logger.error("Failed to fetch fallback history for %s: %s", ticker, e)
        return None


def load_or_fetch_intraday_history(ticker: str):
    from cache_refresh_helpers import submit_cache_refresh
    import tempfile

    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker or is_excluded_from_yahoo_fetch(ticker):
        return None
    root = os.path.realpath(INTRADAY_DIR)
    path = os.path.realpath(os.path.join(root, f"{safe_ticker}_intraday.parquet"))
    if not path.startswith(root + os.sep):
        return None
    if ticker in get_mutual_fund_tickers([ticker]):
        return None

    def read_fresh():
        if not path.startswith(root + os.sep):
            return None
        try:
            df = pd.read_parquet(path)
            fetched_at = df.attrs.get("yahoo_fetched_at", os.path.getmtime(path))
            if not df.empty and time.time() - fetched_at < yahoo_engine._ttl("intraday", "5m"):
                return df
        except (OSError, ValueError):
            pass
        return None

    cached = read_fresh()
    if cached is not None:
        return cached

    def refresh():
        cached = read_fresh()
        if cached is not None:
            return cached
        df = yahoo_engine.get_intraday([ticker], period="1d", interval="5m").get(ticker)
        if df is None or df.empty:
            return None
        df = df.copy()
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        os.makedirs(root, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, suffix=".parquet", delete=False) as handle:
                temporary = Path(handle.name)
            df.to_parquet(temporary, engine="pyarrow")
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return df

    future = submit_cache_refresh("intraday:1d:5m:" + ticker, refresh)
    return future.result() if future is not None else None


_EXPECTED_YFINANCE_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}

def run_yfinance_smoke_test() -> bool:
    """Writes to the notifications table on failure so it's visible in the UI, not just server logs."""
    from database import log_notification
    try:
        _result = yahoo_engine.get_price_history(["SPY"], period="5d", interval="1d")
        df = _result.get("SPY", pd.DataFrame())

        missing = _EXPECTED_YFINANCE_COLUMNS - set(df.columns)

        if df.empty or missing:
            problem = "empty response" if df.empty else f"missing columns: {missing}"
            msg = (
                f"yfinance schema check FAILED ({problem}). "
                "Price data may be silently corrupt — check yfinance/pandas versions."
            )
            logger.error(msg)
            log_notification("Error", msg)
            return False

        logger.info(
            'yfinance schema OK — SPY %s rows, columns: %s', len(df), sorted(df.columns.tolist())
        )
        return True

    except Exception as exc:
        msg = (
            f"yfinance smoke test raised an exception: {exc}. "
            "Data pipeline may be broken — check network and yfinance version."
        )
        logger.error(msg)
        log_notification("Error", msg)
        return False


if __name__ == "__main__":
    engine = DataEngine()
    engine.update_all_data()
