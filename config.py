# config.py
import os
from pathlib import Path

# Dynamically resolve the absolute path to the directory containing this file
BASE_DIR = Path(__file__).resolve().parent

# Define core directories
DATA_DIR = BASE_DIR / "data"

# Sub-directories for organized data storage
HISTORICAL_DIR = DATA_DIR / "historical"       # 2-year daily data (Parquet)
INTRADAY_DIR = DATA_DIR / "intraday"           # 1-day 5-minute data (Parquet)
FUNDAMENTALS_DIR = DATA_DIR / "fundamentals"   # Yahoo Finance .info data (JSON)

# Define specific file paths
DB_PATH = DATA_DIR / "analysis.db"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"

# Automatically create the required directories if they do not exist
# exist_ok=True ensures the system doesn't crash if the folder is already there
HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)