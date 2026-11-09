import os
import random
import sqlite3
import sys
import threading
import time
import logging
import weakref
from datetime import datetime, timezone
from typing import Callable, List, Optional

from config import DB_PATH, load_config
from constants import DB_DEFERRED_WRITE_QUEUE_MAX, DB_DEFERRED_WRITE_RETRY_SECONDS, DB_WRITE_HOLD_WARN_SECONDS

logger = logging.getLogger(__name__)

_LOCK_RETRY_ATTEMPTS = 3
_LOCK_RETRY_BASE_DELAY = 0.5

_open_writes: dict = {}
_open_writes_lock = threading.Lock()


def is_lock_error(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and "database is locked" in str(error)


def _caller() -> str:
    this_file = _caller.__code__.co_filename
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename == this_file:
        frame = frame.f_back
    if frame is None:
        return "unknown"
    return f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_code.co_name}:{frame.f_lineno}"


def _describe_open_writes() -> str:
    now = time.monotonic()
    holders = []
    with _open_writes_lock:
        for key, entry in list(_open_writes.items()):
            if entry["conn"]() is None:
                del _open_writes[key]
            else:
                holders.append((now - entry["started"], entry))
    if not holders:
        return "none tracked (held by another process or by a connection not opened through get_connection)"
    holders.sort(key=lambda holder: -holder[0])
    return "; ".join(
        f"held {age:.1f}s by {entry['caller']} [{entry['thread']}], first statement: {entry['statement']}"
        for age, entry in holders
    )


def _retry_on_locked(call, *args, **kwargs):
    # A busy_timeout expiry means the write lock was contended for the full 20s already —
    # a handful of short, jittered retries recovers from bursty multi-job pileups (several
    # scheduled jobs writing at once) without the caller having to know retry ever happened.
    for attempt in range(_LOCK_RETRY_ATTEMPTS + 1):
        try:
            return call(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if not is_lock_error(e):
                raise
            final = attempt == _LOCK_RETRY_ATTEMPTS
            if attempt == 0 or final:
                logger.warning(
                    "SQLite write lock not obtained (wait %d of %d) for %s; open write transactions: %s",
                    attempt + 1, _LOCK_RETRY_ATTEMPTS + 1, _caller(), _describe_open_writes(),
                )
            if final:
                raise
            time.sleep(_LOCK_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.2))


class _RetryingCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        result = _retry_on_locked(super().execute, sql, parameters)
        self.connection._track_write(sql)
        return result

    def executemany(self, sql, seq_of_parameters):
        result = _retry_on_locked(super().executemany, sql, seq_of_parameters)
        self.connection._track_write(sql)
        return result


class _RetryingConnection(sqlite3.Connection):
    _write_entry = None

    def cursor(self, factory=_RetryingCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def commit(self):
        result = _retry_on_locked(super().commit)
        self._release_write()
        return result

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._release_write()

    def close(self):
        self._release_write()
        super().close()

    def _track_write(self, sql):
        if self._write_entry is None and self.in_transaction:
            self._write_entry = {
                "conn": weakref.ref(self),
                "started": time.monotonic(),
                "caller": _caller(),
                "thread": threading.current_thread().name,
                "statement": " ".join(sql.split())[:80],
            }
            with _open_writes_lock:
                _open_writes[id(self)] = self._write_entry

    def _release_write(self):
        entry, self._write_entry = self._write_entry, None
        if entry is None:
            return
        with _open_writes_lock:
            _open_writes.pop(id(self), None)
        held = time.monotonic() - entry["started"]
        if held >= DB_WRITE_HOLD_WARN_SECONDS:
            logger.warning(
                "SQLite write transaction held %.1fs by %s [%s], first statement: %s",
                held, entry["caller"], entry["thread"], entry["statement"],
            )


_deferred: list = []
_deferred_lock = threading.Lock()
_replay = threading.local()
_deferred_wake = threading.Event()
_deferred_worker: Optional[threading.Thread] = None


def replaying_deferred_write() -> bool:
    """True while a queued write is being retried; a writer of a value that a newer write may have replaced since consults it to apply its newer-wins guard."""
    return getattr(_replay, "active", False)


def write_or_defer(description: str, write: Callable[[], None]) -> bool:
    """Runs a self-contained write (it opens and closes its own connection); a lock error that survives get_connection()'s retries queues the write for a later retry instead of losing it. Other errors propagate. Returns True when written now."""
    try:
        write()
        return True
    except sqlite3.OperationalError as e:
        if not is_lock_error(e):
            raise
        defer_write(description, write)
        return False


def defer_write(description: str, write: Callable[[], None]) -> None:
    now = time.monotonic()
    with _deferred_lock:
        full = len(_deferred) >= DB_DEFERRED_WRITE_QUEUE_MAX
        if not full:
            _deferred.append({
                "description": description, "write": write, "retries": 0,
                "queued": now, "due": now + DB_DEFERRED_WRITE_RETRY_SECONDS[0],
            })
    if full:
        logger.error("Deferred-write queue is full; dropped: %s", description)
        return
    logger.warning("Write deferred after 'database is locked': %s (retry in %ds)", description, DB_DEFERRED_WRITE_RETRY_SECONDS[0])
    _start_deferred_worker()


def _run_due_deferred(now: Optional[float] = None) -> Optional[float]:
    """Runs every deferred write that is due; returns the seconds until the next one is due, or None when the queue is empty."""
    now = time.monotonic() if now is None else now
    with _deferred_lock:
        due = [item for item in _deferred if item["due"] <= now]
        for item in due:
            _deferred.remove(item)
    for item in due:
        _replay.active = True
        try:
            item["write"]()
        except Exception as e:
            item["retries"] += 1
            if is_lock_error(e) and item["retries"] < len(DB_DEFERRED_WRITE_RETRY_SECONDS):
                delay = DB_DEFERRED_WRITE_RETRY_SECONDS[item["retries"]]
                item["due"] = time.monotonic() + delay
                with _deferred_lock:
                    _deferred.append(item)
                logger.warning("Deferred write still blocked: %s (retry %d, next in %ds)", item["description"], item["retries"], delay)
            else:
                logger.error("Deferred write abandoned after %d failed retries: %s: %s", item["retries"], item["description"], e)
        else:
            logger.warning(
                "Deferred write completed on retry %d, %.0fs after it was queued: %s",
                item["retries"] + 1, time.monotonic() - item["queued"], item["description"],
            )
        finally:
            _replay.active = False
    with _deferred_lock:
        if not _deferred:
            return None
        return max(0.0, min(item["due"] for item in _deferred) - time.monotonic())


def _deferred_worker_loop() -> None:
    while True:
        wait = _run_due_deferred()
        _deferred_wake.wait(wait)
        _deferred_wake.clear()


def _start_deferred_worker() -> None:
    global _deferred_worker
    with _deferred_lock:
        if _deferred_worker is None or not _deferred_worker.is_alive():
            _deferred_worker = threading.Thread(target=_deferred_worker_loop, name="deferred-db-writes", daemon=True)
            _deferred_worker.start()
    _deferred_wake.set()


def get_connection() -> sqlite3.Connection:
    """sqlite3.Row enables column-name access (row['ticker'])."""
    # timeout=20.0 gracefully handles background thread write collisions; _RetryingConnection
    # adds a few extra jittered retries on top for the rarer case where even that 20s is exceeded.
    conn = sqlite3.connect(DB_PATH, timeout=20.0, factory=_RetryingConnection)
    conn.execute('PRAGMA journal_mode=WAL;')   # concurrent reads + writes
    conn.execute('PRAGMA synchronous=NORMAL;')  # significant write-perf gain in WAL mode
    conn.execute('PRAGMA temp_store=MEMORY;')   # keeps temp tables in RAM; avoids disk I/O under heavy scans
    conn.execute('PRAGMA mmap_size=134217728;') # 128 MB memory-map; cuts read latency for warm pages
    conn.row_factory = sqlite3.Row
    return conn


# Message types that don't need operator triage — never counted toward the unread badge.
AUTO_READ_MESSAGE_TYPES = {"Info", "Success", "Scheduler"}


def insert_notification(message_type: str, message_text: str, logged_at: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO system_notifications (timestamp, message_type, message_text, is_read) VALUES (?, ?, ?, ?)",
            (logged_at, message_type, message_text, message_type in AUTO_READ_MESSAGE_TYPES),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def log_notification(message_type: str, message_text: str) -> None:
    logged_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        write_or_defer("in-app notification", lambda: insert_notification(message_type, message_text, logged_at))
    except Exception as e:
        logger.error("Failed to log notification: %s", e)


def get_yahoo_api_stats(days: int = 8) -> list:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("""
            SELECT date, total_calls, ipv4_calls, ipv6_calls, rate_limit_429, other_errors, yfinance_logged_errors
            FROM yahoo_api_stats
            ORDER BY date DESC
            LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get Yahoo API stats: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_yahoo_api_call_log(date_str: str) -> list:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("""
            SELECT substr(call_time, 1, 16) AS minute_ts, job_id, status, COUNT(*) AS call_count,
                   SUM(yf_logged_errors) AS yf_logged_errors
            FROM yahoo_api_call_log
            WHERE date = ?
            GROUP BY minute_ts, job_id, status
            ORDER BY minute_ts ASC
        """, (date_str,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get Yahoo API call log for %s: %s", date_str, e)
        return []
    finally:
        if conn:
            conn.close()


from db_schema import init_db, migrate_db  # noqa: E402
from db_etf import (  # noqa: E402
    get_etf_predictor_configs,
    get_etf_predictor_config,
    create_etf_predictor_config,
    update_etf_predictor_config,
    soft_delete_etf_predictor_config,
    log_etf_prediction,
    fill_etf_actual,
    get_etf_accuracy,
    get_recent_prediction_errors,
)
from db_helpers import (  # noqa: E402
    log_score_event,
    get_universe_tickers,
    get_ml_inference_tickers,
    get_stock_signal_tickers,
    get_mutual_fund_tickers,
    get_portfolio_watchlist_tickers,
    upsert_quant_signal,
    log_trap_phase,
    get_unresolved_trap_phases,
    update_trap_phase_actual,
    batch_update_trap_phase_actuals,
    get_trap_phase_accuracy,
    log_pattern_detection,
    get_unresolved_pattern_detections,
    batch_update_pattern_detection_actuals,
    get_pattern_detection_accuracy,
    get_unresolved_predicted_movers,
    batch_update_predicted_movers_actuals,
    get_predicted_movers_accuracy,
    get_auction_summary,
    get_ticker_registry,
    get_registry_spot_future_tickers,
    get_ticker_registry_row,
    get_ticker_registry_row_by_future,
    get_ticker_registry_row_by_exchange,
    upsert_ticker_registry_row,
    soft_delete_ticker_registry_row,
    get_instrument_sessions,
    upsert_instrument_session,
    record_nextcloud_talk_attempt,
    get_nextcloud_talk_today,
)
from db_accounts import (  # noqa: E402
    get_accounts,
    get_account,
    get_scraper_headers,
    create_account,
    update_account,
    soft_delete_account,
    get_transactions,
    get_transaction,
    add_transaction,
    update_transaction,
    delete_transaction,
    upsert_value_snapshot,
    get_value_history,
    upsert_value_snapshot_currency,
    get_value_history_currency,
    upsert_performance_cache,
    get_performance_cache,
    add_price_history,
    get_price_history,
    get_latest_price,
    get_price_as_of,
    get_watchlist_account,
    get_watchlist_items,
    add_watchlist_item,
    delete_watchlist_items,
    remove_watchlist_ticker,
    get_watchlist_tickers,
    get_all_account_tickers,
    get_account_traded_tickers,
    create_pending_topup,
    get_unresolved_pending_topups,
    get_pending_topup,
    resolve_pending_topup,
    get_treasury_bill,
    update_treasury_bill_auto_reinvest,
    get_benchmark_tickers,
    replace_benchmark_tickers,
)

__all__ = [
    "get_connection",
    "log_notification",
    "get_yahoo_api_stats",
    "get_yahoo_api_call_log",
    "init_db",
    "migrate_db",
    "get_etf_predictor_configs",
    "get_etf_predictor_config",
    "create_etf_predictor_config",
    "update_etf_predictor_config",
    "soft_delete_etf_predictor_config",
    "log_etf_prediction",
    "fill_etf_actual",
    "get_etf_accuracy",
    "get_recent_prediction_errors",
    "log_score_event",
    "get_universe_tickers",
    "get_ml_inference_tickers",
    "get_stock_signal_tickers",
    "get_mutual_fund_tickers",
    "get_portfolio_watchlist_tickers",
    "upsert_quant_signal",
    "log_trap_phase",
    "get_unresolved_trap_phases",
    "update_trap_phase_actual",
    "batch_update_trap_phase_actuals",
    "get_trap_phase_accuracy",
    "log_pattern_detection",
    "get_unresolved_pattern_detections",
    "batch_update_pattern_detection_actuals",
    "get_pattern_detection_accuracy",
    "get_unresolved_predicted_movers",
    "batch_update_predicted_movers_actuals",
    "get_predicted_movers_accuracy",
    "get_auction_summary",
    "get_accounts",
    "get_account",
    "get_scraper_headers",
    "create_account",
    "update_account",
    "soft_delete_account",
    "get_transactions",
    "get_transaction",
    "add_transaction",
    "update_transaction",
    "delete_transaction",
    "upsert_value_snapshot",
    "get_value_history",
    "upsert_value_snapshot_currency",
    "get_value_history_currency",
    "upsert_performance_cache",
    "get_performance_cache",
    "add_price_history",
    "get_price_history",
    "get_latest_price",
    "get_price_as_of",
    "get_watchlist_account",
    "get_watchlist_items",
    "add_watchlist_item",
    "delete_watchlist_items",
    "remove_watchlist_ticker",
    "get_watchlist_tickers",
    "get_all_account_tickers",
    "get_account_traded_tickers",
    "create_pending_topup",
    "get_unresolved_pending_topups",
    "get_pending_topup",
    "resolve_pending_topup",
    "get_treasury_bill",
    "update_treasury_bill_auto_reinvest",
    "get_benchmark_tickers",
    "replace_benchmark_tickers",
]
