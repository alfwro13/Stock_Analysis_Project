# maintenance_engine.py
import os
import time
import json
import sqlite3
from datetime import datetime, timedelta
from config import (
    PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR, 
    INTRADAY_DIR, FUNDAMENTALS_DIR
)
from database import get_connection

class MaintenanceEngine:
    """
    Automated system cleaner. Trims the notification ledger, 
    deletes orphaned files from sold assets, and vacuums the SQLite database.
    """

    def __init__(self):
        self.days_to_keep_logs = 30
        self.protected_files = ["SP500_BASELINE.parquet", "FTSE_BASELINE.parquet"]
        # Track metrics for the final notification log
        self.metrics = {
            "logs_deleted": 0,
            "files_deleted": 0,
            "pulse_cache_deleted": 0,
            "vacuum_success": False
        }

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
            conn = get_connection()
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=self.days_to_keep_logs)).strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute("DELETE FROM system_notifications WHERE timestamp <= ?", (cutoff_date,))
            self.metrics["logs_deleted"] = cursor.rowcount
            
            conn.commit()
            conn.close()
            print(f"[MAINTENANCE] Removed {self.metrics['logs_deleted']} stale notifications.")
        except Exception as e:
            print(f"[MAINTENANCE] Error pruning database: {e}")

    def prune_pulse_cache(self):
        """Cleans up extremely old records from the live market pulse cache table."""
        print("[MAINTENANCE] Pruning Market Pulse Cache...")
        try:
            conn = get_connection()
            cursor = conn.cursor()
            # Delete records older than 24 hours (86400 seconds)
            cursor.execute("DELETE FROM market_pulse_cache WHERE last_updated <= ?", (time.time() - 86400,))
            self.metrics["pulse_cache_deleted"] = cursor.rowcount
            conn.commit()
            conn.close()
            print(f"[MAINTENANCE] Removed {self.metrics['pulse_cache_deleted']} stale pulse records.")
        except Exception as e:
            print(f"[MAINTENANCE] Error pruning pulse cache: {e}")

    def garbage_collect_files(self):
        """Scans directories and deletes files belonging to untracked tickers older than 30 days."""
        print("[MAINTENANCE] Running File Garbage Collection...")
        active_tickers = self._get_active_tickers()

        if not active_tickers:
            print("[MAINTENANCE] No active tickers found. Aborting file deletion for safety.")
            return

        cutoff_time = time.time() - (self.days_to_keep_logs * 86400)
        directories = [HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR]

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
                        file_mtime = os.path.getmtime(filepath)
                        if file_mtime > cutoff_time:
                            print(f"  -> Skipping {filename} (less than 30 days old)")
                            continue
                        os.remove(filepath)
                        self.metrics["files_deleted"] += 1
                        print(f"  -> Deleted orphaned file: {filename}")
                    except Exception as e:
                        print(f"  -> Failed to delete {filename}: {e}")
                        
        print(f"[MAINTENANCE] File GC complete. Reclaimed {self.metrics['files_deleted']} files.")

    def vacuum_database(self):
        """Runs the SQLite VACUUM command to defragment and optimize disk space."""
        print("[MAINTENANCE] Vacuuming SQLite Database...")
        try:
            conn = get_connection()
            # SQLite VACUUM requires an Exclusive Lock and cannot run inside an implicit transaction.
            # Setting isolation_level to None forces Python into autocommit mode.
            conn.isolation_level = None 
            conn.execute("VACUUM")
            conn.close()
            self.metrics["vacuum_success"] = True
            print("[MAINTENANCE] Database Vacuum Complete.")
        except Exception as e:
            # Under heavy load, even a 20-second timeout might fail to acquire an Exclusive Lock.
            # We catch it gracefully rather than crashing the maintenance thread.
            print(f"[MAINTENANCE] Error vacuuming database: {e}")

    def log_notification(self):
        """Logs the maintenance summary to the internal SQLite notification center."""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            vac_status = "Successful" if self.metrics["vacuum_success"] else "Failed (Locked or Busy)"
            msg = (
                f"Automated System Maintenance completed.\n"
                f"• Stale Logs Trimmed: {self.metrics['logs_deleted']}\n"
                f"• Stale Pulse Cache Records Removed: {self.metrics['pulse_cache_deleted']}\n"
                f"• Orphaned Files Reclaimed: {self.metrics['files_deleted']}\n"
                f"• DB Defragmentation: {vac_status}"
            )
            
            cursor.execute(
                "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", 
                ("Maintenance", msg)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[MAINTENANCE] Failed to write notification to DB: {e}")

    def run(self):
        print(f"\n--- [MAINTENANCE ENGINE] Initiated @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        self.prune_database_logs()
        self.prune_pulse_cache()
        self.garbage_collect_files()
        self.vacuum_database()
        
        # Write the final summary to the Dashboard Notification UI
        self.log_notification()
        
        print("--- [MAINTENANCE ENGINE] Optimization Complete ---\n")


if __name__ == "__main__":
    engine = MaintenanceEngine()
    engine.run()