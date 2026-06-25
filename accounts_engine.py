# GUI name: "Accounts". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import json
import logging
from typing import Optional

from config import BASE_CURRENCY, PORTFOLIO_PATH
from db_accounts import get_account, get_accounts, get_transactions
from database import get_connection
from portfolio_service import get_rate_to_base
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

_EPS = 1e-9


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
    if ttype in ("Sell", "Dividend", "Interest", "Cash"):
        return gross - fee_base
    return 0.0


def _ledger_for_account(account_id: int):
    """Average-cost pass per ticker → (open_holdings, closed_positions, realized_total_base)."""
    by_ticker: dict[str, list] = {}
    for txn in get_transactions(account_id):
        if txn["txn_type"] not in ("Buy", "Sell") or not txn["ticker"]:
            continue
        by_ticker.setdefault(txn["ticker"], []).append(txn)

    open_holdings: dict[str, dict] = {}
    closed: list[dict] = []
    realized_total = 0.0

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
                if shares > _EPS:
                    avg = cost_base / shares
                    sell_qty = min(qty, shares)
                    realized += sell_qty * (unit * fx - avg)
                    cost_base -= sell_qty * avg
                    shares -= sell_qty
                sold += qty

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

    return open_holdings, closed, round(realized_total, 2)


def derive_account_holdings(account_id: Optional[int] = None) -> dict:
    """Current open holdings across one account (or all when account_id is None), keyed by ticker,
    in the portfolio.json shape (cost basis in BASE currency)."""
    if account_id is not None:
        account_ids = [account_id]
    else:
        account_ids = [acc["id"] for acc in get_accounts()]

    result: dict[str, dict] = {}
    for aid in account_ids:
        acc = get_account(aid)
        if not acc:
            continue
        open_holdings, _closed, _realized = _ledger_for_account(aid)
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
    _open, closed, _realized = _ledger_for_account(account_id)
    return closed


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


def cash_history(account_id: int) -> list:
    acc = get_account(account_id)
    if not acc:
        return []
    balance = acc["initial_cash"] or 0.0
    opening_date = acc["created_at"][:10] if acc["created_at"] else None
    history = [{"date": opening_date, "balance": round(balance, 2)}]
    for txn in get_transactions(account_id):
        if txn["update_cash"]:
            balance += _cash_delta(txn)
            history.append({"date": txn["txn_date"], "balance": round(balance, 2)})
    return history


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
    open_holdings, _closed, realized = _ledger_for_account(account_id)
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
