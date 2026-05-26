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
    "SERVER_URL": "http://localhost",
    "GHOSTFOLIO_URL": "",
    "API_TOKEN": "",
    "FRED_API_KEY": "",
    "YAHOO_IPV6_ADDRESS": "",
    "PORT": 8090,
    "BASE_CURRENCY": "GBP",
    "IGNORED_TICKERS": ["GBP", "USD", "EUR"],
    "GHOSTFOLIO_ACCOUNTS": {
        "discovered": [],
        "active": []
    },
    "NEXTCLOUD_URL": "",
    "BOT_USERNAME": "",
    "APP_PASSWORD": "",
    "CONVERSATION_TOKEN": "",
    "UI_PREFERENCES": {
        "LIVE_PORTFOLIO": False,
        "LIVE_WATCHLIST": False,
        "LIVE_DETAILS": False,
        "REFRESH_RATE": 60,
        "FREETRADE_ONLY_MODE": False
    },
    "POSITION_SIZING": {
        "ACCOUNT_VALUE": 500,
        "RISK_PCT": 1.0,
        "STOP_MULTIPLE": 2.0
    },
    "FREETRADE_MAPPINGS": {
        "US_MICS": ["XNAS", "XNYS", "ARCX", "BATS", "PINK"],
        "EXCHANGES": {
            "XLON": {"yf_suffix": ".L", "ui_name": "LSE"},
            "MUTUAL_FUND_EXCHANGE": {"yf_suffix": ".L", "ui_name": "UK Mutual Fund"}
        }
    },
    "SCHEDULING": {
        "SYNC_INDICES": {
            "ENABLED": False,
            "INDICES": ["SP500", "FTSE100"],
            "DAYS": ["sat"],
            "TIME": "03:00"
        },
        "PROFILER_ENGINE": {
            "ENABLED": False,
            "DAYS": ["sun"],
            "TIME": "05:00"
        },
        "GHOSTFOLIO_SYNC": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "INTERVAL_HOURS": 0,
            "TIME": "06:00"
        },
        "QUANT_ANALYSIS": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "INTERVAL_HOURS": 0,
            "TIME": "18:00"
        },
        "SENTIMENT_ENGINE": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "START_TIME": "09:30",
            "END_TIME": "16:00",
            "INTERVAL_HOURS": 4
        },
        "CRASH_ALERTS": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "START_TIME": "09:30",
            "END_TIME": "16:00",
            "INTERVAL_MINUTES": 10,
            "FLASH_CRASH_THRESHOLD": 3.0
        },
        "MOONSHOT_ALERTS": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "START_TIME": "09:30",
            "END_TIME": "16:00",
            "INTERVAL_MINUTES": 10
        },
        "MAINTENANCE": {
            "ENABLED": True,
            "DAY_OF_WEEK": "sun",
            "TIME": "02:00"
        },
        "FREETRADE_SYNC": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "TIME": "04:00"
        },
        "MACRO_ENGINE": {
            "ENABLED": True,
            "INITIALIZED": False,
            "CALENDAR_TIME": "04:00",
            "DATA_DAY": "sat",
            "DATA_TIME": "05:00"
        },
        "ML_BACKFILL": {
            "ENABLED": False,
            "DAYS": ["sat"],
            "TIME": "02:00"
        },
        "ML_TRAINING": {
            "ENABLED": True,
            "DAYS": ["sun"],
            "TIME": "04:00"
        },
        "ML_INFERENCE": {
            "ENABLED": True,
            "DAYS": ["mon", "tue", "wed", "thu", "fri"],
            "TIME": "01:30"
        }
    },
    "REPORTS_DEFAULTS": {
        "MR_MAX_RSI": 30,
        "DIV_MIN_YIELD": 2.0,
        "DIV_MIN_SCORE": 50
    },
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
        },
        "CRASH_ALERTS": {
            "DROP_PERCENT": 5.0,
            "DROP_DAYS": 3,
            "SMA_LENGTH": 10,
            "SMA_GAP_PERCENT": 2.0
        },
        "MOONSHOT_ALERTS": {
            "SPIKE_PERCENT": 5.0,
            "SPIKE_DAYS": 3,
            "SMA_LENGTH": 10,
            "SMA_GAP_PERCENT": 3.0
        }
    }
}

def load_config() -> dict:
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
                if key in ["NOTIFICATIONS", "SCHEDULING", "GHOSTFOLIO_ACCOUNTS", "UI_PREFERENCES", "FREETRADE_MAPPINGS", "POSITION_SIZING"] and isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        if sub_key in merged_config[key]:
                            if isinstance(sub_val, dict) and isinstance(merged_config[key][sub_key], dict):
                                merged_config[key][sub_key].update(sub_val)
                            else:
                                merged_config[key][sub_key] = sub_val
                        else:
                            merged_config[key][sub_key] = sub_val
                else:
                    merged_config[key] = val
                    
            return merged_config
    except Exception as e:
        print(f"[ERROR] Failed to read config.json: {e}. Using defaults.")
        return copy.deepcopy(DEFAULT_CONFIG)

# Load variables immediately on import
current_config = load_config()

SERVER_URL = current_config.get("SERVER_URL", "http://localhost")
GHOSTFOLIO_URL = current_config.get("GHOSTFOLIO_URL", "")
GHOSTFOLIO_TOKEN = current_config.get("API_TOKEN", "")
FRED_API_KEY = current_config.get("FRED_API_KEY", "")
YAHOO_IPV6_ADDRESS = current_config.get("YAHOO_IPV6_ADDRESS", "")
GHOSTFOLIO_ACCOUNTS = current_config.get("GHOSTFOLIO_ACCOUNTS", {"discovered": [], "active": []})
PORT = current_config.get("PORT", 8090)
BASE_CURRENCY = current_config.get("BASE_CURRENCY", "GBP")
IGNORED_TICKERS = current_config.get("IGNORED_TICKERS", [])
NEXTCLOUD_URL = current_config.get("NEXTCLOUD_URL", "")
BOT_USERNAME = current_config.get("BOT_USERNAME", "")
APP_PASSWORD = current_config.get("APP_PASSWORD", "")
CONVERSATION_TOKEN = current_config.get("CONVERSATION_TOKEN", "")
UI_PREFERENCES = current_config.get("UI_PREFERENCES", {})
NOTIFICATIONS = current_config.get("NOTIFICATIONS", {})
SCHEDULING = current_config.get("SCHEDULING", {})
REPORTS_DEFAULTS = current_config.get("REPORTS_DEFAULTS", DEFAULT_CONFIG["REPORTS_DEFAULTS"])