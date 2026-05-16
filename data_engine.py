# data_engine.py
import json
import logging
import yfinance as yf
import pandas as pd
from config import PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR, load_config

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DATA_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DataEngine:
    def __init__(self):
        self.portfolio = self._load_json(PORTFOLIO_PATH)
        self.watchlist = self._load_json(WATCHLIST_PATH)
        
    def _load_json(self, filepath):
        """Safely loads JSON files and logs Missing File errors gracefully."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Missing JSON file: {filepath}")
            return {}
            
    def get_all_tickers(self):
        """Extracts a unique set of tickers from both Portfolio and Watchlist, excluding ignored items."""
        tickers = set()
        
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

def fetch_market_baseline(self):
        """Downloads macroeconomic gravity indices and benchmarks for intermarket calculations."""
        logger.info("Fetching Market and Intermarket Baselines (US & UK)...")
        try:
            baselines = [
                ("^GSPC", "SP500_BASELINE"), 
                ("^FTSE", "FTSE_BASELINE"), 
                ("^TYX", "TYX_BASELINE"), 
                ("^TNX", "TNX_BASELINE"), 
                ("DX-Y.NYB", "DXY_BASELINE"),
                ("GBPUSD=X", "GBPUSD_BASELINE")
            ]
            for ticker, name in baselines:
                stock = yf.Ticker(ticker)
                df = stock.history(period="2y")
                if not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df.index = df.index.tz_localize(None)
                    df.to_parquet(HISTORICAL_DIR / f"{name}.parquet", engine='pyarrow')
            logger.info("All Market and Intermarket Baselines secured successfully.")
        except Exception as e:
            logger.error(f"Failed to fetch Market baselines: {e}")

    def fetch_and_save_data(self, ticker):
        """
        The Master Fetcher. Downloads three distinct data dimensions:
        1. 2-Year Daily OHLCV (For Macro Charts & MAs)
        2. 1-Day 5-Minute Intraday (For the live pulse chart)
        3. Fundamental Info (For Valuation, Profitability, and Sentiment)
        """
        logger.info(f"Processing Data for {ticker}...")
        try:
            stock = yf.Ticker(ticker)
            
            # 1. Fetch Macro Historical Data
            df_daily = stock.history(period="2y")
            if not df_daily.empty:
                df_daily.index = df_daily.index.tz_localize(None) 
                df_daily.to_parquet(HISTORICAL_DIR / f"{ticker}.parquet", engine='pyarrow')
                logger.info(f"Macro Data Saved for {ticker}.")
            else:
                logger.warning(f"No Macro data returned for {ticker}.")

            # 2. Fetch Intraday Data (5-minute intervals for today)
            df_intraday = stock.history(period="1d", interval="5m")
            if not df_intraday.empty:
                df_intraday.index = df_intraday.index.tz_localize(None)
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                logger.info(f"Intraday Data Saved for {ticker}.")

            # 3. Fetch Fundamental & Sentiment Data
            # yfinance returns a massive dictionary. We save it raw to process later.
            fundamentals = stock.info
            with open(FUNDAMENTALS_DIR / f"{ticker}.json", 'w') as f:
                json.dump(fundamentals, f)
            logger.info(f"Fundamentals & Sentiment Saved for {ticker}.")
            
            return True
            
        except Exception as e:
            logger.error(f"Pipeline failed for {ticker}: {str(e)}")
            return False

    def update_all_data(self):
        """Master function triggered by the system to update all core assets."""
        self.fetch_market_baseline()
        
        tickers = self.get_all_tickers()
        logger.info(f"Target Acquisition: Found {len(tickers)} unique assets.")
        
        for ticker in tickers:
            self.fetch_and_save_data(ticker)

if __name__ == "__main__":
    # Test block to execute the massive data pipeline directly
    engine = DataEngine()
    engine.update_all_data()