import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from accounts_engine import (
    _ticker_known, _has_stock_signals_row, account_summary, confirm_autotopup, create_transfer,
    delete_transaction_with_pair, dismiss_autotopup, export_transactions_csv,
    filter_value_history_by_period, fx_rate_on_date, held_tickers_lightweight,
    pension_units_as_of, reconcile_cash, record_pension_contribution, record_pension_fee,
    refresh_performance_cache, resnapshot_account, resolve_watchlist_metadata,
    sync_house_purchase_price, sync_pension_opening_balance,
    tickers_needing_refresh, watchlist_summary,
)
from account_csv_import_engine import import_csv_activities
from account_scraper_engine import import_price_csv, price_as_of, run_scrape_for_account, test_scrape
from portfolio_metrics_engine import (
    account_metrics_list, holdings_with_metrics_all_accounts, other_accounts_list,
    portfolio_totals, set_holding_price_limit,
)
import notification_engine
from api_deps import limiter, _error_500
from config import load_config
from data_engine import fetch_and_save_single_ticker
from quant_signals import QuantEngine
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
    get_performance_cache,
    get_value_history,
    get_watchlist_items,
    add_watchlist_item,
    delete_watchlist_items,
    get_unresolved_pending_topups,
    get_treasury_bill,
    update_treasury_bill_auto_reinvest,
    get_benchmark_tickers,
    replace_benchmark_tickers,
)
from market_pulse import fetch_and_save_pulse
from markets_engine import registry_lookup_tickers
from notification_engine import notify
from profile_engine import update_single_profile
from scheduler_engine import (
    get_all_job_last_runs, register_account_scraper_job, register_account_topup_job,
    run_account_value_snapshot, run_treasury_bill_maturity_sweep, unregister_account_scraper_job,
    unregister_account_topup_job,
)
from treasury_bill_engine import buy_treasury_bill, confirm_ytm, delete_treasury_bill, list_treasury_bills
from utils import has_cached_fundamentals, is_excluded_from_yahoo_fetch, normalize_ticker
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
    fee_currency: Optional[str] = None
    fee_exchange_rate: Optional[float] = None
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


class BenchmarkTickerBody(BaseModel):
    ticker: str
    display_name: str


class BenchmarkConfigBody(BaseModel):
    cpi_target_pct: float = 4.0
    tickers: list[BenchmarkTickerBody] = []


class AutoTopupDismissBody(BaseModel):
    pending_id: int


class PriceCsvImportBody(BaseModel):
    csv_text: str


class HoldingPriceLimitBody(BaseModel):
    account_id: int
    ticker: str
    low_limit: Optional[float] = None
    high_limit: Optional[float] = None


class PensionContributionBody(BaseModel):
    txn_date: str
    amount: float
    unit_price: Optional[float] = None


class PensionFeeBody(BaseModel):
    txn_date: str
    units_after: Optional[float] = None
    units_removed: Optional[float] = None
    unit_price: Optional[float] = None


class TreasuryBillBuyBody(BaseModel):
    purchase_date: str
    face_value: float
    purchase_price: float
    maturity_date: str
    auto_reinvest: bool = False
    notes: Optional[str] = None
    indicative_ytm: Optional[float] = None


class TreasuryBillAutoReinvestBody(BaseModel):
    auto_reinvest: bool


class TreasuryBillConfirmYtmBody(BaseModel):
    confirmed_ytm: Optional[float] = None
    face_value: Optional[float] = None


def _ensure_ticker_data(ticker: str, background_tasks: BackgroundTasks) -> None:
    """Queues whichever of profile / fundamentals+price / stock_signals row this ticker is still
    missing, so it renders correctly on the Portfolio and Watchlist pages straight away. The three
    gates are deliberately independent: a universe-scraped ticker has an `asset_profiles` row but
    no fundamentals dump, and analyzing it without one silently writes 'USD'/'EQUITY'/'Unknown'
    defaults over the profile's real values."""
    if is_excluded_from_yahoo_fetch(ticker):
        return
    if not _ticker_known(ticker):
        background_tasks.add_task(update_single_profile, ticker)
    if not has_cached_fundamentals(ticker):
        background_tasks.add_task(fetch_and_save_single_ticker, ticker)
    if not _has_stock_signals_row(ticker):
        background_tasks.add_task(QuantEngine().analyze_ticker, ticker)


def _resolve_exchange_rate(currency: Optional[str], exchange_rate: Optional[float], txn_date: str) -> float:
    if exchange_rate is not None:
        return exchange_rate
    return fx_rate_on_date(currency, txn_date)


def _resolve_fee_currency_and_rate(
    fee_currency: Optional[str], fee_exchange_rate: Optional[float],
    trade_currency: str, trade_exchange_rate: float, txn_date: str,
) -> tuple:
    """A fee can be billed in a different currency than the trade itself (e.g. a broker's FX spread
    fee already quoted in base currency on a foreign-currency trade) — resolved independently of
    the trade leg so `_cash_delta` never has to reuse the trade's rate for a fee in another currency."""
    resolved_currency = fee_currency or trade_currency
    if fee_exchange_rate is not None:
        return resolved_currency, fee_exchange_rate
    if resolved_currency == trade_currency:
        return resolved_currency, trade_exchange_rate
    return resolved_currency, fx_rate_on_date(resolved_currency, txn_date)


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


@accounts_router.get("/accounts/{account_id}/live-performance")
async def api_account_live_performance(account_id: int):
    acc = get_account(account_id)
    if acc is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    if acc["account_type"] != "Trading":
        return JSONResponse(status_code=400, content={"status": "error", "message": "Live performance is only available for Trading accounts."})
    cached = get_performance_cache(account_id)
    if cached is None:
        refresh_performance_cache(account_id)
        cached = get_performance_cache(account_id)
    return JSONResponse(content={"status": "success", **cached})


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
        if ticker:
            _ensure_ticker_data(ticker, background_tasks)
        currency = body.currency or acc["currency"]
        exchange_rate = _resolve_exchange_rate(currency, body.exchange_rate, body.txn_date)
        fee_currency, fee_exchange_rate = _resolve_fee_currency_and_rate(
            body.fee_currency, body.fee_exchange_rate, currency, exchange_rate, body.txn_date
        )
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
            fee_currency=fee_currency,
            fee_exchange_rate=fee_exchange_rate,
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
        if existing["ticker"] and existing["ticker"].startswith("TBILL-"):
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": "Treasury Bill transactions can't be edited directly — use the Treasury Bills panel, or delete the bill there to correct a mis-entry."},
            )
        if body.txn_type not in _TXN_TYPES:
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": f"txn_type must be one of: {', '.join(sorted(_TXN_TYPES))}"},
            )
        ticker = normalize_ticker(body.ticker) if body.ticker else None
        currency = body.currency or acc["currency"]
        exchange_rate = _resolve_exchange_rate(currency, body.exchange_rate, body.txn_date)
        fee_currency, fee_exchange_rate = _resolve_fee_currency_and_rate(
            body.fee_currency, body.fee_exchange_rate, currency, exchange_rate, body.txn_date
        )
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
            fee_currency=fee_currency,
            fee_exchange_rate=fee_exchange_rate,
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
        if existing["ticker"] and existing["ticker"].startswith("TBILL-"):
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": "Treasury Bill transactions can't be deleted directly — delete the bill from the Treasury Bills panel instead."},
            )
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
async def api_add_watchlist_item(request: Request, account_id: int, body: WatchlistItemBody, background_tasks: BackgroundTasks):
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
        _ensure_ticker_data(ticker, background_tasks)
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
            _ensure_ticker_data(ticker, background_tasks)
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
    background_tasks.add_task(run_account_value_snapshot, scheduled=False)
    return JSONResponse(content={
        "status": "queued",
        "message": "Account Value Snapshot job queued. Check system notifications for completion.",
    })


@accounts_router.post("/accounts/treasury-bills/maturity-sweep/trigger")
async def api_trigger_treasury_bill_maturity_sweep(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_treasury_bill_maturity_sweep)
    return JSONResponse(content={
        "status": "queued",
        "message": "UK Treasury Bill Maturity Sweep queued. Check system notifications for completion.",
    })


def maybe_trigger_price_refresh(background_tasks: BackgroundTasks) -> None:
    """Backs the Home Assistant integration's polling-driven refresh and the /portfolio page's
    own load: whatever cadence actually hits this (an HA poll, a browser tab, a page reload)
    becomes the real refresh cadence, capped by UI_PREFERENCES.REFRESH_RATE so it doesn't hammer
    Yahoo Finance on every call — the same needs_refresh pattern GET /api/market-pulse already
    uses for the live-ticking widget, extended here to cover every held ticker rather than only
    ones rendered on screen."""
    refresh_rate = int(load_config().get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))
    tickers = held_tickers_lightweight()
    stale = tickers_needing_refresh(tickers, refresh_rate)
    if stale:
        background_tasks.add_task(fetch_and_save_pulse, stale)


@accounts_router.get("/accounts/portfolio-totals")
async def api_portfolio_totals(background_tasks: BackgroundTasks):
    try:
        maybe_trigger_price_refresh(background_tasks)
        return JSONResponse(content={"status": "success", **portfolio_totals()})
    except Exception as e:
        logger.error("api_portfolio_totals failed: %s", e)
        return _error_500(e)


@accounts_router.get("/accounts/list-with-metrics")
async def api_accounts_list_with_metrics(background_tasks: BackgroundTasks):
    try:
        maybe_trigger_price_refresh(background_tasks)
        return JSONResponse(content={"status": "success", **account_metrics_list()})
    except Exception as e:
        logger.error("api_accounts_list_with_metrics failed: %s", e)
        return _error_500(e)


@accounts_router.get("/accounts/holdings-list")
async def api_holdings_list(background_tasks: BackgroundTasks):
    try:
        maybe_trigger_price_refresh(background_tasks)
        return JSONResponse(content={"status": "success", **holdings_with_metrics_all_accounts()})
    except Exception as e:
        logger.error("api_holdings_list failed: %s", e)
        return _error_500(e)


@accounts_router.get("/accounts/other-accounts-list")
async def api_other_accounts_list():
    try:
        return JSONResponse(content={"status": "success", **other_accounts_list()})
    except Exception as e:
        logger.error("api_other_accounts_list failed: %s", e)
        return _error_500(e)


@accounts_router.post("/accounts/holding-price-limit")
async def api_set_holding_price_limit(body: HoldingPriceLimitBody):
    try:
        fields = body.model_dump(include={"low_limit", "high_limit"}, exclude_unset=True)
        set_holding_price_limit(body.account_id, body.ticker, **fields)
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.error("api_set_holding_price_limit failed: %s", e)
        return _error_500(e)


def _run_refresh_now(tickers: list) -> None:
    try:
        fetch_and_save_pulse(tickers)
        for acc in get_accounts():
            if acc["account_type"] == "Trading":
                refresh_performance_cache(acc["id"])
        notify("ha_refresh_now_status", "Success", "Home Assistant refresh-now completed.", level="info")
    except Exception as e:
        logger.error("HA refresh-now failed: %s", e)
        notify("ha_refresh_now_status", "Error", f"Home Assistant refresh-now failed: {e}", level="error")
        raise


def _refresh_markets_registry() -> None:
    """Piggybacks the Markets page's full ticker registry onto every HA-triggered refresh, so
    open tabs see fresh data on their next poll instead of waiting on their own lazy per-tile
    staleness cycle. Fired as a background task (not awaited by the route) since HA doesn't need
    these tickers back — only the portfolio-holdings fetch above must finish before the response."""
    try:
        fetch_and_save_pulse(registry_lookup_tickers())
    except Exception as e:
        logger.error("HA refresh-now market registry warm failed: %s", e)


@accounts_router.post("/accounts/refresh-now")
async def api_refresh_now(background_tasks: BackgroundTasks):
    # Awaited (via a worker thread, so the event loop stays free for other requests) rather than
    # fired-and-forgotten: the Home Assistant "Refresh Data" button re-polls its coordinator
    # immediately after this call returns, so the fetch must actually be finished by then or the
    # re-poll just sees the still-stale data it was trying to fix.
    tickers = held_tickers_lightweight()
    try:
        await run_in_threadpool(_run_refresh_now, tickers)
    except Exception as e:
        return _error_500(e)
    background_tasks.add_task(_refresh_markets_registry)
    return JSONResponse(content={"status": "success", "message": "Refresh complete."})


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
        return None, JSONResponse(status_code=400, content={"status": "error", "message": "This action is only available on Trading accounts."})
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


@accounts_router.get("/accounts/{account_id}/benchmark-config")
@limiter.limit("60/minute")
async def api_get_benchmark_config(request: Request, account_id: int):
    try:
        acc, error = _require_pension_account(account_id)
        if error:
            return error
        return JSONResponse(content={
            "status": "success",
            "cpi_target_pct": acc["benchmark_cpi_target_pct"],
            "tickers": get_benchmark_tickers(account_id),
        })
    except Exception as e:
        logger.error("api_get_benchmark_config account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}/benchmark-config")
@limiter.limit("30/minute")
async def api_update_benchmark_config(request: Request, account_id: int, body: BenchmarkConfigBody):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        tickers = [
            {"ticker": normalize_ticker(t.ticker), "display_name": t.display_name.strip()}
            for t in body.tickers if t.ticker.strip() and t.display_name.strip()
        ]
        ok = update_account(account_id, benchmark_cpi_target_pct=body.cpi_target_pct)
        ok = replace_benchmark_tickers(account_id, tickers) and ok
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save benchmark configuration."})
        return JSONResponse(content={"status": "success", "message": "Benchmark configuration saved."})
    except Exception as e:
        logger.error("api_update_benchmark_config account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/treasury-bills")
@limiter.limit("30/minute")
async def api_buy_treasury_bill(request: Request, account_id: int, body: TreasuryBillBuyBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        result = buy_treasury_bill(
            account_id, body.purchase_date, body.face_value, body.purchase_price,
            body.maturity_date, body.auto_reinvest, body.notes, body.indicative_ytm,
        )
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.error("api_buy_treasury_bill account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.get("/accounts/{account_id}/treasury-bills")
@limiter.limit("60/minute")
async def api_list_treasury_bills(request: Request, account_id: int):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        return JSONResponse(content={"status": "success", "treasury_bills": list_treasury_bills(account_id)})
    except Exception as e:
        logger.error("api_list_treasury_bills account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}/treasury-bills/{bill_id}")
@limiter.limit("30/minute")
async def api_update_treasury_bill(request: Request, account_id: int, bill_id: int, body: TreasuryBillAutoReinvestBody):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        bill = get_treasury_bill(bill_id)
        if not bill or bill["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Treasury Bill not found."})
        if not update_treasury_bill_auto_reinvest(bill_id, body.auto_reinvest):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to update the Treasury Bill."})
        return JSONResponse(content={"status": "success", "message": "Treasury Bill updated."})
    except Exception as e:
        logger.error("api_update_treasury_bill account=%s bill=%s failed: %s", account_id, bill_id, e)
        return _error_500(e)


@accounts_router.post("/accounts/{account_id}/treasury-bills/{bill_id}/confirm-ytm")
@limiter.limit("30/minute")
async def api_confirm_treasury_bill_ytm(request: Request, account_id: int, bill_id: int, body: TreasuryBillConfirmYtmBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        bill = get_treasury_bill(bill_id)
        if not bill or bill["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Treasury Bill not found."})
        result = confirm_ytm(bill_id, body.confirmed_ytm, body.face_value)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.error("api_confirm_treasury_bill_ytm account=%s bill=%s failed: %s", account_id, bill_id, e)
        return _error_500(e)


@accounts_router.delete("/accounts/{account_id}/treasury-bills/{bill_id}")
@limiter.limit("20/minute")
async def api_delete_treasury_bill(request: Request, account_id: int, bill_id: int, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_trading_account(account_id)
        if error:
            return error
        bill = get_treasury_bill(bill_id)
        if not bill or bill["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Treasury Bill not found."})
        result = delete_treasury_bill(bill_id)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", "message": "Treasury Bill deleted."})
    except Exception as e:
        logger.error("api_delete_treasury_bill account=%s bill=%s failed: %s", account_id, bill_id, e)
        return _error_500(e)
