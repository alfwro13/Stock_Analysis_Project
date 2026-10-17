import logging
import sqlite3
from typing import List, Optional

from database import get_connection

logger = logging.getLogger(__name__)


def add_key(name: str, key_hash: str, created_at: str) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO mcp_api_keys (name, key_hash, created_at) VALUES (?, ?, ?)", (name, key_hash, created_at)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        if conn:
            conn.close()


def list_keys() -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("SELECT id, name, created_at, last_used_at FROM mcp_api_keys ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn:
            conn.close()


def key_hashes() -> List[tuple]:
    conn = None
    try:
        conn = get_connection()
        return [(r["id"], r["key_hash"]) for r in conn.execute("SELECT id, key_hash FROM mcp_api_keys")]
    finally:
        if conn:
            conn.close()


def delete_key(key_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        deleted = conn.execute("DELETE FROM mcp_api_keys WHERE id = ?", (key_id,)).rowcount
        conn.commit()
        return deleted > 0
    finally:
        if conn:
            conn.close()


def touch_key(key_id: int, used_at: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute("UPDATE mcp_api_keys SET last_used_at = ? WHERE id = ?", (used_at, key_id))
        conn.commit()
    finally:
        if conn:
            conn.close()
