import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from account_reminders_engine import dismiss_pending_reminder, pending_reminder_error, reminder_error
from accounts_engine import pension_units_as_of, record_pension_contribution, record_pension_fee, resnapshot_account
from api.deps import FiniteModel, limiter, refuse_writes_during_restore, _error_500
from api.routes_accounts import _invalid_input_response
from db.database import (
    get_account,
    get_benchmark_tickers,
    replace_benchmark_tickers,
    update_account,
)
from db.account_reminders import (
    create_reminder, delete_reminder, get_reminder, get_reminders, resolve_pending_reminder, update_reminder,
)
from scheduler_engine import register_account_reminder_job, unregister_account_reminder_job
from utils import normalize_ticker

logger = logging.getLogger(__name__)

pension_router = APIRouter(dependencies=[Depends(refuse_writes_during_restore)])


class BenchmarkTickerBody(BaseModel):
    ticker: str
    display_name: str


class BenchmarkConfigBody(FiniteModel):
    cpi_target_pct: float = 4.0
    tickers: list[BenchmarkTickerBody] = []


class PensionContributionBody(FiniteModel):
    txn_date: str
    amount: float
    unit_price: Optional[float] = None
    reminder_pending_id: Optional[int] = None


class PensionFeeBody(FiniteModel):
    txn_date: str
    units_after: Optional[float] = None
    units_removed: Optional[float] = None
    unit_price: Optional[float] = None
    reminder_pending_id: Optional[int] = None


class ReminderBody(FiniteModel):
    reminder_type: str
    cadence: str = "monthly"
    day_of_month: int
    enabled: bool = False


def _require_pension_account(account_id: int):
    acc = get_account(account_id)
    if acc is None:
        return None, JSONResponse(status_code=404, content={"status": "error", "message": "Account not found."})
    if acc["account_type"] != "Pension":
        return None, JSONResponse(status_code=400, content={"status": "error", "message": "This action is only available on Pension accounts."})
    return acc, None


@pension_router.get("/accounts/{account_id}/pension/units-as-of")
@limiter.limit("60/minute")
def api_pension_units_as_of(request: Request, account_id: int, date: str):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        return JSONResponse(content={"status": "success", "units": pension_units_as_of(account_id, date)})
    except Exception as e:
        logger.error("api_pension_units_as_of account=%s failed: %s", account_id, e)
        return _error_500(e)


@pension_router.post("/accounts/{account_id}/pension/contribution")
@limiter.limit("30/minute")
def api_record_pension_contribution(request: Request, account_id: int, body: PensionContributionBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        invalid = _invalid_input_response(dates={"txn_date": body.txn_date})
        if invalid:
            return invalid
        if body.reminder_pending_id is not None:
            pending_error = pending_reminder_error(account_id, body.reminder_pending_id, "pay_in")
            if pending_error:
                return JSONResponse(status_code=422, content={"status": "error", "message": pending_error})
        result = record_pension_contribution(account_id, body.txn_date, body.amount, body.unit_price)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        if body.reminder_pending_id is not None:
            resolve_pending_reminder(body.reminder_pending_id, "done", result["txn_id"])
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.error("api_record_pension_contribution account=%s failed: %s", account_id, e)
        return _error_500(e)


@pension_router.post("/accounts/{account_id}/pension/fee")
@limiter.limit("30/minute")
def api_record_pension_fee(request: Request, account_id: int, body: PensionFeeBody, background_tasks: BackgroundTasks):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        invalid = _invalid_input_response(dates={"txn_date": body.txn_date})
        if invalid:
            return invalid
        if body.reminder_pending_id is not None:
            pending_error = pending_reminder_error(account_id, body.reminder_pending_id, "admin_fee")
            if pending_error:
                return JSONResponse(status_code=422, content={"status": "error", "message": pending_error})
        result = record_pension_fee(account_id, body.txn_date, body.units_after, body.units_removed, body.unit_price)
        if result.get("error"):
            return JSONResponse(status_code=422, content={"status": "error", "message": result["error"]})
        if body.reminder_pending_id is not None:
            resolve_pending_reminder(body.reminder_pending_id, "done", result["txn_id"])
        background_tasks.add_task(resnapshot_account, account_id)
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.error("api_record_pension_fee account=%s failed: %s", account_id, e)
        return _error_500(e)


def _owned_reminder(account_id: int, reminder_id: int):
    reminder = get_reminder(reminder_id)
    if reminder is None or reminder["account_id"] != account_id:
        return None, JSONResponse(status_code=404, content={"status": "error", "message": "Reminder not found."})
    return reminder, None


@pension_router.get("/accounts/{account_id}/reminders")
@limiter.limit("60/minute")
def api_list_reminders(request: Request, account_id: int):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        return JSONResponse(content={"status": "success", "reminders": get_reminders(account_id)})
    except Exception as e:
        logger.error("api_list_reminders account=%s failed: %s", account_id, e)
        return _error_500(e)


@pension_router.post("/accounts/{account_id}/reminders")
@limiter.limit("30/minute")
def api_create_reminder(request: Request, account_id: int, body: ReminderBody):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        invalid = reminder_error(body.reminder_type, body.cadence, body.day_of_month)
        if invalid:
            return JSONResponse(status_code=422, content={"status": "error", "message": invalid})
        reminder_id = create_reminder(account_id, body.reminder_type, body.cadence, body.day_of_month, body.enabled)
        if reminder_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save the reminder."})
        register_account_reminder_job(get_reminder(reminder_id))
        return JSONResponse(content={"status": "success", "message": "Reminder saved.", "id": reminder_id})
    except Exception as e:
        logger.error("api_create_reminder account=%s failed: %s", account_id, e)
        return _error_500(e)


@pension_router.put("/accounts/{account_id}/reminders/{reminder_id}")
@limiter.limit("30/minute")
def api_update_reminder(request: Request, account_id: int, reminder_id: int, body: ReminderBody):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        _reminder, error = _owned_reminder(account_id, reminder_id)
        if error:
            return error
        invalid = reminder_error(body.reminder_type, body.cadence, body.day_of_month)
        if invalid:
            return JSONResponse(status_code=422, content={"status": "error", "message": invalid})
        if not update_reminder(reminder_id, body.reminder_type, body.cadence, body.day_of_month, body.enabled):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save the reminder."})
        unregister_account_reminder_job(reminder_id)
        register_account_reminder_job(get_reminder(reminder_id))
        return JSONResponse(content={"status": "success", "message": "Reminder saved."})
    except Exception as e:
        logger.error("api_update_reminder account=%s reminder=%s failed: %s", account_id, reminder_id, e)
        return _error_500(e)


@pension_router.delete("/accounts/{account_id}/reminders/{reminder_id}")
@limiter.limit("30/minute")
def api_delete_reminder(request: Request, account_id: int, reminder_id: int):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        _reminder, error = _owned_reminder(account_id, reminder_id)
        if error:
            return error
        unregister_account_reminder_job(reminder_id)
        if not delete_reminder(reminder_id):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete the reminder."})
        return JSONResponse(content={"status": "success", "message": "Reminder deleted."})
    except Exception as e:
        logger.error("api_delete_reminder account=%s reminder=%s failed: %s", account_id, reminder_id, e)
        return _error_500(e)


@pension_router.post("/accounts/{account_id}/reminders/pending/{pending_id}/dismiss")
@limiter.limit("30/minute")
def api_dismiss_pending_reminder(request: Request, account_id: int, pending_id: int):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        invalid = dismiss_pending_reminder(account_id, pending_id)
        if invalid:
            return JSONResponse(status_code=400, content={"status": "error", "message": invalid})
        return JSONResponse(content={"status": "success", "message": "Reminder dismissed."})
    except Exception as e:
        logger.error("api_dismiss_pending_reminder account=%s pending=%s failed: %s", account_id, pending_id, e)
        return _error_500(e)


@pension_router.get("/accounts/{account_id}/benchmark-config")
@limiter.limit("60/minute")
def api_get_benchmark_config(request: Request, account_id: int):
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


@pension_router.put("/accounts/{account_id}/benchmark-config")
@limiter.limit("30/minute")
def api_update_benchmark_config(request: Request, account_id: int, body: BenchmarkConfigBody):
    try:
        _acc, error = _require_pension_account(account_id)
        if error:
            return error
        tickers = [
            {"ticker": normalize_ticker(t.ticker), "display_name": t.display_name.strip()}
            for t in body.tickers if t.ticker.strip() and t.display_name.strip()
        ]
        for item in tickers:
            invalid = _invalid_input_response(ticker=item["ticker"])
            if invalid:
                return invalid
        ok = update_account(account_id, benchmark_cpi_target_pct=body.cpi_target_pct)
        ok = replace_benchmark_tickers(account_id, tickers) and ok
        if not ok:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save benchmark configuration."})
        return JSONResponse(content={"status": "success", "message": "Benchmark configuration saved."})
    except Exception as e:
        logger.error("api_update_benchmark_config account=%s failed: %s", account_id, e)
        return _error_500(e)


