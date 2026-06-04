# maintenance_engine.py
import os
import time
import json
import sqlite3
from datetime import datetime, timedelta
from config import (
    PORTFOLIO_PATH, WATCHLIST_PATH, HISTORICAL_DIR,
    INTRADAY_DIR, FUNDAMENTALS_DIR, load_config
)
from database import get_connection

class MaintenanceEngine:
    """
    Automated system cleaner. Trims the notification ledger, 
    deletes orphaned files from sold assets, and vacuums the SQLite database.
    """

    def __init__(self):
        self.days_to_keep_logs = 30   # notification / pulse-cache retention (days)
        cfg = load_config()
        self.days_to_keep_files = cfg.get("SCHEDULING", {}).get("MAINTENANCE", {}).get("DAYS_TO_KEEP_FILES", 60)
        self.protected_files = {"SP500_BASELINE.parquet", "FTSE_BASELINE.parquet"}
        # Track metrics for the final notification log
        self.metrics = {
            "logs_deleted": 0,
            "files_deleted": 0,
            "pulse_cache_deleted": 0,
            "vacuum_success": False
        }

    def _get_active_tickers(self) -> set:
        """
        Collects every ticker the system knows about — portfolio, watchlist, and
        every DB table that stores per-ticker data.  A ticker present in ANY of
        these sources is considered active; its local files must not be deleted.
        """
        active_tickers = set()

        # 1. Portfolio JSON
        if os.path.exists(PORTFOLIO_PATH):
            try:
                with open(PORTFOLIO_PATH, 'r') as f:
                    data = json.load(f)
                    for v in data.values():
                        if 'ticker' in v:
                            active_tickers.add(v['ticker'])
            except Exception:
                pass

        # 2. Watchlist JSON
        if os.path.exists(WATCHLIST_PATH):
            try:
                with open(WATCHLIST_PATH, 'r') as f:
                    data = json.load(f)
                    if 'watchlist' in data:
                        active_tickers.update(data['watchlist'])
            except Exception:
                pass

        # 3. Every DB table that tracks per-ticker data.
        #    Universe engine alone can track thousands of equities that may not be
        #    in the portfolio/watchlist yet but whose downloaded data we still want.
        ticker_tables = [
            'market_universe',
            'stock_signals',
            'quant_signals',
            'asset_profiles',
            'earnings_volatility',
            'score_history',
            'xray_risk_cache',
            'market_pulse_cache',
            'alert_state',
        ]
        try:
            conn = get_connection()
            cursor = conn.cursor()
            for table in ticker_tables:
                try:
                    cursor.execute(f"SELECT DISTINCT ticker FROM {table}")
                    active_tickers.update(row[0] for row in cursor.fetchall() if row[0])
                except Exception:
                    pass  # table may not exist on older installs
            conn.close()
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
        """
        Deletes local data files only when ALL of these are true:
          - The file is not in the protected set
          - The ticker is not tracked anywhere in the system (portfolio, watchlist, any DB table)
          - The file has not been modified in the last 60 days
        Downloading from Yahoo Finance is expensive; local storage is cheap.
        """
        print("[MAINTENANCE] Running File Garbage Collection...")
        active_tickers = self._get_active_tickers()

        if not active_tickers:
            print("[MAINTENANCE] No active tickers found. Aborting file deletion for safety.")
            return

        cutoff_time = time.time() - (self.days_to_keep_files * 86400)
        directories = [HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR]

        for directory in directories:
            if not os.path.exists(directory):
                continue

            for filename in os.listdir(directory):
                if filename in self.protected_files:
                    continue

                # Robust ticker extraction that handles dots in ticker symbols
                # (e.g. 0P00018XAR.L.parquet, BRK-B.parquet, AAPL_intraday.parquet)
                if filename.endswith('_intraday.parquet'):
                    ticker = filename[:-len('_intraday.parquet')]
                elif filename.endswith('.parquet'):
                    ticker = filename[:-len('.parquet')]
                elif filename.endswith('.json'):
                    ticker = filename[:-len('.json')]
                else:
                    continue  # unknown file type — leave it alone

                if ticker in active_tickers:
                    continue

                filepath = os.path.join(directory, filename)
                try:
                    file_mtime = os.path.getmtime(filepath)
                    if file_mtime > cutoff_time:
                        print(f"  -> Skipping {filename} (less than {self.days_to_keep_files} days old)")
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

    def dry_run(self) -> dict:
        """
        Scans exactly as garbage_collect_files() would, but deletes nothing.
        Returns a dict describing what would be removed and what would be kept,
        so the UI can show the user a preview before committing.
        """
        active_tickers = self._get_active_tickers()
        cutoff_time = time.time() - (self.days_to_keep_files * 86400)
        directories = [HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR]

        would_delete = []
        would_keep_active = []
        would_keep_fresh = []

        for directory in directories:
            if not os.path.exists(directory):
                continue
            dir_label = os.path.basename(str(directory))
            for filename in os.listdir(directory):
                if filename in self.protected_files:
                    continue
                if filename.endswith('_intraday.parquet'):
                    ticker = filename[:-len('_intraday.parquet')]
                elif filename.endswith('.parquet'):
                    ticker = filename[:-len('.parquet')]
                elif filename.endswith('.json'):
                    ticker = filename[:-len('.json')]
                else:
                    continue

                filepath = os.path.join(directory, filename)
                try:
                    file_mtime = os.path.getmtime(filepath)
                    age_days = int((time.time() - file_mtime) / 86400)
                except OSError:
                    continue

                entry = {"file": f"{dir_label}/{filename}", "ticker": ticker, "age_days": age_days}

                if ticker in active_tickers:
                    would_keep_active.append(entry)
                elif file_mtime > cutoff_time:
                    would_keep_fresh.append({**entry, "reason": f"only {age_days}d old (threshold: {self.days_to_keep_files}d)"})
                else:
                    would_delete.append(entry)

        return {
            "days_to_keep_files": self.days_to_keep_files,
            "active_tickers_count": len(active_tickers),
            "would_delete": sorted(would_delete, key=lambda x: x["file"]),
            "would_keep_fresh": sorted(would_keep_fresh, key=lambda x: x["file"]),
            "summary": {
                "delete_count": len(would_delete),
                "keep_active_count": len(would_keep_active),
                "keep_fresh_count": len(would_keep_fresh),
            }
        }

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