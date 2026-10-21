import logging
from typing import List, Tuple

from database import get_connection

logger = logging.getLogger(__name__)


def add_entry(entry: dict) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO mcp_audit_log (logged_at, credential_kind, key_id, client_address, mcp_method, mcp_name, "
            "status, duration_ms, response_bytes) VALUES "
            "(:logged_at, :credential_kind, :key_id, :client_address, :mcp_method, :mcp_name, :status, :duration_ms, :response_bytes)",
            entry,
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def prune_older_than(cutoff: str) -> int:
    conn = None
    try:
        conn = get_connection()
        deleted = conn.execute("DELETE FROM mcp_audit_log WHERE logged_at < ?", (cutoff,)).rowcount
        conn.commit()
        return deleted
    finally:
        if conn:
            conn.close()


def recent_entries(limit: int) -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, logged_at, credential_kind, key_id, client_address, mcp_method, mcp_name, status, duration_ms "
            "FROM mcp_audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if conn:
            conn.close()


def traffic_counts(since: str) -> Tuple[int, int]:
    """(requests, refused requests) logged since the stamp; dashboard actions and the cap's summary rows are not requests."""
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status IN (401, 403, 429)), 0) FROM mcp_audit_log "
            "WHERE logged_at >= ? AND credential_kind != 'session' AND COALESCE(mcp_method, '') != 'audit'",
            (since,),
        ).fetchone()
        return row[0], row[1]
    finally:
        if conn:
            conn.close()
