# GUI name: "Accounts". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import csv
import io
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

import time_engine
import market_pulse
from config import BASE_CURRENCY, HISTORICAL_DIR, PORTFOLIO_PATH, load_config
from db_accounts import (
    add_price_history, add_transaction, delete_transaction, get_account, get_accounts,
    get_pending_topup, get_price_as_of, get_price_history, get_transaction, get_transactions,
    get_value_history, get_watchlist_items, resolve_pending_topup, update_account,
    update_transaction, upsert_performance_cache, upsert_value_snapshot,
    upsert_value_snapshot_currency,
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


def _fee_fx(txn) -> float:
    """The fee's own exchange rate, independent of the trade leg's `_fx()` — a fee can be billed in
    a different currency than the trade itself (e.g. an FX spread fee already quoted in GBP on a
    USD trade). Falls back to the trade's own rate when no fee-specific rate was resolved, which
    also keeps pre-migration rows (where `fee_exchange_rate` is NULL) behaving exactly as before."""
    rate = txn["fee_exchange_rate"]
    return rate if rate is not None else _fx(txn)


def _gross_base(txn) -> float:
    """Monetary value of a transaction in base currency (qty defaults to 1 for cash-type rows)."""
    qty = txn["quantity"] if txn["quantity"] is not None else 1.0
    price = txn["unit_price"] or 0.0
    return qty * price * _fx(txn)


def _cash_delta(txn) -> float:
    gross = _gross_base(txn)
    fee_base = (txn["fee"] or 0.0) * _fee_fx(txn)
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
    prices = current_price_map(list(holdings.keys()))
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
    prices = current_price_map(list(holdings.keys()))
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


def current_price_map(tickers: list) -> dict:
    """Live-aware: prefers `market_pulse_cache`'s price over `stock_signals.current_price`
    whenever the cache row is newer than stock_signals' own last update — not gated by an
    absolute-age cutoff, since the background jobs that keep market_pulse_cache warm (the
    10-minute intraday scan, on-demand refresh triggers) run far coarser than the UI's own
    REFRESH_RATE, and a price a few minutes old is still far better than the once-nightly
    stock_signals snapshot it would otherwise fall back to."""
    if not tickers:
        return {}
    from account_scraper_engine import latest_price, parse_pension_account_id
    from treasury_bill_engine import current_price_for_ticker, parse_tbill_buy_txn_id

    result: dict[str, tuple] = {}
    market_tickers = []
    for ticker in tickers:
        pension_id = parse_pension_account_id(ticker)
        if pension_id is not None:
            priced = latest_price(pension_id)
            if priced:
                result[ticker] = priced
        elif parse_tbill_buy_txn_id(ticker) is not None:
            priced = current_price_for_ticker(ticker)
            if priced:
                result[ticker] = priced
        else:
            market_tickers.append(ticker)
    if not market_tickers:
        return result

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
            if r["last_updated"] and r["price"]:
                live_prices[r["ticker"]] = (r["price"], r["last_updated"])
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
            f"SELECT ticker, current_price, currency, last_updated FROM stock_signals WHERE ticker IN ({placeholders})",
            market_tickers
        )
        for r in cursor.fetchall():
            live = live_prices.get(r["ticker"])
            price = r["current_price"]
            if live and live[1] >= _epoch(r["last_updated"]):
                price = live[0]
            result[r["ticker"]] = (price, r["currency"])
    except Exception as e:
        logger.error("Failed to load current prices: %s", e)
    finally:
        if conn:
            conn.close()
    return result


def _epoch(stored_utc: Optional[str]) -> float:
    """Parses a `"%Y-%m-%d %H:%M:%S"` UTC timestamp (SQLite storage format) to a Unix epoch,
    for comparing against market_pulse_cache's own epoch-float `last_updated`."""
    if not stored_utc:
        return 0.0
    try:
        return datetime.strptime(stored_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def held_tickers_lightweight() -> list:
    """Cheap approximation of get_combined_holdings().keys() for callers that only need the
    ticker universe (e.g. deciding what to keep warm in market_pulse_cache) — a single query
    against account_transactions instead of a full per-account average-cost ledger walk, since
    that computation is wasted work when all that's actually needed is 'which tickers exist'.
    May include a recently-closed position's ticker for a little while after it's fully sold —
    harmless for a keep-warm check, unlike get_combined_holdings()'s own open-holdings-only
    scope which the ledger math actually needs to get right."""
    tickers: set = set()
    for value in _read_portfolio_json().values():
        t = value.get("ticker")
        if t:
            tickers.add(t)
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT t.ticker FROM account_transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = 'Trading' AND a.deleted_at IS NULL AND t.ticker IS NOT NULL
            AND t.ticker NOT LIKE 'TBILL-%'
        """)
        tickers.update(r["ticker"] for r in cursor.fetchall())
    except Exception as e:
        logger.error("Failed to load lightweight held-ticker list: %s", e)
    finally:
        if conn:
            conn.close()

    ignored = {normalize_ticker(t) for t in load_config().get("IGNORED_TICKERS", [])}
    return [t for t in tickers if normalize_ticker(t) not in ignored]


def tickers_needing_refresh(tickers: list, refresh_rate: int) -> list:
    """Held tickers whose market_pulse_cache row is older than refresh_rate seconds, while
    either of the two markets this app tracks (`GET /api/system/market-status`'s own scope —
    UK/US) is open — plus any ticker with NO cached row at all, regardless of market hours.
    The market-hours gate exists to avoid needlessly re-fetching a ticker whose price can't
    have moved since the market shut, but that reasoning doesn't apply to a genuinely missing
    row (a restart or maintenance-prune gap, or a newly-bought ticker) — without this bootstrap
    exception a ticker with no row stays permanently unrecoverable for the rest of a closure,
    since normal polling would never trigger a fetch to create the first row. Gated once per
    call rather than per-ticker via time_engine.ticker_exchange() — that call falls back to a
    config read for any ticker with no recognised suffix/currency, which is cheap for one ticker
    but adds up badly across every held ticker on every poll of the accounts-API endpoints; a
    plain LSE-or-NYSE-open check is the same practical scope GET /api/system/market-status
    already exposes, at a small, constant cost per call. Used by the HA-polled accounts
    endpoints to trigger a real fetch when due, mirroring the same needs_refresh pattern
    GET /api/market-pulse already uses."""
    if not tickers:
        return []
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"SELECT ticker, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            tickers
        )
        cache_map = {r["ticker"]: r["last_updated"] for r in cursor.fetchall()}
    except Exception as e:
        logger.error("Failed to check tickers needing refresh: %s", e)
        return []
    finally:
        if conn:
            conn.close()

    missing = [t for t in tickers if t not in cache_map]
    if not (market_pulse.is_exchange_open("LSE") or market_pulse.is_exchange_open("NYSE")):
        return missing

    now = time.time()
    return [t for t in tickers if now - cache_map.get(t, 0) > refresh_rate]


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
    prices = current_price_map(list(open_holdings.keys()))

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
    cash = 0.0 if acc["account_type"] in ("Pension", "House") else cash_balance(account_id)
    return round(cash + equity, 2)


def unrealized_pnl(account_id: int) -> float:
    """Live equity value minus cost basis of currently-open holdings."""
    rows = holdings_with_market_value(account_id)
    return round(sum(r["market_value"] - r["total_investment"] for r in rows), 2)


_RETURN_WINDOWS = {"1d": 1, "1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365}

# Nightly snapshot writes one row per day, so the closest on-or-before-target row should
# never be more than a day or two stale. A wider gap means a night's snapshot run failed
# (e.g. snapshot_all_accounts() errored on an earlier account) rather than the account
# genuinely predating the window — that case is None, not a misleadingly stale number.
_MAX_BASELINE_GAP_DAYS = 2


def period_returns(account_id: int) -> dict:
    """1D/1W/1M/3M/6M/1Y gain/loss in BASE_CURRENCY, excluding the effect of deposits/withdrawals
    during the period. Deliberately currency, not %: dividing by the period's starting value blows
    up into a meaningless number whenever that baseline is small (e.g. a lookback window older than
    the account itself falls back to the earliest snapshot, which can be near-zero right after
    opening) — the currency amount stays sane and bounded regardless. Each value is a float, or
    None when there's no snapshot history at all yet, or when the nearest available snapshot for
    that window is stale by more than _MAX_BASELINE_GAP_DAYS (a missed nightly snapshot)."""
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
        target_date = today - timedelta(days=days)
        target_date_str = target_date.isoformat()
        candidates = [row for row in history if row["snapshot_date"] <= target_date_str]
        if candidates:
            baseline = candidates[-1]
            gap_days = (target_date - datetime.strptime(baseline["snapshot_date"], "%Y-%m-%d").date()).days
            if gap_days > _MAX_BASELINE_GAP_DAYS:
                returns[key] = None
                continue
        else:
            baseline = history[0]
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
    by both the Crash/Moonshot scan and the dedicated per-minute Account Performance Refresh job
    (see `refresh_all_trading_performance_caches`) for every Trading account. Also stores
    realized_pnl/dividend_income/interest_income from the same account_summary() call so every
    field in the cache row is refreshed together — a consumer reading only this cache never mixes
    a stale cached figure with a live one."""
    summary = account_summary(account_id)
    returns = period_returns(account_id)
    upsert_performance_cache(
        account_id,
        total_value=total_value(account_id),
        equity_value=summary["equity_value"],
        cash_balance=cash_balance(account_id),
        unrealized_pnl=unrealized_pnl(account_id),
        return_1d=returns["1d"], return_1w=returns["1w"], return_1m=returns["1m"],
        return_3m=returns["3m"], return_6m=returns["6m"], return_1y=returns["1y"],
        mwrr=money_weighted_return(account_id),
        realized_pnl=summary["realized_pnl"],
        dividend_income=summary["dividend"],
        interest_income=summary["interest"],
        last_updated=time.time(),
    )


def refresh_all_trading_performance_caches() -> None:
    """Refreshes `account_performance_cache` for every Trading account — the one canonical loop,
    shared by the Crash/Moonshot scan and the faster dedicated per-minute refresh job, so the two
    callers can't drift into separate account-filtering logic."""
    for acc in get_accounts():
        if acc["account_type"] != "Trading":
            continue
        try:
            refresh_performance_cache(acc["id"])
        except Exception:
            logger.error("Failed to refresh performance cache for account %s", acc["id"], exc_info=True)


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
    """Nightly job body: writes today's value snapshot for every account. Returns rows written.
    Each account is isolated so one account's pricing failure (e.g. a delisted ticker) can't
    silently skip the snapshot for every account after it that night — a prior all-or-nothing
    loop left later accounts with a stale baseline until a manual re-run."""
    today = datetime.now(timezone.utc).date().isoformat()
    written = 0
    failed = []
    for acc in get_accounts():
        aid = acc["id"]
        try:
            open_holdings, _closed, _realized, _realized_by_txn = _ledger_for_account(aid)
            equity, breakdown = _equity_value_for_account_with_breakdown(acc, open_holdings)
            # Pension/House have no real cash sub-ledger — cash_balance() would just return initial_cash
            # as a phantom baseline, double-counting money already represented in equity_value.
            cash = 0.0 if acc["account_type"] in ("Pension", "House") else cash_balance(aid)
            contributions = net_contributions(aid)
            upsert_value_snapshot(aid, today, round(cash + equity, 2), round(cash, 2), round(equity, 2), contributions)
            _write_currency_breakdown(aid, today, breakdown)
            written += 1
        except Exception as e:
            logger.warning("Account Value Snapshot failed for account %s (%s): %s", acc["name"], aid, e)
            failed.append(acc["name"])
    if failed:
        raise RuntimeError(f"Snapshot failed for account(s): {', '.join(failed)} ({written} succeeded)")
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
    cash = 0.0 if acc["account_type"] in ("Pension", "House") else cash_balance(account_id)
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
    from db_accounts import get_treasury_bill_by_ticker
    from treasury_bill_engine import accreted_price, parse_tbill_buy_txn_id

    tickers = sorted({t["ticker"] for t in transactions if t["ticker"]})
    price_series: dict[str, pd.Series] = {}
    tbill_rows: dict[str, dict] = {}
    for ticker in tickers:
        pension_id = parse_pension_account_id(ticker)
        if pension_id is not None:
            series = scraped_price_series(pension_id)
            if not series.empty:
                price_series[ticker] = series
            continue
        if parse_tbill_buy_txn_id(ticker) is not None:
            bill = get_treasury_bill_by_ticker(ticker)
            if bill:
                tbill_rows[ticker] = bill
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
            bill = tbill_rows.get(ticker)
            if bill is not None:
                return accreted_price(bill, date_str), holding["currency"], fx_rate_on_date(holding["currency"], date_str)
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
        # House has no real cash sub-ledger — its value is the scraped equity price alone.
        cash = 0.0
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


def scraped_price_performance(account_id: int) -> dict:
    """Performance % over 1 month / YTD / 1 year, derived from the scraped/imported price
    history (`account_price_history`) rather than the value snapshot history — gives a
    meaningful number from day one since the price itself is a like-for-like return series,
    unaffected by contribution timing. Works for any account type backed by that table
    (Pension, House)."""
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
