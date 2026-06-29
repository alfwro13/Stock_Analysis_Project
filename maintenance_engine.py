import logging
import os
import time
import json
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
from config import (
    PORTFOLIO_PATH, HISTORICAL_DIR,
    INTRADAY_DIR, FUNDAMENTALS_DIR, load_config
)
from database import get_connection, get_watchlist_tickers, log_notification as _db_log_notification
from ghostfolio_sync import purge_ghostfolio_files

class MaintenanceEngine:
    """Weekly housekeeping: prune notification logs, delete orphaned files, VACUUM the DB."""

    def __init__(self):
        self.days_to_keep_logs = 30
        cfg = load_config()
        self.days_to_keep_files = cfg.get("SCHEDULING", {}).get("MAINTENANCE", {}).get("DAYS_TO_KEEP_FILES", 60)
        self.protected_files = {"SP500_BASELINE.parquet", "FTSE_BASELINE.parquet"}
        self.metrics = {
            "logs_deleted": 0,
            "files_deleted": 0,
            "deleted_files": [],
            "pulse_cache_deleted": 0,
            "vacuum_success": False,
            "ghostfolio_files_purged": 0
        }

    def enforce_ghostfolio_disabled(self):
        """Backstop: portfolio.json/watchlist.json must not linger once Ghostfolio integration is disabled."""
        if load_config().get("GHOSTFOLIO_ENABLED", False):
            return
        self.metrics["ghostfolio_files_purged"] = purge_ghostfolio_files()
        if self.metrics["ghostfolio_files_purged"]:
            logger.info("Purged %d stale Ghostfolio file(s) (integration disabled)", self.metrics["ghostfolio_files_purged"])

    def _get_active_tickers(self) -> set:
        """Collects tickers from portfolio JSON, the Watchlist account, and every DB table; any hit → file must not be deleted."""
        active_tickers = set()

        if os.path.exists(PORTFOLIO_PATH):
            try:
                with open(PORTFOLIO_PATH, 'r') as f:
                    data = json.load(f)
                    for v in data.values():
                        if 'ticker' in v:
                            active_tickers.add(v['ticker'])
            except Exception:
                logger.warning("Failed to parse portfolio.json for active tickers", exc_info=True)

        active_tickers.update(get_watchlist_tickers())

        # Universe engine tracks thousands of equities not in portfolio/watchlist whose files we still want.
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
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            for table in ticker_tables:
                try:
                    cursor.execute(f"SELECT DISTINCT ticker FROM {table}")
                    active_tickers.update(row[0] for row in cursor.fetchall() if row[0])
                except Exception:
                    logger.debug("Table %s not present, skipping ticker discovery", table)
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        return active_tickers

    def prune_database_logs(self):
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=self.days_to_keep_logs)).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("DELETE FROM system_notifications WHERE timestamp <= ?", (cutoff_date,))
            self.metrics["logs_deleted"] = cursor.rowcount
            conn.commit()
            logger.info("Removed %d stale notifications", self.metrics["logs_deleted"])
        except Exception as e:
            logger.error("Error pruning notification database: %s", e)
        finally:
            if conn:
                conn.close()

    def prune_pulse_cache(self):
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM market_pulse_cache WHERE last_updated <= ?", (time.time() - 86400,))
            self.metrics["pulse_cache_deleted"] = cursor.rowcount
            conn.commit()
            logger.info("Removed %d stale pulse records", self.metrics["pulse_cache_deleted"])
        except Exception as e:
            logger.error("Error pruning pulse cache: %s", e)
        finally:
            if conn:
                conn.close()

    def garbage_collect_files(self):
        """Deletes orphaned local files (not in portfolio/watchlist/DB and older than DAYS_TO_KEEP_FILES)."""
        active_tickers = self._get_active_tickers()

        if not active_tickers:
            logger.warning("No active tickers found; aborting file deletion for safety")
            return

        cutoff_time = time.time() - (self.days_to_keep_files * 86400)
        directories = [HISTORICAL_DIR, INTRADAY_DIR, FUNDAMENTALS_DIR]

        for directory in directories:
            if not os.path.exists(directory):
                continue
            dir_label = os.path.basename(str(directory))

            for filename in os.listdir(directory):
                if filename in self.protected_files:
                    continue

                # Dots in ticker symbols (e.g. 0P00018XAR.L, RR.) require stripping from the correct end.
                if filename.endswith('_intraday.parquet'):
                    ticker = filename[:-len('_intraday.parquet')]
                elif filename.endswith('.parquet'):
                    ticker = filename[:-len('.parquet')]
                elif filename.endswith('.json'):
                    ticker = filename[:-len('.json')]
                else:
                    continue

                if ticker in active_tickers:
                    continue

                filepath = os.path.join(directory, filename)
                try:
                    file_mtime = os.path.getmtime(filepath)
                    if file_mtime > cutoff_time:
                        continue
                    age_days = int((time.time() - file_mtime) / 86400)
                    os.remove(filepath)
                    self.metrics["files_deleted"] += 1
                    self.metrics["deleted_files"].append(f"{dir_label}/{filename} ({age_days}d old)")
                    logger.debug("Deleted orphaned file: %s", filename)
                except Exception as e:
                    logger.warning("Failed to delete %s: %s", filename, e)

        logger.info("File GC complete; reclaimed %d files", self.metrics["files_deleted"])

    def vacuum_database(self):
        conn = None
        try:
            conn = get_connection()
            # SQLite VACUUM requires an Exclusive Lock and cannot run inside an implicit transaction.
            # Setting isolation_level to None forces Python into autocommit mode.
            conn.isolation_level = None
            conn.execute("VACUUM")
            self.metrics["vacuum_success"] = True
            logger.info("Database vacuum complete")
        except Exception as e:
            # Under heavy load, even a 20-second timeout might fail to acquire an Exclusive Lock.
            # We catch it gracefully rather than crashing the maintenance thread.
            logger.error("Error vacuuming database: %s", e)
        finally:
            if conn:
                conn.close()

    def log_notification(self):
        try:
            vac_status = "Successful" if self.metrics["vacuum_success"] else "Failed (Locked or Busy)"
            deleted = self.metrics["deleted_files"]
            if deleted:
                file_list = "\n".join(f"  ✗ {f}" for f in sorted(deleted))
                files_section = f"\n• Deleted Files ({len(deleted)}):\n{file_list}"
            else:
                files_section = "\n• Deleted Files: none"
            msg = (
                f"Automated System Maintenance completed.\n"
                f"• Stale Logs Trimmed: {self.metrics['logs_deleted']}\n"
                f"• Stale Pulse Cache Records Removed: {self.metrics['pulse_cache_deleted']}"
                f"{files_section}\n"
                f"• Ghostfolio Files Purged (integration disabled): {self.metrics['ghostfolio_files_purged']}\n"
                f"• DB Defragmentation: {vac_status}"
            )
            _db_log_notification("Maintenance", msg)
        except Exception as e:
            logger.error("Failed to write maintenance notification: %s", e)

    def dry_run(self) -> dict:
        """Same scan as garbage_collect_files() but deletes nothing; returns what would/would-not be removed."""
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
        logger.info("Maintenance engine initiated")
        self.prune_database_logs()
        self.prune_pulse_cache()
        self.garbage_collect_files()
        self.enforce_ghostfolio_disabled()
        self.vacuum_database()
        self.log_notification()
        logger.info("Maintenance engine complete")


if __name__ == "__main__":
    engine = MaintenanceEngine()
    engine.run()
