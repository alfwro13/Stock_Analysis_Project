# clean_db.py
import os
import json
import sqlite3

PORTFOLIO_PATH = "data/portfolio.json"
WATCHLIST_PATH = "data/watchlist.json"
DB_PATH = "data/analysis.db"

def get_json_tickers():
    tickers = set()
    # Get Portfolio Tickers
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                for v in data.values():
                    if 'ticker' in v:
                        tickers.add(v['ticker'])
        except Exception: pass
        
    # Get Watchlist Tickers
    if os.path.exists(WATCHLIST_PATH):
        try:
            with open(WATCHLIST_PATH, 'r') as f:
                data = json.load(f)
                if 'watchlist' in data:
                    tickers.update(data['watchlist'])
        except Exception: pass
        
    return tickers

def clean_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("Building whitelist of valid tickers...")
    
    # 1. Get all valid tickers from the freshly synced Market Universe
    cursor.execute("SELECT ticker FROM market_universe")
    valid_universe = {row[0] for row in cursor.fetchall()}
    
    # 2. Add user's personal JSON tickers
    valid_json = get_json_tickers()
    
    # 3. The Master Whitelist
    whitelist = valid_universe.union(valid_json)
    print(f"Whitelist created: {len(whitelist)} valid assets identified.")
    
    if not whitelist:
        print("Error: Whitelist is empty. Make sure you ran the freetrade engine first.")
        return

    # 4. Ruthlessly purge orphans from all operational tables
    tables_to_clean = [
        'stock_signals', 
        'quant_signals', 
        'asset_profiles', 
        'earnings_volatility', 
        'market_pulse_cache'
    ]
    
    total_deleted = 0
    # SQLite has a limit on the number of variables in an IN clause (usually 999),
    # so we delete in batches.
    whitelist_list = list(whitelist)
    batch_size = 900
    
    for table in tables_to_clean:
        print(f"Cleaning table: {table}...")
        
        # First, find out what is in the table to see what needs deleting
        cursor.execute(f"SELECT DISTINCT ticker FROM {table}")
        table_tickers = {row[0] for row in cursor.fetchall()}
        
        orphans = table_tickers - whitelist
        if orphans:
            orphan_list = list(orphans)
            for i in range(0, len(orphan_list), batch_size):
                batch = orphan_list[i:i + batch_size]
                placeholders = ','.join('?' for _ in batch)
                cursor.execute(f"DELETE FROM {table} WHERE ticker IN ({placeholders})", batch)
                total_deleted += cursor.rowcount
            print(f" -> Purged {len(orphans)} ghost tickers from {table}.")
        else:
            print(f" -> {table} is already clean.")

    conn.commit()
    conn.close()
    
    print(f"\nSUCCESS! Database completely sanitized. Purged {total_deleted} orphaned rows.")

if __name__ == "__main__":
    clean_database()