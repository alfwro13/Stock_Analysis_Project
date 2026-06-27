import logging
from typing import Optional

from database import get_connection

logger = logging.getLogger(__name__)


def get_accounts(include_deleted: bool = False) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if include_deleted:
            cursor.execute("SELECT * FROM accounts ORDER BY created_at")
        else:
            cursor.execute("SELECT * FROM accounts WHERE deleted_at IS NULL ORDER BY created_at")
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get accounts: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_account(account_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM accounts WHERE id = ? AND deleted_at IS NULL",
            (account_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get account %s: %s", account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def create_account(
    name: str,
    currency: str,
    initial_cash: float = 0.0,
    note: Optional[str] = None,
    opened_date: Optional[str] = None,
    account_type: str = "Trading",
) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO accounts (name, currency, initial_cash, note, opened_date, account_type) VALUES (?, ?, ?, ?, ?, ?)",
            (name, currency, initial_cash, note, opened_date, account_type)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Failed to create account: %s", e)
        return None
    finally:
        if conn:
            conn.close()


_ALLOWED_ACCOUNT_COLUMNS = frozenset({"name", "currency", "initial_cash", "note", "opened_date", "account_type"})


def update_account(account_id: int, **fields) -> bool:
    if not fields:
        return True
    unknown = set(fields) - _ALLOWED_ACCOUNT_COLUMNS
    if unknown:
        logger.error("update_account: unknown column(s) rejected: %s", unknown)
        return False
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [account_id]
        cursor.execute(
            f"UPDATE accounts SET {set_clause} WHERE id = ? AND deleted_at IS NULL",
            values
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to update account %s: %s", account_id, e)
        return False
    finally:
        if conn:
            conn.close()


def soft_delete_account(account_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE accounts SET deleted_at = datetime('now') WHERE id = ? AND deleted_at IS NULL",
            (account_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to soft-delete account %s: %s", account_id, e)
        return False
    finally:
        if conn:
            conn.close()


def get_transactions(account_id: int) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_transactions WHERE account_id = ? ORDER BY txn_date, id",
            (account_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get transactions for account %s: %s", account_id, e)
        return []
    finally:
        if conn:
            conn.close()


def get_transaction(txn_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM account_transactions WHERE id = ?", (txn_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get transaction %s: %s", txn_id, e)
        return None
    finally:
        if conn:
            conn.close()


def add_transaction(
    account_id: int,
    txn_type: str,
    txn_date: str,
    ticker: Optional[str] = None,
    isin: Optional[str] = None,
    company_name: Optional[str] = None,
    currency: Optional[str] = None,
    quantity: Optional[float] = None,
    unit_price: Optional[float] = None,
    fee: float = 0.0,
    exchange_rate: Optional[float] = None,
    notes: Optional[str] = None,
    update_cash: bool = True,
    price_in_pence: bool = False,
    ghostfolio_ref: Optional[str] = None,
    linked_txn_id: Optional[int] = None,
) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO account_transactions
                   (account_id, txn_type, ticker, isin, company_name, currency, txn_date,
                    quantity, unit_price, fee, exchange_rate, notes, update_cash,
                    price_in_pence, ghostfolio_ref, linked_txn_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, txn_type, ticker, isin, company_name, currency, txn_date,
             quantity, unit_price, fee, exchange_rate, notes,
             1 if update_cash else 0, 1 if price_in_pence else 0, ghostfolio_ref, linked_txn_id)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Failed to add transaction to account %s: %s", account_id, e)
        return None
    finally:
        if conn:
            conn.close()


_ALLOWED_TXN_COLUMNS = frozenset({
    "txn_type", "ticker", "isin", "company_name", "currency", "txn_date", "quantity",
    "unit_price", "fee", "exchange_rate", "notes", "update_cash", "price_in_pence",
    "linked_txn_id",
})


def update_transaction(txn_id: int, **fields) -> bool:
    if not fields:
        return True
    unknown = set(fields) - _ALLOWED_TXN_COLUMNS
    if unknown:
        logger.error("update_transaction: unknown column(s) rejected: %s", unknown)
        return False
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if "update_cash" in fields:
            fields["update_cash"] = 1 if fields["update_cash"] else 0
        if "price_in_pence" in fields:
            fields["price_in_pence"] = 1 if fields["price_in_pence"] else 0
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [txn_id]
        cursor.execute(
            f"UPDATE account_transactions SET {set_clause} WHERE id = ?",
            values
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to update transaction %s: %s", txn_id, e)
        return False
    finally:
        if conn:
            conn.close()


def delete_transaction(txn_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM account_transactions WHERE id = ?", (txn_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to delete transaction %s: %s", txn_id, e)
        return False
    finally:
        if conn:
            conn.close()


def upsert_value_snapshot(
    account_id: int,
    snapshot_date: str,
    total_value: float,
    cash_value: float,
    equity_value: float,
    net_contributions: float = 0.0,
) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO account_value_history
                   (account_id, snapshot_date, total_value, cash_value, equity_value, net_contributions)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, snapshot_date) DO UPDATE SET
                   total_value = excluded.total_value,
                   cash_value = excluded.cash_value,
                   equity_value = excluded.equity_value,
                   net_contributions = excluded.net_contributions""",
            (account_id, snapshot_date, total_value, cash_value, equity_value, net_contributions)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to upsert value snapshot for account %s: %s", account_id, e)
    finally:
        if conn:
            conn.close()


def get_value_history(account_id: int) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_value_history WHERE account_id = ? ORDER BY snapshot_date",
            (account_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get value history for account %s: %s", account_id, e)
        return []
    finally:
        if conn:
            conn.close()
