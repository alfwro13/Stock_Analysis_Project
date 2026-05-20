# clean_db.py
import os
import json
from pathlib import Path
from database import get_connection

PORTFOLIO_PATH = Path("data/portfolio.json")
WATCHLIST_PATH = Path("data/watchlist.json")
ISIN_CACHE_PATH = Path("data/isin_ticker_cache.json")
BLACKLIST_PATH = Path("data/freetrade_blacklist.json")

def get_json_tickers() -> set:
    tickers = set()
    if PORTFOLIO_PATH.exists():
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                for v in data.values():
                    if 'ticker' in v:
                        tickers.add(v['ticker'].strip())
        except Exception: pass
        
    if WATCHLIST_PATH.exists():
        try:
            with open(WATCHLIST_PATH, 'r') as f:
                data = json.load(f)
                if 'watchlist' in data:
                    for t in data['watchlist']:
                        tickers.add(t.strip())
        except Exception: pass
    return tickers

def sanitize_system_layers():
    print("🚀 Initiating Deep System Cleansing Protocol...")
    
    # 1. Banish poisoned cache files to avoid fast-path re-injection loops
    if ISIN_CACHE_PATH.exists():
        try:
            ISIN_CACHE_PATH.unlink()
            print(" -> Successfully deleted corrupted ISIN ticker cache file.")
        except Exception as e:
            print(f" -> Error deleting ISIN cache: {e}")

    if BLACKLIST_PATH.exists():
        try:
            BLACKLIST_PATH.unlink()
            print(" -> Resetting freetrade blacklist tracker file for clean baseline.")
        except Exception as e:
            print(f" -> Error deleting blacklist tracker: {e}")

    # 2. Connect to local relational database engine using our centralized WAL-enabled pool
    try:
        conn = get_connection()
    except Exception as e:
        print(f"CRITICAL: Failed to connect to database engine via get_connection(): {e}")
        return
        
    cursor = conn.cursor()
    
    # 3. Aggressively purge any Freetrade records from market universe to clear structural faults
    cursor.execute("DELETE FROM market_universe WHERE is_freetrade = 1")
    print(f" -> Cleared {cursor.rowcount} entries from market_universe.")

    # 4. Extract current valid tickers from pristine sources
    cursor.execute("SELECT ticker FROM market_universe")
    whitelist = {row[0] for row in cursor.fetchall()}.union(get_json_tickers())
    print(f" -> Compiled pristine security whitelist: {len(whitelist)} tracking tokens approved.")

    tables_to_clean = [
        'stock_signals', 
        'quant_signals', 
        'asset_profiles', 
        'earnings_volatility', 
        'market_pulse_cache'
    ]
    
    total_orphans_purged = 0
    
    for table in tables_to_clean:
        # Check if table exists before querying
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cursor.fetchone():
            continue
            
        # Target 1: Wipe out legacy rows containing ANY lowercase letter (Freetrade ghosts)
        cursor.execute(f"SELECT ticker FROM {table}")
        all_tickers = [row[0] for row in cursor.fetchall() if row[0]]
        
        bad_tickers = [t for t in all_tickers if any(char.islower() for char in t)]
        if bad_tickers:
            placeholders = ','.join('?' for _ in bad_tickers)
            cursor.execute(f"DELETE FROM {table} WHERE ticker IN ({placeholders})", bad_tickers)
            total_orphans_purged += cursor.rowcount
            print(f" -> Surgically removed {cursor.rowcount} explicit lowercase tokens from '{table}'.")

        # Target 2: Banish standard orphan keys not represented within our asset universe Whitelist
        cursor.execute(f"SELECT DISTINCT ticker FROM {table}")
        remaining_tickers = {row[0] for row in cursor.fetchall() if row[0]}
        orphans = remaining_tickers - whitelist
        
        if orphans:
            orphan_list = list(orphans)
            batch_size = 900
            for i in range(0, len(orphan_list), batch_size):
                batch = orphan_list[i:i + batch_size]
                placeholders = ','.join('?' for _ in batch)
                cursor.execute(f"DELETE FROM {table} WHERE ticker IN ({placeholders})", batch)
                total_orphans_purged += cursor.rowcount
            print(f" -> Cleaned {len(orphans)} unaligned orphan records from '{table}'.")

    conn.commit()
    conn.close()
    print(f"✨ Purge complete. Surgically extracted {total_orphans_purged} polluted references.")

if __name__ == "__main__":
    sanitize_system_layers()