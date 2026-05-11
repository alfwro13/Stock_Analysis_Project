# maintenance_engine.py
import os
import json
import sqlite3
from datetime import datetime, timedelta
from config import (
    PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, 
    INTRADAY_DIR, FUNDAMENTALS_DIR, DB_PATH
)


class MaintenanceEngine:
    """
    Automated system cleaner. Trims the notification ledger, 
    deletes orphaned files from sold assets, and vacuums the SQLite database.
    """

    def __init__(self):
        self.days_to_keep_logs = 30
        self.protected_files = ["SP500_BASELINE.parquet"]

    def _get_active_tickers(self) -> set:
        """Collects a set of all valid tickers currently tracked."""
        active_tickers = set()
        
        # Parse Portfolio
        if os.path.exists(PORTFOLIO_PATH):
            try:
                with open(PORTFOLIO_PATH, 'r') as f:
                    data = json.load(f)
                    for v in data.values():
                        if 'ticker' in v:
                            active_tickers.add(v['ticker'])
            except Exception:
                pass
                
        # Parse Watchlist
        if os.path.exists(WATCHLIST_PATH):
            try:
                with open(WATCHLIST_PATH, 'r') as f:
                    data = json.load(f)
                    if 'watchlist' in data:
                        active_tickers.update(data['watchlist'])
            except Exception:
                pass
                
        return active_tickers

    def prune_database_logs(self):
        """Deletes notification logs older than 30 days to prevent bloat."""
        print("[MAINTENANCE] Pruning Notification Database...")
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=self.days_to_keep_logs)).strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute("DELETE FROM system_notifications WHERE timestamp <= ?", (cutoff_date,))
            deleted_rows = cursor.rowcount
            
            conn.commit()
            conn.close()
            print(f"[MAINTENANCE] Removed {deleted_rows} stale notifications.")
        except Exception as e:
            print(f"[MAINTENANCE] Error pruning database: {e}")

    def garbage_collect_files(self):
        """Scans directories and deletes files belonging to untracked tickers."""
        print("[MAINTENANCE] Running File Garbage Collection...")
        active_tickers = self._get_active_tickers()
        
        if not active_tickers:
            print("[MAINTENANCE] No active tickers found. Aborting file deletion for safety.")
            return

        directories = [HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR]
        deleted_count = 0
        
        for directory in directories:
            if not os.path.exists(directory):
                continue
                
            for filename in os.listdir(directory):
                if filename in self.protected_files:
                    continue
                    
                # Extract ticker from filename (e.g., 'AAPL_intraday.parquet' -> 'AAPL')
                base_name = filename.split('.')[0]
                ticker = base_name.replace('_intraday', '')
                
                if ticker not in active_tickers:
                    filepath = os.path.join(directory, filename)
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        print(f"  -> Deleted orphaned file: {filename}")
                    except Exception as e:
                        print(f"  -> Failed to delete {filename}: {e}")
                        
        print(f"[MAINTENANCE] File GC complete. Reclaimed {deleted_count} files.")

    def vacuum_database(self):
        """Runs the SQLite VACUUM command to defragment and optimize disk space."""
        print("[MAINTENANCE] Vacuuming SQLite Database...")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("VACUUM")
            conn.close()
            print("[MAINTENANCE] Database Vacuum Complete.")
        except Exception as e:
            print(f"[MAINTENANCE] Error vacuuming database: {e}")

    def run(self):
        print(f"\n--- [MAINTENANCE ENGINE] Initiated @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        self.prune_database_logs()
        self.garbage_collect_files()
        self.vacuum_database()
        print("--- [MAINTENANCE ENGINE] Optimization Complete ---\n")


if __name__ == "__main__":
    engine = MaintenanceEngine()
    engine.run()