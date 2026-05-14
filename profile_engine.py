# profile_engine.py
import time
import random
import logging
from datetime import datetime
import yfinance as yf

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - PROFILE_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def log_notification(message_type: str, message_text: str) -> None:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        conn.close()

def run_profile_audit(limit: int = 250):
    """
    The 'Rolling Audit'.
    Identifies up to `limit` tickers missing from asset_profiles or older than 90 days.
    Fetches static metadata from Yahoo Finance and updates the DB to serve as the single source of truth.
    """
    logger.info("Initiating Rolling Audit for Central Asset Profiles...")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Union all active tickers across the system (Universe + Personal Portfolio/Watchlist)
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
        
        if not tickers_to_update:
            logger.info("All asset profiles are up-to-date within the last 90 days. No action needed.")
            return

        logger.info(f"Found {len(tickers_to_update)} profiles requiring initialization or refresh.")
        
        updated_count = 0
        for ticker in tickers_to_update:
            try:
                logger.info(f"Fetching metadata for {ticker}...")
                info = yf.Ticker(ticker).info
                
                if not info:
                    logger.warning(f"No info payload returned for {ticker}. Skipping.")
                    continue
                    
                company_name = info.get('shortName') or info.get('longName') or ticker
                sector = info.get('sector', 'Unclassified')
                industry = info.get('industry', 'Unclassified')
                country = info.get('country', 'Unknown')
                exchange = info.get('exchange', 'Unknown')
                currency = info.get('currency', 'USD')
                quote_type = info.get('quoteType', 'EQUITY')
                summary = info.get('longBusinessSummary', 'No business summary available.')
                last_verified = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Clean company name formatting anomalies
                company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO asset_profiles 
                    (ticker, company_name, sector, industry, country, exchange, currency, quote_type, business_summary, last_verified_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, company_name, sector, industry, country, exchange, currency, quote_type, summary, last_verified))
                
                conn.commit()
                updated_count += 1
                
            except Exception as e:
                logger.error(f"Failed to fetch/save profile for {ticker}: {e}")
            finally:
                # Absolute requirement to avoid Yahoo Finance IP block over large loops
                time.sleep(random.uniform(1.0, 2.5))
                
        log_notification("Info", f"Asset Profile Audit complete. Updated {updated_count} static metadata records.")
        
    except Exception as e:
        logger.error(f"Fatal error during Asset Profile Audit: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_profile_audit()