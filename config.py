# config.py
import os
import json
import copy
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Auto-provision default dashboard credentials into .env if not already set
def _provision_default_credentials():
    import secrets as _secrets
    env_path = BASE_DIR / ".env"
    changed = False
    for key, default in [("DASHBOARD_USERNAME", "admin"), ("DASHBOARD_PASSWORD", "changeme")]:
        if not os.environ.get(key):
            from dotenv import set_key
            set_key(str(env_path), key, default)
            os.environ[key] = default
            changed = True
    if not os.environ.get("ADMIN_CONFIRM_TOKEN"):
        from dotenv import set_key
        token = _secrets.token_hex(16)
        set_key(str(env_path), "ADMIN_CONFIRM_TOKEN", token)
        os.environ["ADMIN_CONFIRM_TOKEN"] = token
        changed = True
    if changed:
        print("[INFO] Default dashboard credentials written to .env. Login: admin / changeme")

_provision_default_credentials()

DATA_DIR = BASE_DIR / "data"

HISTORICAL_DIR = DATA_DIR / "historical"
INTRADAY_DIR = DATA_DIR / "intraday"
FUNDAMENTALS_DIR = DATA_DIR / "fundamentals"
FORENSIC_DIR = DATA_DIR / "fundamentals" / "quarterly"
ANOMALY_MODELS_DIR = DATA_DIR / "anomaly_models"

DB_PATH = DATA_DIR / "analysis.db"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
SECRETS_PATH = BASE_DIR / "config.json"

HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
FORENSIC_DIR.mkdir(parents=True, exist_ok=True)
ANOMALY_MODELS_DIR.mkdir(parents=True, exist_ok=True)

PORT = 8090
BASE_CURRENCY = "GBP"

# Keys that must never be written back to config.json — sourced from .env only
SENSITIVE_KEYS: set = {"API_TOKEN", "APP_PASSWORD", "NEXTCLOUD_URL", "BOT_USERNAME", "CONVERSATION_TOKEN", "GHOSTFOLIO_URL", "FRED_API_KEY"}

# Backward compat: scheduling keys removed in later releases; silently stripped on load.
DEPRECATED_SCHEDULE_KEYS: set = {"UNIVERSE_FUNDAMENTALS"}

DEFAULT_CONFIG = {
    "SERVER_URL": "http://localhost",
    "FORCE_PASSWORD_RESET": False,
    "YAHOO_IPV6_ADDRESS": "",
    "YAHOO_USE_IPV4": True,
    "YAHOO_USE_IPV6": False,
    "PORT": 8090,
    "BASE_CURRENCY": "GBP",
    "USER_TIMEZONE": "Europe/London",   # IANA tz string — used for all display formatting
    "HOME_EXCHANGE": "LSE",             # NYSE | LSE | XETRA | TSE — drives default market-window logic
    "IGNORED_TICKERS": ["GBP", "USD", "EUR"],
    "ACCOUNT_CURRENCIES": ["GBP", "GBp", "USD", "EUR"],
    "GHOSTFOLIO_ENABLED": False,
    "GHOSTFOLIO_ACCOUNTS": {
        "discovered": [],
        "active": []
    },
    "UI_PREFERENCES": {
        "LIVE_PORTFOLIO": True,
        "LIVE_WATCHLIST": True,
        "LIVE_DETAILS": True,
        "REFRESH_RATE": 60,
        "FREETRADE_ONLY_MODE": True,
        "MARKET_PULSE_DYNAMIC": False,
        "MARKET_PULSE_DESKTOP_COUNT": 10,
        "MARKET_PULSE_MOBILE_COUNT": 8,
        "GLOSSARY_LEARN_UNLOCK_ALL": False,
        "GLOSSARY_LEARN_STUDY_ALL": False,
        "PORTFOLIO_HIDDEN_CORE_COLUMNS": [],
        "PORTFOLIO_SHOWN_OPTIONAL_COLUMNS": [],
        "WATCHLIST_HIDDEN_CORE_COLUMNS": [],
        "WATCHLIST_SHOWN_OPTIONAL_COLUMNS": [],
        "PORTFOLIO_VIEWS": [],
        "WATCHLIST_VIEWS": [],
        "FONT_SIZE_NAV": 12,
        "FONT_SIZE_TABLE": 12,
        "FONT_SIZE_DT_TABLE": 12,
        "FONT_SIZE_FORM": 12,
        "FONT_SIZE_BTN": 12,
        "FONT_SIZE_SECTION": 13,
        "FONT_SIZE_BODY": 12,
        "FONT_SIZE_H1": 17,
        "FONT_SIZE_H2": 14,
        "FONT_SIZE_H3": 12
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
            "ENABLED": True,
            "INDICES": ["SP500", "FTSE100"],
            "DAYS": ["sat"],
            "TIME": "03:00"
        },
        "PROFILER_ENGINE": {
            "ENABLED": False,
            "DAYS": ["sun"],
            "TIME": "05:00",
            "BATCH_SIZE": 250
        },
        "UNIVERSE_DEEP_SYNC": {
            "ENABLED": False,
            "DAYS": ["sun"],
            "TIME": "02:00"
        },
        "GHOSTFOLIO_SYNC": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "INTERVAL_HOURS": 0,
            "TIME": "06:00"
        },
        "QUANT_ANALYSIS": {
            "ENABLED": True,
            "FREQUENCY": "mon-fri",
            "INTERVAL_HOURS": 0,
            "TIME": "18:00"
        },
        "SENTIMENT_ENGINE": {
            "ENABLED": True,
            "FREQUENCY": "mon-fri",
            "START_TIME": "08:00",   # UTC — covers LSE open; set "" to derive from HOME_EXCHANGE
            "END_TIME": "21:00",     # UTC — covers NYSE close
            "INTERVAL_HOURS": 4
        },
        "CRASH_ALERTS": {
            "ENABLED": True,
            "FREQUENCY": "mon-fri",
            "START_TIME": "08:00",   # UTC — covers LSE open; set "" to derive from HOME_EXCHANGE
            "END_TIME": "21:00",     # UTC — covers NYSE close
            "INTERVAL_MINUTES": 10,
            "FLASH_CRASH_THRESHOLD": 3.0
        },
        "MOONSHOT_ALERTS": {
            "ENABLED": True,
            "FREQUENCY": "mon-fri",
            "START_TIME": "08:00",   # UTC — covers LSE open; set "" to derive from HOME_EXCHANGE
            "END_TIME": "21:00",     # UTC — covers NYSE close
            "INTERVAL_MINUTES": 10
        },
        "MAINTENANCE": {
            "ENABLED": True,
            "DAY_OF_WEEK": "sun",
            "TIME": "02:00",
            "DAYS_TO_KEEP_FILES": 60
        },
        "ACCOUNT_VALUE_SNAPSHOT": {
            "ENABLED": True,
            "TIME": "01:30"
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
        "CB_NLP_ALERT": {
            "ENABLED": True,
            "FREQUENCY": "mon-fri",
            "START_TIME": "12:00",
            "END_TIME": "21:00",
            "INTERVAL_MINUTES": 30
        },
        "ML_BACKFILL": {
            "ENABLED": True,
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
        },
        "SYSTEM_CHECK": {
            "ENABLED": True,
            "DAYS": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "TIME": "06:00"
        },
        "AI_CONTAGION": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "START_TIME": "09:00",
            "END_TIME": "21:00",
            "INTERVAL_MINUTES": 15
        },
        "NEWS_FEED": {
            "ENABLED": False,
            "FREQUENCY": "mon-fri",
            "INTERVAL_HOURS": 4,
            "START_TIME": "08:00",
            "END_TIME": "20:00",
            "MAX_PER_TICKER": 5,
            "MAX_AGE_DAYS": 7
        },
        "TRAP_MONITORS": {
            "ENABLED": False,
            "BULL_TRAP": True,
            "BEAR_TRAP": True,
            "CAPITULATION": True,
            "WYCKOFF": True,
            "MONITOR_PORTFOLIO": True,
            "MONITOR_WATCHLIST": False,
            "FREQUENCY": "mon-fri",
            "START_TIME": "08:00",
            "END_TIME": "21:00",
            "INTERVAL_MINUTES": 30
        },
        "BUBBLE_RADAR": {
            "ENABLED": False,
            "DAYS": ["mon", "tue", "wed", "thu", "fri"],
            "TIME": "19:30",
            "WATCH_THRESHOLD": 70,
            "FLAG_THRESHOLD": 85
        },
        "ALERT_REFEREE_TRAINING": {
            "ENABLED": False,
            "DAYS": ["sun"],
            "TIME": "05:00",
            "MODE": "shadow",
            "VETO_THRESHOLD": 0.3,
            "MIN_TRAINING_SAMPLES": 200
        },
        "HEAD_SHOULDERS": {
            "ENABLED": False,
            "REGULAR_ENABLED": True,
            "INVERSE_ENABLED": True,
            "MONITOR_PORTFOLIO": True,
            "MONITOR_WATCHLIST": False,
            "DAYS": ["mon", "tue", "wed", "thu", "fri"],
            "TIME": "22:20"
        },
        "FORENSIC_QUARTERLY_FETCH": {
            "ENABLED": True,
            "DAY_OF_MONTH": 1,
            "TIME": "06:00"
        },
        "FORENSIC_SCORES": {
            "ENABLED": True,
            "DAY_OF_MONTH": 1,
            "TIME": "07:00"
        },
        "BACKUP": {
            "ENABLED": False,
            "LOCATION": "local",
            "LOCAL_PATH": "backups",
            "NFS_SERVER": "",
            "NFS_PATH": "",
            "INCLUDE_DATA": True,
            "INCLUDE_MODELS": True,
            "INCLUDE_DATABASE": True,
            "DAYS": ["sun"],
            "TIME": "03:30",
            "RETENTION_COUNT": 7
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
            "SMA_GAP_PERCENT": 2.0,
            "COOLDOWN_MINUTES": 120,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0
        },
        "MOONSHOT_ALERTS": {
            "SPIKE_PERCENT": 5.0,
            "SPIKE_DAYS": 3,
            "SMA_LENGTH": 10,
            "SMA_GAP_PERCENT": 3.0,
            "COOLDOWN_MINUTES": 120,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0
        },
        "MACRO_ALERTS": {
            "COOLDOWN_MINUTES": 120,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0
        },
        "ANOMALY_ALERTS": {
            "ENABLED": True,
            "THRESHOLD": 0.7,
            "COOLDOWN_MINUTES": 120,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0
        },
        "RSS_FEED": {
            "ENABLED": False
        },
        "AI_CONTAGION": {
            "ENABLED": False,
            "LEADER_THRESHOLD_PCT": 4.0,
            "ETF_CONFIRMATION_THRESHOLD_PCT": 2.5,
            "VOLUME_SPIKE_MULTIPLIER": 1.8,
            "BELLWETHER_TICKERS": ["NVDA", "AMD", "MSFT", "META", "GOOGL", "AAPL", "AVGO"],
            "ETF_BASKET": ["SMH", "SOXX", "QQQ"],
            "MAX_ALERTS_PER_DAY": 1
        },
        "TRAP_MONITOR_ALERTS": {
            "COOLDOWN_MINUTES": 120,
            "RETRIGGER_PERCENT": 3.0,
            "REARM_PERCENT": 5.0,
            "BULL_TRAP_VOLUME_RATIO": 0.75,
            "BEAR_TRAP_VOLUME_RATIO": 1.20,
            "CAPITULATION_VOL_ZSCORE": 3.0,
            "WYCKOFF_BB_SQUEEZE_PCT": 2.0,
            "PROXY_TICKERS": ["QQQ", "SMH", "NVDA", "MSFT", "AAPL"]
        },
        "HEAD_SHOULDERS_ALERTS": {
            "COOLDOWN_MINUTES": 120,
            "RETRIGGER_PERCENT": 3.0,
            "REARM_PERCENT": 5.0,
            "PRIOR_TREND_MIN_PCT": 8.0,
            "VOLUME_CONFIRM_MULTIPLIER": 1.5
        },
        "MARKET_STRESS_ALERTS": {
            "COOLDOWN_MINUTES": 1440
        }
    },
    "NOTIFICATION_ROUTING": {},
    "XRAY_TARGETS": {
        "market_development": {
            "Developed Markets": {"min": 80.0, "max": 95.0},
            "Emerging Markets":  {"min": 5.0,  "max": 20.0}
        },
        "regional_clusters": {
            "North America":    {"min": 55.0, "max": 75.0},
            "Europe":           {"min": 12.0, "max": 22.0},
            "Japan":            {"min": 3.0,  "max": 8.0},
            "Asia-Pacific":     {"min": 2.0,  "max": 8.0},
            "Emerging Markets": {"min": 5.0,  "max": 18.0}
        },
        "country_concentration": {
            "United States":  {"min": None, "max": 70.0},
            "China":          {"min": None, "max": 15.0},
            "Japan":          {"min": None, "max": 10.0},
            "United Kingdom": {"min": None, "max": 10.0}
        },
        "sector_targets": {
            "Technology":             {"min": None, "max": 35.0},
            "Financials":             {"min": None, "max": 25.0},
            "Healthcare":             {"min": None, "max": 20.0},
            "Consumer Cyclical":      {"min": None, "max": 20.0},
            "Industrials":            {"min": None, "max": 20.0},
            "Communication Services": {"min": None, "max": 15.0},
            "Consumer Staples":       {"min": None, "max": 15.0},
            "Energy":                 {"min": None, "max": 10.0},
            "Materials":              {"min": None, "max": 10.0},
            "Utilities":              {"min": None, "max": 8.0},
            "Real Estate":            {"min": None, "max": 8.0}
        },
        "asset_class_targets": {
            "ETF":          {"min": 40.0, "max": None},
            "Equity":       {"min": None, "max": 40.0},
            "Fixed Income": {"min": None, "max": 30.0},
            "Commodity":    {"min": None, "max": 10.0}
        },
        "concentration_targets": {
            "max_single_position_pct": 15.0,
            "top5_weight_max_pct": 50.0,
            "top10_weight_max_pct": 70.0,
            "hhi_max": 0.15
        },
        "risk_metric_targets": {
            "portfolio_beta_min": 0.6,
            "portfolio_beta_max": 1.4,
            "annualized_vol_max_pct": 20.0,
            "sharpe_ratio_min": 0.5,
            "max_drawdown_max_pct": 30.0,
            "avg_correlation_max": 0.75
        },
        "income_targets": {
            "dividend_yield_min_pct": 1.5
        }
    },
    "REGIME_TARGETS": {
        "Risk-On":     {"equities": [65.0, 80.0], "bonds": [5.0, 20.0],  "commodities": [0.0, 10.0], "cash": [0.0, 10.0]},
        "Late Cycle":  {"equities": [50.0, 65.0], "bonds": [20.0, 35.0], "commodities": [5.0, 15.0], "cash": [5.0, 15.0]},
        "Stagflation": {"equities": [30.0, 45.0], "bonds": [10.0, 25.0], "commodities": [15.0, 25.0], "cash": [15.0, 25.0]},
        "Contraction": {"equities": [20.0, 35.0], "bonds": [40.0, 55.0], "commodities": [0.0, 10.0],  "cash": [20.0, 30.0]},
        "Recovery":    {"equities": [55.0, 70.0], "bonds": [15.0, 30.0], "commodities": [5.0, 15.0],  "cash": [5.0, 15.0]}
    },
    "FILE_LOGGING": {
        "ENABLED": False,
        "LEVEL": "INFO",
        "DAYS_TO_KEEP": 30,
        "ARCHIVE": False,
        "LOG_DIR": "logs"
    }
}

def load_config() -> dict:
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
            for key, val in data.items():
                if key in ["NOTIFICATIONS", "SCHEDULING", "GHOSTFOLIO_ACCOUNTS", "UI_PREFERENCES", "FREETRADE_MAPPINGS", "POSITION_SIZING", "XRAY_TARGETS", "REGIME_TARGETS", "FILE_LOGGING", "REPORTS_DEFAULTS", "NOTIFICATION_ROUTING"] and isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        # Silently drop keys removed in later releases (backward compat).
                        if key == "SCHEDULING" and sub_key in DEPRECATED_SCHEDULE_KEYS:
                            continue
                        if sub_key in merged_config[key]:
                            if isinstance(sub_val, dict) and isinstance(merged_config[key][sub_key], dict):
                                merged_config[key][sub_key].update(sub_val)
                            else:
                                merged_config[key][sub_key] = sub_val
                        else:
                            merged_config[key][sub_key] = sub_val
                else:
                    merged_config[key] = val

            # One-time migration: "09:30"/"16:00" were ET times stored as UTC; widen to cover LSE+NYSE.
            _ALERT_KEYS = ("SENTIMENT_ENGINE", "CRASH_ALERTS", "MOONSHOT_ALERTS")
            for _ak in _ALERT_KEYS:
                _blk = merged_config.get("SCHEDULING", {}).get(_ak, {})
                if _blk.get("START_TIME") == "09:30":
                    _blk["START_TIME"] = "08:00"
                if _blk.get("END_TIME") == "16:00":
                    _blk["END_TIME"] = "21:00"

            return merged_config
    except Exception as e:
        print(f"[ERROR] Failed to read config.json: {e}. Using defaults.")
        return copy.deepcopy(DEFAULT_CONFIG)

current_config = load_config()

# Sensitive values: env vars take precedence over anything in config.json
SERVER_URL = current_config.get("SERVER_URL", "http://localhost")
GHOSTFOLIO_URL = os.environ.get("GHOSTFOLIO_URL") or current_config.get("GHOSTFOLIO_URL", "")
GHOSTFOLIO_TOKEN = os.environ.get("GHOSTFOLIO_TOKEN") or current_config.get("API_TOKEN", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY") or current_config.get("FRED_API_KEY", "")
NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL") or current_config.get("NEXTCLOUD_URL", "")
BOT_USERNAME = os.environ.get("NEXTCLOUD_BOT_USERNAME") or current_config.get("BOT_USERNAME", "")
APP_PASSWORD = os.environ.get("NEXTCLOUD_APP_PASSWORD") or current_config.get("APP_PASSWORD", "")
CONVERSATION_TOKEN = os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN") or current_config.get("CONVERSATION_TOKEN", "")
APP_SECRET_KEY = os.environ.get("APP_SECRET_KEY", "")

YAHOO_IPV6_ADDRESS = current_config.get("YAHOO_IPV6_ADDRESS", "")
GHOSTFOLIO_ACCOUNTS = current_config.get("GHOSTFOLIO_ACCOUNTS", {"discovered": [], "active": []})
PORT = current_config.get("PORT", 8090)
BASE_CURRENCY = current_config.get("BASE_CURRENCY", "GBP")
USER_TIMEZONE = current_config.get("USER_TIMEZONE", "Europe/London")
HOME_EXCHANGE  = current_config.get("HOME_EXCHANGE", "LSE")
IGNORED_TICKERS = current_config.get("IGNORED_TICKERS", [])
ACCOUNT_CURRENCIES = current_config.get("ACCOUNT_CURRENCIES", DEFAULT_CONFIG["ACCOUNT_CURRENCIES"])
UI_PREFERENCES = current_config.get("UI_PREFERENCES", {})
NOTIFICATIONS = current_config.get("NOTIFICATIONS", {})
SCHEDULING = current_config.get("SCHEDULING", {})
REPORTS_DEFAULTS = current_config.get("REPORTS_DEFAULTS", DEFAULT_CONFIG["REPORTS_DEFAULTS"])


def update_config_atomic(new_data: dict) -> None:
    # Strip sensitive keys — they live in .env, not config.json
    new_data = {k: v for k, v in new_data.items() if k not in SENSITIVE_KEYS}
    tmp_path = SECRETS_PATH.with_suffix('.tmp')
    try:
        current = {k: v for k, v in load_config().items() if k not in SENSITIVE_KEYS}

        def deep_merge(d, u):
            for k, v in u.items():
                if k in d and isinstance(d[k], dict) and isinstance(v, dict):
                    deep_merge(d[k], v)
                else:
                    d[k] = v
            return d

        merged = deep_merge(current, new_data)

        with open(tmp_path, 'w') as f:
            json.dump(merged, f, indent=4)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, SECRETS_PATH)

    except Exception as e:
        print(f"[ERROR] Atomic config write failed: {e}")
        if tmp_path.exists():
            tmp_path.unlink()