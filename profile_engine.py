# profile_engine.py
import time
import random
import logging
import json
from pathlib import Path
from datetime import datetime
import yfinance as yf
from config import load_config
from database import get_connection
from tools.network_engine import yahoo_connection_boundary

logging.basicConfig(level=logging.INFO, format='%(asctime)s - PROFILE_ENGINE - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BLACKLIST_PATH = Path("data/freetrade_blacklist.json")

def load_blacklist() -> set:
    if BLACKLIST_PATH.exists():
        try:
            with open(BLACKLIST_PATH, 'r') as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_blacklist(blacklist: set) -> None:
    try:
        BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BLACKLIST_PATH, 'w') as f:
            json.dump(sorted(list(blacklist)), f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save blacklist: {e}")

def update_single_profile(ticker: str) -> bool:
    """
    Fetches static metadata for a single ticker via yfinance and inserts it into asset_profiles.
    Handles blacklisting and orphan purging automatically.
    Returns True if successful, False if blacklisted or failed.
    """
    blacklist = load_blacklist()
    
    # Skip immediately if it has been blacklisted previously
    if ticker in blacklist:
        logger.info(f"Skipping profile update for {ticker}: Present in blacklist.")
        return False

    conn = get_connection()
    cursor = conn.cursor()
    
    with yahoo_connection_boundary(f"Profile Audit: {ticker}") as session:
        try:
            info = yf.Ticker(ticker, session=session).info
            
            # --- THE AUTOMATED BLACKLIST PURGE ---
            # Softened check: Mutual Funds often have very small info dictionaries. 
            # We only blacklist if we get absolutely no identifying information back from Yahoo.
            has_identity = 'shortName' in info or 'longName' in info or 'symbol' in info or 'regularMarketPrice' in info
            
            if not info or not has_identity:
                logger.warning(f"No valid payload for {ticker}. Permanently blacklisting and purging from database.")
                blacklist.add(ticker)
                save_blacklist(blacklist)
                
                # Ruthlessly delete the orphan from all tables
                cursor.execute("DELETE FROM market_universe WHERE ticker = ?", (ticker,))
                cursor.execute("DELETE FROM asset_profiles WHERE ticker = ?", (ticker,))
                cursor.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
                cursor.execute("DELETE FROM quant_signals WHERE ticker = ?", (ticker,))
                conn.commit()
                return False
                
            company_name = info.get('shortName') or info.get('longName') or ticker
            sector = info.get('sector', 'Unclassified')
            industry = info.get('industry', 'Unclassified')
            country = info.get('country', 'Unknown')
            exchange = info.get('exchange', 'Unknown')
            currency = info.get('currency', 'USD')
            quote_type = info.get('quoteType', 'EQUITY')
            summary = info.get('longBusinessSummary', 'No business summary available.')
            last_verified = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()
            
            cursor.execute('''
                INSERT OR REPLACE INTO asset_profiles 
                (ticker, company_name, sector, industry, country, exchange, currency, quote_type, business_summary, last_verified_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (ticker, company_name, sector, industry, country, exchange, currency, quote_type, summary, last_verified))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to fetch/save profile for {ticker}: {e}")
            return False
        finally:
            conn.close()

def run_profile_audit(limit: int = 250):
    logger.info(f"Initiating Audit for Central Asset Profiles (Limit: {limit})...")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

        if freetrade_only:
            # THE FIREWALL: Only audit active portfolio/watchlist assets OR tradable index constituents
            cursor.execute("""
                WITH AllTickers AS (
                    SELECT ticker FROM market_universe WHERE is_index = 1 AND is_freetrade = 1
                    UNION
                    SELECT ticker FROM stock_signals
                    UNION
                    SELECT ticker FROM quant_signals
                )
                SELECT a.ticker 
                FROM AllTickers a
                LEFT JOIN asset_profiles p ON a.ticker = p.ticker
                WHERE p.ticker IS NULL 
                   OR p.last_verified_date < date('now', '-90 days')
                LIMIT ?
            """, (limit,))
        else:
            # LEGACY MODE: Audit everything
            cursor.execute("""
                WITH AllTickers AS (
                    SELECT ticker FROM market_universe
                    UNION
                    SELECT ticker FROM stock_signals
                    UNION
                    SELECT ticker FROM quant_signals
                )
                SELECT a.ticker 
                FROM AllTickers a
                LEFT JOIN asset_profiles p ON a.ticker = p.ticker
                WHERE p.ticker IS NULL 
                   OR p.last_verified_date < date('now', '-90 days')
                LIMIT ?
            """, (limit,))
        
        rows = cursor.fetchall()
        tickers_to_update = [row['ticker'] for row in rows]
        
    except Exception as e:
        logger.error(f"Fatal error fetching target tickers during Asset Profile Audit: {e}")
        return
    finally:
        # Close connection before executing the long-running fetch loop to prevent DB locks
        conn.close()

    if not tickers_to_update:
        logger.info("All asset profiles are up-to-date within the last 90 days. No action needed.")
        return

    logger.info(f"Found {len(tickers_to_update)} profiles requiring initialization or refresh.")
    
    updated_count = 0
    for i, ticker in enumerate(tickers_to_update):
        if i > 0 and i % 50 == 0: 
            logger.info(f"Progress: {i}/{len(tickers_to_update)} fetched...")
            
        success = update_single_profile(ticker)
        if success:
            updated_count += 1
            
        # Respect API rate limits gracefully
        time.sleep(random.uniform(0.5, 1.5))
            
    logger.info(f"Asset Profile Audit complete. Updated {updated_count} static metadata records.")

if __name__ == "__main__":
    print("WARNING: Running initial massive data harvest. This will take ~1 to 1.5 hours to respect rate limits.")
    run_profile_audit(limit=5000)