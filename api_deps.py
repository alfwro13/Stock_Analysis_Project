import logging

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
