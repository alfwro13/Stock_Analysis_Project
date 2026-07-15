import logging
import os
import secrets

from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


def require_confirm_token(x_confirm_token: str = Header(..., alias="X-Confirm-Token")):
    expected = os.environ.get("ADMIN_CONFIRM_TOKEN", "")
    if not expected or not secrets.compare_digest(x_confirm_token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing confirmation token.")


def _error_500(e: Exception) -> JSONResponse:
    """Logs the real exception server-side; the client only ever sees a generic message (CWE-209)."""
    logger.error("Unhandled API exception: %s", e, exc_info=True)
    return JSONResponse(status_code=500, content={"status": "error", "message": "An internal error occurred. Check server logs for details."})
