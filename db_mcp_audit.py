import logging

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
