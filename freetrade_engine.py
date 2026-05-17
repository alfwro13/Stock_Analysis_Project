# freetrade_engine.py
import pandas as pd
import requests
import logging
from typing import Optional
from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - FREETRADE_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

FREETRADE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTGZT9-lSDDlgQzHsH0vYdTSz-xnL7zIJQ1SHUddo-BBD5_QlN--57cRe_8Zvw-7QsMrw6X1phz-vKq/pub?output=csv"

def log_freetrade_notification(msg_type: str, msg_text: str) -> None:
    """Logs system notifications securely into the database."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", 
            (msg_type, msg_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def sync_freetrade_universe() -> None:
    """
    Downloads the official Freetrade CSV universe, cleans ticker symbols,
    validates KIID URLs, and performs an idempotent upsert into the database.
    Hardened against upstream schema drift and missing columns.
    """
    logger.info("Starting Freetrade Universe Sync...")
    
    try:
        # Download and load CSV into pandas
        df = pd.read_csv(FREETRADE_CSV_URL)
        logger.info(f"Successfully downloaded {len(df)} records from Freetrade.")
        
        # 1. Normalize all headers to prevent hidden whitespace KeyErrors
        df.columns = df.columns.str.strip()
        
        # 2. Critical Field Validation
        if 'Symbol' not in df.columns:
            raise ValueError("CRITICAL: 'Symbol' column is entirely missing from the Freetrade CSV.")
            
        # Clean 'Symbol' column
        df['Symbol'] = df['Symbol'].astype(str)
        # Replace '.' with '-' for standard yfinance compatibility (e.g., BRK.B -> BRK-B)
        df['Symbol'] = df['Symbol'].str.replace('.', '-', regex=False)
        
        # 3. Conditional Vectorization for 'MIC'
        if 'MIC' in df.columns:
            xlon_mask = (df['MIC'] == 'XLON') & (~df['Symbol'].str.endswith('.L'))
            df.loc[xlon_mask, 'Symbol'] = df.loc[xlon_mask, 'Symbol'] + '.L'
        else:
            logger.warning("'MIC' column not found. Skipping London Stock Exchange '.L' suffix appending.")
            
        # 4. Conditional Vectorization for 'KIID URL'
        if 'KIID URL' in df.columns:
            def clean_url(val: any) -> Optional[str]:
                if isinstance(val, str) and val.strip().startswith("https://"):
                    return val.strip()
                return None
            df['KIID URL'] = df['KIID URL'].apply(clean_url)
        else:
            logger.warning("'KIID URL' column not found. Defaulting all URLs to None.")
            df['KIID URL'] = None
            
        # Prepare records for bulk database insert
        records = []
        for _, row in df.iterrows():
            # .get() safely handles missing non-critical columns returning None
            symbol = row.get('Symbol')
            title = row.get('Title')
            subtitle = row.get('Subtitle')
            currency = row.get('Currency')
            kiid_url = row.get('KIID URL')
            
            # Skip rows where symbol resolved to NaN or is empty
            if pd.isna(symbol) or str(symbol).lower() == 'nan' or not str(symbol).strip():
                continue
                
            records.append((symbol, title, subtitle, currency, kiid_url))
            
        if not records:
            logger.warning("No valid records found after processing Freetrade CSV.")
            return

        # Database operations
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Reset Freetrade flag across the board
            cursor.execute("UPDATE market_universe SET is_freetrade = 0")
            
            # Idempotent Upsert
            upsert_query = """
                INSERT OR REPLACE INTO market_universe 
                (ticker, company_name, freetrade_subtitle, currency, is_freetrade, freetrade_url) 
                VALUES (?, ?, ?, ?, 1, ?)
            """
            cursor.executemany(upsert_query, records)
            
            conn.commit()
            
            success_msg = f"Successfully synced {len(records)} Freetrade assets to the database."
            logger.info(success_msg)
            log_freetrade_notification("Success", success_msg)
            
        except Exception as db_err:
            conn.rollback()
            logger.error(f"Database transaction failed during Freetrade sync: {db_err}")
            raise db_err
        finally:
            conn.close()

    except Exception as e:
        error_msg = f"Failed to sync Freetrade Universe: {e}"
        logger.error(error_msg)
        log_freetrade_notification("Error", error_msg)
        raise e