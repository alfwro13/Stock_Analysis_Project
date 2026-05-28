import sys
import logging
from config import load_config
from database import get_connection, get_universe_tickers
from profile_engine import count_pending_profiles

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def print_header(title: str):
    logger.info("\n" + "="*50)
    logger.info(f" {title.upper()}")
    logger.info("="*50)

def check_configuration():
    print_header("Checking System Configuration")
    config = load_config()
    
    ui_prefs = config.get("UI_PREFERENCES", {})
    ft_mode = ui_prefs.get("FREETRADE_ONLY_MODE", "NOT_FOUND")
    logger.info(f"[*] Freetrade Only Mode   : {ft_mode}")
    
    sched = config.get("SCHEDULING", {})
    
    sync_ind = sched.get("SYNC_INDICES", "NOT_FOUND")
    logger.info(f"[*] Sync Indices Config   : {sync_ind}")
    
    profiler = sched.get("PROFILER_ENGINE", "NOT_FOUND")
    logger.info(f"[*] Profiler Engine Config: {profiler}")
    
    if sync_ind == "NOT_FOUND" or profiler == "NOT_FOUND":
        logger.error("[FAIL] Missing configuration blocks in config.json.")
    else:
        logger.info("[PASS] Configuration settings are properly formatted.")

def check_database_schema():
    print_header("Checking Database Schema")
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(market_universe)")
    columns = [row['name'] for row in cursor.fetchall()]
    
    expected_cols = ['is_index', 'index_membership']
    missing = [c for c in expected_cols if c not in columns]
    
    if missing:
        logger.error(f"[FAIL] Missing columns in market_universe: {missing}")
    else:
        logger.info(f"[*] Found 'is_index' column.")
        logger.info(f"[*] Found 'index_membership' column.")
        logger.info("[PASS] Database schema migration was successful.")
        
    conn.close()

def check_firewall_and_data():
    print_header("Checking Data Overlap & Firewall")
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) FROM market_universe")
        total_assets = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM market_universe WHERE is_freetrade = 1")
        total_ft = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM market_universe WHERE is_index = 1")
        total_idx = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM market_universe WHERE is_index = 1 AND is_freetrade = 1")
        tradable_idx = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT index_membership, COUNT(*) as cnt 
            FROM market_universe 
            WHERE is_index = 1 
            GROUP BY index_membership
        """)
        breakdown = cursor.fetchall()
        
        logger.info(f"[*] Total Assets in DB       : {total_assets:,}")
        logger.info(f"[*] Total Freetrade Assets   : {total_ft:,}")
        logger.info(f"[*] Total Scraped Index Assets: {total_idx:,}")
        logger.info(f"[*] Tradable Index Overlap   : {tradable_idx:,} (This is your actual Firewall payload)")
        
        if breakdown:
            logger.info("\n[*] Index Breakdown:")
            for row in breakdown:
                logger.info(f"    - {row['index_membership']}: {row['cnt']} assets")
        
        # Test Engine Extraction
        engine_tickers = get_universe_tickers()
        logger.info(f"\n[*] Engine Extraction Test   : Extracted {len(engine_tickers):,} tickers based on current UI Settings.")
        
        if total_idx > 0:
            logger.info("[PASS] Data aggregation and firewall overlap calculated successfully.")
        else:
            logger.warning("[WARN] No index assets found. Did you run the scraper?")
            
    except Exception as e:
        logger.error(f"[FAIL] Database query failed: {e}")
    finally:
        conn.close()

def check_profiler_queue():
    print_header("Checking Profiler Queue")
    try:
        pending = count_pending_profiles()
        logger.info(f"[*] Assets requiring Fundamental Downloads: {pending:,}")
        logger.info("[PASS] Profiler queue calculated successfully.")
    except Exception as e:
        logger.error(f"[FAIL] Profiler queue check failed: {e}")

if __name__ == "__main__":
    try:
        check_configuration()
        check_database_schema()
        check_firewall_and_data()
        check_profiler_queue()
        print_header("Diagnostics Complete")
    except KeyboardInterrupt:
        sys.exit(0)