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
            SELECT date, total_calls, ipv4_calls, ipv6_calls, rate_limit_429, other_errors
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
)
from db_helpers import (  # noqa: E402
    log_score_event,
    get_universe_tickers,
    upsert_quant_signal,
    log_trap_phase,
    get_unresolved_trap_phases,
    update_trap_phase_actual,
    batch_update_trap_phase_actuals,
    get_trap_phase_accuracy,
)

__all__ = [
    "get_connection",
    "log_notification",
    "get_yahoo_api_stats",
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
    "log_score_event",
    "get_universe_tickers",
    "upsert_quant_signal",
    "log_trap_phase",
    "get_unresolved_trap_phases",
    "update_trap_phase_actual",
    "batch_update_trap_phase_actuals",
    "get_trap_phase_accuracy",
]
