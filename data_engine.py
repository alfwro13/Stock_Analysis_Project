# data_engine.py
import json
import time
import random
import logging
from pathlib import Path
import yfinance as yf
import pandas as pd
from typing import Set, List, Dict, Any, Optional

from config import PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR, load_config
from gilt_engine import GiltDataService
from tools.network_engine import yahoo_connection_boundary

logger = logging.getLogger(__name__)

class DataEngine:
    def __init__(self) -> None:
        self.portfolio: Dict[str, Any] = self._load_json(PORTFOLIO_PATH)
        self.watchlist: Dict[str, Any] = self._load_json(WATCHLIST_PATH)
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

    @staticmethod
    def _persist_ticker_slice(
        df_bulk: pd.DataFrame,
        key: str,
        path: Path,
        dropna_subset: List[str],
        *,
        flat_single: bool = False,
    ) -> bool:
        """Extract one ticker's slice from a bulk frame, clean it, and write to parquet."""
        if isinstance(df_bulk.columns, pd.MultiIndex):
            if key not in df_bulk.columns.get_level_values(0):
                return False
            df = df_bulk[key].copy()
        elif flat_single:
            df = df_bulk.copy()
        else:
            return False

        present = [c for c in dropna_subset if c in df.columns]
        df.dropna(subset=present, inplace=True)
        if df.empty:
            return False

        DataEngine._strip_tz(df)
        df.to_parquet(path, engine='pyarrow')
        return True

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
        """Extracts a unique set of tickers from both Portfolio and Watchlist, excluding ignored items."""
        tickers: Set[str] = set()
        
        # Parse Portfolio (defensive against malformed non-dict entries)
        for _asset_key, asset_data in self.portfolio.items():
            if isinstance(asset_data, dict) and asset_data.get("ticker"):
                tickers.add(str(asset_data["ticker"]).strip().upper())

        # Parse Watchlist
        if isinstance(self.watchlist.get("watchlist"), list):
            for ticker in self.watchlist["watchlist"]:
                if ticker:
                    tickers.add(str(ticker).strip().upper())
                
        # Strip out ignored tickers
        config_data = load_config()
        ignored_tickers = config_data.get("IGNORED_TICKERS", [])
        
        valid_tickers = [t for t in tickers if t not in ignored_tickers]
        return sorted(valid_tickers)

    def fetch_market_baseline(self) -> None:
        """Downloads macroeconomic gravity indices and benchmarks for intermarket calculations."""
        logger.info("Fetching Market and Intermarket Baselines (US & UK)...")
        try:
            baselines = {
                "^GSPC": "SP500_BASELINE",
                "^FTSE": "FTSE_BASELINE",
                "^TYX": "TYX_BASELINE",
                "^TNX": "TNX_BASELINE",
                "DX-Y.NYB": "DXY_BASELINE",
                "GBPUSD=X": "GBPUSD_BASELINE"
            }

            baseline_tickers = list(baselines.keys())

            with yahoo_connection_boundary("Market Baselines") as session:
                df_bulk = yf.download(
                    baseline_tickers, period="2y", interval="1d",
                    group_by='ticker', auto_adjust=True, progress=False, session=session
                )

                if df_bulk.empty:
                    logger.warning("Baseline bulk download returned empty.")
                else:
                    for ticker, name in baselines.items():
                        self._persist_ticker_slice(
                            df_bulk, ticker, HISTORICAL_DIR / f"{name}.parquet", ['Close']
                        )
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
        with yahoo_connection_boundary("Bulk Historical Download") as session:
            try:
                df_bulk = yf.download(
                    tickers, period="2y", interval="1d", 
                    group_by='ticker', auto_adjust=True, progress=False, session=session
                )
                
                if df_bulk.empty:
                    logger.warning("Historical bulk download returned empty.")
                    return
                    
                is_single = len(tickers) == 1
                for ticker in tickers:
                    self._persist_ticker_slice(
                        df_bulk, ticker, HISTORICAL_DIR / f"{ticker}.parquet",
                        ['Close', 'Volume'], flat_single=is_single,
                    )
            except Exception as e:
                logger.error(f"Fatal error during bulk historical download: {e}")

    def bulk_download_intraday(self, tickers: List[str]) -> None:
        """Vectorized bulk download of 1-day 5-minute intraday prices."""
        if not tickers:
            return
            
        logger.info(f"Bulk downloading 1D Intraday data for {len(tickers)} assets...")
        with yahoo_connection_boundary("Bulk Intraday Download") as session:
            try:
                df_bulk = yf.download(
                    tickers, period="1d", interval="5m", 
                    group_by='ticker', auto_adjust=True, progress=False, session=session
                )
                
                if df_bulk.empty:
                    return
                    
                is_single = len(tickers) == 1
                for ticker in tickers:
                    self._persist_ticker_slice(
                        df_bulk, ticker, INTRADAY_DIR / f"{ticker}_intraday.parquet",
                        ['Close'], flat_single=is_single,
                    )
            except Exception as e:
                logger.error(f"Fatal error during bulk intraday download: {e}")

    def drip_feed_fundamentals(self, tickers: List[str]) -> None:
        """
        Slow, randomized drip-feed loop to fetch the raw .info JSON payloads.
        Mitigates strict JSON-endpoint rate-limiting.
        """
        logger.info(f"Drip-feeding Fundamental JSONs for {len(tickers)} assets...")
        with yahoo_connection_boundary("Fundamentals Drip Feed") as session:
            for i, ticker in enumerate(tickers):
                try:
                    stock = yf.Ticker(ticker, session=session)
                    fundamentals = stock.info
                    
                    if fundamentals:
                        with open(FUNDAMENTALS_DIR / f"{ticker}.json", 'w') as f:
                            json.dump(fundamentals, f, default=str)
                    
                    # Log heartbeat occasionally
                    if i > 0 and i % 50 == 0:
                        logger.info(f"Fundamentals progress: {i}/{len(tickers)}...")
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch fundamentals for {ticker}: {e}")
                finally:
                    # Institutional Anti-Bot Randomization
                    time.sleep(random.uniform(0.5, 2.0))

    def fetch_and_save_data(self, ticker: str) -> bool:
        """
        Legacy single-ticker master fetcher (used by single-UI refreshes).
        Wrapped safely in the new Session boundary.
        """
        logger.info(f"Processing Data for single ticker {ticker}...")
        with yahoo_connection_boundary(f"Single Ticker Refresh: {ticker}") as session:
            try:
                stock = yf.Ticker(ticker, session=session)
                
                persisted = False

                # 1. Fetch Macro Historical Data
                df_daily = stock.history(period="2y")
                if not df_daily.empty:
                    self._strip_tz(df_daily)
                    df_daily.to_parquet(HISTORICAL_DIR / f"{ticker}.parquet", engine='pyarrow')
                    persisted = True

                # 2. Fetch Intraday Data
                df_intraday = stock.history(period="1d", interval="5m")
                if not df_intraday.empty:
                    self._strip_tz(df_intraday)
                    df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                    persisted = True

                # 3. Fetch Fundamental Data
                fundamentals = stock.info
                with open(FUNDAMENTALS_DIR / f"{ticker}.json", 'w') as f:
                    json.dump(fundamentals, f, default=str)

                if not persisted:
                    logger.warning(f"No price data returned by yfinance for {ticker} — nothing persisted.")
                    return False

                return True
            except Exception as e:
                logger.error(f"Pipeline failed for {ticker}: {str(e)}")
                return False

    def update_all_data(self) -> None:
        """Master function triggered by the system to update all core assets using Institutional Bulk Logic."""
        self.fetch_market_baseline()
        
        tickers = self.get_all_tickers()
        logger.info(f"Target Acquisition: Found {len(tickers)} unique assets.")
        
        if not tickers:
            return
            
        self.bulk_download_historical(tickers)
        self.bulk_download_intraday(tickers)
        self.drip_feed_fundamentals(tickers)
        
        logger.info("Massive data pipeline ingestion completed successfully.")

if __name__ == "__main__":
    # Test block to execute the massive data pipeline directly
    engine = DataEngine()
    engine.update_all_data()