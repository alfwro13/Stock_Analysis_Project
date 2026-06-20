import os
import secrets

from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def require_confirm_token(x_confirm_token: str = Header(..., alias="X-Confirm-Token")):
    expected = os.environ.get("ADMIN_CONFIRM_TOKEN", "")
    if not expected or not secrets.compare_digest(x_confirm_token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing confirmation token.")


def _error_500(e: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
