# freetrade_engine.py
import json
import os
import time
import random
import requests
import logging
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any
from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - FREETRADE_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
FREETRADE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTGZT9-lSDDlgQzHsH0vYdTSz-xnL7zIJQ1SHUddo-BBD5_QlN--57cRe_8Zvw-7QsMrw6X1phz-vKq/pub?output=csv"
ISIN_CACHE_PATH = Path("data/isin_ticker_cache.json")

# US Markets don't need ISIN resolution (Fast Path)
US_MICS = {'XNAS', 'XNYS', 'ARCX', 'BATS'}

# Fallback Suffix Map
MIC_YF_SUFFIX_MAP = {
    'XLON': '.L', 'XFRA': '.DE', 'XETR': '.DE', 'XPAR': '.PA',
    'XAMS': '.AS', 'XBRU': '.BR', 'XDUB': '.IR', 'XMAD': '.MC',
    'XMIL': '.MI', 'XLIS': '.LS', 'XHEL': '.HE', 'XSTO': '.ST',
    'XOSL': '.OL', 'XCSE': '.CO', 'XVIE': '.VI', 'XSWX': '.SW'
}

# UI Exchange Map
MIC_EXCHANGE_MAP = {
    'XLON': 'LSE', 'XNAS': 'NASDAQ', 'XNYS': 'NYSE', 'ARCX': 'NYSE ARCA',
    'BATS': 'BATS', 'XFRA': 'Frankfurt', 'XETR': 'XETRA', 'XPAR': 'Paris',
    'XAMS': 'Amsterdam', 'XBRU': 'Brussels', 'XDUB': 'Dublin', 'XMAD': 'Madrid',
    'XMIL': 'Milan', 'XHEL': 'Helsinki', 'XSTO': 'Stockholm', 'XOSL': 'Oslo'
}

def load_isin_cache() -> Dict[str, str]:
    """Loads the previously resolved ISIN mapping from disk."""
    if ISIN_CACHE_PATH.exists():
        try:
            with open(ISIN_CACHE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load ISIN cache: {e}")
    return {}

def save_isin_cache(cache_dict: Dict[str, str]) -> None:
    """Saves the ISIN mapping securely to disk."""
    try:
        ISIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ISIN_CACHE_PATH, 'w') as f:
            json.dump(cache_dict, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save ISIN cache: {e}")

def log_freetrade_notification(msg_type: str, msg_text: str) -> None:
    """Pushes an alert directly to the dashboard notification UI."""
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

def resolve_ticker(symbol: str, isin: str, mic: str, cache_dict: Dict[str, str]) -> str:
    """Intelligently routes ticker resolution using US bypassing and Local Caching."""
    symbol = str(symbol).strip().replace('.', '-')
    mic = str(mic).strip().upper()
    
    # 1. Fast Path: US Stocks
    if mic in US_MICS:
        return symbol
        
    # 2. Check Local Cache
    if pd.notna(isin) and str(isin).strip():
        isin = str(isin).strip()
        if isin in cache_dict:
            return cache_dict[isin]
            
        # 3. Query Yahoo Finance API
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={isin}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        
        try:
            # Mandatory IP safety throttle
            time.sleep(random.uniform(0.3, 0.7)) 
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                quotes = data.get('quotes', [])
                if quotes:
                    resolved_symbol = quotes[0].get('symbol')
                    if resolved_symbol:
                        cache_dict[isin] = resolved_symbol
                        return resolved_symbol
        except Exception:
            pass # Silently fallback below
            
    # 4. Ultimate Fallback Logic
    if mic in MIC_YF_SUFFIX_MAP:
        suffix = MIC_YF_SUFFIX_MAP[mic]
        if not symbol.endswith(suffix):
            return symbol + suffix
    return symbol

def sync_freetrade_universe() -> None:
    """Master Pipeline for downloading, resolving, and upserting the Freetrade catalog."""
    logger.info("Starting ISIN-Enhanced Freetrade Universe Sync...")
    log_freetrade_notification("Info", "Freetrade Sync initiated. Fetching CSV and resolving ISINs... This will take ~15 minutes in the background.")
    
    try:
        df = pd.read_csv(FREETRADE_CSV_URL)
        logger.info(f"Successfully downloaded {len(df)} records from Freetrade.")
        
        df.columns = df.columns.str.strip()
        
        if 'Symbol' not in df.columns:
            raise ValueError("CRITICAL: 'Symbol' column is missing from CSV.")
            
        # Extract KIID dynamically
        kiid_col = next((c for c in df.columns if 'kiid' in c.lower()), None)
        def clean_url(val: Any) -> Optional[str]:
            if isinstance(val, str) and val.strip().lower().startswith("https://"):
                return val.strip()
            return None
        df['KIID URL'] = df[kiid_col].apply(clean_url) if kiid_col else None
            
        cache_dict = load_isin_cache()
        records = []
        
        total_rows = len(df)
        logger.info(f"Resolving {total_rows} tickers. EU assets will be checked against Yahoo Finance...")
        
        processed_count = 0
        
        for i, row in df.iterrows():
            symbol_raw = row.get('Symbol')
            isin = row.get('ISIN')
            mic = row.get('MIC')
            title = row.get('Title', 'Unknown')
            subtitle = row.get('Subtitle', '')
            kiid_url = row.get('KIID URL')
            
            if pd.isna(symbol_raw) or str(symbol_raw).lower() == 'nan' or not str(symbol_raw).strip():
                continue
                
            resolved_ticker = resolve_ticker(symbol_raw, isin, mic, cache_dict)
            exchange = MIC_EXCHANGE_MAP.get(str(mic).strip().upper(), str(mic).strip().upper())
            
            records.append((resolved_ticker, title, subtitle, kiid_url, exchange))
            
            processed_count += 1
            
            # 🟢 THE FIX: STRICT COUNTER PROGRESS LOGGING
            if processed_count % 50 == 0:
                logger.info(f"Freetrade Sync Progress: {processed_count} / {total_rows} assets parsed...")
                
            # DB Notification & Cache Save every 500 to avoid locking the UI/DB
            if processed_count % 500 == 0:
                log_freetrade_notification("Info", f"Freetrade Sync Progress: {processed_count} / {total_rows} assets parsed...")
                save_isin_cache(cache_dict)

        # Final Cache Save
        save_isin_cache(cache_dict)
        
        if not records:
            logger.warning("No valid records to insert.")
            return

        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            logger.info("Executing Bulk SQLite Upsert...")
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
            
            success_msg = (
                f"Successfully synced {len(records)} Freetrade assets to the database.\n"
                "ACTION REQUIRED: Run 'python profile_engine.py' in your terminal, then trigger a Full Quant Scan from the settings UI."
            )
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

if __name__ == "__main__":
    sync_freetrade_universe()