import sqlite3
import logging
from typing import List, Optional

from config import DB_PATH, load_config

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """sqlite3.Row enables column-name access (row['ticker'])."""
    # timeout=20.0 gracefully handles background thread write collisions
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute('PRAGMA journal_mode=WAL;')   # concurrent reads + writes
    conn.execute('PRAGMA synchronous=NORMAL;')  # significant write-perf gain in WAL mode
    conn.execute('PRAGMA temp_store=MEMORY;')   # keeps temp tables in RAM; avoids disk I/O under heavy scans
    conn.execute('PRAGMA mmap_size=134217728;') # 128 MB memory-map; cuts read latency for warm pages
    conn.row_factory = sqlite3.Row
    return conn


def log_notification(message_type: str, message_text: str) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to log notification: %s", e)
    finally:
        if conn:
            conn.close()


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
    get_mutual_fund_tickers,
    upsert_quant_signal,
    log_trap_phase,
    get_unresolved_trap_phases,
    update_trap_phase_actual,
    batch_update_trap_phase_actuals,
    get_trap_phase_accuracy,
    get_auction_summary,
    get_ticker_registry,
    get_ticker_registry_row,
    get_ticker_registry_row_by_future,
    upsert_ticker_registry_row,
    soft_delete_ticker_registry_row,
)
from db_accounts import (  # noqa: E402
    get_accounts,
    get_account,
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
    "get_mutual_fund_tickers",
    "upsert_quant_signal",
    "log_trap_phase",
    "get_unresolved_trap_phases",
    "update_trap_phase_actual",
    "batch_update_trap_phase_actuals",
    "get_trap_phase_accuracy",
    "get_auction_summary",
    "get_accounts",
    "get_account",
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
    "create_pending_topup",
    "get_unresolved_pending_topups",
    "get_pending_topup",
    "resolve_pending_topup",
    "get_treasury_bill",
    "update_treasury_bill_auto_reinvest",
    "get_benchmark_tickers",
    "replace_benchmark_tickers",
]
