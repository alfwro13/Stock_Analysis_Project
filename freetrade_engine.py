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

US_MICS = {'XNAS', 'XNYS', 'ARCX', 'BATS'}

# MIC mapping for Human Readable Exchanges
MIC_EXCHANGE_MAP = {
    'XLON': 'LSE', 'XNAS': 'NASDAQ', 'XNYS': 'NYSE', 'ARCX': 'NYSE ARCA',
    'BATS': 'BATS', 'XFRA': 'Frankfurt', 'XETR': 'XETRA', 'XPAR': 'Paris',
    'XAMS': 'Amsterdam', 'XBRU': 'Brussels', 'XDUB': 'Dublin', 'XMAD': 'Madrid',
    'XMIL': 'Milan', 'XHEL': 'Helsinki', 'XSTO': 'Stockholm', 'XOSL': 'Oslo'
}

# MIC mapping for Yahoo Finance Suffixes (Fallback)
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

def load_isin_cache() -> Dict[str, str]:
    """Loads the ISIN to Ticker resolution cache from disk."""
    if ISIN_CACHE_PATH.exists():
        try:
            with open(ISIN_CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("ISIN cache corrupted. Initializing fresh cache.")
            return {}
        except Exception as e:
            logger.error(f"Failed to load ISIN cache: {e}. Proceeding without cache.")
            return {}
    return {}

def save_isin_cache(cache_dict: Dict[str, str]) -> None:
    """Saves the ISIN to Ticker resolution cache to disk safely."""
    try:
        ISIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ISIN_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache_dict, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save ISIN cache: {e}")

def resolve_ticker(symbol: str, isin: str, mic: str, cache_dict: Dict[str, str]) -> str:
    """
    Deterministically resolves a ticker format for Yahoo Finance using ISIN search.
    Implements cascading fallbacks and local caching.
    """
    symbol = str(symbol).strip()
    mic = str(mic).strip().upper()
    isin = str(isin).strip()
    
    # Base normalization for base symbols
    base_symbol = symbol.replace('.', '-')

    # Rule 1: US MICS require no suffix processing and are assumed 1:1 mapped
    if mic in US_MICS:
        return base_symbol

    # Helper for legacy fallback logic
    def fallback_resolution() -> str:
        if mic in MIC_YF_SUFFIX_MAP:
            suffix = MIC_YF_SUFFIX_MAP[mic]
            if not base_symbol.endswith(suffix):
                return f"{base_symbol}{suffix}"
        return base_symbol

    # Rule 2: If ISIN is missing or invalid, fallback immediately
    if not isin or isin.lower() == 'nan':
        return fallback_resolution()

    # Rule 3: Check Local Cache
    if isin in cache_dict:
        return cache_dict[isin]

    # Rule 4: Query Yahoo Finance API
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={isin}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'quotes' in data and len(data['quotes']) > 0:
            resolved_ticker = data['quotes'][0].get('symbol')
            if resolved_ticker:
                cache_dict[isin] = resolved_ticker
                # Enforce stochastic delay to avoid IP bans
                time.sleep(random.uniform(0.3, 0.7))
                return resolved_ticker
                
    except requests.exceptions.RequestException as req_err:
        logger.warning(f"Network error resolving ISIN {isin} for symbol {symbol}: {req_err}. Triggering fallback.")
    except (KeyError, IndexError, ValueError) as parse_err:
        logger.warning(f"Data parsing error resolving ISIN {isin} for symbol {symbol}: {parse_err}. Triggering fallback.")
        
    # Final Fallback if API successfully called but returned no usable data or errored out
    return fallback_resolution()

def sync_freetrade_universe() -> None:
    """
    Downloads the official Freetrade CSV universe, cleans ticker symbols,
    applies ISIN-based Yahoo Finance resolution, maps metadata, 
    and performs a safe UPSERT into the database.
    """
    logger.info("Starting ISIN-Enhanced Freetrade Universe Sync...")
    
    try:
        df = pd.read_csv(FREETRADE_CSV_URL)
        logger.info(f"Successfully downloaded {len(df)} records from Freetrade.")
        
        # Clean column headers
        df.columns = df.columns.str.strip()
        
        # Validate critical columns
        required_cols = {'Symbol', 'MIC', 'ISIN'}
        missing_cols = required_cols - set(df.columns)
        if missing_cols:
            raise ValueError(f"CRITICAL: Missing essential columns in Freetrade CSV: {missing_cols}")
            
        isin_cache = load_isin_cache()
        
        # Dynamic Column Matching for KIID URL
        kiid_col = next((c for c in df.columns if 'kiid' in c.lower()), None)
        
        def clean_url(val: Any) -> Optional[str]:
            if isinstance(val, str) and val.strip().lower().startswith("https://"):
                return val.strip()
            return None
            
        records = []
        for index, row in df.iterrows():
            raw_symbol = row.get('Symbol')
            if pd.isna(raw_symbol) or str(raw_symbol).lower() == 'nan' or not str(raw_symbol).strip():
                continue
                
            mic = str(row.get('MIC', 'UNKNOWN'))
            isin = str(row.get('ISIN', ''))
            
            # Utilize the newly upgraded ISIN engine
            resolved_ticker = resolve_ticker(raw_symbol, isin, mic, isin_cache)
            
            title = row.get('Title')
            subtitle = row.get('Subtitle')
            kiid_url = clean_url(row.get(kiid_col)) if kiid_col else None
            exchange = MIC_EXCHANGE_MAP.get(mic.upper(), mic if mic.upper() != 'NAN' else 'Unknown')
            
            records.append((resolved_ticker, title, subtitle, kiid_url, exchange))

        # Save cache back to disk immediately after the loop
        save_isin_cache(isin_cache)
            
        if not records:
            logger.warning("No valid records found after processing Freetrade CSV.")
            return

        # Database Execution
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Clear current universe flags
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
                f"Successfully synced {len(records)} Freetrade assets to the database. "
                "ACTION REQUIRED: These new assets are currently dormant. To make them visible in the screeners, "
                "you MUST run 'python profile_engine.py' in your terminal, and then trigger a Full Quant Scan from the settings UI."
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
    # Standard script invocation fallback for manual overrides
    sync_freetrade_universe()