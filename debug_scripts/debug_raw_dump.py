### `debug_raw_dump.py`
import logging
import sys
from database import get_connection

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def dump_index_sample(index_name: str, limit: int = 10):
    conn = get_connection()
    cursor = conn.cursor()
    
    logger.info(f"\n{'='*80}")
    logger.info(f" RAW DATA DUMP: {index_name} (Limit {limit})")
    logger.info(f"{'='*80}")
    
    # Extract from market_universe
    cursor.execute("""
        SELECT ticker, company_name, is_index, is_freetrade, index_membership 
        FROM market_universe 
        WHERE index_membership LIKE ? 
        LIMIT ?
    """, (f"%{index_name}%", limit))
    
    universe_rows = cursor.fetchall()
    
    if not universe_rows:
        logger.info(f"No rows found for {index_name} in market_universe.")
        conn.close()
        return

    for u_row in universe_rows:
        ticker = u_row['ticker']
        logger.info(f"\n--- TICKER: {repr(ticker)} ---")
        logger.info(f"  [market_universe] Name: {repr(u_row['company_name'])}, is_index: {u_row['is_index']}, is_ft: {u_row['is_freetrade']}, tags: {repr(u_row['index_membership'])}")
        
        # Direct lookup in asset_profiles
        cursor.execute("""
            SELECT ticker, company_name, sector, last_verified_date 
            FROM asset_profiles 
            WHERE ticker = ?
        """, (ticker,))
        p_row = cursor.fetchone()
        
        if p_row:
            logger.info(f"  [asset_profiles]  MATCH: {repr(p_row['ticker'])}, Name: {repr(p_row['company_name'])}, Sector: {repr(p_row['sector'])}, Fresh: {p_row['last_verified_date']}")
        else:
            logger.info(f"  [asset_profiles]  >> EXACT MATCH MISSING <<")
            
            # Fuzzy fallback to catch trailing spaces, mangled dots/hyphens, etc.
            clean_ticker = ticker.strip()
            cursor.execute("""
                SELECT ticker, company_name 
                FROM asset_profiles 
                WHERE ticker LIKE ? OR ticker LIKE ?
            """, (f"%{clean_ticker}%", f"%{clean_ticker.replace('-', '.')}%"))
            
            fuzzy_matches = cursor.fetchall()
            if fuzzy_matches:
                fuzzy_list = [repr(f['ticker']) for f in fuzzy_matches]
                logger.warning(f"  [asset_profiles]  *FUZZY MATCH FOUND INSTEAD*: {fuzzy_list}")
            else:
                logger.error(f"  [asset_profiles]  *GHOSTED* - Ticker does not exist in any format.")
                
    conn.close()

if __name__ == "__main__":
    try:
        dump_index_sample("SP500", 10)
        dump_index_sample("FTSE100", 10)
    except KeyboardInterrupt:
        sys.exit(0)