# data_engine.py
import json
import time
import random
import logging
import yfinance as yf
import pandas as pd
from typing import Set, List

from config import PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR, load_config
from gilt_engine import GiltDataService
from network_engine import yahoo_connection_boundary

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DATA_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DataEngine:
    def __init__(self) -> None:
        self.portfolio = self._load_json(PORTFOLIO_PATH)
        self.watchlist = self._load_json(WATCHLIST_PATH)
        
    def _load_json(self, filepath: str) -> dict:
        """Safely loads JSON files and logs Missing File errors gracefully."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Missing JSON file: {filepath}")
            return {}
            
    def get_all_tickers(self) -> List[str]:
        """Extracts a unique set of tickers from both Portfolio and Watchlist, excluding ignored items."""
        tickers: Set[str] = set()
        
        # Parse Portfolio
        for asset_key, asset_data in self.portfolio.items():
            if "ticker" in asset_data:
                tickers.add(asset_data["ticker"])
        
        # Parse Watchlist
        if "watchlist" in self.watchlist:
            for ticker in self.watchlist["watchlist"]:
                tickers.add(ticker)
                
        # Strip out ignored tickers
        config_data = load_config()
        ignored_tickers = config_data.get("IGNORED_TICKERS", [])
        
        valid_tickers = [t for t in tickers if t not in ignored_tickers]
        return valid_tickers

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
                    return
                    
                for ticker, name in baselines.items():
                    if isinstance(df_bulk.columns, pd.MultiIndex):
                        if ticker not in df_bulk.columns.get_level_values(0):
                            continue
                        df = df_bulk[ticker].copy()
                    else:
                        if len(baseline_tickers) == 1:
                            df = df_bulk.copy()
                        else:
                            continue
                            
                    df.dropna(subset=['Close'], inplace=True)
                    if not df.empty:
                        df.index = df.index.tz_localize(None)
                        df.to_parquet(HISTORICAL_DIR / f"{name}.parquet", engine='pyarrow')
                        
            logger.info("All Market and Intermarket Baselines secured successfully.")
            
            # Call our custom FT scraper for the UK Gilt
            GiltDataService().sync_gilt_data()
            
        except Exception as e:
            logger.error(f"Failed to fetch Market baselines: {e}")

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
                    
                for ticker in tickers:
                    if isinstance(df_bulk.columns, pd.MultiIndex):
                        if ticker not in df_bulk.columns.get_level_values(0):
                            continue
                        df_ticker = df_bulk[ticker].copy()
                    else:
                        df_ticker = df_bulk.copy() if len(tickers) == 1 else pd.DataFrame()
                        
                    df_ticker.dropna(subset=['Close', 'Volume'], inplace=True)
                    if not df_ticker.empty:
                        df_ticker.index = df_ticker.index.tz_localize(None)
                        df_ticker.to_parquet(HISTORICAL_DIR / f"{ticker}.parquet", engine='pyarrow')
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
                    
                for ticker in tickers:
                    if isinstance(df_bulk.columns, pd.MultiIndex):
                        if ticker not in df_bulk.columns.get_level_values(0):
                            continue
                        df_ticker = df_bulk[ticker].copy()
                    else:
                        df_ticker = df_bulk.copy() if len(tickers) == 1 else pd.DataFrame()
                        
                    df_ticker.dropna(subset=['Close'], inplace=True)
                    if not df_ticker.empty:
                        df_ticker.index = df_ticker.index.tz_localize(None)
                        df_ticker.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
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
                            json.dump(fundamentals, f)
                    
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
                
                # 1. Fetch Macro Historical Data
                df_daily = stock.history(period="2y")
                if not df_daily.empty:
                    df_daily.index = df_daily.index.tz_localize(None) 
                    df_daily.to_parquet(HISTORICAL_DIR / f"{ticker}.parquet", engine='pyarrow')

                # 2. Fetch Intraday Data
                df_intraday = stock.history(period="1d", interval="5m")
                if not df_intraday.empty:
                    df_intraday.index = df_intraday.index.tz_localize(None)
                    df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')

                # 3. Fetch Fundamental Data
                fundamentals = stock.info
                with open(FUNDAMENTALS_DIR / f"{ticker}.json", 'w') as f:
                    json.dump(fundamentals, f)
                
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