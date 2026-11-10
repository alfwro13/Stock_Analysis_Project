import calendar
import logging
from typing import List, Optional

import time_engine
from db.account_reminders import (
    create_pending_reminder, get_pending_reminder, get_reminder, get_unresolved_pending_reminders,
    resolve_pending_reminder,
)
from db.accounts import get_account

logger = logging.getLogger(__name__)

REMINDER_TYPE_LABELS = {"pay_in": "Pay In", "admin_fee": "Admin Fee"}
REMINDER_CADENCES = frozenset({"monthly"})


def reminder_error(reminder_type: str, cadence: str, day_of_month: int) -> Optional[str]:
    if reminder_type not in REMINDER_TYPE_LABELS:
        return "Type must be Pay In or Admin Fee."
    if cadence not in REMINDER_CADENCES:
        return "Cadence must be monthly."
    if not 1 <= day_of_month <= 31:
        return "Day of month must be between 1 and 31."
    return None


def pending_title(reminder_type: str, due_date: str) -> str:
    year, month, _day = due_date.split("-")
    return f"{calendar.month_name[int(month)]} {year} {REMINDER_TYPE_LABELS[reminder_type]}"


def labelled_pending_reminders(account_id: Optional[int] = None) -> List[dict]:
    rows = get_unresolved_pending_reminders(account_id)
    for row in rows:
        row["type_label"] = REMINDER_TYPE_LABELS[row["reminder_type"]]
        row["title"] = pending_title(row["reminder_type"], row["due_date"])
    return rows


def reminder_job_display_name(reminder_id: int, prefix: str) -> str:
    reminder = get_reminder(reminder_id)
    account = get_account(reminder["account_id"]) if reminder else None
    name = f"{prefix} — {account['name'] if account else reminder_id}"
    return f"{name} ({REMINDER_TYPE_LABELS[reminder['reminder_type']]})" if reminder else name


def create_due_reminder(reminder_id: int) -> Optional[dict]:
    """Stages today's occurrence of an enabled reminder; None when there is nothing new to announce."""
    reminder = get_reminder(reminder_id)
    account = get_account(reminder["account_id"]) if reminder else None
    if not reminder or not reminder["enabled"] or not account:
        return None
    due_date = time_engine.now_local().date().isoformat()
    pending_id = create_pending_reminder(reminder, due_date)
    if pending_id is None:
        return None
    return {
        "pending_id": pending_id,
        "account_name": account["name"],
        "title": pending_title(reminder["reminder_type"], due_date),
    }


def pending_reminder_error(account_id: int, pending_id: int, reminder_type: Optional[str] = None) -> Optional[str]:
    pending = get_pending_reminder(pending_id)
    if not pending or pending["account_id"] != account_id or (reminder_type and pending["reminder_type"] != reminder_type):
        return "Pending reminder not found."
    if pending["status"] != "pending":
        return f"This reminder has already been {pending['status']}."
    return None


def dismiss_pending_reminder(account_id: int, pending_id: int) -> Optional[str]:
    error = pending_reminder_error(account_id, pending_id)
    if error is None:
        resolve_pending_reminder(pending_id, "dismissed")
    return error
