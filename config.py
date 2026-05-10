# config.py
import os
import json
import copy
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
BASE_CURRENCY = "GBP"

# Base Schema for Application Configuration
DEFAULT_CONFIG = {
    "GHOSTFOLIO_URL": "",
    "API_TOKEN": "",
    "PORT": 8090,
    "BASE_CURRENCY": "GBP",
    "NEXTCLOUD_URL": "",
    "BOT_USERNAME": "",
    "APP_PASSWORD": "",
    "CONVERSATION_TOKEN": "",
    "NOTIFICATIONS": {
        "MARKET_SENTIMENT": {
            "ENABLED": False,
            "TIME": "09:30",
            "FREQUENCY": "mon-fri"
        },
        "EARNINGS_ALERTS": {
            "ENABLED": False,
            "TIME": "08:00",
            "DAYS_AHEAD": 7,
            "ALERT_TYPE": "daily"
        },
        "INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": False,
            "ENABLED_WATCHLIST": False,
            "TIME": "18:00",
            "FREQUENCY": "mon-fri",
            "MIN_VALUE": 50000,
            "DAYS_BACK": 7
        }
    }
}

def load_config():
    """Loads config.json into memory safely using Deep Copy merging."""
    if not SECRETS_PATH.exists():
        print("[INFO] config.json not found. Generating default template...")
        with open(SECRETS_PATH, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(SECRETS_PATH, 'r') as f:
            data = json.load(f)
            
            # Use Deep Copy so we don't mutate the global defaults dictionary
            merged_config = copy.deepcopy(DEFAULT_CONFIG)
            
            # Safely merge nested dictionaries without deleting missing keys
            for key, val in data.items():
                if key == "NOTIFICATIONS" and isinstance(val, dict):
                    for notif_key, notif_val in val.items():
                        if notif_key in merged_config["NOTIFICATIONS"]:
                            merged_config["NOTIFICATIONS"][notif_key].update(notif_val)
                        else:
                            merged_config["NOTIFICATIONS"][notif_key] = notif_val
                else:
                    merged_config[key] = val
                    
            return merged_config
    except Exception as e:
        print(f"[ERROR] Failed to read config.json: {e}. Using defaults.")
        return copy.deepcopy(DEFAULT_CONFIG)

# Load variables immediately on import
current_config = load_config()

GHOSTFOLIO_URL = current_config.get("GHOSTFOLIO_URL", "")
GHOSTFOLIO_TOKEN = current_config.get("API_TOKEN", "")
PORT = current_config.get("PORT", 8090)
BASE_CURRENCY = current_config.get("BASE_CURRENCY", "GBP")
NEXTCLOUD_URL = current_config.get("NEXTCLOUD_URL", "")
BOT_USERNAME = current_config.get("BOT_USERNAME", "")
APP_PASSWORD = current_config.get("APP_PASSWORD", "")
CONVERSATION_TOKEN = current_config.get("CONVERSATION_TOKEN", "")
NOTIFICATIONS = current_config.get("NOTIFICATIONS", {})