import logging
from typing import List, Optional, Tuple

from database import get_connection

logger = logging.getLogger(__name__)


def get_client() -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT client_id, secret_hash, created_at, pairing_open_until FROM mcp_oauth_client WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def clear_remote_access(conn, now: str) -> int:
    """Revokes every grant, closes the pairing window and deletes every request, on an open connection; the number of grants revoked."""
    revoked = conn.execute("UPDATE mcp_oauth_grants SET revoked_at = ? WHERE revoked_at IS NULL", (now,)).rowcount
    conn.execute("UPDATE mcp_oauth_client SET pairing_open_until = NULL")
    conn.execute("DELETE FROM mcp_oauth_requests")
    return revoked


def revoke_all(now: str) -> int:
    conn = None
    try:
        conn = get_connection()
        revoked = clear_remote_access(conn, now)
        conn.commit()
        return revoked
    finally:
        if conn:
            conn.close()


def replace_client(client_id: str, secret_hash: str, now: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM mcp_oauth_client")
        conn.execute("INSERT INTO mcp_oauth_client (id, client_id, secret_hash, created_at) VALUES (1, ?, ?, ?)", (client_id, secret_hash, now))
        conn.execute("DELETE FROM mcp_oauth_tokens")
        clear_remote_access(conn, now)
        conn.commit()
    finally:
        if conn:
            conn.close()


def set_pairing_open_until(until: Optional[str]) -> bool:
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute("UPDATE mcp_oauth_client SET pairing_open_until = ? WHERE id = 1", (until,)).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def request_counts(now: str, hour_ago: str) -> Tuple[int, int]:
    """Unexpired pending requests, and requests created since hour_ago."""
    conn = None
    try:
        conn = get_connection()
        pending = conn.execute("SELECT COUNT(*) FROM mcp_oauth_requests WHERE status = 'pending' AND expires_at > ?", (now,)).fetchone()[0]
        recent = conn.execute("SELECT COUNT(*) FROM mcp_oauth_requests WHERE created_at >= ?", (hour_ago,)).fetchone()[0]
        return pending, recent
    finally:
        if conn:
            conn.close()


def count_pending_requests(now: str) -> int:
    conn = None
    try:
        conn = get_connection()
        return conn.execute("SELECT COUNT(*) FROM mcp_oauth_requests WHERE status = 'pending' AND expires_at > ?", (now,)).fetchone()[0]
    finally:
        if conn:
            conn.close()


def insert_request(row: dict, prune_before: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM mcp_oauth_requests WHERE expires_at < ?", (prune_before,))
        conn.execute("DELETE FROM mcp_oauth_tokens WHERE expires_at < ?", (prune_before,))
        conn.execute(
            "INSERT INTO mcp_oauth_requests (id, display_code, client_state, code_challenge, redirect_uri, resource, scope, "
            "created_at, expires_at, requester_address, user_agent, binding_hash) VALUES "
            "(:id, :display_code, :client_state, :code_challenge, :redirect_uri, :resource, :scope, "
            ":created_at, :expires_at, :requester_address, :user_agent, :binding_hash)",
            row,
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def get_request(request_id: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT * FROM mcp_oauth_requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def list_pending_requests(now: str) -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, display_code, created_at, expires_at, requester_address, user_agent FROM mcp_oauth_requests "
            "WHERE status = 'pending' AND expires_at > ? ORDER BY created_at",
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if conn:
            conn.close()


def get_request_by_binding(binding_hash: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT status, display_code, expires_at FROM mcp_oauth_requests WHERE binding_hash = ?", (binding_hash,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def count_requests_since(since: str) -> int:
    conn = None
    try:
        conn = get_connection()
        return conn.execute("SELECT COUNT(*) FROM mcp_oauth_requests WHERE created_at >= ?", (since,)).fetchone()[0]
    finally:
        if conn:
            conn.close()


def count_active_grants(now: str) -> int:
    conn = None
    try:
        conn = get_connection()
        return conn.execute("SELECT COUNT(*) FROM mcp_oauth_grants WHERE revoked_at IS NULL AND expires_at > ?", (now,)).fetchone()[0]
    finally:
        if conn:
            conn.close()


def list_active_grants(now: str, limit: int) -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, client_id, created_at, expires_at, last_used_at, last_address FROM mcp_oauth_grants "
            "WHERE revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (now, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if conn:
            conn.close()


def approve_request(request_id: str, now: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_oauth_requests SET status = 'approved', decided_at = ? WHERE id = ? AND status = 'pending' AND expires_at > ?",
            (now, request_id, now),
        ).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def deny_request(request_id: str, now: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_oauth_requests SET status = 'denied', decided_at = ? WHERE id = ? AND status = 'pending' AND expires_at > ?",
            (now, request_id, now),
        ).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def claim_code(binding_hash: str, code_hash: str, code_expires_at: str, now: str) -> Optional[dict]:
    """Stores the code hash on the approved request of this browser once; the request row, or None when there is nothing to claim."""
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_oauth_requests SET code_hash = ?, code_expires_at = ? "
            "WHERE binding_hash = ? AND status = 'approved' AND code_hash IS NULL AND expires_at > ?",
            (code_hash, code_expires_at, binding_hash, now),
        ).rowcount
        conn.commit()
        if not changed:
            return None
        row = conn.execute("SELECT * FROM mcp_oauth_requests WHERE binding_hash = ? AND code_hash = ?", (binding_hash, code_hash)).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def consume_code(code_hash: str, now: str) -> Tuple[Optional[dict], bool]:
    """(request row, already_used). The first presentation marks the code used atomically; a later one reports reuse."""
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_oauth_requests SET code_used_at = ? WHERE code_hash = ? AND status = 'approved' AND code_used_at IS NULL",
            (now, code_hash),
        ).rowcount
        conn.commit()
        row = conn.execute("SELECT * FROM mcp_oauth_requests WHERE code_hash = ?", (code_hash,)).fetchone()
        return (dict(row) if row else None), bool(row) and not changed
    finally:
        if conn:
            conn.close()


def create_grant(request_id: str, grant: dict, tokens: List[dict]) -> Optional[int]:
    """The grant id, or None when the request was removed or the client replaced since the code was redeemed; the insert holds the write lock, so Revoke All lands wholly before or after."""
    conn = None
    try:
        conn = get_connection()
        grant_id = conn.execute(
            "INSERT INTO mcp_oauth_grants (client_id, scope, resource, created_at, expires_at) "
            "VALUES (:client_id, :scope, :resource, :created_at, :expires_at)",
            grant,
        ).lastrowid
        linked = conn.execute(
            "UPDATE mcp_oauth_requests SET grant_id = ? WHERE id = ? AND status = 'approved' AND grant_id IS NULL", (grant_id, request_id)
        ).rowcount
        current = conn.execute("SELECT client_id FROM mcp_oauth_client WHERE id = 1").fetchone()
        if not linked or current is None or current["client_id"] != grant["client_id"]:
            conn.rollback()
            return None
        _insert_tokens(conn, grant_id, tokens)
        conn.commit()
        return grant_id
    finally:
        if conn:
            conn.close()


def _insert_tokens(conn, grant_id: int, tokens: List[dict]) -> None:
    for token in tokens:
        conn.execute(
            "INSERT INTO mcp_oauth_tokens (token_hash, kind, grant_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token["hash"], token["kind"], grant_id, token["created_at"], token["expires_at"]),
        )


def add_tokens(grant_id: int, tokens: List[dict], prune_before: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM mcp_oauth_tokens WHERE expires_at < ?", (prune_before,))
        _insert_tokens(conn, grant_id, tokens)
        conn.commit()
    finally:
        if conn:
            conn.close()


def get_token(token_hash: str) -> Optional[dict]:
    """The token joined with its grant."""
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT t.kind, t.grant_id, t.expires_at AS token_expires_at, t.rotated_at, g.client_id, g.scope, g.resource, "
            "g.expires_at AS grant_expires_at, g.revoked_at FROM mcp_oauth_tokens t JOIN mcp_oauth_grants g ON g.id = t.grant_id "
            "WHERE t.token_hash = ?",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def rotate_refresh_token(token_hash: str, now: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute(
            "UPDATE mcp_oauth_tokens SET rotated_at = ? WHERE token_hash = ? AND kind = 'refresh' AND rotated_at IS NULL",
            (now, token_hash),
        ).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def revoke_grant(grant_id: int, now: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        changed = conn.execute("UPDATE mcp_oauth_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, grant_id)).rowcount
        conn.commit()
        return changed > 0
    finally:
        if conn:
            conn.close()


def touch_grant(grant_id: int, used_at: str, address: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute("UPDATE mcp_oauth_grants SET last_used_at = ?, last_address = ? WHERE id = ?", (used_at, address, grant_id))
        conn.commit()
    finally:
        if conn:
            conn.close()
