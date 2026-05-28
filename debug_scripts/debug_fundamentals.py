import sys
import logging
from database import get_connection

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def print_header(title: str):
    logger.info("\n" + "="*50)
    logger.info(f" {title.upper()}")
    logger.info("="*50)

def check_fundamentals():
    print_header("Fundamentals Data (Asset Profiles) Audit")
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # 1. Total profiles stored
        cursor.execute("SELECT COUNT(*) FROM asset_profiles")
        total_profiles = cursor.fetchone()[0]
        
        # 2. Freshness check (Within 90 days)
        cursor.execute("SELECT COUNT(*) FROM asset_profiles WHERE last_verified_date >= date('now', '-90 days')")
        fresh_profiles = cursor.fetchone()[0]
        stale_profiles = total_profiles - fresh_profiles
        
        # 3. Data Quality Check (Missing Sectors or Summaries)
        cursor.execute("SELECT COUNT(*) FROM asset_profiles WHERE sector = 'Unclassified' OR sector IS NULL")
        missing_sectors = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM asset_profiles WHERE business_summary = 'No business summary available.' OR business_summary IS NULL")
        missing_summaries = cursor.fetchone()[0]

        logger.info(f"[*] Total Profiles Stored      : {total_profiles:,}")
        logger.info(f"[*] Fresh Profiles (<90 days)  : {fresh_profiles:,}")
        logger.info(f"[*] Stale Profiles (>90 days)  : {stale_profiles:,}")
        logger.info(f"[*] Unclassified Sectors       : {missing_sectors:,}")
        logger.info(f"[*] Missing Business Summaries : {missing_summaries:,}")

        # 4. Fetch a live sample from the Tradable Index Universe
        print_header("Live Data Sample (Tradable Index Assets)")
        cursor.execute("""
            SELECT p.ticker, p.company_name, p.sector, p.last_verified_date 
            FROM asset_profiles p
            INNER JOIN market_universe m ON p.ticker = m.ticker
            WHERE m.is_index = 1 AND m.is_freetrade = 1
            LIMIT 5
        """)
        samples = cursor.fetchall()
        
        if samples:
            for row in samples:
                logger.info(f" -> Ticker: {row['ticker']:<6} | Sector: {row['sector']:<20} | Verified: {row['last_verified_date']} | Name: {row['company_name']}")
        else:
            logger.warning("[WARN] No index profiles found. You may need to run the Profiler Engine.")

    except Exception as e:
        logger.error(f"[FAIL] Database query failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        check_fundamentals()
        print_header("Diagnostics Complete")
    except KeyboardInterrupt:
        sys.exit(0)