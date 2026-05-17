# freetrade_engine.py
import json
import os
import time
import random
import requests
import logging
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from database import get_connection
from config import load_config

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - FREETRADE_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
FREETRADE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTGZT9-lSDDlgQzHsH0vYdTSz-xnL7zIJQ1SHUddo-BBD5_QlN--57cRe_8Zvw-7QsMrw6X1phz-vKq/pub?output=csv"
ISIN_CACHE_PATH = Path("data/isin_ticker_cache.json")

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

def resolve_ticker(symbol: str, isin: str, mic: str, cache_dict: Dict[str, str], ft_config: Dict) -> Tuple[Optional[str], bool]:
    """
    Intelligently routes ticker resolution using the dynamic mapping config.
    Returns: (Resolved Ticker String, Is_Mapped Boolean)
    """
    # DO NOT UPPERCASE YET. We need the exact casing to strip Freetrade's lowercase identifiers safely.
    raw_symbol = str(symbol).strip()
    mic = str(mic).strip().upper()
    
    us_mics = ft_config.get("US_MICS", [])
    exchanges = ft_config.get("EXCHANGES", {})
    
    # 1. Fast Path: US Stocks
    if mic in us_mics:
        return raw_symbol.replace('.', '-').upper(), True
        
    # 2. Unmapped Circuit Breaker
    if mic not in exchanges:
        return None, False
        
    # 3. Check Local ISIN Cache
    if pd.notna(isin) and str(isin).strip():
        isin = str(isin).strip()
        if isin in cache_dict:
            return cache_dict[isin], True
            
        # 4. Query Yahoo Finance API
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
                        return resolved_symbol, True
        except Exception:
            pass # Silently fallback below
            
    # 5. Configurable Fallback Logic
    exchange_info = exchanges[mic]
    ft_char = exchange_info.get("ft_char", "")
    yf_suffix = exchange_info.get("yf_suffix", "")
    
    # Case-sensitive check! This ensures we strip 'b' (ECONBb) but ignore 'B' (WALLB)
    if ft_char and raw_symbol.endswith(ft_char):
        raw_symbol = raw_symbol[:-len(ft_char)]
        
    clean_symbol = raw_symbol.replace('.', '-').upper()
    
    if not clean_symbol.endswith(yf_suffix):
        return clean_symbol + yf_suffix, True
        
    return clean_symbol, True

def sync_freetrade_universe() -> None:
    """Master Pipeline for downloading, resolving, and upserting the Freetrade catalog."""
    logger.info("Starting Configuration-Enhanced Freetrade Universe Sync...")
    
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
        ft_config = load_config().get("FREETRADE_MAPPINGS", {})
        
        records = []
        unmapped_mics = set()
        processed_count = 0
        total_rows = len(df)
        
        logger.info(f"Resolving {total_rows} tickers against dynamic configuration map...")
        
        for i, row in df.iterrows():
            symbol_raw = row.get('Symbol')
            isin = row.get('ISIN')
            mic = str(row.get('MIC')).strip().upper()
            
            if pd.isna(symbol_raw) or str(symbol_raw).lower() == 'nan' or not str(symbol_raw).strip():
                continue
                
            resolved_ticker, is_mapped = resolve_ticker(symbol_raw, isin, mic, cache_dict, ft_config)
            
            if not is_mapped:
                unmapped_mics.add(mic)
                continue
            
            # Fetch the clean UI Name from config, fallback to the raw MIC if missing
            ui_exchange = ft_config.get("EXCHANGES", {}).get(mic, {}).get("ui_name", mic)
            if mic in ft_config.get("US_MICS", []):
                ui_exchange = "US Equities"
            
            records.append((resolved_ticker, row.get('Title', 'Unknown'), row.get('Subtitle', ''), row.get('KIID URL'), ui_exchange))
            processed_count += 1
            
            if processed_count % 50 == 0:
                logger.info(f"Freetrade Sync Progress: {processed_count} assets processed...")
            if processed_count % 500 == 0:
                save_isin_cache(cache_dict)

        save_isin_cache(cache_dict)
        
        # Fire Unmapped MIC Alert to the user
        if unmapped_mics:
            logger.warning(f"Unmapped Freetrade MICs detected and skipped: {list(unmapped_mics)}")
            log_freetrade_notification("Warning", f"Skipped assets due to unmapped exchanges: {list(unmapped_mics)}. Add them to FREETRADE_MAPPINGS in your config.json file to process them.")
        
        if not records:
            logger.warning("No valid records to insert.")
            return

        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            logger.info("Executing Bulk SQLite Purge & Upsert...")
            
            # CRITICAL: This wipes out all malformed Freetrade tickers from the database.
            cursor.execute("DELETE FROM market_universe WHERE is_freetrade = 1")
            
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
            
        except Exception as db_err:
            conn.rollback()
            raise db_err
        finally:
            conn.close()

    except Exception as e:
        error_msg = f"Failed to sync Freetrade Universe: {e}"
        logger.error(error_msg)
        raise e

if __name__ == "__main__":
    sync_freetrade_universe()