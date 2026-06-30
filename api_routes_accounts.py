import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from accounts_engine import (
    _ticker_known, account_summary, confirm_autotopup, create_transfer,
    delete_transaction_with_pair, dismiss_autotopup, export_transactions_csv,
    filter_value_history_by_period, fx_rate_on_date, import_csv_activities,
    pension_units_as_of, reconcile_cash, record_pension_contribution,
    record_pension_fee, resnapshot_account, resolve_watchlist_metadata,
    sync_house_purchase_price, sync_pension_opening_balance, watchlist_summary,
)
from account_scraper_engine import import_price_csv, price_as_of, run_scrape_for_account, test_scrape
import notification_engine
from api_deps import limiter, _error_500
from data_engine import fetch_and_save_single_ticker
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
    get_value_history,
    get_watchlist_items,
    add_watchlist_item,
    delete_watchlist_items,
    get_unresolved_pending_topups,
)
from profile_engine import update_single_profile
from scheduler_engine import (
    get_all_job_last_runs, register_account_scraper_job, register_account_topup_job,
    run_account_value_snapshot, unregister_account_scraper_job, unregister_account_topup_job,
)
from utils import normalize_ticker
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

accounts_router = APIRouter()

_TXN_TYPES = frozenset({"Buy", "Sell", "Fee", "Dividend", "Interest", "Cash", "Transfer"})
_ACCOUNT_TYPES = frozenset({"Trading", "House", "Pension", "Watchlist"})


class AccountBody(BaseModel):
    name: str
    currency: str
    initial_cash: float = 0.0
    note: Optional[str] = None
    opened_date: Optional[str] = None
    account_type: str = "Trading"
    pension_start_date: Optional[str] = None
    opening_balance_units: Optional[float] = None
    pension_ticker_label: Optional[str] = None


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


class ReconcileCashBody(BaseModel):
    actual_balance: float


class WatchlistItemBody(BaseModel):
    ticker: str


class WatchlistBulkDeleteBody(BaseModel):
    ids: list[int]


class ScraperConfigBody(BaseModel):
    scraper_url: str
    scraper_selector: str
    scraper_headers: dict = {}
    scrape_time: str = "02:00"
    scraper_enabled: bool = False


class ScraperTestBody(BaseModel):
    url: str
    selector: str
    headers: dict = {}


class AutoTopupConfigBody(BaseModel):
    enabled: bool = False
    amount: Optional[float] = None
    frequency: Optional[str] = None
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    notes: Optional[str] = None


class AutoTopupConfirmBody(BaseModel):
    pending_id: int
    amount: float
    txn_date: str


class AutoTopupDismissBody(BaseModel):
    pending_id: int


class PriceCsvImportBody(BaseModel):
    csv_text: str


class PensionContributionBody(BaseModel):
    txn_date: str
    amount: float
    unit_price: Optional[float] = None


class PensionFeeBody(BaseModel):
    txn_date: str
    units_after: Optional[float] = None
    units_removed: Optional[float] = None
    unit_price: Optional[float] = None


def _resolve_exchange_rate(currency: Optional[str], exchange_rate: Optional[float], txn_date: str) -> float:
    if exchange_rate is not None:
        return exchange_rate
    return fx_rate_on_date(currency, txn_date)


@accounts_router.get("/accounts")
async def api_list_accounts():
    accounts = get_accounts()
    job_runs = get_all_job_last_runs()
    for acc in accounts:
        if acc.get("scraper_enabled"):
            job = job_runs.get(f"account_scraper_{acc['id']}_job")
            acc["scraper_last_status"] = job["last_status"] if job else None
        else:
            acc["scraper_last_status"] = None
        if acc["account_type"] in ("Pension", "House"):
            acc["current_balance"] = account_summary(acc["id"]).get("equity_value", 0.0)
        elif acc["account_type"] == "Trading":
            summary = account_summary(acc["id"])
            acc["holdings_count"] = summary.get("holdings_count", 0)
            acc["equity_value"] = summary.get("equity_value", 0.0)
            acc["cash_balance"] = summary.get("cash_balance", 0.0)
            acc["pending_topups"] = get_unresolved_pending_topups(acc["id"])
        elif acc["account_type"] == "Watchlist":
            breakdown = watchlist_summary(acc["id"])
            acc["watchlist_count"] = breakdown["count"]
            acc["watchlist_breakdown"] = breakdown["by_type"]
    return JSONResponse(content={"status": "success", "accounts": accounts})


@accounts_router.post("/accounts")
@limiter.limit("120/minute")
async def api_create_account(request: Request, body: AccountBody, background_tasks: BackgroundTasks):
    try:
        if body.account_type not in _ACCOUNT_TYPES:
            return JSONResponse(status_code=400, content={"status": "error", "message": f"Invalid account_type. Must be one of: {sorted(_ACCOUNT_TYPES)}"})
        if body.account_type == "Watchlist":
            return JSONResponse(status_code=400, content={"status": "error", "message": "Watchlist accounts are created automatically and cannot be created manually."})
        account_id = create_account(
            name=body.name.strip(),
            currency=body.currency.upper().strip(),
            initial_cash=body.initial_cash,
            note=body.note,
            opened_date=body.opened_date,
            account_type=body.account_type,
            pension_start_date=body.pension_start_date,
            opening_balance_units=body.opening_balance_units,
            pension_ticker_label=body.pension_ticker_label,
        )
        if account_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to create account."})
        if body.account_type == "Pension":
            sync_pension_opening_balance(account_id)
        elif body.account_type == "House":
            sync_house_purchase_price(account_id)
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Account created.", "id": account_id})
    except Exception as e:
        logger.error("api_create_account failed: %s", e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}")
@limiter.limit("30/minute")
async def api_update_account(request: Request, account_id: int, body: AccountBody, background_tasks: BackgroundTasks):
    try:
        existing = get_account(account_id)
        if existing is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        if body.account_type not in _ACCOUNT_TYPES:
            return JSONResponse(status_code=400, content={"status": "error", "message": f"Invalid account_type. Must be one of: {sorted(_ACCOUNT_TYPES)}"})
        if body.account_type != existing["account_type"]:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Account type cannot be changed after creation."})
        ok = update_account(
            account_id,
            name=body.name.strip(),
            currency=body.currency.upper().strip(),
            initial_cash=body.initial_cash,
            note=body.note,
            opened_date=body.opened_date,
            account_type=body.account_type,
            pension_start_date=body.pension_start_date,
            opening_balance_units=body.opening_balance_units,
            pension_ticker_label=body.pension_ticker_label,
        )
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to update account."})
        if body.account_type == "Pension":
            sync_pension_opening_balance(account_id)
            background_tasks.add_task(resnapshot_account, account_id)
        elif body.account_type == "House":
            sync_house_purchase_price(account_id)
            background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Account updated."})
    except Exception as e:
        logger.error("api_update_account %s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.delete("/accounts/{account_id}")
@limiter.limit("30/minute")
async def api_delete_account(request: Request, account_id: int):
    try:
        existing = get_account(account_id)
        if existing is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        if existing["account_type"] == "Watchlist":
            return JSONResponse(status_code=400, content={"status": "error", "message": "The Watchlist account is managed by the system and cannot be deleted."})
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


@accounts_router.get("/accounts/{account_id}/value-history")
async def api_account_value_history(
    account_id: int,
    period: str = Query(default="max", pattern=r"^(1m|ytd|1y|max)$"),
):
    if get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    history = filter_value_history_by_period(get_value_history(account_id), period)
    return JSONResponse(content={"status": "success", "period": period, "data": history})


@accounts_router.post("/accounts/{account_id}/reconcile-cash")
@limiter.limit("30/minute")
async def api_reconcile_cash(
    request: Request, account_id: int, body: ReconcileCashBody, background_tasks: BackgroundTasks
):
    if get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    result = reconcile_cash(account_id, body.actual_balance)
    if result["txn_id"] is None and result["delta"] == 0.0:
        return JSONResponse(content={
            "status": "success", "delta": 0.0, "computed_balance": result["computed_balance"],
            "message": "Already balanced — no adjustment needed.",
        })
    if result["txn_id"] is None:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to book the adjustment transaction."})
    background_tasks.add_task(resnapshot_account, account_id)
    return JSONResponse(content={
        "status": "success", "txn_id": result["txn_id"], "delta": result["delta"],
        "computed_balance": result["computed_balance"],
    })


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
            background_tasks.add_task(fetch_and_save_single_ticker, ticker)
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


@accounts_router.get("/ticker-search")
@limiter.limit("30/minute")
async def api_ticker_search(request: Request, q: str):
    try:
        if not q or not q.strip():
            return JSONResponse(status_code=422, content={"status": "error", "message": "q is required."})
        results = yahoo_engine.search_ticker(q.strip())
        return JSONResponse(content={"status": "success", "results": results})
    except Exception as e:
        logger.error("api_ticker_search failed for %r: %s", q, e)
        return _error_500(e)


def _require_watchlist_account(account_id: int):
    acc = get_account(account_id)
    if acc is None:
        return None, JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    if acc["account_type"] != "Watchlist":
        return None, JSONResponse(status_code=400, content={"status": "error", "message": "This account is not a Watchlist account."})
    return acc, None


@accounts_router.get("/accounts/{account_id}/watchlist-items")
async def api_list_watchlist_items(account_id: int):
    acc, error = _require_watchlist_account(account_id)
    if error:
        return error
    return JSONResponse(content={"status": "success", "items": get_watchlist_items(acc["id"])})


@accounts_router.post("/accounts/{account_id}/watchlist-items")
@limiter.limit("30/minute")
async def api_add_watchlist_item(request: Request, account_id: int, body: WatchlistItemBody):
    try:
        acc, error = _require_watchlist_account(account_id)
        if error:
            return error
        ticker = normalize_ticker(body.ticker)
        if not ticker:
            return JSONResponse(status_code=422, content={"status": "error", "message": "ticker is required."})
        meta = resolve_watchlist_metadata(ticker)
        item_id = add_watchlist_item(
            acc["id"], ticker, meta["company_name"], meta["currency"], meta["quote_type"], meta["exchange"]
        )
        if item_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add ticker to watchlist."})
        return JSONResponse(content={"status": "success", "id": item_id})
    except Exception as e:
        logger.error("api_add_watchlist_item failed for account %s: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/watchlist-items/bulk-delete")
@limiter.limit("30/minute")
async def api_bulk_delete_watchlist_items(request: Request, account_id: int, body: WatchlistBulkDeleteBody):
    try:
        acc, error = _require_watchlist_account(account_id)
        if error:
            return error
        deleted = delete_watchlist_items(acc["id"], body.ids)
        return JSONResponse(content={"status": "success", "deleted": deleted})
    except Exception as e:
        logger.error("api_bulk_delete_watchlist_items failed for account %s: %s", account_id, e)
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
                background_tasks.add_task(fetch_and_save_single_ticker, ticker)
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


def _require_scraper_account(account_id: int):
    acc = get_account(account_id)
    if acc is None:
        return None, JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    if acc["account_type"] not in ("House", "Pension"):
        return None, JSONResponse(status_code=400, content={"status": "error", "message": "Only House and Pension accounts support a price scraper."})
    return acc, None


def _require_pension_account(account_id: int):
    acc = get_account(account_id)
    if acc is None:
        return None, JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    if acc["account_type"] != "Pension":
        return None, JSONResponse(status_code=400, content={"status": "error", "message": "This action is only available on Pension accounts."})
    return acc, None


def _require_trading_account(account_id: int):
    acc = get_account(account_id)
    if acc is None:
        return None, JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    if acc["account_type"] != "Trading":
        return None, JSONResponse(status_code=400, content={"status": "error", "message": "Auto Top-up is only available on Trading accounts."})
    return acc, None


_AUTOTOPUP_FREQUENCIES = frozenset({"monthly", "weekly"})


@accounts_router.put("/accounts/{account_id}/autotopup-config")
@limiter.limit("30/minute")
async def api_update_autotopup_config(request: Request, account_id: int, body: AutoTopupConfigBody):
    try:
        acc, error = _require_trading_account(account_id)
        if error:
            return error
        if body.enabled:
            if not body.amount or body.amount <= 0:
                return JSONResponse(status_code=400, content={"status": "error", "message": "amount must be greater than 0."})
            if body.frequency not in _AUTOTOPUP_FREQUENCIES:
                return JSONResponse(status_code=400, content={"status": "error", "message": "frequency must be 'monthly' or 'weekly'."})
            if body.frequency == "monthly" and not (body.day_of_month and 1 <= body.day_of_month <= 31):
                return JSONResponse(status_code=400, content={"status": "error", "message": "day_of_month must be between 1 and 31."})
            if body.frequency == "weekly" and not (body.day_of_week and 1 <= body.day_of_week <= 5):
                return JSONResponse(status_code=400, content={"status": "error", "message": "day_of_week must be between 1 (Mon) and 5 (Fri)."})
        ok = update_account(
            account_id,
            autotopup_enabled=body.enabled,
            autotopup_amount=body.amount,
            autotopup_frequency=body.frequency,
            autotopup_day_of_month=body.day_of_month,
            autotopup_day_of_week=body.day_of_week,
            autotopup_notes=body.notes,
        )
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save Auto Top-up config."})
        unregister_account_topup_job(account_id)
        if body.enabled:
            register_account_topup_job(get_account(account_id))
        return JSONResponse(content={"status": "success", "message": "Auto Top-up configuration saved."})
    except Exception as e:
        logger.error("api_update_autotopup_config account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/autotopup/confirm")
@limiter.limit("30/minute")
async def api_confirm_autotopup(request: Request, account_id: int, body: AutoTopupConfirmBody):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        result = confirm_autotopup(account_id, body.pending_id, body.amount, body.txn_date)
        if "error" in result:
            return JSONResponse(status_code=400, content={"status": "error", "message": result["error"]})
        return JSONResponse(content={"status": "success", "message": "Top-up confirmed.", "txn_id": result["txn_id"]})
    except Exception as e:
        logger.error("api_confirm_autotopup account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/autotopup/dismiss")
@limiter.limit("30/minute")
async def api_dismiss_autotopup(request: Request, account_id: int, body: AutoTopupDismissBody):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        result = dismiss_autotopup(account_id, body.pending_id)
        if "error" in result:
            return JSONResponse(status_code=400, content={"status": "error", "message": result["error"]})
        return JSONResponse(content={"status": "success", "message": "Top-up dismissed."})
    except Exception as e:
        logger.error("api_dismiss_autotopup account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}/scraper-config")
@limiter.limit("30/minute")
async def api_update_scraper_config(request: Request, account_id: int, body: ScraperConfigBody):
    try:
        acc, error = _require_scraper_account(account_id)
        if error:
            return error
        ok = update_account(
            account_id,
            scraper_url=body.scraper_url.strip(),
            scraper_selector=body.scraper_selector.strip(),
            scraper_headers=json.dumps(body.scraper_headers or {}),
            scrape_time=body.scrape_time,
            scraper_enabled=body.scraper_enabled,
        )
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save scraper config."})
        unregister_account_scraper_job(account_id)
        if body.scraper_enabled:
            register_account_scraper_job(get_account(account_id))
        return JSONResponse(content={"status": "success", "message": "Scraper configuration saved."})
    except Exception as e:
        logger.error("api_update_scraper_config account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/scraper/test")
@limiter.limit("20/minute")
async def api_test_scraper(request: Request, account_id: int, body: ScraperTestBody):
    try:
        _acc, error = _require_scraper_account(account_id)
        if error:
            return error
        result = test_scrape(body.url, body.selector, body.headers)
        if result["status"] != "success":
            return JSONResponse(status_code=422, content={"status": "error", "message": result["message"]})
        return JSONResponse(content={"status": "success", "price": result["price"]})
    except Exception as e:
        logger.error("api_test_scraper account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/scraper/run-now")
@limiter.limit("20/minute")
async def api_run_scraper_now(request: Request, account_id: int, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_scraper_account(account_id)
        if error:
            return error
        result = run_scrape_for_account(account_id)
        if result["status"] != "success":
            return JSONResponse(status_code=422, content={"status": "error", "message": result["message"]})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "price": result["price"]})
    except Exception as e:
        logger.error("api_run_scraper_now account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/price-history/import-csv")
@limiter.limit("10/minute")
async def api_import_price_csv(request: Request, account_id: int, body: PriceCsvImportBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_scraper_account(account_id)
        if error:
            return error
        result = import_price_csv(account_id, body.csv_text)
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={
            "status": "success",
            "message": f"Imported {result['imported']} price row(s) ({result['skipped']} skipped).",
            "imported": result["imported"],
            "skipped": result["skipped"],
        })
    except Exception as e:
        logger.error("api_import_price_csv account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.get("/accounts/{account_id}/price-history/at-date")
@limiter.limit("60/minute")
async def api_price_at_date(request: Request, account_id: int, date: str):
    try:
        _acc, error = _require_scraper_account(account_id)
        if error:
            return error
        return JSONResponse(content={"status": "success", "price": price_as_of(account_id, date)})
    except Exception as e:
        logger.error("api_price_at_date account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.get("/accounts/{account_id}/pension/units-as-of")
@limiter.limit("60/minute")
async def api_pension_units_as_of(request: Request, account_id: int, date: str):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        return JSONResponse(content={"status": "success", "units": pension_units_as_of(account_id, date)})
    except Exception as e:
        logger.error("api_pension_units_as_of account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/pension/contribution")
@limiter.limit("30/minute")
async def api_record_pension_contribution(request: Request, account_id: int, body: PensionContributionBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        result = record_pension_contribution(account_id, body.txn_date, body.amount, body.unit_price)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.error("api_record_pension_contribution account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/pension/fee")
@limiter.limit("30/minute")
async def api_record_pension_fee(request: Request, account_id: int, body: PensionFeeBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        result = record_pension_fee(account_id, body.txn_date, body.units_after, body.units_removed, body.unit_price)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.error("api_record_pension_fee account=%s failed: %s", account_id, e)
        return _error_500(e)
