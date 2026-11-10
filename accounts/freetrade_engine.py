import json
import os
import time
import random
import logging
import argparse
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from db.database import get_connection
from config import load_config
from constants import FREETRADE_IGNORED_MICS
from yahoo_engine import yahoo_engine
from profile_engine import load_blacklist

logger = logging.getLogger(__name__)

FREETRADE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTGZT9-lSDDlgQzHsH0vYdTSz-xnL7zIJQ1SHUddo-BBD5_QlN--57cRe_8Zvw-7QsMrw6X1phz-vKq/pub?output=csv"
ISIN_CACHE_PATH = Path("data/isin_ticker_cache.json")

def load_isin_cache() -> Dict[str, str]:
    if ISIN_CACHE_PATH.exists():
        try:
            with open(ISIN_CACHE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load ISIN cache: %s", e)
    return {}

def save_isin_cache(cache_dict: Dict[str, str]) -> None:
    try:
        ISIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ISIN_CACHE_PATH, 'w') as f:
            json.dump(cache_dict, f, indent=4)
    except Exception as e:
        logger.error("Failed to save ISIN cache: %s", e)

def resolve_ticker(symbol: str, isin: str, mic: str, cache_dict: Dict[str, str], ft_config: Dict) -> Tuple[Optional[str], bool]:
    # Trailing periods are stripped (RR. and BP. would become RR..L); internal dots become hyphens
    # because Yahoo uses them for share classes on every exchange (BRK.B -> BRK-B, BT.A -> BT-A.L).
    ft_symbol = str(symbol).strip().rstrip('.').replace('.', '-')
    mic = str(mic).strip().upper()
    
    us_mics = ft_config.get("US_MICS", [])
    exchanges = ft_config.get("EXCHANGES", {})
    
    if mic == "MUTUAL_FUND_EXCHANGE":
        if pd.notna(isin) and str(isin).strip():
            isin = str(isin).strip()
            if isin in cache_dict:
                return cache_dict[isin], True

            try:
                time.sleep(random.uniform(0.3, 0.7))
                resolved_symbol = yahoo_engine.search_by_isin(isin)
                if resolved_symbol:
                    cache_dict[isin] = resolved_symbol
                    return resolved_symbol, True
            except Exception:
                logger.debug("ISIN lookup failed for %s, using fallback symbol", isin, exc_info=True)

        return ft_symbol + ".L", True

    if mic in us_mics:
        return ft_symbol.upper(), True

    # MICs not in EXCHANGES config (e.g., XETR, XPAR) are unsupported – skip
    if mic not in exchanges:
        return None, False

    return ft_symbol + ".L", True


def sync_freetrade_universe(target_mic: Optional[str] = None, limit: Optional[int] = None) -> None:
    logger.info("Starting Configuration-Enhanced Freetrade Universe Sync...")
    
    try:
        df = pd.read_csv(FREETRADE_CSV_URL)
        df.columns = df.columns.str.strip()
        
        if 'Symbol' not in df.columns:
            raise ValueError("CRITICAL: 'Symbol' column is missing from CSV.")
            
        if target_mic:
            target_mic = target_mic.strip().upper()
            df = df[df['MIC'].astype(str).str.strip().str.upper() == target_mic]
            logger.info("Filtered for MIC: %s. Found %s records.", target_mic, len(df))
            
        if limit:
            df = df.head(limit)
            logger.info("Limited run to %s records for testing.", limit)
            
        kiid_col = next((c for c in df.columns if 'kiid' in c.lower()), None)
        def clean_url(val: Any) -> Optional[str]:
            if isinstance(val, str) and val.strip().lower().startswith("https://"):
                return val.strip()
            return None
        df['KIID URL'] = df[kiid_col].apply(clean_url) if kiid_col else None
            
        cache_dict = load_isin_cache()
        blacklist = load_blacklist()
        ft_config = load_config().get("FREETRADE_MAPPINGS", {})
        
        records = []
        unmapped_mics = set()
        processed_count = 0
        total_rows = len(df)
        
        logger.info("Resolving %s tickers against dynamic configuration map...", total_rows)
        
        for i, row in df.iterrows():
            symbol_raw = row.get('Symbol')
            isin = row.get('ISIN')
            mic = str(row.get('MIC')).strip().upper()
            
            if pd.isna(symbol_raw) or not str(symbol_raw).strip():
                continue
                
            resolved_ticker, is_mapped = resolve_ticker(symbol_raw, isin, mic, cache_dict, ft_config)
            
            if target_mic or limit:
                logger.info("TEST: Original: '%s' (ISIN: %s) -> Resolved: '%s'", symbol_raw, isin, resolved_ticker)
            
            if not is_mapped:
                if mic not in FREETRADE_IGNORED_MICS:
                    unmapped_mics.add(mic)
                continue
                
            if resolved_ticker in blacklist:
                if target_mic or limit:
                    logger.info("TEST: '%s' is on the Blacklist. Skipping DB insertion.", resolved_ticker)
                continue
            
            ui_exchange = ft_config.get("EXCHANGES", {}).get(mic, {}).get("ui_name", mic)
            if mic in ft_config.get("US_MICS", []):
                ui_exchange = "US Equities"
            
            quote_type = "MUTUALFUND" if mic == "MUTUAL_FUND_EXCHANGE" else "EQUITY"
            records.append((resolved_ticker, row.get('Title', 'Unknown'), row.get('Subtitle', ''), row.get('KIID URL'), ui_exchange, quote_type))
            processed_count += 1
            
            if processed_count % 50 == 0:
                logger.info("Freetrade Sync Progress: %s assets processed...", processed_count)
            if processed_count % 500 == 0:
                save_isin_cache(cache_dict)

        save_isin_cache(cache_dict)
        
        if unmapped_mics:
            logger.warning("Unmapped Freetrade MICs detected and skipped: %s", list(unmapped_mics))
        
        if not records:
            logger.warning("No valid records to insert.")
            return

        conn = get_connection()

        try:
            cursor = conn.cursor()
            if not target_mic and not limit:
                logger.info("Executing Bulk Freetrade Flag Reset & Upsert...")
                # Soft-delete instead of DELETE to preserve is_index flags set by the Wikipedia import
                cursor.execute("UPDATE market_universe SET is_freetrade = 0 WHERE is_freetrade = 1")
            else:
                logger.info("Running in Safe Mode (No purge). Upserting records...")
            
            upsert_query = """
                INSERT INTO market_universe (ticker, company_name, freetrade_subtitle, is_freetrade, freetrade_url, exchange, quote_type)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    is_freetrade = 1,
                    freetrade_subtitle = excluded.freetrade_subtitle,
                    freetrade_url = excluded.freetrade_url,
                    exchange = excluded.exchange,
                    quote_type = excluded.quote_type,
                    company_name = COALESCE(market_universe.company_name, excluded.company_name)
            """
            cursor.executemany(upsert_query, records)
            conn.commit()
            
            logger.info("Successfully synced %s Freetrade assets to the database.", len(records))
            
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except Exception as e:
        logger.error("Failed to sync Freetrade Universe: %s", e)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Freetrade Universe Data Ingestion")
    parser.add_argument("--mic", type=str, help="Specific MIC to process (e.g., XPAR, XBRU)", default=None)
    parser.add_argument("--limit", type=int, help="Limit the number of records to process", default=None)
    
    args = parser.parse_args()
    
    sync_freetrade_universe(target_mic=args.mic, limit=args.limit)