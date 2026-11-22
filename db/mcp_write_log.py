from typing import Optional, Tuple

from db.database import get_connection

_COLUMNS = (
    "id", "kind", "credential_kind", "credential_id", "ticker", "account_id", "expected", "proposed", "summary", "status",
    "created_at", "expires_at", "executed_at", "undone_at", "previous_state", "new_state", "undo_of", "failure_reason",
)


def insert(row: dict) -> None:
    conn = None
    try:
        conn = get_connection()
        columns = [c for c in _COLUMNS if c in row]
        conn.execute(
            f"INSERT INTO mcp_write_log ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", [row[c] for c in columns]
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def get(proposal_id: str, credential_kind: str, credential_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM mcp_write_log WHERE id = ? AND credential_kind = ? AND credential_id = ?",
            (proposal_id, credential_kind, credential_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def claim(proposal_id: str, credential_kind: str, credential_id: int, now: str) -> bool:
    """pending -> executing, once: a replay, an expired row or another credential's id changes nothing."""
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_write_log SET status = 'executing' WHERE id = ? AND status = 'pending' AND expires_at > ? "
            "AND credential_kind = ? AND credential_id = ?",
            (proposal_id, now, credential_kind, credential_id),
        ).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def finish(proposal_id: str, status: str, **fields) -> None:
    allowed = [c for c in ("executed_at", "previous_state", "new_state", "failure_reason") if c in fields]
    conn = None
    try:
        conn = get_connection()
        assignments = ", ".join(["status = ?"] + [f"{c} = ?" for c in allowed])
        conn.execute(f"UPDATE mcp_write_log SET {assignments} WHERE id = ?", [status] + [fields[c] for c in allowed] + [proposal_id])
        conn.commit()
    finally:
        if conn:
            conn.close()


def expire(proposal_id: str, now: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute("UPDATE mcp_write_log SET status = 'expired' WHERE id = ? AND status = 'pending' AND expires_at <= ?", (proposal_id, now))
        conn.commit()
    finally:
        if conn:
            conn.close()


def mark_undone(change_id: str, now: str) -> bool:
    """executed -> undone, once."""
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_write_log SET status = 'undone', undone_at = ? WHERE id = ? AND status = 'executed' AND undone_at IS NULL",
            (now, change_id),
        ).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def counts(credential_kind: str, credential_id: int, hour_ago: str, day_start: str, now: str) -> Tuple[int, int, int, int]:
    """Proposals created in the last hour (refusals excluded), pending proposals, changes executed since the UTC day start, and the Watchlist adds among them."""
    conn = None
    try:
        conn = get_connection()
        who = (credential_kind, credential_id)
        proposals = conn.execute(
            "SELECT COUNT(*) FROM mcp_write_log WHERE credential_kind = ? AND credential_id = ? AND created_at >= ? AND status != 'refused'",
            (*who, hour_ago),
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM mcp_write_log WHERE credential_kind = ? AND credential_id = ? AND status = 'pending' AND expires_at > ?",
            (*who, now),
        ).fetchone()[0]
        executed = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(kind IN ('watchlist_add', 'undo_watchlist_remove')), 0) FROM mcp_write_log "
            "WHERE credential_kind = ? AND credential_id = ? AND executed_at >= ?",
            (*who, day_start),
        ).fetchone()
        return proposals, pending, executed[0], executed[1]
    finally:
        if conn:
            conn.close()


def prune_older_than(cutoff: str) -> int:
    conn = None
    try:
        conn = get_connection()
        deleted = conn.execute("DELETE FROM mcp_write_log WHERE created_at < ?", (cutoff,)).rowcount
        conn.commit()
        return deleted
    finally:
        if conn:
            conn.close()
