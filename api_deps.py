import logging
import math

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth import (
    COOKIE_NAME, STEP_UP_COOKIE_NAME, clear_login_failures, login_lockout_remaining,
    record_login_failure, verify_session_token, verify_step_up_token,
)

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


class FiniteModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


def reject_non_finite(value):
    """allow_inf_nan only covers float-typed fields; an untyped dict/list field needs this walk."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("numbers must be finite")
    elif isinstance(value, dict):
        for item in value.values():
            reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_non_finite(item)
    return value


# Adding a route here is what lets Home Assistant call it. See assets/api_reference.md, "API key scope".
API_KEY_ROUTES = frozenset({
    ("GET", "/api/accounts/portfolio-totals"),
    ("GET", "/api/accounts/list-with-metrics"),
    ("GET", "/api/accounts/holdings-list"),
    ("GET", "/api/accounts/other-accounts-list"),
    ("POST", "/api/accounts/refresh-now"),
    ("POST", "/api/accounts/holding-price-limit"),
    ("GET", "/api/system/market-status"),
    ("GET", "/api/market-regime/current"),
    ("GET", "/api/macro-conditions"),
    ("GET", "/api/markets"),
})


def job_busy_response(job_id: str):
    """409 for a manual trigger while the job runs (scheduled or manual); the route returns it before queuing the task."""
    from scheduler_engine import is_job_running, job_label
    if not is_job_running(job_id):
        return None
    return JSONResponse(status_code=409, content={
        "status": "busy",
        "message": f"{job_label(job_id)} is already running. Wait for it to finish, then try again.",
    })


def require_step_up(request: Request):
    session = request.cookies.get(COOKIE_NAME, "")
    step_up = request.cookies.get(STEP_UP_COOKIE_NAME, "")
    if not (session and step_up and verify_session_token(session) and verify_step_up_token(step_up, session)):
        raise HTTPException(
            status_code=403, detail="Password confirmation required.", headers={"X-Step-Up-Required": "1"},
        )


def enforce_login_lockout() -> None:
    remaining = login_lockout_remaining()
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {remaining} seconds.",
            headers={"Retry-After": str(remaining)},
        )


def record_password_attempt(request: Request, context: str, ok: bool, known_user: bool = True) -> None:
    if ok:
        clear_login_failures()
    else:
        record_login_failure(context, get_remote_address(request), known_user)


def _error_500(e: Exception) -> JSONResponse:
    """Logs the real exception server-side; the client only ever sees a generic message (CWE-209)."""
    logger.error("Unhandled API exception: %s", e, exc_info=True)
    return JSONResponse(status_code=500, content={"status": "error", "message": "An internal error occurred. Check server logs for details."})
