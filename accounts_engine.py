# GUI name: "Accounts". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import csv
import hashlib
import io
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

import time_engine
from config import BASE_CURRENCY, HISTORICAL_DIR, PORTFOLIO_PATH, load_config
from db_accounts import (
    add_price_history, add_transaction, delete_transaction, get_account, get_accounts,
    get_pending_topup, get_price_as_of, get_price_history, get_transaction, get_transactions,
    get_value_history, get_value_history_currency, get_watchlist_items, resolve_pending_topup,
    update_account, update_transaction, upsert_performance_cache, upsert_value_snapshot,
    upsert_value_snapshot_currency,
)
from database import get_connection
from market_pulse import is_price_fresh
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
        has_price = bool(priced and priced[0])
        if has_price:
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
            "priced_at_cost": not has_price,
        })

    total_value = sum(r["market_value"] for r in rows) or 1.0
    for r in rows:
        r["allocation_pct"] = round(r["market_value"] / total_value * 100, 2)
    return rows


def market_values_for_xray(account_id: Optional[int] = None) -> list:
    """Holdings for one Trading account, or all merged when account_id is None — feeds xray_engine."""
    holdings = derive_account_holdings(account_id)
    if not holdings:
        return []
    prices = _current_price_map(list(holdings.keys()))
    rows = []
    for ticker, h in holdings.items():
        total_investment = sum(a["total_investment"] for a in h["accounts"])
        priced = prices.get(ticker)
        has_price = bool(priced and priced[0])
        if has_price:
            price, currency = priced
            market_value = h["global_shares"] * price * get_rate_to_base(currency or h["currency"])
        else:
            price = None
            market_value = total_investment
        rows.append({
            "ticker": ticker,
            "company_name": h["company_name"],
            "currency": h["currency"],
            "shares": h["global_shares"],
            "market_price": price,
            "market_value": round(market_value, 2),
            "total_investment": round(total_investment, 2),
            "priced_at_cost": not has_price,
        })
    return rows


def stale_pricing_warning(holdings: list) -> Optional[str]:
    """Mirrors xray_engine's data_warnings pattern — surfaces holdings priced at cost basis
    instead of market value because no price data exists yet, so this is never silent."""
    unpriced = [h["ticker"] for h in holdings if h.get("priced_at_cost")]
    if not unpriced:
        return None
    return (
        f"{len(unpriced)} holding(s) priced at cost basis, not market value — price data missing for: "
        + ", ".join(unpriced[:5])
        + (f" and {len(unpriced) - 5} more" if len(unpriced) > 5 else "")
        + ". This resolves automatically after the next nightly data pipeline run (22:00 Mon-Fri)."
    )


def _recompute_globals(entry: dict) -> None:
    total_shares = sum(a.get("shares", 0.0) for a in entry["accounts"])
    total_inv = sum(a.get("total_investment", 0.0) for a in entry["accounts"])
    entry["global_shares"] = round(total_shares, 6)
    entry["global_buy_price"] = round(total_inv / total_shares, 4) if total_shares else 0.0


def _read_portfolio_json() -> dict:
    if not load_config().get("GHOSTFOLIO_ENABLED", False):
        return {}
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


def reconcile_cash(account_id: int, actual_balance: float) -> dict:
    """Books a 'Cash' adjustment transaction (tagged `is_adjustment`) for the difference between
    the ledger's computed cash_balance() and the real-world actual_balance the user reports —
    both already in BASE_CURRENCY, so no FX lookup is needed for the adjustment itself."""
    computed = cash_balance(account_id)
    delta = round(actual_balance - computed, 2)
    if abs(delta) < 0.005:
        return {"txn_id": None, "delta": 0.0, "computed_balance": computed}
    today = datetime.now(timezone.utc).date().isoformat()
    txn_id = add_transaction(
        account_id, "Cash", today, currency=BASE_CURRENCY, quantity=1, unit_price=delta,
        fee=0.0, exchange_rate=1.0, update_cash=True, notes="Reconciliation adjustment",
        is_adjustment=True,
    )
    return {"txn_id": txn_id, "delta": delta, "computed_balance": computed}


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
    """Live-aware: prefers a fresh `market_pulse_cache` price (kept warm by the 5-minute intraday
    scan for every held ticker) over `stock_signals.current_price`, which only updates nightly."""
    if not tickers:
        return {}
    from account_scraper_engine import latest_price, parse_pension_account_id

    result: dict[str, tuple] = {}
    market_tickers = []
    for ticker in tickers:
        pension_id = parse_pension_account_id(ticker)
        if pension_id is not None:
            priced = latest_price(pension_id)
            if priced:
                result[ticker] = priced
        else:
            market_tickers.append(ticker)
    if not market_tickers:
        return result

    refresh_rate = int(load_config().get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))
    live_prices: dict[str, float] = {}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(market_tickers))
        cursor.execute(
            f"SELECT ticker, price, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            market_tickers
        )
        for r in cursor.fetchall():
            if is_price_fresh(r["last_updated"], r["price"], refresh_rate):
                live_prices[r["ticker"]] = r["price"]
    except Exception as e:
        logger.error("Failed to load live prices from market_pulse_cache: %s", e)
    finally:
        if conn:
            conn.close()

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(market_tickers))
        cursor.execute(
            f"SELECT ticker, current_price, currency FROM stock_signals WHERE ticker IN ({placeholders})",
            market_tickers
        )
        for r in cursor.fetchall():
            price = live_prices.get(r["ticker"], r["current_price"])
            result[r["ticker"]] = (price, r["currency"])
    except Exception as e:
        logger.error("Failed to load current prices: %s", e)
    finally:
        if conn:
            conn.close()
    return result


def _bucket_equity_by_currency(open_holdings: dict, price_lookup) -> tuple:
    """Shared by the live (_equity_value) and historical (backfill_value_history) equity paths.
    `price_lookup(ticker, holding) -> Optional[(price, currency, fx_rate)]`, or None if unpriced.
    Returns (total_base, {currency: {"native": float, "base": float, "fx_rate": float}}). An
    unpriced holding falls back to cost basis for the total (matching the pre-existing behaviour
    of both callers) and contributes nothing to the per-currency breakdown."""
    total = 0.0
    breakdown: dict[str, dict] = {}
    for ticker, holding in open_holdings.items():
        priced = price_lookup(ticker, holding)
        if not priced or not priced[0]:
            total += holding["total_investment"]
            continue
        price, currency, fx_rate = priced
        currency = currency or holding["currency"]
        native = holding["shares"] * price
        base = native * fx_rate
        total += base
        bucket = breakdown.setdefault(currency, {"native": 0.0, "base": 0.0, "fx_rate": fx_rate})
        bucket["native"] += native
        bucket["base"] += base
        bucket["fx_rate"] = fx_rate
    return total, breakdown


def _equity_value(open_holdings: dict) -> float:
    total, _breakdown = _equity_value_with_breakdown(open_holdings)
    return total


def _equity_value_with_breakdown(open_holdings: dict) -> tuple:
    if not open_holdings:
        return 0.0, {}
    prices = _current_price_map(list(open_holdings.keys()))

    def _lookup(ticker, holding):
        priced = prices.get(ticker)
        if not priced or not priced[0]:
            return None
        price, currency = priced
        currency = currency or holding["currency"]
        return price, currency, get_rate_to_base(currency)

    return _bucket_equity_by_currency(open_holdings, _lookup)


def _equity_value_for_account(acc: dict, open_holdings: dict) -> float:
    """House has no Buy/Sell ledger at all (per design — it's a single scraped valuation, not a
    holding), so `_equity_value` would always see an empty `open_holdings` and return 0."""
    if acc["account_type"] != "House":
        return _equity_value(open_holdings)
    from account_scraper_engine import latest_price
    priced = latest_price(acc["id"])
    if not priced or not priced[0]:
        return 0.0
    price, currency = priced
    return price * get_rate_to_base(currency or acc["currency"])


def _equity_value_for_account_with_breakdown(acc: dict, open_holdings: dict) -> tuple:
    """Currency-breakdown-aware counterpart of `_equity_value_for_account`, for callers
    (snapshot_all_accounts/resnapshot_account) that need both the total and the per-currency
    split from a single pass. House is always single-currency = acc['currency']."""
    if acc["account_type"] != "House":
        return _equity_value_with_breakdown(open_holdings)
    from account_scraper_engine import latest_price
    priced = latest_price(acc["id"])
    if not priced or not priced[0]:
        return 0.0, {}
    price, currency = priced
    currency = currency or acc["currency"]
    fx_rate = get_rate_to_base(currency)
    native = price
    base = native * fx_rate
    return base, {currency: {"native": native, "base": base, "fx_rate": fx_rate}}


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
        "holdings_count": len(open_holdings),
        "equity_value": round(_equity_value_for_account(acc, open_holdings), 2),
        "realized_pnl": realized,
    }


def total_value(account_id: int) -> Optional[float]:
    """Live cash + live equity, right now — the current total value used by the return tiles."""
    acc = get_account(account_id)
    if not acc:
        return None
    open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(account_id)
    equity = _equity_value_for_account(acc, open_holdings)
    cash = 0.0 if acc["account_type"] == "Pension" else cash_balance(account_id)
    return round(cash + equity, 2)


def unrealized_pnl(account_id: int) -> float:
    """Live equity value minus cost basis of currently-open holdings."""
    rows = holdings_with_market_value(account_id)
    return round(sum(r["market_value"] - r["total_investment"] for r in rows), 2)


_RETURN_WINDOWS = {"1d": 1, "1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365}


def period_returns(account_id: int) -> dict:
    """1D/1W/1M/3M/6M/1Y gain/loss in BASE_CURRENCY, excluding the effect of deposits/withdrawals
    during the period. Deliberately currency, not %: dividing by the period's starting value blows
    up into a meaningless number whenever that baseline is small (e.g. a lookback window older than
    the account itself falls back to the earliest snapshot, which can be near-zero right after
    opening) — the currency amount stays sane and bounded regardless. Each value is a float, or
    None only when there's no snapshot history at all yet."""
    history = get_value_history(account_id)
    if not history:
        return {key: None for key in _RETURN_WINDOWS}

    end_value = total_value(account_id)
    if end_value is None:
        return {key: None for key in _RETURN_WINDOWS}

    net_contributions_now = net_contributions(account_id)
    today = datetime.now(timezone.utc).date()

    returns: dict = {}
    for key, days in _RETURN_WINDOWS.items():
        target_date = (today - timedelta(days=days)).isoformat()
        candidates = [row for row in history if row["snapshot_date"] <= target_date]
        baseline = candidates[-1] if candidates else history[0]
        start_value = baseline["total_value"] or 0.0
        contributions_delta = net_contributions_now - baseline["net_contributions"]
        returns[key] = round(end_value - start_value - contributions_delta, 2)
    return returns


def money_weighted_return(account_id: int) -> Optional[float]:
    """Since-inception Modified Dietz return, % — a closed-form approximation of true
    money-weighted (IRR-style) return that avoids iterative solving."""
    acc = get_account(account_id)
    if not acc:
        return None

    opened_date_str = acc["opened_date"] or (acc["created_at"][:10] if acc["created_at"] else None)
    if not opened_date_str:
        return None
    opened_date = datetime.strptime(opened_date_str, "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    cd = max((today - opened_date).days, 0)

    flows: list[tuple[float, int]] = []
    initial_cash = acc["initial_cash"] or 0.0
    if abs(initial_cash) > _EPS:
        flows.append((initial_cash, 0))
    for txn in get_transactions(account_id):
        if txn["update_cash"] and txn["txn_type"] in _CONTRIBUTION_TYPES:
            txn_date = datetime.strptime(txn["txn_date"], "%Y-%m-%d").date()
            d_i = min(max((txn_date - opened_date).days, 0), cd) if cd else 0
            flows.append((_cash_delta(txn), d_i))

    if not flows:
        return None

    emv = total_value(account_id)
    if emv is None:
        return None

    total_cf = sum(amount for amount, _ in flows)
    if cd == 0:
        denominator = total_cf
    else:
        denominator = sum(amount * (cd - d_i) / cd for amount, d_i in flows)
    if abs(denominator) < _EPS:
        return None

    numerator = emv - total_cf
    return round(numerator / denominator * 100, 2)


def refresh_performance_cache(account_id: int) -> None:
    """Computes the live-performance figures once and persists them to `account_performance_cache`
    so every browser/tab that later polls the account detail page reads a cheap cached row instead
    of re-deriving MWRR/period-returns from the full transaction history on every request. Called
    by the 5-minute intraday scan for every Trading account after it refreshes holding prices."""
    returns = period_returns(account_id)
    upsert_performance_cache(
        account_id,
        total_value=total_value(account_id),
        equity_value=account_summary(account_id)["equity_value"],
        cash_balance=cash_balance(account_id),
        unrealized_pnl=unrealized_pnl(account_id),
        return_1d=returns["1d"], return_1w=returns["1w"], return_1m=returns["1m"],
        return_3m=returns["3m"], return_6m=returns["6m"], return_1y=returns["1y"],
        mwrr=money_weighted_return(account_id),
        last_updated=time.time(),
    )


_WATCHLIST_TYPE_BUCKETS = {"EQUITY": "equity", "ETF": "etf", "MUTUALFUND": "fund"}


def watchlist_summary(account_id: int) -> dict:
    """Ticker count + breakdown by equity/etf/fund/other for the Watchlist tile — bucketed from
    each item's `quote_type` (the raw value Yahoo's quoteType field reports, e.g. "EQUITY")."""
    items = get_watchlist_items(account_id)
    by_type = {"equity": 0, "etf": 0, "fund": 0, "other": 0}
    for item in items:
        bucket = _WATCHLIST_TYPE_BUCKETS.get((item["quote_type"] or "").upper(), "other")
        by_type[bucket] += 1
    return {"count": len(items), "by_type": by_type}


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


VALUE_CHART_PERIODS = ("1m", "ytd", "1y", "max")


def filter_value_history_by_period(history: list, period: str) -> list:
    """Slices a snapshot_date-ordered value_history list to the chart's selected range."""
    if period == "max" or not history:
        return history
    today = datetime.now(timezone.utc).date()
    cutoff = today.replace(month=1, day=1) if period == "ytd" else today - timedelta(days=30 if period == "1m" else 365)
    cutoff_str = cutoff.isoformat()
    return [row for row in history if row["snapshot_date"] >= cutoff_str]


def _write_currency_breakdown(account_id: int, snapshot_date: str, breakdown: dict) -> None:
    for currency, bucket in breakdown.items():
        upsert_value_snapshot_currency(
            account_id, snapshot_date, currency,
            round(bucket["native"], 2), round(bucket["base"], 2), bucket["fx_rate"],
        )


def snapshot_all_accounts() -> int:
    """Nightly job body: writes today's value snapshot for every account. Returns rows written."""
    today = datetime.now(timezone.utc).date().isoformat()
    written = 0
    for acc in get_accounts():
        aid = acc["id"]
        open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(aid)
        equity, breakdown = _equity_value_for_account_with_breakdown(acc, open_holdings)
        # Pension has no real cash sub-ledger — cash_balance() would just return initial_cash
        # as a phantom baseline, double-counting money already represented in equity_value.
        cash = 0.0 if acc["account_type"] == "Pension" else cash_balance(aid)
        contributions = net_contributions(aid)
        upsert_value_snapshot(aid, today, round(cash + equity, 2), round(cash, 2), round(equity, 2), contributions)
        _write_currency_breakdown(aid, today, breakdown)
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
    equity, breakdown = _equity_value_for_account_with_breakdown(acc, open_holdings)
    cash = 0.0 if acc["account_type"] == "Pension" else cash_balance(account_id)
    contributions = net_contributions(account_id)
    upsert_value_snapshot(account_id, today, round(cash + equity, 2), round(cash, 2), round(equity, 2), contributions)
    _write_currency_breakdown(account_id, today, breakdown)


def backfill_value_history(account_id: int) -> int:
    """One-time historical snapshot fill from Parquet so the account-value chart has data
    immediately on account create/import, covering every day from the earliest transaction up to
    yesterday (today is left to the nightly snapshot job). Returns rows written; 0 if there are no
    transactions yet."""
    acc = get_account(account_id)
    if not acc:
        return 0
    if acc["account_type"] == "House":
        return _backfill_house_value_history(account_id, acc)

    transactions = get_transactions(account_id)
    if not transactions:
        return 0

    start_date = transactions[0]["txn_date"]
    end_date = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if start_date > end_date:
        return 0

    from account_scraper_engine import parse_pension_account_id
    from account_scraper_engine import price_series as scraped_price_series

    tickers = sorted({t["ticker"] for t in transactions if t["ticker"]})
    price_series: dict[str, pd.Series] = {}
    for ticker in tickers:
        pension_id = parse_pension_account_id(ticker)
        if pension_id is not None:
            series = scraped_price_series(pension_id)
            if not series.empty:
                price_series[ticker] = series
            continue
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
        cash = 0.0 if acc["account_type"] == "Pension" else _cash_balance_as_of(acc, transactions, date_str)

        def _lookup(ticker, holding, date_str=date_str):
            series = price_series.get(ticker)
            if series is None:
                return None
            window = series.loc[:date_str]
            if window.empty:
                return None
            price = float(window.iloc[-1])
            # fx_rate_on_date already halves GBp->GBP by 0.01 — applying price_in_pence here too
            # would divide by 100 twice (the bug behind a 100x equity undervaluation on backfilled rows).
            return price, holding["currency"], fx_rate_on_date(holding["currency"], date_str)

        equity, breakdown = _bucket_equity_by_currency(open_holdings, _lookup)
        contributions = _net_contributions_as_of(acc, transactions, date_str)
        upsert_value_snapshot(account_id, date_str, round(cash + equity, 2), round(cash, 2), round(equity, 2), round(contributions, 2))
        _write_currency_breakdown(account_id, date_str, breakdown)
        written += 1
    return written


def _backfill_house_value_history(account_id: int, acc: dict) -> int:
    """House has no transactions at all, so the date range comes from the scraped price history
    itself rather than from the earliest transaction (as the generic path above does)."""
    from account_scraper_engine import price_series as scraped_price_series
    series = scraped_price_series(account_id)
    if series.empty:
        return 0

    transactions = get_transactions(account_id)
    start_date = series.index[0].date().isoformat()
    end_date = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if start_date > end_date:
        return 0

    written = 0
    for date_str in pd.date_range(start_date, end_date, freq="D").strftime("%Y-%m-%d"):
        window = series.loc[:date_str]
        if window.empty:
            continue
        native = float(window.iloc[-1])
        fx_rate = get_rate_to_base(acc["currency"])
        equity = native * fx_rate
        cash = _cash_balance_as_of(acc, transactions, date_str)
        contributions = _net_contributions_as_of(acc, transactions, date_str)
        upsert_value_snapshot(account_id, date_str, round(cash + equity, 2), round(cash, 2), round(equity, 2), round(contributions, 2))
        _write_currency_breakdown(account_id, date_str, {acc["currency"]: {"native": native, "base": equity, "fx_rate": fx_rate}})
        written += 1
    return written


def sync_pension_opening_balance(account_id: int) -> None:
    """Materialises 'Opening Balance' + 'Opening Balance Units' as a real Buy transaction against
    the synthetic ticker, so a pre-existing pension balance shows real units/holdings from day one
    instead of starting at zero until the first Pay In. `opening_balance_txn_id` tracks which
    transaction (if any) represents this, so a later edit updates it in place rather than
    duplicating it, and clearing either field removes it. Call after every create/update of a
    Pension account — a no-op for any other account type or when either field is blank/zero."""
    acc = get_account(account_id)
    if not acc or acc["account_type"] != "Pension":
        return
    units = acc["opening_balance_units"]
    amount = acc["initial_cash"]
    existing_txn_id = acc["opening_balance_txn_id"]

    if not units or not amount:
        if existing_txn_id:
            delete_transaction(existing_txn_id)
            update_account(account_id, opening_balance_txn_id=None)
        return

    from account_scraper_engine import pension_ticker
    txn_date = acc["opened_date"] or acc["created_at"][:10]
    price = amount / units
    if existing_txn_id and get_transaction(existing_txn_id):
        update_transaction(
            existing_txn_id, txn_date=txn_date, quantity=units, unit_price=price,
            company_name=acc["name"], currency=acc["currency"],
        )
    else:
        txn_id = add_transaction(
            account_id, "Buy", txn_date, ticker=pension_ticker(account_id),
            company_name=acc["name"], currency=acc["currency"], quantity=units, unit_price=price,
            update_cash=False, price_in_pence=False, notes="Opening balance",
        )
        if txn_id is not None:
            update_account(account_id, opening_balance_txn_id=txn_id)


def sync_house_purchase_price(account_id: int) -> None:
    """Seeds the purchase price as the earliest row in `account_price_history`, so the House value
    chart's first data point is the real purchase value/date rather than whenever the scraper first
    ran. Keyed on `opened_date` (falling back to `created_at`), so re-saving the account with the
    same date just upserts this row in place via `add_price_history`'s own ON CONFLICT handling —
    never duplicates it. Call after every create/update of a House account — a no-op for any other
    account type or when `initial_cash` is unset."""
    acc = get_account(account_id)
    if not acc or acc["account_type"] != "House" or not acc["initial_cash"]:
        return
    purchase_date = acc["opened_date"] or acc["created_at"][:10]
    add_price_history(account_id, purchase_date, acc["initial_cash"], source="purchase")


def record_pension_contribution(account_id: int, txn_date: str, amount: float, unit_price: Optional[float] = None) -> dict:
    """'Pay In': resolves that date's unit price (overridable) and buys units = amount / price.
    `update_cash=False` — the money never sits in this account as cash, it's invested same-day, so
    there is no cash sub-ledger for a Pension account to keep consistent (see `cash_balance`)."""
    acc = get_account(account_id)
    if not acc:
        return {"error": "Account not found."}
    if acc["account_type"] != "Pension":
        return {"error": "Only Pension accounts support contributions."}
    from account_scraper_engine import pension_ticker
    from account_scraper_engine import price_as_of as scraped_price_as_of

    price = unit_price if unit_price is not None else scraped_price_as_of(account_id, txn_date)
    if not price:
        return {"error": "No unit price available for that date — import or scrape price history first, or supply unit_price."}
    units = amount / price
    txn_id = add_transaction(
        account_id, "Buy", txn_date, ticker=pension_ticker(account_id),
        company_name=acc["name"], currency=acc["currency"], quantity=units, unit_price=price,
        update_cash=False, price_in_pence=False, notes="Pension contribution",
    )
    if txn_id is None:
        return {"error": "Failed to record the contribution."}
    return {"txn_id": txn_id, "units": round(units, 6), "unit_price": price}


def pension_units_as_of(account_id: int, date_str: str) -> float:
    from account_scraper_engine import pension_ticker
    ticker = pension_ticker(account_id)
    open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(account_id, as_of_date=date_str)
    return open_holdings.get(ticker, {}).get("shares", 0.0)


def pension_display_label(acc: dict) -> str:
    from account_scraper_engine import pension_ticker
    return acc.get("pension_ticker_label") or pension_ticker(acc["id"])


_PENSION_PERFORMANCE_WINDOWS = (("1m", 30), ("ytd", None), ("1y", 365))


def pension_performance(account_id: int) -> dict:
    """Performance % over 1 month / YTD / 1 year, derived from the scraped/imported unit price
    history rather than the value snapshot history — gives a meaningful number from day one since
    the unit price itself is a like-for-like return series, unaffected by contribution timing."""
    history = get_price_history(account_id)
    if not history:
        return {"1m": None, "ytd": None, "1y": None}
    latest_price = history[-1]["price"]
    today = datetime.now(timezone.utc).date()

    result = {}
    for key, days_back in _PENSION_PERFORMANCE_WINDOWS:
        as_of_date = (today.replace(month=1, day=1) if key == "ytd" else today - timedelta(days=days_back)).isoformat()
        as_of_row = get_price_as_of(account_id, as_of_date)
        if not as_of_row or not as_of_row["price"]:
            result[key] = None
            continue
        result[key] = round((latest_price - as_of_row["price"]) / as_of_row["price"] * 100, 2)
    return result


def pension_activities(account_id: int) -> list:
    """Activities enriched with a running total-units balance, walked chronologically (oldest
    first, matching get_transactions' own order) before the page reverses it for newest-first
    display — a Pension ledger has no cash balance to sanity-check against, so this running total
    is the equivalent cross-check."""
    running_units = 0.0
    rows = []
    for txn in get_transactions(account_id):
        qty = txn["quantity"] or 0.0
        if txn["txn_type"] == "Buy":
            running_units += qty
        elif txn["txn_type"] == "Sell":
            running_units -= qty
        rows.append({
            **txn,
            "total_base": transaction_total_base(txn),
            "running_units": round(running_units, 6),
        })
    return rows


def record_pension_fee(
    account_id: int, txn_date: str, units_after: Optional[float] = None,
    units_removed: Optional[float] = None, unit_price: Optional[float] = None,
) -> dict:
    """'Admin Fee': the provider's portal sometimes shows the units remaining after the fee, and
    sometimes states the units deducted directly — accepts either. When given `units_after`, units
    held *before* the fee already come from the existing ledger, so the delta is the fee size."""
    acc = get_account(account_id)
    if not acc:
        return {"error": "Account not found."}
    if acc["account_type"] != "Pension":
        return {"error": "Only Pension accounts support admin fees."}
    if (units_after is None) == (units_removed is None):
        return {"error": "Provide exactly one of units_after or units_removed."}
    from account_scraper_engine import pension_ticker
    from account_scraper_engine import price_as_of as scraped_price_as_of

    ticker = pension_ticker(account_id)
    units_before = pension_units_as_of(account_id, txn_date)
    if units_removed is None:
        units_removed = units_before - units_after
    if units_removed <= _EPS:
        return {"error": f"units_after ({units_after}) must be less than the units currently held ({units_before})."}
    if units_removed > units_before + _EPS:
        return {"error": f"units_removed ({units_removed}) cannot exceed the units currently held ({units_before})."}
    price = unit_price if unit_price is not None else scraped_price_as_of(account_id, txn_date)
    if not price:
        return {"error": "No unit price available for that date — import or scrape price history first, or supply unit_price."}
    txn_id = add_transaction(
        account_id, "Sell", txn_date, ticker=ticker,
        company_name=acc["name"], currency=acc["currency"], quantity=units_removed, unit_price=price,
        update_cash=False, price_in_pence=False, notes="Pension admin fee",
    )
    if txn_id is None:
        return {"error": "Failed to record the admin fee."}
    return {"txn_id": txn_id, "units_removed": round(units_removed, 6), "unit_price": price, "fee_cost": round(units_removed * price, 2)}


def confirm_autotopup(account_id: int, pending_id: int, amount: float, txn_date: str) -> dict:
    """Posts the deferred Auto Top-up as a real 'Cash' deposit only once the user confirms the
    amount/date that actually landed — the scheduled date alone never touches the cash balance."""
    pending = get_pending_topup(pending_id)
    if not pending or pending["account_id"] != account_id:
        return {"error": "Pending top-up not found."}
    if pending["status"] != "pending":
        return {"error": f"This top-up has already been {pending['status']}."}
    acc = get_account(account_id)
    if not acc:
        return {"error": "Account not found."}
    exchange_rate = fx_rate_on_date(acc["currency"], txn_date)
    txn_id = add_transaction(
        acc["id"], "Cash", txn_date, currency=acc["currency"], quantity=1, unit_price=amount,
        exchange_rate=exchange_rate, update_cash=True, notes=acc.get("autotopup_notes") or "Auto Top-up",
    )
    if txn_id is None:
        return {"error": "Failed to record the top-up transaction."}
    resolve_pending_topup(pending_id, "confirmed", confirmed_amount=amount, confirmed_date=txn_date, txn_id=txn_id)
    return {"txn_id": txn_id}


def dismiss_autotopup(account_id: int, pending_id: int) -> dict:
    pending = get_pending_topup(pending_id)
    if not pending or pending["account_id"] != account_id:
        return {"error": "Pending top-up not found."}
    if pending["status"] != "pending":
        return {"error": f"This top-up has already been {pending['status']}."}
    resolve_pending_topup(pending_id, "dismissed")
    return {"status": "success"}


def _cached_ticker_currency(ticker: str) -> Optional[str]:
    """asset_profiles is the app's own authoritative source for a ticker's trading currency (the
    GBp/GBX pence convention is built around this field everywhere else) — trusted over a broker
    export's self-reported currency, which has been observed to report GBP for LSE pence stocks."""
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
    Rows whose ticker can't be resolved are skipped outright (not imported with a "Needs Review"
    flag) — there is no real market data to attach them to. Every skipped row (other than
    `INTERNAL_TRANSFER`/blank rows, which are expected noise) is reported back with its date and
    ticker so the operator can find the exact row in their file."""
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


def _trading_accounts() -> list:
    return [a for a in get_accounts() if a["account_type"] == "Trading"]


def portfolio_totals() -> dict:
    """Aggregates live figures across every non-deleted Trading account — the Home Assistant
    portfolio-summary sensor's data source. Zero Trading accounts (fresh install) returns an
    all-zero/None shape rather than raising or dividing by zero."""
    accounts = _trading_accounts()
    result = {
        "account_count": len(accounts),
        "base_currency": BASE_CURRENCY,
        "as_of": time.time(),
        "current_value": 0.0,
        "total_investment": 0.0,
        "portfolio_gain": 0.0,
        "portfolio_gain_pct": None,
        "portfolio_gain_fx": 0.0,
        "portfolio_gain_fx_pct": None,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": None,
        "twr_pct": None,
        "twr_fx_pct": None,
        "portfolio_dividends": 0.0,
    }
    if not accounts:
        return result

    account_ids = [a["id"] for a in accounts]
    current_value = sum(total_value(aid) or 0.0 for aid in account_ids)
    total_investment = 0.0
    unrealized = 0.0
    dividends = 0.0
    for aid in account_ids:
        for row in holdings_with_market_value(aid):
            total_investment += row["total_investment"]
            unrealized += row["market_value"] - row["total_investment"]
        dividends += account_summary(aid).get("dividend", 0.0)

    gain_ex_fx, gain_actual = portfolio_gain_fx_decomposition(account_ids)

    result["current_value"] = round(current_value, 2)
    result["total_investment"] = round(total_investment, 2)
    result["portfolio_gain"] = round(gain_ex_fx, 2)
    result["portfolio_gain_fx"] = round(gain_actual, 2)
    result["unrealized_pnl"] = round(unrealized, 2)
    result["portfolio_dividends"] = round(dividends, 2)
    if total_investment:
        result["portfolio_gain_pct"] = round(gain_ex_fx / total_investment * 100, 2)
        result["portfolio_gain_fx_pct"] = round(gain_actual / total_investment * 100, 2)
        result["unrealized_pnl_pct"] = round(unrealized / total_investment * 100, 2)
    result["twr_pct"] = portfolio_twr_ex_fx(account_ids)
    result["twr_fx_pct"] = portfolio_twr_fx(account_ids)
    return result


def _avg_purchase_fx_rate(account_id: int, tickers: set) -> dict:
    """Purchase-quantity-weighted-average `exchange_rate` across still-open Buy legs, per ticker —
    isolates the FX rate actually paid at purchase, as opposed to today's live rate."""
    weighted: dict[str, float] = {}
    qty_total: dict[str, float] = {}
    for txn in get_transactions(account_id):
        ticker = txn["ticker"]
        if txn["txn_type"] != "Buy" or ticker not in tickers:
            continue
        qty = txn["quantity"] or 0.0
        weighted[ticker] = weighted.get(ticker, 0.0) + qty * _fx(txn)
        qty_total[ticker] = qty_total.get(ticker, 0.0) + qty
    return {t: (weighted[t] / qty_total[t]) for t in tickers if qty_total.get(t)}


def portfolio_gain_fx_decomposition(account_ids: list) -> tuple:
    """Decomposes OPEN-holdings unrealized gain, summed in BASE_CURRENCY across account_ids, into
    (gain_ex_fx, gain_actual). gain_actual re-derives unrealized_pnl()'s math (today's live FX
    rate); gain_ex_fx re-expresses today's market value using each holding's own purchase-
    quantity-weighted-average exchange rate instead, isolating the equity-only return. Unpriced
    holdings are excluded from both legs, matching holdings_with_market_value()'s own behaviour."""
    gain_ex_fx = 0.0
    gain_actual = 0.0
    for aid in account_ids:
        holdings = derive_account_holdings(aid)
        if not holdings:
            continue
        prices = _current_price_map(list(holdings.keys()))
        avg_fx = _avg_purchase_fx_rate(aid, set(holdings.keys()))
        for ticker, h in holdings.items():
            total_investment = h["accounts"][0]["total_investment"]
            priced = prices.get(ticker)
            if not priced or not priced[0]:
                continue
            price, currency = priced
            currency = currency or h["currency"]
            shares = h["global_shares"]
            market_value = shares * price * get_rate_to_base(currency)
            gain_actual += market_value - total_investment
            purchase_fx = avg_fx.get(ticker, get_rate_to_base(currency))
            market_value_ex_fx = shares * price * purchase_fx
            gain_ex_fx += market_value_ex_fx - total_investment
    return gain_ex_fx, gain_actual


def _chain_link_twr(series: list) -> Optional[float]:
    """Shared chain-linking core for portfolio_twr_fx()/portfolio_twr_ex_fx(). `series` is a list of
    (date, total_value, net_contributions) tuples, sorted by date. Sub-periods whose starting value
    is ~0 are skipped rather than included as 0% or blown up by near-zero division. Fewer than 2
    points → None (not 0%)."""
    if len(series) < 2:
        return None
    growth = 1.0
    counted = 0
    for (_prev_date, v_start, nc_start), (_date, v_end, nc_end) in zip(series, series[1:]):
        if abs(v_start) < _EPS:
            continue
        flow = nc_end - nc_start
        sub_return = (v_end - v_start - flow) / v_start
        growth *= (1.0 + sub_return)
        counted += 1
    if not counted:
        return None
    return round((growth - 1.0) * 100, 2)


def _merge_value_history(account_ids: list) -> list:
    """Sums `total_value`/`net_contributions` across accounts per date — a date missing from one
    account's history simply contributes 0 for it (not an inner join), so an account opened later
    still contributes from its own start date without distorting earlier dates."""
    by_date: dict[str, dict] = {}
    for aid in account_ids:
        for row in get_value_history(aid):
            d = row["snapshot_date"]
            entry = by_date.setdefault(d, {"total_value": 0.0, "net_contributions": 0.0})
            entry["total_value"] += row["total_value"] or 0.0
            entry["net_contributions"] += row["net_contributions"] or 0.0
    return [(d, by_date[d]["total_value"], by_date[d]["net_contributions"]) for d in sorted(by_date)]


def portfolio_twr_fx(account_ids: list) -> Optional[float]:
    """True chain-linked Time-Weighted Return (%), 'actual/with-FX' variant, from the existing
    account_value_history table merged across account_ids."""
    series = _merge_value_history(account_ids)
    return _chain_link_twr(series)


def portfolio_twr_ex_fx(account_ids: list) -> Optional[float]:
    """FX-neutral TWR variant (%): synthesizes a total_value series where each currency's equity
    contribution is revalued at its own earliest-observed ('baseline') fx_rate instead of the
    fx_rate actually in effect on each date, isolating the equity-only return from FX movement.
    A date/holding with no currency-breakdown row (e.g. a ticker whose price history doesn't reach
    that far back) falls back to its actual (FX-inclusive) equity contribution for that slice rather
    than silently dropping to 0 — collapsing to 0 would fabricate a near-total-loss sub-period the
    first time coverage is incomplete, which previously drove the whole chain-linked result to -100%."""
    by_date_currency: dict[str, dict] = {}
    baseline_fx: dict[str, float] = {}
    baseline_date: dict[str, str] = {}
    covered_base_by_date: dict[str, float] = {}
    for aid in account_ids:
        for row in get_value_history_currency(aid):
            d, currency = row["snapshot_date"], row["currency"]
            entry = by_date_currency.setdefault(d, {})
            entry[currency] = entry.get(currency, 0.0) + (row["equity_value_native"] or 0.0)
            covered_base_by_date[d] = covered_base_by_date.get(d, 0.0) + (row["equity_value_base"] or 0.0)
            if currency not in baseline_date or d < baseline_date[currency]:
                baseline_date[currency] = d
                baseline_fx[currency] = row["fx_rate"]

    cash_and_contrib_by_date: dict[str, dict] = {}
    for aid in account_ids:
        for row in get_value_history(aid):
            d = row["snapshot_date"]
            entry = cash_and_contrib_by_date.setdefault(
                d, {"cash_value": 0.0, "net_contributions": 0.0, "equity_value": 0.0}
            )
            entry["cash_value"] += row["cash_value"] or 0.0
            entry["net_contributions"] += row["net_contributions"] or 0.0
            entry["equity_value"] += row["equity_value"] or 0.0

    series = []
    for d in sorted(cash_and_contrib_by_date):
        cash = cash_and_contrib_by_date[d]["cash_value"]
        nc = cash_and_contrib_by_date[d]["net_contributions"]
        equity_actual = cash_and_contrib_by_date[d]["equity_value"]
        covered_ex_fx = sum(
            native * baseline_fx.get(currency, 1.0)
            for currency, native in by_date_currency.get(d, {}).items()
        )
        uncovered_actual = equity_actual - covered_base_by_date.get(d, 0.0)
        equity_ex_fx = covered_ex_fx + uncovered_actual
        series.append((d, cash + equity_ex_fx, nc))
    return _chain_link_twr(series)
