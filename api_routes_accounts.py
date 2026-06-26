import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from accounts_engine import (
    _ticker_known, create_transfer, delete_transaction_with_pair, export_transactions_csv,
    fx_rate_on_date, import_csv_activities, import_ghostfolio_activities, is_unresolved_ticker,
    resnapshot_account,
)
import notification_engine
from api_deps import limiter, _error_500
from config import load_config
from database import (
    get_accounts,
    get_account,
    create_account,
    update_account,
    soft_delete_account,
    get_transactions,
    get_transaction,
    add_transaction,
    update_transaction,
    get_connection,
)
from profile_engine import update_single_profile
from scheduler_engine import run_account_value_snapshot
from utils import normalize_ticker
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

accounts_router = APIRouter()

_TXN_TYPES = frozenset({"Buy", "Sell", "Fee", "Dividend", "Interest", "Cash", "Transfer"})


class AccountBody(BaseModel):
    name: str
    currency: str
    initial_cash: float = 0.0
    note: Optional[str] = None
    opened_date: Optional[str] = None


class TransactionBody(BaseModel):
    txn_type: str
    txn_date: str
    ticker: Optional[str] = None
    isin: Optional[str] = None
    company_name: Optional[str] = None
    currency: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    fee: float = 0.0
    exchange_rate: Optional[float] = None
    notes: Optional[str] = None
    update_cash: bool = True
    price_in_pence: bool = False


class ImportGhostfolioBody(BaseModel):
    ghostfolio_account_id: str


def _resolve_exchange_rate(currency: Optional[str], exchange_rate: Optional[float], txn_date: str) -> float:
    if exchange_rate is not None:
        return exchange_rate
    return fx_rate_on_date(currency, txn_date)


@accounts_router.get("/accounts")
async def api_list_accounts():
    return JSONResponse(content={"status": "success", "accounts": get_accounts()})


@accounts_router.post("/accounts")
@limiter.limit("60/minute")
async def api_create_account(request: Request, body: AccountBody, background_tasks: BackgroundTasks):
    try:
        account_id = create_account(
            name=body.name.strip(),
            currency=body.currency.upper().strip(),
            initial_cash=body.initial_cash,
            note=body.note,
            opened_date=body.opened_date,
        )
        if account_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to create account."})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Account created.", "id": account_id})
    except Exception as e:
        logger.error("api_create_account failed: %s", e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}")
@limiter.limit("30/minute")
async def api_update_account(request: Request, account_id: int, body: AccountBody):
    try:
        if get_account(account_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        ok = update_account(
            account_id,
            name=body.name.strip(),
            currency=body.currency.upper().strip(),
            initial_cash=body.initial_cash,
            note=body.note,
            opened_date=body.opened_date,
        )
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to update account."})
        return JSONResponse(content={"status": "success", "message": "Account updated."})
    except Exception as e:
        logger.error("api_update_account %s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.delete("/accounts/{account_id}")
@limiter.limit("30/minute")
async def api_delete_account(request: Request, account_id: int):
    try:
        if get_account(account_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        soft_delete_account(account_id)
        return JSONResponse(content={"status": "success", "message": "Account deleted."})
    except Exception as e:
        logger.error("api_delete_account %s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.get("/accounts/{account_id}/transactions")
async def api_list_transactions(account_id: int):
    if get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    return JSONResponse(content={"status": "success", "transactions": get_transactions(account_id)})


@accounts_router.post("/accounts/{account_id}/transactions")
@limiter.limit("30/minute")
async def api_create_transaction(
    request: Request, account_id: int, body: TransactionBody, background_tasks: BackgroundTasks
):
    try:
        acc = get_account(account_id)
        if acc is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        if body.txn_type not in _TXN_TYPES:
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": f"txn_type must be one of: {', '.join(sorted(_TXN_TYPES))}"},
            )
        if body.txn_type == "Transfer":
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": "Use POST /accounts/{id}/transfer to record a transfer."},
            )
        ticker = normalize_ticker(body.ticker) if body.ticker else None
        if ticker and not _ticker_known(ticker):
            background_tasks.add_task(update_single_profile, ticker)
        currency = body.currency or acc["currency"]
        exchange_rate = _resolve_exchange_rate(currency, body.exchange_rate, body.txn_date)
        txn_id = add_transaction(
            account_id=account_id,
            txn_type=body.txn_type,
            txn_date=body.txn_date,
            ticker=ticker,
            isin=body.isin,
            company_name=body.company_name,
            currency=currency,
            quantity=body.quantity,
            unit_price=body.unit_price,
            fee=body.fee,
            exchange_rate=exchange_rate,
            notes=body.notes,
            update_cash=body.update_cash,
            price_in_pence=body.price_in_pence or currency == "GBp",
        )
        if txn_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add transaction."})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Transaction added.", "id": txn_id})
    except Exception as e:
        logger.error("api_create_transaction account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}/transactions/{txn_id}")
@limiter.limit("30/minute")
async def api_update_transaction(
    request: Request, account_id: int, txn_id: int, body: TransactionBody, background_tasks: BackgroundTasks
):
    try:
        acc = get_account(account_id)
        if acc is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        existing = get_transaction(txn_id)
        if existing is None or existing["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Transaction not found."})
        if existing["txn_type"] == "Transfer" or body.txn_type == "Transfer":
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": "Transfers can't be edited — delete and recreate instead."},
            )
        if body.txn_type not in _TXN_TYPES:
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": f"txn_type must be one of: {', '.join(sorted(_TXN_TYPES))}"},
            )
        ticker = normalize_ticker(body.ticker) if body.ticker else None
        currency = body.currency or acc["currency"]
        exchange_rate = _resolve_exchange_rate(currency, body.exchange_rate, body.txn_date)
        ok = update_transaction(
            txn_id,
            txn_type=body.txn_type,
            txn_date=body.txn_date,
            ticker=ticker,
            isin=body.isin,
            company_name=body.company_name,
            currency=currency,
            quantity=body.quantity,
            unit_price=body.unit_price,
            fee=body.fee,
            exchange_rate=exchange_rate,
            notes=body.notes,
            update_cash=body.update_cash,
            price_in_pence=body.price_in_pence or currency == "GBp",
        )
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to update transaction."})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Transaction updated."})
    except Exception as e:
        logger.error("api_update_transaction %s failed: %s", txn_id, e)
        return _error_500(e)


@accounts_router.delete("/accounts/{account_id}/transactions/{txn_id}")
@limiter.limit("30/minute")
async def api_delete_transaction(request: Request, account_id: int, txn_id: int, background_tasks: BackgroundTasks):
    try:
        existing = get_transaction(txn_id)
        if existing is None or existing["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Transaction not found."})
        delete_transaction_with_pair(txn_id)
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Transaction deleted."})
    except Exception as e:
        logger.error("api_delete_transaction %s failed: %s", txn_id, e)
        return _error_500(e)


class TransferBody(BaseModel):
    to_account_id: int
    amount: float
    txn_date: str
    fee: float = 0.0
    notes: Optional[str] = None


@accounts_router.post("/accounts/{account_id}/transfer")
@limiter.limit("30/minute")
async def api_create_transfer(request: Request, account_id: int, body: TransferBody, background_tasks: BackgroundTasks):
    try:
        if get_account(account_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        if get_account(body.to_account_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Destination account not found."})
        if body.amount <= 0:
            return JSONResponse(status_code=422, content={"status": "error", "message": "amount must be positive."})
        result = create_transfer(account_id, body.to_account_id, body.amount, body.txn_date, body.fee, body.notes)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        background_tasks.add_task(resnapshot_account, account_id)
        background_tasks.add_task(resnapshot_account, body.to_account_id)
        return JSONResponse(content={"status": "success", "message": "Transfer recorded.", **result})
    except Exception as e:
        logger.error("api_create_transfer account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.get("/accounts/{account_id}/export")
@limiter.limit("20/minute")
async def api_export_transactions(request: Request, account_id: int):
    try:
        acc = get_account(account_id)
        if acc is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        csv_text = export_transactions_csv(account_id)
        filename = f"{acc['name'].replace(' ', '_')}_transactions.csv"
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error("api_export_transactions account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.get("/fx-rate")
@limiter.limit("30/minute")
async def api_fx_rate(request: Request, currency: str, date: str):
    try:
        rate = fx_rate_on_date(currency, date)
        return JSONResponse(content={"status": "success", "rate": rate})
    except Exception as e:
        logger.error("api_fx_rate failed for %r/%r: %s", currency, date, e)
        return _error_500(e)


@accounts_router.get("/ticker-lookup")
@limiter.limit("20/minute")
async def api_ticker_lookup(request: Request, q: str):
    try:
        ticker = normalize_ticker(q)
        if not ticker:
            return JSONResponse(status_code=422, content={"status": "error", "message": "q is required."})
        info = yahoo_engine.get_ticker_info(ticker)
        if not info:
            return JSONResponse(content={"status": "success", "found": False, "ticker": ticker})
        return JSONResponse(content={
            "status": "success",
            "found": True,
            "ticker": ticker,
            "company_name": info.get("longName") or info.get("shortName") or ticker,
            "currency": info.get("currency"),
            "quote_type": info.get("quoteType"),
        })
    except Exception as e:
        logger.error("api_ticker_lookup failed for %r: %s", q, e)
        return _error_500(e)


@accounts_router.get("/accounts/ghostfolio-accounts")
@limiter.limit("20/minute")
async def api_list_ghostfolio_accounts(request: Request):
    try:
        config_data = load_config()
        gf_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {})
        discovered = {a["id"]: a for a in gf_accounts.get("discovered", [])}
        active_ids = gf_accounts.get("active", [])
        accounts = [
            {"id": acc_id, "name": discovered[acc_id]["name"], "currency": discovered[acc_id]["currency"]}
            for acc_id in active_ids if acc_id in discovered
        ]
        return JSONResponse(content={"status": "success", "accounts": accounts})
    except Exception as e:
        logger.error("api_list_ghostfolio_accounts failed: %s", e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/import-ghostfolio")
@limiter.limit("10/minute")
async def api_import_ghostfolio(request: Request, account_id: int, body: ImportGhostfolioBody, background_tasks: BackgroundTasks):
    try:
        if get_account(account_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        if not body.ghostfolio_account_id:
            return JSONResponse(status_code=422, content={"status": "error", "message": "ghostfolio_account_id is required."})
        result = import_ghostfolio_activities(account_id, body.ghostfolio_account_id)
        if result.get("error"):
            return JSONResponse(status_code=400, content={"status": "error", "message": result["error"]})
        tickers = {txn["ticker"] for txn in get_transactions(account_id) if txn["ticker"]}
        for ticker in tickers:
            if not _ticker_known(ticker) and not is_unresolved_ticker(ticker):
                background_tasks.add_task(update_single_profile, ticker)
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={
            "status": "success",
            "message": f"Imported {result['imported']} activities ({result['skipped']} skipped).",
            "imported": result["imported"],
            "skipped": result["skipped"],
        })
    except Exception as e:
        logger.error("api_import_ghostfolio account=%s failed: %s", account_id, e)
        return _error_500(e)


def _notify_csv_import_skips(account_id: int, account_name: str, skipped_rows: list) -> None:
    lines = [f"{r['date'] or '?'}  {r['ticker'] or '?'}  — {r['reason']}" for r in skipped_rows]
    notification_engine.notify(
        "accounts_csv_import",
        "CSV Import — Skipped Rows",
        f"Account '{account_name}': {len(skipped_rows)} row(s) skipped during CSV import:\n" + "\n".join(lines),
        level="warning",
    )


@accounts_router.post("/accounts/{account_id}/import-csv")
@limiter.limit("10/minute")
async def api_import_csv(request: Request, account_id: int, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    try:
        acc = get_account(account_id)
        if acc is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        raw = await file.read()
        csv_text = raw.decode("utf-8-sig")
        result = import_csv_activities(account_id, csv_text)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        tickers = {txn["ticker"] for txn in get_transactions(account_id) if txn["ticker"]}
        for ticker in tickers:
            if not _ticker_known(ticker):
                background_tasks.add_task(update_single_profile, ticker)
        background_tasks.add_task(resnapshot_account, account_id)
        skipped_rows = result["skipped_rows"]
        if skipped_rows:
            background_tasks.add_task(_notify_csv_import_skips, account_id, acc["name"], skipped_rows)
        message = f"Imported {result['imported']} rows ({result['skipped']} skipped, {result['ignored']} ignored)."
        if skipped_rows:
            message += " See the Notifications panel for the per-row detail (date, ticker, reason)."
        return JSONResponse(content={
            "status": "success",
            "message": message,
            "imported": result["imported"],
            "skipped": result["skipped"],
            "ignored": result["ignored"],
            "skipped_rows": skipped_rows,
        })
    except UnicodeDecodeError:
        return JSONResponse(status_code=422, content={"status": "error", "message": "File is not a valid UTF-8 CSV."})
    except Exception as e:
        logger.error("api_import_csv account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/value-snapshot/trigger")
async def api_trigger_account_value_snapshot(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_account_value_snapshot)
    return JSONResponse(content={
        "status": "queued",
        "message": "Account Value Snapshot job queued. Check system notifications for completion.",
    })
