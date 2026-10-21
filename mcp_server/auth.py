import logging
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from config import load_config
from constants import MCP_AUTH_FAILURES_MAX, MCP_AUTH_LOCKOUT_SECONDS

from . import audit, keys, limits
from .server import MCP_PATH

logger = logging.getLogger(__name__)


def _rejection(address: str, status: int, detail: str, headers: Optional[dict] = None, log: bool = True) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
    if log:
        audit.record(None, address, None, None, status, 0, len(response.body))
    return response


def _wrong_credential(address: str, status: int, detail: str, headers: Optional[dict] = None) -> JSONResponse:
    if limits.record_failure(address):
        logger.warning(
            "Locked %s out of %s for %d s after %d wrong credentials", address, MCP_PATH, MCP_AUTH_LOCKOUT_SECONDS, MCP_AUTH_FAILURES_MAX
        )
    return _rejection(address, status, detail, headers)


def auth_failure(request: Request) -> Optional[JSONResponse]:
    if not load_config().get("MCP_SERVER_ENABLED"):
        return JSONResponse({"detail": "The MCP Server is disabled"}, status_code=503)
    address = request.client.host if request.client else "unknown"
    wait = limits.lockout_remaining(address)
    if wait:
        return _rejection(
            address, 429, "Too many wrong MCP credentials from this address", {"Retry-After": str(wait)},
            log=limits.first_refusal(("address", address)),
        )
    if request.headers.get("X-API-Key"):
        return _wrong_credential(address, 403, "The API key cannot be used on this route")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        key_id = keys.match(token.strip())
        if key_id is not None:
            keys.record_use(key_id)
            request.state.mcp_key_id = key_id
            request.state.mcp_address = address
            return None
    if not keys.has_keys():
        logger.warning("Refused a %s request: no MCP key exists", MCP_PATH)
    return _wrong_credential(address, 401, "Invalid or missing MCP key", {"WWW-Authenticate": "Bearer"})
