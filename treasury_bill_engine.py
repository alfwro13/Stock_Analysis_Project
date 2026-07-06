# GUI name: "UK Treasury Bills". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import db_accounts
from db_accounts import (
    add_transaction, confirm_treasury_bill_ytm, create_treasury_bill, delete_transaction,
    get_account, get_treasury_bill, get_treasury_bill_by_ticker, get_treasury_bills_for_account,
    get_treasury_bills_pending_ytm_confirmation, get_open_treasury_bills_due,
    mark_treasury_bill_matured, update_transaction,
)
from notification_engine import notify

logger = logging.getLogger(__name__)

_TBILL_TICKER_RE = re.compile(r'^TBILL-(\d+)$')


def tbill_ticker(buy_txn_id: int) -> str:
    return f"TBILL-{buy_txn_id}"


def parse_tbill_buy_txn_id(ticker: Optional[str]) -> Optional[int]:
    if not ticker:
        return None
    match = _TBILL_TICKER_RE.match(ticker)
    return int(match.group(1)) if match else None


def _parse_date(date_str: str):
    return datetime.strptime(date_str[:10], "%Y-%m-%d").date()


def estimate_face_value(purchase_price: float, indicative_ytm: float, purchase_date: str, maturity_date: str) -> float:
    """Freetrade never states a Treasury Bill's face value directly — only the amount paid and an
    indicative yield, which is itself just an estimate ('you'll receive your yield on top of your
    original investment') since the real yield isn't fixed until the Friday DMO tender. Estimates
    the eventual redemption amount as amount + (amount x annualised yield x days/365), for the
    operator to accept or correct by hand before saving."""
    days = (_parse_date(maturity_date) - _parse_date(purchase_date)).days
    if days <= 0:
        return purchase_price
    return purchase_price * (1 + (indicative_ytm / 100.0) * (days / 365.0))


def buy_treasury_bill(
    account_id: int,
    purchase_date: str,
    face_value: float,
    purchase_price: float,
    maturity_date: str,
    auto_reinvest: bool = False,
    notes: Optional[str] = None,
    indicative_ytm: Optional[float] = None,
) -> dict:
    """Books a purchase as a Buy against a unique per-purchase synthetic ticker (`TBILL-{txn_id}`),
    so concurrently-held bills never blend cost basis the way a shared ticker would in the
    average-cost ledger (_ledger_for_account). The ticker embeds the transaction's own id, so it
    can only be assigned after the row exists — insert, then backfill the ticker onto that same
    row, mirroring how create_transfer() links its two legs after each is inserted. `face_value` is
    an estimate the caller computed (see estimate_face_value()) and may have hand-corrected — it's
    the exact amount the maturity sweep will later credit to cash, not re-derived from
    `indicative_ytm` at maturity time, since the real yield is never confirmed back to this app."""
    acc = get_account(account_id)
    if not acc:
        return {"error": "Account not found."}
    if acc["account_type"] != "Trading":
        return {"error": "UK Treasury Bills can only be bought in a Trading account."}
    if face_value <= 0 or purchase_price <= 0:
        return {"error": "Face value and purchase price must be greater than 0."}
    if purchase_price >= face_value:
        return {"error": "Purchase price must be less than face value — a Treasury Bill is bought at a discount."}
    if maturity_date <= purchase_date:
        return {"error": "Maturity date must be after the purchase date."}

    txn_id = add_transaction(
        account_id, "Buy", purchase_date, company_name="UK Treasury Bill",
        currency=acc["currency"], quantity=1, unit_price=purchase_price,
        update_cash=True, price_in_pence=False, notes=notes,
    )
    if txn_id is None:
        return {"error": "Failed to record the purchase."}

    ticker = tbill_ticker(txn_id)
    if not update_transaction(txn_id, ticker=ticker):
        delete_transaction(txn_id)
        return {"error": "Failed to assign a ticker to the purchase."}

    bill_id = create_treasury_bill(
        account_id, txn_id, ticker, face_value, purchase_price,
        purchase_date, maturity_date, auto_reinvest, notes, indicative_ytm,
        ytm_confirmed=indicative_ytm is None,
    )
    if bill_id is None:
        delete_transaction(txn_id)
        return {"error": "Failed to save the Treasury Bill record."}
    return {"bill_id": bill_id, "txn_id": txn_id, "ticker": ticker}


def accreted_price(bill_row: dict, as_of_date: str) -> float:
    """Straight-line accretion from the discount purchase price to face value — a T-bill's value
    curve needs no external price feed since it's fully determined by the two dates and two
    prices captured at purchase."""
    purchase_price = bill_row["purchase_price"]
    face_value = bill_row["face_value"]
    purchase_date = _parse_date(bill_row["purchase_date"])
    maturity_date = _parse_date(bill_row["maturity_date"])
    total_days = (maturity_date - purchase_date).days
    if total_days <= 0:
        return face_value
    elapsed = (_parse_date(as_of_date) - purchase_date).days
    elapsed = max(0, min(elapsed, total_days))
    return purchase_price + (face_value - purchase_price) * (elapsed / total_days)


def current_price_for_ticker(ticker: str) -> Optional[tuple]:
    """Resolves a `TBILL-{txn_id}` ticker to its accreted value as of today, for
    accounts_engine.current_price_map()'s dispatch — mirrors the Pension synthetic-ticker branch
    but computes the price analytically instead of reading a scraped price table."""
    bill = get_treasury_bill_by_ticker(ticker)
    if not bill:
        return None
    acc = get_account(bill["account_id"])
    if not acc:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    return (accreted_price(bill, today), acc["currency"])


def sweep_matured_bills() -> dict:
    """Daily job body: closes every Treasury Bill whose maturity date has arrived by posting the
    par-value Sell leg, and fires a reminder — never an automatic re-purchase, since the actual
    next yield isn't known until Friday's DMO tender — for any bill flagged to auto-reinvest."""
    from accounts_engine import resnapshot_account

    today = datetime.now(timezone.utc).date().isoformat()
    matured = 0
    reminders = 0
    for bill in get_open_treasury_bills_due(today):
        acc = get_account(bill["account_id"])
        if not acc:
            continue
        sell_txn_id = add_transaction(
            bill["account_id"], "Sell", bill["maturity_date"], ticker=bill["ticker"],
            company_name="UK Treasury Bill", currency=acc["currency"], quantity=1,
            unit_price=bill["face_value"], update_cash=True, price_in_pence=False,
            notes="Treasury Bill maturity payout",
        )
        if sell_txn_id is None:
            logger.error("Failed to post maturity payout for treasury bill %s", bill["id"])
            continue
        if not mark_treasury_bill_matured(bill["id"], sell_txn_id):
            logger.error("Failed to mark treasury bill %s matured after posting txn %s", bill["id"], sell_txn_id)
            continue
        matured += 1
        resnapshot_account(bill["account_id"])
        if bill["auto_reinvest"]:
            notify(
                "treasury_bill_reminder", "Reminder",
                f"A UK Treasury Bill in '{acc['name']}' matured today ({bill['face_value']:.2f} {acc['currency']} "
                f"received) and is flagged to auto-reinvest — place your next order and log it here once filled.",
                level="info",
            )
            reminders += 1
    return {"matured": matured, "reminders": reminders}


def list_treasury_bills(account_id: int) -> list:
    today = datetime.now(timezone.utc).date().isoformat()
    bills = get_treasury_bills_for_account(account_id)
    for bill in bills:
        bill["current_value"] = round(accreted_price(bill, today), 2)
    return bills


def bills_pending_ytm_confirmation(account_id: int) -> list:
    """Bills bought with an indicative (pre-tender) YTM whose Start Date has arrived — by then the
    Friday DMO tender has already happened, so the operator can now confirm the real yield instead
    of the last-week estimate the Buy T-Bill modal computed Face Value from."""
    today = datetime.now(timezone.utc).date().isoformat()
    return get_treasury_bills_pending_ytm_confirmation(today, account_id)


def confirm_ytm(bill_id: int, confirmed_ytm: Optional[float] = None, face_value: Optional[float] = None) -> dict:
    """Resolves the YTM-confirmation banner, and doubles as the general 'Edit' action for a bill's
    valuation at any time — Open or already Matured. `face_value` (if given directly, e.g. the
    operator knows the exact redemption figure) wins over recomputing from `confirmed_ytm`; given
    neither, leaves Face Value/indicative_ytm untouched ('Keep Estimate'). If the bill has already
    matured, also corrects the posted maturity Sell's amount to match, since that transaction — not
    this row — is what the account's cash balance actually derives from."""
    bill = get_treasury_bill(bill_id)
    if not bill:
        return {"error": "Treasury Bill not found."}
    if face_value is not None:
        new_face_value = face_value
        new_indicative_ytm = confirmed_ytm if confirmed_ytm is not None else bill["indicative_ytm"]
    elif confirmed_ytm is not None:
        new_face_value = round(estimate_face_value(bill["purchase_price"], confirmed_ytm, bill["purchase_date"], bill["maturity_date"]), 2)
        new_indicative_ytm = confirmed_ytm
    else:
        new_face_value = bill["face_value"]
        new_indicative_ytm = bill["indicative_ytm"]
    if new_face_value <= bill["purchase_price"]:
        return {"error": "Face value must be greater than the amount paid — a Treasury Bill is bought at a discount."}

    if not confirm_treasury_bill_ytm(bill_id, new_face_value, new_indicative_ytm):
        return {"error": "Failed to update the Treasury Bill."}

    if bill["status"] == "Matured" and bill["maturity_txn_id"]:
        update_transaction(bill["maturity_txn_id"], unit_price=new_face_value)

    return {"face_value": round(new_face_value, 2), "indicative_ytm": new_indicative_ytm}


def delete_treasury_bill(bill_id: int) -> dict:
    """Removes a mis-entered bill: the Buy leg, the maturity Sell leg if it has matured, and the
    treasury_bills row itself — there is no other edit path for a bought bill."""
    bill = get_treasury_bill(bill_id)
    if not bill:
        return {"error": "Treasury Bill not found."}
    delete_transaction(bill["buy_txn_id"])
    if bill["maturity_txn_id"]:
        delete_transaction(bill["maturity_txn_id"])
    if not db_accounts.delete_treasury_bill(bill_id):
        return {"error": "Failed to delete the Treasury Bill record."}
    return {"deleted": True, "account_id": bill["account_id"]}
