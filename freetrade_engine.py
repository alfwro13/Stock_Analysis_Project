# freetrade_engine.py
import json
import os
import time
import random
import requests
import logging
import argparse
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
    if ISIN_CACHE_PATH.exists():
        try:
            with open(ISIN_CACHE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load ISIN cache: {e}")
    return {}

def save_isin_cache(cache_dict: Dict[str, str]) -> None:
    try:
        ISIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ISIN_CACHE_PATH, 'w') as f:
            json.dump(cache_dict, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save ISIN cache: {e}")

def log_freetrade_notification(msg_type: str, msg_text: str) -> None:
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
    # Do not uppercase yet. Preserve original casing to analyze FT's suffix patterns.
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
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        try:
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
            pass
            
    # 5. Configurable Fallback Logic
    exchange_info = exchanges[mic]
    ft_char = exchange_info.get("ft_char", "")
    yf_suffix = exchange_info.get("yf_suffix", "")
    
    # Stripping logic: Using case-sensitive exact match from config
    if ft_char and raw_symbol.endswith(ft_char):
        raw_symbol = raw_symbol[:-len(ft_char)]
        
    clean_symbol = raw_symbol.replace('.', '-').upper()
    
    if not clean_symbol.endswith(yf_suffix):
        return clean_symbol + yf_suffix, True
        
    return clean_symbol, True

def sync_freetrade_universe(target_mic: Optional[str] = None, limit: Optional[int] = None) -> None:
    logger.info("Starting Configuration-Enhanced Freetrade Universe Sync...")
    
    try:
        df = pd.read_csv(FREETRADE_CSV_URL)
        df.columns = df.columns.str.strip()
        
        if 'Symbol' not in df.columns:
            raise ValueError("CRITICAL: 'Symbol' column is missing from CSV.")
            
        # Apply CLI Filters
        if target_mic:
            target_mic = target_mic.strip().upper()
            df = df[df['MIC'].astype(str).str.strip().str.upper() == target_mic]
            logger.info(f"Filtered for MIC: {target_mic}. Found {len(df)} records.")
            
        if limit:
            df = df.head(limit)
            logger.info(f"Limited run to {limit} records for testing.")
            
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
            
            # CLI Debug Output
            if target_mic or limit:
                logger.info(f"TEST: Original: '{symbol_raw}' (ISIN: {isin}) -> Resolved: '{resolved_ticker}'")
            
            if not is_mapped:
                unmapped_mics.add(mic)
                continue
            
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
        
        if unmapped_mics:
            logger.warning(f"Unmapped Freetrade MICs detected and skipped: {list(unmapped_mics)}")
        
        if not records:
            logger.warning("No valid records to insert.")
            return

        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # ONLY wipe the database if we are running a full, un-filtered sync
            if not target_mic and not limit:
                logger.info("Executing Bulk SQLite Purge & Upsert...")
                cursor.execute("DELETE FROM market_universe WHERE is_freetrade = 1")
            else:
                logger.info("Running in Safe Mode (No purge). Upserting records...")
            
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
            
            logger.info(f"Successfully synced {len(records)} Freetrade assets to the database.")
            
        except Exception as db_err:
            conn.rollback()
            raise db_err
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"Failed to sync Freetrade Universe: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Freetrade Universe Data Ingestion")
    parser.add_argument("--mic", type=str, help="Specific MIC to process (e.g., XPAR, XBRU)", default=None)
    parser.add_argument("--limit", type=int, help="Limit the number of records to process", default=None)
    
    args = parser.parse_args()
    
    sync_freetrade_universe(target_mic=args.mic, limit=args.limit)