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

# MIC mapping for Yahoo Finance Suffixes
MIC_YF_SUFFIX_MAP = {
    'XLON': '.L',    # London
    'XFRA': '.DE',   # Frankfurt
    'XETR': '.DE',   # Xetra
    'XPAR': '.PA',   # Paris
    'XAMS': '.AS',   # Amsterdam
    'XBRU': '.BR',   # Brussels
    'XDUB': '.IR',   # Dublin
    'XMAD': '.MC',   # Madrid
    'XMIL': '.MI',   # Milan
    'XLIS': '.LS',   # Lisbon
    'XHEL': '.HE',   # Helsinki
    'XSTO': '.ST',   # Stockholm
    'XOSL': '.OL',   # Oslo
    'XCSE': '.CO',   # Copenhagen
    'XVIE': '.VI',   # Vienna
    'XSWX': '.SW'    # Swiss
}

# MIC mapping for Human Readable Exchanges
MIC_EXCHANGE_MAP = {
    'XLON': 'LSE', 'XNAS': 'NASDAQ', 'XNYS': 'NYSE', 'ARCX': 'NYSE ARCA',
    'BATS': 'BATS', 'XFRA': 'Frankfurt', 'XETR': 'XETRA', 'XPAR': 'Paris',
    'XAMS': 'Amsterdam', 'XBRU': 'Brussels', 'XDUB': 'Dublin', 'XMAD': 'Madrid',
    'XMIL': 'Milan', 'XHEL': 'Helsinki', 'XSTO': 'Stockholm', 'XOSL': 'Oslo'
}

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
    applies international Yahoo Finance suffixes, validates KIID URLs, 
    and performs a safe UPSERT into the database.
    """
    logger.info("Starting Freetrade Universe Sync...")
    
    try:
        df = pd.read_csv(FREETRADE_CSV_URL)
        logger.info(f"Successfully downloaded {len(df)} records from Freetrade.")
        
        df.columns = df.columns.str.strip()
        
        if 'Symbol' not in df.columns:
            raise ValueError("CRITICAL: 'Symbol' column is entirely missing from the Freetrade CSV.")
            
        # Clean 'Symbol' column
        df['Symbol'] = df['Symbol'].astype(str)
        df['Symbol'] = df['Symbol'].str.replace('.', '-', regex=False)
        
        # Apply strict MIC to Yahoo Finance Suffix Mapping
        if 'MIC' in df.columns:
            def apply_suffix(row):
                symbol = str(row['Symbol']).strip()
                mic = str(row['MIC']).strip().upper()
                
                # Check our suffix dictionary
                if mic in MIC_YF_SUFFIX_MAP:
                    suffix = MIC_YF_SUFFIX_MAP[mic]
                    if not symbol.endswith(suffix):
                        return symbol + suffix
                return symbol
                
            df['Symbol'] = df.apply(apply_suffix, axis=1)
            
            # Map human-readable exchanges
            def map_exchange(mic):
                mic = str(mic).strip().upper()
                return MIC_EXCHANGE_MAP.get(mic, mic if mic != 'NAN' else 'Unknown')
                
            df['Mapped_Exchange'] = df['MIC'].apply(map_exchange)
        else:
            logger.warning("'MIC' column not found. Skipping EU suffix mapping.")
            df['Mapped_Exchange'] = 'Unknown'
            
        # Dynamic Column Matching for KIID URL
        kiid_col = next((c for c in df.columns if 'kiid' in c.lower()), None)
        
        if kiid_col:
            def clean_url(val: any) -> Optional[str]:
                if isinstance(val, str) and val.strip().lower().startswith("https://"):
                    return val.strip()
                return None
            df['KIID URL'] = df[kiid_col].apply(clean_url)
        else:
            logger.warning("No column matching 'KIID' was found. Defaulting all URLs to None.")
            df['KIID URL'] = None
            
        # Prepare records for bulk database insert
        records = []
        for _, row in df.iterrows():
            symbol = row.get('Symbol')
            title = row.get('Title')
            subtitle = row.get('Subtitle')
            kiid_url = row.get('KIID URL')
            exchange = row.get('Mapped_Exchange', 'Unknown')
            
            if pd.isna(symbol) or str(symbol).lower() == 'nan' or not str(symbol).strip():
                continue
                
            records.append((symbol, title, subtitle, kiid_url, exchange))
            
        if not records:
            logger.warning("No valid records found after processing Freetrade CSV.")
            return

        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("UPDATE market_universe SET is_freetrade = 0")
            
            upsert_query = """
                INSERT INTO market_universe (ticker, company_name, freetrade_subtitle, is_freetrade, freetrade_url, exchange)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    is_freetrade = 1,
                    freetrade_subtitle = excluded.freetrade_subtitle,
                    freetrade_url = excluded.freetrade_url,
                    exchange = excluded.exchange,
                    company_name = COALESCE(market_universe.company_name, excluded.company_name)
            """
            cursor.executemany(upsert_query, records)
            
            conn.commit()
            success_msg = f"Successfully synced {len(records)} Freetrade assets to the database."
            logger.info(success_msg)
            log_freetrade_notification("Success", success_msg)
            
        except Exception as db_err:
            conn.rollback()
            raise db_err
        finally:
            conn.close()

    except Exception as e:
        error_msg = f"Failed to sync Freetrade Universe: {e}"
        logger.error(error_msg)
        log_freetrade_notification("Error", error_msg)
        raise e