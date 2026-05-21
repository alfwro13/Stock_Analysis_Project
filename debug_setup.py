# debug_setup.py
import os
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DEBUG_SETUP - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_python_version():
    logger.info("--- Testing Python Version ---")
    if sys.version_info >= (3, 10):
        logger.info(f"✅ Python version {sys.version_info.major}.{sys.version_info.minor} is supported.")
    else:
        logger.warning(f"❌ Python version {sys.version_info.major}.{sys.version_info.minor} might be unsupported. 3.10+ recommended.")

def test_directories():
    logger.info("--- Testing Directory Structure ---")
    # Base directories that the app relies on
    dirs = [
        "data", 
        "data/historical", 
        "data/intraday", 
        "data/fundamentals", 
        "models", 
        "reports"
    ]
    all_exist = True
    for d in dirs:
        path = Path(d)
        if path.exists() and path.is_dir():
            logger.info(f"✅ Directory '{d}' exists.")
        else:
            logger.warning(f"⚠️ Directory '{d}' is missing. (It should be created automatically on first run)")
            all_exist = False
    return all_exist

def test_config():
    logger.info("--- Testing Configuration ---")
    try:
        from config import load_config
        config = load_config()
        logger.info("✅ Configuration loaded successfully.")
        
        # Check Ghostfolio
        gf_url = config.get("GHOSTFOLIO_URL")
        if gf_url:
            logger.info(f"ℹ️ Ghostfolio URL configured: {gf_url}")
        else:
            logger.info("⚠️ Ghostfolio URL is not configured.")

        # Check Nextcloud 
        nc_url = config.get("NEXTCLOUD_URL")
        if nc_url:
            logger.info(f"ℹ️ Nextcloud Talk URL configured: {nc_url}")
        else:
            logger.info("⚠️ Nextcloud Talk is not configured.")
            
        return config
    except ImportError as e:
        logger.error(f"❌ Failed to import config module. Are you in the project root? Error: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to load config: {e}")
        return None

def test_database():
    logger.info("--- Testing Database Connection & Schema ---")
    try:
        from database import get_connection, init_db
        # Run init_db to ensure schema is bootstrapped if it doesn't exist
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        
        # Check if tables were successfully created
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row['name'] for row in cursor.fetchall()]
        conn.close()
        
        if 'stock_signals' in tables and 'quant_signals' in tables:
            logger.info(f"✅ Database connection successful. Found {len(tables)} tables.")
        else:
            logger.error(f"❌ Database connected but missing core tables. Found: {tables}")
    except ImportError as e:
        logger.error(f"❌ Failed to import database module: {e}")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")

def test_yfinance():
    logger.info("--- Testing Yahoo Finance API ---")
    try:
        import yfinance as yf
        # Fetch SPY to test core connectivity
        spy = yf.Ticker("SPY")
        hist = spy.history(period="1d")
        if not hist.empty:
            logger.info("✅ Yahoo Finance API is reachable and returning data.")
        else:
            logger.error("❌ Yahoo Finance API returned an empty DataFrame. You might be rate-limited.")
    except Exception as e:
        logger.error(f"❌ Yahoo Finance API failed: {e}")

def run_all_tests():
    logger.info("=========================================")
    logger.info(" 🛠️ RUNNING PROJECT SETUP DIAGNOSTICS")
    logger.info("=========================================")
    
    test_python_version()
    print()
    test_directories()
    print()
    test_config()
    print()
    test_database()
    print()
    test_yfinance()
    
    logger.info("=========================================")
    logger.info(" DIAGNOSTICS COMPLETE")
    logger.info("=========================================")

if __name__ == "__main__":
    run_all_tests()