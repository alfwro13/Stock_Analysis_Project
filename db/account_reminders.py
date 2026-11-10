import logging
from typing import List, Optional

from db.database import get_connection

logger = logging.getLogger(__name__)


def create_reminder(account_id: int, reminder_type: str, cadence: str, day_of_month: int, enabled: bool) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO account_reminders (account_id, reminder_type, cadence, day_of_month, enabled) VALUES (?, ?, ?, ?, ?)",
            (account_id, reminder_type, cadence, day_of_month, 1 if enabled else 0),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Failed to create reminder for account %s: %s", account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def update_reminder(reminder_id: int, reminder_type: str, cadence: str, day_of_month: int, enabled: bool) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE account_reminders SET reminder_type = ?, cadence = ?, day_of_month = ?, enabled = ? WHERE id = ?",
            (reminder_type, cadence, day_of_month, 1 if enabled else 0, reminder_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to update reminder %s: %s", reminder_id, e)
        return False
    finally:
        if conn:
            conn.close()


def delete_reminder(reminder_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM account_reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to delete reminder %s: %s", reminder_id, e)
        return False
    finally:
        if conn:
            conn.close()


def get_reminder(reminder_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT * FROM account_reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to read reminder %s: %s", reminder_id, e)
        return None
    finally:
        if conn:
            conn.close()


def get_reminders(account_id: Optional[int] = None) -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        if account_id is None:
            rows = conn.execute("SELECT * FROM account_reminders ORDER BY id").fetchall()
        else:
            rows = conn.execute("SELECT * FROM account_reminders WHERE account_id = ? ORDER BY id", (account_id,)).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error("Failed to list reminders: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def create_pending_reminder(reminder: dict, due_date: str) -> Optional[int]:
    """Returns None when this reminder already has a pending row for `due_date` (a double fire); a database error raises."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO account_reminder_pending (reminder_id, account_id, reminder_type, due_date) VALUES (?, ?, ?, ?)",
            (reminder["id"], reminder["account_id"], reminder["reminder_type"], due_date),
        )
        conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        if conn:
            conn.close()


def get_unresolved_pending_reminders(account_id: Optional[int] = None) -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        if account_id is None:
            rows = conn.execute(
                "SELECT * FROM account_reminder_pending WHERE status = 'pending' ORDER BY due_date, id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM account_reminder_pending WHERE status = 'pending' AND account_id = ? ORDER BY due_date, id",
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error("Failed to list pending reminders: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_pending_reminder(pending_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT * FROM account_reminder_pending WHERE id = ?", (pending_id,)).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to read pending reminder %s: %s", pending_id, e)
        return None
    finally:
        if conn:
            conn.close()


def resolve_pending_reminder(pending_id: int, status: str, txn_id: Optional[int] = None) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE account_reminder_pending SET status = ?, txn_id = ?, resolved_at = datetime('now') "
            "WHERE id = ? AND status = 'pending'",
            (status, txn_id, pending_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to resolve pending reminder %s: %s", pending_id, e)
        return False
    finally:
        if conn:
            conn.close()
