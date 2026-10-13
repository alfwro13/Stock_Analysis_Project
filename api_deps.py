import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth import COOKIE_NAME, STEP_UP_COOKIE_NAME, verify_session_token, verify_step_up_token

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


def require_step_up(request: Request):
    session = request.cookies.get(COOKIE_NAME, "")
    step_up = request.cookies.get(STEP_UP_COOKIE_NAME, "")
    if not (session and step_up and verify_session_token(session) and verify_step_up_token(step_up, session)):
        raise HTTPException(
            status_code=403, detail="Password confirmation required.", headers={"X-Step-Up-Required": "1"},
        )


def _error_500(e: Exception) -> JSONResponse:
    """Logs the real exception server-side; the client only ever sees a generic message (CWE-209)."""
    logger.error("Unhandled API exception: %s", e, exc_info=True)
    return JSONResponse(status_code=500, content={"status": "error", "message": "An internal error occurred. Check server logs for details."})
