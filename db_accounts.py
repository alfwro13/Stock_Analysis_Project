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
    pension_start_date: Optional[str] = None,
    opening_balance_units: Optional[float] = None,
    pension_ticker_label: Optional[str] = None,
) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO accounts (name, currency, initial_cash, note, opened_date, account_type, pension_start_date, opening_balance_units, pension_ticker_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, currency, initial_cash, note, opened_date, account_type, pension_start_date, opening_balance_units, pension_ticker_label)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Failed to create account: %s", e)
        return None
    finally:
        if conn:
            conn.close()


_ALLOWED_ACCOUNT_COLUMNS = frozenset({
    "name", "currency", "initial_cash", "note", "opened_date", "account_type",
    "scraper_url", "scraper_selector", "scraper_headers", "scrape_time", "scraper_enabled",
    "pension_start_date", "opening_balance_units", "opening_balance_txn_id",
    "pension_ticker_label",
    "autotopup_enabled", "autotopup_amount", "autotopup_frequency",
    "autotopup_day_of_month", "autotopup_day_of_week", "autotopup_notes",
})


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
    fee_currency: Optional[str] = None,
    fee_exchange_rate: Optional[float] = None,
    notes: Optional[str] = None,
    update_cash: bool = True,
    price_in_pence: bool = False,
    ghostfolio_ref: Optional[str] = None,
    linked_txn_id: Optional[int] = None,
    is_adjustment: bool = False,
) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO account_transactions
                   (account_id, txn_type, ticker, isin, company_name, currency, txn_date,
                    quantity, unit_price, fee, exchange_rate, fee_currency, fee_exchange_rate,
                    notes, update_cash, price_in_pence, ghostfolio_ref, linked_txn_id, is_adjustment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, txn_type, ticker, isin, company_name, currency, txn_date,
             quantity, unit_price, fee, exchange_rate, fee_currency, fee_exchange_rate, notes,
             1 if update_cash else 0, 1 if price_in_pence else 0, ghostfolio_ref, linked_txn_id,
             1 if is_adjustment else 0)
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
    "unit_price", "fee", "exchange_rate", "fee_currency", "fee_exchange_rate", "notes",
    "update_cash", "price_in_pence", "linked_txn_id",
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


def upsert_value_snapshot_currency(
    account_id: int,
    snapshot_date: str,
    currency: str,
    native_value: float,
    base_value: float,
    fx_rate: float,
) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO account_value_history_currency
                   (account_id, snapshot_date, currency, equity_value_native, equity_value_base, fx_rate)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, snapshot_date, currency) DO UPDATE SET
                   equity_value_native = excluded.equity_value_native,
                   equity_value_base = excluded.equity_value_base,
                   fx_rate = excluded.fx_rate""",
            (account_id, snapshot_date, currency, native_value, base_value, fx_rate)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to upsert currency value snapshot for account %s: %s", account_id, e)
    finally:
        if conn:
            conn.close()


def get_value_history_currency(account_id: int) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_value_history_currency WHERE account_id = ? ORDER BY snapshot_date",
            (account_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get currency value history for account %s: %s", account_id, e)
        return []
    finally:
        if conn:
            conn.close()


_PERFORMANCE_CACHE_COLUMNS = (
    "total_value", "equity_value", "cash_balance", "unrealized_pnl",
    "return_1d", "return_1w", "return_1m", "return_3m", "return_6m", "return_1y",
    "mwrr", "last_updated",
)


def upsert_performance_cache(account_id: int, **fields) -> None:
    """Persists the last computed live-performance snapshot for one account, shared by every
    browser/tab that polls it — see accounts_engine.refresh_performance_cache()."""
    columns = [c for c in _PERFORMANCE_CACHE_COLUMNS if c in fields]
    if not columns:
        return
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        col_list = ", ".join(["account_id"] + columns)
        placeholders = ", ".join(["?"] * (len(columns) + 1))
        update_clause = ", ".join(f"{c} = excluded.{c}" for c in columns)
        cursor.execute(
            f"""INSERT INTO account_performance_cache ({col_list})
                   VALUES ({placeholders})
               ON CONFLICT(account_id) DO UPDATE SET {update_clause}""",
            [account_id] + [fields[c] for c in columns]
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to upsert performance cache for account %s: %s", account_id, e)
    finally:
        if conn:
            conn.close()


def get_performance_cache(account_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_performance_cache WHERE account_id = ?",
            (account_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get performance cache for account %s: %s", account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def add_price_history(account_id: int, price_date: str, price: float, source: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO account_price_history (account_id, price_date, price, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account_id, price_date) DO UPDATE SET
                   price = excluded.price,
                   source = excluded.source""",
            (account_id, price_date, price, source)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error("Failed to add price history for account %s: %s", account_id, e)
        return False
    finally:
        if conn:
            conn.close()


def get_price_history(account_id: int) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_price_history WHERE account_id = ? ORDER BY price_date",
            (account_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get price history for account %s: %s", account_id, e)
        return []
    finally:
        if conn:
            conn.close()


def get_latest_price(account_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_price_history WHERE account_id = ? ORDER BY price_date DESC LIMIT 1",
            (account_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get latest price for account %s: %s", account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def get_price_as_of(account_id: int, date_str: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM account_price_history WHERE account_id = ? AND price_date <= ? ORDER BY price_date DESC LIMIT 1",
            (account_id, date_str)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get price as of %s for account %s: %s", date_str, account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def get_watchlist_account() -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE account_type = 'Watchlist' AND deleted_at IS NULL LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get watchlist account: %s", e)
        return None
    finally:
        if conn:
            conn.close()


def get_watchlist_items(account_id: int) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM watchlist_items WHERE account_id = ? ORDER BY ticker", (account_id,))
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get watchlist items for account %s: %s", account_id, e)
        return []
    finally:
        if conn:
            conn.close()


def add_watchlist_item(
    account_id: int,
    ticker: str,
    company_name: Optional[str] = None,
    currency: Optional[str] = None,
    quote_type: Optional[str] = None,
    exchange: Optional[str] = None,
) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR IGNORE INTO watchlist_items
                   (account_id, ticker, company_name, currency, quote_type, exchange)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, ticker, company_name, currency, quote_type, exchange)
        )
        conn.commit()
        if cursor.lastrowid and cursor.rowcount > 0:
            return cursor.lastrowid
        cursor.execute(
            "SELECT id FROM watchlist_items WHERE account_id = ? AND ticker = ?", (account_id, ticker)
        )
        row = cursor.fetchone()
        return row["id"] if row else None
    except Exception as e:
        logger.error("Failed to add watchlist item %s for account %s: %s", ticker, account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def delete_watchlist_items(account_id: int, item_ids: list) -> int:
    if not item_ids:
        return 0
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ", ".join("?" for _ in item_ids)
        cursor.execute(
            f"DELETE FROM watchlist_items WHERE account_id = ? AND id IN ({placeholders})",
            [account_id] + list(item_ids)
        )
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        logger.error("Failed to delete watchlist items %s for account %s: %s", item_ids, account_id, e)
        return 0
    finally:
        if conn:
            conn.close()


def remove_watchlist_ticker(account_id: int, ticker: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM watchlist_items WHERE account_id = ? AND ticker = ?", (account_id, ticker)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to remove watchlist ticker %s for account %s: %s", ticker, account_id, e)
        return False
    finally:
        if conn:
            conn.close()


def get_watchlist_tickers() -> list:
    account = get_watchlist_account()
    if not account:
        return []
    return [item["ticker"] for item in get_watchlist_items(account["id"])]


def get_all_account_tickers() -> list:
    """Distinct tickers backing an actual open/closed holding (Buy/Sell only — Interest/Dividend/
    Fee/Cash rows can carry non-ticker values, e.g. a CSV-imported transaction GUID) across
    non-deleted accounts. Excludes the Pension synthetic 'PENSION-{id}' ticker
    (account_scraper_engine.py), which has no Yahoo Finance listing."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT t.ticker FROM account_transactions t "
            "JOIN accounts a ON a.id = t.account_id "
            "WHERE a.deleted_at IS NULL AND t.txn_type IN ('Buy', 'Sell') "
            "AND t.ticker IS NOT NULL AND t.ticker != '' AND t.ticker NOT LIKE 'PENSION-%'"
        )
        return [row["ticker"] for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get all account tickers: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def create_pending_topup(account_id: int, scheduled_date: str, expected_amount: float) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO account_autotopup_pending (account_id, scheduled_date, expected_amount) VALUES (?, ?, ?)",
            (account_id, scheduled_date, expected_amount)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Failed to create pending top-up for account %s: %s", account_id, e)
        return None
    finally:
        if conn:
            conn.close()


def get_unresolved_pending_topups(account_id: Optional[int] = None) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if account_id is None:
            cursor.execute(
                "SELECT * FROM account_autotopup_pending WHERE status = 'pending' ORDER BY scheduled_date"
            )
        else:
            cursor.execute(
                "SELECT * FROM account_autotopup_pending WHERE status = 'pending' AND account_id = ? ORDER BY scheduled_date",
                (account_id,)
            )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to get unresolved pending top-ups: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_pending_topup(pending_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM account_autotopup_pending WHERE id = ?", (pending_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get pending top-up %s: %s", pending_id, e)
        return None
    finally:
        if conn:
            conn.close()


def resolve_pending_topup(
    pending_id: int,
    status: str,
    confirmed_amount: Optional[float] = None,
    confirmed_date: Optional[str] = None,
    txn_id: Optional[int] = None,
) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE account_autotopup_pending SET status = ?, confirmed_amount = ?, confirmed_date = ?, txn_id = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, confirmed_amount, confirmed_date, txn_id, pending_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to resolve pending top-up %s: %s", pending_id, e)
        return False
    finally:
        if conn:
            conn.close()
