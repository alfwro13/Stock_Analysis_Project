import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from accounts_engine import fx_rate_on_date
from api_deps import limiter, _error_500
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
    delete_transaction,
    get_connection,
)
from profile_engine import update_single_profile
from utils import normalize_ticker
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

accounts_router = APIRouter()

_TXN_TYPES = frozenset({"Buy", "Sell", "Fee", "Dividend", "Interest", "Cash"})


class AccountBody(BaseModel):
    name: str
    currency: str
    initial_cash: float = 0.0
    note: Optional[str] = None


class TransactionBody(BaseModel):
    txn_type: str
    txn_date: str
    ticker: Optional[str] = None
    company_name: Optional[str] = None
    currency: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    fee: float = 0.0
    exchange_rate: Optional[float] = None
    notes: Optional[str] = None
    update_cash: bool = True
    price_in_pence: bool = False


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


def _resolve_exchange_rate(currency: Optional[str], exchange_rate: Optional[float], txn_date: str) -> float:
    if exchange_rate is not None:
        return exchange_rate
    return fx_rate_on_date(currency, txn_date)


@accounts_router.get("/accounts")
async def api_list_accounts():
    return JSONResponse(content={"status": "success", "accounts": get_accounts()})


@accounts_router.post("/accounts")
@limiter.limit("30/minute")
async def api_create_account(request: Request, body: AccountBody):
    try:
        account_id = create_account(
            name=body.name.strip(),
            currency=body.currency.upper().strip(),
            initial_cash=body.initial_cash,
            note=body.note,
        )
        if account_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to create account."})
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
        return JSONResponse(content={"status": "success", "message": "Transaction added.", "id": txn_id})
    except Exception as e:
        logger.error("api_create_transaction account=%s failed: %s", account_id, e)
        return _error_500(e)


@accounts_router.put("/accounts/{account_id}/transactions/{txn_id}")
@limiter.limit("30/minute")
async def api_update_transaction(request: Request, account_id: int, txn_id: int, body: TransactionBody):
    try:
        acc = get_account(account_id)
        if acc is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
        existing = get_transaction(txn_id)
        if existing is None or existing["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Transaction not found."})
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
        return JSONResponse(content={"status": "success", "message": "Transaction updated."})
    except Exception as e:
        logger.error("api_update_transaction %s failed: %s", txn_id, e)
        return _error_500(e)


@accounts_router.delete("/accounts/{account_id}/transactions/{txn_id}")
@limiter.limit("30/minute")
async def api_delete_transaction(request: Request, account_id: int, txn_id: int):
    try:
        existing = get_transaction(txn_id)
        if existing is None or existing["account_id"] != account_id:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Transaction not found."})
        delete_transaction(txn_id)
        return JSONResponse(content={"status": "success", "message": "Transaction deleted."})
    except Exception as e:
        logger.error("api_delete_transaction %s failed: %s", txn_id, e)
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
