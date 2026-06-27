# GUI name: "Accounts". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

import time_engine
from config import BASE_CURRENCY, HISTORICAL_DIR, PORTFOLIO_PATH
from db_accounts import (
    add_transaction, delete_transaction, get_account, get_accounts, get_transaction,
    get_transactions, update_transaction, upsert_value_snapshot,
)
from database import get_connection
from portfolio_service import get_rate_to_base
from utils import normalize_ticker
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

_EPS = 1e-9

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def is_unresolved_ticker(ticker: Optional[str]) -> bool:
    """Ghostfolio reports a raw asset UUID as `symbol` for custom/manual assets with no real market ticker."""
    return bool(ticker) and bool(_UUID_RE.match(ticker))


def resolve_watchlist_metadata(ticker: str) -> dict:
    """Authoritative per-ticker metadata for a watchlist insert; exchange always comes from time_engine, never Yahoo's free-text exchDisp."""
    info = yahoo_engine.get_ticker_info(ticker) or {}
    currency = info.get("currency")
    return {
        "company_name": info.get("longName") or info.get("shortName"),
        "currency": currency,
        "quote_type": info.get("quoteType"),
        "exchange": time_engine.ticker_exchange(ticker, currency) if currency else None,
    }


def _ticker_known(ticker: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT 1 FROM asset_profiles WHERE ticker = ?", (ticker,)).fetchone()
        return row is not None
    except Exception as e:
        logger.error("_ticker_known check failed for %s: %s", ticker, e)
        return True
    finally:
        if conn:
            conn.close()


def _fx(txn) -> float:
    rate = txn["exchange_rate"]
    return rate if rate is not None else 1.0


def _gross_base(txn) -> float:
    """Monetary value of a transaction in base currency (qty defaults to 1 for cash-type rows)."""
    qty = txn["quantity"] if txn["quantity"] is not None else 1.0
    price = txn["unit_price"] or 0.0
    return qty * price * _fx(txn)


def _cash_delta(txn) -> float:
    gross = _gross_base(txn)
    fee_base = (txn["fee"] or 0.0) * _fx(txn)
    ttype = txn["txn_type"]
    if ttype in ("Buy", "Fee"):
        return -(gross + fee_base)
    if ttype in ("Sell", "Dividend", "Interest", "Cash", "Transfer"):
        return gross - fee_base
    return 0.0


def _ledger_for_account(account_id: int, as_of_date: Optional[str] = None, transactions: Optional[list] = None):
    """Average-cost pass per ticker → (open_holdings, closed_positions, realized_total_base,
    realized_by_txn_id). `as_of_date` restricts the pass to transactions on/before that date (used
    by the historical backfill); `transactions` lets a caller reuse an already-fetched list across
    many dates. `realized_by_txn_id` keys each individual Sell row's own realized P&L by `id`, for
    callers (e.g. the CSV export) that want per-transaction rather than per-ticker figures."""
    by_ticker: dict[str, list] = {}
    for txn in (transactions if transactions is not None else get_transactions(account_id)):
        if as_of_date and txn["txn_date"] > as_of_date:
            continue
        if txn["txn_type"] not in ("Buy", "Sell") or not txn["ticker"]:
            continue
        by_ticker.setdefault(txn["ticker"], []).append(txn)

    open_holdings: dict[str, dict] = {}
    closed: list[dict] = []
    realized_total = 0.0
    realized_by_txn_id: dict[int, float] = {}

    for ticker, rows in by_ticker.items():
        shares = 0.0
        cost_base = 0.0
        realized = 0.0
        bought = 0.0
        sold = 0.0
        currency = None
        company = None
        pence = False
        for txn in rows:
            qty = txn["quantity"] or 0.0
            unit = txn["unit_price"] or 0.0
            fx = _fx(txn)
            currency = txn["currency"] or currency
            company = txn["company_name"] or company
            pence = bool(txn["price_in_pence"]) or pence
            if txn["txn_type"] == "Buy":
                cost_base += qty * unit * fx
                shares += qty
                bought += qty
            else:
                sell_realized = 0.0
                if shares > _EPS:
                    avg = cost_base / shares
                    sell_qty = min(qty, shares)
                    sell_realized = sell_qty * (unit * fx - avg)
                    realized += sell_realized
                    cost_base -= sell_qty * avg
                    shares -= sell_qty
                sold += qty
                realized_by_txn_id[txn["id"]] = round(sell_realized, 2)

        realized_total += realized
        if shares > 1e-6:
            open_holdings[ticker] = {
                "ticker": ticker,
                "company_name": company or ticker,
                "currency": currency,
                "price_in_pence": pence,
                "shares": round(shares, 6),
                "buy_price": round(cost_base / shares, 4),
                "total_investment": round(cost_base, 2),
            }
        if sold > _EPS:
            closed.append({
                "ticker": ticker,
                "company_name": company or ticker,
                "currency": currency,
                "bought_qty": round(bought, 6),
                "sold_qty": round(sold, 6),
                "remaining_qty": round(shares, 6),
                "realized_pnl": round(realized, 2),
                "first_date": rows[0]["txn_date"],
                "last_date": rows[-1]["txn_date"],
            })

    return open_holdings, closed, round(realized_total, 2), realized_by_txn_id


def derive_account_holdings(account_id: Optional[int] = None) -> dict:
    """Current open holdings across one account (or all when account_id is None), keyed by ticker,
    in the portfolio.json shape (cost basis in BASE currency)."""
    if account_id is not None:
        account_ids = [account_id]
    else:
        account_ids = [acc["id"] for acc in get_accounts() if acc["account_type"] == "Trading"]

    result: dict[str, dict] = {}
    for aid in account_ids:
        acc = get_account(aid)
        if not acc:
            continue
        open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(aid)
        for ticker, holding in open_holdings.items():
            acc_entry = {
                "id": f"acct:{aid}",
                "name": acc["name"],
                "shares": holding["shares"],
                "buy_price": holding["buy_price"],
                "total_investment": holding["total_investment"],
            }
            entry = result.get(ticker)
            if entry is None:
                result[ticker] = {
                    "ticker": ticker,
                    "company_name": holding["company_name"],
                    "currency": holding["currency"],
                    "price_in_pence": holding["price_in_pence"],
                    "global_shares": holding["shares"],
                    "global_buy_price": holding["buy_price"],
                    "accounts": [acc_entry],
                }
            else:
                entry["accounts"].append(acc_entry)
                _recompute_globals(entry)
    return result


def closed_positions(account_id: int) -> list:
    _open, closed, _realized, _realized_by_txn = _ledger_for_account(account_id)
    return closed


def transaction_total_base(txn) -> float:
    """Total transaction value in BASE_CURRENCY (quantity * unit_price * exchange_rate). Surfaces
    the same conversion `_cash_delta` already does internally, so a ledger mixing GBp/USD/GBP rows
    can be read in one consistent currency on the Activities table and CSV export."""
    return round(_gross_base(txn), 2)


_CSV_EXPORT_TYPE_MAP = {"Buy": "ORDER", "Sell": "ORDER", "Cash": "TOP_UP", "Interest": "INTEREST_FROM_CASH", "Dividend": "DIVIDEND"}
_CSV_EXPORT_SHARE_TYPES = ("Buy", "Sell", "Dividend")


def export_transactions_csv(account_id: int) -> str:
    """Full transaction ledger as a CSV whose columns deliberately mirror the GIA-style file the
    Import from CSV control accepts (see assets/csv_import_format.md), so an export doubles as a
    practical backup/restore file rather than just a verification aid. `Stamp Duty` and `Dividend
    Withheld Tax Amount` aren't tracked as separate ledger fields, so both collapse into the single
    `Fee` column here — restoring via Import from CSV needs that header split back out by hand.
    `Fee`/`Transfer` rows have no GIA `Type` equivalent and are written as `FEE`/`TRANSFER`, which
    the importer skips on re-import exactly as it already skips `INTERNAL_TRANSFER`. `Position` is
    `closed` only once a ticker has been fully exited (no shares left) — blank otherwise, including
    while still partially held."""
    acc = get_account(account_id)
    if not acc:
        return ""
    transactions = get_transactions(account_id)
    _open_holdings, closed, _realized, realized_by_txn = _ledger_for_account(account_id)
    fully_exited = {c["ticker"] for c in closed if abs(c["remaining_qty"]) < 1e-6}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Title", "Type", "Timestamp", "Account Currency", "Total Amount in Account Currency",
        "Buy / Sell", "Ticker", "ISIN", "Price per Share in Account Currency", "Fee", "Quantity",
        "Instrument Currency", "Price per Share", "Dividend Net Amount", "FX Rate", "Position",
        "Total Amount in Instrument Currency", "Realized P&L (Account Currency)", "Notes",
        "Account Name", "Transaction ID",
    ])
    for t in transactions:
        ttype = t["txn_type"]
        qty = t["quantity"]
        price = t["unit_price"]
        fee = t["fee"] or 0.0
        fx = _fx(t)
        is_share_row = ttype in _CSV_EXPORT_SHARE_TYPES
        qty_for_total = qty if qty is not None else 1.0

        writer.writerow([
            t["company_name"] or t["notes"] or "",
            _CSV_EXPORT_TYPE_MAP.get(ttype, ttype.upper()),
            datetime.strptime(t["txn_date"], "%Y-%m-%d").strftime("%d/%m/%Y"),
            acc["currency"],
            round(_gross_base(t), 4),
            "BUY" if ttype == "Buy" else ("SELL" if ttype == "Sell" else ""),
            t["ticker"] or "",
            t["isin"] or "",
            round(price * fx, 4) if is_share_row and price is not None else "",
            round(fee, 6),
            qty if qty is not None else "",
            t["currency"] or "",
            price if is_share_row and price is not None else "",
            round((qty or 0.0) * (price or 0.0) - fee, 4) if ttype == "Dividend" else "",
            fx,
            "closed" if t["ticker"] and t["ticker"] in fully_exited else "",
            round(qty_for_total * (price or 0.0), 4),
            realized_by_txn.get(t["id"], "") if ttype == "Sell" else "",
            t["notes"] or "",
            acc["name"],
            t["id"],
        ])
    return buf.getvalue()


def holdings_with_market_value(account_id: int) -> list:
    """Open holdings for one account enriched with current price, market value, allocation %,
    and unrealized performance — shape required by the account detail page's Holdings table."""
    holdings = derive_account_holdings(account_id)
    if not holdings:
        return []
    prices = _current_price_map(list(holdings.keys()))
    first_dates: dict[str, str] = {}
    for txn in get_transactions(account_id):
        if txn["txn_type"] == "Buy" and txn["ticker"]:
            first_dates.setdefault(txn["ticker"], txn["txn_date"])

    rows = []
    for ticker, h in holdings.items():
        total_investment = h["accounts"][0]["total_investment"]
        priced = prices.get(ticker)
        if priced and priced[0]:
            price, currency = priced
            market_value = h["global_shares"] * price * get_rate_to_base(currency or h["currency"])
        else:
            market_value = total_investment
        rows.append({
            "ticker": ticker,
            "company_name": h["company_name"],
            "currency": h["currency"],
            "first_activity": first_dates.get(ticker),
            "shares": h["global_shares"],
            "buy_price": h["global_buy_price"],
            "market_value": round(market_value, 2),
            "total_investment": total_investment,
            "performance_pct": round((market_value / total_investment - 1) * 100, 2) if total_investment else 0.0,
        })

    total_value = sum(r["market_value"] for r in rows) or 1.0
    for r in rows:
        r["allocation_pct"] = round(r["market_value"] / total_value * 100, 2)
    return rows


def _recompute_globals(entry: dict) -> None:
    total_shares = sum(a.get("shares", 0.0) for a in entry["accounts"])
    total_inv = sum(a.get("total_investment", 0.0) for a in entry["accounts"])
    entry["global_shares"] = round(total_shares, 6)
    entry["global_buy_price"] = round(total_inv / total_shares, 4) if total_shares else 0.0


def _read_portfolio_json() -> dict:
    try:
        with open(PORTFOLIO_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def get_combined_holdings() -> dict:
    """Merge Ghostfolio holdings (portfolio.json) + built-in account holdings, keyed by ticker.
    Same ticker from both sources is summed and both account entries are listed (coexistence)."""
    combined: dict[str, dict] = {}

    for value in _read_portfolio_json().values():
        ticker = value.get("ticker")
        if not ticker:
            continue
        entry = {
            "ticker": ticker,
            "company_name": value.get("company_name", ticker),
            "currency": value.get("currency"),
            "price_in_pence": value.get("price_in_pence", False),
            "global_shares": value.get("global_shares", 0.0),
            "global_buy_price": value.get("global_buy_price", 0.0),
            "accounts": [dict(a) for a in value.get("accounts", [])],
        }
        if ticker in combined:
            _merge_into(combined[ticker], entry)
        else:
            combined[ticker] = entry

    for ticker, entry in derive_account_holdings(None).items():
        if ticker in combined:
            _merge_into(combined[ticker], entry)
        else:
            combined[ticker] = entry

    return combined


def _merge_into(base: dict, extra: dict) -> None:
    base["accounts"].extend(extra.get("accounts", []))
    _recompute_globals(base)
    if extra.get("price_in_pence"):
        base["price_in_pence"] = True


def cash_balance(account_id: int) -> float:
    acc = get_account(account_id)
    if not acc:
        return 0.0
    balance = acc["initial_cash"] or 0.0
    for txn in get_transactions(account_id):
        if txn["update_cash"]:
            balance += _cash_delta(txn)
    return round(balance, 2)


def _cash_balance_as_of(acc: dict, transactions: list, as_of_date: str) -> float:
    balance = acc["initial_cash"] or 0.0
    for txn in transactions:
        if txn["txn_date"] > as_of_date:
            continue
        if txn["update_cash"]:
            balance += _cash_delta(txn)
    return balance


_CONTRIBUTION_TYPES = ("Cash", "Transfer")


def net_contributions(account_id: int) -> float:
    """Cumulative money put into (or taken out of) the account — `initial_cash` plus every
    Cash/Transfer movement — deliberately excluding Buy/Sell/Dividend/Interest/Fee, which are
    investment activity rather than money moved in or out. Comparing this against equity+cash
    (`total_value`) shows at a glance whether the account is up or down versus what was put in."""
    acc = get_account(account_id)
    if not acc:
        return 0.0
    total = acc["initial_cash"] or 0.0
    for txn in get_transactions(account_id):
        if txn["update_cash"] and txn["txn_type"] in _CONTRIBUTION_TYPES:
            total += _cash_delta(txn)
    return round(total, 2)


def _net_contributions_as_of(acc: dict, transactions: list, as_of_date: str) -> float:
    total = acc["initial_cash"] or 0.0
    for txn in transactions:
        if txn["txn_date"] > as_of_date:
            continue
        if txn["update_cash"] and txn["txn_type"] in _CONTRIBUTION_TYPES:
            total += _cash_delta(txn)
    return total


def cash_history(account_id: int) -> list:
    """Running cash balance after each cash-affecting transaction. The opening row (`txn_id=None`)
    is the account's `initial_cash` baseline, not a transaction — it has nothing to edit/delete."""
    acc = get_account(account_id)
    if not acc:
        return []
    balance = acc["initial_cash"] or 0.0
    opening_date = acc["opened_date"] or (acc["created_at"][:10] if acc["created_at"] else None)
    history = [{"date": opening_date, "balance": round(balance, 2), "txn_id": None, "txn_type": None}]
    for txn in get_transactions(account_id):
        if txn["update_cash"]:
            balance += _cash_delta(txn)
            history.append({
                "date": txn["txn_date"],
                "balance": round(balance, 2),
                "txn_id": txn["id"],
                "txn_type": txn["txn_type"],
            })
    return history


def create_transfer(
    from_account_id: int,
    to_account_id: int,
    amount: float,
    txn_date: str,
    fee: float = 0.0,
    notes: Optional[str] = None,
) -> dict:
    """Records a cash transfer as two linked `Transfer` rows — a negative leg on the source account
    and a positive leg on the destination — so each side's cash_balance() reflects it correctly with
    no special-cased sign logic (same convention as the `Cash` type: amount sign is the direction).
    The two rows reference each other via `linked_txn_id` so delete_transaction_with_pair() can keep
    them in sync; the pair is otherwise treated as immutable (edit by delete + recreate)."""
    from_acc = get_account(from_account_id)
    to_acc = get_account(to_account_id)
    if not from_acc or not to_acc:
        return {"error": "Account not found."}
    if from_account_id == to_account_id:
        return {"error": "Cannot transfer to the same account."}
    amount = abs(amount)

    out_id = add_transaction(
        from_account_id, "Transfer", txn_date, currency=from_acc["currency"],
        quantity=1, unit_price=-amount, fee=fee, exchange_rate=1.0,
        notes=notes or f"Transfer to {to_acc['name']}",
    )
    if out_id is None:
        return {"error": "Failed to record the outgoing transfer leg."}

    in_id = add_transaction(
        to_account_id, "Transfer", txn_date, currency=to_acc["currency"],
        quantity=1, unit_price=amount, fee=0.0, exchange_rate=1.0,
        notes=notes or f"Transfer from {from_acc['name']}", linked_txn_id=out_id,
    )
    if in_id is None:
        delete_transaction(out_id)
        return {"error": "Failed to record the incoming transfer leg."}

    update_transaction(out_id, linked_txn_id=in_id)
    return {"out_txn_id": out_id, "in_txn_id": in_id}


def delete_transaction_with_pair(txn_id: int) -> bool:
    """Deletes a transaction; if it is one leg of a Transfer, also deletes the linked sibling leg so
    a transfer is never left half-deleted (which would silently unbalance both accounts' cash)."""
    txn = get_transaction(txn_id)
    if txn is None:
        return False
    ok = delete_transaction(txn_id)
    if ok and txn["txn_type"] == "Transfer" and txn["linked_txn_id"]:
        delete_transaction(txn["linked_txn_id"])
    return ok


def _current_price_map(tickers: list) -> dict:
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"SELECT ticker, current_price, currency FROM stock_signals WHERE ticker IN ({placeholders})",
            tickers
        )
        return {r["ticker"]: (r["current_price"], r["currency"]) for r in cursor.fetchall()}
    except Exception as e:
        logger.error("Failed to load current prices: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def _equity_value(open_holdings: dict) -> float:
    if not open_holdings:
        return 0.0
    prices = _current_price_map(list(open_holdings.keys()))
    total = 0.0
    for ticker, holding in open_holdings.items():
        priced = prices.get(ticker)
        if not priced or not priced[0]:
            total += holding["total_investment"]
            continue
        price, currency = priced
        total += holding["shares"] * price * get_rate_to_base(currency or holding["currency"])
    return total


def account_summary(account_id: int) -> dict:
    acc = get_account(account_id)
    if not acc:
        return {}
    transactions = get_transactions(account_id)
    interest = sum(_gross_base(t) for t in transactions if t["txn_type"] == "Interest")
    dividend = sum(_gross_base(t) for t in transactions if t["txn_type"] == "Dividend")
    open_holdings, _closed, realized, _realized_by_txn = _ledger_for_account(account_id)
    return {
        "account_id": account_id,
        "name": acc["name"],
        "currency": acc["currency"],
        "note": acc["note"],
        "cash_balance": cash_balance(account_id),
        "interest": round(interest, 2),
        "dividend": round(dividend, 2),
        "activity_count": len(transactions),
        "equity_value": round(_equity_value(open_holdings), 2),
        "realized_pnl": realized,
    }


def fx_rate_on_date(currency: str, date_str: Optional[str]) -> float:
    """Historical FX rate from `currency` to BASE_CURRENCY on `date_str`; used to backfill the
    exchange rate when a transaction is entered without one. Falls back to the live rate, then 1.0."""
    if not currency or currency == BASE_CURRENCY:
        return 1.0
    if currency == "GBp":
        return 0.01 if BASE_CURRENCY == "GBP" else 0.01 * fx_rate_on_date("GBP", date_str)
    pair = f"{currency}{BASE_CURRENCY}=X"
    if date_str:
        try:
            history = yahoo_engine.get_price_history([pair], period="5y", interval="1d")
            df = history.get(pair)
            if df is not None and not df.empty and "Close" in df:
                window = df.loc[:date_str]
                if not window.empty:
                    value = float(window["Close"].iloc[-1])
                    if value > 0:
                        return value
        except Exception as e:
            logger.warning("Historical FX lookup failed for %s on %s: %s", pair, date_str, e)
    rate = get_rate_to_base(currency)
    return rate if rate else 1.0


def snapshot_all_accounts() -> int:
    """Nightly job body: writes today's value snapshot for every account. Returns rows written."""
    today = datetime.now(timezone.utc).date().isoformat()
    written = 0
    for acc in get_accounts():
        aid = acc["id"]
        open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(aid)
        equity = _equity_value(open_holdings)
        cash = cash_balance(aid)
        contributions = net_contributions(aid)
        upsert_value_snapshot(aid, today, round(cash + equity, 2), round(cash, 2), round(equity, 2), contributions)
        written += 1
    return written


def resnapshot_account(account_id: int) -> None:
    """Recomputes an account's entire value history (including today), so the chart and Net
    Contributions line reflect a transaction change immediately rather than waiting for the
    nightly snapshot job. Called as a background task after every transaction/transfer/import."""
    backfill_value_history(account_id)
    acc = get_account(account_id)
    if not acc:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(account_id)
    equity = _equity_value(open_holdings)
    cash = cash_balance(account_id)
    contributions = net_contributions(account_id)
    upsert_value_snapshot(account_id, today, round(cash + equity, 2), round(cash, 2), round(equity, 2), contributions)


def backfill_value_history(account_id: int) -> int:
    """One-time historical snapshot fill from Parquet so the account-value chart has data
    immediately on account create/import, covering every day from the earliest transaction up to
    yesterday (today is left to the nightly snapshot job). Returns rows written; 0 if there are no
    transactions yet."""
    acc = get_account(account_id)
    transactions = get_transactions(account_id)
    if not acc or not transactions:
        return 0

    start_date = transactions[0]["txn_date"]
    end_date = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if start_date > end_date:
        return 0

    tickers = sorted({t["ticker"] for t in transactions if t["ticker"]})
    price_series: dict[str, pd.Series] = {}
    for ticker in tickers:
        parquet_path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not parquet_path.exists():
            continue
        try:
            price_series[ticker] = pd.read_parquet(parquet_path)["Close"].sort_index()
        except Exception as e:
            logger.warning("backfill_value_history: failed to read parquet for %s: %s", ticker, e)

    written = 0
    for date_str in pd.date_range(start_date, end_date, freq="D").strftime("%Y-%m-%d"):
        open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(
            account_id, as_of_date=date_str, transactions=transactions
        )
        cash = _cash_balance_as_of(acc, transactions, date_str)
        equity = 0.0
        for ticker, holding in open_holdings.items():
            series = price_series.get(ticker)
            price = None
            if series is not None:
                window = series.loc[:date_str]
                if not window.empty:
                    price = float(window.iloc[-1])
            if price is None:
                equity += holding["total_investment"]
                continue
            native_price = price * 0.01 if holding["price_in_pence"] else price
            equity += holding["shares"] * native_price * fx_rate_on_date(holding["currency"], date_str)
        contributions = _net_contributions_as_of(acc, transactions, date_str)
        upsert_value_snapshot(account_id, date_str, round(cash + equity, 2), round(cash, 2), round(equity, 2), round(contributions, 2))
        written += 1
    return written


_GHOSTFOLIO_TYPE_MAP = {
    "BUY": "Buy",
    "SELL": "Sell",
    "DIVIDEND": "Dividend",
    "FEE": "Fee",
    "INTEREST": "Interest",
}


def _cached_ticker_currency(ticker: str) -> Optional[str]:
    """asset_profiles is the app's own authoritative source for a ticker's trading currency (the
    GBp/GBX pence convention is built around this field everywhere else) — trusted over Ghostfolio's
    self-reported SymbolProfile.currency, which has been observed to report GBP for LSE pence stocks."""
    if not ticker:
        return None
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT currency FROM asset_profiles WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()
        return row["currency"] if row and row["currency"] else None
    except Exception as e:
        logger.error("Failed to look up cached currency for %s: %s", ticker, e)
        return None
    finally:
        if conn:
            conn.close()


def _map_ghostfolio_activity(act: dict) -> Optional[dict]:
    """One Ghostfolio activity -> add_transaction() kwargs, or None to skip (draft / unsupported type).
    `unitPrice` is priced in the source Ghostfolio ACCOUNT's own currency, not necessarily
    BASE_CURRENCY (a Ghostfolio account can be denominated in USD/EUR/etc.) — so it is never used to
    derive an FX rate here. The native asset price comes from `unitPriceInAssetProfileCurrency`, and
    `exchange_rate` (native -> BASE_CURRENCY) is computed independently via `fx_rate_on_date`, the
    same trusted FX engine used for manually-entered transactions. Ghostfolio's own
    unitPrice/unitPriceInAssetProfileCurrency ratio is still used for `fee`, since the fee figure and
    `unitPrice` are reported in the same (account-side) currency within one activity record.
    `update_cash=True` like every other transaction — imported Buy/Sell/Dividend/Interest/Fee rows
    affect cash the same way a manually-entered one would. This relies on the operator separately
    recording their real deposit/withdrawal history (via Cash/Transfer); without that, cash_balance()
    will reflect only the net effect of the imported trades, not the true remaining balance."""
    txn_type = _GHOSTFOLIO_TYPE_MAP.get(act.get("type"))
    if not txn_type or act.get("isDraft"):
        return None

    profile = act.get("SymbolProfile") or {}
    ticker = profile.get("symbol") or None
    currency = _cached_ticker_currency(ticker) or profile.get("currency") or act.get("currency")
    txn_date = (act.get("date") or "")[:10]

    unit_price_native = act.get("unitPriceInAssetProfileCurrency")
    unit_price_acct_side = act.get("unitPrice")
    if unit_price_native:
        unit_price = float(unit_price_native)
        native_to_acct_rate = float(unit_price_acct_side) / unit_price if unit_price_acct_side else 1.0
    else:
        unit_price = float(unit_price_acct_side) if unit_price_acct_side is not None else None
        native_to_acct_rate = 1.0

    exchange_rate = fx_rate_on_date(currency, txn_date) if currency else 1.0

    fee_acct_side = float(act.get("fee") or 0.0)
    fee_native = fee_acct_side / native_to_acct_rate if native_to_acct_rate else fee_acct_side

    return {
        "txn_type": txn_type,
        "txn_date": txn_date,
        "ticker": ticker,
        "company_name": profile.get("name") or None,
        "currency": currency,
        "quantity": act.get("quantity"),
        "unit_price": unit_price,
        "fee": round(fee_native, 4),
        "exchange_rate": exchange_rate,
        "price_in_pence": currency == "GBp",
        "ghostfolio_ref": act.get("id"),
        "update_cash": True,
    }


def import_ghostfolio_activities(account_id: int, ghostfolio_account_id: str) -> dict:
    """Imports the entire activity history of ONE Ghostfolio account (`ghostfolio_account_id`) into
    one built-in account, deduped by `ghostfolio_ref` so re-import is idempotent. Imports every
    activity for that Ghostfolio account (incl. tickers no longer held) so fully-sold positions still
    land in closed_positions with realized P&L. Does not touch other Ghostfolio accounts — Ghostfolio
    activities have no implicit grouping otherwise, so importing without this filter would dump every
    Ghostfolio account's transactions into a single built-in account. Imported transactions affect
    cash the same way every other transaction does (see `_map_ghostfolio_activity`) — accurate only
    if the operator also records their real deposit/withdrawal history via Cash/Transfer rows."""
    from ghostfolio_sync import GhostfolioSyncEngine

    engine = GhostfolioSyncEngine()
    if not engine.is_configured:
        return {"imported": 0, "skipped": 0, "error": "Ghostfolio is not configured."}

    activities = engine.fetch_activities(account_id=ghostfolio_account_id)
    existing_refs = {t["ghostfolio_ref"] for t in get_transactions(account_id) if t["ghostfolio_ref"]}

    imported = 0
    skipped = 0
    for act in activities:
        mapped = _map_ghostfolio_activity(act)
        if mapped is None or mapped["ghostfolio_ref"] in existing_refs:
            skipped += 1
            continue
        if add_transaction(account_id=account_id, **mapped) is None:
            skipped += 1
            continue
        imported += 1

    return {"imported": imported, "skipped": skipped}


_CSV_CASH_TYPE_MAP = {"TOP_UP": "Cash", "INTEREST_FROM_CASH": "Interest"}
_CSV_REQUIRED_COLUMNS = (
    "Title", "Type", "Timestamp", "Account Currency", "Total Amount in Account Currency",
    "Buy / Sell", "Ticker", "Price per Share in Account Currency", "Stamp Duty", "Quantity",
    "Instrument Currency", "Price per Share", "FX Fee Amount", "Dividend Eligible Quantity",
    "Dividend Amount Per Share", "Dividend Withheld Tax Amount", "Dividend Net Distribution Amount",
)


def _csv_float(value: Optional[str]) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _csv_date(value: str) -> str:
    return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()


def _map_csv_row(row: dict) -> tuple:
    """One GIA-style CSV row -> add_transaction() kwargs, or (None, reason, ticker) to skip.
    See assets/csv_import_format.md for the column spec and the exchange-rate derivation."""
    row_type = (row.get("Type") or "").strip()
    if not row_type:
        return None, "blank_row", None
    if row_type == "INTERNAL_TRANSFER":
        return None, "ignored", None

    try:
        txn_date = _csv_date(row.get("Timestamp") or "")
    except ValueError:
        return None, "bad_date", None
    account_currency = (row.get("Account Currency") or BASE_CURRENCY).strip()

    if row_type in _CSV_CASH_TYPE_MAP:
        exchange_rate = 1.0 if account_currency == BASE_CURRENCY else fx_rate_on_date(account_currency, txn_date)
        return {
            "txn_type": _CSV_CASH_TYPE_MAP[row_type],
            "txn_date": txn_date,
            "currency": account_currency,
            "unit_price": _csv_float(row.get("Total Amount in Account Currency")),
            "exchange_rate": exchange_rate,
            "notes": row.get("Title") or None,
        }, None, None

    if row_type not in ("ORDER", "DIVIDEND"):
        return None, "unknown_type", None

    ticker = normalize_ticker(row["Ticker"]) if row.get("Ticker") else None
    if not ticker:
        return None, "no_ticker", "(no ticker)"

    currency = (row.get("Instrument Currency") or account_currency).strip()
    company_name = row.get("Title") or None
    isin = row.get("ISIN") or None

    # Brokers commonly report LSE trade prices already converted to GBP, but this app's own
    # market-data feed (Yahoo via asset_profiles/stock_signals/Parquet) always quotes these same
    # tickers in GBp pence — the only thing that matters for `price_in_pence` is which convention
    # the *market-data lookup* uses, not what currency the broker chose to display. Trust the app's
    # own cache over the file when it disagrees, same fix already applied to Ghostfolio import for
    # the identical mismatch (see `_cached_ticker_currency`'s docstring).
    pence_override = currency != "GBp" and _cached_ticker_currency(ticker) == "GBp"
    if pence_override:
        currency = "GBp"

    if row_type == "ORDER":
        buy_sell = (row.get("Buy / Sell") or "").strip().upper()
        txn_type = "Buy" if buy_sell == "BUY" else "Sell"
        price_native = _csv_float(row.get("Price per Share"))
        price_account = _csv_float(row.get("Price per Share in Account Currency"))
        exchange_rate = price_account / price_native if price_native else 1.0
        fee_account = _csv_float(row.get("Stamp Duty")) + _csv_float(row.get("FX Fee Amount"))
        fee_native = fee_account / exchange_rate if exchange_rate else fee_account
        mapped = {
            "txn_type": txn_type,
            "txn_date": txn_date,
            "ticker": ticker,
            "isin": isin,
            "company_name": company_name,
            "currency": currency,
            "quantity": _csv_float(row.get("Quantity")),
            "unit_price": price_native,
            "fee": round(fee_native, 6),
            "exchange_rate": exchange_rate,
            "price_in_pence": currency == "GBp",
        }
    else:
        net_distribution = _csv_float(row.get("Dividend Net Distribution Amount"))
        total_account = _csv_float(row.get("Total Amount in Account Currency"))
        exchange_rate = total_account / net_distribution if net_distribution and currency != account_currency else 1.0
        mapped = {
            "txn_type": "Dividend",
            "txn_date": txn_date,
            "ticker": ticker,
            "isin": isin,
            "company_name": company_name,
            "currency": currency,
            "quantity": _csv_float(row.get("Dividend Eligible Quantity")),
            "unit_price": _csv_float(row.get("Dividend Amount Per Share")),
            "fee": _csv_float(row.get("Dividend Withheld Tax Amount")),
            "exchange_rate": exchange_rate,
            "price_in_pence": currency == "GBp",
        }

    if pence_override:
        mapped["unit_price"] *= 100
        mapped["fee"] *= 100
        mapped["exchange_rate"] *= 0.01

    return mapped, None, None


def _csv_row_fingerprint(account_id: int, row: dict, occurrence: int) -> str:
    """Stable dedup key for one CSV row, reusing the `ghostfolio_ref` column as a generic import-dedup
    slot (prefixed so it can never collide with a real Ghostfolio UUID). `occurrence` disambiguates
    genuinely identical rows within the same file (e.g. three same-day same-amount Top Up rows)."""
    raw = "|".join([
        str(account_id), row.get("Type", ""), row.get("Timestamp", ""), row.get("Ticker", ""),
        row.get("Total Amount in Account Currency", ""), row.get("Quantity", ""), str(occurrence),
    ])
    return "csv:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _ticker_resolvable(ticker: str) -> bool:
    """A live Yahoo miss is retried once. `get_ticker_info` swallows every exception (including the
    HTTP 429 circuit breaker), so a single transient rate-limit hit on an otherwise-valid ticker
    looks identical to a genuinely delisted one — without a retry it gets permanently skipped from
    the import. The retry's own `get_ticker_info` call waits out any in-progress 429 cooldown via
    yahoo_engine's existing rate-limit lock before trying again, so no extra backoff is needed here."""
    if _ticker_known(ticker):
        return True
    return bool(yahoo_engine.get_ticker_info(ticker)) or bool(yahoo_engine.get_ticker_info(ticker))


_CSV_SKIP_REASON_LABELS = {
    "no_ticker": "no ticker in file",
    "unknown_type": "unrecognized row type",
    "bad_date": "unparseable date",
    "unresolved_ticker": "ticker not found (possibly delisted or mistyped)",
    "duplicate": "already imported",
    "db_error": "database error",
}


def import_csv_activities(account_id: int, csv_text: str) -> dict:
    """Imports a GIA/broker-export CSV (see assets/csv_import_format.md) into one built-in account.
    Unlike `import_ghostfolio_activities`, rows whose ticker can't be resolved are skipped outright
    (not imported with a "Needs Review" flag) — there is no real market data to attach them to.
    Every skipped row (other than `INTERNAL_TRANSFER`/blank rows, which are expected noise) is
    reported back with its date and ticker so the operator can find the exact row in their file."""
    reader = csv.DictReader(io.StringIO(csv_text))
    header = set(reader.fieldnames or [])
    missing = [c for c in _CSV_REQUIRED_COLUMNS if c not in header]
    if missing:
        return {"error": f"CSV is missing required column(s): {', '.join(missing)}"}

    existing_refs = {t["ghostfolio_ref"] for t in get_transactions(account_id) if t["ghostfolio_ref"]}
    ticker_ok: dict[str, bool] = {}
    occurrence_counts: dict[str, int] = {}
    skipped_rows: list[dict] = []

    imported = 0
    ignored = 0

    def _skip(reason: str, date: Optional[str], ticker: Optional[str]) -> None:
        skipped_rows.append({"date": date, "ticker": ticker or None, "reason": _CSV_SKIP_REASON_LABELS[reason]})

    for row in reader:
        mapped, reason, unresolved_key = _map_csv_row(row)
        if mapped is None:
            if reason == "ignored":
                ignored += 1
            elif reason != "blank_row":
                _skip(reason, row.get("Timestamp"), unresolved_key or row.get("Ticker"))
            continue

        ticker = mapped.get("ticker")
        if ticker:
            if ticker not in ticker_ok:
                ticker_ok[ticker] = _ticker_resolvable(ticker)
            if not ticker_ok[ticker]:
                _skip("unresolved_ticker", mapped["txn_date"], ticker)
                continue

        fingerprint_base = "|".join([row.get("Type", ""), row.get("Timestamp", ""), row.get("Ticker", ""),
                                      row.get("Total Amount in Account Currency", ""), row.get("Quantity", "")])
        occurrence_counts[fingerprint_base] = occurrence_counts.get(fingerprint_base, 0) + 1
        ref = _csv_row_fingerprint(account_id, row, occurrence_counts[fingerprint_base])
        if ref in existing_refs:
            _skip("duplicate", mapped["txn_date"], ticker)
            continue

        if add_transaction(account_id=account_id, ghostfolio_ref=ref, **mapped) is None:
            _skip("db_error", mapped["txn_date"], ticker)
            continue
        imported += 1

    return {
        "imported": imported,
        "skipped": len(skipped_rows),
        "ignored": ignored,
        "skipped_rows": skipped_rows,
    }
