# config.py
import os
import json
from pathlib import Path

# Dynamically resolve the absolute path to the directory containing this file
BASE_DIR = Path(__file__).resolve().parent

# Define core directories
DATA_DIR = BASE_DIR / "data"

# Sub-directories for organized data storage
HISTORICAL_DIR = DATA_DIR / "historical"       
INTRADAY_DIR = DATA_DIR / "intraday"           
FUNDAMENTALS_DIR = DATA_DIR / "fundamentals"   

# Define specific file paths
DB_PATH = DATA_DIR / "analysis.db"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
SECRETS_PATH = BASE_DIR / "config.json"

# Automatically create the required directories if they do not exist
HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)

# Application Default Variables
PORT = 8090

# Load secure credentials from config.json
GHOSTFOLIO_URL = ""
GHOSTFOLIO_TOKEN = ""

if SECRETS_PATH.exists():
    with open(SECRETS_PATH, 'r') as f:
        try:
            secrets = json.load(f)
            GHOSTFOLIO_URL = secrets.get("GHOSTFOLIO_URL", "")
            GHOSTFOLIO_TOKEN = secrets.get("API_TOKEN", "")
            # If you ever want to change the port via your config.json, it will override the default here
            PORT = secrets.get("PORT", 8090)
        except json.JSONDecodeError:
            print("[WARNING] config.json is not formatted correctly.")
else:
    print("[WARNING] config.json not found. Ghostfolio sync will be disabled.")