import logging
from database import get_connection

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def verify_firewall_data():
    conn = get_connection()
    cursor = conn.cursor()
    
    logger.info("\n==================================================")
    logger.info(" FINAL FIREWALL DATA VALIDATION")
    logger.info("==================================================")
    
    # 1. Fetch 5 valid overlapping tickers directly from the source of truth
    cursor.execute("SELECT ticker, index_membership FROM market_universe WHERE is_index = 1 AND is_freetrade = 1 LIMIT 5")
    firewall_assets = cursor.fetchall()
    
    if not firewall_assets:
        logger.error("[FAIL] No assets found in the firewall overlap.")
        return
        
    logger.info(f"[*] Extracting {len(firewall_assets)} sample assets that cleared the Freetrade Firewall...\n")
    
    # 2. Query their fundamentals explicitly, bypassing SQL JOINs
    for asset in firewall_assets:
        ticker = asset['ticker']
        membership = asset['index_membership']
        
        cursor.execute("SELECT company_name, sector, last_verified_date FROM asset_profiles WHERE ticker = ?", (ticker,))
        profile = cursor.fetchone()
        
        if profile:
            logger.info(f" [✓] {ticker:<6} ({membership})")
            logger.info(f"     -> Sector  : {str(profile['sector'])}")
            logger.info(f"     -> Name    : {profile['company_name']}")
            logger.info(f"     -> Fresh   : {profile['last_verified_date']}\n")
        else:
            logger.error(f" [X] {ticker:<6} -> NOT FOUND IN asset_profiles! (Check Freetrade API Queue)\n")
            
    conn.close()

if __name__ == "__main__":
    verify_firewall_data()