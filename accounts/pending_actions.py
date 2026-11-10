from typing import Callable, Dict, List, Optional

from accounts.account_reminders_engine import labelled_pending_reminders
from db.account_store import get_accounts, get_unresolved_pending_topups
from accounts.treasury_bill_engine import bills_pending_ytm_confirmation


def _auto_topups(account_id: Optional[int], accounts: Dict[int, dict]) -> List[dict]:
    items = []
    for row in get_unresolved_pending_topups(account_id):
        account = accounts.get(row["account_id"])
        if account:
            items.append({
                "account_id": row["account_id"],
                "title": "Auto Top-up",
                "detail": f"Due {row['scheduled_date']}, expected {row['expected_amount']:.2f} {account['currency']}.",
            })
    return items


def _pension_reminders(account_id: Optional[int], accounts: Dict[int, dict]) -> List[dict]:
    return [
        {"account_id": row["account_id"], "title": row["title"], "detail": f"{row['title']} reminder is waiting."}
        for row in labelled_pending_reminders(account_id) if row["account_id"] in accounts
    ]


def _treasury_bill_ytm(account_id: Optional[int], accounts: Dict[int, dict]) -> List[dict]:
    return [
        {
            "account_id": row["account_id"],
            "title": "Treasury Bill YTM",
            "detail": f"Confirm the final YTM of the bill maturing {row['maturity_date']}.",
        }
        for row in bills_pending_ytm_confirmation(account_id) if row["account_id"] in accounts
    ]


# A new kind of action that waits for the operator on an account is added here and nowhere else:
# the navbar count, the Accounts tile tags and GET /api/accounts/pending-actions all read this registry.
PENDING_ACTION_KINDS: Dict[str, Dict[str, object]] = {
    "autotopup": {"label": "Auto Top-up", "read": _auto_topups},
    "pension_reminder": {"label": "Pension Reminders", "read": _pension_reminders},
    "treasury_bill_ytm": {"label": "Treasury Bill YTM", "read": _treasury_bill_ytm},
}


def pending_account_actions(account_id: Optional[int] = None) -> List[dict]:
    accounts = {a["id"]: a for a in get_accounts()}
    items: List[dict] = []
    for kind, spec in PENDING_ACTION_KINDS.items():
        read: Callable = spec["read"]
        for item in read(account_id, accounts):
            item["kind"] = kind
            item["kind_label"] = spec["label"]
            item["account_name"] = accounts[item["account_id"]]["name"]
            items.append(item)
    return items
