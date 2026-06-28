# data_engine.py
import json
import time
import random
import logging
from pathlib import Path
import pandas as pd
from typing import Set, List, Dict, Any

from config import PORTFOLIO_PATH, HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR, load_config
from database import get_watchlist_tickers, get_all_account_tickers, get_mutual_fund_tickers
from gilt_engine import GiltDataService
from yahoo_engine import yahoo_engine

from utils import normalize_ticker  # noqa: F401 — re-exported for callers

logger = logging.getLogger(__name__)


class DataEngine:
    def __init__(self) -> None:
        self.portfolio: Dict[str, Any] = self._load_json(PORTFOLIO_PATH)
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
                logger.error(f"Failed to create data directory {directory}: {e}")

    @staticmethod
    def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        return df

    def _load_json(self, filepath: str) -> Dict[str, Any]:
        """Safely loads JSON files, logging missing/corrupt files without crashing init."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Missing JSON file: {filepath}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Corrupt JSON in {filepath} (line {e.lineno}, col {e.colno}): {e.msg}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error reading {filepath}: {e}")
            return {}
            
    def get_all_tickers(self) -> List[str]:
        tickers: Set[str] = set()

        # defensive against malformed non-dict entries in portfolio.json
        for _asset_key, asset_data in self.portfolio.items():
            if isinstance(asset_data, dict) and asset_data.get("ticker"):
                tickers.add(normalize_ticker(asset_data["ticker"]))

        if isinstance(self.watchlist.get("watchlist"), list):
            for ticker in self.watchlist["watchlist"]:
                if ticker:
                    tickers.add(normalize_ticker(ticker))

        for ticker in self.account_tickers:
            if ticker:
                tickers.add(normalize_ticker(ticker))

        # normalized to match the uppercased ticker set
        config_data = load_config()
        ignored_tickers = {normalize_ticker(t) for t in config_data.get("IGNORED_TICKERS", [])}

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

            ticker_dfs = yahoo_engine.get_price_history(list(baselines.keys()), period="2y", interval="1d")

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
            logger.error(f"Failed to fetch Market baselines: {e}")

        try:
            GiltDataService().sync_gilt_data()
        except Exception as e:
            logger.error(f"Gilt data sync failed (independent of Yahoo baselines): {e}")

    def bulk_download_historical(self, tickers: List[str]) -> None:
        """Vectorized bulk download of 2-year daily prices to bypass rate limits."""
        if not tickers:
            return

        logger.info(f"Bulk downloading 2Y Macro Historical data for {len(tickers)} assets...")
        try:
            ticker_dfs = yahoo_engine.get_price_history(tickers, period="2y", interval="1d")
            if not ticker_dfs:
                logger.warning("Historical bulk download returned empty.")
                return
            for ticker, df in ticker_dfs.items():
                if df is None or df.empty:
                    continue
                df = df.dropna(subset=['Close', 'Volume'])
                for col in ('Open', 'High', 'Low'):
                    mask = (df[col] == 0) & (df['Close'] > 0)
                    df.loc[mask, col] = df.loc[mask, 'Close']
                if not df.empty:
                    df.to_parquet(HISTORICAL_DIR / f"{ticker}.parquet", engine='pyarrow')
        except Exception as e:
            logger.error(f"Fatal error during bulk historical download: {e}")

    def bulk_download_intraday(self, tickers: List[str]) -> None:
        if not tickers:
            return

        mutual_funds = get_mutual_fund_tickers(tickers)
        if mutual_funds:
            tickers = [t for t in tickers if t not in mutual_funds]
        if not tickers:
            return

        logger.info(f"Bulk downloading 1D Intraday data for {len(tickers)} assets...")
        try:
            ticker_dfs = yahoo_engine.get_intraday(tickers, period="1d", interval="5m")
            if not ticker_dfs:
                return
            for ticker, df in ticker_dfs.items():
                if df is None or df.empty:
                    continue
                df = df.dropna(subset=['Close'])
                if not df.empty:
                    df.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
        except Exception as e:
            logger.error(f"Fatal error during bulk intraday download: {e}")

    def drip_feed_fundamentals(self, tickers: List[str]) -> None:
        """
        Slow, randomized drip-feed loop to fetch the raw .info JSON payloads.
        Mitigates strict JSON-endpoint rate-limiting.
        """
        logger.info(f"Drip-feeding Fundamental JSONs for {len(tickers)} assets...")
        for i, ticker in enumerate(tickers):
            try:
                fundamentals = yahoo_engine.get_ticker_info(ticker)

                if fundamentals:
                    with open(FUNDAMENTALS_DIR / f"{ticker}.json", 'w') as f:
                        json.dump(fundamentals, f, default=str)

                if i > 0 and i % 50 == 0:
                    logger.info(f"Fundamentals progress: {i}/{len(tickers)}...")

            except Exception as e:
                logger.warning(f"Failed to fetch fundamentals for {ticker}: {e}")
            finally:
                # Institutional Anti-Bot Randomization — pacing stays here, not in the engine
                time.sleep(random.uniform(0.5, 2.0))

    def fetch_and_save_data(self, ticker: str) -> bool:
        """Legacy single-ticker fetcher used by manual UI refresh."""
        logger.info(f"Processing Data for single ticker {ticker}...")
        try:
            persisted = False

            _daily = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
            df_daily = _daily.get(ticker, pd.DataFrame())
            if not df_daily.empty:
                self._strip_tz(df_daily)
                for col in ('Open', 'High', 'Low'):
                    mask = (df_daily[col] == 0) & (df_daily['Close'] > 0)
                    df_daily.loc[mask, col] = df_daily.loc[mask, 'Close']
                df_daily.to_parquet(HISTORICAL_DIR / f"{ticker}.parquet", engine='pyarrow')
                persisted = True

            _intraday = yahoo_engine.get_intraday([ticker], period="1d", interval="5m")
            df_intraday = _intraday.get(ticker, pd.DataFrame())
            if not df_intraday.empty:
                self._strip_tz(df_intraday)
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                persisted = True

            fundamentals = yahoo_engine.get_ticker_info(ticker) or {}
            if fundamentals:
                with open(FUNDAMENTALS_DIR / f"{ticker}.json", 'w') as f:
                    json.dump(fundamentals, f, default=str)

            if not persisted:
                logger.warning(f"No price data returned for {ticker} — nothing persisted.")
                return False

            return True
        except Exception as e:
            logger.error(f"Pipeline failed for {ticker}: {str(e)}")
            return False

    def update_all_data(self) -> None:
        self.fetch_market_baseline()

        tickers = self.get_all_tickers()
        logger.info(f"Target Acquisition: Found {len(tickers)} unique assets.")

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
            f"yfinance schema OK — SPY {len(df)} rows, "
            f"columns: {sorted(df.columns.tolist())}"
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